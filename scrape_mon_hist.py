# -*- coding: utf-8 -*-
"""월매출 옛 공시 거슬러 받기(회원님 11/03: "지난 날들 공백이 너무 많다").

TDnet 목록·첨부는 한 달쯤만 남는다 — 그래서 월매출 이력이 수집을 시작한 날부터만 쌓였다. 그런데 같은 공시가
**JPX 공시 보관소**(www2.jpx.co.jp/disc/{코드}0/{파일}, 5~6년치)에 그대로 있고, 목록은 yanoshin TDnet API 가 준다
(일본 지역별 매출 `scrape_geo_jp.py` 와 같은 길). 회사마다 목록 한 번 + 월매출 PDF 를 2024-01 부터 받아 **같은 규칙**
(`montable.read`)으로 읽고 `data/monthly_nums_jp.json` 에 얹는다(`scrape_mon_jp.merge` — 새 공시가 헌 값을 이긴다).

대상은 TDnet 으로 월매출을 내는 회사(monthly_jp.json)와 이미 수치가 있는 회사. 시총 큰 순. 한 번 끝낸 회사는
`data/monthly_hist_jp.json` 에 적고 30일 뒤에 다시 본다(그 사이 새 달은 평소 수집기가 받는다).
못 읽은 PDF 는 그 회사 이름표와 함께 skip 에 적는다 — AI 판독(ai_mon.py)이 그것만 본다.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import montable
import pdftext
import scrape_jp_tdnet as T
import scrape_mon_jp as M

HERE = Path(__file__).parent
STATE = HERE / "data" / "monthly_hist_jp.json"
SINCE = os.environ.get("MON_HIST_SINCE", "2024-01-01")
BUDGET = float(os.environ.get("MON_HIST_SECS", "300"))
WORKERS = int(os.environ.get("MON_HIST_WORKERS", "3"))
AGAIN_DAYS = 30


def listing(code):
    raw = M.get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=600", timeout=30)
    try:
        j = json.loads(raw or b"{}")
    except ValueError:
        return None
    out = []
    for it in (x.get("Tdnet", x) for x in (j.get("items") or [])):
        t, du, d = it.get("title") or "", it.get("document_url") or "", (it.get("pubdate") or "")[:10]
        if d >= SINCE and du.lower().endswith(".pdf") and T.is_monthly(t):
            out.append({"date": d, "title": t, "fn": du.rsplit("/", 1)[-1]})
    return out


def read_one(code, it):
    url = f"https://www2.jpx.co.jp/disc/{code}0/{it['fn']}"
    data = M.get(url, timeout=60)
    if not data:
        return url, None, "받기실패"
    try:
        got = montable.read(data, it["date"], title=it["title"])
    except Exception:                                     # noqa: BLE001
        return url, None, "터짐"
    if got:
        return url, got, ""
    try:
        opened = len(pdftext.extract_text(data, 4)) > 40
    except Exception:                                     # noqa: BLE001
        opened = False
    return url, None, ("암호" if not opened and pdftext.is_encrypted(data) else "표없음")


def main():
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("codes", {})
    by, done, skip = M.load()
    rows = json.loads((HERE / "data" / "monthly_jp.json").read_text(encoding="utf-8")).get("rows", [])
    names = {r["code"]: r.get("name", "") for r in rows}
    want = set(names) | set(by)
    # IR·기사·대조표·원장에만 있는 회사도 — 예전에 TDnet 으로 월차를 냈으면 JPX 보관소에 있다(11/04 2792·2502·7604·3544…)
    for f in ("monthly_ir_jp.json", "monthly_web_jp.json", "monthly_ledger_jp.json", "monthly_reviewed_jp.json"):
        try:
            d = json.loads((HERE / "data" / f).read_text(encoding="utf-8"))
            cs = d.get("codes") or d.get("companies") or {}
            want |= set(cs if isinstance(cs, dict) else [c.get("code") for c in cs if isinstance(c, dict)])
        except (OSError, ValueError):
            pass
    want = {c for c in want if c and len(c) == 4}
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8"))
        caps = caps.get("caps", caps)
    except (OSError, ValueError):
        caps = {}
    again = (date.today() - timedelta(days=AGAIN_DAYS)).isoformat()
    todo = sorted((c for c in want if (st["codes"].get(c) or "") < again), key=lambda c: -(caps.get("jp:" + c) or 0))
    print(f"월매출 옛 공시: 대상 {len(want)}곳 · 이번에 볼 곳 {len(todo)}")
    t0, n_co, n_doc, n_mo, n_miss = time.time(), 0, 0, 0, 0
    with ThreadPoolExecutor(WORKERS) as ex:
        for code in todo:
            if time.time() - t0 > BUDGET:
                break
            items = listing(code)
            time.sleep(0.4)                                  # yanoshin — 한 사람이 돌리는 무료 API
            if items is None:
                continue
            items = [it for it in items
                     if f"https://www2.jpx.co.jp/disc/{code}0/{it['fn']}" not in done
                     and f"https://www2.jpx.co.jp/disc/{code}0/{it['fn']}" not in skip]
            finished = True
            for i in range(0, len(items), WORKERS * 2):
                if time.time() - t0 > BUDGET:
                    finished = False
                    break
                part = items[i:i + WORKERS * 2]
                for it, (url, got, why) in zip(part, ex.map(lambda it: read_one(code, it), part)):
                    n_doc += 1
                    if why == "받기실패":
                        continue
                    done.add(url)
                    if got:
                        rec = by.setdefault(code, {"name": names.get(code, "")})
                        n_mo += M.merge(rec, got, url, it["date"])
                    else:
                        skip[url] = why
                        st.setdefault("meta", {})[url] = [it["date"], it["title"]]      # AI 판독(ai_mon.py)이 발표일·제목을 안다
                        n_miss += 1
                time.sleep(0.3)
            if finished:
                st["codes"][code] = date.today().isoformat()
            n_co += 1
            if n_co % 5 == 0:
                M.save(by, done, skip)
                STATE.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    M.save(by, done, skip)
    STATE.write_text(json.dumps(st, separators=(",", ":")), encoding="utf-8")
    print(f"  회사 {n_co} · PDF {n_doc} · 담은 달 {n_mo} · 못 읽음 {n_miss} · 끝낸 회사 누적 {len(st['codes'])}")


if __name__ == "__main__":
    main()
