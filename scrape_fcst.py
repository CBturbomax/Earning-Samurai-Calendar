"""연간 실적·컨센서스·밸류에이션 — 상세창의 '연간 표'.

회원님이 증권사 화면처럼 「직전년도 실적 · 당해년도 · 차기 · 차차기」 매출·영업이익·
순이익과 PER·PBR·PSR 표를 상세창에 달라고 하셨다("실적 볼 때 이런 것도 중시한다").

소스는 stockanalysis 의 forecast 페이지(S&P Global 컨센서스)와 statistics
페이지다. 실적 수치와 같은 곳이라 종목 주소 규칙(`scrape_fin_intl.url_for`)을
그대로 쓴다. **무료로 열리는 범위만 담는다**(실측 2026-10, 일본담배 2914):

  - 연간 표: 지난 해들의 실적 + **올해(1FY) 추정**의 매출·영업이익·순이익·EPS.
    그 뒤 해는 `[PRO]` 로 가려져 온다.
  - 머리 요약: 올해·내년(2FY)의 **매출·EPS** 추정. 내년 영업이익은 없다.
  - statistics: 지금 시총(현지 통화)·PBR.

없는 칸은 비워 둔다 — 지어 넣지 않는다. 내년 순이익만은 내년 EPS × 올해 주식수
(올해 순이익 ÷ 올해 EPS)로 옮기고 그렇다고 적는다(`ni2_from_eps`).

결과: data/forecast.json  {"stocks": {"jp:2914": {...}}}
"""
import json
from markets import excluded
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scrape_fin_intl import Throttled, get, unflatten, url_for

HERE = Path(__file__).parent
OUT = HERE / "data" / "forecast.json"
PER_RUN = int(os.environ.get("FCST_PER_RUN", "120"))
SECS = int(os.environ.get("FCST_SECS", "200"))
PAUSE = float(os.environ.get("FCST_PAUSE", "0.6"))
STALE_DAYS = int(os.environ.get("FCST_STALE_DAYS", "7"))
NEAR_DAYS = 4                     # 발표일 ±4일은 하루 한 번(컨센서스가 그때 바뀐다)
MIN_CAP = float(os.environ.get("FCST_MIN_CAP", "0.5"))   # 시총(십억 달러) 이상만
GIVE_UP_AFTER = 6
FCST_VER = 1


def page(market, code, kind):
    """financials 주소를 forecast·statistics 주소로 바꾼다."""
    u = url_for(market, code)
    if not u:
        return ""
    return u.replace("/financials/__data.json?p=quarterly", f"/{kind}/__data.json")


def nodes(txt):
    """SvelteKit __data.json 의 노드들을 되살려 하나의 목록으로."""
    try:
        d = json.loads(txt)
    except ValueError:
        return []
    out = []
    for n in d.get("nodes") or []:
        if isinstance(n, dict) and isinstance(n.get("data"), list):
            out.append(unflatten(n["data"]))
    return out


def find_key(o, key, depth=0):
    if depth > 8:
        return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = find_key(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = find_key(v, key, depth + 1)
            if r is not None:
                return r
    return None


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_forecast(txt):
    ns = nodes(txt)
    info = next((n.get("info") for n in ns if isinstance(n, dict) and n.get("info")), {}) or {}
    est = next((n.get("estimates") for n in ns if isinstance(n, dict) and n.get("estimates")), None)
    if not est:
        return None
    annual = ((est.get("table") or {}).get("annual")) or {}
    dates = annual.get("dates") or []
    last = annual.get("lastDate") if isinstance(annual.get("lastDate"), str) else ""
    years = []
    for i, d in enumerate(dates):
        if not isinstance(d, str):
            continue

        def col(k):
            arr = annual.get(k) or []
            return num(arr[i]) if i < len(arr) else None
        row = {"end": d, "rev": col("revenue"), "opi": col("operatingIncome"),
               "ni": col("netIncome"), "eps": col("eps")}
        if all(row[k] is None for k in ("rev", "opi", "ni", "eps")):
            continue                         # [PRO] 로 가려진 해
        row["est"] = 0 if (last and d <= last) else 1
        years.append(row)
    if not last:
        # lastDate 가 없으면 오늘보다 앞에서 끝난 해를 실적으로 본다.
        today = date.today().isoformat()
        for y in years:
            y["est"] = 0 if y["end"] < today else 1
    stats = ((est.get("table") or {}).get("stats") or est.get("stats") or {})
    stats = (stats.get("annual") if isinstance(stats.get("annual"), dict) else stats) or {}
    nxt = {
        "rev": num((stats.get("revenueNext") or {}).get("this")),
        "eps": num((stats.get("epsNext") or {}).get("this")),
    }
    curr = info.get("curr") or {}
    quote = info.get("quote") or {}
    return {
        "years": years, "next": nxt,
        "price": num(quote.get("p")),
        "curPrice": curr.get("price") if isinstance(curr.get("price"), str) else "",
        "curFin": curr.get("financial") if isinstance(curr.get("financial"), str) else "",
    }


def parse_stats(txt):
    """statistics 페이지의 {id: 숫자}. hover 에 덜 반올림된 값이 있다."""
    out = {}

    def walk(o, depth=0):
        if depth > 10:
            return
        if isinstance(o, dict):
            if isinstance(o.get("id"), str) and "value" in o:
                raw = o.get("hover") if o.get("hover") not in (None, "") else o.get("value")
                v = to_num(raw)
                if v is not None:
                    out.setdefault(o["id"], v)
            for v in o.values():
                walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                walk(v, depth + 1)
    for n in nodes(txt):
        walk(n)
    return {"mcap": out.get("marketcap"), "pb": out.get("pb"), "equity": out.get("equity")}


def to_num(s):
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return float(s)
    if not isinstance(s, str):
        return None
    t = s.replace(",", "").replace("%", "").strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)([KMBT]?)", t)
    if not m:
        return None
    mul = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[m.group(2)]
    return float(m.group(1)) * mul


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


def universe():
    """{시장:코드: (시총, 발표일까지 남은 날)} — 화면에 오르는 종목 중 MIN_CAP 이상."""
    caps = {}
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8")).get("caps", {})
    except (ValueError, OSError):
        pass
    today = date.today()
    out = {}
    for market, fn in (("us", "earnings_us.json"), ("jp", "earnings.json"),
                       ("jp", "earnings_jp_past.json"), ("jp", "earnings_jp_sched.json"),
                       ("hk", "earnings_hk.json")):
        try:
            rows = json.loads((HERE / "data" / fn).read_text(encoding="utf-8")).get("rows", [])
        except (ValueError, OSError):
            continue
        for r in rows:
            c = r.get("code")
            if not c:
                continue
            k = f"{market}:{c}"
            if _skip(market, c, r.get("name"), r.get("sector", "")):
                continue                          # 은행·리츠는 안 받는다
            cap = (r.get("cap") if market == "us" else 0) or caps.get(k) or 0
            try:
                gap = abs((date.fromisoformat(r.get("date") or "") - today).days)
            except ValueError:
                gap = 9999
            oc, og = out.get(k, (0, 9999))
            out[k] = (max(cap, oc), min(gap, og))
    return {k: v for k, v in out.items() if v[0] >= MIN_CAP}


def queue(old, cand):
    today = date.today().isoformat()
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    picks = []
    for k, (cap, gap) in cand.items():
        rec = old.get(k)
        ts = (rec or {}).get("ts", "")
        if not rec or rec.get("v") != FCST_VER:
            pri = 0
        elif gap <= NEAR_DAYS and ts[:10] < today:
            pri = 1
        elif ts[:10] < stale:
            pri = 2
        else:
            continue
        picks.append((pri, -cap, k))
    picks.sort()
    return [k for _, _, k in picks]


def main():
    t0 = time.time()
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError):
        old = {}
    stocks = dict(old)
    todo = queue(old, universe())[:PER_RUN]
    got = streak = 0
    for i, k in enumerate(todo):
        if time.time() - t0 > SECS:
            print("  시간이 다 됐다 — 다음 실행에서 이어서")
            break
        market, code = k.split(":", 1)
        rec = {"v": FCST_VER,
               "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")}
        try:
            u = page(market, code, "forecast")
            txt = get(u) if u else None
            time.sleep(PAUSE)
            f = parse_forecast(txt) if txt else None
            if f and f["years"]:
                rec.update(f)
                s = get(page(market, code, "statistics"))
                time.sleep(PAUSE)
                if s:
                    rec.update(parse_stats(s))
                got += 1
            else:
                rec["none"] = 1
            streak = 0
        except Throttled as e:
            streak += 1
            print(f"  {k} 막힘: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("  연속으로 막혔다. 여기서 접는다.", file=sys.stderr)
                break
            continue
        # 받은 게 없으면 있던 것을 지우지 않는다.
        if rec.get("none") and (old.get(k) or {}).get("years"):
            rec = dict(old[k], ts=rec["ts"])
        stocks[k] = rec
        if i % 25 == 24:
            save(stocks)
    save(stocks)
    print(f"연간 컨센서스: 대기 {len(todo)} · 확보 {got} ({time.time() - t0:.0f}초) · 누적 "
          f"{sum(1 for v in stocks.values() if v.get('years'))}종목")


def save(stocks):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "source": "stockanalysis forecast·statistics (S&P Global 컨센서스)",
        "note": "years 는 연간 실적(est=0)·추정(est=1). next 는 내년 매출·EPS 추정. mcap 은 현지 통화.",
        "stocks": stocks}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)


def probe(args):
    for k in args:
        market, code = k.split(":", 1)
        f = parse_forecast(get(page(market, code, "forecast")) or "")
        s = parse_stats(get(page(market, code, "statistics")) or "")
        print(k, json.dumps(f, ensure_ascii=False)[:800], s)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe([a for a in sys.argv[1:] if a != "--probe"])
    else:
        main()
