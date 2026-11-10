"""외국 기업(10-Q 를 안 내는 미국 상장사)의 분기 **총매출 + 사업부** — 6-K 보도자료 표에서 읽는다 → data/fpi_pr.json.

회원님(11/10): "이번 주 발표 회사들 매출 breakdown 빈칸이 너무 많다 · SE 만 봐도 거의 다 빈칸 · 토큰 낭비·이상한 루프 없이 제대로."

왜 비었나(실측): 씨(SE)·누홀딩스·페트로브라스·알콘 같은 외국 기업은 10-Q 가 없어 SEC 분기 XBRL 이 없고, stockanalysis 는
20분기(3Q21~)뿐이다. **총매출 자체가 없는 분기는 '빈칸'으로도 안 세어져** ai_fill 이 영영 안 봤다(대상 = 총매출은 있는데
사업부가 빈 분기 · 사업부 기록 4분기 이상). 숫자는 분기마다 낸 6-K 보도자료 표에 그대로 있다.

원리 — 한 장이 두 분기(이번·전년 같은 분기)를 주고, **검산은 AI 없이** 한다.
  1. 표 안에서: 사업부 합 = 그 표의 총매출(1% 안). 안 맞으면 사업부는 버린다.
  2. 우리가 가진 분기 총매출과 2% 안(겹치는 분기). 안 맞으면 그 기간을 통째로 버린다(통화·단위가 다른 표).
  3. 우리가 없는 옛 분기는 빌드(build.load_fpi_pr)가 **네 분기 합 = 20-F 연간 매출(1.5% 안)** 또는 **두 공시가 같은 값**일 때만 싣는다.

헛돌지 않게: 공시 하나는 한 번만 묻는다(asked) · 연달아 셋이 빈손이면 그 회사는 반년 쉰다(dry) · 실행마다 토큰·호출 상한 ·
한도에 걸리면 그 자리에서 멈춘다. 곧 발표할 회사(지난 7일~앞으로 21일)가 먼저, 그다음 시총 큰 순. 다 차면 할 일이 없어진다.

  python ai_fpi.py              # 대기줄대로
  python ai_fpi.py us:SE us:NU  # 몇 종목만(시험)
  python ai_fpi.py --plan       # AI 를 안 부르고 대상·빈 분기만 센다
"""
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "fpi_pr.json"
SINCE = "2018-01-01"
SECS = int(os.environ.get("FPI_SECS", "2400"))
MAX_CALLS = int(os.environ.get("FPI_MAX", "80"))
MAX_TOK = int(os.environ.get("FPI_TOKENS", "200000"))      # 이번 실행 토큰 상한 — 넘으면 멈춘다
MIN_CAP = float(os.environ.get("FPI_MIN_CAP", "7"))         # 십억 달러 — 7 ≈ 10조 원(11/10 회원님: "3천만 토큰은 너무 심하다" — 큰 회사부터)
# 무엇을 읽나(11/10 — 범위를 좁혔다). rev: 총매출부터 없는 옛 분기만(기본) · seg: + 총매출은 있는데 사업부가 빈 분기 ·
# all: + 사업부 줄이 흩어진 회사는 있는 분기까지 다시. 넓힐수록 토큰이 몇 배로 든다 — 실측을 보고 넓힐 것.
SCOPE = os.environ.get("FPI_SCOPE", "rev")
# 일본 회사의 미국 상장분 — 같은 회사 숫자가 일본 시장(jp:) 쪽에 이미 1Q16 부터 있다. 여기서 또 읽으면 중복이다(11/10 회원님과 정함:
# 중국계 미국 상장사는 읽고, 일본 중복은 뺀다).
JP_ADR = {"TM", "SONY", "HMC", "TAK", "IX", "NMR", "MUFG", "SMFG", "MFG", "NTDOY", "CAJ"}
DOC_LIMIT = int(os.environ.get("FPI_DOC", "14000"))
DRY_N, DRY_DAYS = 3, 180
KW = re.compile(r"revenue|net sales|turnover|sales|segment", re.I)
QHINT = re.compile(r"three months|three-month|quarter|3 months|\b[1-4]Q\s?\d\d|\bQ[1-4]\b|six months|half[- ]year", re.I)

PROMPT = """You are extracting numbers from a company's quarterly earnings press release. Read ONLY the text below.

Company: {name} ({key}). Filing: {doc}. Reporting currency we use: {cur} (if a table is printed in two currencies, use {cur}).
Segment names we already use: {segs}. Reuse a name when the row is the same thing.

For the {span} columns only — the current period AND the same period a year earlier — give:
- "rev": total revenue of the whole company (one item per period).
- "seg": the company's main revenue breakdown by business segment or product/service line, whose rows add up to total revenue
  (external revenue if both are printed). One item per row per period. Skip geographic tables, sub-totals and eliminations.
- "opi": operating income (profit from operations) of the whole company, if printed.

Each item: k (rev|seg|opi), name (as printed; "" for rev/opi), end (period end date YYYY-MM-DD), m (months covered: {m}),
v (the number exactly as printed, negative if in brackets), u (unit multiplier of that table: 1000000 for "in millions",
1000 for "in thousands", 1000000000 for "in billions", 1 if none).
Never include year-to-date columns, percentages, changes, guidance, or numbers you computed.
If nothing relevant is printed, return an empty list.

Answer with ONLY JSON: {{"items": [{{"k": "seg", "name": "Cloud", "end": "2021-06-30", "m": {m}, "v": 1234.5, "u": 1000000}}]}}

TEXT:
{tables}
"""


def _d(s):
    return date.fromisoformat(s)


def _back(e, months):
    """월말 날짜를 months 달 앞으로(월말 유지)."""
    y, m = e.year, e.month - months
    while m <= 0:
        y, m = y - 1, m + 12
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last if e.day >= 28 else date(y, m, min(e.day, last.day))


def quarter_ends(fin):
    """이 회사의 분기(반기) 종료일을 1Q18 부터 — 가장 최근 점에서 석 달(여섯 달)씩 거슬러 잇는다. 가진 점 사이의 구멍도 메운다."""
    pts = sorted(p["end"] for p in fin.get("points") or [] if p.get("end"))
    if len(pts) < 4:
        return []
    step = 6 if fin.get("freq") == "H" else 3
    out, e = set(pts), _d(pts[-1])
    while True:
        e = _back(e, step)
        if e.isoformat() < SINCE:
            break
        if not any(abs((_d(x) - e).days) <= 20 for x in pts):
            out.add(e.isoformat())
    return sorted(x for x in out if x >= SINCE)


def is_fpi(code, raw_us):
    """SEC 분기 재무(10-Q)가 없는 미국 상장사 — financials.json 에 없거나 연간뿐."""
    r = raw_us.get(code)
    return not r or r.get("freq") != "Q" or not r.get("points")


def fragmented(seg):
    """사업부 줄이 이름 바뀜·소스 섞임으로 흩어졌나 — 한 분기에 값이 있는 줄이 전체 줄의 6할이 안 되면(가운뎃값) 그렇다.
    SE 가 그랬다: stockanalysis 가 옛 이름·새 이름을 일곱 줄로 나눠 줄마다 거의 빈칸이었다. 이런 회사는 보도자료로 전 분기를 한 표기로 읽는다."""
    rows = [r for r in (seg or {}).get("pts") or [] if any(v for v in r[1:])]
    if len(rows) < 4 or len(seg["names"]) < 4:
        return False
    share = sorted(sum(1 for v in r[1:] if v) / len(seg["names"]) for r in rows)
    return share[len(share) // 2] < 0.6


def need(B, key, db, holes):
    """읽을 값어치가 있는 종료일 — 총매출이 없거나 사업부가 빈 분기. 이미 검산을 통과해 담아 둔 분기는 뺀다."""
    fin = B.FIN.get(key) or {}
    have = {p["end"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}
    got = (db["stocks"].get(key) or {}).get("q") or {}
    fresh = (date.today() - timedelta(days=12)).isoformat()   # 막 끝난 분기는 아직 발표 전
    seg = B.SEG.get(key)
    sh = holes.get(key)
    frag = SCOPE == "all" and fragmented(seg)
    out = []
    for e in quarter_ends(fin):
        if e > fresh:
            continue
        g = got.get(e) or []
        if any(x.get("seg") for x in g):
            continue
        no_seg = frag or ((e in sh) if sh is not None else not seg)
        if e not in have and not g:
            out.append(e)                               # 총매출부터 없다
        elif SCOPE != "rev" and no_seg and not (key in db.get("noseg", {})):
            out.append(e)                               # 총매출은 있는데 사업부가 빈다
    return out


def six_k(cik):
    """[(접수일, 접수번호)] — 6-K 전부(2018-04 이후). 설명 칸이 'FORM 6-K' 뿐인 회사가 많아 이름으로 거르지 않는다(SE 가 그렇다)."""
    import ai_fill as af
    j = json.loads(af.sec(af.SUBS.format(cik=cik)) or "{}")
    blocks = [j.get("filings", {}).get("recent", {})]
    for f in j.get("filings", {}).get("files", []) or []:
        if (f.get("filingTo") or "") >= "2018-04-01":
            try:
                blocks.append(json.loads(af.sec(af.SUBS_OLD.format(name=f["name"])) or "{}"))
            except ValueError:
                pass
    out = set()
    for r in blocks:
        for i, form in enumerate(r.get("form", [])):
            if form == "6-K" and r["filingDate"][i] >= "2018-04-01":
                out.add((r["filingDate"][i], r["accessionNumber"][i].replace("-", "")))
    return sorted(out)


def release_html(cik, acc):
    """6-K 의 실적 보도자료 첨부. 첨부가 여럿이면(SE 2019-11: 99.1 보도자료 20만 자 · 99.2 공모용 재무제표 140만 자) **가장 큰 것이 아니라
    이름 차례로 열어 실적 표가 있는 첫 장**을 쓴다. -> (글, 없으면 "")"""
    import ai_fill as af
    import scrape_live_us as lu
    blob = af.sec(lu.FILES.format(cik=cik, acc=acc))
    try:
        items = json.loads(blob)["directory"]["item"] if blob else []
    except (ValueError, KeyError):
        items = []
    htm = sorted(it["name"] for it in items if it["name"].lower().endswith((".htm", ".html"))
                 and not re.match(r"index|.*-index", it["name"], re.I))
    ex = [n for n in htm if re.search(r"ex[-_]?99|99[-_.]?[1-3]\b|991|992|press|earnings|result", n, re.I)]
    for name in (ex or htm)[:3]:
        raw = af.sec(lu.DOC.format(cik=cik, acc=acc, name=name), binary=True)
        txt = text_of(raw.decode("utf-8", "replace")) if raw else ""
        if txt:
            return txt
    return ""


def text_of(raw):
    """보도자료 -> AI 에 넘길 글: 첫머리(무슨 분기인지) + 매출·사업부 낱말이 든 표만."""
    import pr_table as pt
    plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw[:60000]))
    if not (QHINT.search(plain) and KW.search(plain)):
        return ""
    out = []
    for t in pt.tables(raw):
        rows = [" | ".join(c for c in r if c and c.strip() and c.strip() != "$") for r in t]
        rows = [r for r in rows if r.strip()]
        blob = "\n".join(rows)
        if len(rows) >= 2 and KW.search(blob) and re.search(r"\d{3,}", blob):
            out.append(blob)
    if not out:
        return ""
    # 단위 문구는 표 밖에 있는 일이 많다("(RMB in thousands, except per share data)") — 첫 시험에서 AI 가 단위를 1 로 적었다. 찾아서 같이 넘긴다.
    units = sorted({m.group(0)[:90] for m in re.finditer(
        r"(?i)\(?[^().]{0,40}\b(in|expressed in)\s+(thousands|millions|billions)\b[^().]{0,40}\)?", plain)})[:4]
    head = plain[:500] + ("\nUNIT NOTES FOUND IN THE DOCUMENT: " + " | ".join(units) if units else "")
    return (head + "\n\n=== TABLE ===\n" + "\n\n=== TABLE ===\n".join(out))[:DOC_LIMIT]


def _num(x):
    if isinstance(x, (int, float)):
        return float(x)
    try:
        s = str(x).replace(",", "").strip()
        neg = s.startswith("(") or s.startswith("-")
        return (-1 if neg else 1) * float(s.strip("()-"))
    except ValueError:
        return None


def accept(items, ends, fin, acc, fd, m_want):
    """AI 답 -> {종료일: {"a","rev","opi","seg"}} — 표 안 검산(사업부 합 = 총매출 1%)과 우리 총매출 대조(2%)를 통과한 기간만."""
    per = {}
    for it in items or []:
        if not isinstance(it, dict) or it.get("m") != m_want:
            continue
        v, u = _num(it.get("v")), _num(it.get("u")) or 1
        try:
            e = _d(str(it.get("end")))
        except ValueError:
            continue
        if v is None:
            continue
        near = min(ends, key=lambda x: abs((_d(x) - e).days), default=None)
        if not near or abs((_d(near) - e).days) > 20:
            continue
        p = per.setdefault(near, {"seg": {}})
        k = it.get("k")
        if k == "rev":
            p["rev"] = v * u
        elif k == "opi":
            p["opi"] = v * u
        elif k == "seg" and it.get("name") and not re.search(r"(?i)^total|eliminat|inter-?segment", str(it["name"])):
            p["seg"][str(it["name"]).strip()[:60]] = v * u
    have = {p["end"]: p["rev"] for p in fin.get("points") or [] if p.get("end") and p.get("rev")}
    out = {}
    for e, p in per.items():
        rev = p.get("rev")
        if not rev or rev <= 0:
            continue
        if e in have and abs(rev / have[e] - 1) > 0.02:
            continue                                    # 다른 통화·단위·누계 칸
        seg = {n: x for n, x in p["seg"].items() if x}
        if len(seg) < 2 or abs(sum(seg.values()) / rev - 1) > 0.01:
            seg = {}
        rec = {"a": acc, "d": fd, "rev": rev}
        if p.get("opi") is not None:
            rec["opi"] = p["opi"]
        if seg:
            rec["seg"] = seg
        out[e] = rec
    return out


def one(B, key, miss, db, t0, verbose):
    import ai_fill as af
    import ai_seg
    import scrape_live_us as lu
    code = key.split(":")[1]
    cik = lu.cik_map_cached().get(code) or lu.cik_map_cached().get(code.replace(".", "-"))
    if not cik:
        return "CIK 없음", 0
    fin = B.FIN.get(key) or {}
    m_want = 6 if fin.get("freq") == "H" else 3
    ends = quarter_ends(fin)
    files = six_k(int(cik))
    if not files:                                        # 6-K 를 안 내는 회사(국내 기업인데 SEC 분기 재무가 비어 있을 뿐) — 반년 뒤에나 다시 본다
        db.setdefault("dry", {})[key] = date.today().isoformat()
        return "6-K 없음", 0
    st = db["stocks"].setdefault(key, {"cur": fin.get("cur") or "USD", "q": {}})
    asked = db["asked"].setdefault(key, [])
    log, got, dry, calls = [], 0, 0, 0
    todo = sorted(miss, reverse=True)                   # 최근 것부터 — 한 장이 그 분기와 전년 같은 분기를 준다
    while todo:
        e = todo.pop(0)
        if e in st["q"] and (any(x.get("seg") for x in st["q"][e]) or key in db.get("noseg", {})):
            continue
        cands = [f for f in files if 10 <= (_d(f[0]) - _d(e)).days <= 100 and f[1] not in asked][:8]
        hit = False
        for fd, acc in cands:
            if time.time() - t0 > SECS or calls_left(db) <= 0:
                return " / ".join(log[-6:]) or "상한", got
            txt = release_html(int(cik), acc)
            if not txt:
                asked.append(acc)                        # 실적 보도자료가 아니다(주총·배당·지분 공시) — 다시 안 연다
                continue
            names = sorted({n for rr in st["q"].values() for x in rr for n in (x.get("seg") or {})}) or \
                (B.SEG.get(key) or {}).get("names") or []
            try:
                ans = ai_seg.claude(PROMPT.format(name=code, key=key, doc=f"6-K filed {fd}", cur=st["cur"],
                                                  segs=json.dumps(names[:12], ensure_ascii=False), tables=txt,
                                                  span="SIX-MONTH (half-year)" if m_want == 6 else "THREE-MONTH (quarterly)",
                                                  m=m_want))
            except ai_seg.Limited as ex:
                db["limited"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
                return " / ".join(log[-6:] + [f"한도: {str(ex)[:80]}"]), got
            except Exception as ex:                      # noqa: BLE001 — 한 서류가 실행을 죽이지 않게
                log.append(f"{fd} 실패 {ex!r}"[:90])
                continue
            db["calls"] += 1
            calls += 1
            asked.append(acc)
            mm = re.search(r"\{.*\}", ans or "", re.S)
            try:
                items = json.loads(mm.group(0)).get("items") if mm else None
            except ValueError:
                items = None
            ok = accept(items, ends, fin, acc, fd, m_want)
            if verbose:
                print(key, fd, json.dumps(items, ensure_ascii=False)[:600])
            for ee, rec in ok.items():
                lst = st["q"].setdefault(ee, [])
                if not any(x["a"] == acc for x in lst):
                    lst.append(rec)
                    lst.sort(key=lambda x: x.get("d", ""))           # 나중 공시가 뒤(재표시된 값)
                    del lst[:-3]
            n_seg = sum(1 for r in ok.values() if r.get("seg"))
            log.append(f"{fd} 기간 {len(ok)} · 사업부 {n_seg}")
            got += len(ok)
            if ok:
                dry = 0
                if e in ok:
                    hit = True
                    break
            else:
                dry += 1
                if dry >= DRY_N:
                    db.setdefault("dry", {})[key] = date.today().isoformat()
                    return " / ".join(log[-6:] + ["연달아 빈손 — 쉰다"]), got
        if hit:                                          # 전년 같은 분기도 같이 왔다 — 그 분기는 건너뛴다
            prev = min(ends, key=lambda x: abs((_d(e) - _d(x)).days - 365))
            if prev in st["q"] and prev in todo and (st["q"][prev][-1].get("seg") or not st["q"][e][-1].get("seg")):
                todo.remove(prev)
    # 사업부를 한 번도 못 얻고 총매출만 얻은 회사 — 단일 부문이거나 보도자료에 사업부 표가 없다. 사업부만 비는 분기는 다시 안 묻는다.
    if st["q"] and calls >= 4 and not any(x.get("seg") for rr in st["q"].values() for x in rr):
        db.setdefault("noseg", {})[key] = date.today().isoformat()
    return " / ".join(log[-6:]), got


def calls_left(db):
    import ai_seg
    if MAX_TOK and ai_seg.USED["in"] + ai_seg.USED["out"] >= MAX_TOK:
        return 0
    return MAX_CALLS - db["calls"]


def main():
    args = sys.argv[1:]
    only = {a for a in args if ":" in a}
    plan = "--plan" in args
    import ai_seg
    if not plan:
        if ai_seg.ENGINE == "codex":
            if not shutil.which("codex") or not (Path.home() / ".codex" / "auth.json").exists():
                print("codex CLI 또는 로그인 파일이 없다 — 아무것도 안 한다.")
                return
        elif not shutil.which("claude") or not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            print("claude CLI 또는 구독 토큰이 없다 — 아무것도 안 한다.")
            return
    t0 = time.time()
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    db.setdefault("v", 1)
    db.setdefault("stocks", {})
    db.setdefault("asked", {})
    db["calls"] = 0
    lim = db.get("limited")
    if lim and not only and not plan and (datetime.now(timezone.utc) - datetime.fromisoformat(lim)).total_seconds() < 1500:
        print(f"구독 한도에 걸린 지 25분이 안 됐다({lim}) — 이번엔 쉰다.")
        return
    db.pop("limited", None)
    import build as B
    import ai_fill as af
    tg = af.targets(B, only)                             # 곧 발표할 회사 먼저 · 시총 큰 순(은행·리츠 제외) — af.CAPS·af.SOON 도 채운다
    try:
        raw_us = json.loads((HERE / "data" / "financials.json").read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        raw_us = {}
    holes = af.HOLES or {}
    todo = []
    for key in tg:
        if not key.startswith("us:") or not is_fpi(key[3:], raw_us) or (key[3:] in JP_ADR and key not in only):
            continue
        if not only:
            if (af.CAPS.get(key) or 0) < MIN_CAP:
                continue
            d = (db.get("dry") or {}).get(key)
            if d and (date.today() - _d(d)).days < DRY_DAYS:
                continue
        miss = need(B, key, db, holes)
        if miss:
            todo.append((key, miss))
    soon = sum(1 for k, _ in todo if k in af.SOON)
    print(f"외국 기업 보도자료 읽기: 대상 {len(todo):,}곳(곧 발표 {soon}) · 빈 분기 {sum(len(m) for _, m in todo):,}")
    if plan:
        for k, m in todo[:60]:
            print(f"  {k:<10} 시총 {af.CAPS.get(k) or 0:>7.1f} · 빈 분기 {len(m):>2} ({m[0]} ~ {m[-1]})")
        return
    done = got = 0
    for key, miss in todo:
        if time.time() - t0 > SECS or calls_left(db) <= 0 or db.get("limited"):
            break
        try:
            log, n = one(B, key, miss, db, t0, bool(only))
        except Exception as ex:                          # noqa: BLE001
            log, n = f"실패 {ex!r}"[:160], 0
        done += 1
        got += n
        print(f"  {key}: {log}")
        save(db)
    save(db)
    u = ai_seg.USED
    print(f"토큰: 엔진 {ai_seg.ENGINE} · 호출 {u['n']} · 입력 {u['in']:,} · 출력 {u['out']:,} · 호출당 {(u['in'] + u['out']) // max(u['n'], 1):,}")
    print(f"외국 기업 보도자료 읽기: 회사 {done} · 검산 통과 기간 {got} · 남은 회사 {len(todo) - done}")


def save(db):
    db["ts"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
