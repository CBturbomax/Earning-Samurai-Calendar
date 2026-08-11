# -*- coding: utf-8 -*-
"""
미국 실적 '수치' 수집 — 출처: SEC XBRL (재무 시계열) + Nasdaq (발표 완료·서프라이즈)

지금까지는 '언제 발표하는가'만 다뤘다. 여기서는 '무엇을 발표했는가'를 받는다.

두 소스를 쓰는 이유가 각각 있다.

  SEC XBRL  — 회사가 제출한 공식 재무제표. 분기 매출·영업이익을 몇 년치 쭉 준다.
              다만 **미국 국내 기업(10-Q 제출)만** 분기가 있다. SEA·알리바바 같은
              외국 기업은 20-F(연 1회)만 내므로 분기가 아예 없다. 지어내지 않고
              연간만 담고, 화면에 '연간만 있음'이라고 적는다.

  Nasdaq    — 발표 완료 여부와 EPS 서프라이즈. 예정만 있으면 지났는지 알 수 없는데,
              여기서 '실제 EPS 가 찍혔는가'로 판단할 수 있다. 외국 기업도 나온다.

종목이 4천 개라 전부 받으면 오래 걸린다. 시가총액 상위부터 받고, 다음 실행 때
이어서 채운다. 이미 받은 종목은 건너뛴다.

결과: data/financials.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials.json"

# SEC 는 연락처가 담긴 User-Agent 를 요구한다(그쪽 이용약관).
SEC_UA = os.environ.get("SEC_UA", "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)")
NAS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TICKERS = "https://www.sec.gov/files/company_tickers.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"
NAS_EPS = "https://api.nasdaq.com/api/quote/{sym}/eps"

# 매출 태그는 회사마다 다르다. 먼저 걸리는 것을 쓴다.
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet"]
OPI_TAG = "OperatingIncomeLoss"
NI_TAG = "NetIncomeLoss"

PER_RUN = int(os.environ.get("FIN_PER_RUN", "150"))   # 한 번에 새로 받을 종목 수
SINCE = "2018-10-01"                                  # 1Q19 부터 보려면 조금 앞부터


def get(url, ua, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def quarters(units):
    """companyconcept 응답에서 분기 값만 골라낸다.

    같은 분기가 10-Q 와 나중의 10-K 에 중복해 실린다. 나중 제출본이 정정된 값이므로
    (시작,끝) 이 같으면 **뒤에 나온 것으로 덮는다**. 기간 길이로 분기/연간을 가른다 —
    80~100일이면 분기, 350~380일이면 연간이다.
    """
    q, a = {}, {}
    for x in units:
        s, e, v = x.get("start"), x.get("end"), x.get("val")
        if not s or not e or v is None or e < SINCE:
            continue
        days = (date.fromisoformat(e) - date.fromisoformat(s)).days
        if 80 <= days <= 100:
            q[(s, e)] = v
        elif 350 <= days <= 380:
            a[(s, e)] = v
    return q, a


def label_of(end):
    """분기말 날짜 -> '2Q26' 처럼 역년 기준 이름. 회원님이 보는 표기법이다."""
    d = date.fromisoformat(end)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def series(cik):
    """한 종목의 분기 매출·영업이익·순이익 시계열."""
    out = {}
    rev_q = rev_a = None
    for tag in REV_TAGS:
        try:
            d = get(CONCEPT.format(cik=cik, tag=tag), SEC_UA)
        except Exception:
            continue
        q, a = quarters(d.get("units", {}).get("USD", []))
        if q or a:
            rev_q, rev_a = q, a
            break
        time.sleep(0.12)
    if rev_q is None and rev_a is None:
        return None

    def one(tag):
        try:
            d = get(CONCEPT.format(cik=cik, tag=tag), SEC_UA)
        except Exception:
            return {}, {}
        return quarters(d.get("units", {}).get("USD", []))

    time.sleep(0.12)
    opi_q, opi_a = one(OPI_TAG)
    time.sleep(0.12)
    ni_q, ni_a = one(NI_TAG)

    # 분기가 있으면 분기로, 없으면(외국 기업) 연간으로 낸다.
    use_q = bool(rev_q)
    rev, opi, ni = (rev_q, opi_q, ni_q) if use_q else (rev_a, opi_a, ni_a)
    keys = sorted(rev, key=lambda k: k[1])
    if not keys:
        return None
    out["freq"] = "Q" if use_q else "A"
    out["points"] = [{
        "label": label_of(e) if use_q else str(date.fromisoformat(e).year),
        "end": e,
        "rev": rev[(s, e)],
        "opi": opi.get((s, e)),
        "ni": ni.get((s, e)),
    } for (s, e) in keys]
    return out


def reported(sym):
    """발표 완료 여부와 최근 EPS. UpcomingQuarter 는 아직 안 나온 분기다."""
    try:
        d = get(NAS_EPS.format(sym=sym), NAS_UA)
    except Exception:
        return None
    eps = (d.get("data") or {}).get("earningsPerShare") or []
    done, upcoming = [], None
    for e in eps:
        row = {"period": e.get("period"), "consensus": e.get("consensus"),
               "actual": e.get("earnings")}
        if e.get("type") == "UpcomingQuarter":
            upcoming = row
        elif row["actual"]:
            done.append(row)
    return {"done": done[-8:], "upcoming": upcoming} if (done or upcoming) else None


def targets():
    """시가총액 상위부터. 종목이 많아 한 번에 다 못 받는다."""
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return []
    d = json.loads(p.read_text(encoding="utf-8"))
    best = {}
    for r in d.get("rows", []):
        c = r.get("code")
        if c:
            best[c] = max(best.get(c, 0), r.get("cap") or 0)
    return [c for c, _ in sorted(best.items(), key=lambda kv: -kv[1])]


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    print("  티커→CIK 목록 받는 중")
    cikmap = {}
    for v in get(TICKERS, SEC_UA).values():
        cikmap[v["ticker"].upper()] = v["cik_str"]
    print(f"  {len(cikmap):,}종목")

    todo = [s for s in targets() if s not in old][:PER_RUN]
    print(f"  이번에 받을 종목 {len(todo)}개 (이미 받은 것 {len(old)}개)")

    stocks = dict(old)
    got = 0
    for i, sym in enumerate(todo):
        rec = {}
        cik = cikmap.get(re.sub(r"[^A-Z.]", "", sym.upper()))
        if cik:
            try:
                s = series(cik)
                if s:
                    rec.update(s)
            except Exception as e:
                print(f"    {sym} 재무 실패: {e}", file=sys.stderr)
        r = reported(sym)
        if r:
            rec["eps"] = r
        if rec:
            stocks[sym] = rec
            got += 1
        if i % 25 == 24:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(0.5)

    payload = {
        "source": "SEC XBRL (재무 시계열) + Nasdaq (발표 완료·EPS)",
        "note": ("미국 국내 기업만 분기가 있다. SEA·알리바바 같은 외국 기업은 "
                 "SEC 에 20-F(연 1회)만 내므로 연간만 담긴다."),
        "count": len(stocks),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    nq = sum(1 for v in stocks.values() if v.get("freq") == "Q")
    na = sum(1 for v in stocks.values() if v.get("freq") == "A")
    ne = sum(1 for v in stocks.values() if v.get("eps"))
    print(f"\n{len(stocks):,}종목 -> {OUT}")
    print(f"  분기 시계열 {nq:,} · 연간만 {na:,} · EPS(발표완료) {ne:,}")


if __name__ == "__main__":
    main()
