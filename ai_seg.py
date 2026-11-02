# -*- coding: utf-8 -*-
"""AI(Claude Code `claude -p`)로 실적 보도자료의 사업부 표를 읽는다 — **규칙으로 못 읽은 것만**, **검산을 통과한 것만**.

회원님(11/02): "AI 를 넣으면 회사마다 제각각인 공시도 알아서 읽는다 — API 말고 구독 CLI 로." 규칙 수집기
(`scrape_seg_pr.py`)는 우리가 가진 부문 이름·전년 값으로 표를 맞춰 읽는데, 이름이 다르거나(마이크로소프트·아마존·
테슬라·마스터카드) 표가 두 겹이면 못 읽는다. 그 회사들만 여기로 온다.

**AI 가 준 숫자를 믿지 않는다 — 우리가 가진 숫자와 맞을 때만 싣는다.**
  1. AI 에게 이번 분기와 **전년 같은 분기** 값을 같이 달라고 한다.
  2. 전년 값이 우리가 가진 SEC 공식 부문 값과 **0.6% 안에서** 맞는 부문이 둘 이상이고 큰 부문(전년 합의 95%)을
     덮어야 한다 — 열을 잘못 읽었으면 여기서 걸린다.
  3. 이번 분기 '부문 합 ÷ 총매출' 비가 전년 분기 비와 3% 안이어야 한다(규칙 수집기와 같은 잣대).
  안 맞으면 안 싣는다. 틀린 칸보다 빈칸이 낫다 — 10-Q 가 나오면 벌크·edgar 가 채운다.

**한 번 물은 공시는 다시 묻지 않는다**(`asked` 에 접수번호). 구독 한도에 걸리면 그 자리에서 멈추고 로그에 적는다 —
예전 자동 번역(Routine)이 한도에 걸린 채 닷새를 조용히 헛돈 일을 되풀이하지 않는다.

키: GitHub Secrets 의 `CLAUDE_CODE_OAUTH_TOKEN`(회원님 PC 에서 `claude setup-token` 으로 만든다). 없으면 아무것도 안 한다.

    python ai_seg.py              # 대상 전부(시간 예산 안에서)
    python ai_seg.py MSFT AMZN    # 몇 종목만, 무엇을 물었고 무엇이 돌아왔는지 찍는다
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pr_table as pt
import scrape_live_us as lu
import scrape_pr_us as pr
import scrape_seg_pr as sp

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_aipr.json"
SECS = int(os.environ.get("AISEG_SECS", "1200"))
PER_CALL = int(os.environ.get("AISEG_TIMEOUT", "240"))
MAX_CALLS = int(os.environ.get("AISEG_MAX", "40"))       # 한 실행에 묻는 횟수 — 구독 한도를 아낀다
LIMIT_HINT = re.compile(r"usage limit|rate limit|rate_limit|limit reached|quota|try again later|overloaded|"
                        r'"api_error_status":\s*(429|529)', re.I)


class Limited(Exception):
    """구독 한도·과부하 — 이번 실행은 여기서 멈춘다."""


USED = {"n": 0, "in": 0, "out": 0}                  # 이번 실행에 쓴 토큰 — 끝에 찍는다
ENGINE = os.environ.get("AI_ENGINE", "claude")       # claude | codex — 구독 한도를 여러 계정에 나눠 쓴다


def codex(prompt):
    """`codex exec` 한 번(ChatGPT 구독 한도) -> 답 글자. 프롬프트는 표준입력으로(길어서), 답은 마지막 메시지 파일로."""
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as f:
        last = f.name
    cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
           "--output-last-message", last]
    cmd += ["-c", 'model_reasoning_effort="%s"' % os.environ.get("CODEX_EFFORT", "low")]   # 표 읽기엔 깊게 생각할 필요 없다 — 한도 아끼기
    if os.environ.get("CODEX_MODEL"):
        cmd += ["--model", os.environ["CODEX_MODEL"]]
    r = subprocess.run(cmd + ["-"], input=prompt, cwd=tempfile.gettempdir(), capture_output=True, text=True, timeout=PER_CALL)
    out = (r.stdout or "") + (r.stderr or "")
    try:
        ans = open(last, encoding="utf-8").read()
    except OSError:
        ans = ""
    finally:
        try:
            os.unlink(last)
        except OSError:
            pass
    if r.returncode != 0 or not ans.strip():
        if LIMIT_HINT.search(out):
            raise Limited(out[-300:])
        raise RuntimeError(f"codex 실패 {r.returncode}: {out[-300:]}")
    m = re.search(r"tokens used\D{0,5}([\d,]+)", out, re.I)
    if m:
        USED["in"] += int(m.group(1).replace(",", ""))
    USED["n"] += 1
    return ans


def claude(prompt):
    """`claude -p` 한 번 -> 답 글자. 도구는 안 쓴다(글만 읽고 JSON 을 쓴다). AI_ENGINE=codex 면 codex 로."""
    if ENGINE == "codex":
        return codex(prompt)
    # 아끼기: 도구·MCP·스킬을 다 끄고 시스템 프롬프트를 한 줄로 — 매 호출 붙던 기본 안내문(수만 토큰)을 안 보낸다
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--max-turns", "1", "--tools", "",
           "--system-prompt", "You read financial filings and reply with JSON only.",
           "--strict-mcp-config", "--disable-slash-commands"]
    if os.environ.get("AI_MODEL"):                       # 표 읽기는 가벼운 모델로도 된다 — 구독 한도를 아낀다
        cmd += ["--model", os.environ["AI_MODEL"]]
    # cwd 를 저장소 밖으로 — 저장소 안에서 부르면 CLAUDE.md(14만 자 ≈ 7만 토큰)가 매 호출 따라붙었다(11/02 실측)
    r = subprocess.run(cmd, stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir(),
                       capture_output=True, text=True, timeout=PER_CALL)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0 or LIMIT_HINT.search(out[-2000:]) and '"result"' not in out:
        if LIMIT_HINT.search(out):
            raise Limited(out[-300:])
        raise RuntimeError(f"claude 실패 {r.returncode}: {out[-300:]}")
    try:
        j = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("claude 답이 JSON 이 아니다: " + r.stdout[-300:])
    if j.get("is_error"):
        if LIMIT_HINT.search(str(j.get("result"))):
            raise Limited(str(j.get("result"))[:300])
        raise RuntimeError(str(j.get("result"))[:300])
    u = j.get("usage") or {}
    USED["in"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    USED["out"] += u.get("output_tokens") or 0
    USED["n"] += 1
    return j.get("result") or ""


def tables_text(raw, names, limit=24000):
    """보도자료 HTML -> 사업부 표로 보이는 표만 글로(행마다 '이름 | 숫자…'). 프롬프트를 짧게 — 한도를 아낀다."""
    keys = [sp.key(n) for n in names]
    out = []
    for t in pt.tables(raw):
        rows = [" | ".join(str(c) for c in r if str(c).strip()) for r in t]
        blob = " ".join(rows).lower()
        hit = sum(1 for k in keys if k and k[:8] in re.sub(r"[^a-z0-9]", "", blob))
        if hit >= 1 or re.search(r"segment|revenue by|revenues by|disaggregat", blob):
            out.append("\n".join(rows))
    txt = "\n\n=== TABLE ===\n".join(out)
    return txt[:limit]


PROMPT = """You are reading a US company's quarterly earnings press release (8-K exhibit 99). Below are its tables.

Company: {sym}. The quarter that just ended on {end} (call it CURRENT). The same quarter one year earlier ended about {pend} (call it PRIOR).
Our segment names (from SEC filings): {names}

Task: for EACH of our segment names, find that segment's REVENUE for the CURRENT quarter and for the PRIOR-year quarter
(three months only — never year-to-date / nine-month / annual columns). Match names even if the release words them
differently (e.g. "Intelligent Cloud" may appear as "Intelligent Cloud revenue"). Use the units stated in the table and
convert to plain US dollars (e.g. "$ in millions" 1,234 -> 1234000000). If a segment is not in the tables, omit it.
Do not compute or estimate anything that is not printed.

Answer with ONLY a JSON object, no prose:
{{"unit_note": "...", "segments": [{{"name": "<one of our names>", "current": <number>, "prior": <number>}}]}}

TABLES:
{tables}
"""


def ask(sym, raw, rec, last, pend):
    txt = tables_text(raw, rec["names"])
    if not txt:
        return None, "표 없음"
    ans = claude(PROMPT.format(sym=sym, end=last["end"], pend=pend, names=json.dumps(rec["names"]), tables=txt))
    m = re.search(r"\{.*\}", ans, re.S)
    if not m:
        return None, "JSON 없음"
    try:
        j = json.loads(m.group(0))
    except ValueError:
        return None, "JSON 깨짐"
    return j.get("segments") or [], None


def check(segs, prior, tot_now, tot_prior):
    """AI 답 -> {부문: 이번 분기 값} 또는 (None, 까닭). 규칙 수집기 read() 와 같은 잣대."""
    want = [n for n, v in prior.items() if v]
    got = {s.get("name"): s for s in segs if s.get("name") in prior}
    ok = [n for n in want if n in got and isinstance(got[n].get("prior"), (int, float))
          and abs(got[n]["prior"] - prior[n]) <= abs(prior[n]) * 0.006]
    if len(ok) < 2:
        return None, f"전년 값이 맞는 부문 {len(ok)}개"
    big = sum(abs(prior[n]) for n in want)
    if sum(abs(prior[n]) for n in ok) < big * 0.95:
        return None, "큰 부문이 안 맞는다"
    vals = {n: float(got[n]["current"]) for n in ok if isinstance(got[n].get("current"), (int, float))
            and got[n]["current"] > 0}
    if len(vals) != len(ok):
        return None, "이번 분기 값이 빈 부문"
    r_now = sum(vals.values()) / tot_now
    r_pri = sum(prior[n] for n in ok) / tot_prior
    if abs(r_now / r_pri - 1) > 0.03:
        return None, f"부문 합÷총매출 비가 안 맞다 {r_now:.3f}/{r_pri:.3f}"
    return vals, None


def main():
    only = [a.upper() for a in sys.argv[1:]]
    if ENGINE == "codex":
        if not shutil.which("codex"):
            print("codex CLI 가 없다 — 아무것도 안 한다.")
            return
    elif not shutil.which("claude") or not (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")):
        print("claude CLI 나 CLAUDE_CODE_OAUTH_TOKEN 이 없다 — 아무것도 안 한다.")
        return
    srcs = [sp.load("segments_sec.json"), sp.load("segments_edgar.json"), sp.load("segments_fpi.json"),
            sp.load("segments.json"), sp.load("segments_pr.json")]
    fin = sp.load("financials.json")
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    st = db.setdefault("stocks", {})
    asked = db.setdefault("asked", {})
    cik_of = lu.cik_map_cached()
    todo = []
    for sym, f in fin.items():
        sym = sym.replace("us:", "")
        if only and sym not in only:
            continue
        pts = f.get("points") or []
        if f.get("freq") != "Q" or len(pts) < 5 or not pts[-1].get("rev"):
            continue
        last = pts[-1]
        rec = sp.seg_record(sym, srcs)
        if not rec:
            continue
        ends = [p[0] for p in rec["pts"]] + [p[0] for p in (st.get(sym) or {}).get("pts", [])]
        if any(abs((date.fromisoformat(e) - date.fromisoformat(last["end"])).days) <= 12 for e in ends
               if re.match(r"\d{4}-\d\d-\d\d$", str(e))):
            continue                                        # 그 분기 부문이 이미 있다(규칙·SEC 가 읽었다)
        todo.append((-(f.get("cap") or 0), sym, last, rec))
    todo.sort(key=lambda x: x[0])
    print(f"AI 부문 판독 대상 {len(todo)}종목 (규칙으로 못 읽은 것)")
    t0, calls, got = time.time(), 0, 0
    for _c, sym, last, rec in todo:
        if not only and (time.time() - t0 > SECS or calls >= MAX_CALLS):
            break
        cik = cik_of.get(sym) or cik_of.get(sym.replace(".", "-"))
        if not cik:
            continue
        try:
            hit = pr.latest_earnings_8k(int(cik))
        except Exception as e:                              # noqa: BLE001
            print(f"  ! {sym} 8-K 목록 {e!r}"[:140])
            continue
        if not hit or asked.get(sym) == hit[0]:
            continue
        raw = lu.ex99_html(int(cik), hit[0])
        if not raw:
            continue
        pend = (date.fromisoformat(last["end"]) - timedelta(days=365)).isoformat()
        prior = {n: sp.value_at(srcs, sym, pend, n) for n in rec["names"]}
        fp = [p for p in (fin.get(sym) or fin.get("us:" + sym))["points"] if p.get("rev")
              and abs((date.fromisoformat(p["end"]) - date.fromisoformat(pend)).days) <= 12]
        if not fp or sum(1 for v in prior.values() if v) < 2:
            continue
        try:
            calls += 1
            segs, why = ask(sym, raw, rec, last, pend)
        except Limited as e:
            print(f"  ! 구독 한도·과부하 — 이번 실행은 여기서 멈춘다: {e}")
            db["limited"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            break
        except Exception as e:                              # noqa: BLE001
            print(f"  ! {sym} {e!r}"[:200])
            continue
        asked[sym] = hit[0]
        if segs is None:
            print(f"  {sym} 못 읽음: {why}")
            continue
        vals, why = check(segs, prior, last["rev"], fp[0]["rev"])
        if only:
            print(sym, json.dumps(segs, ensure_ascii=False)[:600])
        if not vals:
            print(f"  {sym} 검산 탈락: {why}")
            continue
        names = [n for n in rec["names"] if n in vals]
        old = st.get(sym) if (st.get(sym) or {}).get("names") == names else {"names": names, "pts": []}
        old["pts"] = [p for p in old.get("pts", []) if p[0] != last["end"]] + [[last["end"]] + [vals[n] for n in names]]
        old["acc"], old["axis"] = hit[0], rec.get("axis")
        st[sym] = old
        got += 1
        print(f"  ✓ {sym} {last['end']} " + " · ".join(f"{n} {vals[n]/1e9:.2f}B" for n in names))
    db["count"] = len(st)
    db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"AI 부문 판독: 물어본 {calls} · 검산 통과 {got} · 대상 {len(todo)}")


if __name__ == "__main__":
    main()
