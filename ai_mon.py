# -*- coding: utf-8 -*-
"""월매출 PDF 중 표 읽기 규칙(montable)이 못 읽은 것만 AI 로 읽는다(회원님 11/03: "토큰 최소화하며 다 채워").

- 대상: data/monthly_nums_jp.json 의 skip 중 '표없음'(TDnet 첨부 · JPX 보관소 옛 공시). 시총 큰 회사부터.
- PDF 에서 글자만 꺼내(pdftext) 앞 2,500자를 넘긴다 — 그림·표 좌표는 안 넘긴다. 한 번에 네 건씩 묶어 묻는다.
- **검산**: 돌려받은 전년비·금액 숫자가 **원문 글자에 그대로 있어야** 싣는다(지어낸 숫자를 막는다). 전년비는 30~300 사이.
  달은 발표일보다 앞이어야 하고 2년 넘게 앞서면 안 된다.
- 한 번 물은 공시는 다시 안 묻는다(asked). 한도에 걸리면 그 자리에서 멈춘다(추가 결제 금지 — ai_seg.claude 규칙).

data/monthly_ai_jp.json  {"codes": {코드: {"name", "months": {YYYY-MM: {day, doc, yoy, rev, metric}}, "yoy_label", "amount_label"}},
                          "asked": {url: 날짜}}
build.py 의 load_monthly_nums 가 'AI 판독' 소스로 쓴다(다른 소스와 기준이 같거나 겹치는 달이 맞을 때만 잇는다).
"""
import json
import os
import re
import time
from datetime import date
from pathlib import Path

import ai_seg
import pdftext
import scrape_mon_jp as M

HERE = Path(__file__).parent
SHARD = os.environ.get("AIMON_SHARD", "")              # "0/3" — 갈래마다 제 파일(커밋이 안 부딪힌다)
OUT = HERE / "data" / (f"monthly_ai_jp_s{SHARD.split('/')[0]}.json" if SHARD else "monthly_ai_jp.json")
BATCH = int(os.environ.get("AIMON_BATCH", "6"))
MAX_CALLS = int(os.environ.get("AIMON_MAX", "40"))
SECS = int(os.environ.get("AIMON_SECS", "1500"))
CHARS = 2500

PROMPT = """아래는 일본 상장회사의 월차(月次) 매출 공시 PDF 에서 꺼낸 글자다(표가 한 줄로 펴져 있어 칸이 섞여 있을 수 있다).
공시마다 **회사 전체의 달별 매출 전년동월비(%)** 를 읽어라. 既存店(기존점)과 全店(전점)이 둘 다 있으면 全店, 全店이 없으면 既存店.
금액(売上高)이 달마다 있으면 그 숫자도(공시에 적힌 단위 그대로). 객수·객단가·점포수·가동률은 매출이 아니다 — 쓰지 마라.
전년비는 '前年同月比 105.2%' 처럼 100 기준으로 적어라('5.2%増' 이면 105.2, '△3.1%' 이면 96.9).
달은 YYYY-MM (공시일보다 앞선 달). 읽을 수 없으면 빈 객체.

JSON 한 덩이로만 답하라: {{"공시번호": {{"metric": "全店|既存店|売上高", "unit": "百万円 같은 단위 또는 빈칸",
  "months": {{"YYYY-MM": {{"yoy": 숫자 또는 null, "rev": 숫자 또는 null}}}}}}}}

{items}
"""


def num_in(text, v):
    """숫자가 원문에 그대로 있는가 — 105.2 · 5.2(증감률로 적힌 경우) · 1,234 꼴 모두 본다."""
    t = text.replace(",", "").replace("，", "")
    cands = {f"{v:g}", f"{v:.1f}"}
    if 30 <= v <= 300:
        d = round(abs(v - 100), 1)
        cands |= {f"{d:g}", f"{d:.1f}"}
    return any(re.search(r"(?<![\d.])" + re.escape(c) + r"(?![\d])", t) for c in cands)


def main():
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    db.setdefault("codes", {})
    asked = db.setdefault("asked", {})
    asked_all = dict(asked)
    for f in (HERE / "data").glob("monthly_ai_jp*.json"):       # 다른 갈래·옛 파일이 이미 물은 공시는 건너뛴다
        if f != OUT:
            try:
                asked_all.update(json.loads(f.read_text(encoding="utf-8")).get("asked", {}))
            except (OSError, ValueError):
                pass
    nums = json.loads((HERE / "data" / "monthly_nums_jp.json").read_text(encoding="utf-8"))
    try:                                                     # 3분 루프가 막 못 읽은 새 공시도 — 가장 먼저 읽는다
        fr = json.loads((HERE / "data" / "monthly_nums_fresh_jp.json").read_text(encoding="utf-8"))
        nums["skip"] = {**(nums.get("skip") or {}), **(fr.get("skip") or {})}
        for c, r in (fr.get("codes") or {}).items():
            nums["codes"].setdefault(c, r)
    except (OSError, ValueError):
        pass
    rows = json.loads((HERE / "data" / "monthly_jp.json").read_text(encoding="utf-8")).get("rows", [])
    meta = {r["doc"]: (r["code"], r["date"], r.get("name", ""), r.get("title", "")) for r in rows if r.get("doc")}
    try:
        caps = json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8"))
        caps = caps.get("caps", caps)
    except (OSError, ValueError):
        caps = {}
    try:
        hist = json.loads((HERE / "data" / "monthly_hist_jp.json").read_text(encoding="utf-8")).get("meta", {})
    except (OSError, ValueError):
        hist = {}
    todo = []
    for url, why in (nums.get("skip") or {}).items():
        if why != "표없음" or url in asked_all:
            continue
        m = re.search(r"/disc/(\w{4})0/", url)
        if url in meta:
            code, day, name, title = meta[url]
        elif m and url in hist:
            code, (day, title) = m.group(1), hist[url]
            name = (nums["codes"].get(code) or {}).get("name", "")
        else:
            continue
        todo.append((url, code, day, name, title))
    import zlib
    from markets import excluded
    todo = [t for t in todo if not excluded("jp", t[3] or (nums["codes"].get(t[1]) or {}).get("name", ""))]   # 리츠·은행 등은 월매출 화면 밖
    if SHARD:
        i_sh, n_sh = (int(x) for x in SHARD.split("/"))
        todo = [t for t in todo if zlib.crc32(t[0].encode()) % n_sh == i_sh]
    # **빈 달이 많은 회사부터**(회원님 11/04: "일본 월매출이 다 엉망") — 이미 가진 달 수가 적은 회사, 그다음 시총 큰 순, 최근 공시부터.
    have = {}
    for src in (nums.get("codes") or {}, ):
        for c, r in src.items():
            have[c] = len(r.get("months") or {})
    for c, r in db["codes"].items():
        have[c] = have.get(c, 0) + len(r.get("months") or {})
    from datetime import timedelta as _td
    fresh_cut = (date.today() - _td(days=3)).isoformat()
    todo.sort(key=lambda x: (x[2] < fresh_cut, min(have.get(x[1], 0), 24) // 6, -(caps.get("jp:" + x[1]) or 0), -int(x[2].replace("-", "") or 0)))
    print(f"월매출 AI 판독: 남은 {len(todo)}건")
    t0, calls, got = time.time(), 0, 0
    for i in range(0, len(todo), BATCH):
        if calls >= MAX_CALLS or time.time() - t0 > SECS:
            break
        part, texts = [], {}
        for url, code, day, name, title in todo[i:i + BATCH]:
            data = M.get(url) or M.get(re.sub(r"https://www\.release\.tdnet\.info/inbs/",
                                               f"https://www2.jpx.co.jp/disc/{code}0/", url))
            asked[url] = date.today().isoformat()
            if not data:
                continue
            try:
                tx = "\n".join(pdftext.extract_lines(data, 4))[:CHARS]
            except Exception:                                # noqa: BLE001
                continue
            if len(tx) < 60:
                continue
            nid = str(len(part) + 1)
            part.append((nid, url, code, day, name, title))
            texts[nid] = tx
        if not part:
            continue
        items = "\n\n".join(f"### 공시번호 {nid} — {code} {name} · 공시일 {day} · {title}\n{texts[nid]}"
                            for nid, url, code, day, name, title in part)
        try:
            calls += 1
            ans = ai_seg.claude(PROMPT.format(items=items))
        except ai_seg.Limited as e:
            print(f"  ! 한도 — 멈춘다: {e}"[:200])
            for p in part:
                asked.pop(p[1], None)
            break
        except Exception as e:                               # noqa: BLE001
            print(f"  ! {e!r}"[:200])
            continue
        mm = re.search(r"\{.*\}", ans, re.S)
        try:
            res = json.loads(mm.group(0)) if mm else {}
        except ValueError:
            res = {}
        for nid, url, code, day, name, title in part:
            r = res.get(nid) or {}
            tx = texts[nid]
            rec = db["codes"].setdefault(code, {"name": name, "months": {}})
            for p, v in (r.get("months") or {}).items():
                if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", p or ""):
                    continue
                if p >= day[:7] or p < f"{int(day[:4]) - 2}{day[4:7]}":
                    continue
                yoy, rev = v.get("yoy"), v.get("rev")
                ok_y = isinstance(yoy, (int, float)) and 30 <= yoy <= 300 and num_in(tx, yoy)
                ok_r = isinstance(rev, (int, float)) and rev > 0 and num_in(tx, rev)
                if not (ok_y or ok_r):
                    continue
                cur = rec["months"].get(p)
                if cur and cur.get("day", "") > day:
                    continue
                mt = r.get("metric") or ""
                e = {"day": day, "doc": url, "metric": mt if "売上" in mt else mt + "売上高"}   # 빌드가 매출성 지표만 받는다(_tdnet_series)
                if ok_y:
                    e["yoy"] = round(float(yoy), 1)
                if ok_r:
                    e["rev"] = rev
                    if r.get("unit"):
                        e["unit"] = r["unit"]
                rec["months"][p] = e
                got += 1
            rec["yoy_label"] = r.get("metric") or rec.get("yoy_label", "")
            rec["amount_label"] = "売上高" if any("rev" in x for x in rec["months"].values()) else ""
        OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    db["codes"] = {k: v for k, v in db["codes"].items() if v.get("months")}
    OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"월매출 AI 판독: 물어본 {calls}번 · 담은 달 {got} · 회사 {len(db['codes'])}")


if __name__ == "__main__":
    main()
