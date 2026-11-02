# -*- coding: utf-8 -*-
"""떠보기 — 2017~2020년 결산단신(JPX 보관소)에서도 지역·수주 표가 읽히나."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402
import scrape_geo_jp as S  # noqa: E402

for code in "6503 6762 6981 6301 6501 4502 6135 1959 7751 6594".split():
    try:
        j = json.loads(S.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                             binary=False) or "{}")
    except Exception as e:  # noqa: BLE001
        print(code, "목록 실패", e)
        continue
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    tan = sorted((it for it in items if F.TANSHIN.search(it.get("title") or "")
                  and not F.NOT_TANSHIN.search(it.get("title") or "") and it.get("url_xbrl")),
                 key=lambda it: it["pubdate"])
    pick = [it for it in tan if "2016-10" <= it["pubdate"][:7] <= "2020-12"][::3]
    for it in pick:
        fn = it["url_xbrl"].rsplit("/", 1)[-1]
        try:
            blob = S.get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", timeout=60)
        except Exception as e:  # noqa: BLE001
            print(code, it["pubdate"][:10], "실패", e)
            continue
        if not blob:
            print(code, it["pubdate"][:10], "없음")
            continue
        z = zipfile.ZipFile(io.BytesIO(blob))
        att = [n.split("/")[-1][:28] for n in z.namelist() if n.lower().endswith(".htm")]
        g = S.read(blob)
        geo = g.get("geo") or {}
        k = max(geo) if geo else None
        print(code, it["pubdate"][:10], f"htm {len(att)}", "| 지역", (k, list(geo[k])[:6]) if k else "-",
              "| 수주", g.get("oh"), g.get("ob"), "|", it["title"][:30])
        time.sleep(0.4)
