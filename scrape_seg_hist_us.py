"""미국 사업부·지역 **옛 분기 이력을 한 번에** — 회사마다 2017년 이후 10-Q·10-K 인라인 XBRL 을 전부 읽어 영구히 쌓는다.

회원님(11/02): "과거 실적은 한 번 찾으면 두 번 안 건드려도 되잖아." 맞다. 그런데 우리는
  * SEC 분기 벌크(segments_sec)가 회사에 따라 중간에 끊기거나(테슬라 2Q23 에서 끝) 1분기 칸 두 부문이 뒤바뀌어 있었고
  * 제출 서류(scrape_seg_edgar)는 회사마다 **최근 두 장만** 읽어 옛 구멍을 영영 못 메웠다.
그 빈칸을 AI 에게 공시를 읽혀 메우려니 토큰이 끝이 없었다. 그런데 회원님 MCP(InvestingWiki us_financials breakdown)로
테슬라 2024년 10-Q 를 열어 보니 XBRL 인스턴스에 부문 매출이 그대로 있다 — **AI 가 필요 없는 칸이었다.**

그래서 빈칸이 있는 회사부터, 그 회사의 2017-06 이후 10-Q·10-K 를 **전부** 한 번에 읽는다(회사당 요청 70번쯤 · 20초 남짓).
뜯는 규칙·축 고르기·누계 되돌리기는 scrape_seg_edgar / scrape_seg_sec 의 함수를 그대로 쓴다(한 벌). 다 읽은 회사는
`done_co` 에 적고 다시 안 본다 — 옛 분기는 안 바뀐다. 새 분기는 벌크·edgar 가 잇는다.

갈래(HIST_SHARD "0/3")마다 제 파일(data/segments_hist_us_s{n}.json)의 주인이다 — 커밋이 안 부딪힌다.

    python scrape_seg_hist_us.py            # 빈칸 많은 회사부터 HIST_SECS 초 동안
    python scrape_seg_hist_us.py TSLA MU    # 몇 종목만
"""
import json
import os
import sys
import time
import zlib
from datetime import date
from pathlib import Path

import scrape_seg_edgar as se
import scrape_seg_sec as ss

HERE = Path(__file__).parent
SHARD = os.environ.get("HIST_SHARD", "")
OUT = HERE / "data" / (f"segments_hist_us_s{SHARD.split('/')[0]}.json" if SHARD else "segments_hist_us.json")
SECS = int(os.environ.get("HIST_SECS", "1300"))
SINCE = "2017-06-01"
SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBS_OLD = "https://data.sec.gov/submissions/{name}"


def filings(cik):
    """[(보고 기준일, 서식, 접수번호(대시 포함), 접수일)] — 10-Q·10-K(정정 제외), 2017-06 이후."""
    j = json.loads(se.get(SUBS.format(cik=cik), binary=False) or "{}")
    blocks = [j.get("filings", {}).get("recent", {})]
    for f in j.get("filings", {}).get("files", []) or []:
        if (f.get("filingTo") or "") >= SINCE:
            time.sleep(se.PAUSE)
            blocks.append(json.loads(se.get(SUBS_OLD.format(name=f["name"]), binary=False) or "{}"))
    out = set()
    for r in blocks:
        for i, form in enumerate(r.get("form", [])):
            if form not in ("10-Q", "10-K"):
                continue
            rd = (r.get("reportDate") or [""] * (i + 1))[i]
            if rd and rd >= SINCE:
                out.add((rd, form, r["accessionNumber"][i], r["filingDate"][i]))
    return sorted(out, key=lambda x: x[3])         # 접수 차례 — '나중 서류가 이긴다'


def gaps_by_ticker():
    """티커 -> 빈 분기 수(2018 이후 총매출은 있는데 부문 종료일이 ±20일 안에 없는 분기). build 를 안 불러 싸다."""
    def stocks(name):
        try:
            return json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("stocks", {})
        except (OSError, ValueError):
            return {}
    fin = stocks("financials.json")
    have = {}
    for name in ("segments_sec.json", "segments_edgar.json", "segments.json"):
        for k, v in stocks(name).items():
            t = k.replace("us:", "")
            for p in v.get("pts") or []:
                if any(x for x in p[1:]):
                    have.setdefault(t, set()).add(str(p[0]))
    out = {}
    for k, f in fin.items():
        t = k.replace("us:", "")
        if f.get("freq") != "Q":
            continue
        ends = [p["end"] for p in f.get("points") or [] if p.get("end", "") >= "2018-01-01" and p.get("rev")]
        hs = sorted(have.get(t, ()))
        hd = [date.fromisoformat(h) for h in hs if len(h) == 10]
        miss = sum(1 for e in ends if not any(abs((date.fromisoformat(e) - d).days) <= 20 for d in hd))
        if miss:
            out[t] = miss
    return out


def main():
    only = [a.upper() for a in sys.argv[1:]]
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    done_co = db.setdefault("done_co", {})
    caps = ss.want_tickers()
    cikmap = ss.cik_map()
    gp = gaps_by_ticker()
    i_sh, n_sh = (int(x) for x in SHARD.split("/")) if SHARD else (0, 1)
    cands = only or sorted((t for t in gp if t in caps), key=lambda t: -(gp[t] * (1 + (caps.get(t) or 0) / 1e10)))
    todo, seen_cik = [], set()
    for t in cands:
        cik = ss.CIK_ALIAS.get(t) or cikmap.get(t) or cikmap.get(t.replace(".", "-"))
        if not cik or cik in seen_cik:
            continue
        if not only and (t in done_co or zlib.crc32(t.encode()) % n_sh != i_sh):
            continue
        seen_cik.add(cik)
        todo.append((t, cik))
    print(f"이력 받을 회사 {len(todo):,} (빈칸 있는 미국 회사 {len(gp):,} · 이 갈래 다 읽은 회사 {len(done_co):,})")
    t0, got, fail = time.time(), 0, 0
    keep = (ss.MIN_PTS, ss.MIN_MEMBER_PTS)
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = 3, 2
    for t, cik in todo:
        if not only and time.time() - t0 > SECS:
            break
        try:
            fl = filings(cik)
            facts, pairs = {}, {}
            n = 0
            for rd, form, acc, filed in fl:
                url = se.instance_url(cik, acc)
                time.sleep(se.PAUSE)
                if not url:
                    continue
                blob = se.get(url)
                time.sleep(se.PAUSE)
                if not blob:
                    continue
                try:
                    rows = se.parse_instance(blob)
                except ss.Blocked:
                    continue
                se.absorb(facts, pairs, cik, filed, acc, rows)
                n += 1
        except ss.Blocked as e:
            fail += 1
            print(f"  ! {t} {e}"[:160])
            if fail >= 6 and got == 0:
                print("  ! 연속 실패 — 접는다")
                break
            continue
        rec = ss.build_stock(facts.get(cik, {}), pairs.get(cik, {}))
        done_co[t] = date.today().isoformat()
        if rec:
            rec = ss.dedupe_names(rec)
            if rec.get("geo"):
                rec["geo"] = ss.dedupe_names(rec["geo"])
            rec["src"] = "edgar-hist"
            st[t] = rec
            got += 1
            print(f"  {t}: 서류 {n} · 부문 {len(rec.get('pts') or [])}분기 {rec['pts'][0][0] if rec.get('pts') else ''}~"
                  + (f" · 지역 {len(rec['geo']['pts'])}" if rec.get("geo") else ""), flush=True)
        if got % 10 == 0:
            db["count"] = len(st)
            OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = keep
    db["count"] = len(st)
    db["ts"] = date.today().isoformat()
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"옛 이력(XBRL): 이번 {got}곳 · 실패 {fail} · 이 갈래 누적 {len(st)}곳 · 남은 회사 {max(len(todo) - got - fail, 0)}")


if __name__ == "__main__":
    main()
