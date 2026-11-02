# -*- coding: utf-8 -*-
"""떠보기 — 2019~2021 결산단신 zip 원본을 /tmp/po/zips 에 둔다. 동방재부 홍콩 F10 스크립트에서 API 이름 찾기."""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402
import scrape_geo_jp as S  # noqa: E402

OUT = Path("/tmp/po/zips")
OUT.mkdir(parents=True, exist_ok=True)
for code in "6503 6135 1959 6501 4502 6594 6762".split():
    j = json.loads(S.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                         binary=False) or "{}")
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    tan = sorted((it for it in items if F.TANSHIN.search(it.get("title") or "")
                  and not F.NOT_TANSHIN.search(it.get("title") or "") and it.get("url_xbrl")),
                 key=lambda it: it["pubdate"])
    pick = [it for it in tan if it["pubdate"][:7] in ("2018-04", "2019-10", "2020-07", "2021-07", "2018-05",
                                                       "2019-11", "2020-08", "2021-08")][:4]
    for it in pick:
        fn = it["url_xbrl"].rsplit("/", 1)[-1]
        b = S.get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", timeout=60)
        if b:
            (OUT / f"{code}_{it['pubdate'][:10]}_old.zip").write_bytes(b)
            print("saved", code, it["pubdate"][:10], len(b))
        time.sleep(0.4)

UA = {"User-Agent": "Mozilla/5.0 Chrome/131.0", "Referer": "https://emweb.securities.eastmoney.com/"}
for u in ("https://emweb.securities.eastmoney.com/PC_HKF10/pages/static/script/home/index/index.js",
          "https://emweb.securities.eastmoney.com/PC_HKF10/static/script/home/index/index.js"):
    try:
        t = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30).read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print(u, e)
        continue
    print(u, len(t))
    names = sorted(set(re.findall(r"RPT_[A-Z0-9_]+", t)))
    print("RPT:", names)
    for kw in ("主营", "地区", "zyfw", "Business", "MAINOP", "api/data"):
        for m in list(re.finditer(kw, t))[:3]:
            print("  ", kw, "…", t[max(0, m.start() - 150):m.start() + 150].replace("\n", " "))
    break
