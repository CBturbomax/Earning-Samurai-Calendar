# -*- coding: utf-8 -*-
"""미국 수주잔고(RPO) 이력 — SEC companyconcept, 종목당 요청 한 번.

**SEC 분기 묶음(FSDS)에는 RPO 가 거의 없다**(11/02 실측 — 2,948종목 중 4종목). 묶음 num.txt 는 주로 재무제표 본표
숫자이고 RPO 는 주석에 실린다. 그런데 `companyconcept` 는 회사가 그 항목으로 낸 **모든 값**을 10-Q·10-K 마다 준다.
RPO(`RevenueRemainingPerformanceObligation`, ASC 606 표준 항목)는 회사 전체 시점값이라 축 없이 그대로 쓴다.
IFRS 외국 기업은 `TransactionPriceAllocatedToRemainingPerformanceObligations`(ifrs-full).

data/rpo_us.json  stocks[티커] = {"pts": [[종료일, 값]], "ts": 받은 날}
  · 발표 언저리(캘린더 최근 날짜가 사흘 안)는 매번, 나머지는 열흘마다 다시 받는다. 값은 쌓는다(덮어써도 같은 값).
  · build.py 의 load_ord 가 segments_sec/edgar 의 rpo 와 합쳐 「수주잔고(RPO)」 한 줄로 그린다.
"""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import scrape_seg_sec as ss

HERE = Path(__file__).parent
OUT = HERE / "data" / "rpo_us.json"
SECS = int(os.environ.get("RPO_SECS", "600"))
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{ns}/{tag}.json"
TAGS = (("us-gaap", "RevenueRemainingPerformanceObligation"),
        ("ifrs-full", "TransactionPriceAllocatedToRemainingPerformanceObligations"))
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "6-K", "40-F")


def pull(cik):
    """{종료일: 값} — 같은 날 값이 여럿이면 나중에 낸 서류(정정)가 이긴다."""
    out = {}
    for ns, tag in TAGS:
        blob = ss.fetch(CONCEPT.format(cik=cik, ns=ns, tag=tag), timeout=40)
        time.sleep(0.12)                              # SEC 는 초당 10건까지
        if not blob:
            continue
        units = (json.loads(blob).get("units") or {})
        for unit, facts in units.items():
            if unit != "USD" and len(units) > 1:
                continue
            best = {}
            for f in facts:
                if f.get("start") or f.get("form") not in FORMS or not f.get("val"):
                    continue
                e = f.get("end")
                if e and (e not in best or (f.get("filed") or "") > best[e][1]):
                    best[e] = (float(f["val"]), f.get("filed") or "")
            for e, (v, _f) in best.items():
                out.setdefault(e, v)
        if out:
            break
    return out


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    caps = ss.want_tickers()
    try:
        rows = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8")).get("rows", [])
    except (OSError, ValueError):
        rows = []
    today = date.today()
    near = {r["code"] for r in rows if r.get("date") and
            abs((date.fromisoformat(r["date"]) - today).days) <= 3}
    cikmap = ss.cik_map()

    def due(t):
        rec = st.get(t)
        if not rec or not rec.get("ts"):
            return True
        age = (today - date.fromisoformat(rec["ts"][:10])).days
        return age >= (0 if t in near else 10)
    todo = sorted((t for t in caps if due(t)), key=lambda t: (t not in near, -(caps.get(t) or 0)))
    print(f"RPO: 대상 {len(todo):,} / {len(caps):,} (발표 언저리 {len(near & set(todo)):,})")
    t0, got, n, fails = time.time(), 0, 0, 0
    for t in todo:
        if time.time() - t0 > SECS:
            break
        cik = ss.cik_of(cikmap, t)
        if not cik:
            continue
        try:
            vals = pull(cik)
        except ss.Blocked as e:
            fails += 1
            print(f"  ! {t} {e}")
            if fails >= 8:
                print("  ! 연달아 막힌다 — 여기서 멈춘다.")
                break
            time.sleep(5)
            continue
        n += 1
        old = dict(st.get(t, {}).get("pts") or [])
        old.update(vals)
        st[t] = {"pts": sorted([e, v] for e, v in old.items()),
                 "ts": datetime.now(timezone.utc).isoformat(timespec="minutes")}
        got += bool(vals)
    db["count"] = sum(1 for v in st.values() if v.get("pts"))
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"RPO: 이번 {n:,}종목 · RPO 있음 {got:,} · 전체 RPO 종목 {db['count']:,}")


if __name__ == "__main__":
    main()
