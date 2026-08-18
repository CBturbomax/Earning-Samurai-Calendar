# -*- coding: utf-8 -*-
"""미국 상장 **외국 기업**의 부문별 매출 — 20-F·6-K 의 인라인 XBRL.

왜 따로 만드나. 미국 국내 기업은 10-Q/10-K 를 내므로 SEC 분기 벌크
(`scrape_seg_sec.py`)와 분기 색인(`scrape_seg_edgar.py`)이 다 잡는다. 그런데
**외국 기업(FPI)은 10-Q 를 아예 안 낸다** — 연 1회 20-F 와 수시 6-K 다. 그래서
TSMC·HSBC·ARM·아메르스포츠 같은 큰 회사의 부문이 어느 소스에도 없었다.
회원님이 아메르스포츠를 짚어 물으신 자리가 여기다.

측정(probe 28-C)해서 안 것:

  ARM        6-K 가 전부 XBRL=1        · us-gaap 288개
  아메르스포츠 6-K(as-20260331.htm) XBRL=1 · ifrs-full 291개
  HSBC       6-K(hsbc-20260630.htm) XBRL=1 · ifrs-full 405개
  TSMC       최근 6-K 는 전부 XBRL=0    · 20-F(연 1회)에만 XBRL

즉 **분기 XBRL 이 있는 회사와 없는 회사가 갈린다.** 있는 회사는 여기서 받고,
없는 회사(TSMC)는 이 길로도 분기가 없다 — 없는 것을 지어내지 않는다.

어떻게. 분기 색인(form.idx)은 10-Q/10-K 만 훑으므로 여기서는 **회사별 제출
목록**(submissions JSON)을 쓴다. 종목당 요청 한 번으로 XBRL 이 붙은 서류만
골라내므로, 6-K 를 859건 내는 HSBC 같은 회사에서도 헛걸음이 없다.

뜯고 쌓는 규칙은 벌크·색인과 **한 벌**이다(`scrape_seg_sec` 의 축 고르기·상계
빼기·누계 되돌리기, `scrape_seg_edgar` 의 인스턴스 파서). 같은 판단을 세 군데
적어 두면 반드시 갈라진다.
"""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import io
import re
import xml.etree.ElementTree as ET

import scrape_seg_sec as ss
from scrape_seg_edgar import (absorb, ddate_of, get, instance_url, local,
                              parse_instance, qtrs_of)

# 회사 전체(부문 축이 안 걸린) 매출·영업이익. 부문을 뽑으려고 이미 받아 둔
# 인스턴스에서 같이 뽑는다 — 요청이 한 번도 안 는다.
OPI_TAGS = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities",
            "OperatingProfitLoss", "ProfitLossFromOperations"]

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_fpi.json"
# **실적 수치도 여기서 낸다.** 아메르스포츠 같은 외국 기업은 10-Q 를 안 내므로
# SEC 분기 수치(financials.json)가 없고, stockanalysis 는 며칠 늦다 — 실제로
# 8/18 아침에 발표한 6월 분기가 그날 저녁까지 안 실렸다. 부문을 뽑는 그 6-K
# 인스턴스에 회사 전체 매출·영업이익도 함께 있으므로 같이 담는다.
FIN_OUT = HERE / "data" / "financials_fpi.json"

# 2: 표시축(SegmentConsolidationItems)·지역축(GeographicalAreas)·HSBC 의 매출
#    항목을 알아보게 고쳤다. 이미 본 서류를 다시 뜯어야 하므로 판을 올린다.
# 3: 이름공간을 안 따진다(HSBC 는 회사 확장 항목이었다) · 표지뿐인 6-K 를
#    넘기며 열어 보는 한도를 열 장으로.
FPI_VER = 3
# **뜯는 규칙이 바뀌면 이것만 올린다.** 판(FPI_VER)을 올리면 모아둔 종목까지
# 통째로 지워져 처음부터 다시 훑어야 한다 — 오늘 그걸 두 번 하며 시총 아래쪽
# 회사(아메르스포츠)를 두 번 잃었다. 이 번호가 다르면 **본 서류 기록만** 비우고
# 이미 얻은 종목은 그대로 둔다. 다시 훑으면서 덮어쓰면 그만이다.
PARSE_VER = 2      # 2: 반기 결산(HSBC 류) 지원
SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# 외국 기업이 내는 서류. 40-F 는 캐나다 회사다.
FORMS = ("6-K", "20-F", "40-F")
# 한 실행에 회사 몇 곳까지. 회사마다 요청이 1 + 2×서류라 넉넉히 잡아도 가볍다.
PER_RUN = int(os.environ.get("FPI_PER_RUN", "40"))
# 회사마다 **부문이 실제로 나온** 서류를 몇 장까지 모을까. 6-K 한 장에 이번
# 분기와 전년 같은 분기가 함께 실리므로 석 장이면 대여섯 분기가 모인다.
DOCS_PER_CO = 3
# 그 석 장을 찾느라 열어 볼 수 있는 서류 수. 6-K 에는 배당 공고·표지뿐인 것이
# 섞여 있어(ARM 은 셋 중 둘이 그랬다) 열어 봐야 알맹이가 있는지 안다. 이 여유가
# 없으면 알맹이 있는 서류를 못 채워 기록이 안 만들어진다.
TRIES_PER_CO = 10
PAUSE = 0.25
# 다 따라잡은 회사는 이만큼 지나야 다시 본다(6-K 는 분기마다 나온다).
REFRESH_DAYS = 20


def load_old():
    if OUT.exists():
        try:
            d = json.loads(OUT.read_text(encoding="utf-8"))
            if d.get("v") == FPI_VER:
                if d.get("pv") != PARSE_VER:
                    # 뜯는 규칙이 바뀌었다. 서류를 다시 읽어야 하므로 '본 것'만
                    # 비우고 이미 얻은 종목은 남긴다.
                    print(f"  뜯는 규칙이 바뀌었다(pv {d.get('pv')} -> {PARSE_VER})."
                          f" 서류를 다시 읽는다.")
                    d["done"], d["seen"] = [], {}
                return d
        except (ValueError, OSError):
            pass
    return {"v": FPI_VER, "pv": PARSE_VER, "stocks": {}, "done": [], "seen": {}}


def covered():
    """이미 다른 소스가 부문을 주는 종목. 그런 회사를 다시 두드리지 않는다."""
    out = set()
    for name in ("segments_sec.json", "segments_edgar.json", "segments.json"):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            continue
        for k, v in st.items():
            if v.get("names"):
                out.add(k.split(":")[-1])
    return out


def announced_recently(days=12):
    """최근 며칠 안에 실적을 발표한 미국 종목 -> 발표일."""
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return {}
    lo = (date.fromordinal(date.today().toordinal() - days)).isoformat()
    hi = date.today().isoformat()
    out = {}
    for r in rows:
        c, d = r.get("code"), r.get("date") or ""
        if c and lo <= d <= hi:
            out[c] = max(out.get(c, ""), d)
    return out


def universe(old):
    """이번에 볼 종목을 순서대로. 두 갈래를 이어 붙인다.

    1. **방금 발표한 외국 기업**을 맨 앞에 둔다. 부문을 이미 얻은 회사라도
       실적 수치는 새 분기가 나왔을 수 있다 — 아메르스포츠가 그랬다. 부문이
       있다고 대기줄에서 빼 버렸더니 발표 당일에 숫자가 안 들어왔다.
       (여기 오르는 것은 지난번에 XBRL 서류가 실제로 잡힌 회사뿐이다.
       미국 국내 기업을 매일 두드리면 헛걸음만 는다.)
    2. 그다음이 **부문이 아직 없는 종목**, 시총 큰 순.
    """
    caps = ss.want_tickers()
    have = covered()
    seen = old.get("seen", {})
    # 이미 이 수집기로 부문을 얻은 회사는 두말할 것 없이 외국 기업이다.
    # (처음 돌 때는 'fpi' 목록이 비어 있으므로 여기서 씨앗을 얻는다 —
    #  아니면 부문이 있는 회사는 대기줄 어디에도 못 올라 영영 안 본다.)
    fpi = set(old.get("fpi") or []) | {k.split(":")[-1] for k in (old.get("stocks") or {})}
    today = date.today().isoformat()
    cut = (date.fromordinal(date.today().toordinal() - REFRESH_DAYS)).isoformat()

    fresh = [(c, d) for c, d in announced_recently().items()
             if c in fpi and (seen.get(c, "") < d or seen.get(c, "") < today)]
    fresh.sort(key=lambda kv: (-caps.get(kv[0], 0),))

    todo = [(c, cap) for c, cap in caps.items()
            if c not in have and (seen.get(c, "") < cut)]
    todo.sort(key=lambda kv: -kv[1])

    order, seen_once = [], set()
    for c in [c for c, _ in fresh] + [c for c, _ in todo]:
        if c not in seen_once:
            seen_once.add(c)
            order.append(c)
    if fresh:
        print(f"  방금 발표한 외국 기업 {len(fresh)}곳을 먼저 본다: "
              + ", ".join(c for c, _ in fresh[:8]))
    return order, today


def xbrl_filings(cik):
    """XBRL 이 붙은 20-F·6-K 를 새것부터. [(접수번호, 접수일, 서류종류)]"""
    body = get(SUBS.format(cik=cik), timeout=60, binary=False)
    if not body:
        return []
    try:
        rec = json.loads(body)["filings"]["recent"]
    except (ValueError, KeyError, TypeError):
        return []
    forms = rec.get("form") or []
    accs = rec.get("accessionNumber") or []
    dates = rec.get("filingDate") or []
    xb = rec.get("isXBRL") or []
    out = []
    for i, f in enumerate(forms):
        if f not in FORMS or i >= len(accs) or i >= len(dates):
            continue
        if i < len(xb) and not xb[i]:
            continue                      # 인스턴스가 없는 서류는 헛걸음이다
        out.append((accs[i], dates[i], f))
    return out


def totals(blob):
    """인스턴스 -> {(종료일, 기간길이): {"rev": 매출, "opi": 영업이익}}

    **부문 축이 안 걸린 문맥만** 쓴다. 축이 걸린 값은 부문 매출이라 그것을
    회사 전체로 적으면 매출이 조각난다. 회계기준은 안 가린다(국내 us-gaap ·
    외국 ifrs-full · 회사 확장) — 이름으로만 거른다.
    """
    ctx, out = {}, {}
    rev_rank, opi_rank = ss.REV_RANK, {t: i for i, t in enumerate(OPI_TAGS)}
    try:
        for ev, el in ET.iterparse(io.BytesIO(blob), events=("end",)):
            n = local(el.tag)
            if n == "context":
                cid, dims, st, en = el.get("id"), 0, "", ""
                for m in el.iter():
                    lm = local(m.tag)
                    if lm == "explicitMember":
                        dims += 1
                    elif lm == "startDate":
                        st = (m.text or "").strip()
                    elif lm == "endDate":
                        en = (m.text or "").strip()
                if cid and not dims and st and en:
                    ctx[cid] = (st, en)
                el.clear()
            elif el.get("contextRef"):
                slot = ctx.get(el.get("contextRef"))
                r, o = rev_rank.get(n), opi_rank.get(n)
                if slot and (r is not None or o is not None):
                    txt = (el.text or "").strip().replace(",", "")
                    if re.fullmatch(r"-?\d+(\.\d+)?", txt or ""):
                        q = qtrs_of(*slot)
                        if q:
                            # 열쇠는 부문 쪽과 같은 꼴(YYYYMMDD, 월말 반올림)이라야
                            # ss.quarterly 의 '한 분기 앞' 셈이 맞는다.
                            key = (ddate_of(slot[1]), q)
                            box = out.setdefault(key, {})
                            val = float(txt)
                            if r is not None and (box.get("revRank", 99) > r):
                                box["rev"], box["revRank"] = val, r
                            if o is not None and (box.get("opiRank", 99) > o):
                                box["opi"], box["opiRank"] = val, o
                el.clear()
    except ET.ParseError:
        return {}
    return out


def fin_points(raw):
    """{(종료일, 기간길이): 값} -> 화면용 분기(또는 반기) 점 목록.

    누계를 되돌리는 규칙은 부문과 한 벌이다 — `scrape_seg_sec.quarterly` 를
    그대로 부른다. 분기가 하나도 안 나오면 그쪽이 반기로 돌려준다(HSBC 류).
    """
    pts = []
    for field in ("rev", "opi"):
        one = {k: (v[field], "") for k, v in raw.items() if field in v}
        for end, val in ss.quarterly(one).items():
            pts.append((end, field, val))
    by_end = {}
    for end, field, val in pts:
        by_end.setdefault(end, {})[field] = val
    out = []
    for end in sorted(by_end):
        if "rev" not in by_end[end]:
            continue
        # 분기 이름표는 build.py 가 붙인다(SEC 프레임·unstack 을 거친 규칙이라
        # 여기서 어림하는 것보다 옳다). 여기서는 종료일과 값만 남긴다.
        out.append({"end": f"{end[:4]}-{end[4:6]}-{end[6:]}",
                    "rev": by_end[end]["rev"], "opi": by_end[end].get("opi")})
    return out


def collect():
    old = load_old()
    done = set(old.get("done", []))
    seen = dict(old.get("seen", {}))
    codes, today = universe(old)
    print(f"  부문 없는 미국 종목 {len(codes):,} · 이번 실행 {min(PER_RUN, len(codes))}곳")
    cikmap = ss.cik_map()

    facts, pairs, by_cik = {}, {}, {}
    fin_raw = {}                 # 종목 -> {(종료일, 기간길이): ({rev,opi}, 접수일)}
    fpi_seen = (set(old.get("fpi") or [])
                | {k.split(":")[-1] for k in (old.get("stocks") or {})})
    walked = docs = 0
    for sym in codes[:PER_RUN]:
        cik = ss.cik_of(cikmap, sym)
        seen[sym] = today
        if not cik:
            continue
        walked += 1
        try:
            fl = xbrl_filings(cik)
        except ss.Blocked as e:
            print(f"    {sym} 목록 실패: {e}", file=sys.stderr, flush=True)
            continue
        if fl:
            fpi_seen.add(sym)          # XBRL 서류가 있는 외국 기업이다
        got = tried = 0
        for acc, filed, form in fl:
            if got >= DOCS_PER_CO or tried >= TRIES_PER_CO:
                break
            tried += 1
            if acc in done:
                continue
            try:
                url = instance_url(cik, acc)
            except ss.Blocked as e:
                print(f"    {sym} {acc} 폴더 실패: {e}", file=sys.stderr, flush=True)
                continue
            done.add(acc)
            if not url:
                continue                  # XBRL 표시는 있는데 인스턴스가 없다
            try:
                blob = get(url, timeout=120)
            except ss.Blocked as e:
                print(f"    {sym} {acc} 인스턴스 실패: {e}", file=sys.stderr, flush=True)
                continue
            if not blob:
                continue
            docs += 1
            # **깨진 서류 하나가 실행 전체를 죽이지 않게 한다.** 실제로 XML 이
            # 망가진 6-K 하나에 걸려 매 실행이 일곱 번째 회사에서 멈췄고,
            # 저장은 반복문 뒤에 있으므로 **그때까지 받은 것이 통째로 버려졌다.**
            # 다음 실행도 같은 자리에서 같은 서류를 다시 받아 또 죽었다 —
            # 대기줄이 영영 안 나아가는 덫이다.
            try:
                rows = parse_instance(blob)
            except ss.Blocked as e:
                print(f"    {sym} {acc} 못 읽음: {e}", file=sys.stderr, flush=True)
                continue
            if rows:
                got += 1
                absorb(facts, pairs, cik, filed, acc, rows)
                by_cik.setdefault(cik, []).append(sym)
            # 부문이 안 나온 서류에도 회사 전체 수치는 있다(표지뿐인 6-K 제외).
            for k, v in totals(blob).items():
                cur = fin_raw.setdefault(sym, {}).get(k)
                if cur is None or filed >= cur[1]:
                    fin_raw.setdefault(sym, {})[k] = (v, filed)
            time.sleep(PAUSE)
        if got:
            print(f"    {sym}: 서류 {got}장 · 부문 행 "
                  f"{len(facts.get(cik, {})) + len(pairs.get(cik, {}))}축", flush=True)
        # 서른 곳마다 써 둔다. 반복문 뒤에만 저장하면 도중에 죽을 때 다 잃는다.
        if walked % 30 == 0:
            old["fpi"] = sorted(fpi_seen)
            save(old, facts, pairs, by_cik, done, seen)
            save_fin(fin_raw)

    old["fpi"] = sorted(fpi_seen)
    made = save(old, facts, pairs, by_cik, done, seen)
    fin_made = save_fin(fin_raw)
    print(f"  {walked:,}곳을 보고 서류 {docs:,}장에서 {made:,}종목 부문을 얻었다"
          f" -> {OUT}")
    print(f"  실적 수치는 {fin_made:,}종목 -> {FIN_OUT}")


def save(old, facts, pairs, by_cik, done, seen):
    """벌크·색인과 같은 규칙으로 종목 기록을 만든다.

    분기 수 문턱은 색인 쪽(3분기)과 같이 낮춘다 — 여기 기록도 다른 소스 뒤에
    이어 붙이거나 없는 자리를 메우는 몫이라, 서류 석 장에서 네 분기가 안 나온다고
    버리면 정작 메우려던 회사를 못 메운다.
    """
    keep = (ss.MIN_PTS, ss.MIN_MEMBER_PTS)
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = 3, 2
    made = 0
    for cik in set(facts) | set(pairs):
        rec = ss.build_stock(facts.get(cik, {}), pairs.get(cik, {}))
        if not rec:
            continue
        rec = ss.dedupe_names(rec)
        rec["v"] = FPI_VER
        for sym in set(by_cik.get(cik, [])):
            old["stocks"][sym] = rec
            made += 1
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = keep
    old["v"] = FPI_VER
    old["pv"] = PARSE_VER
    old["source"] = "SEC EDGAR — 외국 기업(FPI)의 20-F·6-K 인라인 XBRL"
    old["note"] = ("미국 국내 기업만 10-Q 를 낸다. 외국 기업은 20-F(연 1회)와 "
                   "6-K(수시)라 벌크·분기색인이 못 잡는다. 판단 규칙은 "
                   "scrape_seg_sec.py 와 한 벌을 쓴다.")
    old["done"] = sorted(done)[-40000:]
    old["seen"] = seen
    old["count"] = len(old["stocks"])
    old["ts"] = date.today().isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return made


def save_fin(fin_raw):
    """전사 수치를 financials_fpi.json 에 담는다. 열쇠는 `us:티커`.

    **덮어쓰지 않고 합친다.** 한 실행이 훑는 것은 시총 순 일부라, 통째로 쓰면
    지난 실행에서 얻은 종목이 사라진다.
    """
    old = {}
    if FIN_OUT.exists():
        try:
            old = json.loads(FIN_OUT.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            old = {}
    stocks = old.get("stocks") or {}
    made = 0
    for sym, raw in fin_raw.items():
        pts = fin_points({k: v[0] for k, v in raw.items()})
        if len(pts) < 2:
            continue
        stocks["us:" + sym] = {"v": 1, "freq": "Q", "cur": "USD", "src": "6-K",
                               "points": pts[-20:],
                               "ts": date.today().isoformat()}
        made += 1
    payload = {
        "source": "SEC EDGAR — 외국 기업(FPI)의 20-F·6-K 인라인 XBRL",
        "note": ("외국 기업은 10-Q 를 안 내 SEC 분기 수치가 없고 stockanalysis 는 "
                 "며칠 늦다. 부문을 뽑는 그 인스턴스에서 전사 매출·영업이익을 "
                 "같이 담는다 — 요청이 늘지 않는다."),
        "count": len(stocks), "stocks": stocks,
        "ts": date.today().isoformat(),
    }
    tmp = FIN_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FIN_OUT)
    return made


def probe(syms):
    """종목 몇 개만 떠서 서류·부문이 잡히는지 눈으로 본다."""
    cikmap = ss.cik_map()
    for sym in syms:
        cik = ss.cik_of(cikmap, sym)
        print(f"\n== {sym} · CIK {cik}")
        if not cik:
            continue
        fl = xbrl_filings(cik)
        print(f"   XBRL 붙은 20-F·6-K {len(fl)}건:", [(a[1], a[2]) for a in fl[:5]])
        facts, pairs = {}, {}
        for acc, filed, form in fl[:DOCS_PER_CO]:
            url = instance_url(cik, acc)
            print(f"   {filed} {form} {acc} -> {(url or '인스턴스 없음').split('/')[-1][:44]}")
            if not url:
                continue
            blob = get(url, timeout=120)
            rows = parse_instance(blob) if blob else []
            print(f"      부문 행 {len(rows)}건", rows[:2])
            if rows:
                absorb(facts, pairs, cik, filed, acc, rows)
            time.sleep(PAUSE)
        keep = (ss.MIN_PTS, ss.MIN_MEMBER_PTS)
        ss.MIN_PTS, ss.MIN_MEMBER_PTS = 3, 2
        rec = ss.build_stock(facts.get(cik, {}), pairs.get(cik, {}))
        ss.MIN_PTS, ss.MIN_MEMBER_PTS = keep
        if rec:
            rec = ss.dedupe_names(rec)
            print("   -> 부문:", rec.get("names"), "· 점", len(rec.get("pts") or []))
            for p in (rec.get("pts") or [])[-3:]:
                print("      ", p)
        else:
            print("   -> 기록 못 만듦")


def main():
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        syms = [a for a in sys.argv[i + 1:] if not a.startswith("-")]
        return probe(syms or ["AS", "ARM", "TSM"])
    collect()


if __name__ == "__main__":
    main()
