# -*- coding: utf-8 -*-
"""EPS 서프라이즈 이력 — 컨센서스 대비 몇 % 웃돌았나/밑돌았나(회원님 11/03: "EPS 가 얼마인지는 안 궁금하다, 지난 기간 서프/쇼크만
+/- 차트로, 최대한 오래").

나스닥 earnings-surprise 는 최근 네 분기뿐이다. Zacks 종목 '실적 발표 일정' 페이지(서버가 그린 HTML)에 지난 발표가 전부
표로 실려 있다(떠보기 11/03: VRTX 200 · `earnings_announcements_earnings_table`). 칸: 발표일 · 회계분기 · 예상 · 실제 · 차이 · 차이%.
미국만이다(Zacks 가 일본·홍콩은 안 다룬다).

data/surprise_us.json  stocks[티커] = {"s": [[회계분기 'YYYY-MM', 발표일 'YYYY-MM-DD', 서프라이즈 %], ...], "ts": 받은 날}
  · 남의 서버라 1.2초에 한 번, 시총 큰 순. 발표 언저리(앞뒤 닷새)는 매번, 나머지는 30일마다 다시 받는다.
"""
import gzip
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "surprise_us.json"
SECS = int(os.environ.get("SURPRISE_SECS", "1200"))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept-Encoding": "gzip", "Accept-Language": "en-US,en;q=0.9"}
TABLE = re.compile(r'"earnings_announcements_earnings_table"\s*:\s*(\[.*?\]\s*\])', re.S)


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")


def parse(html):
    m = TABLE.search(html)
    if not m:
        return None
    try:
        rows = json.loads(m.group(1))
    except ValueError:
        return None
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r[0], "%m/%d/%y").date().isoformat()
            mo, yr = r[1].split("/")
            pct = re.sub(r"<[^>]+>", "", r[5]).replace("%", "").replace("+", "").replace(",", "").strip()
            if pct in ("", "--", "NA", "N/A"):
                continue
            out.append([f"{yr}-{int(mo):02d}", d, round(float(pct), 1)])
        except (ValueError, IndexError, AttributeError):
            continue
    return out


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    try:
        rows = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8")).get("rows", [])
    except (OSError, ValueError):
        rows = []
    cap, near = {}, set()
    today = date.today()
    for r in rows:
        c = r.get("code")
        if not c or not re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c):
            continue
        cap[c] = max(cap.get(c, 0), r.get("cap") or 0)
        try:
            if abs((date.fromisoformat(r["date"]) - today).days) <= 5:
                near.add(c)
        except (KeyError, ValueError):
            pass

    def due(c):
        rec = st.get(c)
        if not rec or not rec.get("ts"):
            return True
        age = (today - date.fromisoformat(rec["ts"][:10])).days
        return age >= (0 if c in near else 30) and not (c in near and rec["ts"][:13] >= (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H"))
    todo = sorted((c for c in cap if cap[c] and due(c)), key=lambda c: (c not in near, -cap[c]))
    print(f"서프라이즈: 대상 {len(todo):,} (발표 언저리 {len(near & set(todo)):,})")
    t0, n, got, fails = time.time(), 0, 0, 0
    for c in todo:
        if time.time() - t0 > SECS:
            break
        try:
            html = get(f"https://www.zacks.com/stock/research/{c.replace('.', '-')}/earnings-calendar")
            fails = 0
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                fails += 1
                print(f"  ! {c} HTTP {e.code}")
                if fails >= 5:
                    print("  ! 연달아 막힌다 — 여기서 멈춘다")
                    break
                time.sleep(20)
                continue
            st[c] = {"s": (st.get(c) or {}).get("s", []), "ts": datetime.now(timezone.utc).isoformat(timespec="minutes")}
            continue
        except Exception as e:                              # noqa: BLE001
            print(f"  ! {c} {e!r}"[:120])
            time.sleep(3)
            continue
        n += 1
        s = parse(html) or []
        old = {x[0]: x for x in (st.get(c) or {}).get("s", [])}
        for x in s:
            old[x[0]] = x                                    # 쌓는다 — 새 값이 이긴다
        st[c] = {"s": sorted(old.values()), "ts": datetime.now(timezone.utc).isoformat(timespec="minutes")}
        got += bool(s)
        if n % 50 == 0:
            OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
        time.sleep(1.2)
    db["count"] = sum(1 for v in st.values() if v.get("s"))
    OUT.write_text(json.dumps(db, separators=(",", ":")), encoding="utf-8")
    print(f"서프라이즈: 이번 {n:,}곳 · 이력 있음 {got:,} · 전체 {db['count']:,}")


if __name__ == "__main__":
    main()
