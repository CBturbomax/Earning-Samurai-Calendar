# -*- coding: utf-8 -*-
"""일본 월매출(月次)의 **숫자**를 첨부 PDF 에서 뽑는다.

`scrape_jp_tdnet.py` 가 '언제 무엇을 냈나'와 첨부 주소까지 담아 둔다. 여기서는
그 첨부를 받아 `montable.py` 로 **달별 매출·전년동월비**를 읽는다.

  data/monthly_jp.json   (누가 언제 냈나)  ->  여기  ->  data/monthly_nums_jp.json

**왜 따로 두나.** 목록 훑기는 HTML 한 장이라 3분마다 돌려도 싸지만, 첨부는
종목마다 200KB 쯤이다. 파일 주인을 갈라 두는 이 저장소의 규칙대로 새 파일을
쓰고, 한 번 뜯어 본 공시는 주소로 건너뛴다.

**쌓아 두고 지우지 않는다.** TDnet 은 첨부를 한 달쯤만 두므로 창 밖으로 밀려난
달은 어느 길로도 다시 못 받는다. 여기 쌓인 만큼이 이력의 전부고, 돌수록 길어진다.

**못 읽으면 안 담는다.** 암호가 걸렸거나(실측 70건 중 4건) 표가 그림이거나
달 이름표를 못 찾으면 그 공시는 숫자 없이 지나간다 — 화면에는 지금처럼 원문
링크만 남는다. 없는 값을 지어 넣지 않는다.

  python scrape_mon_jp.py            # 새로 나온 공시만
  python scrape_mon_jp.py --probe 7685   # 한 종목만 뜯어 본다
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import montable
import pdftext

HERE = Path(__file__).resolve().parent
SRC = HERE / "data" / "monthly_jp.json"
OUT = HERE / "data" / "monthly_nums_jp.json"

# 뜯는 규칙이 바뀌면 올린다. **본 공시 기록만** 비우고 모아둔 값은 남긴다 —
# 창 밖으로 밀려난 공시는 다시 못 받으므로 값을 버리면 영영 잃는다.
PARSE_VER = 2

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PER_RUN = int(os.environ.get("MON_PDF_PER_RUN", "40"))
PAUSE = 0.35


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return raw
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError, ValueError):
        return None


def load():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}, set(), {}
    by = old.get("codes") or {}
    done = set(old.get("done") or [])
    skip = old.get("skip") or {}
    if old.get("pv") != PARSE_VER:
        # **건너뛴 것도 같이 비운다.** 규칙이 넓어지면 전에 '표없음'으로 넘긴
        # 공시에서 이제 표가 나올 수 있는데, skip 을 남겨 두면 영영 안 본다.
        print(f"  뜯는 규칙이 바뀌었다(pv {old.get('pv')} -> {PARSE_VER})."
              f" 모아둔 값은 두고 본 공시·건너뛴 공시 기록을 비운다.")
        done, skip = set(), {}
    return by, done, skip


def save(by, done, skip):
    months = sum(len(v.get("months") or {}) for v in by.values())
    payload = {
        "pv": PARSE_VER,
        "source": "TDnet 월매출 공시 첨부 PDF — 표에서 직접 읽는다",
        "note": ("달 이름표 줄을 찾아 아래 줄의 값을 x 가 가장 가까운 이름표에 "
                 "붙인다. 붙일 이름표가 없으면 버린다. 암호가 걸렸거나 표가 "
                 "그림인 공시는 숫자 없이 지나간다."),
        "codes": by,
        "companies": len(by),
        "months": months,
        # 본 공시는 주소로 기억한다. 없으면 매 실행 수백 건을 다시 받는다.
        "done": sorted(done)[-6000:],
        # 뜯어도 아무것도 안 나온 공시. 다시 두드릴 이유가 없다.
        "skip": skip,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return len(by), months


def merge(rec, got, doc, day):
    """읽어낸 달들을 회사 기록에 얹는다. **새 공시가 헌 값을 이긴다** —
    속보 뒤에 본보고를 내는 회사가 있어서(8237) 나중 것이 옳다."""
    months = rec.setdefault("months", {})
    n = 0
    for r in got["rows"]:
        p = r["period"]
        cur = months.get(p)
        if cur and cur.get("day", "") > day:
            continue
        m = {"day": day, "doc": doc}
        for k in ("rev", "yoy", "unit", "metric"):
            if r.get(k) is not None:
                m[k] = r[k]
        months[p] = m
        n += 1
    rec["amount_label"] = got.get("amount_label") or rec.get("amount_label", "")
    rec["yoy_label"] = got.get("yoy_label") or rec.get("yoy_label", "")
    return n


def main():
    if "--probe" in sys.argv:
        want = sys.argv[sys.argv.index("--probe") + 1]
        rows = json.loads(SRC.read_text(encoding="utf-8"))["rows"]
        hit = [r for r in rows if r["code"] == want and r.get("doc")]
        for r in sorted(hit, key=lambda r: r["date"])[-2:]:
            print(f"── {r['date']} {r['title'][:50]}\n   {r['doc']}")
            data = get(r["doc"])
            if not data:
                print("   받기 실패"); continue
            if pdftext.is_encrypted(data):
                print("   암호가 걸려 있다"); continue
            for ln in pdftext.extract_lines(data, 4)[:24]:
                print("   |", ln[:120])
            got = montable.read(data, r["date"])
            print("   ->", json.dumps(got, ensure_ascii=False)[:600] if got else "표를 못 읽었다")
        return

    try:
        rows = json.loads(SRC.read_text(encoding="utf-8"))["rows"]
    except (ValueError, OSError) as e:
        print(f"월매출 목록을 못 읽었다: {e}")
        return
    by, done, skip = load()

    # **새 공시부터** 본다. 첨부는 한 달쯤만 남으므로 늦게 보면 영영 못 받는다.
    todo = [r for r in sorted(rows, key=lambda r: r["date"], reverse=True)
            if r.get("doc") and r["doc"] not in done and r["doc"] not in skip]
    print(f"월매출 공시 {len(rows)}건 · 아직 안 뜯은 것 {len(todo)}건 "
          f"· 이번에 {min(len(todo), PER_RUN)}건")

    read = got_n = miss = enc = fail = 0
    for r in todo[:PER_RUN]:
        data = get(r["doc"])
        time.sleep(PAUSE)
        if not data:
            fail += 1
            continue
        done.add(r["doc"])
        if pdftext.is_encrypted(data):
            enc += 1
            skip[r["doc"]] = "암호"
            continue
        try:
            got = montable.read(data, r["date"])
        except Exception as e:                      # noqa: BLE001
            # 깨진 PDF 하나가 실행 전체를 죽이지 않게 한다.
            print(f"  ! {r['code']} {type(e).__name__}: {e}")
            skip[r["doc"]] = "터짐"
            continue
        read += 1
        if not got:
            miss += 1
            skip[r["doc"]] = "표없음"
            continue
        rec = by.setdefault(r["code"], {"name": r.get("name", "")})
        got_n += merge(rec, got, r["doc"], r["date"])
        if read % 20 == 0:
            save(by, done, skip)

    n_co, n_mo = save(by, done, skip)
    print(f"  읽음 {read} · 표를 찾음 {read - miss} · 못 찾음 {miss} "
          f"· 암호 {enc} · 받기실패 {fail} · 이번에 담은 달 {got_n}")
    print(f"  쌓인 것: {n_co}개사 · {n_mo}개월")


if __name__ == "__main__":
    main()
