# -*- coding: utf-8 -*-
"""AI(Claude Code `claude -p`)로 **1Q18 부터의 빈칸**을 메운다 — 사업부·지역·수주를 공시 원문에서 직접 읽는다.

회원님(11/02): "이제 AI 를 붙였으니 1Q18~3Q26 지역별 매출·수주까지 빠지는 것 없이 다 받아와라."
규칙 수집기(SEC 벌크·제출 서류 XBRL, 일본 결산단신 표 읽기)가 먼저 돌고, **그래도 빈 분기**가 있는 회사만 여기로 온다.

어디가 비었나 — build.py 를 불러 화면에 실리는 그대로(FIN · SEG · GEO · ORD)를 보고, 1Q18 부터 총매출이 있는 분기 중
사업부나 지역이 빈 분기를 센다. 시총 큰 순(미국·일본 번갈아).

무엇을 읽나 — 그 분기를 담은 공시 한 장.
  미국  SEC 제출 목록(submissions)의 10-Q·10-K 본문. 10-Q 한 장에 이번 분기·전년 같은 분기·누계가 같이 실려
        두 해가 한 번에 찬다. 결산 분기는 10-K 의 연간 − 3분기 10-Q 의 9개월 누계(빌드가 뺀다).
  일본  결산단신(JPX 보관소, 2016~). zip 첨부 표가 없던 해는 PDF 글자를 편다.
  홍콩  아직 안 한다(반기·연간 보고서 PDF — 다음 차례).

**AI 가 준 숫자를 믿지 않는다 — 총매출과 맞을 때만 싣는다.**
  · 사업부·지역은 한 기간의 합이 우리가 이미 가진 그 기간 총매출과 2% 안(또는 공시가 적은 상계 줄을 더해 2% 안)일 때만.
  · 수주·수주잔고는 맞춰 볼 총계가 없어서 **두 공시가 같은 기간에 같은 값**(1% 안)을 줄 때만 화면에 싣는다
    (그해 공시의 당기 칸 = 이듬해 공시의 전기 칸). 일본 PDF 수주와 같은 규칙이다(build.py 의 confirmed).
  · 한 번 물은 공시는 다시 묻지 않는다(`asked`). 구독 한도에 걸리면 그 자리에서 멈추고 적는다.

data/ai_fill.json
  stocks[시장:코드] = {"seg": {"시작/종료": {이름: 값}}, "geo": {...}, "oc": {"oh|시작/종료": [값…], "ob|종료": [값…]}}
  asked[시장:코드] = [공시 id…]
build.py 의 load_ai_fill 이 누계를 분기로 되돌려(_decum) **빈 분기만** 채운다 — 있는 값은 안 덮는다.

    python ai_fill.py                 # 시간 예산 안에서
    python ai_fill.py us:KO jp:6501   # 몇 종목만(무엇을 물었고 무엇이 돌아왔는지 찍는다)
"""
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ai_seg
import pr_table as pt

HERE = Path(__file__).parent
# 여러 갈래(러너)가 나눠 돈다 — 열쇠의 해시로 회사를 가른다. 갈래마다 제 파일에만 쓰므로 커밋이 안 부딪힌다.
SHARD = os.environ.get("AIFILL_SHARD", "")          # "0/4" 꼴. 비면 한 갈래(옛 파일)
LEGACY = HERE / "data" / "ai_fill.json"
OUT = HERE / "data" / (f"ai_fill_s{SHARD.split('/')[0]}.json" if SHARD else "ai_fill.json")
SECS = int(os.environ.get("AIFILL_SECS", "2100"))
MAX_CALLS = int(os.environ.get("AIFILL_MAX", "2000"))
MAX_TOK = int(os.environ.get("AIFILL_TOKENS", "0") or 0)   # 이번 실행 토큰 상한(0 = 없음) — 넘으면 멈춘다


def over_tok():
    return MAX_TOK and ai_seg.USED["in"] + ai_seg.USED["out"] >= MAX_TOK
WORKERS = int(os.environ.get("AIFILL_WORKERS", "6"))
# 수주를 내는 업종 — 사업부·지역이 다 차 있어도 수주 칸이 비면 묻는다
ORD_JP = re.compile(r"建設|機械|電気機器|精密機器|造船|輸送用機器|情報通信|プラント|エンジニアリング")
ORD_US = re.compile(r"Industrial Machinery|Aerospace|Military|Electrical Products|Engineering|Construction|Metal Fabrications|"
                    r"Telecommunications Equipment|Homebuilding|Shipbuilding|Railroad|Ordnance|Electronic Components", re.I)
_JP_SECT = {}


def jp_sector(code):
    if not _JP_SECT:
        for name in ("earnings.json", "earnings_jp_sched.json"):
            try:
                rows = json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
            except (OSError, ValueError):
                rows = []
            for r in rows:
                if isinstance(r, dict) and r.get("sector"):
                    _JP_SECT.setdefault(str(r.get("code")), r["sector"])
        _JP_SECT.setdefault("_", "")
    return _JP_SECT.get(code, "")
SINCE = "2018-01-01"
BIG = set()                                          # 시총 상위 — main 이 채운다
BIG_N = int(os.environ.get("AIFILL_BIG", "0"))       # 사업부 기록이 아예 없는 회사(단일 부문 — 키엔스·닌텐도·다케다)는 안 묻는다(11/02)
RECENT_DAYS = int(os.environ.get("AIFILL_RECENT", "0") or 0)   # >0 이면 그 날수 안의 빈칸만
DOC_LIMIT = int(os.environ.get("AIFILL_DOC", "20000"))   # 원문 길이 상한 — 길수록 한도를 많이 먹는다
EMPTY_VER = 3                   # 2: 일본 PDF 이름을 목록에서(미국 기준 회사 — 도요타·고마쓰)
DRY_N = 4                       # 연달아 이만큼 읽어 맞은 것이 없으면 그 회사는 넉 달 쉰다
KEYS = re.compile(r"segment|geograph|region|countr|americas|europe|asia|china|japan|emea|international|domestic|"
                  r"backlog|remaining performance|bookings|new orders|order intake|orders received|disaggregat|"
                  r"revenue by|revenues by|net sales by|sales by", re.I)
KEYS_JP = re.compile(r"地域|所在地|仕向|国別|エリア|海外|国内|受注|セグメント|部門|事業別|品目|製品別|売上高明細")


# ── 어디가 비었나 ────────────────────────────────────────────────────────

def _d(s):
    return date.fromisoformat(s)


def covered(rec, end, tol=20):
    if not rec:
        return False
    e = _d(end)
    for r in rec.get("pts") or []:
        if re.match(r"\d{4}-\d\d-\d\d$", str(r[0])) and abs((_d(r[0]) - e).days) <= tol and any(v for v in r[1:]):
            return True
    return False


def gaps(B, key):
    """총매출이 있는 1Q18 이후 분기 중 사업부·지역이 빈 종료일들."""
    f = B.FIN.get(key) or {}
    if f.get("freq") not in ("Q", "H"):
        return []
    m, code = key.split(":")
    # **한도 아끼기(11/02 실측)**: 지역·수주는 그 회사가 낸 적이 있을 때만 묻는다. 빈칸 18만 칸 중 대부분이 '분기 지역 매출을
    # 아예 안 내는 회사'였고, AI 가 그걸 찾느라 공시를 읽고 빈손으로 나왔다. 낸 적이 있는 회사의 구멍만 판다.
    ordc = len((B.ORD.get(key) or {}).get("pts") or []) >= 2
    geoc = len((B.GEO.get(key) or {}).get("pts") or []) >= 4
    # 사업부 기록이 아예 없는 회사는 대개 '단일 부문'이다 — 시총 상위(BIG)만 묻는다(우리 수집기가 놓친 큰 회사를 찾으려고)
    segc = len((B.SEG.get(key) or {}).get("pts") or []) >= 4 or key in BIG or key in SOON
    since = SINCE
    if RECENT_DAYS and key not in SOON:               # 곧 발표할 회사는 1Q18 부터 전부                                    # 최근 칸부터(같은 토큰으로 눈에 보이는 빈칸이 가장 많이 준다)
        since = max(SINCE, (date.today() - timedelta(days=RECENT_DAYS)).isoformat())
    out = []
    for p in f.get("points") or []:
        e = p.get("end")
        if not e or e < since or not p.get("rev"):
            continue
        if (segc and not covered(B.SEG.get(key), e)) or (geoc and not covered(B.GEO.get(key), e)) or (ordc and not covered(B.ORD.get(key), e)):
            out.append(e)
    return out


SOON = set()                    # 지난 7일 ~ 앞으로 21일 안에 발표하는 회사(targets 가 채운다)


def targets(B, only):
    us, jp = [], []
    try:
        rows = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8")).get("rows", [])
    except (OSError, ValueError):
        rows = []
    ucap = {}
    for r in rows:
        ucap["us:" + r["code"]] = max(ucap.get("us:" + r["code"], 0), r.get("cap") or 0)
    # **곧 발표할 회사가 먼저**(11/02 회원님: "2~3주 안에 실적 낼 회사일수록 예민하게"). 지난 7일 ~ 앞으로 21일 안에
    # 발표가 있는 회사를 시총과 상관없이 맨 앞에 세운다 — 발표날 차트에 옛 분기 구멍이 없게.
    soon = SOON
    lo, hi = (date.today() - timedelta(days=7)).isoformat(), (date.today() + timedelta(days=21)).isoformat()
    for name, pre in (("earnings_us.json", "us:"), ("earnings.json", "jp:"), ("earnings_jp_sched.json", "jp:"),
                      ("earnings_jp_past.json", "jp:")):
        try:
            rr = rows if name == "earnings_us.json" else \
                json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
        except (OSError, ValueError):
            rr = []
        for r in rr:
            if isinstance(r, dict) and lo <= str(r.get("date") or "") <= hi:
                soon.add(pre + str(r.get("code")))
    from markets import excluded
    seen = set()
    for key in B.FIN:
        if only and key not in only:
            continue
        m = key.split(":")[0]
        sect = B.SECTORS.get(key, "") or (jp_sector(key[3:]) if m == "jp" else "")
        if excluded(m, "", sect, (B.INDUSTRIES.get(key, "") or "").replace("-", "")) or (m == "jp" and "銀行" in sect):
            continue                                  # 은행·리츠는 화면에 안 싣는다
        sig = tuple(p.get("rev") for p in (B.FIN[key].get("points") or [])[-6:])
        if m == "us" and sig in seen:
            continue                                  # GOOG/GOOGL 처럼 같은 회사의 두 종목
        seen.add(sig)
        first = 0 if key in soon else 1
        if m == "us":
            us.append((first, -(ucap.get(key) or 0), key))
        elif m == "jp":
            jp.append((first, -(B.CAPS.get(key) or 0), key))
    us.sort()
    jp.sort()
    out = []
    for i in range(max(len(us), len(jp))):           # 미국·일본 번갈아 — 한 시장이 대기줄을 다 먹지 않게
        for lst in (us, jp):
            if i < len(lst):
                out.append(lst[i][-1])
    return out


# ── 미국: SEC 10-Q·10-K ──────────────────────────────────────────────────

SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBS_OLD = "https://data.sec.gov/submissions/{name}"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
_SEC_LOCK = threading.Lock()
_sec_t = [0.0]


def sec(url, binary=False):
    import scrape_seg_edgar as se
    with _SEC_LOCK:                                   # SEC 는 초당 10건 — 갈래가 여럿이어도 넉넉히
        w = 0.15 - (time.time() - _sec_t[0])
        if w > 0:
            time.sleep(w)
        _sec_t[0] = time.time()
    return _patient(lambda: se.get(url, timeout=60, binary=binary))


def _patient(fn):
    """남의 서버가 '너무 자주'(429·503·403·끊김)라 하면 물러났다 다시(5·20·60초). 세 번 다 막히면 그 공시만 건너뛴다 —
    다음 실행이 다시 본다(asked 에 안 적힌다). SEC 는 초당 10건, yanoshin 은 한 사람이 돌리는 무료 API 라 더 조심한다."""
    for wait in (5, 20, 60, None):
        try:
            return fn()
        except Exception as e:                         # noqa: BLE001
            if wait is None or not re.search(r"429|503|403|timed out|reset|Throttl|Blocked|URLError", repr(e)):
                raise
            time.sleep(wait)


def us_filings(cik):
    """[(보고 기준일, 서식, 접수번호, 본문 파일)] — 10-Q·10-K(정정 제외), 2017-06 이후."""
    j = json.loads(sec(SUBS.format(cik=cik)) or "{}")
    blocks = [j.get("filings", {}).get("recent", {})]
    for f in j.get("filings", {}).get("files", []) or []:
        if (f.get("filingTo") or "") >= "2017-06-01":
            try:
                blocks.append(json.loads(sec(SUBS_OLD.format(name=f["name"])) or "{}"))
            except ValueError:
                pass
    out = []
    for r in blocks:
        for i, form in enumerate(r.get("form", [])):
            if form not in ("10-Q", "10-K", "20-F", "40-F", "6-K"):
                continue
            rd = (r.get("reportDate") or [""] * (i + 1))[i]
            if form == "6-K":
                # 외국 기업(노바티스·셸·아스트라제네카…)은 10-Q 대신 6-K 로 분기 실적을 낸다. 보고 기준일이 비어 오므로
                # 접수일을 적고, 고를 때 '분기 끝 + 15~80일'로 본다. 설명에 실적 낱말이 있는 것만(6-K 는 수백 건이다).
                fd = (r.get("filingDate") or [""] * (i + 1))[i]
                desc = ((r.get("primaryDocDescription") or [""] * (i + 1))[i] + " " + r["primaryDocument"][i]).lower()
                if fd < "2017-07-01" or not re.search(r"result|earning|quarter|q[1-4]|interim|half|trading|report", desc):
                    continue
                out.append((fd, "6-K", r["accessionNumber"][i].replace("-", ""), r["primaryDocument"][i]))
                continue
            if not rd or rd < "2017-06-01":
                continue
            out.append((rd, "10-K" if form in ("20-F", "40-F") else form, r["accessionNumber"][i].replace("-", ""),
                        r["primaryDocument"][i]))
    return sorted(set(out))


def us_pick(files, miss):
    """빈 종료일을 덮는 서류 — 그 날 끝난 서류가 먼저, 없으면 한 해 뒤 서류(전년 칸). 결산 분기면 9개월 누계를 줄 3분기 10-Q 도."""
    want, done = [], []
    for e in sorted(miss, reverse=True):              # 최근 것부터 — 한 장이 그 분기와 전년 같은 분기를 같이 준다
        ed = _d(e)
        if any(abs((ed - d0).days) <= 15 for d0 in done):
            continue
        hit = [f for f in files if f[1] != "6-K" and abs((_d(f[0]) - ed).days) <= 12] or \
            [f for f in files if f[1] == "6-K" and 15 <= (_d(f[0]) - ed).days <= 80][:2]
        nxt = [f for f in files if f[1] != "6-K" and abs((_d(f[0]) - ed - timedelta(days=365)).days) <= 15]
        for f in hit or nxt:
            if f not in want:
                want.append(f)
            fd = ed if f[1] == "6-K" else _d(f[0])
            done.extend([fd, fd - timedelta(days=364)])
            if f[1] == "10-K":                       # 연간 − 9개월
                q3 = [g for g in files if g[1] == "10-Q" and 80 <= (_d(f[0]) - _d(g[0])).days <= 100]
                for g in q3:
                    if g not in want:
                        want.append(g)
            break
    return want


def tables_text(raw, kw):
    out = []
    for t in pt.tables(raw):
        rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
        rows = [r for r in rows if r.strip()]
        if len(rows) < 2:
            continue
        blob = "\n".join(rows)
        if kw.search(blob) and re.search(r"\d{2,}", blob):
            out.append(blob)
    txt = "\n\n=== TABLE ===\n".join(out)
    return txt[:DOC_LIMIT]


# ── 일본: 결산단신 ────────────────────────────────────────────────────────

def jp_docs(code):
    """[(공시일, zip 이름, PDF 이름)]. XBRL 목록(hasXBRL=1)의 document_url 은 zip 이라, PDF 는 전체 목록에서 같은 날·같은
    결산단신 줄을 찾아 짝짓는다(미국 기준 회사 — 도요타·고마쓰 — 는 zip 에 첨부가 없어 PDF 가 유일한 원문이다)."""
    import scrape_fin_jp as F
    import scrape_geo_jp as G
    xs = G.listing(code)
    try:
        with G._YANO:
            time.sleep(0.4)
        j = json.loads(_patient(lambda: G.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=600", binary=False,
                             timeout=30)) or "{}")
        alls = [it.get("Tdnet", it) for it in (j.get("items") or [])]
    except Exception:                                  # noqa: BLE001
        alls = []
    pdfs = {}
    for it in alls:
        du = it.get("document_url") or ""
        if du.lower().endswith(".pdf") and F.TANSHIN.search(it.get("title") or "") \
                and not F.NOT_TANSHIN.search(it.get("title") or ""):
            pdfs.setdefault((it.get("pubdate") or "")[:10], du.rsplit("/", 1)[-1])
    return [(it["pubdate"][:10], it["url_xbrl"].rsplit("/", 1)[-1], pdfs.get(it["pubdate"][:10], "")) for it in xs]


def jp_text(code, fn, pn=""):
    """(글, {(시작, 종료): 누계 매출}) — zip 첨부 표, 없으면 PDF 글자."""
    import geojp
    import scrape_fin_jp as F
    import scrape_geo_jp as G
    blob = _patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", timeout=60))
    if not blob:
        return "", {}
    spans = G.spans_of(F.read_summary(blob))
    parts = []
    for before, rows in geojp.tables(blob):           # 첨부 이름이 회사마다 달라(도요타) has_body 로 가르지 않는다 — 표가 있으면 쓴다
        body = "\n".join(" | ".join(c for c in r if c) for r in rows)
        if KEYS_JP.search(before + body):
            parts.append("[" + before[-120:] + "]\n" + body)
    if not parts:
        # PDF 이름은 목록이 준다(미국 기준 회사 — 도요타·고마쓰 — 는 zip 이름과 PDF 이름의 번호가 다르다)
        pn = pn if pn.lower().endswith(".pdf") else (fn.replace("0812", "1401", 1) if fn.startswith("0812") else "")
        try:
            import pdftext
            pdf = _patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{pn}", timeout=60)) if pn else None
            lines = pdftext.extract_cells(pdf, max_pages=40) if pdf else []
        except Exception:                              # noqa: BLE001 — 암호·깨진 PDF
            lines = []
        rows = [" | ".join(re.sub(r"\s+", " ", t).strip() for _x, t in sorted(c) if t.strip()) for c in lines]
        keep = set()
        for i, r in enumerate(rows):
            if KEYS_JP.search(r) and len(r) < 90:
                keep.update(range(i, min(len(rows), i + 30)))
        parts.append("\n".join(rows[i] for i in sorted(keep)))
    return "\n\n=== TABLE ===\n".join(p for p in parts if p)[:DOC_LIMIT], spans


# ── 묻기 ──────────────────────────────────────────────────────────────────

PROMPT = """You are extracting numbers from a listed company's financial filing. Read ONLY the tables below.

Company: {name} ({key}). Filing: {doc}. Reporting currency: {cur}.
Names we already use — segments: {segs}; regions: {geos}. Reuse these names when the row is the same thing.

Extract:
- "seg": revenue (sales to external customers if both are printed) by business segment or by product/service line — the company's main revenue breakdown.
- "geo": revenue by geographic region or country.
- "oh": order intake / new orders / bookings / 受注高 (company total only).
- "ob": order backlog / 受注残高 (company total only).
- "rpo": remaining performance obligations (company total only).

For every printed period column (current AND prior year, three-month AND year-to-date) give one item:
  k (seg|geo|oh|ob|rpo), name (as printed, or our name if it is the same row), end (period end date YYYY-MM-DD),
  m (months covered: 3, 6, 9 or 12; 0 for balances at a date such as backlog or RPO), v (the number exactly as printed),
  u (unit multiplier stated for the table: 1000000 for "in millions"/百万円, 1000 for "in thousands"/千円, 100000000 for 億円, 1 if none),
  t (true only for a printed total row of that table; "eliminations"/調整額 rows: name "eliminations").
Never include percentages, changes, forecasts/guidance/予想/見通し, profits, or numbers you computed. Skip sub-totals that are sums of other rows you list.
If a table repeats in the filing, list it once. If nothing relevant is printed, return an empty list.

Answer with ONLY JSON: {{"items": [{{"k": "geo", "name": "Americas", "end": "2025-09-30", "m": 3, "v": 1234.5, "u": 1000000, "t": false}}]}}

TABLES:
{tables}
"""


_PAUSE = [0.0]
_PLOCK = threading.Lock()


def ask(key, name, doc, cur, segs, geos, txt):
    """한도(429)에 걸리면 **모든 줄이 함께** 쉬었다가 같은 공시를 다시 묻는다(1·2·3…분). 여섯 번 연달아 막히면 진짜 한도로 본다."""
    txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", txt)          # PDF 글자의 NUL 이 명령 인자를 깨뜨렸다
    for attempt in range(int(os.environ.get("AIFILL_RETRY", "1"))):   # 기본 1 — 한도에 한 번 걸리면 곧장 멈춘다(추가 결제 금지)
        w = _PAUSE[0] - time.time()
        if w > 0:
            time.sleep(w)
        try:
            return _ask(key, name, doc, cur, segs, geos, txt)
        except ai_seg.Limited:
            with _PLOCK:
                _PAUSE[0] = max(_PAUSE[0], time.time() + 60 * (attempt + 1))
    raise ai_seg.Limited("한도에 걸림")


def _ask(key, name, doc, cur, segs, geos, txt):
    ans = ai_seg.claude(PROMPT.format(name=name, key=key, doc=doc, cur=cur, segs=json.dumps(segs, ensure_ascii=False),
                                      geos=json.dumps(geos, ensure_ascii=False), tables=txt))
    m = re.search(r"\{.*\}", ans, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0)).get("items") or []
    except ValueError:
        return None


# ── 검산 ──────────────────────────────────────────────────────────────────

def _months_back(e, m):
    """종료일에서 m 달 앞 + 하루 = 시작일. 월말 결산이면 그달 1일이 된다(9/30 · 3달 -> 7/1)."""
    import calendar
    y, mo = e.year, e.month - m
    while mo <= 0:
        y, mo = y - 1, mo + 12
    last = calendar.monthrange(y, mo)[1]
    day = last if e.day >= calendar.monthrange(e.year, e.month)[1] else min(e.day, last)
    return date(y, mo, day) + timedelta(days=1)


def span_of(end, m, starts):
    """(시작, 종료) — 시작은 이 회사가 이미 쓴 시작일 중 10일 안의 것으로 맞춘다(_decum 이 같은 시작끼리 뺀다)."""
    e = _d(end)
    st = _months_back(e, m)
    for s in starts:
        if abs((_d(s) - st).days) <= 12:
            return s, end
    return st.isoformat(), end


def fin_rev(B, key, st, en, jspans):
    """(시작, 종료) 기간의 총매출 — 일본은 같은 공시 요약의 누계, 아니면 우리 분기 매출의 합."""
    for (a, b), v in (jspans or {}).items():
        if abs((_d(b) - _d(en)).days) <= 5 and abs((_d(a) - _d(st)).days) <= 12:
            return v
    pts = [p for p in (B.FIN.get(key) or {}).get("points") or [] if p.get("end") and p.get("rev")]
    inside = [p for p in pts if _d(st) + timedelta(days=40) < _d(p["end"]) <= _d(en) + timedelta(days=10)]
    months = round(((_d(en) - _d(st)).days + 1) / 30.4)
    need = max(1, round(months / 3))
    if (B.FIN.get(key) or {}).get("freq") == "H":     # 반기 회사(유럽·홍콩계 외국 기업)는 점 하나가 여섯 달
        need = max(1, round(months / 6))
    if len(inside) != need:
        return None
    return sum(p["rev"] for p in inside)


def accept(B, key, items, rec, jspans):
    """검산 통과분만 rec 에 담는다. 반환: 담은 기간 수."""
    starts = sorted({k.split("/")[0] for kind in ("seg", "geo") for k in rec.get(kind, {})})
    groups = {}
    n_ord = 0
    for it in items or []:
        try:
            k, nm, en, m = it["k"], str(it["name"]).strip(), str(it["end"])[:10], int(it.get("m") or 0)
            v = float(it["v"]) * float(it.get("u") or 1)
            _d(en)
        except (KeyError, TypeError, ValueError):
            continue
        if en < "2016-10-01" or not nm:
            continue
        if k in ("seg", "geo") and m in (3, 6, 9, 12):
            groups.setdefault((k, en, m), []).append((nm, v, bool(it.get("t"))))
        elif k == "oh" and m in (3, 6, 9, 12) and v > 0:
            st, en2 = span_of(en, m, starts)
            rec.setdefault("oc", {}).setdefault(f"oh|{st}/{en2}", []).append(round(v))
            n_ord += 1
        elif k in ("ob", "rpo") and m == 0 and v > 0:
            rec.setdefault("oc", {}).setdefault(f"{k}|{en}", []).append(round(v))
            n_ord += 1
    n = 0
    for (k, en, m), rows in groups.items():
        st, en = span_of(en, m, starts)
        rev = fin_rev(B, key, st, en, jspans)
        if not rev:
            continue
        parts = {}
        elim = tot = 0.0
        for nm, v, is_t in rows:
            if is_t or re.search(r"^total|合計|総計|^計$|連結", nm, re.I):
                tot = tot or v
            elif re.search(r"eliminat|調整|消去|corporate|reconcil", nm, re.I):
                elim += v
            elif v > 0:
                parts.setdefault(nm, v)
        if len(parts) < 2:
            continue
        s = sum(parts.values())
        ok = abs(s / rev - 1) <= 0.02 or (elim and abs((s + elim) / rev - 1) <= 0.02) \
            or (tot and abs(s / tot - 1) <= 0.01 and abs(tot / rev - 1) <= 0.02)
        if not ok:
            continue
        rec.setdefault(k, {})[f"{st}/{en}"] = {nm: round(v) for nm, v in parts.items()}
        starts.append(st)
        n += 1
    return n + n_ord


# ── 한 회사 ───────────────────────────────────────────────────────────────

class Budget:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.limited = None

    def take(self):
        with self.lock:
            if self.limited or self.calls >= MAX_CALLS or over_tok():
                return False
            self.calls += 1
            return True


def one(B, key, miss, db, bud, t0, verbose):
    m, code = key.split(":")
    asked = set(db["asked"].get(key, []))
    rec = json.loads(json.dumps(db["stocks"].get(key) or {}))     # 갈래마다 제 사본 — 저장과 부딪히지 않게
    new_asked, new_empty = [], []
    asked |= set(db.get("empty", {}).get(key, []))
    f = B.FIN.get(key) or {}
    cur = f.get("cur") or ("JPY" if m == "jp" else "USD")
    segs = (B.SEG.get(key) or {}).get("names") or []
    geos = (B.GEO.get(key) or {}).get("names") or []
    name = code
    log, got, tried = [], 0, 0
    dry_n = 0
    if m == "us":
        import scrape_live_us as lu
        cik = lu.cik_map_cached().get(code) or lu.cik_map_cached().get(code.replace(".", "-"))
        if not cik:
            return key, 0, 0, ["CIK 없음"], rec, (new_asked, new_empty), 0
        files = us_filings(int(cik))
        docs = [(f"{a}", f"{fm} {rd}", (int(cik), a, nm)) for rd, fm, a, nm in us_pick(files, miss)]
    else:
        docs = []
        for pub, fn, pn in jp_docs(code):
            docs.append((fn, f"決算短信 {pub}", (code, fn, pn)))
        # 빈 종료일 언저리(종료 + 30~60일 공시) 또는 한 해 뒤(전기 칸) 것만
        keep, done = [], []
        for e in sorted(miss, reverse=True):          # 최근 것부터 — 결산단신 한 장이 당기·전기 두 해를 준다
            ed = _d(e)
            if any(abs((ed - d0).days) <= 15 for d0 in done):
                continue
            near = [x for x in docs if 25 <= (_d(x[1].split()[-1]) - ed).days <= 75] or \
                [x for x in docs if 390 <= (_d(x[1].split()[-1]) - ed).days <= 440]
            if near:
                if near[0] not in keep:
                    keep.append(near[0])
                done.extend([ed, ed - timedelta(days=364)])
        docs = keep
    for did, label, arg in docs:
        if did in asked:
            continue
        if time.time() - t0 > SECS or not bud.take():
            break
        try:
            if m == "us":
                cik, acc, nm = arg
                if label.startswith("6-K"):                 # 6-K 본문은 표지 — 보도자료는 첨부(EX-99)에 있다
                    import scrape_live_us as lu
                    raw = (lu.ex99_html(cik, acc) or "").encode()
                else:
                    raw = sec(DOC.format(cik=cik, acc=acc, name=nm), binary=True)
                txt = tables_text(raw.decode("utf-8", "replace"), KEYS) if raw else ""
                jspans = None
            else:
                txt, jspans = jp_text(*arg)
            if not txt:
                new_empty.append(did)                  # 표 없음 — 읽는 규칙(EMPTY_VER)이 바뀌면 다시 본다
                log.append(f"{label} 표 없음")
                continue
            tried += 1
            items = ask(key, name, label, cur, segs, geos, txt)
        except ai_seg.Limited as e:
            bud.limited = str(e)[:200]
            break
        except Exception as e:                          # noqa: BLE001 — 한 서류가 실행을 죽이지 않게
            log.append(f"{label} 실패 {e!r}"[:120])
            continue
        new_asked.append(did)
        if items is None:
            log.append(f"{label} 답 깨짐")
            continue
        n = accept(B, key, items, rec, jspans)
        got += n
        dry_n = 0 if n else dry_n + 1
        if dry_n >= DRY_N:
            log.append("연달아 안 맞음 — 쉰다")
            break
        if verbose:
            print(key, label, json.dumps(items, ensure_ascii=False)[:800])
        log.append(f"{label} 항목 {len(items)} · 맞은 기간 {n}")
    return key, tried, got, log, rec, (new_asked, new_empty), dry_n


def main():
    only = [a for a in sys.argv[1:] if ":" in a]
    if ai_seg.ENGINE == "codex":
        if not shutil.which("codex") or not (Path.home() / ".codex" / "auth.json").exists():
            print("codex CLI 또는 로그인 파일이 없다 — 아무것도 안 한다.")
            return
    elif not shutil.which("claude") or not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("claude CLI 또는 구독 토큰이 없다 — 아무것도 안 한다.")   # API 키(종량제)로는 돌지 않는다
        return
    t0 = time.time()
    import build as B                                   # 화면에 실리는 그대로를 본다(12초쯤)
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    db.setdefault("stocks", {})
    db.setdefault("asked", {})
    lim = db.get("limited")
    if lim and not only and (datetime.now(timezone.utc) - datetime.fromisoformat(lim)).total_seconds() < 1500:
        print(f"구독 한도에 걸린 지 25분이 안 됐다({lim}) — 이번엔 쉰다.")
        return
    db.pop("limited", None)
    if db.get("empty_ver") != EMPTY_VER:              # 원문 꺼내는 규칙이 바뀌었다 — '표 없음' 이던 공시를 다시 본다
        if db.get("empty_ver") is None:               # 첫 판은 '표 없음'도 asked 에 적었다 — 일본(PDF 를 못 찾던 것)만 지운다
            for k in [k for k in db["asked"] if k.startswith("jp:")]:
                db["asked"].pop(k)
        db["empty"], db["empty_ver"] = {}, EMPTY_VER
    if SHARD:                                         # 옛 단일 파일에서 이미 물은 공시는 다시 안 묻는다
        try:
            old = json.loads(LEGACY.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            old = {}
        for k, lst in (old.get("asked") or {}).items():
            have = db["asked"].setdefault(k, [])
            have.extend(x for x in lst if x not in have)
    i_sh, n_sh = (int(x) for x in SHARD.split("/")) if SHARD else (0, 1)
    import zlib
    todo = []
    dry = db.setdefault("dry", {})
    tg = targets(B, set(only))
    for mk in ("us:", "jp:"):
        BIG.update([k for k in tg if k.startswith(mk)][:BIG_N])
    if only:
        BIG.update(only)
    for key in tg:
        if zlib.crc32(key.encode()) % n_sh != i_sh:
            continue
        d = dry.get(key) or {}
        if not only and d.get("n", 0) >= DRY_N and (date.today() - _d(d.get("ts", "2000-01-01"))).days < 120:
            continue                                  # 공시를 넷 읽어도 하나도 안 맞은 회사 — 넉 달 쉰다(안 내는 회사)
        miss = gaps(B, key)
        if miss:
            todo.append((key, miss))
    print(f"AI 빈칸 메우기: 빈 분기가 있는 회사 {len(todo):,} (빈 분기 {sum(len(x[1]) for x in todo):,})")
    bud = Budget()
    lock = threading.Lock()
    done_keys, tried, got = 0, 0, 0
    it = iter(todo)
    with ThreadPoolExecutor(WORKERS) as ex:
        live = {}
        while True:
            while len(live) < WORKERS and not bud.limited and bud.calls < MAX_CALLS and not over_tok() and time.time() - t0 < SECS:
                nx = next(it, None)
                if nx is None:
                    break
                live[ex.submit(one, B, nx[0], nx[1], db, bud, t0, bool(only))] = nx[0]
            if not live:
                break
            fut = next(as_completed(live))
            live.pop(fut)
            try:
                key, tr, gt, log, rec, new_asked, dn = fut.result()
            except Exception as e:                      # noqa: BLE001
                print(f"  ! {e!r}"[:200])
                continue
            with lock:
                if rec:
                    db["stocks"][key] = rec
                db["asked"].setdefault(key, []).extend(new_asked[0])
                db["empty"].setdefault(key, []).extend(new_asked[1])
                if tr:
                    dry[key] = {"n": dn, "ts": date.today().isoformat()}
                done_keys += 1
                tried += tr
                got += gt
                if tr or gt:
                    print(f"  {key}: " + " / ".join(log[-6:]))
                if done_keys % 10 == 0:
                    save(db)
    if bud.limited:
        print(f"  ! 구독 한도·과부하 — 여기서 멈춘다: {bud.limited}")
        db["limited"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    save(db)
    u = ai_seg.USED
    print(f"토큰: 엔진 {ai_seg.ENGINE} · 호출 {u['n']} · 입력 {u['in']:,} · 출력 {u['out']:,} · 호출당 {(u['in'] + u['out']) // max(u['n'], 1):,}")
    print(f"AI 빈칸 메우기: 회사 {done_keys} · 물어본 공시 {tried} · 검산 통과 기간 {got} · 남은 회사 {len(todo) - done_keys}")


def save(db):
    db["count"] = len(db["stocks"])
    db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
