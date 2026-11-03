# -*- coding: utf-8 -*-
"""회사 이름 한국어 — AI 로 '한국에서 실제로 부르는 이름'을 받는다(회원님 11/03: "일본어는 무조건 한글이나 그 기업 대표 이름으로,
영어도 꼭 영어가 아니면 내가 알 만한 한글 이름으로. 마쓰야백화점이 「송옥」으로 돼 있는 건 문제").

기계 변환(translit.py)은 한자를 한국 음으로 읽어 松屋 → 송옥, 島津 → 島津 처럼 틀리거나 못 옮긴다. 일본 회사 이름은 훈독이라
규칙으로는 못 푼다 — 그 회사를 '아는' 쪽에 묻는다.

- 대상: 일본 = 기계 변환(등급 0)·가나/한자가 남은 것·소문자 영어 낱말이 든 것, 미국·홍콩 = 한글 이름이 없는 것.
  사람이 고른 이름(companies*.py 의 NOTABLE·NAMES, translit WORDS 로 다 덮인 것)은 묻지 않는다 — 그쪽이 이긴다.
- 대문자 약어(JFE·TOWA·JMDC·IBM)는 그대로 둔다. 일반 낱말(Holdings·Group·Electric)은 한국 언론 표기로.
- 기계 검사: 한글이 있거나 원문 약어 그대로여야 하고, 가나·한자가 남으면 안 되고, 30자 안, 따옴표 없이. 못 넘으면 안 싣는다.
- 원문 이름이 바뀌면 다시 묻는다. 한도에 걸리면 그 자리에서 멈춘다(추가 결제 금지).

data/names_ko_ai_s{i}.json  {"ko": {열쇠: 이름}, "src": {열쇠: 원문}, "skip": {열쇠: 원문}}
build.py 가 사람 사전 다음으로 쓴다. 화면에는 원문이 늘 같이 뜬다(점선·원문 열) — 그 안전장치는 그대로다.
"""
import json
import os
import re
import sys
import time
import zlib
from pathlib import Path

import ai_seg

HERE = Path(__file__).parent
SHARD = os.environ.get("AINAME_SHARD", "")
OUT = HERE / "data" / (os.environ.get("AINAME_OUT") or "names_ko_ai.json")
BATCH = int(os.environ.get("AINAME_BATCH", "80"))
MAX_CALLS = int(os.environ.get("AINAME_MAX", "40"))
SECS = int(os.environ.get("AINAME_SECS", "1500"))
KJ = re.compile(r"[぀-ヿ㐀-鿿]")
HG = re.compile(r"[가-힣]")

PROMPT = """아래는 일본·미국·홍콩 상장회사 이름이다(열쇠: 원문 이름 | 지금 화면 표기). 회사마다 **한국 사람이 알아보는 한글 이름**을 적어라.
- 한국 언론·증권가에서 쓰는 이름이 있으면 그것(예: jp:8237 松屋 → 마쓰야, jp:7701 島津製作所 → 시마즈제작소, us:AAPL Apple Inc. → 애플,
  hk:00700 TENCENT → 텐센트, hk:09988 BABA-W → 알리바바, jp:6460 セガサミーホールディングス → 세가사미홀딩스).
- 일본 한자는 **그 회사의 실제 일본어 읽기**로 음차한다(한국 한자음으로 읽지 마라: 松屋은 송옥이 아니라 마쓰야). 일본어 표기법은 국립국어원 규칙.
- 중국·홍콩 회사는 한국 언론 표기(중국어 음 또는 널리 쓰는 영문 이름의 한글 음차). 한국 한자음으로 읽지 마라(騰訊 ≠ 등신).
- 대문자 약어 이름(JFE, TOWA, JMDC, IBM, 3M, BYD)은 약어를 그대로 쓰고 뒤 낱말만 옮긴다(JFE HD → JFE홀딩스, santec Holdings → 산텍홀딩스).
- Inc.·Corp.·Ltd.·Co.·plc·N.V.·-W·-SW·-B 같은 법인·주식 꼬리는 뺀다. Holdings·Group 은 홀딩스·그룹으로 남겨도 된다.
- 모르는 회사는 원문 발음대로 한글로. 일본어·한자를 남기지 마라. 따옴표를 쓰지 마라. 설명을 붙이지 마라.

JSON 한 덩이로만 답하라: {{"열쇠": "한글 이름", ...}}

{items}
"""


def ok(v, orig):
    v = (v or "").strip()
    if not v or len(v) > 30 or KJ.search(v) or '"' in v or "“" in v:
        return False
    return bool(HG.search(v)) or v.replace(" ", "") == re.sub(r"[^A-Za-z0-9&]", "", orig or "")


def targets():
    import companies
    import companies_hk
    import companies_us
    h = (HERE / "index.html").read_text(encoding="utf-8")
    D = json.loads(re.search(r'<script[^>]*id="payload"[^>]*>(.*?)</script>', h, re.S).group(1))
    hand = {"jp": companies.NOTABLE, "us": companies_us.NOTABLE,
            "hk": {**companies_hk.NOTABLE, **getattr(companies_hk, "NAMES", {})}}
    out, cap = {}, {}
    for r in D["rows"]:
        k = r[9] + ":" + r[1]
        cap[k] = max(cap.get(k, 0), r[11] or 0)
        if k in out or r[1] in hand.get(r[9], {}):
            continue
        ko, orig, lvl = r[2], r[7] or r[2], r[8]
        if r[9] == "jp":
            need = KJ.search(ko) or lvl == 0 or (not HG.search(ko) and re.search(r"[a-z]{3}", ko))
        else:
            need = not HG.search(ko)
        if need:
            out[k] = (orig, ko)
    return out, cap


def main():
    want, cap = targets()
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    for k in ("ko", "src", "skip"):
        db.setdefault(k, {})
    done = {}
    for f in (HERE / "data").glob("names_ko_ai*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            done.update(d.get("src", {}))
            done.update(d.get("skip", {}))
        except (OSError, ValueError):
            pass
    todo = [k for k, (orig, _) in want.items() if done.get(k) != orig]
    if SHARD:
        i_sh, n_sh = (int(x) for x in SHARD.split("/"))
        todo = [k for k in todo if zlib.crc32(k.encode()) % n_sh == i_sh]
    todo.sort(key=lambda k: -(cap.get(k) or 0))
    only = [a for a in sys.argv[1:] if ":" in a]
    if only:
        todo = [k for k in only if k in want]
    print(f"회사 이름: 남은 {len(todo):,}곳")
    t0, calls, got = time.time(), 0, 0
    for i in range(0, len(todo), BATCH):
        if calls >= MAX_CALLS or time.time() - t0 > SECS:
            break
        part = todo[i:i + BATCH]
        items = "\n".join(f"{k}: {want[k][0]} | {want[k][1]}" for k in part)
        try:
            calls += 1
            ans = ai_seg.claude(PROMPT.format(items=items))
        except ai_seg.Limited as e:
            print(f"  ! 한도 — 멈춘다: {e}"[:200])
            break
        except Exception as e:                              # noqa: BLE001
            print(f"  ! {e!r}"[:200])
            continue
        m = re.search(r"\{.*\}", ans, re.S)
        try:
            res = json.loads(m.group(0)) if m else {}
        except ValueError:
            res = {}
        for k in part:
            v = (res.get(k) or "").strip()
            if ok(v, want[k][0]):
                db["ko"][k] = v
                db["src"][k] = want[k][0]
                got += 1
        OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"회사 이름: 물어본 {calls}번 · 새로 {got}곳 · 이 파일 {len(db['ko']):,}곳")


if __name__ == "__main__":
    main()
