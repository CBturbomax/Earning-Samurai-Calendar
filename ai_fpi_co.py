"""외국 기업 사업부 — **회사 하나를 한 번에** 읽는다(시험판, 11/10) → data/fpi_co.json.

회원님(11/10): "사업부 완전 엉망 · 지금까지 한 방식 말고 싹 다 갈아엎자."

ai_fpi.py(공시 한 장 = 한 번 묻기 · 사업부 합이 총매출과 1% 안이어야 통과)가 사업부를 못 채운 까닭 둘:
  1. 회사가 내는 부문 표는 총매출과 안 맞는 일이 흔하다(CNQ 로열티 차감 전 · SU·MT 부문 간 거래 포함) — 맞게 읽어도 버려졌다.
  2. 한 장씩 따로 읽어 분기마다 이름·기준이 흔들리고 띄엄띄엄 들어왔다.

여기서는
  · 분기마다 실적 공시 한 장씩 골라 **부문 표만** 추려 한 프롬프트에 차례대로 싣고(회사당 한 번),
  · 모델이 **한 이름 체계·한 기준**으로 전 분기를 정리한다(나중 공시의 재표시 값 우선, 상계 줄은 따로),
  · 검산은 코드가 한다 — 합이 총매출과 같을 필요는 없다. 대신
      (가) 총매출: 가진 분기와 2% 안
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
import time
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "fpi_co.json"
A, Z = "2018-03-31", "2021-12-31"                       # 읽을 분기 범위(끝은 이미 가진 분기 하나 — 닻)
PER_DOC = int(os.environ.get("FPICO_DOC", "6000"))
MAX_PROMPT = 110000

PROMPT = """You are building a clean QUARTERLY revenue-by-segment time series for one company from its own quarterly reports.

Company: {code}. Reporting currency: {cur}. Segment names we already use for recent quarters: {segs}.
Below are table excerpts from {n} quarterly filings, oldest first. Each starts with "### FILING filed YYYY-MM-DD".

For every quarter end from {a} to {z} that the filings cover, give:
- "rev": total revenue of the whole company for the THREE months (the headline revenue line; the same definition in every quarter).
- "seg": revenue of each reportable business segment for the THREE months.
- "elim": the eliminations / intersegment / corporate line of that same table, if printed (usually negative), so that sum(seg) + elim = the table's total.

Rules:
1. Three-month (quarter) values only. Never six-, nine-, twelve-month or year-to-date values, never percentages or volumes.
   Do not compute anything (no subtracting year-to-date). If a quarter has no three-month column anywhere, leave it out.
2. Use ONE consistent set of segment names and ONE consistent measure across all quarters (for example always "gross revenues
   including intersegment" or always "external revenues"). State it in "basis". Reuse our existing names when the segment is the same thing.
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


def seg_text(raw):
    """실적 공시 한 장 -> 부문 표 위주의 글(PER_DOC 자). 부문 낱말 + 매출 줄이 다 있는 표가 맨 앞, 그다음 부문 표, 그다음 손익 표."""
    import ai_fpi as P
    import pr_table as pt
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw[:60000]))
    if not P.QHINT.search(plain):
        return ""
    got = []
    for i, t in enumerate(pt.tables(raw)):
        rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
        rows = [r for r in rows if r.strip()]
        blob = "\n".join(rows)
        if len(rows) < 2 or not P.KW.search(blob) or not re.search(r"\d{3,}", blob):
            continue
        if re.search(r"(?i)bbl|boe|mmcf|tonnes?\b|\bkt\b|per share|headcount|\$/|%", rows[0] + rows[1]):
            continue                                     # 물량·단가 표
        s = (2 if re.search(r"(?i)segment|divisions?\b|business (unit|area|line)", blob) else 0) + (1 if P.REVROW.search(blob) else 0)
        if re.search(r"(?i)three months|quarter", blob[:400]):
            s += 1
        if s:
            got.append((-s, i, blob[:3000]))
    if not got or not any(P.REVROW.search(b) for _s, _i, b in got):
        return ""
    out, n = [], 0
    for _s, _i, b in sorted(got):
        if n + len(b) > PER_DOC:
            continue
        out.append(b)
        n += len(b)
    units = sorted({m.group(0)[:80] for m in re.finditer(
        r"(?i)\(?[^().]{0,30}\b(in|expressed in)\s+(thousands|millions|billions)\b[^().]{0,30}\)?", plain)})[:2]
    return ("UNIT NOTES: " + " | ".join(units) + "\n" if units else "") + "\n--- table ---\n".join(out)


def filing_text(cik, acc):
    """6-K 한 건 -> 부문 표 글. 첨부를 이름 맞는 것 먼저 네 장까지 열어 가장 알찬 장(부문 표가 든 장)을 쓴다."""
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
    best = ""
    for name in (ex + [n for n in htm if n not in ex])[:4]:
        raw = af.sec(lu.DOC.format(cik=cik, acc=acc, name=name), binary=True)
        txt = seg_text(raw.decode("utf-8", "replace")) if raw else ""
        if re.search(r"(?i)segment", txt) and len(txt) > len(best):
            best = txt
        elif not best:
            best = txt
    return best


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
    ai_seg.USED["in"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    ai_seg.USED["out"] += u.get("output_tokens") or 0
    ai_seg.USED["n"] += 1
    return j.get("result") or ""


def check(B, key, ans, pr_q):
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
        near = min(have, key=lambda h: abs((_d(h) - d).days), default=None)
        if not near or abs((_d(near) - d).days) > 20 or not isinstance(x, dict):
            continue
        seg = {str(n).strip()[:60]: float(v) * u for n, v in (x.get("seg") or {}).items() if isinstance(v, (int, float)) and v}
        rev = float(x["rev"]) * u if isinstance(x.get("rev"), (int, float)) else None
        elim = float(x["elim"]) * u if isinstance(x.get("elim"), (int, float)) else 0.0
        rows[near] = {"rev": rev, "seg": seg, "elim": elim, "src": x.get("src"), "have": have[near]}
    ends = sorted(rows)
    ratio = {e: sum(rows[e]["seg"].values()) / rows[e]["have"] for e in ends if len(rows[e]["seg"]) >= 2}
    # 화면에 이미 있는 사업부(닻) — 겹치는 분기에서 부문마다 견준다
    old = {}
    for r in (B.SEG.get(key) or {}).get("pts") or []:
        if re.match(r"\d{4}-\d\d-\d\d$", str(r[0])):
            old[str(r[0])] = {B.seg_key(n): v for n, v in zip(B.SEG[key]["names"], r[1:]) if v}
    for e, lst in (pr_q or {}).items():
        x = next((r for r in reversed(lst) if r.get("seg")), None)
        if x:
            old[e] = {B.seg_key(n): v for n, v in x["seg"].items()}
    ok, rep = {}, []
    for i, e in enumerate(ends):
        r = rows[e]
        why = []
        rev_ok = r["rev"] is not None and abs(r["rev"] / r["have"] - 1) <= 0.02
        if not rev_ok:
            why.append(f"총매출 {None if r['rev'] is None else round(r['rev'] / 1e6)} ≠ 가진 값 {round(r['have'] / 1e6)}")
        grade = ""
        if e in ratio:
            exact = abs((sum(r["seg"].values()) + r["elim"]) / r["have"] - 1) <= 0.01
            nb = [ratio[x] for x in ends[max(0, i - 4):i + 5] if x != e and x in ratio]
            mid = sorted(nb)[len(nb) // 2] if len(nb) >= 3 else None
            steady = mid is not None and abs(ratio[e] / mid - 1) <= (0.25 if mid >= 1.3 else 0.12)
            grade = "맞아떨어짐" if exact else ("비율 고름" if steady else "")
            if not grade:
                why.append(f"합÷총매출 {ratio[e]:.2f} (앞뒤 {None if mid is None else round(mid, 2)})")
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
        good = rev_ok and grade and "가진 값과 다름" not in why
        rep.append(f"    {e} {'✓' if good else '✗'} 매출 {round(r['have'] / 1e6):>7,} · 합÷매출 {ratio.get(e, 0):.2f} · {grade or '-'} {anchor} "
                   f"· {' / '.join(f'{n} {round(v / 1e6):,}' for n, v in r['seg'].items())[:150]}" + (f"  ← {'; '.join(why)}" if why else ""))
        if good:
            ok[e] = {"seg": r["seg"], "elim": r["elim"], "src": r["src"], "grade": grade}
    return ok, rep


def one(B, key, db):
    import ai_fill as af
    import ai_fpi as P
    import scrape_live_us as lu
    code = key.split(":")[1]
    cik = lu.cik_map_cached().get(code) or lu.cik_map_cached().get(code.replace(".", "-"))
    if not cik:
        return ["  CIK 없음"]
    fin = B.FIN.get(key) or {}
    ends = [e for e in P.quarter_ends(fin) if A <= e <= Z]
    files = P.six_k(int(cik))
    docs, t0 = [], time.time()
    for e in ends:
        for fd, acc in [f for f in files if 10 <= (_d(f[0]) - _d(e)).days <= 100][:10]:
            if any(acc == a for _f, a, _t in docs):
                break
            txt = filing_text(int(cik), acc)
            if txt:
                docs.append((fd, acc, txt))
                break
    if len(docs) < 3:
        return [f"  실적 공시를 {len(docs)}장밖에 못 골랐다 — 안 묻는다"]
    docs.sort()
    body = "\n\n".join(f"### FILING filed {fd}\n{txt}" for fd, _a, txt in docs)
    names = (B.SEG.get(key) or {}).get("names") or []
    prompt = PROMPT.format(code=code, cur=fin.get("cur") or "USD", segs=json.dumps(names[:12], ensure_ascii=False),
                           n=len(docs), a=A, z=Z, docs=body[:MAX_PROMPT])
    out = [f"  공시 {len(docs)}장 · 글 {len(body):,}자 · 고르는 데 {time.time() - t0:.0f}초"]
    raw = ask(prompt)
    mm = re.search(r"\{.*\}", raw or "", re.S)
    try:
        ans = json.loads(mm.group(0)) if mm else {}
    except ValueError:
        return out + ["  답이 JSON 이 아니다: " + (raw or "")[:300]]
    pr_q = ((json.loads((HERE / "data" / "fpi_pr.json").read_text(encoding="utf-8")).get("stocks") or {}).get(key) or {}).get("q") \
        if (HERE / "data" / "fpi_pr.json").exists() else {}
    ok, rep = check(B, key, ans, pr_q)
    db["stocks"][key] = {"cur": fin.get("cur") or "USD", "basis": str(ans.get("basis"))[:200], "q": ok,
                         "docs": [a for _f, a, _t in docs], "ts": date.today().isoformat()}
    was = sum(1 for r in (B.SEG.get(key) or {}).get("pts") or [] if A <= str(r[0]) <= Z and any(v for v in r[1:]))
    return out + [f"  기준: {str(ans.get('basis'))[:160]}"] + rep + \
        [f"  → 사업부 통과 {len(ok)}/{len(ends)}분기 (지금 화면 {was}분기)"]


def main():
    keys = [a for a in sys.argv[1:] if ":" in a]
    if not keys:
        print("종목을 줄 것 — python ai_fpi_co.py us:SU us:CNQ")
        return
    import ai_seg
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {"v": 1, "stocks": {}}
    import build as B
    for key in keys:
        print(f"== {key}")
        try:
            for line in one(B, key, db):
                print(line)
        except Exception as ex:                          # noqa: BLE001
            print(f"  실패 {ex!r}"[:400])
        db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        u = ai_seg.USED
        print(f"  누적 토큰: 호출 {u['n']} · 입력 {u['in']:,} · 출력 {u['out']:,}")


if __name__ == "__main__":
    main()
