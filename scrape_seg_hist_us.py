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
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
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


_LOCK, _T = threading.Lock(), [0.0]


def paced(fn, *a):
    """SEC 는 IP 하나에 초당 10건 — 줄 넷이 같이 써도 0.12초 간격을 지킨다."""
    with _LOCK:
        w = 0.12 - (time.time() - _T[0])
        if w > 0:
            time.sleep(w)
        _T[0] = time.time()
    return fn(*a)


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
    revs = {}
    for k, f in fin.items():
        revs[k.replace("us:", "")] = [(date.fromisoformat(p["end"]), p["rev"]) for p in f.get("points") or []
                                     if p.get("end") and p.get("rev")]

    def full(t, p):
        """**반쪽 칸은 빈칸이다**(11/04). CAT 2018~2021 은 벌크에 금융 부문 한 줄만, COST 결산 분기는 회비 한 줄만
        있었는데 '값이 있다'로 쳐서 이 수집기가 받을 게 없다고 보고 그 회사를 끝냄으로 적었다(끝낸 1,986곳 중 자료를
        남긴 곳 681). 부문 합이 그 분기 총매출의 70% 이상일 때만 채워진 칸이다."""
        vals = [x for x in p[1:] if x]
        if not vals or not str(p[0])[:4].isdigit() or len(str(p[0])) != 10:
            return False
        d0 = date.fromisoformat(str(p[0]))
        near = [r for e, r in revs.get(t, []) if abs((e - d0).days) <= 20]
        if not near:
            return len(vals) >= 2
        return sum(vals) >= 0.7 * near[0]
    have = {}
    for name in ("segments_sec.json", "segments_edgar.json"):    # segments.json(stockanalysis)은 이름이 달라 화면 기록과 안 이어진다 — 덮인 걸로 안 친다
        for k, v in stocks(name).items():
            t = k.replace("us:", "")
            for p in v.get("pts") or []:
                if full(t, p):
                    have.setdefault(t, set()).add(str(p[0]))
    out = {}
    for k, f in fin.items():
        t = k.replace("us:", "")
        if f.get("freq") != "Q":
            continue
        ends = [p["end"] for p in f.get("points") or [] if p.get("end", "") >= "2018-01-01" and p.get("rev")]
        hs = sorted(have.get(t, ()))
        hd = [date.fromisoformat(h) for h in hs if len(h) == 10]
        miss = [e for e in ends if not any(abs((date.fromisoformat(e) - d).days) <= 20 for d in hd)]
        if miss:
            out[t] = miss
    return out


def pick(fl, miss):
    """빈 종료일을 덮는 서류만 — 그 분기에 끝난 서류(없으면 한 해 뒤 서류: 전년 같은 분기 비교치), 결산 분기면 3분기 10-Q 도
    (연간 − 9개월). 회사마다 37장을 다 읽던 첫 판이 회사당 3분 반이라(11/02 실측) 이렇게 좁힌다."""
    d = date.fromisoformat
    want, cov = [], []
    for e in sorted(miss, reverse=True):
        ed = d(e)
        if any(abs((ed - c).days) <= 15 for c in cov):
            continue
        hit = [f for f in fl if abs((d(f[0]) - ed).days) <= 12]
        nxt = [f for f in fl if abs((d(f[0]) - ed).days - 365) <= 15]
        for f in (hit or nxt)[:1]:
            if f not in want:
                want.append(f)
            fd = d(f[0])
            cov.extend([fd, date.fromordinal(fd.toordinal() - 364)])
            if f[1] == "10-K":
                for g in fl:
                    if g[1] == "10-Q" and 80 <= (fd - d(g[0])).days <= 100 and g not in want:
                        want.append(g)
    return sorted(want, key=lambda x: x[3])


def main():
    only = [a.upper() for a in sys.argv[1:]]
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    done_co = db.setdefault("done_co", {})
    if (db.get("gv") or 0) < 4:                              # 4(11/10): 대상에 **화면 기준 빈칸**(seg_holes.json)을 더했다 — 한 번 다시 본다
        done_co.clear(); db["gv"] = 4
    if False:                                    # 1: 반쪽 칸을 채워진 칸으로 셌다 · 2: ReportableSubsegments 행을 버렸다(CAT) — 다시 본다
        done_co.clear(); db["gv"] = 3
    caps = ss.want_tickers()
    cikmap = ss.cik_map()
    gp = gaps_by_ticker()
    # **화면에서 빈 분기도 대상이다(11/10).** 원자료에는 값이 있어도 이름이 안 맞거나 틀려서 화면에서 빠진 분기(AMD 2Q21·3Q21)는
    # 위 셈에 안 잡혀, 공짜로 읽을 수 있는 XBRL 을 두고 AI 가 같은 공시를 글로 읽고 있었다. 빌드가 남긴 목록을 합친다.
    try:
        sh = json.loads((HERE / "data" / "seg_holes.json").read_text(encoding="utf-8")).get("holes") or {}
    except (OSError, ValueError):
        sh = {}
    for k, ends in sh.items():
        if k.startswith("us:") and ends:
            t = k[3:]
            gp[t] = sorted(set(gp.get(t) or []) | {e for e in ends if e >= "2018-01-01"})
    i_sh, n_sh = (int(x) for x in SHARD.split("/")) if SHARD else (0, 1)
    from markets import excluded
    try:
        ind = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8")).get("industries") or {}
    except (OSError, ValueError):
        ind = {}
    # 시총 큰 순(화면에서 보는 회사부터) · 은행·리츠 등 화면에 안 싣는 업종은 뺀다(첫 판이 은행·리츠부터 읽었다)
    # **이번 주(앞뒤 열흘) 발표 회사가 맨 앞**(11/04 회원님: SN 사업부에 3Q25·4Q25·2024 이전이 비었는데 시총 순 줄에서
    # 아직 차례가 안 왔다). 그다음 시총.
    hot = set()
    try:
        from datetime import timedelta
        eu = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8"))
        lo, hi = (date.today() - timedelta(days=10)).isoformat(), (date.today() + timedelta(days=10)).isoformat()
        hot = {r.get("code") for r in (eu.get("rows") or []) if lo <= (r.get("date") or "") <= hi}
    except (OSError, ValueError):
        pass
    cands = only or sorted((t for t in gp if t in caps and not excluded("us", "", "", (ind.get("us:" + t) or "").replace("-", ""))),
                           key=lambda t: (t not in hot, -(caps.get(t) or 0)))
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
            if not only or gp.get(t):
                fl = pick(fl, gp.get(t) or [])
            facts, pairs = {}, {}

            def one(f):
                rd, form, acc, filed = f
                url = paced(se.instance_url, cik, acc)
                if not url:
                    return f, None
                blob = paced(se.get, url)
                try:
                    return f, (se.parse_instance(blob) if blob else None)
                except ss.Blocked:
                    return f, None
            n = 0
            with ThreadPoolExecutor(4) as ex:
                for f, rows in ex.map(one, fl):
                    if rows:
                        se.absorb(facts, pairs, cik, f[3], f[2], rows)
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
            # 앞서 읽은 기록을 버리지 않는다 — 이번엔 빈 분기를 덮는 서류만 읽어 기록이 짧다. 옛 기록은 more 에 남겨 빌드가 둘 다 본다.
            if st.get(t) and st[t].get("pts") != rec.get("pts"):
                more = db.setdefault("more", {}).setdefault(t, [])
                more.append({k: st[t][k] for k in ("names", "pts", "axis") if k in st[t]})
                del more[:-4]
            st[t] = rec
            got += 1
            print(f"  {t}: 빈 분기 {len(gp.get(t) or [])} · 서류 {n} · 부문 {len(rec.get('pts') or [])}분기 {rec['pts'][0][0] if rec.get('pts') else ''}~"
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
