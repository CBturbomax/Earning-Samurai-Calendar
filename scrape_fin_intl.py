# -*- coding: utf-8 -*-
"""
분기 실적 수집 — 출처: stockanalysis.com

**분기만 모은다.** 연간 막대는 추세를 못 보여준다. 회원님이 보고 싶은 건
"이번 분기가 작년 같은 분기보다 나아졌나"이지 몇 년치 총합이 아니다.

왜 여기인가. 소스 여덟 곳을 같은 잣대로 떠봤다.

  stockanalysis   200  일본·홍콩·미국 모두   <- 이것만 세 시장을 다 준다
  WSJ / 마켓워치   401  막힘
  인베스팅 / IR뱅크 403  막힘
  가부탄           405  막힘
  트레이딩뷰       200  값이 비어서 옴(POST 본문이 따로 필요)
  AA스탁스         200  HTML 표라 부서지기 쉽다

깊이도 확인했다. 야후는 최근 4~5개가 한계였는데 여기는 20개 안팎을 준다.

  도요타   야후 5개  ->  20개 (2021-09 ~ 2026-06)
  텐센트   야후 1개  ->  20개 (2021-06 ~ 2026-03)
  SEA     SEC 연간만 ->  30개 (2018-12 ~ 2026-06)

SEA·알리바바처럼 SEC 에 20-F(연 1회)만 내는 외국 기업도 여기서는 분기가 나온다.
미국 종목은 SEC 가 공식이라 그쪽을 먼저 쓰고(scrape_fin.py), **분기를 못 구한
종목만** 여기서 메운다.

응답은 SvelteKit 의 `__data.json` 이다. 값 대신 배열 색인이 들어 있어 되살려야 한다.
표는 sections[].data 에 세로 배열로 들어 있다:
  datekey / fiscalYear / fiscalQuarter / revenue / gp / opinc / netinccmn / epsdil

**남의 서버다.** 종목이 4천 개라 매시간 전부 두드리면 민폐다. 시총 큰 순으로
조금씩, 한 종목당 요청 한 번, 받아둔 것은 오래 쓴다.

결과: data/financials_intl.json
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

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials_intl.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SA_INTL = "https://stockanalysis.com/quote/{ex}/{code}/financials/__data.json?p=quarterly"
SA_US = "https://stockanalysis.com/stocks/{sym}/financials/__data.json?p=quarterly"
EXCH = {"jp": "tyo", "hk": "hkg"}

PER_RUN = int(os.environ.get("INTL_PER_RUN", "400"))     # 한 실행에 받을 종목 수
STALE_DAYS = int(os.environ.get("INTL_STALE_DAYS", "10"))
NEAR_DAYS = int(os.environ.get("INTL_NEAR_DAYS", "4"))   # 발표일 언저리는 매일
PAUSE = float(os.environ.get("INTL_PAUSE", "0.6"))       # 요청 사이 쉬는 시간
# 400개 × 0.6초 = 4분 남짓. 시간당 400건이면 초당 0.11건이라 남의 서버에 무리는
# 아니다. 옛 자료(야후 4~5분기)를 새 자료(20분기)로 갈아 끼우는 동안만 이 속도다.

# 저장 형식 번호. 받는 방식을 고치면 올린다 — 이미 받아둔 기록도 다시 받는다.
#   1 -> 2  야후에서 stockanalysis 로. 분기 4~5개가 20개로 늘고, 연간은 안 담는다.
INTL_VER = 2

BACKOFF = (0, 5, 20, 60)
GIVE_UP_AFTER = 6      # 연속 이만큼 막히면 이번 실행은 접는다


class Throttled(Exception):
    """막혔다. '그 회사에 자료가 없다'와 전혀 다른 일이다."""


def get(url, timeout=30):
    """404 는 None(그 종목이 없다), 그 밖의 실패는 Throttled."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            return None
        raise Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise Throttled(str(e))


def unflatten(arr):
    """SvelteKit `__data.json` 은 값 자리에 배열 색인을 담는다. 그걸 되살린다.

    같은 값을 여러 군데서 가리키므로 한 번 푼 것은 기억해 두고, 자기 자신을
    가리키는 고리에 빠지지 않게 깊이를 막는다.
    """
    memo = {}

    def walk(i, depth=0):
        if not isinstance(i, int) or i < 0 or i >= len(arr) or depth > 40:
            return i
        if i in memo:
            return memo[i]
        memo[i] = None
        v = arr[i]
        if isinstance(v, dict):
            out = {k: walk(x, depth + 1) for k, x in v.items()}
        elif isinstance(v, list):
            out = [walk(x, depth + 1) for x in v]
        else:
            out = v
        memo[i] = out
        return out

    return walk(0)


def find_tables(o, out=None, depth=0):
    """트리 속에서 재무 표를 찾는다 — datekey 와 revenue 를 함께 가진 dict."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(o, dict):
        if isinstance(o.get("datekey"), list) and isinstance(o.get("revenue"), list):
            out.append(o)
        for v in o.values():
            find_tables(v, out, depth + 1)
    elif isinstance(o, list):
        for v in o[:60]:
            find_tables(v, out, depth + 1)
    return out


def find_currency(o, depth=0):
    """통화 표기를 찾는다. 못 찾으면 빈 문자열 — 지어내지 않는다."""
    if depth > 6:
        return ""
    if isinstance(o, dict):
        for k, v in o.items():
            if k.lower() in ("currency", "reportedcurrency", "curr") and isinstance(v, str):
                s = v.strip().upper()
                if re.fullmatch(r"[A-Z]{3}", s):
                    return s
        for v in o.values():
            got = find_currency(v, depth + 1)
            if got:
                return got
    elif isinstance(o, list):
        for v in o[:40]:
            got = find_currency(v, depth + 1)
            if got:
                return got
    return ""


def num(v):
    """'1,234' 이나 '-' 같은 것들이 섞여 온다. 숫자만 받는다."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s):
            return float(s)
    return None


def label_of(end):
    """분기말 날짜 -> '2Q26'. 회원님이 보는 표기법이다(역년 기준)."""
    d = date.fromisoformat(end)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


DATE_RE = re.compile(r"^20\d\d-\d\d-\d\d$")


def parse(txt):
    """응답 -> {종료일: (매출, 영업이익, 순이익)} 과 통화."""
    body = json.loads(txt)
    best, cur = {}, ""
    for node in body.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "data":
            continue
        arr = node.get("data")
        if not isinstance(arr, list):
            continue
        tree = unflatten(arr)
        cur = cur or find_currency(tree)
        for t in find_tables(tree):
            dates = t.get("datekey") or []
            rev = t.get("revenue") or []
            opi = t.get("opinc") or []
            ni = t.get("netinccmn") or []
            got = {}
            for i, dk in enumerate(dates):
                if not isinstance(dk, str) or not DATE_RE.match(dk):
                    continue
                r = num(rev[i]) if i < len(rev) else None
                if r is None:
                    continue
                got[dk] = (r,
                           num(opi[i]) if i < len(opi) else None,
                           num(ni[i]) if i < len(ni) else None)
            if len(got) > len(best):
                best = got
    return best, cur


def series(url):
    """한 종목의 분기 시계열. 없으면 None."""
    for wait in BACKOFF:
        if wait:
            print(f"      막혔다. {wait}초 쉬고 다시", file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            txt = get(url)
        except Throttled:
            continue
        if txt is None:
            return None                       # 그 종목이 없다. 재시도해도 없다.
        try:
            rows, cur = parse(txt)
        except (ValueError, TypeError) as e:
            raise Throttled(f"응답을 못 읽었다: {e}")
        if not rows:
            return None
        ends = sorted(rows)
        return {
            "v": INTL_VER,
            "freq": "Q",
            "cur": cur,
            "src": "sa",
            "points": [{
                "label": label_of(e),
                "end": e,
                "rev": rows[e][0],
                "opi": rows[e][1],
                "ni": rows[e][2],
            } for e in ends],
        }
    raise Throttled("네 번 다 실패")


def url_for(market, code):
    """stockanalysis 주소. 일본은 tyo/7203, 홍콩은 hkg/0700, 미국은 stocks/SE."""
    if market == "us":
        return SA_US.format(sym=code.lower().replace(".", "-"))
    if market == "hk":
        digits = re.sub(r"\D", "", code)
        if not digits:
            return ""
        return SA_INTL.format(ex="hkg", code=f"{int(digits):04d}")
    return SA_INTL.format(ex=EXCH.get(market, ""), code=code)


def us_needs_quarters():
    """미국 중 SEC 로 분기를 못 구한 종목. SEA·알리바바 같은 외국 기업들이다.

    미국은 SEC 가 공식이라 그쪽이 먼저다. 다만 20-F(연 1회)만 내는 회사는 SEC 에
    분기가 아예 없어서 연간 막대밖에 안 나온다 — 그건 회원님이 원하는 그림이 아니다.
    """
    p = HERE / "data" / "financials.json"
    if not p.exists():
        return set()
    try:
        got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError):
        return set()
    out = set()
    for sym, rec in got.items():
        pts = rec.get("points") or []
        if rec.get("freq") != "Q" or len(pts) < 8:
            out.add(sym.split(":")[-1])
    return out


def targets():
    """{시장:코드: 시총} — 시총 큰 순으로 채우려고 같이 본다."""
    caps = {}
    p = HERE / "data" / "caps.json"
    if p.exists():
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, OSError):
            pass
    out = {}
    for market, fn in (("jp", "earnings.json"), ("hk", "earnings_hk.json")):
        f = HERE / "data" / fn
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for r in d.get("rows", []):
            c = r.get("code")
            if c:
                k = f"{market}:{c}"
                out[k] = caps.get(k) or 0

    # 미국은 SEC 로 분기를 못 구한 종목만.
    need = us_needs_quarters()
    f = HERE / "data" / "earnings_us.json"
    if need and f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            d = {}
        for r in d.get("rows", []):
            c = r.get("code")
            if c and c in need:
                k = "us:" + c
                out[k] = max(out.get(k, 0), r.get("cap") or 0)
    return out


def near_days():
    """{시장:코드: 발표일까지 며칠}. 발표 언저리 종목은 값이 지금 바뀐다."""
    today = date.today()
    out = {}
    for market, fn in (("jp", "earnings.json"), ("hk", "earnings_hk.json"),
                       ("us", "earnings_us.json")):
        f = HERE / "data" / fn
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for r in d.get("rows", []):
            c = r.get("code")
            if not c:
                continue
            try:
                gap = abs((date.fromisoformat(r.get("date") or "") - today).days)
            except ValueError:
                continue
            k = f"{market}:{c}"
            out[k] = min(out.get(k, 9999), gap)
    return out


def queue(old, cand, gaps):
    """받을 순서. 못 받은 것 -> 발표 언저리 -> 오래된 것. 모두 시총 큰 순."""
    today = date.today().isoformat()
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 6)).isoformat()
    picks = []
    for k, cap in cand.items():
        rec = old.get(k)
        if not rec or rec.get("v") != INTL_VER:
            pri = 0
        else:
            ts = rec.get("ts") or ""
            if not rec.get("points"):
                if ts >= cold:
                    continue          # 여기에도 없는 종목. 자주 두드리지 않는다.
                pri = 3
            elif gaps.get(k, 9999) <= NEAR_DAYS and ts < today:
                pri = 1
            elif ts < stale:
                pri = 2
            else:
                continue              # 아직 싱싱하다.
        picks.append((pri, -cap, k))
    picks.sort()
    return [k for _, _, k in picks]


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if probe:
        for label, market, code in (("도요타", "jp", "7203"), ("소니", "jp", "6758"),
                                    ("텐센트", "hk", "00700"), ("알리바바", "hk", "09988"),
                                    ("SEA", "us", "SE"), ("알리바바 ADR", "us", "BABA")):
            u = url_for(market, code)
            print(f"\n===== {label} {market}:{code}\n  {u}")
            try:
                s = series(u)
            except Throttled as e:
                print("  실패:", e)
                continue
            if not s:
                print("  자료 없음")
                continue
            p = s["points"]
            print(f"  분기 {len(p)}개 · 통화 {s['cur'] or '(모름)'} · "
                  f"{p[0]['label']} ~ {p[-1]['label']}")
            for x in p[-3:]:
                print(f"    {x['label']} 매출 {x['rev']:>16,.0f} "
                      f"영업이익 {x['opi'] if x['opi'] is None else format(x['opi'], ',.0f'):>14}")
        return

    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    cand = targets()
    gaps = near_days()
    pending = queue(old, cand, gaps)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개 "
          f"(가진 것 {len(old):,}개 / 후보 {len(cand):,}개)")

    today = date.today().isoformat()
    stocks = dict(old)
    got, streak = 0, 0
    for i, k in enumerate(todo):
        market, code = k.split(":", 1)
        u = url_for(market, code)
        rec = None
        if u:
            try:
                rec = series(u)
                streak = 0
            except Throttled as e:
                streak += 1
                print(f"    {k} 막힘: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print(f"  연속 {streak}종목이 막혔다. 이번 실행은 여기서 접는다.",
                          file=sys.stderr, flush=True)
                    break
                continue              # 막힌 종목은 기록을 건드리지 않는다
        if rec:
            got += 1
        else:
            rec = dict(stocks.get(k) or {})
            rec["none"] = 1           # 두드려 봤지만 없더라는 표시
        rec["v"] = INTL_VER
        rec["ts"] = today
        stocks[k] = rec
        if i % 25 == 24:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(PAUSE)

    have = {k: v for k, v in stocks.items() if v.get("points")}
    payload = {
        "source": "stockanalysis.com (분기 실적)",
        "note": ("분기만 담는다. 연간 막대는 추세를 못 보여준다. "
                 "미국은 SEC 가 먼저이고, 분기를 못 구한 종목만 여기서 메운다."),
        "count": len(have),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    def cnt(m):
        return sum(1 for k in have if k.startswith(m))
    deep = [len(v["points"]) for v in have.values()]
    print(f"\n{len(have):,}종목 -> {OUT} (자료 없는 종목 {len(stocks)-len(have):,} 표시만)")
    print(f"  일본 {cnt('jp:'):,} · 홍콩 {cnt('hk:'):,} · 미국 보충 {cnt('us:'):,}")
    if deep:
        deep.sort()
        print(f"  분기 개수 중앙값 {deep[len(deep)//2]}개 "
              f"(가장 짧은 것 {deep[0]}개 · 가장 긴 것 {deep[-1]}개)")


if __name__ == "__main__":
    main()
