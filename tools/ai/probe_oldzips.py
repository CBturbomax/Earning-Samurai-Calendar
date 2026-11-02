# -*- coding: utf-8 -*-
"""떠보기 — 결산단신 PDF 주소 찾기(yanoshin 목록 hasXBRL 없이) 와 JPX 보관소의 PDF."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402
import scrape_geo_jp as S  # noqa: E402

OUT = Path("/tmp/po/pdfs")
OUT.mkdir(parents=True, exist_ok=True)
for code in "6503 6135 1959 6501 4502 6762 6301 6981".split():
    j = json.loads(S.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=600",
                         binary=False) or "{}")
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    tan = sorted((it for it in items if F.TANSHIN.search(it.get("title") or "")
                  and not F.NOT_TANSHIN.search(it.get("title") or "")), key=lambda it: it["pubdate"])
    pick = [it for it in tan if it["pubdate"][:7] in ("2019-10", "2020-07", "2018-04", "2019-11", "2020-08", "2018-05")][:3]
    for it in pick:
        print(code, it["pubdate"][:10], "doc", it.get("document_url"), "xbrl", it.get("url_xbrl"))
        doc = (it.get("document_url") or "").split("?")[-1]
        fn = doc.rsplit("/", 1)[-1]
        if not fn.endswith(".pdf"):
            continue
        for u in (f"https://www2.jpx.co.jp/disc/{code}0/{fn}", doc):
            try:
                b = S.get(u, timeout=60)
            except Exception as e:  # noqa: BLE001
                print("   ", u[:90], "실패", e)
                continue
            print("   ", u[:90], len(b) if b else None)
            if b:
                (OUT / f"{code}_{it['pubdate'][:10]}.pdf").write_bytes(b)
                break
        time.sleep(0.4)
