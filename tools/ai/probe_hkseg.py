# -*- coding: utf-8 -*-
"""떠보기 — 동방재부 RPT_HKF10_ORG_BUSSINESS 내용(홍콩 사업부·지역 구성 이력인가)."""
import json
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 Chrome/131.0", "Referer": "https://emweb.securities.eastmoney.com/"}
api = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_HKF10_ORG_BUSSINESS&columns=ALL"
       "&filter=(SECUCODE%3D%22{c}.HK%22)&pageNumber=1&pageSize=500&sortColumns=REPORT_DATE&sortTypes=-1&source=F10&client=PC")
for c in ("00700", "03750", "09992", "01211", "00941"):
    j = json.loads(urllib.request.urlopen(urllib.request.Request(api.format(c=c), headers=UA), timeout=30).read())
    rows = (j.get("result") or {}).get("data") or []
    print("=====", c, len(rows), "pages", (j.get("result") or {}).get("pages"))
    if rows:
        print("열:", list(rows[0].keys()))
        dates = sorted({r.get("REPORT_DATE", "")[:10] for r in rows})
        print("기간:", dates[:3], "…", dates[-3:], len(dates))
        for r in rows[:12]:
            print("  ", json.dumps({k: v for k, v in r.items() if k not in ("SECUCODE", "SECURITY_CODE", "ORG_CODE", "SECURITY_INNER_CODE", "SECURITY_NAME_ABBR")}, ensure_ascii=False)[:400])
