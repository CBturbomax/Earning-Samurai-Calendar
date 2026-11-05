"""사업부 빈칸 진단 — 회사의 특정 분기 10-Q/10-K 인라인 XBRL 에서 매출 항목의 문맥(축) 을 통째로 찍는다.
python tools/ai/probe_seg.py CAT:2021-06-30 COST:2023-09-03 ..."""
import io
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date

sys.path.insert(0, ".")
import scrape_seg_edgar as se
import scrape_seg_hist_us as hu
import scrape_seg_sec as ss

REV = re.compile(r"Revenue|Sales", re.I)
ALL = False


def local(t):
    return t.rsplit("}", 1)[-1]


def dump(t, end):
    cik = ss.cik_map().get(t)
    fl = hu.filings(cik)
    d0 = date.fromisoformat(end)
    hit = [f for f in fl if abs((date.fromisoformat(f[0]) - d0).days) <= 10]
    print(f"=== {t} cik {cik} · {end} · 서류 {hit[:2]}")
    if not hit:
        return
    rd, form, acc, filed = hit[0]
    url = se.instance_url(cik, acc)
    blob = se.get(url) if url else None
    print("   url", url, "bytes", len(blob or b""))
    if not blob:
        return
    ctx = {}
    facts = defaultdict(list)
    for ev, el in ET.iterparse(io.BytesIO(blob), events=("end",)):
        n = local(el.tag)
        if n == "context":
            dims = [f"{local(m.get('dimension') or '').split(':')[-1]}={(m.text or '').split(':')[-1] or ''.join(x.text or '' for x in m)}"
                    for m in el.iter() if local(m.tag) in ("explicitMember", "typedMember")]
            s = e = ""
            for p in el.iter():
                if local(p.tag) == "startDate":
                    s = p.text
                if local(p.tag) == "endDate":
                    e = p.text
            ctx[el.get("id")] = (tuple(dims), s, e)
        elif (REV.search(n) or n.startswith("Revenue")) and el.get("contextRef") and (el.text or "").strip():
            facts[n].append((el.get("contextRef"), el.text.strip()))
    print("   매출 태그:", {t: len(r) for t, r in facts.items()})
    for tag, rows in facts.items():
        shown = 0
        for cref, v in rows:
            dims, s, e = ctx.get(cref, ((), "", ""))
            if not dims or not e or abs((date.fromisoformat(e) - d0).days) > 10:
                continue
            days = (date.fromisoformat(e) - date.fromisoformat(s)).days if s else 0
            if days > 100 and not ALL:
                continue
            print(f"   {tag} | {';'.join(dims)} | {s}~{e} | {v}")
            shown += 1
            if shown > 40:
                break


for a in sys.argv[1:]:
    if a == "--all":
        ALL = True
        continue
    t, _, e = a.partition(":")
    try:
        dump(t, e)
    except Exception as ex:                       # noqa: BLE001
        print("  !", t, repr(ex)[:200])
