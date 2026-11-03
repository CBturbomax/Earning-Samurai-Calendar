# -*- coding: utf-8 -*-
"""주가 요약 — 스크리너용 신고가·수익률(회원님 11/03: "신고가 애들, 3달·52주·역사적, RS 강도 센 애들").

야후 차트 API 를 종목당 한 번(range=max · 주봉). 주봉 고가로 13주·52주·상장 이래 고가를 보고, 종가로 1·3·6·12개월 수익률을 낸다.
전체 이력은 싣지 않는다 — 종목마다 숫자 몇 개만. RS(상대강도) 순위는 빌드가 전 종목을 모아 매긴다.

data/px_s{i}.json  {"px": {열쇠: [기준일, 종가, r1, r3, r6, r12, d13, d52, dall]}}   (r = 수익률 %, d = 그 고가에서 몇 % 아래, 0 이면 신고가)
"""
import json
import os
import re
import time
import urllib.error
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


def summary(j):
    r = ((j.get("chart") or {}).get("result") or [{}])[0]
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    ts, cl, hi = r.get("timestamp") or [], q.get("close") or [], q.get("high") or []
    rows = [(t, c, h if h is not None else c) for t, c, h in zip(ts, cl, hi) if c is not None]
    if len(rows) < 14:
        return None
    last_t, last = rows[-1][0], rows[-1][1]

    def ret(weeks):
        return round((last / rows[-1 - weeks][1] - 1) * 100, 1) if len(rows) > weeks and rows[-1 - weeks][1] else None

    def dist(weeks):
        hs = [h for _, _, h in (rows[-weeks:] if weeks else rows)]
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
    return sorted(cap, key=lambda k: -cap[k])


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    px = db.setdefault("px", {})
    todo = universe()
    if SHARD:
        i_sh, n_sh = (int(x) for x in SHARD.split("/"))
        todo = [k for k in todo if zlib.crc32(k.encode()) % n_sh == i_sh]
    now = datetime.now(timezone.utc)
    today = now.date().isoformat() + ("a" if now.hour < 12 else "p")      # 하루 두 번(아시아 장 마감 뒤 · 미국 장 마감 뒤)
    seen = db.setdefault("seen", {})
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
            req = urllib.request.Request(f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?range=max&interval=1wk", headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read())
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
        v = summary(j)
        if v:
            px[k] = v
        seen[k] = today
        n += 1
        if n % 200 == 0:
            OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
        time.sleep(SLEEP)
    OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
    print(f"주가: 이번 {n:,} · 이 파일 {len(px):,}")


if __name__ == "__main__":
    main()
