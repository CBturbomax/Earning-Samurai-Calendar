# -*- coding: utf-8 -*-
"""
홍콩 실적발표 스크래퍼 — 출처: HKEXnews 상장사 공시 검색
https://www1.hkexnews.hk/search/titlesearch.xhtml

미국·일본과 성격이 다르다. 그쪽은 회사가 "며칠에 발표하겠다"고 미리 신고한
**예정일**을 모아주는데, 홍콩은 그 제도가 약하다. HKEX 가 내던 이사회 개최일
캘린더는 개편 때 통째로 없어졌고(경로가 전부 404), 대체 소스로 흔히 쓰이는
AAStocks 는 표를 자바스크립트로 그려서 HTML 에 데이터가 없다.

그래서 홍콩만 **이미 공시된 실적**을 모은다. HKEXnews 는 거래소가 직접 내는
공식 공시 시스템이라 출처는 가장 확실하다. 대신 성격이 다르다:

    미국·일본  =  앞으로 누가 발표할 예정인가
    홍콩       =  누가 실제로 발표했는가

이걸 같은 캘린더에 섞어 놓고 아무 말 안 하면 거짓말이 된다. 그래서 화면에
「공시 기준」이라고 적고, markets.py 의 note 에도 남겨두었다. 앞으로의 일정을
주는 소스를 찾으면 그때 바꾸면 된다.

응답에서 걸러낼 것: HKEXnews 는 하루 수백 건이 올라오고 대부분 실적과 무관하다
(주주총회 표결결과, 자사주 매입, 증자 …). 제목·분류에 실적으로 읽히는 것만 남긴다.

결과: data/earnings_hk.json
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

SEARCH = "https://www1.hkexnews.hk/search/titleSearchServlet.do"
PAGE = "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = Path(__file__).parent / "data" / "earnings_hk.json"
BACKOFF = (0, 15, 45, 120)
GIVE_UP_AFTER = 3

# 실적 공시로 볼 것. 홍콩은 반기 보고가 기본이고 분기는 선택이다.
KEEP = re.compile(
    r"(annual|interim|final|quarterly|half[\s-]?year|first quarter|third quarter)"
    r"[\s\-\[\]]*results", re.I)
# 실적처럼 읽히지만 아닌 것들. '주주총회 표결결과'가 제일 흔한 함정이다.
DROP = re.compile(
    r"(poll\s+results|results\s+of\s+(the\s+)?(annual|extraordinary|general|special)"
    r"|general\s+meeting|agm|egm|rights\s+issue|placing|buy[\s-]?back"
    r"|clarification|delay|postpone)", re.I)

KIND = [
    (re.compile(r"(annual|final)", re.I), "본결산"),
    (re.compile(r"(interim|half[\s-]?year)", re.I), "중간결산"),
    (re.compile(r"(third\s+quarter|q3)", re.I), "3Q"),
    (re.compile(r"(first\s+quarter|q1)", re.I), "1Q"),
    (re.compile(r"quarter", re.I), "분기"),
]


class Throttled(Exception):
    pass


class ShapeChanged(Exception):
    """응답 구조가 예상과 다르다. 0건으로 삼키면 안 되는 상황."""


def get(url: str, retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE,
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
            print(f"    retry {attempt+1} ({e})", file=sys.stderr)
    return ""


# 하루에 올라오는 공시가 수백 건이라 넉넉히 잡는다. 이 수에 딱 맞게 돌아오면
# 잘렸다는 뜻이므로 fetch_day 가 큰 소리로 알린다.
ROW_CAP = 5000


def query(a: date, b: date, rows: int = ROW_CAP) -> str:
    return SEARCH + "?" + urllib.parse.urlencode({
        "sortDir": 0, "sortByOptions": "DateTime", "category": 0,
        "market": "SEHK", "stockId": -1, "documentType": -1,
        "fromDate": a.strftime("%Y%m%d"), "toDate": b.strftime("%Y%m%d"),
        "title": "", "searchType": 1, "t1code": -2, "t2Gcode": -2, "t2code": -2,
        "rowRange": rows, "lang": "EN",
    })


def unwrap(text: str):
    """{"result": "[{...}]"} — result 가 JSON '문자열'로 한 번 더 싸여 있다."""
    body = json.loads(text)
    raw = body.get("result")
    if isinstance(raw, str):
        raw = json.loads(raw or "[]")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ShapeChanged(f"result 가 목록이 아니다: {type(raw).__name__}")
    return raw, body


def first_field(v) -> str:
    """복수 카운터 종목은 코드·사명이 <br/> 로 이어져 온다.

    알리바바는 09988(홍콩달러 창구)과 89988(위안화 창구)이 한 칸에 같이 담겨
    "09988<br/>89988" 로 온다. 태그만 지우고 숫자만 남기면 '0998889988' 이 되어
    어느 종목도 아닌 코드가 만들어진다 — 실제로 알리바바·레노버가 그렇게 새어나갔다.
    같은 회사의 다른 창구이므로 첫 번째(주 창구)만 쓴다.
    """
    s = re.split(r"<br\s*/?>", str(v or ""), maxsplit=1)[0]
    return re.sub(r"<[^>]+>", "", s).strip()


def norm_date(s: str) -> str:
    """'07/08/2026 17:25' — 홍콩 표기는 일/월/연이다."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def kind_of(text: str) -> str:
    for rx, ko in KIND:
        if rx.search(text or ""):
            return ko
    return "실적"


def fetch_day(day: date, probe: bool = False):
    """하루치씩 받는다.

    처음에는 주 단위로 묶어 받았는데, HKEXnews 는 하루에만 수백 건이 올라와서
    한 주치를 한 번에 달라고 하면 응답이 앞에서 잘린다. 잘린 줄 모르고 넘어가면
    텐센트 같은 큰 종목이 통째로 빠지는데(실제로 4개월치에서 하나도 안 잡혔다),
    화면에는 '그날 발표가 없었다'로 보인다. 이 프로젝트가 제일 싫어하는 거짓말이다.
    """
    a = b = day
    for wait in BACKOFF:
        if wait:
            print(f"    throttled, {wait}s 대기 후 재시도", file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            text = get(query(a, b))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print(f"    응답 이상 ({e})", file=sys.stderr, flush=True)
            continue
        if probe:
            print(text[:2500])
            return []
        try:
            raw, body = unwrap(text)
        except (ValueError, ShapeChanged) as e:
            print(f"    파싱 실패 ({e}), 앞부분: {text[:200]!r}", file=sys.stderr, flush=True)
            continue

        # 받은 건수가 한도에 딱 붙으면 잘린 것이다. 조용히 넘기지 않는다.
        if len(raw) >= ROW_CAP:
            raise ShapeChanged(
                f"{day}: 공시 {len(raw)}건 = 한도({ROW_CAP})에 걸렸다. 응답이 잘렸다는 뜻이라\n"
                f"  이 날은 기록하지 않는다. ROW_CAP 을 올리거나 반나절씩 끊어야 한다.")

        # 봉투가 정상이면 0건도 정상 응답으로 인정한다 — 막히면 JSON 이 아니거나
        # result 키 자체가 없으므로 위에서 걸러진다.
        out, seen = [], set()
        for r in raw:
            label = f"{r.get('LONG_TEXT','')} {r.get('TITLE','')}"
            if not KEEP.search(label) or DROP.search(label):
                continue
            d = norm_date(r.get("DATE_TIME", ""))
            code = re.sub(r"\D", "", first_field(r.get("STOCK_CODE")))
            if not d or not code or len(code) > 5:
                continue
            code = code.zfill(5)
            if (d, code) in seen:
                continue
            seen.add((d, code))
            out.append({
                "date": d,
                "code": code,
                "name": (r.get("STOCK_NAME") or "").strip(),
                # 공시 제목에 줄바꿈이 그대로 들어 있다. 한 줄로 눕힌다.
                "fy": re.sub(r"\s+", " ", r.get("TITLE") or "").strip()[:60],
                "kind": kind_of(label),
                "sector": "",
                "market": "",
                "link": "https://www1.hkexnews.hk" + (r.get("FILE_LINK") or ""),
            })
        print(f"{day} 공시 {len(raw):>4}건 중 실적 {len(out):>3}건", flush=True)
        return out
    raise Throttled(f"{day}: 유효 응답 실패")


def load_cache():
    if not OUT.exists():
        return {}
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    by_day = {d: [] for d in old.get("ok_days", [])}
    for r in old.get("rows", []):
        by_day.setdefault(r["date"], []).append(r)
    return by_day


def save(by_day: dict, start: date, end: date):
    ok_days = sorted(by_day)
    rows = [r for d in ok_days for r in by_day[d]]
    payload = {
        "source": "HKEXnews 상장사 공시 (실적 공시 기준)",
        "source_url": PAGE,
        "range": [start.isoformat(), end.isoformat()],
        "count": len(rows),
        "ok_days": ok_days,
        "per_day": {d: len(by_day[d]) for d in ok_days},
        "rows": rows,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return len(rows), len(ok_days)


def main(start: date, end: date, probe: bool = False):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if probe:
        fetch_day(start, probe=True)
        return

    # 공시는 이미 나온 것만 있다. 미래 구간을 긁어봐야 빈다.
    today = date.today()
    if start > today:
        print(f"시작일이 오늘({today})보다 뒤다. 공시는 지나간 것만 있다.")
        return
    end = min(end, today)

    by_day = load_cache()
    failed, streak = [], 0

    # 하루씩. 최근 이틀은 계속 새 공시가 붙으므로 캐시를 무시하고 다시 받는다.
    day = start
    while day <= end:
        key = day.isoformat()
        fresh = day >= today - timedelta(days=1)
        if key in by_day and not fresh:
            print(f"{key} {len(by_day[key]):>3}건 (캐시)", flush=True)
        else:
            try:
                rows = fetch_day(day)
            except Exception as e:
                failed.append(key)
                streak += 1
                print(f"{key} 실패: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print("연속 실패 — 여기서 멈춘다. 다시 돌리면 이어서 받는다.",
                          file=sys.stderr, flush=True)
                    break
            else:
                streak = 0
                # 받은 날은 0건이라도 '수집 성공'으로 표시한다.
                # 그래야 '발표 없는 날'과 '못 받은 날'이 구분된다.
                by_day[key] = rows
                save(by_day, start, end)
            time.sleep(1.2)
        day += timedelta(days=1)

    n, days = save(by_day, start, end)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    if failed:
        print(f"미수집 {len(failed)}일: {failed[0]} ~ {failed[-1]} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    probe = "--probe" in sys.argv
    s = date.fromisoformat(a[0]) if a else date.today() - timedelta(days=30)
    e = date.fromisoformat(a[1]) if len(a) > 1 else date.today()
    main(s, e, probe)
