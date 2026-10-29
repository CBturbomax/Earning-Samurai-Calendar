"""BI 매칭 실패 원인 보기(시험용)."""
import re, sys, json, urllib.parse
import scrape_fcst as s
from scrape_fin_intl import get

for k in sys.argv[1:]:
    m, c = k.split(":", 1)
    try:
        f = s.parse_forecast(s.get(s.page(m, c, "forecast")) or "")
    except Exception as e:
        print(k, "SA err", e); continue
    if not f or not f["years"]:
        print(k, "SA 없음"); continue
    ys = f["years"]; ref = next((y for y in ys if y.get("est")), None) or ys[-1]
    print(f"== {k} name={f.get('name')!r} ref={ref['end']} rev={ref.get('rev')} next={f['next'].get('rev')} cur={f.get('curFin')}")
    try:
        cands = s.bi_candidates(m, c, f.get("name"))
    except Exception as e:
        print("  cand err", e); continue
    print("  cands", cands)
    if m != "us" and f.get("name"):
        q = f.get("name")
        t = get(s.BI + "/ajax/SearchController_Suggest?max_results=10&query=" + urllib.parse.quote(q)) or ""
        print("  raw(full name)", re.findall(r'new Array\("([^"]*)", "Stocks", "([^"]*)", "\d+", "", "([^"]*)"\)', t)[:5])
    for sl in cands:
        t = get(f"{s.BI}/stocks/{sl}-stock") or ""
        ys2 = s.parse_bi(t)
        cur = re.search(r"Estimates\*? in ([A-Z]{3})", t)
        print("  ", sl, len(t), cur and cur.group(1), [(y[0], y[1]) for y in ys2][:3])
