"""홍콩 실적 긴 이력 — 동방재부(Eastmoney) F10 손익표에서 2016년치부터.

회원님(11/01): "홍콩 회사들이 너무 엉망. 분기 내는 회사(CATL 같은)는 1Q18 부터 분기로,
안 되면 반기(1H18·2H18)로라도." stockanalysis 는 20기간이 무료 한도라 반기 회사는 2021년,
분기 회사도 3Q21 부터밖에 없었다.

동방재부 `RPT_HKF10_FN_INCOME_PC` 는 회사가 낸 보고기간마다 **누계**를 준다(1분기·반기·9개월·연간,
시작일 START_DATE 가 같이 온다). 같은 시작일의 앞 누계를 빼서 기간값으로 되돌린다.
  - 그 회사가 1분기·9개월 누계를 낸 적이 있으면 **분기**로(텐센트·BYD·CATL)
  - 아니면 **반기**로(상반기 = 6개월 누계, 하반기 = 연간 − 상반기)
통화는 이 표에 없다(MAININDICATOR 의 CURRENCY 는 틀린 값이 온다 — 텐센트 위안 숫자에 HKD).
그래서 여기서는 숫자만 담고, build.py 가 stockanalysis 기록과 **겹치는 기간 값이 맞을 때만**
그쪽 통화로 붙인다. 안 맞으면 안 쓴다.

    python scrape_fin_hk_em.py            # 한 바퀴(EM_SECS 초 안에서)
    python scrape_fin_hk_em.py 00700 03750  # 몇 종목만 보고 찍는다
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials_hk_em.json"
SINCE = "2016-01-01"                     # 2017년 반기·분기를 되돌리려면 그 앞 누계가 필요 없다(시작일이 같은 것끼리 뺀다)
SECS = int(os.environ.get("EM_SECS", "240"))
FRESH_DAYS = 10                          # 이 안에 받은 것은 다시 안 받는다(발표 언저리는 아래에서 따로)
NEAR_DAYS = 4                            # 홍콩 캘린더에 최근 발표가 있으면 하루만 지나도 다시
API = ("https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_HKF10_FN_INCOME_PC"
       "&columns=REPORT_DATE,START_DATE,STD_ITEM_NAME,AMOUNT&pageNumber=1&pageSize=500&sortTypes=-1"
       "&sortColumns=REPORT_DATE&source=F10&client=PC&filter=")
ITEMS = ("营业额", "营运收入", "经营溢利", "股东应占溢利")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}


def months(a, b):
    """시작일·종료일 사이 달 수(2026-01-01 ~ 2026-06-30 = 6)."""
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1


def fetch(code):
    f = '(SECUCODE="%s.HK")(REPORT_DATE>=\'%s\')(STD_ITEM_NAME in (%s))' % (
        code, SINCE, ",".join('"%s"' % x for x in ITEMS))
    with urllib.request.urlopen(urllib.request.Request(API + urllib.parse.quote(f), headers=UA), timeout=25) as r:
        j = json.loads(r.read())
    if not j.get("success", True) and j.get("code") not in (9201,):      # 9201 = 자료 없음
        raise RuntimeError(j.get("message"))
    return (j.get("result") or {}).get("data") or []


def periods(rows):
    """누계 행 -> {(시작, 끝): {항목: 값}} -> 기간값 점 목록과 주기('Q'·'H')."""
    cum = {}
    for r in rows:
        st, en = (r.get("START_DATE") or "")[:10], (r.get("REPORT_DATE") or "")[:10]
        if not st or not en or r.get("AMOUNT") is None:
            continue
        cum.setdefault((st, en), {})[r["STD_ITEM_NAME"]] = float(r["AMOUNT"])
    spans = {(st, en): months(st, en) for st, en in cum}
    quarterly = any(m in (3, 9) for m in spans.values())
    step = 3 if quarterly else 6
    pts = []
    for (st, en), v in cum.items():
        m = spans[(st, en)]
        if m % step or m > 12:
            continue
        if m == step:
            val = dict(v)
        else:                                    # 같은 시작일의 바로 앞 누계를 뺀다
            prev = next((cum[k] for k in cum if k[0] == st and spans[k] == m - step), None)
            if prev is None:
                continue
            val = {k: v[k] - prev[k] for k in v if k in prev}
        e = date.fromisoformat(en)
        pts.append({"end": en, "mid": (e - timedelta(days=45 if quarterly else 90)).isoformat(),
                    "rev": val.get("营业额"), "rev2": val.get("营运收入"),
                    "opi": val.get("经营溢利"), "ni": val.get("股东应占溢利")})
    pts.sort(key=lambda p: p["end"])
    return ("Q" if quarterly else "H"), [p for p in pts if (p["rev"] or p["rev2"])]


def universe():
    """캘린더에 뜬 홍콩 종목 — 최근 발표일을 같이 돌려준다(발표 언저리는 빨리 다시 받는다)."""
    try:
        rows = json.loads((HERE / "data" / "earnings_hk.json").read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        rows = []
    last = {}
    for r in rows:
        c = r.get("code")
        if c and r.get("date", "") > last.get(c, ""):
            last[c] = r["date"]
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8"))
        cap = {k[3:]: v for k, v in (caps.get("caps") or caps).items() if str(k).startswith("hk:")} if isinstance(caps, dict) else {}
    except (OSError, ValueError):
        cap = {}
    order = sorted(last, key=lambda c: -(cap.get(c) or 0) if isinstance(cap.get(c), (int, float)) else 0)
    return order, last


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    now = datetime.now(timezone.utc)
    only = [a.zfill(5) for a in sys.argv[1:]]
    order, last = universe()
    if only:
        order = only
    today = now.date().isoformat()

    def stale(c):
        rec = st.get("hk:" + c)
        if not rec or not rec.get("ts"):
            return True
        age = (now - datetime.fromisoformat(rec["ts"])).total_seconds() / 86400
        near = last.get(c) and (date.fromisoformat(today) - date.fromisoformat(last[c])).days <= NEAR_DAYS
        return age > (0.25 if near else FRESH_DAYS)
    todo = [c for c in order if only or stale(c)]
    t0, got, fail = time.time(), 0, 0
    for c in todo:
        if not only and time.time() - t0 > SECS:
            break
        try:
            freq, pts = periods(fetch(c))
        except Exception as e:                                   # noqa: BLE001
            fail += 1
            print(f"  ! {c} {e!r}"[:160])
            if fail >= 15 and got == 0:
                print("  ! 연속 실패 — 이번 바퀴는 접는다")
                break
            continue
        st["hk:" + c] = {"freq": freq, "src": "em", "points": pts, "ts": now.isoformat(timespec="minutes")}
        got += 1
        if only:
            print(c, freq, len(pts), pts[:2], pts[-2:])
        time.sleep(0.25)
    db["count"] = sum(1 for v in st.values() if v.get("points"))
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"홍콩 긴 이력(동방재부): 이번 {got}종목 · 남은 {max(len(todo) - got - fail, 0)} · 실패 {fail} · 전체 {db['count']}")


if __name__ == "__main__":
    main()
