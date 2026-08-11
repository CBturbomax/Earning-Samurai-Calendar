# -*- coding: utf-8 -*-
"""
일본·홍콩 시가총액 수집 — 출처: Yahoo Finance quote API

나스닥은 미국 시총을 같이 주지만 닛케이·HKEXnews 는 안 준다. 시총이 없으면
규모 필터가 반쪽이 된다 — 하루 800건씩 쏟아지는 캘린더에서 큰 회사만 골라볼 수가 없다.

야후는 한 번에 수십 종목을 묶어 물어볼 수 있어서, 종목이 수천 개여도 요청 수십 번이면 끝난다.
다만 그냥 부르면 401 이고 쿠키와 crumb 을 먼저 받아야 한다.

시총은 **현지 통화**로 온다(도요타는 엔, 텐센트는 홍콩달러). 미국 시총과 나란히
비교하려면 달러로 맞춰야 하므로 환율도 같이 받아 환산한다. 환율을 못 받으면
그 통화 종목은 통째로 버린다 — 엔 금액을 달러로 착각해 담으면 시총이 150배로
부풀어 필터가 거꾸로 동작한다.

결과: data/caps.json   {"jp:7203": 250.5, "hk:00700": 480.2}  (십억 달러)
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HERE = Path(__file__).parent
OUT = HERE / "data" / "caps.json"

QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com"

BATCH = 50            # 한 번에 물어볼 종목 수
BACKOFF = (0, 10, 30, 90)
GIVE_UP_AFTER = 3     # 연속 실패가 이어지면 접는다

_opener = None
_crumb = ""


class Throttled(Exception):
    pass


def bootstrap():
    """쿠키를 받고 crumb 을 얻는다. 이게 없으면 야후는 401 을 준다."""
    global _opener, _crumb
    _opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()))
    _opener.addheaders = [("User-Agent", UA), ("Accept", "*/*")]
    try:
        _opener.open(COOKIE_URL, timeout=30).read()
    except Exception:
        pass                                   # 쿠키만 심으면 되고 본문은 필요 없다
    with _opener.open(CRUMB_URL, timeout=30) as r:
        _crumb = r.read().decode("utf-8", "replace").strip()
    if not _crumb or len(_crumb) > 40 or "<" in _crumb:
        raise Throttled(f"crumb 을 못 받았다: {_crumb[:60]!r}")
    print(f"  crumb 확보 ({len(_crumb)}자)")


def fetch(symbols):
    """한 묶음치 시세를 받는다. 401 이면 crumb 이 죽은 것이라 다시 받아 재시도한다."""
    url = QUOTE + "?" + urllib.parse.urlencode(
        {"symbols": ",".join(symbols), "crumb": _crumb})
    for wait in BACKOFF:
        if wait:
            print(f"    {wait}s 대기 후 재시도", file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            with _opener.open(url, timeout=40) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print("    crumb 만료 — 다시 받는다", file=sys.stderr, flush=True)
                try:
                    bootstrap()
                    url = QUOTE + "?" + urllib.parse.urlencode(
                        {"symbols": ",".join(symbols), "crumb": _crumb})
                except Exception as e2:
                    print(f"    재발급 실패 ({e2})", file=sys.stderr, flush=True)
            else:
                print(f"    HTTP {e.code}", file=sys.stderr, flush=True)
            continue
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            print(f"    응답 이상 ({e})", file=sys.stderr, flush=True)
            continue

        res = (body.get("quoteResponse") or {}).get("result")
        if res is None:
            print(f"    quoteResponse 없음: {str(body)[:150]}", file=sys.stderr, flush=True)
            continue
        return res
    raise Throttled(f"{symbols[0]} 외 {len(symbols)-1}종목: 유효 응답 실패")


def fx_rates(currencies):
    """통화별 '1달러 = ?' 환율. USD 는 1."""
    want = sorted(c for c in currencies if c and c != "USD")
    if not want:
        return {"USD": 1.0}
    rows = fetch([c + "=X" for c in want])
    out = {"USD": 1.0}
    for r in rows:
        sym = (r.get("symbol") or "").replace("=X", "")
        px = r.get("regularMarketPrice")
        if sym and isinstance(px, (int, float)) and px > 0:
            out[sym] = float(px)
    missing = [c for c in want if c not in out]
    if missing:
        # 환율을 모르는 통화는 아예 버린다. 엔 금액을 달러로 담으면
        # 시총이 150배로 부풀어 필터가 거꾸로 돈다.
        print(f"  ! 환율을 못 받은 통화: {missing} — 이 통화 종목은 버린다", file=sys.stderr)
    print("  환율:", {k: round(v, 2) for k, v in out.items()})
    return out


def symbols_for(market, codes):
    """야후 표기로 바꾼다. 일본은 7203.T, 홍콩은 0700.HK (앞의 0을 4자리로 맞춘다)."""
    out = {}
    for c in codes:
        if market == "jp":
            out[f"{c}.T"] = c
        else:
            digits = re.sub(r"\D", "", c)
            if not digits:
                continue
            out[f"{int(digits):04d}.HK"] = c
    return out


def load_codes(market, filename):
    p = HERE / "data" / filename
    if not p.exists():
        print(f"  {filename} 없음 — 건너뛴다")
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    codes = sorted({r["code"] for r in d.get("rows", []) if r.get("code")})
    print(f"  {market}: 종목 {len(codes):,}개")
    return symbols_for(market, codes)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bootstrap()

    todo = {}                                  # 야후심볼 -> (시장, 원래코드)
    for mkt, fn in [("jp", "earnings.json"), ("hk", "earnings_hk.json")]:
        for sym, code in load_codes(mkt, fn).items():
            todo[sym] = (mkt, code)
    if not todo:
        print("받을 종목이 없다.")
        return

    syms = sorted(todo)
    raw, streak, failed = [], 0, 0
    for i in range(0, len(syms), BATCH):
        chunk = syms[i:i + BATCH]
        try:
            rows = fetch(chunk)
        except Throttled as e:
            failed += len(chunk)
            streak += 1
            print(f"  실패: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("연속 실패 — 여기서 멈춘다. 받은 만큼만 저장한다.",
                      file=sys.stderr, flush=True)
                break
        else:
            streak = 0
            raw += rows
            print(f"  {i+len(chunk):>5}/{len(syms)}  누적 {len(raw):,}건", flush=True)
        time.sleep(1.0)

    rates = fx_rates({r.get("currency") for r in raw})

    caps, skipped = {}, 0
    for r in raw:
        sym = r.get("symbol") or ""
        if sym not in todo:
            continue
        mkt, code = todo[sym]
        cap, cur = r.get("marketCap"), r.get("currency")
        if not isinstance(cap, (int, float)) or cap <= 0 or cur not in rates:
            skipped += 1
            continue
        caps[f"{mkt}:{code}"] = round(cap / rates[cur] / 1e9, 3)

    payload = {
        "source": "Yahoo Finance quote API",
        "note": "십억 달러 단위. 현지 통화 시총을 당일 환율로 환산한 값이라 어림값이다.",
        "rates": {k: round(v, 4) for k, v in rates.items()},
        "count": len(caps),
        "caps": caps,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    print(f"\n시총 {len(caps):,}종목 -> {OUT}")
    if skipped:
        print(f"  시총·통화를 못 읽어 건너뛴 종목 {skipped:,}개")
    if failed:
        print(f"  요청 실패로 못 받은 종목 {failed:,}개 (다시 돌리면 채워진다)")
    big = sorted(caps.items(), key=lambda kv: -kv[1])[:5]
    print("  상위:", [(k, f"{v:,.0f}B") for k, v in big])


if __name__ == "__main__":
    main()
