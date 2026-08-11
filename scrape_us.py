# -*- coding: utf-8 -*-
"""
미국 실적발표 스케줄 스크래퍼 — 출처: Nasdaq Earnings Calendar
https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD

닛케이와 마찬가지로 날짜 '범위' 조회가 없다. 하루씩 도는데, 대신 페이징은 없어서
하루 한 요청이면 끝난다. 응답이 JSON이라 파싱은 훨씬 편하다.

일본 소스에 없던 게 여기엔 있다.
  - 발표 시각: 장전(pre-market) / 장후(after-hours)
  - 시가총액: '오늘 발표하는 700개 중 뭐가 큰 건지'를 기계가 안다
  - EPS 컨센서스

거래소(NYSE/나스닥)는 이 API가 안 준다. 나스닥트레이더가 내는 심볼 디렉터리
두 파일을 처음에 한 번 받아 심볼→거래소로 붙인다. 실패해도 죽지 않고 빈칸으로 둔다.

결과: data/earnings_us.json
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

BASE = "https://api.nasdaq.com/api/calendar/earnings"
SYMDIR = "https://www.nasdaqtrader.com/dynamic/SymDir/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = Path(__file__).parent / "data" / "earnings_us.json"

# 나스닥도 연속 요청을 오래 끌면 403이나 빈 껍데기를 돌려준다.
# 닛케이만큼 사납지는 않지만 물러서는 폭은 같은 방식으로 잡았다.
BACKOFF = (0, 15, 45, 120)

# 연속으로 이만큼 실패하면 그 시장은 접는다.
# 백오프는 '잠깐 막힌 것'을 견디는 장치지 '차단된 상대'를 뚫는 장치가 아니다.
# 상대가 아예 막아버렸는데 계속 두들기면 몇 시간을 태우고 상대에게도 민폐다.
# 받아둔 만큼은 저장돼 있으니, 다시 돌리면 못 받은 날부터 이어서 받는다.
GIVE_UP_AFTER = 3

# otherlisted.txt 의 Exchange 열 코드.
EXCH = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca",
        "Z": "Cboe BZX", "V": "IEX"}

MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}


class Throttled(Exception):
    """레이트리밋에 걸리면 '오늘은 발표가 없다'와 구분되지 않는 응답이 온다.
    이걸 0건으로 삼키면 발표가 몰린 날이 조용히 빈 날로 기록된다."""


def get(url: str, retries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nasdaq.com/market-activity/earnings",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
            print(f"    retry {attempt+1} ({e})", file=sys.stderr)
    return b""


def fetch_valid(day: date):
    """정상 응답이라는 증거가 있어야 한다. 없으면 물러섰다 다시 온다.

    판정 기준은 **응답 봉투**다. status.rCode 가 200 으로 온 JSON 이면
    나스닥이 질문을 받아 대답한 것이고, rows 가 비어 있으면 그 날은 진짜 0건이다.
    막히면 HTTP 403 이나 JSON 아닌 본문, 혹은 rCode!=200 이 오므로 위에서 걸러진다.

    처음에는 0건을 인정하는 조건으로 message 필드가 채워져 있을 것을 요구했는데,
    나스닥은 발표가 없는 날 message 를 비워 보내기도 한다. 그 바람에 주말처럼
    원래 0건인 날이 통째로 '수집 실패'로 기록되면서 하루당 8분씩 재시도를 태웠다.
    발표가 없는 날을 못 받은 날로 적는 것도 캘린더가 거짓말을 하는 것이다.
    """
    url = BASE + "?" + urllib.parse.urlencode({"date": day.isoformat()})
    for wait in BACKOFF:
        if wait:
            print(f"    throttled, {wait}s 대기 후 재시도", file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            body = json.loads(get(url).decode("utf-8", "replace"))
        except (ValueError, urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            print(f"    응답 이상 ({e})", file=sys.stderr, flush=True)
            continue

        rcode = (body.get("status") or {}).get("rCode")
        if rcode != 200:
            print(f"    rCode={rcode!r} — 정상 응답이 아니다", file=sys.stderr, flush=True)
            continue
        return (body.get("data") or {}).get("rows") or []
    raise Throttled(f"{day}: 유효 응답 실패")


def cap_to_busd(s: str) -> float:
    """'$3,145,678,901,234' -> 3145.7 (십억 달러). 없으면 0."""
    digits = re.sub(r"[^\d]", "", s or "")
    if not digits:
        return 0.0
    return round(int(digits) / 1e9, 1)


def quarter_of(fq: str):
    """'Jun/2026' -> ('2026년 6월 분기', '2Q'). 회사 회계연도가 아니라
    분기말이 속한 '역년 분기'다. 회계연도 시작월을 소스가 안 주기 때문에
    1Q/2Q 를 회사 기준으로 매길 수가 없다. 없는 걸 지어내지 않는다."""
    m = re.match(r"([A-Za-z]{3})/(\d{4})", (fq or "").strip())
    if not m:
        return fq or "", ""
    mon, year = MONTHS.get(m.group(1), 0), m.group(2)
    if not mon:
        return fq, ""
    return f"{year}년 {mon}월 분기", f"{(mon - 1) // 3 + 1}Q"


def load_exchanges() -> dict:
    """심볼 -> 거래소. 두 파일 다 '|' 구분 텍스트고 마지막 줄은 파일 생성시각이다.
    받아오지 못해도 수집 자체는 계속한다 — 거래소는 있으면 좋은 정보지 필수가 아니다."""
    out = {}
    try:
        text = get(SYMDIR + "nasdaqlisted.txt").decode("utf-8", "replace")
        for line in text.splitlines()[1:]:
            f = line.split("|")
            if len(f) > 3 and f[0] and not line.startswith("File Creation"):
                out[f[0].strip()] = "NASDAQ"
    except Exception as e:
        print(f"  나스닥 심볼 목록 실패: {e}", file=sys.stderr)
    try:
        text = get(SYMDIR + "otherlisted.txt").decode("utf-8", "replace")
        for line in text.splitlines()[1:]:
            f = line.split("|")
            if len(f) > 2 and f[0] and not line.startswith("File Creation"):
                out.setdefault(f[0].strip(), EXCH.get(f[2].strip(), ""))
    except Exception as e:
        print(f"  기타 상장 목록 실패: {e}", file=sys.stderr)
    print(f"  거래소 매핑 {len(out):,}개")
    return out


def scrape_day(day: date, exch: dict):
    out, seen = [], set()
    for r in fetch_valid(day):
        sym = (r.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        fy, kind = quarter_of(r.get("fiscalQuarterEnding", ""))
        out.append({
            "date": day.isoformat(),
            "code": sym,
            "name": (r.get("name") or "").strip(),
            "fy": fy,
            "kind": kind,
            "sector": "",                      # 이 소스에는 업종이 없다
            "market": exch.get(sym, ""),
            "time": (r.get("time") or "").strip(),
            "cap": cap_to_busd(r.get("marketCap", "")),
            "eps": (r.get("epsForecast") or "").strip(),
        })
    return out


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
        "source": "Nasdaq Earnings Calendar",
        "source_url": "https://www.nasdaq.com/market-activity/earnings",
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


def main(start: date, end: date):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    by_day = load_cache()
    # 받을 날이 하나도 없으면 심볼 디렉터리도 굳이 받지 않는다.
    pending = [i for i in range((end - start).days + 1)
               if (start + timedelta(days=i)).isoformat() not in by_day]
    exch = load_exchanges() if pending else {}
    failed = []

    day, streak = start, 0
    while day <= end:
        key = day.isoformat()
        if key in by_day:
            print(f"{key} {len(by_day[key]):>4}건 (캐시)", flush=True)
        else:
            try:
                by_day[key] = scrape_day(day, exch)
            except Exception as e:
                failed.append(key)
                streak += 1
                print(f"{key} 실패: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print(f"연속 {streak}일 실패 — 여기서 멈춘다. 받아둔 만큼은 저장돼 있고, "
                          f"다시 돌리면 이어서 받는다.", file=sys.stderr, flush=True)
                    break
            else:
                streak = 0
                print(f"{key} {len(by_day[key]):>4}건", flush=True)
                save(by_day, start, end)
            time.sleep(1.2)
        day += timedelta(days=1)

    n, days = save(by_day, start, end)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    if failed:
        print(f"미수집 {len(failed)}일: {', '.join(failed)} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = sys.argv[1:]
    s = date.fromisoformat(a[0]) if a else date.today()
    e = date.fromisoformat(a[1]) if len(a) > 1 else s + timedelta(days=60)
    main(s, e)
