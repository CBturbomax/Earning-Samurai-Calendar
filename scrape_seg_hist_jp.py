"""일본 사업부 **옛 분기 이력을 한 번에** — 사업부 빈칸이 있는 회사의 JPX 공시 보관소 결산단신(5~6년치)을 전부 읽어 영구히 쌓는다.

TDnet 은 한 달치만 남아 놓친 분기는 영영 빈칸이었다(어드밴테스트 2024-12·2025-03, 종합상사 전부). 그 칸을 AI 로 메우려 하면 토큰이
끝없다. 그런데 보관소(www2.jpx.co.jp/disc)에는 결산단신 zip 이 그대로 있고, 부문 표는 그 zip 안 XBRL 에 태그돼 있다 — 실적 수치를
뽑는 scrape_fin_jp.read_segments 로 그대로 읽힌다(AI 없음). 목록·내려받기는 scrape_geo_jp 와 같은 길(yanoshin + 보관소).

다 읽은 회사는 `done_co` 에 적고 다시 안 본다(옛 분기는 안 바뀐다). 새 분기는 TDnet(scrape_fin_jp)이 잇는다.
갈래(HIST_SHARD "0/2")마다 제 파일(data/segments_hist_jp_s{n}.json)의 주인이다.

    python scrape_seg_hist_jp.py            # 빈칸 많은 회사부터 HIST_SECS 초 동안
    python scrape_seg_hist_jp.py 6857 8058  # 몇 종목만
"""
import json
import os
import sys
import time
import zlib
from datetime import date
from pathlib import Path

import scrape_fin_jp as F
import scrape_geo_jp as G

HERE = Path(__file__).parent
SHARD = os.environ.get("HIST_SHARD", "")
OUT = HERE / "data" / (f"segments_hist_jp_s{SHARD.split('/')[0]}.json" if SHARD else "segments_hist_jp.json")
SECS = int(os.environ.get("HIST_SECS", "1300"))


def stocks(name):
    try:
        return json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        return {}


def gaps():
    """코드 -> 사업부 빈 분기 수(2018 이후 — 보관소는 2016년치부터 있다). build 를 안 불러 싸다."""
    fin = {}
    for name in ("financials_intl.json", "financials_jp.json"):
        for k, v in stocks(name).items():
            if k.startswith("jp:"):
                for p in v.get("points") or []:
                    if p.get("end", "") >= "2018-01-01" and p.get("rev"):
                        fin.setdefault(k[3:], set()).add(p["end"])
    have = {}
    for k, v in stocks("segments_jp.json").items():
        for p in v.get("pts") or []:
            if any(x for x in p[1:]):
                have.setdefault(k[3:], []).append(date.fromisoformat(p[0]))
    out = {}
    for c, ends in fin.items():
        hd = have.get(c, [])
        miss = sum(1 for e in ends if not any(abs((date.fromisoformat(e) - d).days) <= 20 for d in hd))
        if miss:
            out[c] = miss
    return out


def quarters(raw):
    """누계 점 {"시작/끝": {부문: 값}} -> 화면용 기록(scrape_fin_jp.save_segments 와 같은 규칙)."""
    pts = []
    for span, row in raw.items():
        st, _, en = span.partition("/")
        clean = F.seg_clean(row)
        if st and en and len(clean) >= 2:
            pts.append((st, en, clean))
    q = F.merge_renamed(F.drop_totals(F.seg_quarters(pts)))
    if len(q) < 2:
        return None
    ends = sorted(q)
    tot = {}
    for e in ends:
        for n, v in q[e].items():
            tot[n] = tot.get(n, 0) + v
    order = sorted(tot, key=lambda n: -tot[n])[:12]
    if len(order) < 2:
        return None
    return {"axis": "사업부문", "names": order, "pts": [[e] + [q[e].get(n) for n in order] for e in ends], "src": "jpx-hist"}


def main():
    only = [a.zfill(4) for a in sys.argv[1:]]
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    rawdb = db.setdefault("raw", {})
    done_co = db.setdefault("done_co", {})
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8"))
        caps = caps.get("caps") or caps
    except (OSError, ValueError):
        caps = {}
    cap = lambda c: caps.get("jp:" + c) if isinstance(caps.get("jp:" + c), (int, float)) else 0   # noqa: E731
    gp = gaps()
    i_sh, n_sh = (int(x) for x in SHARD.split("/")) if SHARD else (0, 1)
    from markets import excluded
    try:
        sect = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8")).get("sectors") or {}
    except (OSError, ValueError):
        sect = {}
    # 시총 큰 순 · 은행 등 화면에 안 싣는 업종은 뺀다
    cands = only or sorted((c for c in gp if not excluded("jp", "", sect.get("jp:" + c) or "")), key=lambda c: -(cap(c) or 0))
    todo = [c for c in cands if only or (c not in done_co and zlib.crc32(c.encode()) % n_sh == i_sh)]
    print(f"이력 받을 일본 회사 {len(todo):,} (사업부 빈칸 있는 회사 {len(gp):,} · 이 갈래 다 읽은 회사 {len(done_co):,})")
    G.T0 = time.time()
    G.SECS = SECS
    t0, got, fail, nd = time.time(), 0, 0, 0
    for c in todo:
        if not only and time.time() - t0 > SECS - 90:
            break
        try:
            items = G.listing(c)
        except F.Throttled as e:
            fail += 1
            print(f"  ! {c} 목록 {e}"[:140])
            if fail >= 6 and got == 0:
                break
            continue
        raw = rawdb.setdefault(c, {})
        complete = True
        for it in items:
            if not only and time.time() - t0 > SECS - 60:
                complete = False
                break
            fn = it["url_xbrl"].rsplit("/", 1)[-1]
            try:
                blob = G.get(f"https://www2.jpx.co.jp/disc/{c}0/{fn}", timeout=60)
            except F.Throttled:
                complete = False
                continue
            if not blob:
                continue
            try:
                for s_, e_, row in F.read_segments(blob):
                    raw[f"{s_}/{e_}"] = row                    # 나중에 낸 공시(재표시)가 이긴다 — 목록이 발표 차례다
            except Exception as e:                             # noqa: BLE001 — 깨진 공시 하나가 실행을 죽이지 않게
                print(f"  {c} {fn} 읽기 실패 {e!r}"[:140])
            time.sleep(G.PAUSE)
        rec = quarters(raw) if raw else None
        if rec:
            st["jp:" + c] = rec
            got += 1
            print(f"  {c}: 공시 {len(items)} · 부문 {len(rec['pts'])}분기 {rec['pts'][0][0]}~{rec['pts'][-1][0]}", flush=True)
        if not raw:
            rawdb.pop(c, None)
        if complete:
            done_co[c] = date.today().isoformat()
            nd += 1
        if got % 10 == 0:
            OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    db["count"] = len(st)
    db["ts"] = date.today().isoformat()
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"일본 옛 이력(보관소): 이번 {got}곳 · 실패 {fail} · 이 갈래 누적 {len(st)}곳 · 남은 회사 {max(len(todo) - nd, 0) if not only else 0}")


if __name__ == "__main__":
    main()
