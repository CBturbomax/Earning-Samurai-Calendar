# -*- coding: utf-8 -*-
"""
사업 설명 수집 — "이 회사가 뭘 파는가"를 한 줄로

업종·테마만으로는 안 보인다. 'ONEOK, Inc. 에너지' 라고 적어봐야 무엇을 파는
회사인지 모른다. 눌렀을 때 한 줄이라도 나와야 한다.

소스는 시장마다 다르고, 떠보고 골랐다.

| 시장 | 소스 | 결과 |
|---|---|---|
| 미국 | 나스닥 company-profile | 200 — 회사가 쓴 소개문. 쓸 만한 것도 있고 광고문구도 있다 |
| 일본 | 닛케이 결산 페이지의 `【…】` | 200 — **가장 좋다.** 「【世界的電機メーカー】音楽や半導体にも強み」 |
| 홍콩 | stockanalysis 회사 페이지 | 메타태그는 껍데기라 본문에서 뽑는다 |

**한국어는 사람이 쓴다.** 러너에는 번역기가 없다. 회사 한글명을 `companies*.py`
에 손으로 넣었듯, 사업 설명도 `DESC_KO` 에 손으로 넣는다. 아직 안 쓴 종목은
여기서 받은 **원문을 그대로** 보여준다 — 빈칸으로 두는 것보다 낫다.

결과: data/desc.json   {시장:코드: {"t": 원문, "src": 출처, "ts": 받은 날}}
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from descriptions import DESC_KO      # 한국어를 이미 써 둔 종목은 안 받는다

HERE = Path(__file__).parent
OUT = HERE / "data" / "desc.json"

NAS_PROFILE = "https://api.nasdaq.com/api/company/{sym}/company-profile"
NIKKEI = "https://www.nikkei.com/nkd/company/kessan/?scode={code}"
SA_PROFILE = "https://stockanalysis.com/quote/{ex}/{code}/company/"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

PER_RUN = int(os.environ.get("DESC_PER_RUN", "250"))
TOP_N = int(os.environ.get("DESC_TOP_N", "3000"))
PAUSE = float(os.environ.get("DESC_PAUSE", "0.4"))
STALE_DAYS = int(os.environ.get("DESC_STALE_DAYS", "120"))   # 사업 설명은 잘 안 바뀐다
GIVE_UP_AFTER = 8
# v2: 홍콩에서 SEO 껍데기("Company profile for …")를 본문인 줄 알고 담았다.
# 일본은 닛케이가 데이터센터 IP 를 막아 CI 에서 늘 빈손이었다 — stockanalysis 로
# 대신 받는다. 헌 기록을 다시 받도록 판을 올린다.
DESC_VER = 2

TAGS = re.compile(r"<[^>]+>")
# 닛케이 요약. 【…】 뒤에 한 문장이 더 붙는다. 그 뒤의 "この企業の最新ニュース…"는
# 사업 설명이 아니라 페이지 안내문이라 잘라낸다.
NK_RE = re.compile(r"【([^】]{2,40})】([^\"<]{0,120})")
NK_TAIL = re.compile(r"この企業の.*$")


class Throttled(Exception):
    """막혔다. '설명이 없다'와 다른 일이다."""


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Language": "ja,ko,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                      # 그 종목 페이지가 없다
        raise Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise Throttled(str(e))


def junk(t):
    """받아 놓고 보니 회사 설명이 아닌 것. 저장돼 있어도 다시 받는다.

    실제로 담겼던 세 갈래다 —
    - stockanalysis 의 SEO 껍데기: "Company profile for CK Hutchison … with a
      description, list of executives…" (홍콩 199종목이 전부 이 문장이었다)
    - 닛케이의 페이지 소개문: "【日本経済新聞】 …の決算情報を収録。…" (417A).
      회사 요약과 같은 【…】 꼴이라 NK_RE 가 그대로 물었다.
    - HTML 속성 부스러기: '<p class="[&>p]:mb-5">' 의 [^>]* 가 클래스값 속
      > 에서 끊겨 'p]:mb-5">' 가 본문에 샜다 (02513).
    """
    if not t:
        return False
    return (t.startswith("Company profile for")
            or "日本経済新聞" in t
            or '">' in t[:120])


def tidy(s, limit=220):
    """공백을 고르고 너무 길면 문장 끝에서 자른다."""
    s = re.sub(r"\s+", " ", TAGS.sub(" ", s or "")).strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    for end in ("。", ". ", "! ", "? "):
        i = cut.rfind(end)
        if i > limit * 0.5:
            return cut[:i + 1].strip()
    return cut.rstrip() + "…"


def us_desc(sym):
    """나스닥이 싣는 회사 소개. 회사가 직접 쓴 글이라 광고문구도 섞인다."""
    txt = get(NAS_PROFILE.format(sym=sym.upper()))
    if txt is None:
        return ""
    try:
        d = (json.loads(txt).get("data") or {})
    except ValueError:
        raise Throttled("JSON 이 아니다")
    got = ((d.get("CompanyDescription") or {}).get("value") or "")
    return tidy(got)


def jp_desc(code):
    """일본. 닛케이의 【…】 요약이 가장 알짜지만, **닛케이는 데이터센터 IP 를
    막는다** — CI 에서는 늘 껍데기가 와서 이 경로가 한 번도 채워지지 않았다
    (일본 설명이 164건뿐이던 이유). 그래서 닛케이를 먼저 두드리되 빈손이면
    stockanalysis 회사 페이지로 받는다. 영어지만 빈칸보다 낫고, 한국어는
    descriptions.py 가 덮는다.
    """
    try:
        txt = get(NIKKEI.format(code=code))
    except Throttled:
        txt = None
    if txt:
        m = NK_RE.search(txt)
        # 페이지 자기소개(【日本経済新聞】 …の決算情報を収録)도 같은 꼴이라
        # 걸린다. 그건 회사 설명이 아니다 — 버리고 대체 소스로 간다.
        if m and "日本経済新聞" not in m.group(1):
            head, rest = m.group(1).strip(), NK_TAIL.sub("", m.group(2)).strip()
            return tidy(("【" + head + "】 " + rest).strip())
    return sa_desc("tyo", code)


def hk_desc(code):
    return sa_desc("hkg", code.lstrip("0").zfill(4) if code.strip("0") else code)


def sa_desc(ex, code):
    """stockanalysis 회사 페이지에서 본문 설명을 뽑는다.

    페이지에 "description" 이 여러 벌 실려 있는데 그중 하나는 **SEO 껍데기**다 —
    "Company profile for CK Hutchison Holdings Limited (HKG:0001) with a
    description, list of executives…". 이걸 본문인 줄 알고 담아서 홍콩 199종목이
    전부 그 문장이었다. 껍데기 문구는 거르고 가장 긴 진짜 문단을 고른다.
    """
    txt = get(SA_PROFILE.format(ex=ex, code=code))
    if txt is None:
        return ""
    best = ""
    for m in re.finditer(r'"description\\?":\\?"([^"\\]{60,900})', txt):
        t = m.group(1)
        if t.startswith("Company profile for"):
            continue                     # SEO 껍데기. 본문이 아니다.
        if len(t) > len(best):
            best = t
    if not best:
        for m in re.finditer(r"<p[^>]*>(.{80,900}?)</p>", txt, re.S):
            t = tidy(m.group(1), 900)
            # <p class="[&>p]:mb-5"> 처럼 클래스값에 > 가 든 태그는 [^>]* 가
            # 중간에서 끊겨 속성 부스러기('p]:mb-5">')가 본문에 샌다. 잘라낸다.
            while '">' in t[:120]:
                t = t.split('">', 1)[1].strip()
            if t.startswith("Company profile for") or len(t) < 60:
                continue
            if " engages in " in t or " is a " in t or " provides " in t:
                best = t
                break
    return tidy(best)


def targets():
    """시총 큰 순으로. 캘린더에 실린 종목만 본다."""
    caps = {}
    p = HERE / "data" / "caps.json"
    if p.exists():
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, OSError):
            pass
    out = {}
    # earnings_jp_past.json — 발표를 마쳐 닛케이에서 사라진 일본 종목(TDnet).
    # 캘린더에는 뜨므로 설명도 있어야 한다.
    for market, fn in (("us", "earnings_us.json"), ("jp", "earnings.json"),
                       ("jp", "earnings_jp_past.json"), ("hk", "earnings_hk.json")):
        f = HERE / "data" / fn
        if not f.exists():
            continue
        try:
            rows = json.loads(f.read_text(encoding="utf-8")).get("rows", [])
        except (ValueError, OSError):
            continue
        for r in rows:
            c = r.get("code")
            if not c:
                continue
            k = market + ":" + c
            out[k] = max(out.get(k, 0), r.get("cap") or caps.get(k) or 0)
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:TOP_N])


def queue(old, cand):
    """못 받은 것 -> 오래된 것. 같은 순위면 시총 큰 쪽.

    **한국어를 이미 써 둔 종목은 건너뛴다.** 화면은 한국어가 있으면 그걸 쓰고
    원문은 안 쓴다(build.py 의 `desc` 는 `DESC_KO` 에 없는 것만 싣는다). 그런데
    받는 순서가 시총 큰 순이라, 처음 쉰 종목을 받았더니 **쉰 개가 전부 이미
    한국어가 있는 종목**이었다 — 화면에 하나도 안 늘었다. 지금은 빈칸부터 채운다.
    """
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 3)).isoformat()
    picks = []
    for k, cap in cand.items():
        if k in DESC_KO:
            continue
        rec = old.get(k)
        # 껍데기·부스러기가 담긴 기록은 버전이 같아도 못 받은 것으로 친다 —
        # 판별을 새로 배울 때마다 버전을 올려 멀쩡한 것까지 다시 받을 이유가 없다.
        if not rec or rec.get("v") != DESC_VER or junk(rec.get("t") or ""):
            pri = 0
        elif not rec.get("t"):
            if (rec.get("ts") or "") >= cold:
                continue                     # 설명이 없는 종목. 자주 안 본다.
            pri = 2
        elif (rec.get("ts") or "") < stale:
            pri = 1
        else:
            continue
        picks.append((pri, -cap, k))
    picks.sort()
    return [k for _, _, k in picks]


def one(key):
    market, code = key.split(":", 1)
    if market == "us":
        return us_desc(code), "nasdaq"
    if market == "jp":
        return jp_desc(code), "nikkei"
    return hk_desc(code), "stockanalysis"


def main():
    if "--probe" in sys.argv:
        for k in ("us:AAPL", "us:RKLB", "jp:7203", "jp:6758", "jp:1379", "hk:00700"):
            try:
                t, src = one(k)
            except Throttled as e:
                print(f"  {k}: 막힘 {e}")
                continue
            print(f"  {k} [{src}] {len(t)}자\n      {t[:180]}")
            time.sleep(PAUSE)
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    cand = targets()
    pending = queue(old, cand)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개 "
          f"(가진 것 {len(old):,}개)")

    today = date.today().isoformat()
    stocks = dict(old)
    got = streak = 0
    for i, k in enumerate(todo):
        try:
            t, src = one(k)
            streak = 0
        except Throttled as e:
            streak += 1
            print(f"    {k} 막힘: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("  연속으로 막혔다. 여기서 접는다.", file=sys.stderr, flush=True)
                break
            continue                          # 막힌 종목은 기록을 건드리지 않는다
        stocks[k] = {"v": DESC_VER, "ts": today, "t": t, "src": src}
        if t:
            got += 1
        if i % 25 == 24:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(PAUSE)

    have = {k: v for k, v in stocks.items() if v.get("t")}
    payload = {
        "source": "나스닥 회사소개 · 닛케이 결산페이지 · stockanalysis",
        "note": ("원문 그대로다. 한국어 설명은 companies*.py 의 DESC_KO 에 "
                 "사람이 써 넣고, 없으면 이 원문을 보여준다."),
        "count": len(have),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    per = {}
    for k, v in have.items():
        per[k.split(":")[0]] = per.get(k.split(":")[0], 0) + 1
    print(f"\n{len(have):,}종목 -> {OUT}  {per}")


if __name__ == "__main__":
    main()
