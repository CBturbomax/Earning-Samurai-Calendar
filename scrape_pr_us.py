# -*- coding: utf-8 -*-
"""미국 실적 보도자료 — '회사가 한 말'을 8-K 에서 원문으로 담는다.

실적 브리핑 칸에 "왜 잘 됐나 / 안 됐나 / 가이던스를 얼마나 올렸나"를 적으려면
수치만으로는 안 된다 — 그건 회사가 **말**로 하는 것이라서다. 미국 회사는 실적을
발표하는 그 순간 8-K(Item 2.02)에 보도자료(EX-99)를 붙여 SEC 에 낸다. 공식
경로고, 우리가 이미 쓰는 EDGAR 다(scrape_seg_edgar 와 같은 서버).

여기서는 **원문만** 담는다 — CEO 코멘트(따옴표 인용)와 가이던스/전망 문단.
한국어로 옮기는 것은 descriptions.py 와 같은 방식으로 사람이(세션에서 일괄로)
하고, briefs.py 에 (종목, 분기) 열쇠로 적는다. 화면은 그 분기가 최신일 때만
내므로 낡은 말이 새 분기에 붙지 않는다.

경로:
  회사별 제출 목록  https://data.sec.gov/submissions/CIK##########.json
                    (form + items 가 실려 있어 8-K 중 실적(2.02)만 고를 수 있다)
  서류 목록         /Archives/edgar/data/{cik}/{접수번호}/index.json
  보도자료          그중 ex99·press 가 든 .htm

결과: data/briefs_us.json  {stocks: {SYM: {acc, date, quote, outlook, head}}}
"""
import html
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import scrape_seg_sec as ss
from scrape_seg_edgar import get          # gzip 켠 EDGAR fetch. 404 는 None.

HERE = Path(__file__).parent
OUT = HERE / "data" / "briefs_us.json"

PER_RUN = int(os.environ.get("PR_PER_RUN", "120"))
PAUSE = float(os.environ.get("PR_PAUSE", "0.4"))
FRESH_DAYS = int(os.environ.get("PR_FRESH_DAYS", "14"))   # 발표 며칠 안쪽을 신선으로 보나
GIVE_UP_AFTER = 6
PR_VER = 1

SUBS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FILES = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
DOC = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"

TAGS = re.compile(r"<[^>]+>")
# 따옴표 인용 — CEO 가 실적을 설명하는 문장. 굽은 따옴표가 보통이고 곧은 것도 온다.
QUOTE = re.compile(r'[“"]([^”"]{120,900})[”"]')
# 가이던스가 사는 자리. 머리글이 이 낱말들로 온다.
OUTLOOK_HEAD = re.compile(
    r"(?:financial\s+)?(?:outlook|guidance|forecast)(?:\s+for|\s*:)?", re.I)
GUIDE_SENT = re.compile(
    r"[^.!?]*\b(?:guidance|outlook|expect[si]?|anticipat\w+|full[\s-]year|"
    r"fiscal\s+(?:year\s+)?20\d\d|raises?|lowers?|reaffirm\w*)\b[^.!?]*[.!?]", re.I)


def tidy(s, limit):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:limit].rstrip() if len(s) > limit else s


def want():
    """받을 종목: 최근 발표(FRESH_DAYS 안쪽)가 있는 것부터, 그 안에서 시총 큰 순.

    브리핑은 '방금 발표한 회사'를 읽는 칸이라 신선한 것부터 간다. 발표가 오래된
    회사의 보도자료는 어차피 지난 분기 이야기다.
    """
    f = HERE / "data" / "earnings_us.json"
    if not f.exists():
        return []
    try:
        rows = json.loads(f.read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return []
    today = date.today().isoformat()
    floor = (date.today() - timedelta(days=FRESH_DAYS)).isoformat()
    best = {}
    for r in rows:
        sym, d = r.get("code"), r.get("date") or ""
        if not sym:
            continue
        cap = r.get("cap") or 0
        fresh = 1 if floor <= d <= today else 0
        cur = best.get(sym)
        if not cur or (fresh, cap) > (cur[0], cur[1]):
            best[sym] = (fresh, cap)
    ordered = sorted(best.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
    return [sym for sym, _ in ordered]


def latest_earnings_8k(cik):
    """가장 최근의 실적 8-K (Item 2.02). (접수번호, 제출일) 또는 None."""
    blob = get(SUBS.format(cik=cik), binary=False)
    if blob is None:
        return None
    try:
        recent = json.loads(blob)["filings"]["recent"]
    except (ValueError, KeyError):
        return None
    forms = recent.get("form", [])
    items = recent.get("items", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        if "2.02" not in (items[i] if i < len(items) else ""):
            continue
        return accs[i].replace("-", ""), dates[i] if i < len(dates) else ""
    return None


def press_release(cik, acc):
    """8-K 의 보도자료 exhibit 을 골라 본문 텍스트로. 없으면 None."""
    blob = get(FILES.format(cik=cik, acc=acc), binary=False)
    if blob is None:
        return None
    try:
        names = [it["name"] for it in json.loads(blob)["directory"]["item"]]
    except (ValueError, KeyError):
        return None
    htm = [n for n in names if n.lower().endswith((".htm", ".html"))]
    ex = [n for n in htm if re.search(r"ex[-_]?99|991|992|press|earnings", n, re.I)]
    # exhibit 이름이 규칙 없는 회사도 있다 — 그때는 R 파일·색인을 뺀 본문 후보 중
    # 이름이 가장 긴 것(대개 본문)을 본다. 아예 없으면 접는다.
    pick = ex or [n for n in htm if not re.match(r"index|.*-index", n, re.I)]
    if not pick:
        return None
    name = sorted(pick, key=lambda n: (0 if n in ex else 1, len(n)))[0]
    doc = get(DOC.format(cik=cik, acc=acc, name=name), binary=False)
    if doc is None:
        return None
    txt = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", doc)
    txt = TAGS.sub(" ", txt)
    # EDGAR 문서는 따옴표·대시가 &#8220; 같은 실체참조로 온다. 안 풀면 인용문
    # 정규식이 한 건도 못 문다(probe 10차에서 실제로 그랬다).
    txt = html.unescape(txt)
    txt = re.sub(r"[​﻿\xa0]", " ", txt)
    return re.sub(r"\s+", " ", txt)


def digest(txt):
    """보도자료 본문 -> {head, quote, outlook}. 원문 그대로, 자르기만 한다."""
    out = {}
    head = txt[:1200]
    out["head"] = tidy(head, 500)
    m = QUOTE.search(txt)
    if m:
        out["quote"] = tidy(m.group(1), 700)
    h = OUTLOOK_HEAD.search(txt)
    if h:
        out["outlook"] = tidy(txt[h.start():h.start() + 1400], 1000)
    else:
        sents = GUIDE_SENT.findall(txt[:20000])
        if sents:
            out["outlook"] = tidy(" ".join(sents[:5]), 1000)
    return out


def main():
    probe = [a for a in sys.argv[1:] if not a.startswith("-")] \
        if "--probe" in sys.argv else None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            d = json.loads(OUT.read_text(encoding="utf-8"))
            if d.get("v") == PR_VER:
                old = d.get("stocks", {})
        except (ValueError, OSError):
            pass

    cikmap = ss.cik_map()
    syms = probe or want()[:PER_RUN]
    stocks = dict(old)
    got = streak = 0
    for sym in syms:
        cik = ss.cik_of(cikmap, sym)
        if not cik:
            continue
        try:
            hit = latest_earnings_8k(cik)
            time.sleep(PAUSE)
            if not hit:
                if probe:
                    print(f"\n=== {sym} === 실적 8-K(Item 2.02)가 최근 목록에 없다")
                continue
            acc, day = hit
            if not probe and (stocks.get(sym) or {}).get("acc") == acc:
                continue                     # 이미 뜯어본 보도자료다
            txt = press_release(cik, acc)
            time.sleep(PAUSE)
            streak = 0
        except ss.Blocked as e:
            streak += 1
            print(f"  {sym} 막힘: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("  연속으로 막혔다. 여기서 접는다.", file=sys.stderr, flush=True)
                break
            continue
        if not txt:
            if probe:
                print(f"\n=== {sym} ({day}) === 보도자료 exhibit 을 못 골랐다")
            continue
        d = digest(txt)
        if not d.get("quote") and not d.get("outlook"):
            if probe:
                print(f"\n=== {sym} ({day}) === 인용문도 가이던스도 없다 "
                      f"(본문 {len(txt):,}자)")
            continue                        # 말이 없는 서류다(표만 있는 8-K)
        rec = {"acc": acc, "date": day, **d}
        stocks[sym] = rec
        got += 1
        if probe:
            print(f"\n=== {sym} ({day}) ===")
            for k in ("head", "quote", "outlook"):
                if rec.get(k):
                    print(f"  [{k}] {rec[k][:300]}")
        elif got % 20 == 0:
            save(stocks, quiet=True)
    if probe:
        return
    save(stocks, got=got)


def save(stocks, got=None, quiet=False):
    payload = {
        "v": PR_VER,
        "source": "SEC 8-K 실적 보도자료 (EX-99)",
        "note": "원문 발췌만 담는다. 한국어 요약은 briefs.py 에 사람이 적는다.",
        "count": len(stocks),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    if not quiet:
        print(f"{len(stocks):,}종목 -> {OUT.name}"
              + (f" (이번에 {got}건)" if got is not None else ""))


if __name__ == "__main__":
    main()
