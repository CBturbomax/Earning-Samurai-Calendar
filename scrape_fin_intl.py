# -*- coding: utf-8 -*-
"""
일본·홍콩 실적 '수치' 수집 — 출처: Yahoo Finance fundamentals-timeseries

미국은 SEC 가 공식 재무제표를 그대로 준다(scrape_fin.py). 일본·홍콩에는 그런 게 없다.

  일본  EDINET 은 2024년부터 신청키를 요구한다. 회원님한테 가입을 시킬 일이 아니다.
  홍콩  HKEXnews 는 공시를 PDF 로 올린다. 기계가 읽을 수 있는 형태가 아니다.

야후는 두 시장 모두 분기(또는 반기) 매출·영업이익·순이익을 준다. 공식 원본은
아니지만 **회사가 낸 숫자를 옮겨 적은 것**이고, "매출이 늘고 있나 줄고 있나"를
그림으로 보기에는 맞다. 출처는 화면에 그대로 적는다 — SEC 인 척하지 않는다.

**깊이가 짧다.** 공짜로 열린 야후는 최근 **연간 4개 / 분기 5개**까지만 준다.
period1 을 2018년으로 줘도 더 안 준다(확인함: 텐센트 1개, 소니 5개, 청쿵 4개).
미국은 SEC 가 1Q19 부터 다 주는데 일본·홍콩은 그런 게 없다. 짧은 걸 길게 보이려고
분기를 쪼개거나 채워 넣지 않는다 — 몇 개인지 화면에 그대로 적는다.

**홍콩은 반기다.** 홍콩 상장사 다수가 1년에 두 번만 낸다. 없는 분기를 쪼개
지어내지 않고 반기 막대로 낸다. 야후가 기간 종류(3M/6M/12M)를 같이 주므로
그걸 그대로 믿는다.

**통화가 시장마다 다르다.** 도요타는 엔, 텐센트는 위안(홍콩 상장이지만 보고는
위안으로 한다). 달러로 환산하지 않고 **원래 통화 그대로** 담고 화면에 통화를 적는다 —
몇 년치 시계열을 그때그때 환율로 환산하면 매출 추세가 아니라 환율 추세가 된다.

결과: data/financials_intl.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials_intl.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com"
TS = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"

# 한 번 요청으로 여섯 계열을 다 받는다. 분기가 없는 회사도 있어서 연간까지 같이 부른다.
TYPES = ["quarterlyTotalRevenue", "quarterlyOperatingIncome", "quarterlyNetIncome",
         "annualTotalRevenue", "annualOperatingIncome", "annualNetIncome"]
P1 = 1538352000        # 2018-10-01. 1Q19 부터 보려면 조금 앞부터.
P2 = 2000000000        # 2033년. 넉넉히.

PER_RUN = int(os.environ.get("INTL_PER_RUN", "400"))    # 한 번에 받을 종목 수
STALE_DAYS = int(os.environ.get("INTL_STALE_DAYS", "14"))
INTL_VER = 1

BACKOFF = (0, 10, 30, 90)
GIVE_UP_AFTER = 8      # 연속 실패가 이어지면 접는다. 야후가 막은 것이다.

_opener = None
_crumb = ""


class Throttled(Exception):
    pass


def bootstrap():
    """쿠키를 받고 crumb 을 얻는다. 이게 없으면 야후는 401 을 준다.
    (scrape_caps.py 와 같은 절차다. 두 파일이 따로 돌 수 있게 각자 갖고 있다.)"""
    global _opener, _crumb
    _opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    _opener.addheaders = [("User-Agent", UA), ("Accept", "*/*")]
    try:
        _opener.open(COOKIE_URL, timeout=30).read()
    except Exception:
        pass
    with _opener.open(CRUMB_URL, timeout=30) as r:
        _crumb = r.read().decode("utf-8", "replace").strip()
    if not _crumb or len(_crumb) > 40 or "<" in _crumb:
        raise Throttled(f"crumb 을 못 받았다: {_crumb[:60]!r}")
    print(f"  crumb 확보 ({len(_crumb)}자)")


def fetch(sym):
    """한 종목치 재무 시계열. 401 이면 crumb 이 죽은 것이라 다시 받아 재시도한다."""
    def url():
        return TS.format(sym=urllib.parse.quote(sym)) + "?" + urllib.parse.urlencode(
            {"symbol": sym, "type": ",".join(TYPES),
             "period1": P1, "period2": P2, "crumb": _crumb})
    u = url()
    for wait in BACKOFF:
        if wait:
            time.sleep(wait)
        try:
            with _opener.open(u, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                try:
                    bootstrap()
                    u = url()
                except Exception as e2:
                    print(f"    crumb 재발급 실패 ({e2})", file=sys.stderr, flush=True)
            elif e.code == 404:
                return None            # 야후에 없는 종목. 재시도해도 없다.
            else:
                print(f"    {sym} HTTP {e.code}", file=sys.stderr, flush=True)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            print(f"    {sym} 응답 이상 ({e})", file=sys.stderr, flush=True)
    raise Throttled(f"{sym}: 유효 응답 실패")


# 야후가 알려주는 기간 종류. 3M=분기, 6M=반기, 12M=연간.
# 기간 길이를 날짜로 재서 짐작하지 않는다 — 야후가 이미 적어 주는 걸 믿는다.
SPAN = {"3M": "Q", "6M": "H", "12M": "A"}


def pull(res, kind):
    """응답에서 한 계열을 뽑아 {종료일: (값, 기간종류, 통화)} 로."""
    out = {}
    for block in res:
        meta = block.get("meta") or {}
        types = meta.get("type") or []
        if kind not in types:
            continue
        for x in block.get(kind) or []:
            if not x:
                continue
            end = x.get("asOfDate")
            span = SPAN.get(x.get("periodType") or "")
            val = (x.get("reportedValue") or {}).get("raw")
            if end and span and isinstance(val, (int, float)):
                out[end] = (float(val), span, x.get("currencyCode") or "")
    return out


def label_of(end, span):
    """'2019-03-31' -> '1Q19' / '1H19' / '2019'. 회원님이 보는 표기법이다."""
    d = date.fromisoformat(end)
    y = d.year % 100
    if span == "Q":
        return f"{(d.month - 1) // 3 + 1}Q{y:02d}"
    if span == "H":
        return f"{1 if d.month <= 6 else 2}H{y:02d}"
    return str(d.year)


def series(sym):
    """한 종목의 시계열. 분기가 있으면 분기, 없으면 반기, 그것도 없으면 연간."""
    body = fetch(sym)
    if not body:
        return None
    res = (body.get("timeseries") or {}).get("result")
    if res is None:
        raise Throttled(f"{sym}: timeseries 봉투가 없다: {str(body)[:200]}")

    rev = pull(res, "quarterlyTotalRevenue")
    opi = pull(res, "quarterlyOperatingIncome")
    ni = pull(res, "quarterlyNetIncome")
    # 야후는 반기 보고 회사의 6M 값도 'quarterly' 통에 담아 준다. 그래도 분기가
    # 하나도 없는 회사가 있어서 연간까지 받아 뒀다.
    if not rev:
        rev = pull(res, "annualTotalRevenue")
        opi = pull(res, "annualOperatingIncome")
        ni = pull(res, "annualNetIncome")
    if not rev:
        return None

    # 한 회사 안에 3M 과 6M 이 섞여 오는 수가 있다(반기 보고 회사인데 마지막 한
    # 분기만 3M 으로 오는 식). 섞어서 그리면 반기 막대가 분기의 두 배로 솟아
    # 추세를 왜곡한다. 많은 쪽만 남긴다.
    spans = {}
    for _, (_, sp, _c) in rev.items():
        spans[sp] = spans.get(sp, 0) + 1
    keep = max(spans, key=lambda k: (spans[k], k == "Q"))
    ends = sorted(e for e, (_v, sp, _c) in rev.items() if sp == keep)
    if not ends:
        return None
    cur = next((c for _e, (_v, _s, c) in sorted(rev.items()) if c), "")

    return {
        "v": INTL_VER,
        "freq": keep,
        "cur": cur,
        "src": "yahoo",
        "points": [{
            "label": label_of(e, keep),
            "end": e,
            "rev": rev[e][0],
            "opi": opi[e][0] if e in opi else None,
            "ni": ni[e][0] if e in ni else None,
        } for e in ends],
    }


def yahoo_symbol(market, code):
    """야후 표기. 일본은 7203.T, 홍콩은 0700.HK (앞의 0을 4자리로 맞춘다)."""
    if market == "jp":
        return f"{code}.T"
    digits = re.sub(r"\D", "", code)
    return f"{int(digits):04d}.HK" if digits else ""


def targets():
    """{시장:코드: 시총} — 시총 큰 순으로 채우려고 같이 본다."""
    caps = {}
    p = HERE / "data" / "caps.json"
    if p.exists():
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, OSError):
            pass
    out = {}
    for market, fn in (("jp", "earnings.json"), ("hk", "earnings_hk.json")):
        f = HERE / "data" / fn
        if not f.exists():
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for r in d.get("rows", []):
            c = r.get("code")
            if c:
                k = f"{market}:{c}"
                out[k] = caps.get(k) or 0
    return out


def queue(old, cand):
    """받을 순서. 아직 못 받은 것 먼저, 그 다음 오래된 것. 둘 다 시총 큰 순."""
    stale = (date.fromordinal(date.today().toordinal() - STALE_DAYS)).isoformat()
    cold = (date.fromordinal(date.today().toordinal() - STALE_DAYS * 4)).isoformat()
    picks = []
    for k, cap in cand.items():
        rec = old.get(k)
        if not rec or rec.get("v") != INTL_VER:
            pri = 0
        else:
            ts = rec.get("ts") or ""
            if not rec.get("points"):
                if ts >= cold:
                    continue          # 야후에도 없는 종목. 자주 두드리지 않는다.
                pri = 2
            elif ts < stale:
                pri = 1
            else:
                continue              # 아직 싱싱하다.
        picks.append((pri, -cap, k))
    picks.sort()
    return [k for _, _, k in picks]


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    bootstrap()

    if probe:
        # 응답 생김새를 눈으로 본다. 시장마다 하나씩.
        for sym in ("7203.T", "0700.HK", "0001.HK", "6758.T"):
            print(f"\n===== {sym} =====")
            try:
                body = fetch(sym)
            except Throttled as e:
                print("  실패:", e)
                continue
            res = (body or {}).get("timeseries", {}).get("result") or []
            print(f"  블록 {len(res)}개:",
                  [(b.get("meta") or {}).get("type") for b in res][:8])
            for b in res[:2]:
                t = ((b.get("meta") or {}).get("type") or [""])[0]
                rows = b.get(t) or []
                print(f"  {t}: {len(rows)}건")
                for x in rows[:3] + rows[-2:]:
                    if x:
                        print(f"    {x.get('asOfDate')} {x.get('periodType')} "
                              f"{x.get('currencyCode')} "
                              f"{(x.get('reportedValue') or {}).get('fmt')}")
            try:
                s = series(sym)
            except Throttled as e:
                print("  series 실패:", e); continue
            if s:
                p = s["points"]
                print(f"  -> freq={s['freq']} cur={s['cur']} {len(p)}개 "
                      f"{p[0]['label']}~{p[-1]['label']}")
            else:
                print("  -> 시계열 없음")
        return

    cand = targets()
    pending = queue(old, cand)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개 "
          f"(가진 것 {len(old):,}개 / 후보 {len(cand):,}개)")

    today = date.today().isoformat()
    stocks = dict(old)
    got, miss = 0, 0
    for i, k in enumerate(todo):
        market, code = k.split(":", 1)
        sym = yahoo_symbol(market, code)
        rec = None
        if sym:
            try:
                rec = series(sym)
            except Throttled as e:
                miss += 1
                print(f"    {k} 실패: {e}", file=sys.stderr, flush=True)
                if miss >= GIVE_UP_AFTER:
                    # 연속 실패가 이어지면 야후가 막은 것이다. 붙들고 있지 않는다.
                    print(f"  연속 {miss}회 실패 — 이번 실행은 여기서 접는다.",
                          file=sys.stderr, flush=True)
                    break
                continue
        if rec:
            miss = 0
            got += 1
        else:
            miss = 0
            rec = {}                  # 야후에 없는 종목. '두드려 봤다'만 남긴다.
        rec["v"] = INTL_VER
        rec["ts"] = today
        rec["sym"] = sym
        stocks[k] = rec
        if i % 50 == 49:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(0.35)

    have = {k: v for k, v in stocks.items() if v.get("points")}
    payload = {
        "source": "Yahoo Finance fundamentals-timeseries",
        "note": ("공식 원본이 아니라 야후가 옮겨 적은 값이다. 최근 연간 4개 / "
                 "분기 5개까지만 온다 — 미국(SEC)처럼 1Q19 까지 거슬러 가지 않는다. "
                 "홍콩은 반기 보고가 많아 반기 막대로 낸다. "
                 "통화는 회사가 보고한 통화 그대로다."),
        "count": len(have),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    def cnt(m, f):
        return sum(1 for k, v in have.items() if k.startswith(m) and v.get("freq") == f)
    print(f"\n{len(have):,}종목 -> {OUT} (야후에 없는 종목 {len(stocks)-len(have):,} 표시만)")
    print(f"  일본  분기 {cnt('jp:', 'Q'):,} · 반기 {cnt('jp:', 'H'):,} · 연간 {cnt('jp:', 'A'):,}")
    print(f"  홍콩  분기 {cnt('hk:', 'Q'):,} · 반기 {cnt('hk:', 'H'):,} · 연간 {cnt('hk:', 'A'):,}")
    for k in ("jp:7203", "jp:6758", "hk:00700", "hk:09988"):
        v = have.get(k)
        if v:
            p = v["points"]
            print(f"  {k}: {v['freq']} {v['cur']} {len(p)}개 "
                  f"{p[0]['label']}~{p[-1]['label']}")


if __name__ == "__main__":
    main()
