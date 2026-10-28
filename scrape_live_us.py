"""미국 발표 속보 — 오늘·어제 발표 종목을 3분마다 본다.

회원님이 "실적 나온 지 3시간이 지났는데 왜 반영이 안 되냐"고 짚으신 자리다
(브링커 EAT, 장전 발표). 원인은 둘이었다.

- 실적 수치(`scrape_fin.py`)는 종목마다 **하루 한 번**만 다시 받았다(`ts` 가
  날짜라서 '오늘 받았다'면 발표 전 아침에 받은 것도 끝난 것으로 쳤다). 일본에서
  한 번 겪은 '같은 날은 앞뒤를 못 가린다'와 같은 병이다.
- 회사가 실적을 내는 그 순간 SEC 에 8-K(Item 2.02)를 낸다. 그런데 그걸 보는
  `scrape_pr_us.py` 는 30분에 한 번, 시총 큰 순 60종목만 봤다.

그래서 여기서는 **오늘·어제 발표 예정 종목만** 좁혀서 3분마다 본다.

  1. SEC 제출 목록(submissions JSON)에 발표일 이후의 실적 8-K 가 생겼나
     → 생겼으면 **실제 접수 시각**(acceptanceDateTime, 미국 동부 시각)을 적는다.
        나스닥이 주는 건 장전/장후뿐이라 화면 시각은 어림(「경」)이었다.
  2. 8-K 가 있는데 실제 EPS 가 아직 없으면 나스닥 EPS 를 다시 묻는다.

결과는 `data/live_us.json`. build.py 가 시각(정확도 1)과 EPS 를 덮어 싣는다.
이미 본 것은 다시 묻지 않으므로 평소에는 몇십 요청이다.
"""
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import scrape_seg_sec as ss
from scrape_seg_edgar import get as sec_get
from scrape_fin import get as nas_get, NAS_EPS, NAS_UA

HERE = Path(__file__).parent
OUT = HERE / "data" / "live_us.json"
SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
ET = ZoneInfo("America/New_York")

MAX = int(os.environ.get("LIVE_MAX", "250"))          # 한 바퀴에 볼 종목 수
SECS = int(os.environ.get("LIVE_SECS", "100"))        # 스스로 끊는 시간
PAUSE = float(os.environ.get("LIVE_PAUSE", "0.12"))   # SEC 는 초당 10건까지 허용
KEEP_DAYS = 4


def et_today():
    return datetime.now(ET).date()


def targets(today):
    """오늘·어제(미국 동부 날짜) 발표 예정 종목. 시총 큰 순."""
    f = HERE / "data" / "earnings_us.json"
    try:
        rows = json.loads(f.read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return []
    days = {today.isoformat(), (today - timedelta(days=1)).isoformat()}
    # 월요일이면 금요일 장후 발표도 아직 신선하다.
    if today.weekday() == 0:
        days.add((today - timedelta(days=3)).isoformat())
    best = {}
    for r in rows:
        if r.get("date") in days and r.get("code"):
            cur = best.get(r["code"])
            if not cur or r["date"] > cur["date"]:
                best[r["code"]] = r
    return sorted(best.values(), key=lambda r: -(r.get("cap") or 0))[:MAX]


def accepted_et(raw, filing_date=""):
    """acceptanceDateTime -> 'YYYY-MM-DD HH:MM'(미국 동부 시각).

    값은 **UTC** 다(끝의 Z 그대로). 처음에 동부 시각이라 보고 그대로 적었더니
    장전에 낸 AEP 가 10:59(장중), 장후에 낸 벤타스가 20:32 로 찍혔다 — 첫
    실데이터에서 바로 드러났다. 동부로 옮겨 적는다(서머타임은 zoneinfo 가 안다).
    """
    try:
        t = datetime.fromisoformat(raw.replace("Z", "")[:19]).replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return t.astimezone(ET).strftime("%Y-%m-%d %H:%M")


def _fix_v1(prev):
    """v1 파일은 UTC 값을 동부라 적었다. 한 번만 옮겨 적는다."""
    def conv(v):
        try:
            t = datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return v
        return t.astimezone(ET).strftime("%Y-%m-%d %H:%M")
    for rec in prev.get("stocks", {}).values():
        if rec.get("accepted"):
            rec["accepted"] = conv(rec["accepted"])
    typ = prev.get("typical", {})
    for sym, rec in prev.get("stocks", {}).items():
        if rec.get("accepted") and sym in typ:
            typ[sym] = rec["accepted"][11:16]
    return prev


def find_8k(cik, since):
    """발표일(since) 이후 실적 8-K. (접수번호, 접수 시각, 원문 주소) 또는 None."""
    blob = sec_get(SUBS.format(cik=cik), binary=False)
    if blob is None:
        return None
    try:
        r = json.loads(blob)["filings"]["recent"]
    except (ValueError, KeyError):
        return None
    for i, form in enumerate(r.get("form", [])):
        fd = r["filingDate"][i]
        if fd < since:
            break                                # 최근 것부터 온다
        if form != "8-K" or "2.02" not in (r.get("items") or [""])[i]:
            continue
        acc = r["accessionNumber"][i].replace("-", "")
        at = accepted_et(r.get("acceptanceDateTime", [""])[i], fd)
        doc = DOC.format(cik=cik, acc=acc, name=r.get("primaryDocument", [""])[i])
        return acc, at, doc
    return None


def eps_actual(sym, want_period):
    """나스닥 EPS 중 이번 분기 실제치. want_period 는 'Sep 2026' 꼴."""
    try:
        d = nas_get(NAS_EPS.format(sym=sym), NAS_UA)
    except Exception:
        return None
    for e in (d.get("data") or {}).get("earningsPerShare") or []:
        if e.get("type") == "UpcomingQuarter" or not e.get("earnings"):
            continue
        if e.get("period") == want_period:
            return {"period": e.get("period"), "consensus": e.get("consensus"),
                    "actual": e.get("earnings")}
    return None


def period_of(fy):
    """'2026년 9월 분기' -> 'Sep 2026'."""
    import re
    m = re.match(r"(\d{4})년\s*(\d{1,2})월", fy or "")
    if not m:
        return ""
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{names[int(m.group(2)) - 1]} {m.group(1)}"


CIK_CACHE = HERE / ".cache" / "cik.json"      # 커밋하지 않는다(data/ 밖)


def cik_map_cached():
    """SEC 티커 목록(수 MB)을 3분마다 받지 않는다 — 여섯 시간 캐시.

    이 실행은 다섯 시간 동안 3분마다 돈다. 매번 받으면 SEC 에 큰 파일을 백 번
    두드리고, 한 번 막히면 그 바퀴가 아무것도 못 쓰고 죽는다. 막히면 헌 캐시라도 쓴다.
    """
    try:
        fresh = time.time() - CIK_CACHE.stat().st_mtime < 6 * 3600
        cached = json.loads(CIK_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fresh, cached = False, None
    if fresh and cached:
        return cached
    try:
        m = ss.cik_map()
    except ss.Blocked:
        if cached:
            return cached
        raise
    CIK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CIK_CACHE.write_text(json.dumps(m), encoding="utf-8")
    return m


def main():
    t0 = time.time()
    try:
        prev = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        prev = {}
    if prev and prev.get("v") != 2:
        prev = _fix_v1(prev)
    old = prev.get("stocks", {})
    # 회사마다 지난번 8-K 접수 시각(동부 HH:MM). 지우지 않고 쌓는다 —
    # 다음 분기 예정 시각을 어림하는 데 쓴다(회사는 대개 같은 시각에 낸다).
    typical = prev.get("typical", {})
    today = et_today()
    floor = (today - timedelta(days=KEEP_DAYS)).isoformat()
    stocks = {s: v for s, v in old.items() if v.get("date", "") >= floor}
    try:
        cikmap = cik_map_cached()
    except ss.Blocked as e:
        print(f"  SEC 티커 목록 막힘: {e} — 이번 바퀴는 EPS 만 본다", file=sys.stderr)
        cikmap = {}
    seen_8k = seen_eps = new_8k = new_eps = 0
    for r in targets(today):
        if time.time() - t0 > SECS:
            print("  시간이 다 됐다 — 다음 바퀴에서 이어서")
            break
        sym, day = r["code"], r["date"]
        rec = stocks.get(sym)
        if not rec or rec.get("date") != day:
            rec = stocks[sym] = {"date": day, "period": period_of(r.get("fy"))}
        try:
            if not rec.get("acc"):
                cik = ss.cik_of(cikmap, sym)
                if not cik:
                    continue
                seen_8k += 1
                hit = find_8k(cik, day)
                time.sleep(PAUSE)
                if hit:
                    rec["acc"], rec["accepted"], rec["url"] = hit
                    rec["found"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
                    new_8k += 1
                    if rec["accepted"]:
                        typical[sym] = rec["accepted"][11:16]
                    print(f"  8-K {sym} {rec['accepted'] or '(시각 불명)'}")
            if rec.get("acc") and not rec.get("eps") and rec.get("period"):
                seen_eps += 1
                e = eps_actual(sym, rec["period"])
                time.sleep(0.3)
                if e:
                    rec["eps"] = e
                    new_eps += 1
                    print(f"  EPS {sym} {e['actual']} (예상 {e['consensus']})")
        except ss.Blocked as e:
            print(f"  막힘: {e} — 이번 바퀴는 여기까지", file=sys.stderr)
            break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "v": 2,
        "source": "SEC 8-K(Item 2.02) 접수 시각 + 나스닥 EPS",
        "note": "accepted·typical 은 미국 동부 시각. stocks 는 오늘·어제 발표 종목만, typical 은 회사별 지난번 접수 시각.",
        "stocks": stocks, "typical": typical}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"미국 속보: 8-K 확인 {seen_8k} · 새로 {new_8k} | EPS 확인 {seen_eps} · 새로 {new_eps}"
          f" ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    main()
