# -*- coding: utf-8 -*-
"""미국 인수 확인 — 매출이 한 분기에 계단처럼 뛴 회사만 SEC 에 '회사 인수에 쓴 돈'을 물어본다(회원님 11/03 닥터페퍼 JDE Peet's).

1) 후보: data/financials.json 의 분기 매출이 전년비 +35% 이상 · 직전 분기 대비 +25% 이상 뛴 분기가 최근 3년 안에 있는 회사.
2) 확인: companyconcept `PaymentsToAcquireBusinessesNetOfCashAcquired`(현금흐름표 '사업 인수 지출') — 누계를 분기로 되돌린다.
   빌드가 그 분기(또는 직전 분기)의 인수 지출이 직전 1년 매출의 15% 를 넘을 때만 「인수」로 친다.

data/acq_us.json  stocks[티커] = {"q": {분기말: 인수 지출(달러)}, "ts": 받은 날}
"""
import json
import os
import time
from datetime import date
from pathlib import Path

import scrape_seg_sec as ss

HERE = Path(__file__).parent
OUT = HERE / "data" / "acq_us.json"
SECS = int(os.environ.get("ACQ_SECS", "300"))
URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/PaymentsToAcquireBusinessesNetOfCashAcquired.json"


def candidates():
    fin = json.loads((HERE / "data" / "financials.json").read_text(encoding="utf-8"))
    st = fin.get("stocks", fin)
    since = f"{date.today().year - 3}-01-01"
    out = []
    for t, r in st.items():
        pts = [p for p in (r.get("points") or []) if p.get("rev") and p.get("end")]
        for i in range(4, len(pts)):
            p, q, y = pts[i], pts[i - 1], pts[i - 4]
            if p["end"] >= since and y["rev"] > 0 and q["rev"] > 0 and \
                    p["rev"] / y["rev"] >= 1.35 and p["rev"] / q["rev"] >= 1.25:
                out.append(t)
                break
    return out


def quarterly(facts):
    """누계(같은 시작일) -> 분기. 분기 길이(60~125일) 사실은 그대로."""
    by_start, q = {}, {}
    for f in facts:
        if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A") or f.get("val") is None or not f.get("start"):
            continue
        by_start.setdefault(f["start"], {})[f["end"]] = float(f["val"])
    for s, ends in by_start.items():
        prev = 0.0
        for e in sorted(ends):
            days = (date.fromisoformat(e) - date.fromisoformat(s)).days
            # 앞 분기 값이 없으면(그 분기엔 인수가 없어 회사가 안 적었다) 누계가 곧 그 기간의 인수 지출이다 —
            # 닥터페퍼는 1분기 줄이 없고 상반기 누계에만 JDE Peet's 가 있어 통째로 버려졌다(11/03).
            v = ends[e] - prev if days > 125 else ends[e]
            q.setdefault(e, round(v))
            prev = ends[e]
    return q


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    if db.get("v") != 2:                                       # 1: 앞 분기 없는 누계를 버림 — 다시
        st.clear(); db["v"] = 2
    cikmap = ss.cik_map()
    today = date.today().isoformat()
    todo = [t for t in candidates() if (st.get(t) or {}).get("ts", "")[:10] < today[:8] + "01" or t not in st]
    print(f"인수 확인: 후보 중 볼 것 {len(todo)}")
    t0, n = time.time(), 0
    for t in todo:
        if time.time() - t0 > SECS:
            break
        cik = cikmap.get(t)
        if not cik:
            continue
        blob = ss.fetch(URL.format(cik=cik), timeout=40)
        time.sleep(0.12)
        q = {}
        if blob:
            facts = (json.loads(blob).get("units") or {}).get("USD") or []
            q = quarterly(facts)
        st[t] = {"q": {e: v for e, v in q.items() if v > 0 and e >= "2017-01-01"}, "ts": today}
        n += 1
    OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
    print(f"인수 확인: 이번 {n} · 전체 {len(st)}")


if __name__ == "__main__":
    main()
