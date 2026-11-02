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
import io
import json
import os
import zipfile
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import geojp
import scrape_fin_jp as F
from markets import excluded

HERE = Path(__file__).parent
OUT = HERE / "data" / "geo_jp.json"
RULE_VER = 5      # 5: PDF 를 전체 목록에서 짝지음(미국 기준 회사) · 2: 옛 공시는 PDF 에서 지역 · 3: PDF 수주(확인 전 칸) · 4: 3개월치 표(우리 분기 매출로 맞춤)
SECS = float(os.environ.get("GEO_SECS", "1500"))
BACK_DAYS = int(os.environ.get("GEO_BACK_DAYS", "4"))
SINCE = "2016-10-01"            # 1Q18(1~3월)의 전년 같은 분기까지 — 2017년 연간·9개월 누계가 필요하다. JPX 보관소는 2016년치까지 있다(떠보기 11/02)
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


def has_body(blob):
    """zip 에 본문(qualitative·세그먼트 장)이 있나. 2019~2022년쯤 zip 에는 재무제표 장만 있다."""
    try:
        return any("qualitative" in n or "sg" in n.rsplit("/", 1)[-1][:16]
                   for n in zipfile.ZipFile(io.BytesIO(blob)).namelist())
    except zipfile.BadZipFile:
        return False


_QREV = {}


def quarter_revs(code):
    """우리가 이미 가진 그 회사의 **분기(3개월) 매출** {(시작, 종료): 매출} — 3개월치 표만 싣는 공시(미쓰비시전기 2019~2022 PDF)를
    맞춰 볼 잣대. 결산단신 요약에는 누계만 있어서 이게 없으면 그 표를 못 읽는다."""
    if not _QREV:
        for name in ("financials_jp.json", "financials_intl.json", "financials_jp_old.json"):
            try:
                d = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            st = d.get("stocks", d) if isinstance(d, dict) else {}
            for k, rec in st.items():
                if not str(k).startswith("jp:") or not isinstance(rec, dict):
                    continue
                for p_ in rec.get("points") or []:
                    e, r = p_.get("end"), p_.get("rev")
                    if not e or not r:
                        continue
                    y, m = int(e[:4]), int(e[5:7]) - 2
                    if m <= 0:
                        y, m = y - 1, m + 12
                    _QREV.setdefault(k[3:], {}).setdefault((f"{y}-{m:02d}-01", e), r)
        # 옛 결산단신 누계(실시간 서버가 JPX 보관소에서 뽑아 둔 것) — 같은 시작일의 앞 누계를 빼서 3개월치로
        try:
            old = json.loads((HERE / "data" / "financials_jp_old.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        for k, spans_ in old.items():
            by = {}
            for key, row in (spans_ or {}).items():
                st_, _, en_ = key.partition("/")
                if st_ and en_ and (row or {}).get("rev"):
                    by.setdefault(st_, []).append((en_, row["rev"]))
            for st_, lst in by.items():
                lst.sort()
                prev_e, prev_v = None, None
                for en_, v in lst:
                    d0 = (date.fromisoformat(en_) - date.fromisoformat(st_)).days
                    if d0 <= 100:
                        _QREV.setdefault(k[3:], {}).setdefault((st_, en_), v)
                    elif prev_e and 80 <= (date.fromisoformat(en_) - date.fromisoformat(prev_e)).days <= 100 and v > prev_v:
                        qs = (date.fromisoformat(prev_e) + timedelta(days=1)).isoformat()
                        _QREV.setdefault(k[3:], {}).setdefault((qs, en_), v - prev_v)
                    prev_e, prev_v = en_, v
        _QREV.setdefault("_", {})
    return _QREV.get(code, {})


def read(blob, pdf_url=None, code=None):
    """zip 한 개 -> {"geo": {...}, "oh": {...}, "ob": {...}} (비면 {}).

    **zip 에 본문이 없으면 같은 공시의 PDF 를 읽는다**(pdf_url) — 지역만. 수주는 PDF 에서 칸 이름(当·前)을
    가를 수 없어 예상·누계를 집는다(구덴코 418,000 은 통기 예상이었다). 틀린 숫자보다 빈칸이 낫다."""
    vals = F.read_summary(blob)
    spans = spans_of(vals)
    if not spans:
        return {}
    # 3개월치 표용 잣대 — 이 공시의 이번 누계·전년 누계가 끝나는 날에 끝난 분기만
    if code:
        ends = {e for _st, e in spans}
        for (qs, qe), r in quarter_revs(code).items():
            if any(abs((date.fromisoformat(qe) - date.fromisoformat(e)).days) <= 7 for e in ends):
                spans.setdefault((qs, qe), r)
    tb = geojp.tables(blob)
    out = {}
    if pdf_url and not has_body(blob):
        try:
            pdf = get(pdf_url, timeout=60)
        except F.Throttled:
            pdf = None
        geo = geojp.geo_from(geojp.pdf_tables(pdf), spans) if pdf else {}
        if geo:
            out["geo"] = {f"{st}/{en}": {k: round(v) for k, v in row.items()} for (st, en), row in geo.items()}
        # PDF 수주는 '확인 전' 칸(ohp·obp)에 공시마다 하나씩 쌓는다 — build.py 가 두 공시가 맞을 때만 싣는다
        cur = max(spans, key=lambda s_: (s_[1], -len(s_[0])))
        prior = (_yago(cur[0]), _yago(cur[1]))
        for rec in (geojp.pdf_orders(pdf) if pdf else []):
            if rec["kind"] == "受注高":
                o = out.setdefault("ohp", {})
                o[f"{cur[0]}/{cur[1]}"] = round(rec["cur"] * 1e6)
                if rec["prev"]:
                    o[f"{prior[0]}/{prior[1]}"] = round(rec["prev"] * 1e6)
            else:
                o = out.setdefault("obp", {})
                o[cur[1]] = round(rec["cur"] * 1e6)
                if rec["prev"]:
                    o[prior[1]] = round(rec["prev"] * 1e6)
        return out
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
            if kind.endswith("p"):                               # 확인 전(PDF) — 공시마다 따로 남겨 서로 맞춰 본다
                dst.setdefault(k, []).append(v)                 # 같은 값이 두 공시에서 오면 두 번 — 그게 확인이다
            else:
                dst[k] = v                                       # 나중에 낸 공시(정정·재표시)가 이긴다
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
            if blob and absorb(db, r["code"], read(blob, None, r["code"])):
                hit += 1
            time.sleep(PAUSE)
    print(f"  최근 {BACK_DAYS}일: 결산단신 {n:,}건 · 지역/수주를 읽은 것 {hit:,}")


WORKERS = int(os.environ.get("GEO_WORKERS", "3"))
_YANO = threading.Lock()
_yano_t = [0.0]


def listing(code):
    """yanoshin 목록 — 한 사람이 돌리는 무료 API 라 0.4초에 한 번만(실시간 서버 jphist 와 같은 예절)."""
    with _YANO:
        wait = 0.4 - (time.time() - _yano_t[0])
        if wait > 0:
            time.sleep(wait)
        _yano_t[0] = time.time()
    j = json.loads(get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                       binary=False, timeout=30) or "{}")
    items = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    out = sorted((it for it in items
                  if (it.get("pubdate") or "")[:10] >= SINCE and it.get("url_xbrl")
                  and F.TANSHIN.search(it.get("title") or "")
                  and not F.NOT_TANSHIN.search(it.get("title") or "")),
                 key=lambda it: it["pubdate"])
    # XBRL 목록의 document_url 은 zip 이다 — PDF 는 전체 목록에서 같은 날 결산단신 줄을 짝짓는다(11/02).
    # 미국 기준 회사(도요타·고마쓰)는 zip 에 첨부가 없고 PDF 번호도 zip 과 달라, 2018~2022 지역 표를 통째로 못 읽었다.
    try:
        with _YANO:
            wait = 0.4 - (time.time() - _yano_t[0])
            if wait > 0:
                time.sleep(wait)
            _yano_t[0] = time.time()
        a = json.loads(get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=600",
                           binary=False, timeout=30) or "{}")
        pdfs = {}
        for it in (x.get("Tdnet", x) for x in (a.get("items") or [])):
            du = it.get("document_url") or ""
            if du.lower().endswith(".pdf") and F.TANSHIN.search(it.get("title") or "") \
                    and not F.NOT_TANSHIN.search(it.get("title") or ""):
                pdfs.setdefault((it.get("pubdate") or "")[:10], du.rsplit("/", 1)[-1])
        for it in out:
            it["pdf_name"] = pdfs.get(it["pubdate"][:10], "")
    except (F.Throttled, ValueError):
        pass
    return out


def one_code(code, done):
    """한 종목의 옛 결산단신 전부 -> (code, 다 받았나, [(zip 이름, 읽은 것)]). db 는 안 만진다(갈래끼리 안 부딪히게)."""
    items = listing(code)
    got, complete = [], True
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
        pn = it.get("pdf_name") or ""                                # 미국 기준 회사(도요타·고마쓰)는 zip 과 PDF 번호가 다르다
        if not pn.lower().endswith(".pdf"):
            pn = fn.replace("0812", "1401", 1) if fn.startswith("0812") else ""
        pdf = f"https://www2.jpx.co.jp/disc/{code}0/{pn}" if pn else None
        try:
            r = read(blob, pdf, code) if blob else {}
        except Exception as e:                       # noqa: BLE001 — 깨진 공시 하나가 실행을 죽이지 않게
            print(f"  {code} {fn} 읽기 실패: {e!r}"[:160])
            r = {}
        got.append((fn, r))
        time.sleep(PAUSE)
    return code, complete, got


def backfill(db, done):
    """옛 결산단신 — 시총 큰 순으로, 아직 안 거슬러 간 종목부터. **세 갈래로 동시에** 받는다
    (한 종목에 결산단신이 40건쯤이라 한 줄로는 전 종목이 사흘 걸렸다)."""
    todo = [c for c in universe() if c not in db["codes"]]
    print(f"  옛 공시: 남은 종목 {len(todo):,} · {WORKERS}갈래")
    n_codes = n_docs = hit = fails = 0
    it = iter(todo)
    with ThreadPoolExecutor(WORKERS) as ex:
        live = set()
        while True:
            while len(live) < WORKERS and left() > 120:
                c = next(it, None)
                if c is None:
                    break
                live.add(ex.submit(one_code, c, frozenset(done)))
            if not live:
                break
            fin, live = wait(live, return_when=FIRST_COMPLETED)
            for f in fin:
                try:
                    code, complete, got = f.result()
                except (F.Throttled, ValueError) as e:
                    fails += 1
                    print(f"  목록 실패: {e}")
                    if fails >= 8:
                        print("  목록이 연달아 막힌다 — 새로 넣지 않는다.")
                        it = iter(())
                    continue
                for fn, r in got:
                    done.add(fn)
                    n_docs += 1
                    hit += bool(absorb(db, code, r))
                if complete:
                    db["codes"][code] = date.today().isoformat()
                    n_codes += 1
                    if n_codes % 20 == 0:
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
