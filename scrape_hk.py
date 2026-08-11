# -*- coding: utf-8 -*-
"""
홍콩 실적발표 스케줄 스크래퍼 — 출처: HKEX 이사회 개최일(Board Meeting) 캘린더
https://www.hkex.com.hk/Market-Data/Statistics/Consolidated-Reports/Board-Meeting?sc_lang=en

홍콩은 '실적발표일'을 따로 고시하지 않는다. 대신 상장사가 결산을 승인할
**이사회 개최일**을 미리 신고하고, HKEX 가 그걸 모아 캘린더로 낸다.
실적 공시는 그 이사회 당일 장 마감 후에 나온다. 그래서 이사회 개최일을
발표 예정일로 삼는다. 일본의 '결산발표 스케줄'과 성격이 같다.

일본·미국과 다른 점 둘.
  1) 날짜 범위 조회가 된다. 하루씩 돌 필요가 없어 주 단위로 끊어 받는다.
  2) 앞으로의 일정만 나온다. 지나간 날짜는 이 소스로 못 채운다.

  ※ HKEX 위젯 API 는 페이지에 박힌 토큰을 요구하고, 그 토큰과 파라미터 이름이
  개편 때마다 바뀐다. 그래서 응답이 예상과 다르면 조용히 0건으로 넘기지 않고
  **원문 앞부분을 그대로 찍고 멈춘다.** 무엇이 바뀌었는지 눈으로 보고 고치라는 뜻이다.
  `python scrape_hk.py --probe` 로 응답만 떠볼 수도 있다.

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

PAGE = ("https://www.hkex.com.hk/Market-Data/Statistics/"
        "Consolidated-Reports/Board-Meeting?sc_lang=en")
WIDGET = "https://www1.hkex.com.hk/hkexwidget/data/getboardmeeting"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = Path(__file__).parent / "data" / "earnings_hk.json"
BACKOFF = (0, 15, 45, 120, 300)

# 이사회 안건 -> 분기 표기. 홍콩은 반기 보고가 기본이고 분기는 선택이다.
PURPOSE = [
    (re.compile(r"annual|final", re.I), "본결산"),
    (re.compile(r"interim|half", re.I), "중간결산"),
    (re.compile(r"quarter|1st|first", re.I), "분기"),
]

# 응답에서 찾아 쓸 키 이름 후보. HKEX 가 개편할 때마다 조금씩 달라져서
# 하나로 못 박지 않고 후보를 늘어놓고 먼저 걸리는 걸 쓴다.
K_DATE = ("date", "meetingdate", "bmdate", "meeting_date", "boardmeetingdate")
K_CODE = ("stockcode", "code", "sym", "ric", "stock_code")
K_NAME = ("stockname", "name", "nm", "stock_name", "companyname")
K_PURPOSE = ("purpose", "event", "type", "bmtype", "remark")


class Throttled(Exception):
    pass


class ShapeChanged(Exception):
    """응답 구조가 예상과 다르다. 0건으로 삼키면 안 되는 상황."""


def get(url: str, referer: str = "", retries: int = 3) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        **({"Referer": referer} if referer else {}),
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


def bootstrap_token() -> str:
    """HKEX 위젯은 페이지에 박아둔 토큰을 요구한다. 페이지를 받아 뽑아온다."""
    html = get(PAGE)
    for pat in (r'["\']?token["\']?\s*[:=]\s*["\']([A-Za-z0-9%\-_.=]{16,})["\']',
                r'[?&]token=([A-Za-z0-9%\-_.=]{16,})'):
        m = re.search(pat, html)
        if m:
            return urllib.parse.unquote(m.group(1))
    raise ShapeChanged(
        "HKEX 페이지에서 위젯 토큰을 찾지 못했습니다. 페이지 구조가 바뀌었을 수 있습니다.\n"
        f"  확인: {PAGE}\n"
        f"  받은 본문 앞부분: {html[:300]!r}")


def unwrap(text: str) -> dict:
    """JSONP 껍데기(jQuery123_456({...}))를 벗긴다."""
    s = text.strip()
    m = re.match(r"^[A-Za-z_$][\w$.]*\((.*)\)\s*;?\s*$", s, re.S)
    if m:
        s = m.group(1)
    return json.loads(s)


def pick(row: dict, keys) -> str:
    """키 이름 후보 중 먼저 걸리는 값. 대소문자·언더스코어 차이를 흡수한다."""
    norm = {re.sub(r"[^a-z]", "", k.lower()): v for k, v in row.items()}
    for k in keys:
        v = norm.get(re.sub(r"[^a-z]", "", k.lower()))
        if v not in (None, ""):
            return str(v).strip()
    return ""


def find_rows(body):
    """응답 어디에 목록이 들어 있든 찾아낸다. HKEX 는 data.content 에 담아왔는데
    개편에 대비해 '문자열 키를 가진 dict 의 리스트' 중 가장 큰 것을 고른다."""
    best = []

    def walk(node):
        nonlocal best
        if isinstance(node, list):
            if node and all(isinstance(x, dict) for x in node) and len(node) > len(best):
                best = node
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(body)
    return best


def norm_date(s: str) -> str:
    """HKEX 는 '2026/08/12', '12/08/2026', '20260812', '12 Aug 2026' 등으로 준다."""
    s = s.strip()
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:                                  # 일/월/연 — 홍콩 표기
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return "%s-%s-%s" % m.groups()
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})", s)
    if m:
        mon = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
               "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}.get(m.group(2).lower())
        if mon:
            return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(1)):02d}"
    return ""


def kind_of(purpose: str) -> str:
    for rx, ko in PURPOSE:
        if rx.search(purpose or ""):
            return ko
    return "이사회"


def fetch_chunk(a: date, b: date, token: str, probe: bool = False):
    qid = int(time.time() * 1000)
    q = urllib.parse.urlencode({
        "lang": "eng", "token": token, "qid": qid,
        "callback": f"jQuery{qid}_{qid}",
        "from": a.strftime("%Y%m%d"), "to": b.strftime("%Y%m%d"),
        "rows": 2000, "start": 0,
    })
    url = WIDGET + "?" + q
    for wait in BACKOFF:
        if wait:
            print(f"    throttled, {wait}s 대기 후 재시도", file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            text = get(url, referer=PAGE)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print(f"    응답 이상 ({e})", file=sys.stderr, flush=True)
            continue
        if probe:
            print(text[:2000])
            return []
        try:
            body = unwrap(text)
        except ValueError:
            print(f"    JSON 아님, 앞부분: {text[:200]!r}", file=sys.stderr, flush=True)
            continue

        raw = find_rows(body)
        if not raw:
            # 진짜 0건인지 구조가 바뀐 건지 구분이 안 된다. 삼키지 않는다.
            raise ShapeChanged(
                f"{a}~{b}: 목록을 찾지 못했습니다. 파라미터나 응답 구조가 바뀐 듯합니다.\n"
                f"  응답 앞부분: {text[:400]!r}\n"
                f"  --probe 로 원문을 떠서 키 이름을 확인하세요.")

        out, seen = [], set()
        for r in raw:
            d = norm_date(pick(r, K_DATE))
            code = re.sub(r"\D", "", pick(r, K_CODE))
            if not d or not code:
                continue
            code = code.zfill(5)
            if (d, code) in seen:
                continue
            seen.add((d, code))
            purpose = pick(r, K_PURPOSE)
            out.append({
                "date": d,
                "code": code,
                "name": pick(r, K_NAME),
                "fy": purpose,
                "kind": kind_of(purpose),
                "sector": "",
                "market": "",
            })
        if not out:
            raise ShapeChanged(
                f"{a}~{b}: {len(raw)}행을 받았지만 날짜·코드를 못 읽었습니다. "
                f"키 이름이 바뀐 듯합니다.\n  첫 행: {raw[0]!r}")
        return out
    raise Throttled(f"{a}~{b}: 유효 응답 실패")


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
        "source": "HKEX Board Meeting Calendar",
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
    token = bootstrap_token()
    print(f"  토큰 확보 ({len(token)}자)")
    if probe:
        fetch_chunk(start, min(start + timedelta(days=6), end), token, probe=True)
        return

    by_day = load_cache()
    failed = []

    # 주 단위로 끊는다. 한 주가 실패해도 나머지 주는 남는다.
    a = start
    while a <= end:
        b = min(a + timedelta(days=6), end)
        span = [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
        if all(d in by_day for d in span):
            print(f"{a}~{b} (캐시)", flush=True)
        else:
            try:
                rows = fetch_chunk(a, b, token)
                # 받은 구간은 통째로 '수집 성공'으로 표시한다. 0건인 날도 마찬가지 —
                # 그래야 '발표 없는 날'과 '못 받은 날'이 구분된다.
                for d in span:
                    by_day[d] = []
                for r in rows:
                    if r["date"] in by_day:
                        by_day[r["date"]].append(r)
                print(f"{a}~{b} {len(rows):>4}건", flush=True)
                save(by_day, start, end)
            except Exception as e:
                failed += span
                print(f"{a}~{b} 실패: {e}", file=sys.stderr, flush=True)
            time.sleep(1.5)
        a = b + timedelta(days=1)

    n, days = save(by_day, start, end)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    if failed:
        print(f"미수집 {len(failed)}일: {failed[0]} ~ {failed[-1]} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    probe = "--probe" in sys.argv
    s = date.fromisoformat(a[0]) if a else date.today()
    e = date.fromisoformat(a[1]) if len(a) > 1 else s + timedelta(days=60)
    main(s, e, probe)
