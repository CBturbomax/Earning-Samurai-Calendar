# -*- coding: utf-8 -*-
"""일본 지역별 매출 · 수주(受注高·受注残高) — 결산단신의 태그 없는 표에서(`geojp.py`).

  python scrape_geo_jp.py            최근 TDnet + 옛 공시 거슬러 받기(시간 예산 안에서)
  python scrape_geo_jp.py --probe 6503   한 종목만 받아 무엇을 읽는지 찍는다(저장 안 함)

**두 길로 받는다.**
  1) **최근** — TDnet 목록(최근 며칠)의 결산단신 zip. 발표 당일 들어온다.
  2) **옛 공시** — 목록은 yanoshin TDnet API(2009년치부터), 파일은 JPX 공시 보관소
     (www2.jpx.co.jp/disc/{코드}0/{파일}, 5~6년치). 실시간 서버의 `jphist` 와 같은 길이다.
     TDnet 은 한 달치만 남기므로 이 길이 없으면 지역·수주 차트가 두어 칸에서 시작한다.
     시총 큰 순으로, 한 실행에 `GEO_SECS` 초만큼만 거슬러 간다(남의 서버다).

**담는 것은 원자료(누계)뿐이다.** 분기로 되돌리기·이름 맞추기·총매출 대보기는 build.py 가 한다 —
규칙을 고칠 때마다 다시 내려받지 않으려는 것이다(일본 부문 `raw` 와 같은 뜻). 다만 표를 읽는
규칙(geojp) 자체를 고치면 `RULE_VER` 를 올려 **통째로 다시 받는다** — 보관소는 늘 거기 있다
(회사 IR 월매출과 같은 판단).

data/geo_jp.json
  raw["jp:6503"] = {"geo": {"시작/종료": {지역: 엔}}, "oh": {"시작/종료": 엔}, "ob": {"종료": 엔}}
     oh = 受注高(누계 흐름) · ob = 受注残高(그날 잔액)
  docs   = 읽은 zip 이름(다시 안 받는다) · codes = 옛 공시를 다 거슬러 받은 날
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geojp
import scrape_fin_jp as F
from markets import excluded

HERE = Path(__file__).parent
OUT = HERE / "data" / "geo_jp.json"
RULE_VER = 1
SECS = float(os.environ.get("GEO_SECS", "1500"))
BACK_DAYS = int(os.environ.get("GEO_BACK_DAYS", "4"))
SINCE = "2017-06-01"            # 1Q18 의 전년 같은 분기까지
PAUSE = 0.35
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
T0 = time.time()


def left():
    return SECS - (time.time() - T0)


def get(url, binary=True, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return b if binary else b.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise F.Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise F.Throttled(str(e))


def spans_of(vals):
    """요약 XBRL -> {(시작, 종료): 누계 매출}. 연결 우선, 예상은 뺀다(실시간 서버 jp_fin_spans 와 같은 셈)."""
    out = {}
    for nm in F.NAME_REV:
        hits = [(cid, v, se) for cid, v, se in (vals or {}).get(nm, [])
                if not F.FORECAST_CTX.search(cid) and F.DUR_CTX.search(cid) and se and se[0] and se[1]
                and not F.NONCONS_CTX.search(cid) and v > 0]
        if hits:
            for _cid, v, se in hits:
                out.setdefault(tuple(se), v)
            break
    return out


def _yago(d):
    try:
        x = date.fromisoformat(d)
        return x.replace(year=x.year - 1).isoformat()
    except ValueError:                 # 2월 29일
        return (date.fromisoformat(d) - timedelta(days=365)).isoformat()


def read(blob):
    """zip 한 개 -> {"geo": {...}, "oh": {...}, "ob": {...}} (비면 {})."""
    vals = F.read_summary(blob)
    spans = spans_of(vals)
    if not spans:
        return {}
    tb = geojp.tables(blob)
    out = {}
    geo = geojp.geo_from(tb, spans)
    if geo:
        out["geo"] = {f"{st}/{en}": {k: round(v) for k, v in row.items()} for (st, en), row in geo.items()}
    cur = max(spans, key=lambda s: (s[1], -len(s[0])))          # 이번 누계(가장 늦게 끝나는 것)
    prior = (_yago(cur[0]), _yago(cur[1]))
    seen = set()
    for rec in geojp.orders_from(tb):
        if rec["kind"] in seen:                                  # 첫 표(회사 전체 수주 표)만
            continue
        seen.add(rec["kind"])
        pv, cv = rec["prev"], rec["cur"]
        sc = 1e6 if not re.search(r"億円", rec["before"] + rec["head"]) else 1e8
        if re.search(r"千円", rec["before"] + rec["head"]):
            sc = 1e3
        if rec["kind"] == "受注高":
            o = out.setdefault("oh", {})
            o[f"{cur[0]}/{cur[1]}"] = round(cv * sc)
            if pv:
                o.setdefault(f"{prior[0]}/{prior[1]}", round(pv * sc))
        else:
            o = out.setdefault("ob", {})
            o[cur[1]] = round(cv * sc)
            if pv:
                o.setdefault(prior[1], round(pv * sc))
    return out


def absorb(db, code, got):
    if not got:
        return False
    slot = db["raw"].setdefault("jp:" + code, {})
    for kind, by in got.items():
        dst = slot.setdefault(kind, {})
        for k, v in by.items():
            dst[k] = v                                           # 나중에 낸 공시(정정·재표시)가 이긴다
    return True


def load():
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if d.get("rv") != RULE_VER:
        if d:
            print(f"  표 읽는 규칙이 바뀌었다(rv{d.get('rv')} -> rv{RULE_VER}). 통째로 다시 받는다.")
        d = {}
    d.setdefault("raw", {})
    d.setdefault("docs", [])
    d.setdefault("codes", {})
    d["rv"] = RULE_VER
    return d


def save(db):
    db["source"] = "TDnet 결산단신 첨부(태그 없는 HTML 표) · JPX 공시 보관소"
    db["note"] = ("geo=지역별 매출 누계 · oh=受注高 누계 · ob=受注残高 잔액. 값은 엔. "
                  "분기로 되돌리기는 build.py 가 한다.")
    db["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    db["docs"] = sorted(set(db["docs"]))[-200000:]
    db["count"] = len(db["raw"])
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)


def universe():
    """캘린더에 오르는 일본 종목, 시총 큰 순."""
    codes, out = set(), set()
    for name in ("earnings.json", "earnings_jp_past.json", "earnings_jp_sched.json"):
        try:
            d = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in d.get("rows", []):
            if not isinstance(r, dict) or not r.get("code"):
                continue
            c = str(r["code"])
            codes.add(c)
            # 은행·리츠 등은 화면에 안 싣는다(markets.excluded) — 받을 까닭이 없다
            if excluded("jp", r.get("name") or "", r.get("sector") or ""):
                out.add(c)
    codes -= out
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8")).get("caps", {})
    except (OSError, ValueError):
        caps = {}
    return sorted(codes, key=lambda c: -(caps.get("jp:" + c) or 0))


def recent(db, done):
    """최근 며칠 TDnet 결산단신."""
    n = hit = 0
    for back in range(BACK_DAYS):
        if left() < 60:
            break
        day = (date.today() - timedelta(days=back)).isoformat()
        try:
            rows = F.announcements(day)
        except F.Throttled as e:
            print(f"  {day} 목록 실패: {e}")
            continue
        for r in rows:
            fn = r["zip"].rsplit("/", 1)[-1]
            if fn in done or left() < 60:
                continue
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"])
            except F.Throttled as e:
                print(f"  {r['code']} zip 실패: {e}")
                continue
            done.add(fn)
            n += 1
            if blob and absorb(db, r["code"], read(blob)):
                hit += 1
            time.sleep(PAUSE)
    print(f"  최근 {BACK_DAYS}일: 결산단신 {n:,}건 · 지역/수주를 읽은 것 {hit:,}")


def backfill(db, done):
    """옛 결산단신 — 시총 큰 순으로, 아직 안 거슬러 간 종목부터."""
    todo = [c for c in universe() if c not in db["codes"]]
    print(f"  옛 공시: 남은 종목 {len(todo):,}")
    n_codes = n_docs = hit = 0
    streak = 0
    for code in todo:
        if left() < 90:
            break
        try:
            j = json.loads(get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                               binary=False, timeout=30) or "{}")
        except (F.Throttled, ValueError) as e:
            streak += 1
            print(f"  {code} 목록 실패: {e}")
            if streak >= 5:
                print("  목록이 연달아 막힌다 — 여기서 멈춘다.")
                break
            time.sleep(3)
            continue
        streak = 0
        items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
        items = sorted((it for it in items
                        if (it.get("pubdate") or "")[:10] >= SINCE and it.get("url_xbrl")
                        and F.TANSHIN.search(it.get("title") or "")
                        and not F.NOT_TANSHIN.search(it.get("title") or "")),
                       key=lambda it: it["pubdate"])
        complete = True
        for it in items:
            fn = it["url_xbrl"].rsplit("/", 1)[-1]
            if fn in done:
                continue
            if left() < 60:
                complete = False
                break
            try:
                blob = get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", timeout=60)
            except F.Throttled as e:
                print(f"  {code} {fn} 실패: {e}")
                complete = False
                continue
            done.add(fn)
            n_docs += 1
            if blob and absorb(db, code, read(blob)):
                hit += 1
            time.sleep(PAUSE)
        if complete:
            db["codes"][code] = date.today().isoformat()
            n_codes += 1
        if n_codes and n_codes % 20 == 0:
            db["docs"] = list(done)
            save(db)
    print(f"  옛 공시: 종목 {n_codes:,} 끝 · 결산단신 {n_docs:,}건 · 지역/수주를 읽은 것 {hit:,}")


def probe(code):
    j = json.loads(get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                       binary=False) or "{}")
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    items = [it for it in items if F.TANSHIN.search(it.get("title") or "")
             and not F.NOT_TANSHIN.search(it.get("title") or "") and it.get("url_xbrl")][:6]
    for it in items:
        fn = it["url_xbrl"].rsplit("/", 1)[-1]
        blob = get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}")
        print(it["pubdate"][:10], it["title"][:40])
        print("   ", json.dumps(read(blob) if blob else "없음", ensure_ascii=False)[:600])


def main():
    if "--probe" in sys.argv:
        probe(sys.argv[sys.argv.index("--probe") + 1])
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    db = load()
    done = set(db["docs"])
    recent(db, done)
    backfill(db, done)
    db["docs"] = list(done)
    save(db)
    raw = db["raw"]
    print(f"  지역 {sum(1 for v in raw.values() if v.get('geo')):,}종목 · "
          f"수주 {sum(1 for v in raw.values() if v.get('oh') or v.get('ob')):,}종목 · "
          f"옛 공시를 다 받은 종목 {len(db['codes']):,}")


if __name__ == "__main__":
    main()
