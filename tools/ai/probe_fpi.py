"""ai_fpi 가 6-K 에서 실적 표를 왜 못 고르는지 떠본다(AI 안 부름). python tools/ai/probe_fpi.py CNQ [SU]"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ai_fpi as P
import ai_fill as af
import scrape_live_us as lu
import pr_table as pt

for code in sys.argv[1:]:
    cik = int(lu.cik_map_cached().get(code))
    files = P.six_k(cik)
    print(f"== {code} cik {cik} · 6-K {len(files)}")
    # 분기 끝 뒤 10~100일 안의 것만(수집기와 같은 창) — 2021·2019 두 해
    pick = [f for f in files if f[0][:4] in ("2021", "2019")][:40]
    shown = 0
    for fd, acc in pick:
        blob = af.sec(lu.FILES.format(cik=cik, acc=acc))
        try:
            items = json.loads(blob)["directory"]["item"] if blob else []
        except (ValueError, KeyError):
            items = []
        names = [(it["name"], it.get("size")) for it in items]
        htm = sorted(n for n, _ in names if n.lower().endswith((".htm", ".html")) and not re.match(r"index|.*-index", n, re.I))
        ex = [n for n in htm if re.search(r"ex[-_]?99|99[-_.]?[1-3]\b|991|992|press|earnings|result", n, re.I)]
        print(f"- {fd} {acc} files={names[:8]}")
        for name in (ex or htm)[:4]:
            raw = af.sec(lu.DOC.format(cik=cik, acc=acc, name=name), binary=True)
            s = raw.decode("utf-8", "replace") if raw else ""
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s[:60000]))
            full = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))
            tabs = pt.tables(s) if s else []
            good = 0
            for t in tabs:
                rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
                rows = [r for r in rows if r.strip()]
                b = "\n".join(rows)
                if len(rows) >= 2 and P.KW.search(b) and re.search(r"\d{3,}", b):
                    good += 1
            print(f"    {name}: raw {len(s):,} · plain60k {len(plain):,} · QHINT {bool(P.QHINT.search(plain))}/{bool(P.QHINT.search(full))}"
                  f" · KW {bool(P.KW.search(plain))}/{bool(P.KW.search(full))} · tables {len(tabs)} good {good} · text_of {len(P.text_of(s)):,}")
            if shown < 3 and s:
                shown += 1
                print("      HEAD:", plain[:300].replace("\n", " "))
                print("      <table>", s.lower().count("<table"), "<pre>", s.lower().count("<pre"), "<img", s.lower().count("<img"))
