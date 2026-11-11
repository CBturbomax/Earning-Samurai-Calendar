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
"u" = unit multiplier of the tables (百万円 -> 1000000, 千円 -> 1000, 億円 -> 100000000).

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
        r = subprocess.run(["pdftotext", "-layout", "-l", "45", "-", "-"], input=pdf, capture_output=True, timeout=60)
        pages = r.stdout.decode("utf-8", "replace").split("\f")
    except Exception:                                  # noqa: BLE001 — pdftotext 없음·암호 PDF
        return ""
    scored = []
    for i, pg in enumerate(pages):
        flat = re.sub(r"[ \u3000]", "", pg)
        if not re.search(r"セグメント", flat) or not re.search(r"\d{1,3},\d{3}", pg):
            continue
        sc = (3 if re.search(r"外部顧客", flat) else 0) + (2 if re.search(r"報告セグメント", flat) else 0) + \
             (1 if re.search(r"セグメント情報", flat) else 0)
        if sc >= 2:
            scored.append((-sc, i))
    out = []
    for _sc, i in sorted(sorted(scored)[:2], key=lambda t: t[1]):
        pg = "\n".join(re.sub(r" {3,}", "   ", ln.rstrip()) for ln in pages[i].splitlines() if ln.strip())
        out.append(f"[PDF page {i + 1}]\n" + pg[:3200])
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "\n--- page ---\n".join(out))


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


def one(B, key, db, verbose=False):
    import ai_fill as af
    import scrape_fin_jp as F
    import scrape_geo_jp as G
    code = key.split(":")[1]
    fin, seg = B.FIN.get(key) or {}, B.SEG.get(key) or {}
    done = set(((db["stocks"].get(key) or {}).get("q") or {}))          # 앞 실행이 채운 칸(af.gaps 는 마지막 정식 빌드 기준이라 아직 빈칸으로 센다)
    holes = sorted((e for e in af.gaps(B, key) if e not in done), reverse=True)
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
    picked, spans, t0 = {}, {}, time.time()            # zip 이름 -> (공시일, 글, 그 공시의 기간들)
    for e in holes:
        import ai_fpi as P
        for ee in (e, P._back(_d(e), 3).isoformat()):
            # 그 분기 공시에 표가 없으면 **한 해 뒤 같은 분기 공시**(전기 칸에 같은 누계가 실린다 — 11/11: 2018년 구멍의 태반이 이 길로 열린다)
            for tag, x in (("", doc_for(ee)), ("+1y", doc_for(_d(ee).replace(year=_d(ee).year + 1, day=min(_d(ee).day, 28)).isoformat()))):
                if not x:
                    trace.append(f"{ee}{tag}:공시없음")
                    continue
                if x[1] in picked:
                    break
                if len(picked) >= MAX_DOCS:
                    break
                blob = af._patient(lambda: G.get(f"https://www2.jpx.co.jp/disc/{code}0/{x[1]}", timeout=60))
                if not blob:
                    continue
                sp = G.spans_of(F.read_summary(blob))
                txt = seg_excerpt(blob)
                how = "zip"
                if not txt:
                    txt, how = pdf_pages(code, x[1], x[2]), "pdf쪽"
                if not txt:
                    txt, how = pdf_excerpt(code, x[1], x[2]), "pdf"
                if sp:
                    spans.update(sp)
                trace.append(f"{ee}{tag}:{how if txt else '표없음'}{'' if sp else '·기간없음'}")
                if txt and sp:
                    picked[x[1]] = (x[0], txt, sorted(sp))
                    break
    out = [f"  빈 분기 {len(holes)}({holes[-1]}~{holes[0]}) · 평소 비율 {None if base is None else round(base, 2)} · "
           f"세그먼트 표가 든 공시 {len(picked)}장 · 글 {sum(len(v[1]) for v in picked.values()):,}자 · {time.time() - t0:.0f}초"]
    out.append("  공시 찾기: " + " ".join(trace)[:300])
    if not picked:
        with LOCK:
            db.setdefault("none", {})[key] = date.today().isoformat()
        return out + ["  세그먼트 표를 못 찾았다(zip 에 첨부가 없던 시절이거나 표가 그림) — 안 묻는다(토큰 0)"], 0
    body = []
    for _fn, (pub, txt, sps) in sorted(picked.items(), key=lambda kv: kv[1][0]):
        cur = max(sps, key=lambda s: s[1])
        pri = [s for s in sps if s != cur and abs((_d(cur[1]) - _d(s[1])).days - 365) <= 6]
        body.append(f"### FILING published {pub} — CURRENT = {cur[0]}/{cur[1]}" + (f" · PRIOR = {pri[0][0]}/{pri[0][1]}" if pri else "") + "\n" + txt)
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
    got = {}
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
    if got or raw_keep:
        with LOCK:
            st = db["stocks"].setdefault(key, {"cur": fin.get("cur") or "JPY", "fill": 1, "q": {}})
            st["q"].update(got)
            st.setdefault("cum", {}).update(raw_keep)        # 검산을 통과한 누계 원값 — 규칙을 고치면 AI 없이 다시 뺄 수 있게
            st["ts"] = date.today().isoformat()
    out.append(f"  → 빈 분기 {len(got)}/{len(holes)} 채움")
    return out, len(got)


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
    return [k for k in tg if k.startswith("jp:") and af.gaps(B, k)]


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
    import ai_fill as af
    if "--days" in args:
        i = args.index("--days")
        keys = week(B, args[i + 1], args[i + 2])
        print(f"{args[i + 1]}~{args[i + 2]} 일본 발표 회사 중 사업부 빈칸이 있는 곳: {len(keys)} · 빈칸 {sum(len(af.gaps(B, k)) for k in keys)}")
    else:
        af.targets(B, set(keys))
    if not keys:
        print("종목을 줄 것 — python ai_jp_co.py jp:1662  또는  --days 2026-11-11 2026-11-13")
        return
    res = {}

    def run(key):
        if time.time() - T0 > SECS or ai_seg.USED["in"] + ai_seg.USED["out"] >= MAX_TOK:
            return
        try:
            lines, n = one(B, key, db, verbose=len(keys) <= 6)
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
