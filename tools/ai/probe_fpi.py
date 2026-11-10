"""ai_fpi 가 6-K 에서 실적 표를 왜 못 고르는지 떠본다(AI 안 부름). python tools/ai/probe_fpi.py CIK:ACC:NAME …"""
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ai_fpi as P
import ai_fill as af
import scrape_live_us as lu
import pr_table as pt

for spec in sys.argv[1:]:
    cik, acc, name = spec.split(":")
    raw = af.sec(lu.DOC.format(cik=int(cik), acc=acc, name=name), binary=True)
    s = raw.decode("utf-8", "replace") if raw else ""
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s[:60000]))
    print(f"== {spec} raw {len(s):,} QHINT {bool(P.QHINT.search(plain))} KW {bool(P.KW.search(plain))} text_of {len(P.text_of(s)):,}")
    print("HEAD:", plain[:400])
    n = 0
    for i, t in enumerate(pt.tables(s)):
        rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
        rows = [r for r in rows if r.strip()]
        b = "\n".join(rows)
        if len(rows) >= 2 and P.KW.search(b) and re.search(r"\d{3,}", b):
            n += 1
            seg = bool(re.search(r"(?i)segment", b))
            tot = bool(re.search(r"(?im)^(total )?(revenues?|net sales|product sales|sales)\b", b))
            print(f"-- table#{i} good#{n} rows {len(rows)} chars {len(b):,} segment {seg} revrow {tot}")
            print("   " + "\n   ".join(r[:170] for r in rows[:5]))
