# -*- coding: utf-8 -*-
"""주가 요약 — 스크리너용 신고가·수익률(회원님 11/03: "신고가 애들, 3달·52주·역사적, RS 강도 센 애들").

야후 차트 API 를 종목당 두 번(2년 주봉 + 상장 이래 3개월봉). 주봉 고가로 13주·52주·상장 이래 고가를 보고, 종가로 1·3·6·12개월 수익률을 낸다.
전체 이력은 싣지 않는다 — 종목마다 숫자 몇 개만. RS(상대강도) 순위는 빌드가 전 종목을 모아 매긴다.

data/px_s{i}.json  {"px": {열쇠: [기준일, 종가, r1, r3, r6, r12, d13, d52, dall]}}   (r = 수익률 %, d = 그 고가에서 몇 % 아래, 0 이면 신고가)
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
SHARD = os.environ.get("PX_SHARD", "")
OUT = HERE / "data" / (f"px_s{SHARD.split('/')[0]}.json" if SHARD else "px.json")
SECS = int(os.environ.get("PX_SECS", "1200"))
SLEEP = float(os.environ.get("PX_SLEEP", "0.25"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}


def sym(k):
    mkt, _, code = k.partition(":")
    return {"us": code.replace(".", "-"), "jp": code + ".T", "hk": code.lstrip("0").zfill(4) + ".HK"}.get(mkt)


def bars(j):
    r = ((j.get("chart") or {}).get("result") or [{}])[0]
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    ts, cl, hi = r.get("timestamp") or [], q.get("close") or [], q.get("high") or []
    return [(t, c, h if h is not None else c) for t, c, h in zip(ts, cl, hi) if c is not None]


def summary(j, jmax=None):
    """j = 2년 주봉(수익률·13주·52주), jmax = 상장 이래 3개월봉(역사적 고가만). range=max 에 주봉을 달라고 하면 야후가
    3개월봉을 준다 — 그걸 주봉으로 읽어 마이크로소프트 1년 수익률이 +1,224% 로 나왔다(11/03). 그래서 둘로 나눠 받는다."""
    rows = bars(j)
    if len(rows) < 14 or (rows[-1][0] - rows[-14][0]) > 13 * 8 * 86400:   # 13주가 석 달 남짓이어야 주봉이다
        return None
    last_t, last = rows[-1][0], rows[-1][1]

    def ret(weeks):
        return round((last / rows[-1 - weeks][1] - 1) * 100, 1) if len(rows) > weeks and rows[-1 - weeks][1] else None

    def dist(weeks):
        hs = [h for _, _, h in rows[-weeks:]] if weeks else [h for _, _, h in (bars(jmax) if jmax else rows)] + [rows[-1][2]]
        top = max(hs)
        if hs[-1] >= top:
            return 0                                         # 이번 주 고가가 그 기간 최고 — 신고가
        return round((1 - last / top) * 100, 1) if top else None
    return [datetime.fromtimestamp(last_t, timezone.utc).date().isoformat(), round(last, 4),
            ret(4), ret(13), ret(26), ret(52), dist(13), dist(52), dist(0)]


def universe():
    h = (HERE / "index.html").read_text(encoding="utf-8")
    D = json.loads(re.search(r'<script[^>]*id="payload"[^>]*>(.*?)</script>', h, re.S).group(1))
    cap = {}
    for r in D["rows"]:
        k = r[9] + ":" + r[1]
        cap[k] = max(cap.get(k, 0), r[11] or 0)
    return sorted(cap, key=lambda k: -cap[k]), cap


# 시장 현황판의 지수·ETF(회원님 11/03) — 갈래 0 이 일봉 2년치를 받는다(50일선·주·월 추세).
ETFS = {"us": [("SPY", "S&P 500"), ("QQQ", "나스닥100"), ("IWM", "러셀2000"), ("RSP", "S&P 500 동일가중"),
               ("SMH", "반도체"), ("IGV", "소프트웨어"), ("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"),
               ("XLP", "필수소비재"), ("XLV", "헬스케어"), ("IBB", "바이오"), ("XLF", "금융"), ("XLI", "산업재"),
               ("ITA", "방산·항공"), ("XLE", "에너지"), ("XLB", "소재"), ("XLU", "유틸리티"), ("GDX", "금광"), ("ARKK", "혁신성장")],
        "jp": [("^N225", "닛케이225"), ("1306.T", "TOPIX"), ("2516.T", "그로스250")],
        "hk": [("^HSI", "항셍"), ("3033.HK", "항셍테크"), ("2828.HK", "H주")]}


def sig(x):
    return float(f"{x:.4g}")


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    px = db.setdefault("px", {})
    todo, cap = universe()
    wk = db.setdefault("w", {})
    if SHARD:
        i_sh, n_sh = (int(x) for x in SHARD.split("/"))
        todo = [k for k in todo if zlib.crc32(k.encode()) % n_sh == i_sh]
    now = datetime.now(timezone.utc)
    today = now.date().isoformat() + ("a" if now.hour < 12 else "p")      # 하루 두 번(아시아 장 마감 뒤 · 미국 장 마감 뒤)
    seen = db.setdefault("seen", {})
    if db.get("v") != 2:                                     # 3개월봉을 주봉으로 읽던 판(1) — 통째로 다시
        px.clear(); seen.clear(); db["v"] = 2
    todo = [k for k in todo if seen.get(k) != today]          
    print(f"주가: 오늘 볼 것 {len(todo):,}")
    t0, n, fails = time.time(), 0, 0
    for k in todo:
        if time.time() - t0 > SECS:
            break
        s = sym(k)
        if not s:
            continue
        try:
            base = f"https://query1.finance.yahoo.com/v8/finance/chart/{s}"
            with urllib.request.urlopen(urllib.request.Request(base + "?range=2y&interval=1wk", headers=UA), timeout=20) as r:
                j = json.loads(r.read())
            with urllib.request.urlopen(urllib.request.Request(base + "?range=max&interval=3mo", headers=UA), timeout=20) as r:
                jmax = json.loads(r.read())
            fails = 0
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                fails += 1
                print(f"  ! {k} HTTP {e.code}")
                if fails >= 8:
                    print("  ! 연달아 막힌다 — 멈춘다")
                    break
                time.sleep(15)
                continue
            seen[k] = today                                     # 404 — 야후에 없는 종목
            continue
        except Exception as e:                                  # noqa: BLE001
            print(f"  ! {k} {e!r}"[:120])
            time.sleep(2)
            continue
        v = summary(j, jmax)
        if v:
            px[k] = v
            if cap.get(k, 0) >= 0.3:                            # 업종 지수용 주봉 종가 — 시총 약 4천억원 이상만(작은 회사는 가중치가 없다)
                b = bars(j)[-105:]
                wk[k] = [datetime.fromtimestamp(b[0][0], timezone.utc).date().isoformat(), [sig(c) for _, c, _ in b]]
        seen[k] = today
        n += 1
        if n % 200 == 0:
            OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
        time.sleep(SLEEP)
    if not SHARD or SHARD.startswith("0/"):
        etf = db.setdefault("etf", {})
        for mkt, lst in ETFS.items():
            for s, _ in lst:
                try:
                    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(s)}?range=2y&interval=1d"
                    with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
                        b = bars(json.loads(r.read()))
                    if len(b) > 200:
                        etf[s] = [[datetime.fromtimestamp(t, timezone.utc).date().isoformat(), sig(c)] for t, c, _ in b]
                except Exception as e:                          # noqa: BLE001
                    print(f"  ! ETF {s} {e!r}"[:120])
                time.sleep(SLEEP)
    OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
    print(f"주가: 이번 {n:,} · 이 파일 {len(px):,}")


if __name__ == "__main__":
    main()
