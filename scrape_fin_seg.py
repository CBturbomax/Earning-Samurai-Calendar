# -*- coding: utf-8 -*-
"""
사업부별 매출 수집 — 출처: stockanalysis.com (revenue-by-segment)

"이 회사 매출이 늘었다"보다 "**어디서** 늘었다"가 중요할 때가 있다. 로켓랩은
발사 서비스와 우주 시스템이 따로 움직이고, SEA 는 쇼피·가레나·머니가 따로 논다.

SEC 로는 못 한다. companyfacts/companyconcept 는 부문 축(dimension)을 떨어뜨리고
연결 합계만 준다 — 부문별 값은 제출 서류 원본(XBRL 인스턴스)에나 있다.
stockanalysis 에는 부문 페이지가 따로 있어서 거기서 받는다. **미국 종목만** 있다
(일본·홍콩은 404).

조심할 것이 둘 있다.

1) **0 은 '없음'이지 '0원'이 아니다.** 회사가 부문 이름을 바꾸면 옛 이름은 그
   시점부터, 새 이름은 그 이전이 0으로 채워져 온다. 그대로 쌓으면 없던 사업이
   바닥에 깔린다. 0 은 버린다.

2) **이름만 바뀐 같은 부문이 둘로 온다.** SEA 의 'Other Services' 와 'Monee' 는
   2021~2025 값이 한 푼도 다르지 않다 — 이름만 갈렸다. 둘 다 쌓으면 매출이
   부풀어 총매출과 안 맞는다. 같은 분기에 값이 똑같으면 하나로 본다.

결과: data/segments.json
"""
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import scrape_fin_intl as si          # 요청·unflatten·Throttled 을 같이 쓴다

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments.json"

SA_SEG = "https://stockanalysis.com/stocks/{sym}/metrics/revenue-by-segment/__data.json"

PER_RUN = int(os.environ.get("SEG_PER_RUN", "200"))    # 한 실행에 받을 종목 수
STALE_DAYS = int(os.environ.get("SEG_STALE_DAYS", "20"))
TOP_N = int(os.environ.get("SEG_TOP_N", "1500"))       # 시총 상위 이만큼만
PAUSE = float(os.environ.get("SEG_PAUSE", "0.6"))
SEG_VER = 1
GIVE_UP_AFTER = 6


def find_segments(o, depth=0):
    """트리에서 부문 표를 찾는다 — quarterly/trailing 을 가진 dict."""
    if depth > 8:
        return None
    if isinstance(o, dict):
        q = o.get("quarterly")
        if isinstance(q, list) and q and isinstance(q[0], dict) and "values" in q[0]:
            return q
        for v in o.values():
            got = find_segments(v, depth + 1)
            if got:
                return got
    elif isinstance(o, list):
        for v in o[:60]:
            got = find_segments(v, depth + 1)
            if got:
                return got
    return None


def parse(txt):
    """응답 -> ([부문 이름], {종료일: {이름: 값}})."""
    body = json.loads(txt)
    for node in body.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "data":
            continue
        arr = node.get("data")
        if not isinstance(arr, list):
            continue
        segs = find_segments(si.unflatten(arr))
        if not segs:
            continue

        # {종료일: {이름: 값}} 으로 눕힌다. 0 은 담지 않는다 — '없음'이지 '0원'이 아니다.
        by_end = {}
        order = []
        for s in segs:
            name = (s.get("name") or "").strip()
            if not name:
                continue
            order.append(name)
            for v in s.get("values") or []:
                end, y = v.get("x"), si.num(v.get("y"))
                if not end or not si.DATE_RE.match(str(end)) or not y:
                    continue
                by_end.setdefault(end, {})[name] = y
        if by_end:
            return order, by_end
    return [], {}


def dedupe(by_end, order):
    """이름만 바뀐 같은 부문을 하나로 친다.

    같은 분기에 값이 정확히 같으면 같은 것으로 본다. 남길 쪽은 **가장 최근까지
    값이 있는 이름**이다 — 회사가 지금 쓰는 이름이 그쪽이다.
    """
    last = {}
    for end, row in by_end.items():
        for name in row:
            if end > last.get(name, ""):
                last[name] = end

    dropped = set()
    for end, row in by_end.items():
        names = [n for n in row if n not in dropped]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if a in dropped or b in dropped:
                    continue
                if row.get(a) == row.get(b):
                    # 최근까지 살아 있는 쪽을 남긴다.
                    dropped.add(a if last.get(a, "") < last.get(b, "") else b)
    if dropped:
        for row in by_end.values():
            for n in dropped:
                row.pop(n, None)
    return [n for n in order if n not in dropped], dropped


def series(sym):
    """한 종목의 부문별 매출. 없으면 None."""
    url = SA_SEG.format(sym=sym.lower().replace(".", "-"))
    for wait in si.BACKOFF:
        if wait:
            time.sleep(wait)
        try:
            txt = si.get(url)
        except si.Throttled:
            continue
        if txt is None:
            return None                      # 부문 페이지가 없는 종목
        try:
            order, by_end = parse(txt)
        except (ValueError, TypeError) as e:
            raise si.Throttled(f"응답을 못 읽었다: {e}")
        if not by_end or len(order) < 2:
            return None                      # 부문이 하나뿐이면 나눌 게 없다
        names, _ = dedupe(by_end, order)
        if len(names) < 2:
            return None
        ends = sorted(by_end)
        return {
            "v": SEG_VER,
            "names": names,
            # [종료일, 부문1, 부문2, …] — 없는 값은 null
            "pts": [[e] + [by_end[e].get(n) for n in names] for e in ends],
        }
    raise si.Throttled("네 번 다 실패")


def targets():
    """시총 상위 미국 종목. 부문 페이지는 미국만 있다(일본·홍콩 404)."""
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    caps = {}
    for r in d.get("rows", []):
        c = r.get("code")
        if c:
            caps[c] = max(caps.get(c, 0), r.get("cap") or 0)
    top = sorted(caps.items(), key=lambda kv: -kv[1])[:TOP_N]
    return dict(top)


def queue(old, cand, ann):
    """받을 순서. 방금 발표한 것 -> 못 받은 것 -> 오래된 것. 시총 큰 순."""
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 6)).isoformat()
    recent = (date.today() - timedelta(days=45)).isoformat()
    picks = []
    for code, cap in cand.items():
        rec = old.get(code)
        last_ann = ann.get("us:" + code, (9999, ""))[1]
        if last_ann >= recent and (not rec or (rec.get("ts") or "") < last_ann):
            picks.append((-1, -cap, code))
            continue
        if not rec or rec.get("v") != SEG_VER:
            pri = 0
        else:
            ts = rec.get("ts") or ""
            if not rec.get("names"):
                if ts >= cold:
                    continue                 # 부문을 안 나누는 회사. 자주 안 본다.
                pri = 2
            elif ts < stale:
                pri = 1
            else:
                continue
        picks.append((pri, -cap, code))
    picks.sort()
    return [c for _, _, c in picks]


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if probe:
        for sym in ("RKLB", "AAPL", "SE", "NVDA"):
            print(f"\n===== {sym}")
            try:
                s = series(sym)
            except si.Throttled as e:
                print("  실패:", e)
                continue
            if not s:
                print("  부문 자료 없음")
                continue
            print("  부문:", s["names"])
            print(f"  분기 {len(s['pts'])}개  {s['pts'][0][0]} ~ {s['pts'][-1][0]}")
            for row in s["pts"][-2:]:
                print("   ", row[0], [f"{v/1e6:,.0f}M" if v else "-" for v in row[1:]])
        return

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    cand = targets()
    ann = si.announcements()
    pending = queue(old, cand, ann)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개 "
          f"(가진 것 {len(old):,}개 / 후보 {len(cand):,}개)")

    today = date.today().isoformat()
    stocks = dict(old)
    got, streak = 0, 0
    for i, code in enumerate(todo):
        rec = None
        try:
            rec = series(code)
            streak = 0
        except si.Throttled as e:
            streak += 1
            print(f"    {code} 막힘: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print(f"  연속 {streak}종목이 막혔다. 이번 실행은 여기서 접는다.",
                      file=sys.stderr, flush=True)
                break
            continue                          # 막힌 종목은 기록을 건드리지 않는다
        if rec:
            got += 1
        else:
            rec = {}                          # 부문을 안 나누는 회사
        rec["v"] = SEG_VER
        rec["ts"] = today
        stocks[code] = rec
        if i % 25 == 24:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(PAUSE)

    have = {k: v for k, v in stocks.items() if v.get("names")}
    payload = {
        "source": "stockanalysis.com (사업부별 매출)",
        "note": ("미국 종목만 있다. 0 은 '그 부문이 없던 때'라 담지 않고, "
                 "이름만 바뀐 같은 부문은 하나로 친다."),
        "count": len(have),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    n = sorted(len(v["names"]) for v in have.values())
    print(f"\n{len(have):,}종목 -> {OUT} (부문을 안 나누는 종목 {len(stocks)-len(have):,})")
    if n:
        print(f"  부문 수 중앙값 {n[len(n)//2]}개 (가장 적은 것 {n[0]} · 많은 것 {n[-1]})")


if __name__ == "__main__":
    main()
