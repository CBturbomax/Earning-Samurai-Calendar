# -*- coding: utf-8 -*-
"""떠보기 3 — 시험용 결산단신 zip 을 모아 /tmp/po/zips 에 둔다(최근 TDnet + 옛 JPX 보관소).
옛 보관소(yanoshin 목록 + www2.jpx.co.jp/disc)가 Actions 에서 열리는지도 본다."""
import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402

OUT = Path("/tmp/po/zips")
OUT.mkdir(parents=True, exist_ok=True)
NOW = ("6920 6586 6135 6503 6762 6981 4307 4502 6305 6754 1959 6301 6723 6701 6506 6594 "
       "7741 6501 6702 7751 6504 1942 4062 6141 7735 6146 8035 6861 4063 7309 6954 6857 "
       "6902 6273 6367 7011 7012 1801 1812 6383").split()
OLD = "6503 6301 1959 6135 7011 1812 6762".split()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def get(u, binary=True):
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


seen = set()
for back in range(40):
    if len(seen) >= len(NOW):
        break
    day = (date.today() - timedelta(days=back)).isoformat()
    try:
        rows = F.announcements(day)
    except F.Throttled as e:
        print(day, e)
        continue
    for r in rows:
        if r["code"] in NOW and r["code"] not in seen:
            seen.add(r["code"])
            try:
                b = F.get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
                (OUT / f"{r['code']}_{day}_now.zip").write_bytes(b)
                print("now", r["code"], day, len(b))
            except Exception as e:  # noqa: BLE001
                print("zip 실패", r["code"], e)
            time.sleep(0.3)
print("최근 못 찾음:", sorted(set(NOW) - seen))

for code in OLD:
    try:
        j = json.loads(get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                           binary=False))
    except Exception as e:  # noqa: BLE001
        print("yanoshin 실패", code, repr(e)[:200])
        continue
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    tan = [it for it in items if F.TANSHIN.search(it.get("title") or "")
           and not F.NOT_TANSHIN.search(it.get("title") or "") and it.get("url_xbrl")]
    print(f"yanoshin {code}: 항목 {len(items)} · 결산단신 {len(tan)} · "
          f"가장 옛 {min((t['pubdate'] for t in tan), default='-')}")
    # 몇 개만 — 2년 전·4년 전 언저리
    pick = [t for t in tan if t["pubdate"][:4] in ("2021", "2023", "2024")][:4]
    for it in pick:
        fn = it["url_xbrl"].rsplit("/", 1)[-1]
        try:
            b = get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}")
            (OUT / f"{code}_{it['pubdate'][:10]}_old.zip").write_bytes(b)
            print("  old", code, it["pubdate"][:10], len(b), it["title"][:40])
        except Exception as e:  # noqa: BLE001
            print("  jpx 실패", code, fn, repr(e)[:120])
        time.sleep(0.5)
print("모음:", len(os.listdir(OUT)), "개")
