# -*- coding: utf-8 -*-
"""떠보기 — 홍콩 사업부·지역 구성(主营构成) 이력을 주는 API 이름 찾기(동방재부 F10 스크립트 · 동화순)."""
import re
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
      "Referer": "https://emweb.securities.eastmoney.com/"}


def get(u):
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, ""


B = "https://emweb.securities.eastmoney.com/PC_HKF10/pages/home/index.html?code=00700&type=web&color=w"
st, html = get(B)
print("index", st, len(html))
js = re.findall(r'(?:src|href)=["\']?([^"\' >]+\.js)', html)
print(html[:4700])
print("js:", js)
names = set()
for j in js:
    u = j if j.startswith("http") else ("https:" + j if j.startswith("//") else
                                         "https://emweb.securities.eastmoney.com/PC_HKF10/pages/home/" + j.lstrip("./"))
    s, t = get(u)
    found = set(re.findall(r"RPT_[A-Z0-9_]+", t))
    names |= found
    print(" ", u[-60:], s, len(t), len(found))
    for m in re.finditer(r"(主营|业务|地区|Business|MainOp|zygc|ZYGC)", t):
        pass
    for kw in ("主营构成", "按地区", "按业务", "BusinessAnalysis", "zyfw", "zygc", "MAINOP", "SEGMENT"):
        i = t.find(kw)
        if i >= 0:
            print("    ", kw, "…", t[max(0, i - 200):i + 200].replace("\n", " ")[:400])
    time.sleep(0.3)
print("RPT 이름:", sorted(names))
api = "https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName={r}&columns=ALL&filter=(SECUCODE%3D%2200700.HK%22)&pageNumber=1&pageSize=50&source=F10&client=PC"
for r in sorted(names):
    if any(k in r for k in ("MAIN", "OPER", "BUS", "SEG", "REGION", "ZY", "INCOME_ITEM", "COMPOSE", "STRUCT")):
        s, t = get(api.format(r=r))
        print(r, s, t[:500].replace("\n", " "))
        time.sleep(0.3)

