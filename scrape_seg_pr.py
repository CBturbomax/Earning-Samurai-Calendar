"""미국 — 발표한 분기의 사업부별 매출을 **실적 보도자료(8-K EX-99) 표에서 바로** 읽는다. AI 를 쓰지 않는다.

회원님(11/01): "구글 3분기 실적 분명히 나왔는데 매출 브레이크다운이 안 돼 있다." 사업부 매출은 10-Q 가 나와야(벌크·
edgar) 들어오고, stockanalysis 는 며칠 늦다. 그 사이(며칠~몇 주) 차트의 마지막 칸이 비었다. 그런데 보도자료에는
사업부(또는 매출 종류) 표가 이미 있다 — 구글의 「Revenues by type」, 대부분 회사의 「Segment revenues」.

읽는 법(pr_table 과 같은 생각 — 열 위치를 가정하지 않고 우리가 가진 숫자로 맞춘다):
  1. 우리가 이미 가진 그 회사 부문 기록(segments.json > segments_fpi > segments_sec + edgar)에서 이름과
     **전년 같은 분기 값**을 꺼낸다.
  2. 보도자료 표에서 이름이 같은 줄을 찾고, 그 줄의 숫자 가운데 전년 값과 0.6% 안으로 맞는 열을 찾는다
     (단위 1·천·백만·십억을 다 대 본다). 모든 줄이 **같은 열**에서 맞아야 한다.
  3. 바로 옆 열(앞 또는 뒤)이 이번 분기다. 둘 중 '부문 합 ÷ 총매출' 비가 전년 분기의 그 비와 3% 안인 쪽만 쓴다.
  4. 못 맞추면 안 싣는다. 틀린 칸보다 빈칸이 낫다(그 칸은 10-Q 가 나오면 벌크·edgar 가 채운다).

data/segments_pr.json 에 {종목: {names, pts:[[종료일, 값…]], acc}} 로 쌓고 build.py 가 가운데·뒤 빈 칸에 잇는다.

    python scrape_seg_pr.py                # 발표했는데 부문 마지막 칸이 빈 회사들
    python scrape_seg_pr.py GOOGL MSFT     # 몇 종목만 보고 찍는다
"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pr_table as pt
import scrape_live_us as lu
import scrape_pr_us as pr

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_pr.json"
SECS = int(os.environ.get("SEGPR_SECS", "240"))
NORM = re.compile(r"[^a-z0-9]|and")


def key(n):
    return NORM.sub("", (n or "").lower())


def load(name):
    try:
        return json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        return {}


def seg_record(sym, srcs):
    """화면이 쓰는 것과 같은 차례로 그 종목의 부문 기록(이름·점). 뒤엣것이 이긴다."""
    rec = None
    for st in srcs:
        v = st.get(sym) or st.get("us:" + sym)
        if v and v.get("names") and v.get("pts"):
            rec = v
    return rec


def value_at(srcs, sym, end, name):
    """어느 소스든 그 종료일(±12일)의 그 부문 값 — 전년 같은 분기를 찾는 데 쓴다."""
    d0 = date.fromisoformat(end)
    for st in srcs:
        v = st.get(sym) or st.get("us:" + sym)
        if not v:
            continue
        ks = [key(n) for n in v.get("names") or []]
        if key(name) not in ks:
            continue
        j = ks.index(key(name))
        for p in v.get("pts") or []:
            try:
                if abs((date.fromisoformat(p[0]) - d0).days) <= 12 and j + 1 < len(p) and p[j + 1]:
                    return p[j + 1]
            except (ValueError, TypeError):
                continue
    return None


def read(raw, names, prior, tot_now, tot_prior):
    """보도자료 HTML -> {부문: 이번 분기 값} 또는 None."""
    best = None
    for t in pt.tables(raw):
        rows = {}
        for r in t:
            lab, nums = pt.row_split(r)
            k = key(lab)
            if k and nums and k not in rows:
                rows[k] = nums
        hit = [n for n in names if key(n) in rows and prior.get(n)]
        if len(hit) < max(2, int(len(names) * 0.6)):
            continue
        for scale in (1e6, 1e3, 1, 1e9):
            cols = None
            for n in hit:
                c = {i for i, v in enumerate(rows[key(n)]) if abs(v * scale - prior[n]) <= abs(prior[n]) * 0.006}
                cols = c if cols is None else cols & c
                if not cols:
                    break
            if not cols:
                continue
            for c in sorted(cols):
                for cur in (c - 1, c + 1):
                    if cur < 0:
                        continue
                    vals = {}
                    for n in hit:
                        nums = rows[key(n)]
                        if cur < len(nums):
                            vals[n] = nums[cur] * scale
                    if len(vals) != len(hit):
                        continue
                    r_now = sum(vals.values()) / tot_now
                    r_pri = sum(prior[n] for n in hit) / tot_prior
                    if abs(r_now / r_pri - 1) <= 0.03 and all(v >= 0 for v in vals.values()):
                        cand = (len(hit), -abs(r_now / r_pri - 1), vals)
                        if best is None or cand[:2] > best[:2]:      # 많이 맞은 표, 그다음 비가 더 맞는 쪽
                            best = cand
    return best[2] if best else None


def main():
    only = [a.upper() for a in sys.argv[1:]]
    srcs = [load("segments_sec.json"), load("segments_edgar.json"), load("segments_fpi.json"), load("segments.json")]
    fin = load("financials.json")
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    seen = db.setdefault("seen", {})
    cik_of = lu.cik_map_cached()                           # {티커: CIK}
    todo = []
    for sym, f in fin.items():
        sym = sym.replace("us:", "")
        if only and sym not in only:
            continue
        pts = f.get("points") or []
        if f.get("freq") != "Q" or len(pts) < 5:
            continue
        last = pts[-1]
        rec = seg_record(sym, srcs)
        if not rec:
            continue
        have = st.get(sym, {}).get("pts") or []
        ends = [p[0] for p in rec["pts"]] + [p[0] for p in have]
        if any(abs((date.fromisoformat(e) - date.fromisoformat(last["end"])).days) <= 12 for e in ends
               if re.match(r"\d{4}-\d\d-\d\d$", str(e))):
            continue                                       # 그 분기 부문이 이미 있다
        if not only and seen.get(sym) == last["end"]:
            continue                                       # 이 분기는 이미 해 봤다(못 읽었다) — 10-Q 를 기다린다
        todo.append((sym, last, rec))
    t0, got, miss = time.time(), 0, 0
    for sym, last, rec in todo:
        if not only and time.time() - t0 > SECS:
            break
        cik = cik_of.get(sym) or cik_of.get(sym.replace(".", "-"))
        if not cik:
            continue
        seen[sym] = last["end"]
        try:
            hit = pr.latest_earnings_8k(int(cik))
            raw = lu.ex99_html(int(cik), hit[0]) if hit else None
        except Exception as e:                             # noqa: BLE001
            print(f"  ! {sym} {e!r}"[:120])
            continue
        if not raw:
            continue
        pend = (date.fromisoformat(last["end"]) - timedelta(days=365)).isoformat()
        prior = {n: value_at(srcs, sym, pend, n) for n in rec["names"]}
        fp = [p for p in (fin.get(sym) or fin.get("us:" + sym))["points"]
              if abs((date.fromisoformat(p["end"]) - date.fromisoformat(pend)).days) <= 12]
        if not fp or not last.get("rev"):
            continue
        vals = read(raw, rec["names"], prior, last["rev"], fp[0]["rev"])
        if not vals:
            miss += 1
            if only:
                print(sym, "못 읽음", {n: prior.get(n) for n in rec["names"]})
            continue
        names = [n for n in rec["names"] if n in vals]
        old = st.get(sym) if st.get(sym, {}).get("names") == names else {"names": names, "pts": []}
        old["pts"] = [p for p in old.get("pts", []) if p[0] != last["end"]] + [[last["end"]] + [vals[n] for n in names]]
        old["acc"] = hit[0]
        st[sym] = old
        got += 1
        print(f"  {sym} {last['end']} " + " · ".join(f"{n} {vals[n]/1e9:.1f}B" for n in names))
        time.sleep(0.2)
    db["count"] = len(st)
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"보도자료 부문: 이번 {got}종목 읽음 · 못 읽음 {miss} · 대상 {len(todo)}")


if __name__ == "__main__":
    main()
