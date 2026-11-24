"""사업부 빈칸 — **웹에서 찾아** 메운다(11/11) → data/fpi_co.json(같은 파일 · web=1).

회원님(11/11): "공시에서 못 찾으면 기사든 어디든 알아서 찾아서 채우기만 하면 되잖아."

공시 표를 우리가 골라 넘기는 두 수집기(ai_fpi_co · ai_jp_co)가 끝내 못 채운 칸만 본다 — 결산단신에 부문 표가 없는 분기, 6-K 에 실적
표가 없는 외국 기업, 개편 전후의 미국 국내 기업. 여기서는 모델이 **스스로 검색·열람**(WebSearch · WebFetch)해 회사의 공식 자료
(決算説明資料 · 실적 보도자료 · 데이터북 · 분기보고서)에서 석 달 값을 찾아 출처 주소와 함께 답한다.

검산은 그대로 코드가 한다(ai_fpi_co.check): 잣대는 우리 분기 총매출 — 합(+상계)이 1% 안이거나 합÷총매출이 그 회사 평소 비율 ±12% ·
화면에 사업부가 있는 회사는 이름이 절반 이상 겹쳐야 · 닻(이미 가진 분기)과 어긋나면 버림. 출처 주소가 없는 분기는 버린다.

  python ai_web_co.py jp:2587 us:NU
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
LOCK = threading.Lock()
MAX_TOK = int(os.environ.get("FPI_TOKENS", "1000000"))
SECS = int(os.environ.get("FPI_SECS", "4500"))
MAX_Q = int(os.environ.get("WEBCO_Q", "8"))             # 회사당 한 번에 찾는 빈 분기 수(최근 것부터)
T0 = time.time()
USED = {"n": 0, "tok": 0}

PROMPT = """Find a listed company's QUARTERLY revenue by business segment on the web and report it as JSON.

Company: {name} (ticker/code {code}, market {mkt}). Reporting currency: {cur}.
Segment names we already use: {segs}
{anchors}Quarters we need (quarter end date : the company's total revenue for that three-month quarter, which we already hold):
{wants}

How:
- Use web search and open the company's OWN official materials: quarterly earnings release, results presentation / 決算説明資料,
  financial data book / supplementary data, quarterly report (10-Q, 6-K, 決算短信, 四半期報告書). Prefer PDFs/pages on the company's
  investor-relations site or the exchange/regulator site. News articles are acceptable only if they quote the company's numbers.
- We need THREE-MONTH values for each quarter. If a source only prints year-to-date (cumulative) figures, give them under "ytd"
  with their start and end dates and we will subtract ourselves. Do not compute or estimate anything yourself.
- Use the same segment definition as our existing names / verified values when they are given.
- Every quarter you report must carry the URL you took it from ("src"). If you cannot find a quarter, leave it out.

Answer with ONLY JSON (numbers as printed, "u" = unit multiplier, e.g. 1000000 for millions):
{{"u": 1000000,
  "q": {{"2019-06-30": {{"seg": {{"Segment A": 123, "Segment B": 45}}, "elim": -3, "src": "https://..."}}}},
  "ytd": {{"2019-01-01/2019-06-30": {{"seg": {{"Segment A": 240, "Segment B": 91}}, "src": "https://..."}}}},
  "notes": "one line: what you found / why something is missing"}}
"""


def _d(s):
    return date.fromisoformat(s)


def names_map():
    out = {}
    for name, pre in (("earnings_us.json", "us:"), ("earnings_us_fpi.json", "us:"), ("earnings.json", "jp:"),
                      ("earnings_jp_sched.json", "jp:"), ("earnings_jp_past.json", "jp:")):
        try:
            rows = json.loads((HERE / "data" / name).read_text(encoding="utf-8")).get("rows", [])
        except (OSError, ValueError):
            rows = []
        for r in rows:
            if isinstance(r, dict) and r.get("code") and (r.get("name") or r.get("orig")):
                out.setdefault(pre + str(r["code"]), str(r.get("name") or r.get("orig")))
    return out


def ask(prompt):
    """claude -p — 검색·열람 도구를 켜고 여러 턴(스스로 찾아 읽는다)."""
    cmd = ["claude", "-p", "--output-format", "json", "--max-turns", os.environ.get("WEBCO_TURNS", "30"),
           "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch",
           "--system-prompt", "You are a careful financial data researcher. You search the web, read primary sources, and reply with JSON only.",
           "--strict-mcp-config", "--disable-slash-commands"]
    if os.environ.get("AI_MODEL"):
        cmd += ["--model", os.environ["AI_MODEL"]]
    r = subprocess.run(cmd, input=prompt, cwd=tempfile.gettempdir(), capture_output=True, text=True,
                       timeout=int(os.environ.get("WEBCO_TIMEOUT", "1200")))
    try:
        j = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("claude 답이 JSON 이 아니다: " + ((r.stdout or "") + (r.stderr or ""))[-300:])
    u = j.get("usage") or {}
    tok = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + \
        (u.get("output_tokens") or 0)
    with LOCK:
        USED["n"] += 1
        USED["tok"] += tok
    if j.get("is_error"):
        raise RuntimeError(f"(토큰 {tok:,}) " + str(j.get("result"))[:300])
    return j.get("result") or "", tok, j.get("num_turns")


def holes_of(B, key, db):
    """이 회사의 빈 분기(최근 것부터) — 일본·미국 국내는 화면 구멍(af.gaps), 외국 기업은 사업부가 없는 분기 전부."""
    import ai_fill as af
    import ai_fpi as P
    import ai_fpi_co as C
    done = set(((db["stocks"].get(key) or {}).get("q") or {}))
    if key.startswith("us:"):
        try:
            raw_us = json.loads((HERE / "data" / "financials.json").read_text(encoding="utf-8")).get("stocks", {})
        except (OSError, ValueError):
            raw_us = {}
        if P.is_fpi(key[3:], raw_us):
            return [e for e in C.missing(B, key)[1] if e not in done]
    return sorted((e for e in af.gaps(B, key) if e not in done), reverse=True)


def one(B, key, db, nm):
    import ai_fpi as P
    import ai_fpi_co as C
    fin, seg = B.FIN.get(key) or {}, B.SEG.get(key) or {}
    holes = holes_of(B, key, db)[:MAX_Q]
    if not holes:
        return ["  빈칸 없음"], 0
    names = seg.get("names") or []
    rev = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}

    def rv(e):
        h = min(rev, key=lambda x: abs((_d(x) - _d(e)).days), default=None)
        return rev[h] if h and abs((_d(h) - _d(e)).days) <= 20 else None
    holes = [e for e in holes if rv(e)]
    if not holes:
        return ["  그 분기 총매출이 없다 — 검산할 잣대가 없어 안 찾는다"], 0
    mine_before = set(((db["stocks"].get(key) or {}).get("q") or {}))
    anc = {str(r[0]): {n: v for n, v in zip(names, r[1:]) if v} for r in seg.get("pts") or []
           if re.match(r"\d{4}-\d\d-\d\d$", str(r[0])) and any(v for v in r[1:])}
    pick = sorted(sorted(anc, key=lambda a: abs((_d(a) - _d(holes[len(holes) // 2])).days))[:2])
    anchors = ""
    if pick:
        anchors = "VERIFIED VALUES we already hold (absolute currency units) — use the same segment definition:\n" + "\n".join(
            f"  {e}: " + ", ".join(f"{n} {v:,.0f}" for n, v in anc[e].items()) for e in pick) + "\n"
    wants = "\n".join(f"  {e} : {rv(e):,.0f}" for e in sorted(holes))
    raw, tok, turns = ask(PROMPT.format(name=nm.get(key) or key[3:], code=key[3:], mkt={"us": "US (NYSE/Nasdaq)", "jp": "Tokyo"}[key[:2]],
                                        cur=fin.get("cur") or "", segs=json.dumps(names[:14], ensure_ascii=False) if names else "(none yet)",
                                        anchors=anchors, wants=wants))
    out = [f"  빈 분기 {len(holes)}({holes[-1]}~{holes[0]}) · 토큰 {tok:,} · {turns}턴"]
    mm = re.search(r"\{.*\}", raw or "", re.S)
    try:
        ans = json.loads(mm.group(0)) if mm else {}
    except ValueError:
        return out + ["  답이 JSON 이 아니다: " + (raw or "")[:200]], 0
    out.append("  메모: " + str(ans.get("notes"))[:220])
    q = {e: x for e, x in (ans.get("q") or {}).items() if isinstance(x, dict) and str(x.get("src") or "").startswith("http")}
    # 누계만 준 분기 — 같은 시작일의 앞 누계가 같이 있으면 뺀다(첫 분기는 그대로)
    ytd = {}
    for k, x in (ans.get("ytd") or {}).items():
        try:
            s, e = k.split("/")
            _d(s), _d(e)
        except ValueError:
            continue
        if isinstance(x, dict) and str(x.get("src") or "").startswith("http") and isinstance(x.get("seg"), dict):
            ytd[(s, e)] = x
    for (s, e), x in ytd.items():
        if e in q:
            continue
        months = round((_d(e) - _d(s)).days / 30.4)
        if months <= 4:
            q[e] = {"seg": x["seg"], "src": x["src"]}
            continue
        pv = next((k for k in ytd if k[0] == s and 75 <= (_d(e) - _d(k[1])).days <= 105), None)
        if pv and set(ytd[pv]["seg"]) == set(x["seg"]):
            try:
                q[e] = {"seg": {n: x["seg"][n] - ytd[pv]["seg"][n] for n in x["seg"]}, "src": x["src"]}
            except TypeError:
                pass
    ans["q"] = {e: x for e, x in q.items() if any(abs((_d(e) - _d(h)).days) <= 20 for h in holes)}
    ok, rep = C.check(B, key, ans, {}, [e for e in P.quarter_ends(fin)], mine_before)
    if names:                                           # 화면에 사업부가 있는 회사 — 이름이 절반 이상 겹쳐야(다른 축을 섞지 않는다)
        pre = {B.seg_key(n) or n for n in names}
        for e in [e for e, x in ok.items() if len({B.seg_key(n) or n for n in x["seg"]} & pre) * 2 < len(x["seg"])]:
            rep.append(f"    {e} ✗ 화면 이름과 안 겹친다 — 버림")
            ok.pop(e)
    ok = {e: dict(x, src=str((ans["q"].get(e) or {}).get("src") or x.get("src"))[:300]) for e, x in ok.items()}
    out += rep
    out.append(f"  → 채움 {len(ok)}/{len(holes)}")
    if ok:
        with LOCK:
            st = db["stocks"].setdefault(key, {"cur": fin.get("cur") or "", "q": {}})
            st["q"].update(ok)
            st["web"] = 1
            if names:
                st["fill"] = 1
            st["ts"] = date.today().isoformat()
    return out, len(ok)


def main():
    keys = [a for a in sys.argv[1:] if ":" in a]
    if not keys:
        print("종목을 줄 것 — python ai_web_co.py jp:2587 us:NU")
        return
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {"v": 1, "stocks": {}}
    db.setdefault("stocks", {})
    import build as B
    import ai_fill as af
    af.targets(B, set(keys))
    nm = names_map()
    res = {}

    def run(key):
        if time.time() - T0 > SECS or USED["tok"] >= MAX_TOK:
            return
        try:
            lines, n = one(B, key, db, nm)
        except Exception as ex:                          # noqa: BLE001
            lines, n = [f"  실패 {ex!r}"[:400]], 0
        with LOCK:
            res[key] = n
            print(f"== {key} {nm.get(key, '')}")
            for line in lines:
                print(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", line))
            db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            sys.stdout.flush()

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=int(os.environ.get("FPICO_PAR", "4"))) as ex:
        list(ex.map(run, keys))
    print(f"요약: 본 회사 {len(res)}/{len(keys)} · 채운 회사 {sum(1 for n in res.values() if n)} · 채운 칸 {sum(res.values())} · "
          f"호출 {USED['n']} · 토큰 {USED['tok']:,}")


if __name__ == "__main__":
    main()
