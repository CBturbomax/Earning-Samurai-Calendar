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

import scrape_seg_sec as ss
from scrape_seg_edgar import (absorb, get, instance_url, parse_instance)

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_fpi.json"

FPI_VER = 1
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
TRIES_PER_CO = 7
PAUSE = 0.25
# 다 따라잡은 회사는 이만큼 지나야 다시 본다(6-K 는 분기마다 나온다).
REFRESH_DAYS = 20


def load_old():
    if OUT.exists():
        try:
            d = json.loads(OUT.read_text(encoding="utf-8"))
            if d.get("v") == FPI_VER:
                return d
        except (ValueError, OSError):
            pass
    return {"v": FPI_VER, "stocks": {}, "done": [], "seen": {}}


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


def universe(old):
    """시총 큰 순으로, 부문이 없는 미국 종목만. 최근에 본 회사는 뒤로 민다."""
    caps = ss.want_tickers()
    have = covered()
    seen = old.get("seen", {})
    today = date.today().isoformat()
    cut = (date.fromordinal(date.today().toordinal() - REFRESH_DAYS)).isoformat()
    todo = [(c, cap) for c, cap in caps.items()
            if c not in have and (seen.get(c, "") < cut)]
    todo.sort(key=lambda kv: -kv[1])
    return [c for c, _ in todo], today


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


def collect():
    old = load_old()
    done = set(old.get("done", []))
    seen = dict(old.get("seen", {}))
    codes, today = universe(old)
    print(f"  부문 없는 미국 종목 {len(codes):,} · 이번 실행 {min(PER_RUN, len(codes))}곳")
    cikmap = ss.cik_map()

    facts, pairs, by_cik = {}, {}, {}
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
            rows = parse_instance(blob)
            if rows:
                got += 1
                absorb(facts, pairs, cik, filed, acc, rows)
                by_cik.setdefault(cik, []).append(sym)
            time.sleep(PAUSE)
        if got:
            print(f"    {sym}: 서류 {got}장 · 부문 행 "
                  f"{len(facts.get(cik, {})) + len(pairs.get(cik, {}))}축", flush=True)

    made = save(old, facts, pairs, by_cik, done, seen)
    print(f"  {walked:,}곳을 보고 서류 {docs:,}장에서 {made:,}종목 부문을 얻었다"
          f" -> {OUT}")


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
