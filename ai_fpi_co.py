"""외국 기업 사업부 — **회사 하나를 한 번에** 읽는다(시험판, 11/10) → data/fpi_co.json.

회원님(11/10): "사업부 완전 엉망 · 지금까지 한 방식 말고 싹 다 갈아엎자."

ai_fpi.py(공시 한 장 = 한 번 묻기 · 사업부 합이 총매출과 1% 안이어야 통과)가 사업부를 못 채운 까닭 둘:
  1. 회사가 내는 부문 표는 총매출과 안 맞는 일이 흔하다(CNQ 로열티 차감 전 · SU·MT 부문 간 거래 포함) — 맞게 읽어도 버려졌다.
  2. 한 장씩 따로 읽어 분기마다 이름·기준이 흔들리고 띄엄띄엄 들어왔다.

여기서는
  · 분기마다 실적 공시 한 장씩 골라 **부문 표만** 추려 한 프롬프트에 차례대로 싣고(회사당 한 번),
  · 모델이 **한 이름 체계·한 기준**으로 전 분기를 정리한다(나중 공시의 재표시 값 우선, 상계 줄은 따로),
  · 검산은 코드가 한다 — 합이 총매출과 같을 필요는 없다. 대신
      (가) 잣대는 **우리가 이미 검산해 가진 분기 총매출**이다(모델이 고른 총매출 줄은 안 믿는다 — SU 는 장마다 '기타 수익 포함' 줄을 집었다)
      (나) 부문 합(+상계) = 총매출 1% 안이면 '맞아떨어짐'
      (다) 아니면 **부문 합 ÷ 총매출이 분기마다 고른가**(앞뒤 가운뎃값의 ±12%, 1.3배 넘는 회사는 ±25% — build.pack_seg 와 같은 잣대)
      (라) 이미 검산을 통과해 가진 분기(fpi_pr · 화면 SEG)와 겹치면 부문마다 3% 안.

  python ai_fpi_co.py us:SU us:CNQ us:MT
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "fpi_co.json"
PER_DOC = int(os.environ.get("FPICO_DOC", "3200"))
MAX_PROMPT = 110000

PROMPT = """You are building a clean QUARTERLY revenue-by-segment time series for one company from its own quarterly reports.

Company: {code}. Reporting currency: {cur}. Segment names we already use for recent quarters: {segs}.
{anchors}Below are table excerpts from {n} quarterly filings, oldest first. Each starts with "### FILING filed YYYY-MM-DD".

For every quarter end from {a} to {z} that the filings cover, give:
- "rev": total revenue of the whole company for the THREE months (the headline revenue line; the same definition in every quarter).
- "seg": revenue of each reportable business segment for the THREE months.
- "elim": the eliminations / intersegment / corporate line of that same table, if printed (usually negative), so that sum(seg) + elim = the table's total.

Rules:
1. Three-month (quarter) values only. Never six-, nine-, twelve-month or year-to-date values, never percentages or volumes.
   Do not compute anything (no subtracting year-to-date). If a quarter has no three-month column anywhere, leave it out.
2. Use ONE consistent set of segment names and ONE consistent measure across all quarters. If VERIFIED VALUES are given above,
   use exactly the measure that reproduces them. Otherwise prefer the measure that reconciles to total revenue (external revenues,
   or gross segment revenues together with the eliminations line). State it in "basis". Reuse our existing names when the segment is the same thing.
   A line "[before table: ...]" is the text printed just above that table (often the segment's name). If you cannot tell which
   segment a table belongs to, leave it out — never assign names by position.
3. When a later filing reprints an earlier quarter (prior-year or trailing-quarter column), prefer the later filing's value.
4. If segments were reorganised, use the newest structure wherever restated numbers are printed; for older quarters with no
   restated numbers keep the names printed then. Never guess a mapping.
5. Numbers exactly as printed (negative if in brackets), with "u" = the unit multiplier (1000000 for millions, 1000 for thousands).
6. "src" = the filing date you took that quarter from.

Answer with ONLY JSON:
{{"basis": "...", "u": 1000000, "q": {{"2019-06-30": {{"rev": 10098, "seg": {{"Oil Sands": 5140, "Refining": 5626}}, "elim": -668, "src": "2019-07-25"}}}}}}

{docs}
"""


def _d(s):
    return date.fromisoformat(s)


REVTERM = re.compile(r"(?i)\b(revenues?|sales|turnover)\b")
NOTREV = re.compile(r"(?i)price|volume|per (share|unit|boe|bbl|tonne)|cost of|bbl|boe|mmcf|margin|days|growth|change")
SEGWORD = re.compile(r"(?i)segment|divisions?\b|business (unit|area|line)|by (business|product|division)")
UNITS = re.compile(r"(?i)bbl|boe|mmcf|tonnes?\b|\bkt\b|per share|headcount|\$/")


def _tables(raw):
    """공시 한 장 -> [(점수, 글)] — **표를 통째로 싣지 않고 머리줄 + 매출 줄만** 남긴다(11/10 시험 1차: 표 전체를 실어 회사당 5만 토큰,
    그러고도 상한에 잘려 MT 의 ACIS 가 빠졌다). 부문이 가로로 놓인 표(CNQ·SU: 칸마다 부문)는 머리줄 + '…sales/revenue' 줄,
    부문마다 표가 따로인 회사(MT: 'NAFTA … Sales')도 같은 꼴로 줄어든다. 부문이 세로로 놓인 표(줄마다 부문)는 통째로(2,500자)."""
    import pr_table as pt
    out = []
    tabs = pt.tables(raw)
    # 표 바로 앞 글(부문 이름이 표 밖 제목에 있는 회사 — MT: 'NAFTA' 다음에 표). 표 수가 <table> 수와 같을 때만 믿는다(겹친 표면 어긋난다).
    pos = [m.start() for m in re.finditer(r"(?i)<table\b", raw)]
    cap = {}
    if len(pos) == len(tabs):
        for i, p0 in enumerate(pos):
            c = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;|&#160;", " ", raw[max(0, p0 - 1500):p0])).strip()
            cap[i] = c[-90:]
    for i, t in enumerate(tabs):
        rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
        rows = [r for r in rows if r.strip()]
        if len(rows) < 2:
            continue
        def numrow(r):                                   # 값 줄인가 — 연도·날짜만 든 줄(머리줄)은 아니다
            rest = r.split(" | ", 1)[1] if " | " in r else ""
            if re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? ?\d|months|quarter|ended|\b[1-4]Q ?\d\d|\bQ[1-4]\b|\bFY ?\d\d|\b[12]H ?\d\d|\b\d{1,2}M ?\d\d\b", rest):
                return False
            toks = re.findall(r"\d[\d,.]*\d|\d", rest.replace("\u00a0", " "))
            if len(toks) < 2 or all(re.fullmatch(r"(19|20)\d\d", t) for t in toks):
                return False
            return bool(re.search(r"\d{3}|\d[ ,]\d", rest))
        isnum = [numrow(r) for r in rows]
        if not any(isnum):
            continue
        first = isnum.index(True)
        head = rows[:min(first, 5)]
        htxt = " ".join(head)
        if UNITS.search(htxt) or UNITS.search(rows[first].split(" | ")[0]):
            continue
        blob = "\n".join(rows)
        revrows = [k for k, r in enumerate(rows) if isnum[k] and REVTERM.search(r.split(" | ")[0]) and not NOTREV.search(r.split(" | ")[0])]
        seg = bool(SEGWORD.search(blob))
        q = bool(re.search(r"(?i)three months|quarter|\b[1-4]Q ?\d\d|\bQ[1-4]\b", htxt + rows[first]))
        tot_last = [k for k in revrows if re.match(r"(?i)\s*(total|net)\b", rows[k]) and sum(isnum[:k]) >= 2]
        if tot_last and len(rows) <= 40 and not NOTREV.search(htxt):
            out.append(((2 if seg else 1) + (1 if q else 0), i, (f"[before table: {cap[i]}]\n" if cap.get(i) else "") + blob[:2500]))
        elif revrows:
            keep = sorted(set(revrows) | {k for k, r in enumerate(rows) if isnum[k] and
                                           re.search(r"(?i)eliminat|inter-?segment|royalt|^total", r.split(" | ")[0])})[:12]
            txt = (f"[before table: {cap[i]}]\n" if cap.get(i) else "") + "\n".join(head + [rows[k] for k in keep])[:1800]
            out.append(((2 if seg else 0) + (1 if q else 0) + (1 if len(head) >= 1 else 0), i, txt))
        elif (seg or REVTERM.search(htxt)) and not NOTREV.search(htxt):
            out.append(((2 if seg else 0) + (1 if q else 0), i, (f"[before table: {cap[i]}]\n" if cap.get(i) else "") + blob[:2500]))       # 줄마다 부문인 표
    return out


def seg_text(raws, annual=False):
    """실적 공시 한 건의 첨부들 -> 부문 표 글(PER_DOC 자). 첨부를 가리지 않고 표를 한데 모아 점수 순으로 싣는다
    (CNQ: 부문 표는 재무제표 장에, 분기 요약은 MD&A 에 있다)."""
    import ai_fpi as P
    got, seen, plain0 = [], set(), ""
    for raw in raws:
        plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw[:60000]))
        if not annual and not P.QHINT.search(plain):
            continue
        plain0 = plain0 or plain
        for s_, i, txt in _tables(raw):
            if txt not in seen:
                seen.add(txt)
                got.append((-s_, len(got), txt))
    if not got or not any(REVTERM.search(t) for _s, _i, t in got):
        return ""
    out, n = [], 0
    for _s, _i, b in sorted(got):
        if n + len(b) > PER_DOC:
            continue
        out.append(b)
        n += len(b)
    units = sorted({m.group(0)[:80] for m in re.finditer(
        r"(?i)\(?[^().]{0,30}\b(in|expressed in)\s+(thousands|millions|billions)\b[^().]{0,30}\)?", plain0)})[:2]
    return ("UNIT NOTES: " + " | ".join(units) + "\n" if units else "") + "\n--- table ---\n".join(out)


def filing_text(cik, acc):
    """6-K 한 건 -> 부문 표 글. 2.5만 자 넘는 첨부를 이름 맞는 것 먼저 네 장까지 열어 표를 한데 모은다."""
    import ai_fill as af
    import scrape_live_us as lu
    blob = af.sec(lu.FILES.format(cik=cik, acc=acc))
    try:
        items = json.loads(blob)["directory"]["item"] if blob else []
    except (ValueError, KeyError):
        return ""

    def size(it):
        try:
            return int(it.get("size") or 0)
        except ValueError:
            return 0
    htm = sorted(it["name"] for it in items if it["name"].lower().endswith((".htm", ".html"))
                 and not re.match(r"index|.*-index", it["name"], re.I) and not 0 < size(it) < 25000)
    ex = [n for n in htm if re.search(r"ex[-_]?99|99[-_.]?[1-3]\b|991|992|press|earnings|result", n, re.I)]
    raws = []
    for name in (ex + [n for n in htm if n not in ex])[:4]:
        raw = af.sec(lu.DOC.format(cik=cik, acc=acc, name=name), binary=True)
        if raw:
            raws.append(raw.decode("utf-8", "replace"))
    return seg_text(raws)


def ask(prompt):
    """claude -p 한 번(프롬프트는 표준입력으로 — 10만 자가 넘으면 인자 길이 제한에 걸린다)."""
    import ai_seg
    cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "1", "--tools", "",
           "--system-prompt", "You read financial filings and reply with JSON only.",
           "--strict-mcp-config", "--disable-slash-commands"]
    if os.environ.get("AI_MODEL"):
        cmd += ["--model", os.environ["AI_MODEL"]]
    r = subprocess.run(cmd, input=prompt, cwd=tempfile.gettempdir(), capture_output=True, text=True,
                       timeout=int(os.environ.get("FPICO_TIMEOUT", "900")))
    try:
        j = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("claude 답이 JSON 이 아니다: " + ((r.stdout or "") + (r.stderr or ""))[-400:])
    if j.get("is_error"):
        raise RuntimeError(str(j.get("result"))[:400])
    u = j.get("usage") or {}
    with LOCK:
        ai_seg.USED["in"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
        ai_seg.USED["out"] += u.get("output_tokens") or 0
        ai_seg.USED["n"] += 1
    return j.get("result") or ""


def check(B, key, ans, pr_q, all_ends=(), mine_before=()):
    """모델 답 -> (받아들인 분기 {종료일: {...}}, 보고 줄들). 검산 (가)~(라)."""
    fin = B.FIN.get(key) or {}
    have = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}
    u = float(ans.get("u") or 1)
    rows = {}
    for e, x in (ans.get("q") or {}).items():
        try:
            d = _d(e)
        except ValueError:
            continue
        if not isinstance(x, dict):
            continue
        near = min(have, key=lambda h: abs((_d(h) - d).days), default=None)
        mrev = float(x["rev"]) * u if isinstance(x.get("rev"), (int, float)) and x["rev"] > 0 else None
        pend = not near or abs((_d(near) - d).days) > 20
        if pend:                                         # 우리에게 총매출이 없는 분기 — 모델의 총매출로 임시 검산, 빌드가 연간 합으로 확인한 뒤에만 싣는다
            near = min(all_ends, key=lambda h: abs((_d(h) - d).days), default=None)
            if not near or abs((_d(near) - d).days) > 20 or not mrev:
                continue
        seg = {str(n).strip()[:60]: float(v) * u for n, v in (x.get("seg") or {}).items() if isinstance(v, (int, float)) and v}
        rev = float(x["rev"]) * u if isinstance(x.get("rev"), (int, float)) else None
        elim = float(x["elim"]) * u if isinstance(x.get("elim"), (int, float)) else 0.0
        rows[near] = {"rev": rev, "seg": seg, "elim": elim, "src": x.get("src"), "have": mrev if pend else have[near], "pend": pend, "mrev": mrev}
    ends = sorted(rows)
    ratio = {e: sum(rows[e]["seg"].values()) / rows[e]["have"] for e in ends if len(rows[e]["seg"]) >= 2}
    # 화면에 이미 있는 사업부(닻) — 겹치는 분기에서 부문마다 견준다
    old = {}
    for r in (B.SEG.get(key) or {}).get("pts") or []:
        if re.match(r"\d{4}-\d\d-\d\d$", str(r[0])) and str(r[0]) not in mine_before:
            old[str(r[0])] = {B.seg_key(n): v for n, v in zip(B.SEG[key]["names"], r[1:]) if v}
    for e, lst in (pr_q or {}).items():
        x = next((r for r in reversed(lst) if r.get("seg")), None)
        if x:
            old[e] = {B.seg_key(n): v for n, v in x["seg"].items()}
    # 뼈대 부문 — 절반 넘는 분기에 나오는 이름. 그중 하나가 빠진 분기는 '부문 빠짐'(상한에 잘렸거나 못 읽은 칸 — 합이 줄어든 채 실리면 안 된다)
    cnt = {}
    for e in ratio:
        for n in rows[e]["seg"]:
            cnt[B.seg_key(n)] = cnt.get(B.seg_key(n), 0) + 1
    core = {k for k, c in cnt.items() if c > len(ratio) / 2}
    ok, rep = {}, []
    for i, e in enumerate(ends):
        r = rows[e]
        why = []
        grade = ""
        if e in ratio:
            exact = abs((sum(r["seg"].values()) + r["elim"]) / r["have"] - 1) <= 0.01 or abs(ratio[e] - 1) <= 0.01
            nb = [ratio[x] for x in ends[max(0, i - 4):i + 5] if x != e and x in ratio]
            mid = sorted(nb)[len(nb) // 2] if len(nb) >= 3 else None
            steady = mid is not None and abs(ratio[e] / mid - 1) <= (0.25 if mid >= 1.3 else 0.12) and 0.8 <= ratio[e] <= 2.6
            grade = "맞아떨어짐" if exact else ("비율 고름" if steady else "")
            if not grade:
                why.append(f"합÷총매출 {ratio[e]:.2f} (앞뒤 {None if mid is None else round(mid, 2)})")
            mine_k = {B.seg_key(n) for n in r["seg"]}
            miss = core - mine_k
            if miss and not exact:
                # 앞뒤 분기에는 있는 부문이 이 분기에만 없다
                nbk = set()
                for x in ends[max(0, i - 1):i + 2]:
                    if x != e and x in ratio:
                        nbk |= {B.seg_key(n) for n in rows[x]["seg"]}
                if miss & nbk:
                    why.append("부문 빠짐")
        else:
            why.append("부문 없음")
        anchor = ""
        if e in old and r["seg"]:
            mine = {B.seg_key(n): v for n, v in r["seg"].items()}
            both = [k for k in mine if k in old[e]]
            if both:
                bad = [k for k in both if abs(mine[k] / old[e][k] - 1) > 0.03]
                anchor = f"닻 {len(both) - len(bad)}/{len(both)}"
                if len(bad) > len(both) / 2:
                    why.append("가진 값과 다름")
        good = bool(grade) and not ({"가진 값과 다름", "부문 빠짐"} & set(why))
        rep.append(f"    {e} {'✓' if good else '✗'}{'?' if r['pend'] else ' '}매출 {round(r['have'] / 1e6):>7,} · 합÷매출 {ratio.get(e, 0):.2f} · {grade or '-'} {anchor} "
                   f"· {' / '.join(f'{n} {round(v / 1e6):,}' for n, v in r['seg'].items())[:170]}" + (f"  ← {'; '.join(why)}" if why else ""))
        if good:
            ok[e] = {"seg": r["seg"], "elim": r["elim"], "src": r["src"], "grade": grade, **({"rev": r["mrev"], "pend": 1} if r["pend"] else {})}
    return ok, rep


LOCK = threading.Lock()
MAX_TOK = int(os.environ.get("FPI_TOKENS", "1500000"))
SECS = int(os.environ.get("FPI_SECS", "4500"))
CHUNK = 14                                              # 한 번에 묻는 빈 분기 수(+ 닻 분기 둘)
T0 = time.time()


def spent():
    import ai_seg
    return ai_seg.USED["in"] + ai_seg.USED["out"]


def _seg_dates(B, key):
    return [str(r[0]) for r in (B.SEG.get(key) or {}).get("pts") or []
            if any(v for v in r[1:]) and re.match(r"\d{4}-\d\d-\d\d$", str(r[0]))]


def missing(B, key):
    """사업부가 빈 분기(1Q18~, 막 끝난 분기 제외) — 최근 것부터."""
    import ai_fpi as P
    fin = B.FIN.get(key) or {}
    fresh = date.fromordinal(date.today().toordinal() - 12).isoformat()
    ends = [e for e in P.quarter_ends(fin) if "2018-03-31" <= e <= fresh]
    have = _seg_dates(B, key)
    return ends, sorted((e for e in ends if not any(abs((_d(h) - _d(e)).days) <= 20 for h in have)), reverse=True)


def one(B, key, db):
    import ai_fpi as P
    import scrape_live_us as lu
    code = key.split(":")[1]
    cik = lu.cik_map_cached().get(code) or lu.cik_map_cached().get(code.replace(".", "-"))
    if not cik:
        return ["  CIK 없음"], 0
    fin = B.FIN.get(key) or {}
    all_ends, miss = missing(B, key)
    files = P.six_k(int(cik))
    dom = not files                                     # 6-K 가 없다 = 미국 국내 기업(10-Q·10-K) — 화면에 사업부가 있는 회사의 구멍만 메운다
    ufiles = []
    if dom:
        import ai_fill as af
        done0 = set(((db["stocks"].get(key) or {}).get("q") or {}))
        miss = sorted((e for e in af.gaps(B, key) if e not in done0), reverse=True)
        if not miss:
            return ["  빈칸 없음"], 0
        ufiles = [f for f in af.us_filings(int(cik)) if f[1] in ("10-Q", "10-K")]
        if not ufiles:
            return ["  10-Q·10-K 없음"], 0
    elif len(miss) < 4:
        return [f"  사업부 빈 분기 {len(miss)} — 할 일 없음"], 0
    names = (B.SEG.get(key) or {}).get("names") or []
    pr_q = {}
    if (HERE / "data" / "fpi_pr.json").exists():
        pr_q = ((json.loads((HERE / "data" / "fpi_pr.json").read_text(encoding="utf-8")).get("stocks") or {}).get(key) or {}).get("q") or {}
    mine_before = set(((db["stocks"].get(key) or {}).get("q") or {}))
    anc = {}                                            # 닻: 화면에 이미 있는 분기(이 수집기가 넣은 것 제외) + 장마다 읽어 검산한 값
    for r in (B.SEG.get(key) or {}).get("pts") or []:
        if re.match(r"\d{4}-\d\d-\d\d$", str(r[0])) and str(r[0]) not in mine_before and any(v for v in r[1:]):
            anc[str(r[0])] = {n: v for n, v in zip(names, r[1:]) if v}
    for e, lst in pr_q.items():
        x = next((r for r in reversed(lst) if r.get("seg")), None)
        if x:
            anc[e] = x["seg"]
    out, total = [], 0
    got_all = dict((db["stocks"].get(key) or {}).get("q") or {})
    basis = ""
    for ci in range(0, min(len(miss), 2 * CHUNK), CHUNK):
        chunk = miss[ci:ci + CHUNK]
        if time.time() - T0 > SECS or spent() >= MAX_TOK:
            out.append("  상한 — 여기서 멈춤")
            break
        # 닻 분기: 이 묶음과 가장 가까운 것 셋(값을 프롬프트에 주고, 그 분기 공시도 둘까지 같이 싣는다)
        src = anc or {e: x["seg"] for e, x in got_all.items()}      # 가진 값이 없으면 앞 묶음의 제 답(같은 기준으로 잇게)
        mid = chunk[len(chunk) // 2]
        pick = sorted(sorted(src, key=lambda a_: abs((_d(a_) - _d(mid)).days))[:3])
        ends = sorted(set(chunk) | set(pick[:2]))
        docs, t0 = [], time.time()
        for e in ends:
            if dom:                                      # 그 날 끝난 10-Q(석 달 칸) 또는 10-K(연간 칸 — 결산 분기는 코드가 뺀다)
                import ai_fill as af
                f = next((f for f in ufiles if abs((_d(f[0]) - _d(e)).days) <= 12), None)
                if f and not any(f[2] == a for _f, a, _t in docs):
                    raw_ = af.sec(af.DOC.format(cik=int(cik), acc=f[2], name=f[3]), binary=True)
                    txt = seg_text([raw_.decode("utf-8", "replace")], annual=f[1] == "10-K") if raw_ else ""
                    if txt:
                        head = (f"ANNUAL REPORT (10-K), fiscal year ended {f[0]} — twelve-month columns" if f[1] == "10-K"
                                else f"quarterly report (10-Q), quarter ended {f[0]}")
                        docs.append((f[0] + " " + head, f[2], txt))
                continue
            for fd, acc in [f for f in files if 10 <= (_d(f[0]) - _d(e)).days <= 100][:10]:
                if any(acc == a for _f, a, _t in docs):
                    break
                txt = filing_text(int(cik), acc)
                if txt:
                    docs.append((fd, acc, txt))
                    break
        segdocs = sum(1 for _f, _a, t in docs if SEGWORD.search(t) or "[before table:" in t and t.count("--- table ---") >= 2)
        out.append(f"  묶음 {ci // CHUNK + 1}: 빈 분기 {len(chunk)}({chunk[-1]}~{chunk[0]}) · 공시 {len(docs)}장(부문 표 {segdocs}장) · "
                   f"글 {sum(len(t) for _f, _a, t in docs):,}자 · 고르는 데 {time.time() - t0:.0f}초")
        if (not docs or not segdocs) if dom else (len(docs) < 3 or segdocs < max(2, len(docs) // 3)):
            out.append("  부문 표가 든 공시가 모자라다(단일 사업부이거나 실적 공시가 6-K 에 없다) — 안 묻는다(토큰 0)")
            if ci == 0:
                with LOCK:
                    db.setdefault("none", {})[key] = date.today().isoformat()
            break
        docs.sort()
        body = "\n\n".join(f"### FILING filed {fd}\n{txt}" for fd, _a, txt in docs)
        anchors = ""
        if pick:
            anchors = "VERIFIED VALUES we already hold (absolute currency units) — your series must reproduce these:\n" + "\n".join(
                f"  {e}: " + ", ".join(f"{n} {v:,.0f}" for n, v in src[e].items()) for e in pick) + "\n\n"
        if dom:
            anchors += ("ANNUAL REPORTS: for a filing marked ANNUAL REPORT give the TWELVE-month segment revenues of that fiscal year under "
                        '"y": {"<fiscal year end YYYY-MM-DD>": {"seg": {...}, "elim": ...}} (same names, same measure). '
                        'Do not put twelve-month values under "q".\n\n')
        prompt = PROMPT.format(anchors=anchors, code=code, cur=fin.get("cur") or "USD", segs=json.dumps(names[:12], ensure_ascii=False),
                               n=len(docs), a=ends[0], z=ends[-1], docs=body[:MAX_PROMPT])
        raw = ask(prompt)
        mm = re.search(r"\{.*\}", raw or "", re.S)
        try:
            ans = json.loads(mm.group(0)) if mm else {}
        except ValueError:
            out.append("  답이 JSON 이 아니다: " + (raw or "")[:200])
            break
        ok, rep = check(B, key, ans, pr_q, all_ends, mine_before)
        ok = {e: x for e, x in ok.items() if e in chunk}             # 닻 분기는 이미 가진 값 — 다시 담지 않는다
        if dom:                                          # 결산 분기 = 연간 − 앞 세 분기(이 답 또는 화면) — 합÷매출이 그 세 분기의 비율과 4% 안일 때만
            have = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}
            scr = {str(r[0]): {B.seg_key(n): v for n, v in zip(names, r[1:]) if v} for r in (B.SEG.get(key) or {}).get("pts") or []
                   if re.match(r"\d{4}-\d\d-\d\d$", str(r[0]))}
            u_ = float(ans.get("u") or 1)
            for fe, x in (ans.get("y") or {}).items():
                e4 = next((e for e in chunk if abs((_d(e) - _d(fe)).days) <= 12), None) if re.match(r"\d{4}-\d\d-\d\d$", str(fe)) else None
                yr = {B.seg_key(n): (n, float(v) * u_) for n, v in ((x or {}).get("seg") or {}).items() if isinstance(v, (int, float)) and v}
                if not e4 or e4 in ok or len(yr) < 2:
                    continue
                prev, rats = [], []
                for d0 in sorted(all_ends):
                    if 60 <= (_d(e4) - _d(d0)).days <= 300:
                        row = {B.seg_key(n): v for n, v in (ok.get(d0) or {}).get("seg", {}).items()} or \
                            next((v for dd, v in scr.items() if abs((_d(dd) - _d(d0)).days) <= 20), None)
                        hv = next((have[h] for h in have if abs((_d(h) - _d(d0)).days) <= 20), None)
                        if row and hv:
                            prev.append(row)
                            rats.append(sum(row.values()) / hv)
                hv4 = next((have[h] for h in have if abs((_d(h) - _d(e4)).days) <= 20), None)
                if len(prev) != 3 or not hv4 or any(set(yr) != set(r) for r in prev):
                    out.append(f"    {e4} ✗ 연간 − 앞 세 분기: 앞 분기 {len(prev)}칸 또는 이름이 다르다")
                    continue
                q4 = {yr[k_][0]: yr[k_][1] - sum(r[k_] for r in prev) for k_ in yr}
                tot = sum(q4.values())
                r4, mid = tot / hv4, sorted(rats)[1]
                good = abs(r4 / mid - 1) <= 0.04 and all(v > -0.02 * abs(tot) for v in q4.values())
                out.append(f"    {e4} {'✓' if good else '✗'} 연간 − 앞 세 분기: 합÷매출 {r4:.2f}(앞 분기 {mid:.2f}) · " +
                           " / ".join(f"{n} {round(v / 1e6):,}" for n, v in q4.items())[:150])
                if good:
                    ok[e4] = {"seg": q4, "elim": 0.0, "src": fe, "grade": "연간−3분기"}
        basis = basis or str(ans.get("basis"))[:200]
        out.append(f"  닻 {pick} · 기준: {str(ans.get('basis'))[:140]}")
        out += [r for r in rep if r[4:14] in chunk]
        out.append(f"  → 통과 {len(ok)}/{len(chunk)}분기")
        got_all.update(ok)
        total += len(ok)
        with LOCK:
            db["stocks"][key] = {"cur": fin.get("cur") or "USD", "basis": basis, "q": got_all, "ts": date.today().isoformat(),
                                 **({"fill": 1} if dom else {})}
        if len(ok) < 4 and not dom:
            break                                         # 첫 묶음이 안 되면 옛 분기는 안 묻는다
    return out, total


def week(B, d1, d2, mincap):
    """d1~d2 에 발표하는 미국 상장 외국 기업(10-Q 없음 · 분기 회사 · 은행·리츠·일본 ADR 제외) — 시총 큰 순."""
    import ai_fill as af
    import ai_fpi as P
    from markets import excluded
    af.targets(B, set())                                 # af.CAPS 를 채운다
    try:
        raw_us = json.loads((HERE / "data" / "financials.json").read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        raw_us = {}
    codes = set()
    for name in ("earnings_us.json", "earnings_us_fpi.json"):
        try:
            rows = json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
        except (OSError, ValueError):
            rows = []
        codes |= {r["code"] for r in rows if isinstance(r, dict) and d1 <= str(r.get("date")) <= d2}
    out = []
    for c in codes:
        k = "us:" + c
        f = B.FIN.get(k)
        if not f or not P.is_fpi(c, raw_us) or c in P.JP_ADR or P.is_half(B, f) or (af.CAPS.get(k) or 0) < mincap:
            continue
        if excluded("us", "", B.SECTORS.get(k, ""), (B.INDUSTRIES.get(k, "") or "").replace("-", "")):
            continue
        if len(missing(B, k)[1]) >= 4:
            out.append((-(af.CAPS.get(k) or 0), k))
    return [k for _c, k in sorted(out)], len(codes)


def main():
    args = sys.argv[1:]
    keys = [a for a in args if ":" in a]
    import ai_seg
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {"v": 1, "stocks": {}}
    db.setdefault("stocks", {})
    import build as B
    import scrape_live_us as lu
    lu.cik_map_cached()
    if "--usdays" in args:                               # 그 사흘에 발표하는 미국 국내 기업 중 사업부 구멍이 있는 곳
        import ai_fill as af
        i = args.index("--usdays")
        ks = set()
        for name in ("earnings_us.json", "earnings_us_fpi.json"):
            try:
                rows = json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
            except (OSError, ValueError):
                rows = []
            ks |= {"us:" + r["code"] for r in rows if isinstance(r, dict) and args[i + 1] <= str(r.get("date")) <= args[i + 2]}
        tg = af.targets(B, ks)
        keys = [k for k in tg if k.startswith("us:") and
                [e for e in af.gaps(B, k) if e not in ((db["stocks"].get(k) or {}).get("q") or {})]]
        print(f"{args[i + 1]}~{args[i + 2]} 미국 발표 회사 중 사업부 구멍이 있는 곳: {len(keys)}")
    if "--days" in args:
        i = args.index("--days")
        mincap = float(os.environ.get("FPI_MIN_CAP", "0.7"))
        keys, n_all = week(B, args[i + 1], args[i + 2], mincap)
        skip = [k for k in keys if k in (db.get("none") or {})]
        keys = [k for k in keys if k not in skip]
        print(f"{args[i + 1]}~{args[i + 2]} 미국 발표 {n_all:,}곳 중 외국 기업·시총 {mincap}B$↑·사업부 빈 분기 4↑: {len(keys)}곳"
              f"(전에 '부문 표 없음'으로 적어 둔 {len(skip)}곳 제외)")
    if not keys:
        print("종목을 줄 것 — python ai_fpi_co.py us:SU us:CNQ  또는  --days 2026-11-11 2026-11-13")
        return
    res = {}

    def run(key):
        if time.time() - T0 > SECS or spent() >= MAX_TOK:
            return
        try:
            lines, n = one(B, key, db)
        except Exception as ex:                          # noqa: BLE001
            lines, n = [f"  실패 {ex!r}"[:300]], 0
        with LOCK:
            res[key] = n
            print(f"== {key}")
            for line in lines:
                print(line)
            db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            sys.stdout.flush()

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=int(os.environ.get("FPICO_PAR", "5"))) as ex:
        list(ex.map(run, keys))
    u = ai_seg.USED
    filled = [k for k, n in res.items() if n >= 4]
    print(f"요약: 본 회사 {len(res)}/{len(keys)} · 사업부가 찬 회사(4분기↑) {len(filled)} · 통과 분기 {sum(res.values())} · "
          f"토큰 호출 {u['n']} · 입력 {u['in']:,} · 출력 {u['out']:,}")
    print("찬 회사: " + " ".join(f"{k[3:]}({res[k]})" for k in filled))
    print("안 찬 회사: " + " ".join(k[3:] for k in res if k not in filled))


if __name__ == "__main__":
    main()
