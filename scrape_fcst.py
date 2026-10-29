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

미국 종목은 나스닥 analyst API(`earnings-forecast`)의 **연간 EPS 컨센서스 4개 연도**를
덧받는다(`ny`) — stockanalysis 가 가린 차차기년도 순이익·PER 을 그걸로 채운다.
차기·차차기의 **매출·영업이익(EBIT)·순이익**은 Business Insider 종목 페이지의
Estimates 표(5개 연도, 백만 단위)에서 받는다(`bi`). 미국은 티커가 주소고,
일본·홍콩은 영문 이름으로 BI 검색(SearchController_Suggest)을 해 ISIN 국가가 맞는
종목을 고른다. 엉뚱한 회사·통화를 막으려고 **당해년도 매출이 stockanalysis 것과
±12% 안에 들 때만** 쓴다. MarketScreener·finanzen.net·IFIS 는 GitHub 서버에서
403/503, 닛케이·사계보는 유료라 못 쓴다(실측 2026-10).

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
WORKERS = int(os.environ.get("FCST_WORKERS", "1"))
SECS = int(os.environ.get("FCST_SECS", "240"))
PAUSE = float(os.environ.get("FCST_PAUSE", "0.6"))
STALE_DAYS = int(os.environ.get("FCST_STALE_DAYS", "7"))
NEAR_DAYS = 4                     # 발표일 ±4일은 하루 한 번(컨센서스가 그때 바뀐다)
MIN_CAP = float(os.environ.get("FCST_MIN_CAP", "0"))     # 시총 하한(십억 달러) — 소형주까지 다
GIVE_UP_AFTER = 6
FCST_VER = 2
BI = "https://markets.businessinsider.com"
BI_ISIN = {"jp": ("JP",), "hk": ("HK", "KY", "BM", "CN", "VG", "SG", "US", "GB")}
NASDAQ_FC = "https://api.nasdaq.com/api/analyst/{}/earnings-forecast"
MONTHS = {m: i + 1 for i, m in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def nasdaq_years(code):
    """나스닥 연간 EPS 컨센서스 [[결산 YYYY-MM, EPS, 추정 수], ...]. 없으면 []."""
    txt = get(NASDAQ_FC.format(code.replace("-", ".").upper()))
    try:
        d = json.loads(txt or "")
    except ValueError:
        return []
    rows = (((d.get("data") or {}).get("yearlyForecast") or {}).get("rows")) or []
    out = []
    for r in rows:
        m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{4})", str(r.get("fiscalEnd") or ""))
        eps = num(r.get("consensusEPSForecast"))
        if m and m.group(1) in MONTHS and eps is not None:
            out.append([f"{m.group(2)}-{MONTHS[m.group(1)]:02d}", eps,
                        r.get("noOfEstimates") or 0])
    return out


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
        "years": years, "next": nxt, "name": info.get("name") if isinstance(info.get("name"), str) else "",
        "price": num(quote.get("p")),
        "curPrice": curr.get("price") if isinstance(curr.get("price"), str) else "",
        "curFin": curr.get("financial") if isinstance(curr.get("financial"), str) else "",
    }


def _bi_num(t):
    t = t.replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def parse_bi(txt):
    """BI 종목 페이지의 Estimates 표 → [[결산연도, 매출, EBIT, 순이익, EPS], ...] (현지 통화)."""
    i = txt.find(">EBIT<")
    if i < 0:
        return []
    seg = txt[txt.rfind("<table", 0, i):txt.find("</table>", i)]
    rows = {}
    head = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", r, re.S)]
        if not cells:
            continue
        if not head and all(re.fullmatch(r"\d{4}", c) for c in cells[1:] if c):
            head = cells[1:]
            continue
        rows.setdefault(cells[0], cells[1:])
    if not head:
        return []
    out = []
    for j, y in enumerate(head):
        def v(k, mul=1e6):
            x = rows.get(k) or []
            n = _bi_num(x[j]) if j < len(x) else None
            return n * mul if n is not None else None
        rec = [int(y), v("Revenue"), v("EBIT"), v("Net Profit"), v("EPS", 1)]
        if any(x is not None for x in rec[1:4]):
            out.append(rec)
    return out


STOP = {"co", "ltd", "corporation", "corp", "holdings", "holding", "inc", "limited",
        "company", "group", "kabushiki", "kaisha", "kk", "the", "plc", "sa", "ag", "nv",
        "registered", "shs", "unitary", "reg", "s", "144a", "h", "a", "b", "class"}


def _toks(name):
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
            if t not in STOP]


def _sim(a, b):
    """이름 낱말 겹침(0~1) — 짧은 쪽 기준."""
    x, y = set(_toks(a)), set(_toks(b))
    if not x or not y:
        return 0.0
    return len(x & y) / min(len(x), len(y))


def _suggest(q):
    import urllib.parse
    txt = get(BI + "/ajax/SearchController_Suggest?max_results=10&query=" + urllib.parse.quote(q)) or ""
    time.sleep(PAUSE / 2)
    return re.findall(r'new Array\("([^"]*)", "Stocks", "([^"]*)", "\d+", "", "([^"]*)"\)', txt)


def bi_candidates(market, code, name):
    """BI 주소 후보(slug), 그럴듯한 순. 미국은 티커가 먼저, 나머지는 이름 검색.

    검색어는 여러 꼴로 넣어 본다(전체 이름 · 군더더기 뗀 이름 · 앞 두 낱말 · 첫 낱말) —
    BI 검색은 낱말 하나만 어긋나도 엉뚱한 것을 준다(「Coca-Cola Bottlers Japan
    Holdings」 → 0건). 같은 나라 ISIN 만, ADR 은 뺀다(통화·주식 비율이 다르다).
    """
    out = []
    if market == "us":
        out.append(code.lower().replace(".", "_").replace("-", "_"))
    if not name:
        return out
    tk = _toks(name)
    qs = [name, " ".join(tk[:4]), " ".join(tk[:2]), " ".join(tk[:1])]
    seen_q, cands = set(), {}
    pref = {"us": ("US",), **BI_ISIN}[market]
    for q in qs:
        q = q.strip()
        if len(q) < 3 or q.lower() in seen_q:
            continue
        seen_q.add(q.lower())
        for rank, (nm, kw, ids) in enumerate(_suggest(q)):
            kws = kw.split("|")
            isin = kws[1] if len(kws) > 1 else ""
            slug = ids.split("|")[0]
            if not slug or slug in cands or not isin.startswith(pref):
                continue
            if re.search(r"(?i)deposit|\bADR", nm):
                continue
            hk_hit = market == "hk" and code.lstrip("0").zfill(4) in kws[-1].split(",")
            sim = _sim(name, nm)
            cands[slug] = (0 if hk_hit else 1, -round(sim, 2), 0 if kws[0] else 1, len(cands))
        best = min(cands.values(), default=None)
        if best and (best[0] == 0 or best[1] <= -0.99):
            break                                 # 확실한 것을 찾았으면 검색을 그친다
    out += [sl for sl, sc in sorted(cands.items(), key=lambda x: x[1]) if sc[0] == 0 or sc[1] < -0.3]
    return list(dict.fromkeys(out))[:4]


def _close(a, b, lo=0.88, hi=1.13):
    return a is not None and b and b > 0 and a > 0 and lo <= a / b <= hi


def bi_ok(ys, ref, next_rev):
    """BI 표가 같은 회사·같은 통화인가 — stockanalysis 의 당해(또는 차기) 값과 맞춰 본다.
    매출이 있으면 매출로(±12%), 매출이 비면 순이익(±20%)·EPS(±15%)로."""
    yr = int(ref["end"][:4])
    for yy, rv in ((yr, ref.get("rev")), (yr + 1, next_rev)):
        b = next((y for y in ys if y[0] == yy), None)
        if b and b[1] and rv:
            return _close(b[1], rv)
    b = next((y for y in ys if y[0] == yr), None)
    if b and (_close(b[3], ref.get("ni"), 0.8, 1.25) or _close(b[4], ref.get("eps"), 0.85, 1.18)):
        return True
    return False


def bi_fetch(market, code, name, ref, next_rev, slug=None):
    """검증된 BI 추정 [slug, 표]. 못 찾으면 [None, []]."""
    for sl in ([slug] if slug else bi_candidates(market, code, name)):
        txt = get(f"{BI}/stocks/{sl}-stock")
        time.sleep(PAUSE)
        ys = parse_bi(txt or "")
        if ys and bi_ok(ys, ref, next_rev):
            return sl, ys
    return None, []


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
    for market, fn in (("us", "earnings_us.json"), ("us", "earnings_us_fpi.json"), ("jp", "earnings.json"),
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


def us_names():
    try:
        rows = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return {}
    return {f"us:{r.get('code')}": r.get("name") or "" for r in rows}


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


def fetch_one(k, old, names):
    """한 종목. 막히면 Throttled 를 그대로 올린다."""
    market, code = k.split(":", 1)
    rec = {"v": FCST_VER, "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")}
    o = old.get(k) or {}
    u = page(market, code, "forecast")
    txt = get(u) if u else None
    time.sleep(PAUSE)
    f = parse_forecast(txt) if txt else None
    if not (f and f["years"]):
        return dict(o, ts=rec["ts"]) if o.get("years") else dict(rec, none=1)
    rec.update(f)
    s = get(page(market, code, "statistics"))
    time.sleep(PAUSE)
    if s:
        rec.update(parse_stats(s))
    # Business Insider 추정(차기·차차기 매출·영업이익·순이익).
    ys = rec["years"]
    ref = next((y for y in ys if y.get("est")), None) or ys[-1]
    nrev = (rec.get("next") or {}).get("rev")
    name = rec.get("name") or names.get(k, "")
    try:
        sl, bys = (bi_fetch(market, code, name, ref, nrev, o["bi"]) if o.get("bi")
                   else (None, []))
        if not sl:
            sl, bys = bi_fetch(market, code, name, ref, nrev)
        rec["bi"], rec["by"] = sl or "", bys
    except Throttled as e:
        print(f"  {k} BI 막힘: {e}", file=sys.stderr)
        for f2 in ("bi", "by"):
            if f2 in o:
                rec[f2] = o[f2]
    if market == "us" and not rec.get("by"):
        try:
            rec["ny"] = nasdaq_years(code)
        except Throttled:
            if "ny" in o:
                rec["ny"] = o["ny"]
        time.sleep(PAUSE)
    return rec


def main():
    """여러 종목을 나란히 받는다(WORKERS). 한 종목이 3~6 요청이라 하나씩이면
    전 종목(1만여)에 며칠이 걸린다. 서버는 셋(stockanalysis·BI·나스닥)으로 갈린다."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    t0 = time.time()
    try:
        old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError):
        old = {}
    stocks = dict(old)
    todo = queue(old, universe())[:PER_RUN]
    names = us_names()
    got = done = blocked = 0

    def job(k):
        if time.time() - t0 > SECS or blocked >= GIVE_UP_AFTER * WORKERS:
            return k, None
        try:
            return k, fetch_one(k, old, names)
        except Throttled as e:
            print(f"  {k} 막힘: {e}", file=sys.stderr, flush=True)
            return k, "blocked"

    with ThreadPoolExecutor(WORKERS) as ex:
        for fu in as_completed([ex.submit(job, k) for k in todo]):
            k, rec = fu.result()
            if rec is None:
                continue
            if rec == "blocked":
                blocked += 1
                continue
            blocked = 0
            stocks[k] = rec
            done += 1
            got += bool(rec.get("by"))
            if done % 50 == 0:
                save(stocks)
                print(f"  {done}종목 · BI {got} ({time.time() - t0:.0f}초)", flush=True)
    save(stocks)
    have = [v for v in stocks.values() if v.get("years")]
    print(f"연간 컨센서스: 대기 {len(todo)} · 이번 {done} (BI {got}) · {time.time() - t0:.0f}초 · "
          f"누적 {len(have)}종목 · BI 누적 {sum(1 for v in have if v.get('by'))}")


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
        if not f or not f["years"]:
            print(k, "stockanalysis 없음")
            continue
        ys = f["years"]
        ref = next((y for y in ys if y.get("est")), None) or ys[-1]
        print(k, f.get("name"), "후보", bi_candidates(market, code, f.get("name")))
        sl, bys = bi_fetch(market, code, f.get("name"), ref, (f.get("next") or {}).get("rev"))
        print("  ->", sl, [(y[0], y[1], y[2]) for y in bys][:3])


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe([a for a in sys.argv[1:] if a != "--probe"])
    else:
        main()
