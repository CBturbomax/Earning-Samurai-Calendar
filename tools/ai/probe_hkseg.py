# -*- coding: utf-8 -*-
"""떠보기 — 홍콩 사업부·지역 구성 이력을 주는 곳(동방재부 F10 보고서 이름 전부, 동화순 다른 주소)."""
import re
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36",
      "Referer": "https://emweb.securities.eastmoney.com/"}


def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, ""


st, t = get("https://emweb.securities.eastmoney.com/PC_HKF10/pages/static/script/home/index/index.js")
names = sorted(set(re.findall(r"RPT_[A-Z0-9_]+", t)))
print("index.js", st, len(t), "RPT", len(names))
print(names)
chunks = sorted(set(re.findall(r'"(\d+)":"([0-9a-f]{6,})"', t)))[:80]
print("chunks", chunks[:40])
for m in re.finditer(r"(\w+Analysis|\w+Structure|BusinessExpectation|OperationsAnalysis|MainBusiness)", t):
    pass
print("해시 경로:", sorted(set(re.findall(r"#/(\w+)", t))))
api = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName={r}&columns=ALL"
       "&filter=(SECUCODE%3D%2200700.HK%22)&pageNumber=1&pageSize=20&source=F10&client=PC")
for r in names:
    s2, b = get(api.format(r=r))
    ok = '"success":true' in b
    print(r, s2, "OK" if ok else "", b[:220].replace("\n", " ") if ok else "")
    time.sleep(0.25)
for u in ("https://emweb.securities.eastmoney.com/PC_HKF10/OperationsRequired/PageAjax?code=00700",
          "https://emweb.securities.eastmoney.com/PC_HKF10/BusinessAnalysis/PageAjax?code=00700",
          "https://emweb.securities.eastmoney.com/PC_HKF10/CompanyProfile/PageAjax?code=00700"):
    s2, b = get(u)
    print(u, s2, b[:300])
