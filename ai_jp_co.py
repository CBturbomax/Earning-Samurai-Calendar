"""일본 사업부 빈칸 — **회사 하나를 한 번에** 읽는다(11/10 밤, ai_fpi_co.py 의 일본판) → data/fpi_co.json(같은 파일 · fill=1).

왜 새로 썼나: ai_fill.py(공시 한 장 = 한 번 묻기)를 이번 주 발표 138곳에 돌렸더니 100만 토큰에 '검산 통과 137기간'인데 화면 빈칸은
그대로였다. 결산단신의 부문 표는 **누계**라, 빈 분기 하나를 채우려면 그 분기 누계와 앞 분기 누계가 **같은 이름·같은 기준으로** 둘 다
있어야 하는데 한 장씩 따로 물으니 짝이 안 맞았다(연간만 · 반기만 · 이름이 다름).

여기서는
  · 빈 분기마다 '그 분기 결산단신 + 앞 분기 결산단신'을 골라(1분기면 한 장) 세그먼트 표의 **머리줄 + 매출 줄만** 추려 한 프롬프트로,
  · 모델은 표를 **누계 그대로** 옮긴다(계산 금지) — 공시마다 당기·전기 기간을 코드가 요약 XBRL 에서 읽어 알려 준다,
  · 빼기와 검산은 코드가: 누계 합 ÷ 누계 매출, 분기 합 ÷ 분기 매출이 **그 회사 화면의 평소 비율**과 ±12%(또는 총매출과 1%) ·
    음수 부문 없음 · 화면 부문 이름과 절반 이상 겹침. 화면에 이미 있는 분기는 안 건드린다(빈 분기만).

  python ai_jp_co.py jp:1662 jp:2587
  python ai_jp_co.py --days 2026-11-11 2026-11-13
"""
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
FYM = {}                        # 열쇠 -> 결산월(3월기 = 3) — main 이 채운다
IR_TRACE = []                   # IR 자료 찾기 자취(로그용 — 0장일 때 어디서 막혔는지)
NAMES = {}                      # 열쇠 -> 회사 이름(검색에 쓴다) — main 이 채운다
OUT = HERE / "data" / "fpi_co.json"
LOCK = threading.Lock()
MAX_TOK = int(os.environ.get("FPI_TOKENS", "1000000"))
SECS = int(os.environ.get("FPI_SECS", "4500"))
PER_DOC = int(os.environ.get("JPCO_DOC", "2200"))
MAX_DOCS = 8
T0 = time.time()

SEGTAB = re.compile(r"セグメント|部門別|事業別|事業の種類")
REVROW = re.compile(r"外部顧客|売上高|売上収益|営業収益|営業収入|収益|セグメント間|計|調整|連結")

PROMPT = """You are copying numbers from a Japanese company's quarterly earnings releases (決算短信) into JSON.

Company: {code}. Segment names we already use: {segs}. Reuse a name when it is the same segment.
Below are segment-table excerpts from {n} filings. Each starts with "### FILING" and states its periods:
CURRENT = the year-to-date period of that filing, PRIOR = the same period one year earlier.
The segment tables in a filing are YEAR-TO-DATE (cumulative) values for those two periods (当期 = CURRENT, 前期 = PRIOR).

For every filing, and for both CURRENT and PRIOR when printed, give:
- "seg": sales to external customers (外部顧客への売上高 / 外部収益) of each reportable segment. If the table does not split external
  and intersegment sales, use the segment's 売上高 row. Include "その他" if it is a column of the table.
- "adj": the 調整額 column for that same row, if printed (else omit).
- "total": the consolidated total of that row (連結 / 合計) as printed.

Rules: copy numbers exactly as printed (△ or brackets = negative). NEVER compute, subtract or estimate. If a period's table is not
in the excerpt, leave that period out. Use ONE consistent set of segment names for the whole answer: the same segment must
have the identical name in every period (use our existing name if given, otherwise the Japanese name as printed in the latest filing).
Some excerpts are NARRATIVE SENTENCES instead of tables (e.g. 「日本事業の売上収益は1,505億円」): use those numbers for the segment named
in the sentence or its heading; they are cumulative for the filing's CURRENT period unless the sentence says otherwise.
Report EVERY number in million yen and set "u": 1000000 — convert 億円 by ×100 (1,505億円 -> 150500) and 千円 by ÷1000. This unit
conversion is the only arithmetic allowed.

Answer with ONLY JSON:
{{"u": 1000000, "spans": {{"2024-04-01/2024-09-30": {{"seg": {{"日本": 1234, "北米": 567}}, "adj": -12, "total": 1789}}}}}}

{docs}
"""


def _d(s):
    return date.fromisoformat(s)


def seg_excerpt(blob):
    """결산단신 zip -> 세그먼트 표 글(PER_DOC 자) — 표마다 앞글(당기·전기 구분) + 머리줄 넉 줄 + 매출 줄."""
    import geojp
    out, n = [], 0
    for before, rows in geojp.tables(blob):
        body = [" | ".join(c for c in r if c) for r in rows]
        if not SEGTAB.search(before + " ".join(body[:6])) or not re.search(r"売上|収益|営業収入", " ".join(body)):
            continue
        keep = body[:4] + [r for r in body[4:] if REVROW.search(r.split(" | ")[0])][:8]
        txt = "[" + before[-110:] + "]\n" + "\n".join(r[:420] for r in keep)
        if n + len(txt) > PER_DOC:
            continue
        out.append(txt)
        n += len(txt)
    return "\n--- table ---\n".join(out)


def page_texts(pdf, last=45):
    """PDF -> 쪽마다의 글(칸 모양을 살려). pdftotext 가 있으면 그것, 없으면 pdfplumber(pip — 러너의 apt 가 막힌 날에도 된다. 11/12)."""
    import shutil
    import subprocess
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", "-l", str(last), "-", "-"], input=pdf, capture_output=True, timeout=90)
        return r.stdout.decode("utf-8", "replace").split("\f")
    import io
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        for pg in doc.pages[:last]:
            try:
                out.append(pg.extract_text(layout=True) or "")
            except Exception:                          # noqa: BLE001
                out.append("")
    return out


def pdf_tool():
    import shutil
    if shutil.which("pdftotext"):
        return "pdftotext"
    try:
        import pdfplumber  # noqa: F401
        return "pdfplumber"
    except ImportError:
        return ""


def pdf_pages(code, fn, pn):
    """결산단신 PDF 에서 **세그먼트 정보가 실린 쪽을 통째로**(pdftotext -layout — 칸 모양이 살아 있다) 둘까지.
    11/11: 줄 단위로 '外部顧客' 을 찾던 pdf_excerpt 는 공시 342장을 '표없음'으로 놓쳤다. 웹 검색으로 떠보니 숫자는 기사에 없고
    바로 이 PDF 안에 있었다 — 표를 우리가 가르지 말고 쪽째 넘겨 모델이 읽게 한다."""
    import subprocess
    import ai_fill as af
    import scrape_geo_jp as G
    pn = pn if pn.lower().endswith(".pdf") else (fn.replace("0812", "1401", 1) if fn.startswith("0812") else "")
    if not pn:
        return ""
    try:
        pdf = af._patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{pn}", timeout=60))
        if not pdf:
            return ""
        pages = page_texts(pdf, 45)
    except Exception:                                  # noqa: BLE001 — pdftotext 없음·암호 PDF
        return ""
    scored = []
    for i, pg in enumerate(pages):
        flat = re.sub(r"[ \u3000]", "", pg)
        nums = len(re.findall(r"(?<![\d.])\d[\d,]*(?![\d.]*[%％])", pg))
        if not re.search(r"セグメント", flat) or nums < 12:
            continue                                     # 억 엔 단위 표는 쉼표 없는 숫자다(삿포로 2501: 512 · 126) — 쉼표가 아니라 숫자 수로 본다
        # IFRS 회사는 '外部顧客' 대신 '外部収益·外部売上·セグメント収益' 이라 쓴다(11/12 아사히 2501 — 세그먼트 주석 쪽이 점수 1 로 떨어졌다)
        sc = (3 if re.search(r"外部顧客|外部収益|外部売上|外部からの", flat) else 0) + (2 if re.search(r"報告セグメント|セグメント収益|セグメント売上", flat) else 0) + \
             (1 if re.search(r"セグメント情報", flat) else 0) + \
             (1 if re.search(r"売上(高|収益)|営業収益", flat) and nums >= 20 else 0)
        if sc >= 2:
            scored.append((-sc, i))
    out = []
    # 세그먼트 주석은 '전기 표 → 당기 표' 순으로 두세 쪽에 걸친다. 점수 높은 두 쪽만 주면 전기 표만 가고 **당기 표가 빠졌다**(11/11 추적:
    # '그 분기 누계가 없다' 34칸의 태반). 가장 점수 높은 쪽에서 시작해 이어지는 쪽을 넷까지 싣는다.
    idx = sorted(i for _sc, i in scored)
    if idx:
        best = min(scored)[1]
        run = [i for i in idx if best - 1 <= i <= best + 2][:3]
    else:
        run = []
    for i in run:
        pg = "\n".join(re.sub(r" {3,}", "   ", ln.rstrip()) for ln in pages[i].splitlines() if ln.strip())
        # 쪽을 자르지 않는다(11/11: 한 쪽에 전기·당기 표가 같이 있으면 2,800자에서 당기 표나 전기 표가 잘렸다 — 3769·2587 의 전기 누계가 안 왔다).
        # 대신 숫자·세그먼트 낱말이 없는 긴 글줄(주석 문단)만 줄인다.
        lines_ = [ln if (re.search(r"\d", ln) or len(ln) < 60) else ln[:60] + "…" for ln in pg.splitlines()]
        out.append(f"[PDF page {i + 1}]\n" + "\n".join(lines_)[:6000])
    if not out:
        out = narrative(pages)
    if not out and OMIT_RE.search(re.sub(r"[ \u3000\n]", "", "".join(pages[:45]))):
        OMIT.add((code, pn))                             # 이 공시는 '단일 세그먼트라 기재를 생략한다'고 적었다 — 채울 값이 없다
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "\n--- page ---\n".join(out))


# **채울 수 없는 칸은 다시 안 본다**(회원님 11/12: "사업부 안 내는 회사는 다시는 검토 안 하게 하면 줄어드는 거 아니야?").
# 그 분기 결산단신이 스스로 「単一セグメントであるため記載を省略」 라고 적었고 표도 문장도 없으면, 그 분기를 fpi_co.json 의 `skipq` 에 적어
# 대기줄(all_holes)에서 영영 뺀다. 회사가 나중에 사업부를 내기 시작하면 그 뒤 분기는 그대로 본다(분기 단위로 적는다).
OMIT_RE = re.compile(r"単一セグメント|単一の(報告)?セグメント|セグメント情報(等)?の?記載を省略|セグメント情報(等)?は?、?[^。]{0,40}省略")
OMIT = set()                    # (코드, PDF 이름) — pdf_pages 가 채운다
SKIPQ = {}                      # 열쇠 -> {종료일} — main 이 파일에서 읽어 채운다(all_holes 가 뺀다)
SEG_SENT = re.compile(r"(売上高|売上収益|営業収益|営業収入|収益)は?[、,]?[^。]{0,40}?[\d,，.]+\s*(億|百万|千)円")


def narrative(pages):
    """**표가 없고 문장으로만 부문 매출을 적는 결산단신**(11/12 — 네트워크를 열고 산토리BF 2018년 1분기 단신을 직접 열어 보니 세그먼트 표는 없고
    본문에 「日本事業の売上収益は1,505億円」「欧州事業…518億円」이라고 적혀 있었다. '표없음'으로 넘기던 공시의 태반이 이 꼴이다).
    「経営成績に関する説明」 쪽에서 부문 머리말과 매출 문장만 추려 넘긴다 -> [쪽 글]"""
    out = []
    for i, pg in enumerate(pages[:12]):
        flat = re.sub(r"[ \u3000\n]", "", pg)
        hits = SEG_SENT.findall(flat)
        if len(hits) < 3 or not re.search(r"セグメント|事業|部門", flat):     # 낱말 'セグメント' 는 앞 쪽에만 있고 문장은 다음 쪽에 이어지는 공시가 있다(7157)
            continue
        keep = []
        for sent in re.split(r"(?<=。)", flat):
            head = re.findall(r"[\[［【<＜〔][^\]］】>＞〕。]{2,20}[\]］】>＞〕]", sent)
            if SEG_SENT.search(sent):
                keep.append((" ".join(head[-1:]) + " " if head else "") + sent[-170:])
            elif head and len(sent) < 60:
                keep.append(sent)
        if len(keep) >= 3:
            out.append(f"[PDF page {i + 1} — narrative sentences, not a table]\n" + "\n".join(keep)[:3500])
        if len(out) >= 3:
            break
    return out


def pdf_excerpt(code, fn, pn):
    """zip 에 첨부가 없던 시절(대략 2022년 이전)과 미국 기준 회사 — 결산단신 PDF 에서 '外部顧客への売上高' 줄과 그 위 머리줄을 추린다."""
    import ai_fill as af
    import scrape_geo_jp as G
    pn = pn if pn.lower().endswith(".pdf") else (fn.replace("0812", "1401", 1) if fn.startswith("0812") else "")
    if not pn:
        return ""
    try:
        import pdftext
        pdf = af._patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{pn}", timeout=60))
        lines = pdftext.extract_cells(pdf, max_pages=40) if pdf else []
        rows = [" | ".join(re.sub(r"\s+", " ", t).strip() for _x, t in sorted(c) if t.strip()) for c in lines]
        if not rows and pdf:
            rows = pdftext.layout_lines(pdf, 40)
    except Exception:                                  # noqa: BLE001 — 암호·깨진 PDF
        return ""
    hits = [i for i, r in enumerate(rows) if re.search(r"外部顧客|セグメント間", r) and re.search(r"\d{2,}", r)]
    keep = set()
    for i in hits:
        keep.update(range(max(0, i - 7), min(len(rows), i + 3)))
        for j in range(max(0, i - 16), i):               # 기간 줄(自 20xx年 … 至 …)
            if re.search(r"自.{0,12}年|累計期間|会計年度", rows[j]):
                keep.add(j)
    out, n, last = [], 0, -9
    for i in sorted(keep):
        r = rows[i][:420]
        if n + len(r) > PER_DOC:
            break
        out.append(("--- table ---\n" if i - last > 1 and out else "") + r)
        n += len(r)
        last = i
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "\n".join(out))


def ir_pages(code, name, ends):
    """**회사 IR 자료를 웹에서 찾아 우리가 직접 내려받는다**(회원님 11/12: "수단과 방법을 가리지 말고 다 동원").
    결산단신에 그 분기 세그먼트 표가 없을 때의 다음 수단 — 決算説明資料·決算補足資料·四半期報告書 PDF 에는 분기 부문 매출이 실려 있는 일이 많다.
    모델의 열람 도구(WebFetch)는 일본 IR 사이트에서 403 을 받으므로(11/11 실측) **검색으로 주소만 받고**(WebSearch) 내려받기·글 펴기는 러너가 한다.
    -> [(주소, 쪽 글)]"""
    import subprocess
    import tempfile
    import urllib.request
    q = ("Find direct PDF URLs of this Japanese listed company's own investor-relations documents that print QUARTERLY segment sales "
         "(セグメント別売上高) for the periods listed. Good documents: 決算説明資料, 決算補足資料 / データブック / ファクトブック, 四半期報告書, 決算短信. "
         f"Company: {name} (TSE code {code}). Quarter ends needed: {', '.join(sorted(ends))}.\n"
         "Use web search only. Prefer PDFs on the company's own site or its IR host (eir-parts.net, xj-storage.jp, irpocket.com, pronexus). "
         'Answer with ONLY JSON: {"docs": [{"url": "https://...pdf", "covers": "2018-06-30", "kind": "決算説明資料"}]} — at most 8 URLs, '
         "direct .pdf links only, no search-result pages.")
    cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "12", "--tools", "WebSearch", "--allowedTools", "WebSearch",
           "--system-prompt", "You find primary-source documents with web search and reply with JSON only.",
           "--strict-mcp-config", "--disable-slash-commands"]
    if os.environ.get("AI_MODEL"):
        cmd += ["--model", os.environ["AI_MODEL"]]
    import ai_seg
    try:
        r = subprocess.run(cmd, input=q, cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=600)
        j = json.loads(r.stdout)
        u = j.get("usage") or {}
        with LOCK:
            ai_seg.USED["in"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            ai_seg.USED["out"] += u.get("output_tokens") or 0
            ai_seg.USED["n"] += 1
        mm = re.search(r"\{.*\}", j.get("result") or "", re.S)
        docs = (json.loads(mm.group(0)).get("docs") or []) if mm else []
    except Exception as ex:                            # noqa: BLE001
        IR_TRACE.append(f"검색 실패 {ex!r}"[:160])
        return []
    IR_TRACE.append(f"검색이 준 주소 {len(docs)}: " + " ".join(str((d or {}).get('url'))[-50:] for d in docs[:8]))
    out = []
    for d in docs[:8]:
        url = str((d or {}).get("url") or "")
        if not url.lower().startswith("http") or ".pdf" not in url.lower():
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                                                       "Accept-Language": "ja,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                pdf = resp.read(30_000_000)
            if not pdf.startswith(b"%PDF"):
                IR_TRACE.append(f"PDF 아님 {url[-40:]}")
                continue
            pages = page_texts(pdf, 80)
        except Exception as ex:                        # noqa: BLE001 — 403·시간 초과·깨진 PDF
            IR_TRACE.append(f"내려받기 실패 {url[-40:]} {ex!r}"[:150])
            continue
        scored = []
        for i, pg in enumerate(pages):
            flat = re.sub(r"[ \u3000]", "", pg)
            if not re.search(r"セグメント|事業別|部門別|事業区分", flat) or not re.search(r"売上|収益", flat) or len(re.findall(r"\d{1,3},\d{3}|\d{3,}", pg)) < 6:
                continue
            sc = (2 if re.search(r"四半期|[1-4]Q|Q[1-4]|3ヶ月|３ヶ月|3か月", flat) else 0) + (1 if re.search(r"セグメント", flat) else 0)
            scored.append((-sc, i))
        txt = []
        for _sc, i in sorted(sorted(scored)[:3], key=lambda t: t[1]):
            pg = "\n".join(re.sub(r" {3,}", "   ", ln.rstrip()) for ln in pages[i].splitlines() if ln.strip())
            txt.append(f"[PDF page {i + 1}]\n" + pg[:2600])
        IR_TRACE.append(f"{url[-40:]} 쪽 {len(pages)} · 세그먼트 쪽 {len(scored)}")
        if txt:
            out.append((url, re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "\n--- page ---\n".join(txt))))
        if len(out) >= 4:
            break
    return out


def ask(prompt):
    import ai_fpi_co as C
    return C.ask(prompt)


def base_ratio(B, key):
    """그 회사 화면의 평소 '부문 합 ÷ 분기 매출'(가운뎃값) — 새로 넣는 칸의 잣대."""
    seg, fin = B.SEG.get(key) or {}, B.FIN.get(key) or {}
    rev = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}
    rs = []
    for r in seg.get("pts") or []:
        if not re.match(r"\d{4}-\d\d-\d\d$", str(r[0])):
            continue
        near = min(rev, key=lambda e: abs((_d(e) - _d(r[0])).days), default=None)
        s = sum(v for v in r[1:] if v)
        if near and abs((_d(near) - _d(r[0])).days) <= 20 and s > 0:
            rs.append(s / rev[near])
    rs = sorted(x for x in rs if 0.5 <= x <= 3)
    return rs[len(rs) // 2] if len(rs) >= 4 else None


def fits(r, base):
    return abs(r - 1) <= 0.012 or (base is not None and abs(r / base - 1) <= 0.12)


def all_holes(B, key):
    """**사업부가 빈 분기 전부**(2018년부터 · 총매출이 있는 분기) — 최근 것부터. 회원님 11/12: "모든 빈칸 다 채운다 생각하라".
    af.gaps 는 '기록이 시작된 뒤의 구멍'만 세지만 여기서는 기록 시작 전과 사업부가 아예 없는 회사까지 본다. 막 끝난 분기(45일)는 뺀다."""
    fin, seg = B.FIN.get(key) or {}, B.SEG.get(key) or {}
    have = [str(r[0]) for r in seg.get("pts") or [] if any(v for v in r[1:]) and re.match(r"\d{4}-\d\d-\d\d$", str(r[0]))]
    fresh = date.fromordinal(date.today().toordinal() - 45).isoformat()
    ends = [p["end"] for p in fin.get("points") or [] if p.get("end") and p.get("rev") and "2018-03-01" <= p["end"] <= fresh]
    skip = SKIPQ.get(key) or ()
    return sorted((e for e in ends if e not in skip and not any(abs((_d(h) - _d(e)).days) <= 20 for h in have)), reverse=True)


def one(B, key, db, verbose=False, tried=None):
    import ai_fill as af
    import scrape_fin_jp as F
    import scrape_geo_jp as G
    code = key.split(":")[1]
    fin, seg = B.FIN.get(key) or {}, B.SEG.get(key) or {}
    done = {e for e, x in ((db["stocks"].get(key) or {}).get("q") or {}).items() if not x.get("uv")}          # 앞 실행이 채운 칸(af.gaps 는 마지막 정식 빌드 기준이라 아직 빈칸으로 센다)
    tried = tried if tried is not None else set()
    holes = [e for e in all_holes(B, key) if e not in done and e not in tried][:4]      # 한 번에 넷씩(공시 여덟 장) — 부른 쪽이 묶음을 돌린다
    tried.update(holes)
    if not holes:
        return ["  빈칸 없음"], 0
    base = base_ratio(B, key)
    names = seg.get("names") or []
    docs = af.jp_docs(code)
    if not docs:
        return ["  결산단신 목록이 비었다"], 0
    rev = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}

    def doc_for(e):
        c = [x for x in docs if 10 <= (_d(x[0]) - _d(e)).days <= 75]
        return c[0] if c else None
    trace = []
    # **첨부 데이터(XBRL)가 붙은 공시 목록에 없는 옛 결산단신 PDF**(11/12 추적: 2018~2020년 구멍의 공시가 '공시없음·표없음'이었는데 거래소 보관소에는
    # PDF 가 그대로 있었다 — 웹 검색이 가리킨 곳이 바로 거기였다). 전체 공시 목록에서 결산단신 PDF 를 날짜로 찾아 쪽째 읽는다.
    pdfs = []
    try:
        with G._YANO:
            time.sleep(0.4)
        jl = json.loads(af._patient(lambda: G.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=1000", binary=False,
                                                  timeout=30)) or "{}")
        for it in (jl.get("items") or []):
            it = it.get("Tdnet", it)
            du, ti = it.get("document_url") or "", it.get("title") or ""
            if du.lower().endswith(".pdf") and F.TANSHIN.search(ti) and not F.NOT_TANSHIN.search(ti):
                pdfs.append(((it.get("pubdate") or "")[:10], du.rsplit("/", 1)[-1]))
    except Exception:                                  # noqa: BLE001
        pdfs = []
    pdfs.sort()

    def fy_start(ee):
        """그 분기가 든 회계연도의 첫날 — 요약 XBRL 에서 본 시작일의 달, 없으면 결산월(FYM)에서."""
        sm = next((int(s_[0][5:7]) for s_ in spans), None) or ((FYM.get(key) or 3) % 12 + 1)
        d = _d(ee)
        st = date(d.year, sm, 1)
        if st > d:
            st = date(d.year - 1, sm, 1)
        return st.isoformat()
    picked, spans, t0 = {}, {}, time.time()            # zip 이름 -> (공시일, 글, 그 공시의 기간들)
    hit_ee = set()
    omit_q = set()                                       # 제 공시가 '기재 생략'이라고 한 분기(종료일)

    def yb(d_, n=-1):
        return _d(d_).replace(year=_d(d_).year + n, day=min(_d(d_).day, 28)).isoformat()

    def from_listed(base):
        """XBRL 목록의 공시(zip 첨부 표 → PDF 쪽·문장 → PDF 줄) -> (이름, 공시일, 글, 기간들) 또는 None"""
        x = doc_for(base)
        if not x:
            return None, "공시없음"
        if x[1] in picked:
            return None, "이미"
        blob = af._patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{x[1]}", timeout=60))
        if not blob:
            return None, "못받음"
        sp = G.spans_of(F.read_summary(blob))
        if sp:
            spans.update(sp)
        pn = x[2] or next((p_[1] for p_ in pdfs if p_[0] == x[0]), "")       # 짝 PDF 이름 — 목록이 못 줬으면 같은 날 결산단신 PDF
        txt, how = seg_excerpt(blob), "zip"
        if not txt:
            txt, how = pdf_pages(code, x[1], pn), "pdf쪽"
        if not txt:
            txt, how = pdf_excerpt(code, x[1], pn), "pdf"
        if txt and sp:
            return (x[1], x[0], txt, sorted(sp)), how
        if pn and (code, pn) in OMIT:
            omit_q.add(base)
            return None, "생략"
        return None, "표없음" if sp else "표없음·기간없음"

    def from_old_pdf(base):
        """XBRL 목록에 없는 옛 결산단신 PDF(표 쪽 또는 문장) — 기간은 결산월로 셈한다."""
        c = [x for x in pdfs if 10 <= (_d(x[0]) - _d(base)).days <= 75 and x[1] not in picked]
        if not c:
            return None, "옛pdf없음"
        txt = pdf_pages(code, "", c[0][1])
        if not txt:
            if (code, c[0][1]) in OMIT:
                omit_q.add(base)
                return None, "옛pdf·생략"
            return None, "옛pdf·표없음"
        st = fy_start(base)
        return (c[0][1], c[0][0], txt, sorted([(st, base), (yb(st), yb(base))])), "옛pdf"

    for e in holes:
        import ai_fpi as P
        for ee in (e, P._back(_d(e), 3).isoformat()):
            # 차례(11/12): 그 분기 공시(목록) → 그 분기의 옛 PDF(표가 없으면 **문장**) → 한 해 뒤 공시의 전기 칸 → 한 해 뒤 옛 PDF.
            # 예전에는 '한 해 뒤 공시'가 먼저 잡히면 그 분기 제 공시를 안 열었는데, 전기 표가 없는 회사(3769)는 그 길로 영영 못 얻었다.
            if len(picked) >= MAX_DOCS:
                break
            if ee in hit_ee:
                continue
            for tag, fn_, bd in (("", from_listed, ee), ("", from_old_pdf, ee), ("+1y", from_listed, yb(ee, 1)), ("+1y", from_old_pdf, yb(ee, 1))):
                got_, how = fn_(bd)
                trace.append(f"{ee}{tag}:{how}")
                if got_:
                    picked[got_[0]] = got_[1:]
                    hit_ee.add(ee)
                    break
    out = [f"  빈 분기 {len(holes)}({holes[-1]}~{holes[0]}) · 평소 비율 {None if base is None else round(base, 2)} · "
           f"세그먼트 표가 든 공시 {len(picked)}장 · 글 {sum(len(v[1]) for v in picked.values()):,}자 · {time.time() - t0:.0f}초"]
    out.append("  공시 찾기: " + " ".join(trace)[:300])
    irs = []
    if os.environ.get("JPCO_IR", "0") == "1":             # 11/12: 검색이 준 것은 거래소 PDF 였다(위에서 직접 읽는다) — 검색은 기본으로 끈다
        got_end = {s_[1] for v in picked.values() for s_ in v[2]}
        lack = [e for e in holes if not any(abs((_d(e) - _d(g)).days) <= 12 for g in got_end)] or (holes if not picked else [])
        if lack:
            irs = ir_pages(code, NAMES.get(key) or code, lack)
            out.append(f"  IR 자료(웹): 못 구한 분기 {len(lack)} → 내려받은 PDF {len(irs)}장 " + " ".join(u.rsplit('/', 1)[-1][:28] for u, _t in irs))
            out += ["    · " + t for t in IR_TRACE[-10:]]
            del IR_TRACE[:]
    if not picked and not irs:
        gone0 = sorted(e for e in holes if e in omit_q)
        with LOCK:
            db.setdefault("none", {})[key] = date.today().isoformat()
            if gone0:
                db.setdefault("skipq", {})[key] = sorted(set(db.get("skipq", {}).get(key, [])) | set(gone0))
                SKIPQ.setdefault(key, set()).update(gone0)
        if gone0:
            out.append(f"  사업부를 안 내는 분기(공시가 '기재 생략') — 다시 안 본다: {gone0}")
        return out + ["  세그먼트 표를 못 찾았다(결산단신에도 IR 자료에도) — 안 묻는다"], 0
    body = []
    for _fn, (pub, txt, sps) in sorted(picked.items(), key=lambda kv: kv[1][0]):
        cur = max(sps, key=lambda s: s[1])
        pri = [s for s in sps if s != cur and abs((_d(cur[1]) - _d(s[1])).days - 365) <= 6]
        body.append(f"### FILING published {pub} — CURRENT = {cur[0]}/{cur[1]}" + (f" · PRIOR = {pri[0][0]}/{pri[0][1]}" if pri else "") + "\n" + txt)
    for url, txt in irs:
        body.append(f"### FILING (company IR material from the web: {url}) — its periods are NOT given: read each table's period from "
                    "the page itself. Three-month (quarter-only) tables are welcome: report them with their own start and end dates.\n" + txt)
    if verbose:
        out.append("  ---- 첫 공시 발췌 ----\n" + body[0][:1500])
    targets = ("\n\nTARGET QUARTERS (three-month periods we want, by quarter end date): " + ", ".join(sorted(holes)) +
               '\nAfter "spans", also add "q": for each target quarter the THREE-MONTH external sales per segment = cumulative to that '
               "quarter end minus cumulative to the previous quarter end of the same fiscal year (for the first fiscal quarter, the "
               'cumulative itself). Only when both cumulative tables are in the excerpts. Same names as in "spans". '
               'Format: "q": {"2018-09-30": {"seg": {"日本": 12, "北米": 3}}}.')
    raw = ask(PROMPT.format(code=code, segs=json.dumps(names[:14], ensure_ascii=False), n=len(body), docs="\n\n".join(body)) + targets)
    mm = re.search(r"\{.*\}", raw or "", re.S)
    try:
        ans = json.loads(mm.group(0)) if mm else {}
    except ValueError:
        return out + ["  답이 JSON 이 아니다: " + (raw or "")[:200]], 0
    u = float(ans.get("u") or 1)
    cum, raw_keep = {}, {}
    for k_, v_ in (((db["stocks"].get(key) or {}).get("cum")) or {}).items():      # 앞 실행이 읽어 둔 누계 — 다시 묻지 않고 짝으로 쓴다
        s0, e0 = k_.split("/")
        cum[(s0, e0)] = v_
    for k, x in (ans.get("spans") or {}).items():
        try:
            s, e = k.split("/")
            _d(s), _d(e)
        except ValueError:
            continue
        sg = {str(n).strip()[:40]: float(v) * u for n, v in ((x or {}).get("seg") or {}).items() if isinstance(v, (int, float)) and v}
        if len(sg) < 2:
            continue
        crev = next((v for (s2, e2), v in spans.items() if abs((_d(s2) - _d(s)).days) <= 6 and abs((_d(e2) - _d(e)).days) <= 6), None)
        if crev is None:                                # IR 자료에서 읽은 기간 — 우리 분기 매출을 더해 잣대로(그 기간의 분기가 다 있을 때만)
            inq = [v for d0, v in rev.items() if _d(s) < _d(d0) <= _d(e) + (date.resolution * 12)]
            need_n = max(1, round((_d(e) - _d(s)).days / 91.3))
            crev = sum(inq) if len(inq) == need_n else None
        r = sum(sg.values()) / crev if crev else None
        ok = r is not None and fits(r, base)
        out.append(f"    누계 {s}~{e} {'✓' if ok else '✗'} 합÷매출 {None if r is None else round(r, 2)} · " +
                   " / ".join(f"{n} {round(v / 1e8):,}" for n, v in sg.items())[:150])
        if ok:
            cum[(s, e)] = sg
            raw_keep[f"{s}/{e}"] = sg
    def nk(n):                                          # 이름 열쇠 — 띄어쓰기·'事業'·'関連'·'セグメント' 차이를 지운다(11/11: 장마다 조금씩 달리 적혀 21칸이 빼기에서 떨어졌다)
        return B.seg_key(n) or re.sub(r"[\s\u3000・･·\-－—()（）]|事業|関連|セグメント|部門|グループ", "", str(n)).lower()
    okey = {nk(n): n for n in names}
    seen_nm = {}

    def ren(row):                                       # 화면 이름으로(같은 것이면) — 화면에 없으면 이 답에서 처음 나온 표기로
        out_ = {}
        for n, v in row.items():
            k_ = nk(n)
            nm = okey.get(k_) or seen_nm.setdefault(k_, n)
            out_[nm] = out_.get(nm, 0) + v
        return out_
    got, unv = {}, {}

    def loose(q_, r_, over_):
        """**검산은 못 통과했지만 '표시를 달고' 실을 만한가**(회원님 11/11: "일단 실어봐") — 합이 총매출의 0.85~1.15배 · 음수 부문 없음 ·
        화면 이름과 절반↑ 겹침. 화면에는 분기 이름에 '?' 를 붙여 낸다(build 의 FPI_UV). 검산을 통과한 값이 있으면 그쪽이 이긴다."""
        t_ = sum(q_.values())
        return 0.85 <= r_ <= 1.15 and over_ and len(q_) >= 2 and all(v > -0.05 * abs(t_) for v in q_.values())
    for e in holes:
        me = next(((s, en) for (s, en) in cum if abs((_d(en) - _d(e)).days) <= 12), None)
        near = min(rev, key=lambda x: abs((_d(x) - _d(e)).days), default=None)
        if not me or not near or abs((_d(near) - _d(e)).days) > 20:
            out.append(f"    {e} ✗ 그 분기 누계가 없다")
            continue
        months = round((_d(me[1]) - _d(me[0])).days / 30.4)
        tight = False
        if months <= 4:
            q = dict(cum[me])
        else:
            pv = next((k for k in cum if abs((_d(k[0]) - _d(me[0])).days) <= 6 and 75 <= (_d(me[1]) - _d(k[1])).days <= 105), None)
            if pv:
                a, b = ren(cum[me]), ren(cum[pv])
            else:
                # 앞 분기 누계가 없으면 **화면에 이미 있는 그 회계연도 앞 분기들**을 뺀다(같은 이름일 때만 · 검산은 더 좁게 3%)
                a = ren(cum[me])
                prior = [r for r in seg.get("pts") or [] if re.match(r"\d{4}-\d\d-\d\d$", str(r[0]))
                         and _d(me[0]) < _d(r[0]) and (_d(e) - _d(r[0])).days > 60 and any(v for v in r[1:])]
                if len(prior) != round(months / 3) - 1 or not set(a) <= set(names):
                    out.append(f"    {e} ✗ 앞 분기 누계가 없다({months}개월 누계만 · 화면 앞 분기 {len(prior)}칸)")
                    continue
                b = {n: sum((r[1 + names.index(n)] or 0) for r in prior) for n in a}
                tight = True

            if set(a) != set(b):
                out.append(f"    {e} ✗ 두 누계의 부문 이름이 다르다 {sorted(set(a) ^ set(b))[:4]}")
                continue
            q = {n: a[n] - b[n] for n in a}
        q = ren(q)
        tot = sum(q.values())
        r = tot / rev[near]
        over = len([n for n in q if n in names]) * 2 >= len(q) if names else True
        neg = any(v < -0.02 * abs(tot) for v in q.values())
        good = (abs(r - (base or 1)) <= 0.03 if months > 4 and tight else fits(r, base)) and over and not neg
        out.append(f"    {e} {'✓' if good else '✗'} 분기 합÷매출 {r:.2f} · " + " / ".join(f"{n} {round(v / 1e8):,}" for n, v in q.items())[:150] +
                   ("" if good else f"  ← {'이름 안 겹침 ' if not over else ''}{'음수 부문 ' if neg else ''}{'비율' if not fits(r, base) else ''}"))
        if good:
            got[e] = {"seg": {n: v for n, v in q.items() if v}, "grade": "맞아떨어짐" if abs(r - 1) <= 0.012 else "비율 고름"}
        elif loose(q, r, over):
            unv[e] = {"seg": {n: v for n, v in q.items() if v}, "grade": "검산 안 됨", "uv": 1, "r": round(r, 3)}
    # 코드가 못 뺀 칸(두 누계의 이름이 달랐거나 한쪽이 누계 검산에서 떨어진 칸) — **모델이 뺀 값**을 더 좁은 검산으로 받는다:
    # 합이 그 분기 총매출과 1.2% 안(또는 평소 비율 ±3%) · 화면 이름과 절반↑ · 음수 없음.
    for e, x in ((ans.get("q") or {}) if isinstance(ans.get("q"), dict) else {}).items():
        he = next((h for h in holes if h not in got and re.match(r"\d{4}-\d\d-\d\d$", str(e)) and abs((_d(h) - _d(e)).days) <= 12), None)
        sg = {str(n).strip()[:40]: float(v) * u for n, v in ((x or {}).get("seg") or {}).items() if isinstance(v, (int, float)) and v} \
            if isinstance(x, dict) else {}
        near = min(rev, key=lambda y: abs((_d(y) - _d(he)).days), default=None) if he else None
        if not he or len(sg) < 2 or not near or abs((_d(near) - _d(he)).days) > 20:
            continue
        q = ren(sg)
        tot = sum(q.values())
        r = tot / rev[near]
        good = (abs(r - 1) <= 0.012 or (base is not None and abs(r - base) <= 0.03)) and \
            (len([n for n in q if n in names]) * 2 >= len(q) if names else True) and all(v > -0.02 * abs(tot) for v in q.values())
        out.append(f"    {he} {'✓' if good else '✗'} (모델이 뺌) 합÷매출 {r:.2f} · " + " / ".join(f"{n} {round(v / 1e8):,}" for n, v in q.items())[:140])
        if good:
            got[he] = {"seg": {n: v for n, v in q.items() if v}, "grade": "모델이 뺌"}
            unv.pop(he, None)
        elif he not in unv and loose(q, r, len([n for n in q if n in names]) * 2 >= len(q) if names else True):
            unv[he] = {"seg": {n: v for n, v in q.items() if v}, "grade": "검산 안 됨", "uv": 1, "r": round(r, 3)}
    gone = sorted(e for e in holes if e in omit_q and e not in got and e not in unv)
    if gone:
        out.append(f"  사업부를 안 내는 분기(공시가 '기재 생략') — 다시 안 본다: {gone}")
        with LOCK:
            db.setdefault("skipq", {}).setdefault(key, [])
            db["skipq"][key] = sorted(set(db["skipq"][key]) | set(gone))
            SKIPQ.setdefault(key, set()).update(gone)
    unv = {e: x for e, x in unv.items() if e not in got}
    if unv:
        out.append(f"  검산 안 된 채 표시 달고 싣는 칸: {sorted(unv)}")
    if got or raw_keep or unv:
        with LOCK:
            st = db["stocks"].setdefault(key, {"cur": fin.get("cur") or "JPY", "fill": 1, "q": {}})
            st["q"].update(unv)
            st["q"].update(got)
            st.setdefault("cum", {}).update(raw_keep)        # 검산을 통과한 누계 원값 — 규칙을 고치면 AI 없이 다시 뺄 수 있게
            st["ts"] = date.today().isoformat()
    out.append(f"  → 빈 분기 {len(got)}/{len(holes)} 채움")
    return out, len(got)


def mark(B, key, db):
    """**AI 를 안 부르고** 빈 분기의 제 결산단신을 열어 '기재 생략' 문구만 본다 — 채울 수 없는 칸을 skipq 에 적어 대기줄에서 뺀다(토큰 0).
    빈 분기가 많으면 처음·가운데·끝 여섯 칸만 열어 보고, 본 칸이 전부 생략이면 그 회사의 빈 분기를 통째로 적는다(단일 사업부 회사)."""
    import ai_fill as af
    import scrape_fin_jp as F
    import scrape_geo_jp as G
    code = key.split(":")[1]
    holes = all_holes(B, key)
    if not holes:
        return 0, 0
    with G._YANO:
        time.sleep(0.5)
    try:
        jl = json.loads(af._patient(lambda: G.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=1000", binary=False,
                                                  timeout=30)) or "{}")
    except Exception:                                  # noqa: BLE001
        return 0, 0
    pdfs = []
    for it in (jl.get("items") or []):
        it = it.get("Tdnet", it)
        du, ti = it.get("document_url") or "", it.get("title") or ""
        if du.lower().endswith(".pdf") and F.TANSHIN.search(ti) and not F.NOT_TANSHIN.search(ti):
            pdfs.append(((it.get("pubdate") or "")[:10], du.rsplit("/", 1)[-1]))
    pdfs.sort()
    n = len(holes)
    pick = holes if n <= 6 else [holes[i] for i in sorted({0, 1, n // 3, n // 2, (2 * n) // 3, n - 1})]
    seen, om = 0, []
    for e in pick:
        c = [x for x in pdfs if 10 <= (_d(x[0]) - _d(e)).days <= 75]
        if not c:
            continue
        try:
            txt = pdf_pages(code, "", c[0][1])
        except Exception:                              # noqa: BLE001
            continue
        seen += 1
        if not txt and (code, c[0][1]) in OMIT:
            om.append(e)
    gone = holes if (seen >= 2 and len(om) == seen and n > 6) else om
    if gone:
        with LOCK:
            db.setdefault("skipq", {})[key] = sorted(set(db.get("skipq", {}).get(key, [])) | set(gone))
            SKIPQ.setdefault(key, set()).update(gone)
    return len(gone), seen


def week(B, d1, d2):
    import ai_fill as af
    keys = set()
    for name in ("earnings.json", "earnings_jp_sched.json", "earnings_jp_past.json"):
        try:
            rows = json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
        except (OSError, ValueError):
            rows = []
        keys |= {"jp:" + str(r.get("code")) for r in rows if isinstance(r, dict) and d1 <= str(r.get("date")) <= d2}
    tg = af.targets(B, keys)                             # 은행·리츠 제외 · 시총 순 · af.CAPS 채움
    from markets import extractive
    mc = float(os.environ.get("FPI_MIN_CAP", "0.7"))       # 시총 하한(십억 달러 — 0.7 ≈ 1조 원)
    return [k for k in tg if k.startswith("jp:") and (af.CAPS.get(k) or B.CAPS.get(k) or 0) >= mc and all_holes(B, k)
            and not extractive("jp", B.SECTORS.get(k, "") or af.jp_sector(k[3:]), "", k)]


def main():
    if not pdf_tool():
        print("PDF 를 읽을 도구(pdftotext·pdfplumber)가 없다 — 결과가 전부 '표없음'이 된다. 돌리지 않는다.")
        sys.exit(1)
    print("PDF 도구:", pdf_tool())
    args = sys.argv[1:]
    keys = [a for a in args if ":" in a]
    import ai_seg
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {"v": 1, "stocks": {}}
    db.setdefault("stocks", {})
    for k_, v_ in (db.get("skipq") or {}).items():
        SKIPQ[k_] = set(v_)
    import build as B
    import ai_fill as af
    import ai_web_co
    NAMES.update(ai_web_co.names_map())
    for nm_ in ("earnings.json", "earnings_jp_sched.json", "earnings_jp_past.json"):
        try:
            for r_ in json.loads((HERE / "data" / nm_).read_text(encoding="utf-8")).get("rows", []):
                m_ = re.search(r"(\d+)月期", str(r_.get("fy") or "")) if isinstance(r_, dict) else None
                if m_:
                    FYM.setdefault("jp:" + str(r_.get("code")), int(m_.group(1)))
        except (OSError, ValueError):
            pass
    if "--days" in args:
        i = args.index("--days")
        keys = week(B, args[i + 1], args[i + 2])
        print(f"{args[i + 1]}~{args[i + 2]} 일본 발표 회사 중 사업부 빈칸이 있는 곳: {len(keys)} · 빈칸 {sum(len(all_holes(B, k)) for k in keys)}")
    else:
        af.targets(B, set(keys))
    if not keys:
        print("종목을 줄 것 — python ai_jp_co.py jp:1662  또는  --days 2026-11-11 2026-11-13")
        return
    if "--mark" in args:                                 # 토큰 0 — 채울 수 없는 칸만 적는다
        from concurrent.futures import ThreadPoolExecutor
        tot = [0, 0]

        def mk(key):
            try:
                g, sn = mark(B, key, db)
            except Exception as ex:                      # noqa: BLE001
                print(f"== {key} 실패 {ex!r}"[:160])
                return
            with LOCK:
                tot[0] += g
                tot[1] += 1 if g else 0
                if g:
                    print(f"== {key} 채울 수 없는 분기 {g} (열어 본 공시 {sn})")
                OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(mk, keys))
        print(f"요약(표시만): 회사 {len(keys)} 중 {tot[1]}곳 · 채울 수 없는 분기 {tot[0]} — 다음부터 안 본다")
        return
    res = {}

    def run(key):
        if time.time() - T0 > SECS or ai_seg.USED["in"] + ai_seg.USED["out"] >= MAX_TOK:
            return
        try:
            lines, n, tried, zero = [], 0, set(), 0
            for _b in range(int(os.environ.get("JPCO_BATCH", "4"))):      # 회사마다 네 묶음(빈칸 열여섯)까지 — 두 묶음 연달아 빈손이면 그만
                if time.time() - T0 > SECS or ai_seg.USED["in"] + ai_seg.USED["out"] >= MAX_TOK:
                    break
                l2, n2 = one(B, key, db, verbose=len(keys) <= 6, tried=tried)
                lines += l2
                n += n2
                if l2 and "빈칸 없음" in l2[0]:
                    break
                zero = 0 if n2 else zero + 1
                if zero >= 2:
                    break
        except Exception as ex:                          # noqa: BLE001
            lines, n = [f"  실패 {ex!r}"[:300]], 0
        with LOCK:
            res[key] = n
            print(f"== {key}")
            for line in lines:                           # PDF 글에 제어 문자가 섞여 워크플로의 grep 이 출력을 통째로 삼켰다(11/10)
                print(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", line))
            db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            sys.stdout.flush()

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=int(os.environ.get("FPICO_PAR", "4"))) as ex:
        list(ex.map(run, keys))
    u = ai_seg.USED
    print(f"요약: 본 회사 {len(res)}/{len(keys)} · 빈칸을 하나라도 채운 회사 {sum(1 for n in res.values() if n)} · 채운 칸 {sum(res.values())} · "
          f"토큰 호출 {u['n']} · 입력 {u['in']:,} · 출력 {u['out']:,}")


if __name__ == "__main__":
    main()
