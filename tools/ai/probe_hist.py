# -*- coding: utf-8 -*-
"""떠보기 — (가) 홍콩 동방재부 F10 의 사업부·지역 구성(主营构成) 이력 (나) JPX 공시 보관소가 몇 년치까지 있나."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
      "Referer": "https://emweb.securities.eastmoney.com/"}


def get(u, binary=False):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
            b = r.read()
            return r.status, (b if binary else b.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, b""


print("===== (가) 동방재부 홍콩 主营构成")
base = "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName={r}&columns=ALL&filter=(SECUCODE%3D%22{c}%22)&pageNumber=1&pageSize=200&sortColumns=REPORT_DATE&sortTypes=-1&source=F10&client=PC"
for rep in ("RPT_HKF10_FN_MAINOP", "RPT_HKF10_MAINOP", "RPT_HKF10_FN_BUSINESS", "RPT_HKF10_MAIN_OPERATE",
            "RPT_HKF10_FN_MAINOPERATE", "RPT_HKF10_OPERATE_MAIN", "RPT_HKF10_FN_SEGMENT", "RPT_HKF10_BUSINESS_SEGMENT",
            "RPT_HKF10_FN_REVENUE_SEGMENT", "RPT_HKF10_ZYGC", "RPT_HKF10_FN_ZYGC", "RPT_F10_HK_MAINOP"):
    st, txt = get(base.format(r=rep, c="00700.HK"))
    head = txt[:300].replace("\n", " ") if isinstance(txt, str) else ""
    print(rep, st, head)
    time.sleep(0.4)
# 웹 페이지 쪽에서 쓰는 이름을 찾는다
for u in ("https://emweb.securities.eastmoney.com/PC_HKF10/BusinessAnalysis/index?type=web&code=00700",
          "https://emweb.securities.eastmoney.com/PC_HKF10/pages/home/index.html?code=00700&type=web&color=w#/BusinessAnalysis",
          "https://emweb.securities.eastmoney.com/PC_HKF10/BusinessAnalysis/PageAjax?code=00700"):
    st, txt = get(u)
    import re
    print("\n--", u, st, len(txt) if isinstance(txt, str) else 0)
    if isinstance(txt, str):
        print("   RPT 이름:", sorted(set(re.findall(r"RPT_[A-Z0-9_]+", txt)))[:40])
        print("   ", txt[:600].replace("\n", " "))
    time.sleep(0.4)

print("\n===== (나) JPX 공시 보관소 깊이")
for code in ("6503", "6762", "6981", "6301"):
    st, txt = get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1")
    try:
        items = [it.get("Tdnet", it) for it in json.loads(txt).get("items", [])]
    except Exception:  # noqa: BLE001
        print(code, "목록 실패", st)
        continue
    tan = [it for it in items if F.TANSHIN.search(it.get("title") or "") and it.get("url_xbrl")]
    by_year = {}
    for it in tan:
        by_year.setdefault(it["pubdate"][:4], it)
    for y in sorted(by_year):
        if y < "2016":
            continue
        it = by_year[y]
        fn = it["url_xbrl"].rsplit("/", 1)[-1]
        s1, _ = get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", binary=True)
        s2, _ = get(it["url_xbrl"], binary=True)
        print(code, y, it["pubdate"][:10], "JPX", s1, "| 원 주소", s2, it["url_xbrl"][:80])
        time.sleep(0.4)
