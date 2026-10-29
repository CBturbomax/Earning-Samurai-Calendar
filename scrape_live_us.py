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
from markets import excluded
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pr_table
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
# 보도자료 표 읽기 규칙 판. 올리면 이미 읽은 종목도 다시 읽는다(pr_table 을 고칠 때).
# 2: 누계 열 거르기(직전 분기의 0.5~2배) · 영업이익·순이익 전년 대조 0.6%
# 3: 직전 분기 0.6~1.7배 · 전년 분기 0.5~2.0배 (CVLT·LECO 오검출)
PR_V = 3


def et_today():
    return datetime.now(ET).date()


def _caps_meta():
    """caps.json 의 업종·세부 업종 — 은행·리츠를 수집 단계에서부터 뺀다."""
    try:
        d = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8"))
        return d.get("sectors", {}), d.get("industries", {})
    except (ValueError, OSError):
        return {}, {}


def _skip(mkt, code, name, sector=""):
    sec, ind = _META
    k = f"{mkt}:{code}"
    i = ind.get(k, "")
    return excluded(mkt, name or "", sector or sec.get(k, ""), "" if i == "-" else i)


_META = _caps_meta()


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
        if r.get("date") in days and r.get("code") and not _skip("us", r["code"], r.get("name")):
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
    blob = sec_get(SUBS.format(cik=cik), timeout=15, binary=False)
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


FILES = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"


def ex99_html(cik, acc):
    """8-K 의 보도자료(EX-99) HTML. 이름이 제각각이라 크기로도 고른다(scrape_pr_us 와 같은 규칙)."""
    blob = sec_get(FILES.format(cik=cik, acc=acc), timeout=15, binary=False)
    if not blob:
        return None
    try:
        items = json.loads(blob)["directory"]["item"]
    except (ValueError, KeyError):
        return None
    htm = [(it["name"], int(it.get("size") or 0)) for it in items
           if it["name"].lower().endswith((".htm", ".html"))
           and not re.match(r"index|.*-index", it["name"], re.I)]
    ex = [t for t in htm if re.search(r"ex[-_]?99|991|992|press|earnings", t[0], re.I)]
    if not (ex or htm):
        return None
    name = max(ex or htm, key=lambda t: t[1])[0]
    raw = sec_get(DOC.format(cik=cik, acc=acc, name=name), timeout=20)
    return raw.decode("utf-8", "replace") if raw else None


_FIN = None


def sec_prior(sym, period):
    """전년 같은 분기 SEC 수치와, 그 사이에 이미 가진 분기 매출들(`known`).

    보도자료 표의 전년 열을 이것과 맞춰 본다 — 맞는 열을 못 찾으면 안 싣는다.
    `known` 은 표에 직전 분기 열이 끼어 있을 때(BKR·BNL) 그 열을 건너뛰는 데 쓴다.
    """
    global _FIN
    if _FIN is None:
        try:
            _FIN = json.loads((HERE / "data" / "financials.json").read_text(encoding="utf-8"))["stocks"]
        except (ValueError, OSError, KeyError):
            _FIN = {}
    m = re.match(r"([A-Za-z]{3})\s+(\d{4})", period or "")
    if not m:
        return None
    mon = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split().index(m.group(1)) + 1
    year = int(m.group(2))
    pts = (_FIN.get(sym) or {}).get("points") or []

    def near(y):
        tgt = date(y, mon, 28)
        best = None
        for p in pts:
            try:
                g = abs((date.fromisoformat(p["end"]) - tgt).days)
            except (KeyError, ValueError):
                continue
            if g <= 40 and (best is None or g < best[0]):
                best = (g, p)
        return best[1] if best else None
    pr, cur = near(year - 1), near(year)
    if not pr or not pr.get("rev"):
        return None
    known = [p["rev"] for p in pts if p.get("rev") and p["end"] > pr["end"]
             and (not cur or p["end"] < cur["end"])]
    return {"rev": pr["rev"], "opi": pr.get("opi"), "ni": pr.get("ni"),
            "known": known, "end": pr["end"]}


def eps_actual(sym, want_period):
    """나스닥 EPS 중 이번 분기 실제치. want_period 는 'Sep 2026' 꼴."""
    try:
        d = nas_get(NAS_EPS.format(sym=sym), NAS_UA, timeout=15)
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


def save(stocks, typical, status):
    """중간중간 쓴다. 끝에 한 번만 쓰면 바퀴가 시간에 잘렸을 때 아무것도 안 남는다.

    `status` 는 이 바퀴가 어디까지 갔고 무엇에 막혔는지다. GitHub Actions 로그를
    밖에서 못 볼 때(이 저장소를 돌보는 세션이 그랬다) 이 파일만 보고 진단한다.
    """
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "v": 2,
        "source": "SEC 8-K(Item 2.02) 접수 시각 + 나스닥 EPS",
        "note": "accepted·typical 은 미국 동부 시각. stocks 는 오늘·어제 발표 종목만, typical 은 회사별 지난번 접수 시각.",
        "status": status, "stocks": stocks, "typical": typical}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)


def main():
    t0 = time.time()
    now = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
    st = {"started": now(), "finished": "", "stage": "시작", "targets": 0,
          "checked8k": 0, "new8k": 0, "checkedEps": 0, "newEps": 0, "errors": []}

    def err(msg):
        st["errors"] = (st["errors"] + [msg[:200]])[-8:]
        print("  " + msg, file=sys.stderr, flush=True)

    save(stocks, typical, st)
    try:
        st["stage"] = "SEC 티커 목록"
        try:
            cikmap = cik_map_cached()
        except ss.Blocked as e:
            err(f"SEC 티커 목록 막힘: {e}")
            cikmap = {}
        tg = targets(today)
        st["targets"] = len(tg)
        # **본 지 오래된 종목부터.** 시총 순으로만 돌면 한 바퀴 100초에 앞의 120개쯤만
        # 보고 끝나서, 뒤쪽 종목(브링커 EAT)은 영영 차례가 안 왔다(첫 진단에서 드러남).
        # 이미 끝난 종목(8-K 와 EPS 가 다 있다)은 맨 뒤로 보낸다.
        def order(r):
            rec = stocks.get(r["code"]) or {}
            done = bool(rec.get("acc") and (rec.get("eps") or not rec.get("period")) and rec.get("prv") == PR_V)
            return (done, rec.get("chk", ""))
        tg.sort(key=order)
        st["stage"] = "종목 훑기"
        streak = 0
        for i, r in enumerate(tg):
            if time.time() - t0 > SECS:
                st["stage"] = f"시간 끝 ({i}/{len(tg)})"
                break
            sym, day = r["code"], r["date"]
            rec = stocks.get(sym)
            if not rec or rec.get("date") != day:
                rec = stocks[sym] = {"date": day, "period": period_of(r.get("fy"))}
            if rec.get("acc") and (rec.get("eps") or not rec.get("period")) and rec.get("prv") == PR_V:
                continue                         # 다 받았다
            rec["chk"] = now()
            try:
                if not rec.get("acc"):
                    cik = ss.cik_of(cikmap, sym) if cikmap else None
                    if cik:
                        st["checked8k"] += 1
                        hit = find_8k(cik, day)
                        time.sleep(PAUSE)
                        if hit:
                            rec["acc"], rec["accepted"], rec["url"] = hit
                            rec["found"] = now()
                            st["new8k"] += 1
                            if rec["accepted"]:
                                typical[sym] = rec["accepted"][11:16]
                            print(f"  8-K {sym} {rec['accepted'] or '(시각 불명)'}")
                # 보도자료 손익 표 — 8-K 가 잡힌 뒤 한 번(받기 실패면 다음 바퀴에 다시).
                if rec.get("acc") and rec.get("prv") != PR_V and cikmap:
                    pri = sec_prior(sym, rec.get("period"))
                    if not pri:
                        rec["pr"] = None              # 대조할 SEC 전년 수치가 없다 — 안 싣는다
                        rec["prv"] = PR_V
                    else:
                        cik = ss.cik_of(cikmap, sym)
                        raw = ex99_html(cik, rec["acc"]) if cik else None
                        time.sleep(PAUSE)
                        if raw is not None:
                            got = pr_table.read(raw, pri)
                            if got:
                                y, m, d = pri["end"].split("-")
                                got["end"] = f"{int(y) + 1}-{m}-{d}"
                                st["newPr"] = st.get("newPr", 0) + 1
                                print(f"  8-K 표 {sym} 매출 {got['rev'] / 1e6:,.0f}M")
                            rec["pr"] = got
                            rec["prv"] = PR_V
                if rec.get("acc") and not rec.get("eps") and rec.get("period"):
                    st["checkedEps"] += 1
                    e = eps_actual(sym, rec["period"])
                    time.sleep(0.3)
                    if e:
                        rec["eps"] = e
                        st["newEps"] += 1
                        print(f"  EPS {sym} {e['actual']} (예상 {e['consensus']})")
                streak = 0
            except ss.Blocked as e:
                # 한 번 막혔다고 바퀴를 접지 않는다 — 다섯 번 연달아일 때만.
                streak += 1
                err(f"{sym} 막힘: {e}")
                if streak >= 5:
                    st["stage"] = f"연속 막힘으로 중단 ({i}/{len(tg)})"
                    break
            if i % 15 == 14:
                save(stocks, typical, st)
        else:
            st["stage"] = "끝"
    except Exception as e:                       # 무엇이든 남기고 죽는다
        import traceback
        err("예외: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        st["stage"] = "예외로 중단"
    st["finished"] = now()
    st["secs"] = round(time.time() - t0)
    save(stocks, typical, st)
    print(f"미국 속보: {st}")


if __name__ == "__main__":
    _t0 = time.time()
    main()
    # 나스닥 캘린더에 없는 미국 상장 외국 회사(MICC 등)의 실적 6-K — 같은 3분 루프에서,
    # 이 실행에 남은 시간만큼(워크플로의 timeout 110초 안). 못 본 종목은 다음 바퀴가 잇는다.
    left = 100 - (time.time() - _t0)
    if left > 15:
        os.environ["FPI_SECS"] = str(int(left - 10))
        try:
            import scrape_us_fpi
            scrape_us_fpi.main()
        except Exception as e:                                # noqa: BLE001
            print(f"외국 상장사 6-K 실패: {e}", file=sys.stderr)
