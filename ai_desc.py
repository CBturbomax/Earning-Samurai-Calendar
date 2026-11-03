# -*- coding: utf-8 -*-
"""사업 설명 한국어 — AI(Codex 구독)로 원문을 쉬운 한국어 한 줄로 옮긴다(회원님 11/03: "번역 안 된 회사가 너무 많다, 쉬운 말로,
이상한 업종 설명 가져오지 말고").

- 원문(data/desc.json — 회사가 스스로 쓴 소개)만 근거로 쓴다. 업종 분류명·광고 문구로 지어내지 않는다.
- 사람이 쓴 `descriptions.py` 의 DESC_KO 가 있는 회사는 건너뛴다(그쪽이 이긴다).
- 한 번에 25곳씩 묻는다. 답은 기계로 검사한다: 한글이어야 하고, 가나·한자가 남으면 안 되고, 90자 안, 따옴표 없이.
  원문이 회사 설명이 아니면 AI 가 빈칸으로 돌려준다 — 그 회사는 `skip` 에 적고 원문이 바뀔 때까지 다시 안 묻는다.
- 시총 큰 순. 한도에 걸리면 그 자리에서 멈춘다(추가 결제 금지 — ai_seg.claude 와 같은 규칙).

data/desc_ko_ai.json  {"ko": {열쇠: 한국어}, "src": {열쇠: 원문 해시}, "skip": {열쇠: 원문 해시}}
build.py 가 DESC_KO 다음으로 쓴다.
"""
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import ai_seg

HERE = Path(__file__).parent
SHARD = os.environ.get("AIDESC_SHARD", "")          # "0/3" — 갈래마다 제 파일(커밋이 안 부딪힌다)
OUT = HERE / "data" / (f"desc_ko_ai_s{SHARD.split('/')[0]}.json" if SHARD else "desc_ko_ai.json")
BATCH = int(os.environ.get("AIDESC_BATCH", "25"))
MAX_CALLS = int(os.environ.get("AIDESC_MAX", "40"))
SECS = int(os.environ.get("AIDESC_SECS", "1500"))
KANA = re.compile(r"[぀-ヿ㐀-鿿]")
HANGUL = re.compile(r"[가-힣]")

PROMPT = """아래는 상장회사들이 스스로 쓴 회사 소개 원문이다(영어·일본어·중국어). 회사마다 **쉬운 한국어 한 줄**(40~80자)로,
"무엇을 팔아서 돈을 버는 회사인지"를 적어라. 예시 말투:
  us:AAPL → 아이폰·맥·에어팟을 만들고, 앱스토어·아이클라우드 구독료도 받는다.
  us:VRTX → 낭포성 섬유증 치료제가 주력인 제약사.
규칙: 원문에 있는 내용만 쓴다(추측·업종 분류명·광고 문구·'선도적인' 같은 수식어 금지). 회사 이름은 빼라. 제품·서비스는 한국에서
흔히 쓰는 말로(고유 브랜드는 한글 음차 또는 영문 그대로). 일본어·한자를 남기지 마라. 원문이 회사 설명이 아니면(웹페이지 소개문,
주가 정보 등) 빈 문자열을 적어라. 따옴표를 쓰지 마라.

JSON 한 덩이로만 답하라: {{"열쇠": "한국어", ...}}

원문:
{items}
"""


def h(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()[:10]


def ok(t):
    t = (t or "").strip()
    return bool(t) and HANGUL.search(t) and not KANA.search(t) and len(t) <= 90 and '"' not in t and "“" not in t


def order(keys):
    cap = {}
    try:
        cap.update(json.loads((HERE / "data" / "caps.json").read_text(encoding="utf-8")).get("caps", {}))
    except (OSError, ValueError):
        pass
    try:
        for r in json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8")).get("rows", []):
            k = "us:" + r["code"]
            cap[k] = max(cap.get(k, 0) or 0, (r.get("cap") or 0) * 1.4)      # 달러 시총(십억) ≈ 조원 어림
    except (OSError, ValueError):
        pass
    return sorted(keys, key=lambda k: -(cap.get(k) or 0))


def main():
    import descriptions
    src = json.loads((HERE / "data" / "desc.json").read_text(encoding="utf-8")).get("stocks", {})
    try:
        db = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    for k in ("ko", "src", "skip"):
        db.setdefault(k, {})
    todo = []
    for k, v in src.items():
        t = (v.get("t") or "").strip()
        if len(t) < 25 or k in descriptions.DESC_KO or t.startswith("Company profile for") or "日本経済新聞" in t:
            continue
        hh = h(t)
        if db["src"].get(k) == hh or db["skip"].get(k) == hh:
            continue
        todo.append(k)
    if SHARD:
        import zlib
        i_sh, n_sh = (int(x) for x in SHARD.split("/"))
        todo = [k for k in todo if zlib.crc32(k.encode()) % n_sh == i_sh]
        for f in (HERE / "data").glob("desc_ko_ai*.json"):          # 다른 갈래·옛 파일이 이미 옮긴 것은 건너뛴다
            if f != OUT:
                try:
                    done = json.loads(f.read_text(encoding="utf-8")).get("src", {})
                    todo = [k for k in todo if done.get(k) != h((src[k].get("t") or "").strip())]
                except (OSError, ValueError):
                    pass
    todo = order(todo)
    only = [a for a in sys.argv[1:] if ":" in a]
    if only:
        todo = [k for k in todo if k in only] or only
    print(f"사업 설명 번역: 남은 {len(todo):,}곳")
    t0, calls, got = time.time(), 0, 0
    for i in range(0, len(todo), BATCH):
        if calls >= MAX_CALLS or time.time() - t0 > SECS:
            break
        part = todo[i:i + BATCH]
        items = "\n".join(f"{k}: {(src[k].get('t') or '')[:700]}" for k in part)
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
            t = (src[k].get("t") or "").strip()
            v = (res.get(k) or "").strip()
            if ok(v):
                db["ko"][k] = v
                db["src"][k] = h(t)
                got += 1
            elif k in res and not v:
                db["skip"][k] = h(t)                         # 원문이 회사 설명이 아니다
        OUT.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"사업 설명 번역: 물어본 {calls}번 · 새로 옮긴 {got}곳 · 전체 {len(db['ko']):,}곳")


if __name__ == "__main__":
    main()
