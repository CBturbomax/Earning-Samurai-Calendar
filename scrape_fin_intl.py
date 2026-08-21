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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials_intl.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SA_INTL = "https://stockanalysis.com/quote/{ex}/{code}/financials/__data.json?p=quarterly"
SA_US = "https://stockanalysis.com/stocks/{sym}/financials/__data.json?p=quarterly"
EXCH = {"jp": "tyo", "hk": "hkg"}

PER_RUN = int(os.environ.get("INTL_PER_RUN", "600"))     # 한 실행에 받을 종목 수
STALE_DAYS = int(os.environ.get("INTL_STALE_DAYS", "10"))
NEAR_DAYS = int(os.environ.get("INTL_NEAR_DAYS", "4"))   # 발표일 언저리는 매일
# 미국 종목 중 SEC 자료의 마지막 분기가 이보다 오래됐으면 여기서 메운다.
# 한 분기(92일)에 발표까지 걸리는 시간을 더해 넉넉히 잡는다.
STALE_QUARTER = int(os.environ.get("INTL_STALE_QUARTER", "150"))
PAUSE = float(os.environ.get("INTL_PAUSE", "0.6"))       # 요청 사이 쉬는 시간
# 600개 × 0.6초 = 6분 남짓. 시간당 600건이면 초당 0.17건이라 남의 서버에 무리는
# 아니다. 옛 자료를 갈아 끼우고 방금 발표한 종목을 따라잡는 동안만 이 속도다.

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
    """분기말 날짜 -> '2Q26'. **끝난 날이 아니라 기간의 한가운데**로 가른다.

    회계 분기는 달력에 딱 맞지 않는다. 끝난 날로 가르면 이렇게 어긋난다.

      코카콜라  1~3월 분기가 4월 3일에 끝난다  -> 2Q26 (틀림, 1Q26 이 맞다)
      모토로라  4~6월 분기가 7월 4일에 끝난다  -> 3Q26 (틀림, 2Q26 이 맞다)

    끝나기 45일 전, 즉 기간 한가운데를 보면 제대로 갈린다.
    """
    d = date.fromisoformat(end) - timedelta(days=45)
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
    stale = (date.today() - timedelta(days=STALE_QUARTER)).isoformat()
    reported = reported_quarters()
    out = set()
    for sym, rec in got.items():
        code = sym.split(":")[-1]
        pts = rec.get("points") or []
        last = pts[-1].get("end", "") if pts else ""
        # 분기가 아니거나, 너무 짧거나, 최근 분기가 오래됐으면 메운다.
        if rec.get("freq") != "Q" or len(pts) < 8 or last < stale:
            out.add(code)
            continue
        # **이미 발표한 분기가 차트에 없으면** 메운다. 이게 없으면 방금 발표한
        # 회사가 며칠씩 옛 분기에 머문다 — SEC 는 실적 발표가 아니라 10-Q 가
        # 올라와야 값이 생기는데 그 사이가 며칠에서 몇 주다. 루멘텀이 그랬다:
        # 8/11 에 6월 분기를 발표했는데 차트는 3월 분기에서 멈춰 있었다.
        done = reported.get(code)
        if done and done > last:
            out.add(code)
    return out


FY_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월")


def reported_quarters():
    """{미국 코드: 이미 발표한 가장 최근 분기의 종료일}.

    캘린더에 '지난 날짜 + 결산기'가 들어 있으므로, 회사가 무슨 분기를 발표했는지
    알 수 있다. 발표일이 지났으면 그 분기는 세상에 나온 것이다.
    """
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    today = date.today().isoformat()
    out = {}
    for r in d.get("rows", []):
        code, day = r.get("code"), r.get("date") or ""
        if not code or not day or day > today:
            continue
        m = FY_RE.search(r.get("fy") or "")
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        # 그 달의 끝 언저리. 회사마다 며칠씩 다르니 넉넉히 앞으로 잡는다.
        end = date(y, mo, 20).isoformat()
        if end > out.get(code, ""):
            out[code] = end
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
    # 일본은 소스가 둘이다. 닛케이(earnings.json)는 앞으로의 예정만 주고, 발표를
    # 마친 회사는 TDnet 쪽(earnings_jp_past.json)에만 있다 — 3093 트레저팩토리가
    # 여기 빠져서 캘린더에는 뜨는데 실적 차트가 없었다. **캘린더에 뜨는 종목은
    # 전부 우주에 넣는다.**
    for market, fn in (("jp", "earnings.json"), ("jp", "earnings_jp_past.json"),
                       ("jp", "earnings_jp_sched.json"), ("hk", "earnings_hk.json")):
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


def announcements():
    """{시장:코드: (가장 가까운 발표일까지 며칠, 이미 지난 마지막 발표일)}.

    뒤엣값이 중요하다. **우리가 받아둔 시점이 그 발표보다 앞서면** 우리 기록은
    그 발표를 못 담은 것이다 — 지금 당장 다시 받아야 한다.
    """
    today = date.today()
    tstr = today.isoformat()
    out = {}
    for market, fn in (("jp", "earnings.json"), ("jp", "earnings_jp_past.json"),
                       ("jp", "earnings_jp_sched.json"),
                       ("hk", "earnings_hk.json"), ("us", "earnings_us.json")):
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
            cur = out.get(k, (9999, ""))
            day = r.get("date") or ""
            out[k] = (min(cur[0], gap),
                      max(cur[1], day) if day <= tstr else cur[1])
    return out


def now_stamp():
    """UTC 로 분까지. 날짜 문자열과 견줘도 앞뒤가 맞다(ISO 라 사전순 = 시간순)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def missing_announced(rec, last_ann):
    """발표는 났는데 그 분기가 우리 기록에 없나.

    **'언제 받았나'로 따지면 안 된다.** 예전에는 받아둔 날짜가 발표일보다
    앞서는지만 봤는데, 날짜끼리 견주니 **같은 날이면 앞뒤를 가릴 수가 없다.**
    일본 회사는 오후 3시(한국 시각)에 발표하는데 우리는 그날 아침에 이미 받아
    뒀으므로 '오늘 받았다'가 되어 열흘 동안 다시 안 받았다. 회원님이 오후에
    보면 3월 분기에 멈춰 있었다.

    그래서 **'무엇을 가졌나'로 따진다.** 발표일 두어 달 앞에서 끝난 분기가
    우리에게 없으면 그 발표는 안 담긴 것이다. 시장마다 결산기 표기가 제각각인데
    (일본 '3月期 第１', 홍콩 영어 한 문장) 이 방법은 그걸 안 읽어도 된다.
    """
    pts = (rec or {}).get("points") or []
    if not pts:
        return True
    last = pts[-1].get("end") or ""
    try:
        want = (date.fromisoformat(last_ann) - timedelta(days=100)).isoformat()
    except ValueError:
        return False
    return last < want


def queue(old, cand, ann):
    """받을 순서.

    -1순위 **발표는 났는데 그 분기가 우리 기록에 없는 것** — 새치기시킨다.
           이게 없으면 어제 발표한 회사가 삼천 개 대기줄 뒤에 서서 몇 시간을
           기다린다(루멘텀이 그랬다).
     0순위 아직 못 받았거나 저장 형식이 헌 것
     1순위 발표일 언저리인데 오늘 아직 안 받은 것
     2순위 받은 지 오래된 것
     3순위 여기에도 자료가 없던 것 — 아주 가끔만
    같은 순위 안에서는 시가총액이 큰 쪽부터.
    """
    today = date.today().isoformat()
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 6)).isoformat()
    recent = (date.today() - timedelta(days=45)).isoformat()
    # 소스가 발표 당일 바로 싣지 않을 때가 있다. 그렇다고 매 실행마다 다시
    # 두드리면 그 몇백 종목이 대기줄을 통째로 차지한다 — 다만 이 종목들은
    # -1순위라 어차피 새치기 셋에 하나만 끼워 넣으므로(아래) 대기줄을 통째로
    # 차지하지는 못한다. 실행 주기가 5분으로 당겨졌으니 재시도 간격도 맞춰
    # 당긴다 — 세 시간이면 크론을 아무리 당겨도 소용이 없다.
    retry = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M")
    picks = []
    for k, cap in cand.items():
        rec = old.get(k)
        gap, last_ann = ann.get(k, (9999, ""))
        if (last_ann >= recent and missing_announced(rec, last_ann)
                and (rec.get("ts") if rec else "") < retry):
            picks.append((-1, -cap, k))
            continue
        if not rec or rec.get("v") != INTL_VER:
            pri = 0
        else:
            ts = rec.get("ts") or ""
            if not rec.get("points"):
                if ts >= cold:
                    continue          # 여기에도 없는 종목. 자주 두드리지 않는다.
                pri = 3
            elif gap <= NEAR_DAYS and ts < today:
                pri = 1
            elif ts < stale:
                pri = 2
            else:
                continue              # 아직 싱싱하다.
        picks.append((pri, -cap, k))
    picks.sort()
    # **발표 직후 그룹이 대기줄을 독식하지 못하게 섞는다.** 실적 시즌 절정에는
    # '방금 발표해서 다시 받아야 할' 종목만 700을 넘는다 — 그러면 아직 한 번도
    # 못 받은 종목(팝마트가 그랬다)이 매 실행 뒤로 밀려 영영 차례가 안 온다.
    # 새치기 셋에 밀린 것 하나 꼴로 끼워 넣으면 새 발표도 빠르게 담으면서
    # 못 받은 종목도 반드시 줄어든다.
    neg = [k for p, _c, k in picks if p < 0]
    rest = [k for p, _c, k in picks if p >= 0]
    out, i, j = [], 0, 0
    while i < len(neg) or j < len(rest):
        for _ in range(3):
            if i < len(neg):
                out.append(neg[i]); i += 1
        if j < len(rest):
            out.append(rest[j]); j += 1
    return out


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if probe:
        # 인자로 '시장:코드' 를 주면 그것만 본다. 오늘 발표한 회사가 소스에
        # 실렸는지 확인할 때 쓴다.
        want = [a for a in sys.argv[1:] if ":" in a and not a.startswith("-")]
        picks = ([(c, *c.split(":", 1)) for c in want] or
                 [("도요타", "jp", "7203"), ("소니", "jp", "6758"),
                  ("텐센트", "hk", "00700"), ("알리바바", "hk", "09988"),
                  ("SEA", "us", "SE"), ("알리바바 ADR", "us", "BABA")])
        for label, market, code in picks:
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
    ann = announcements()
    pending = queue(old, cand, ann)
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
        # 날짜만 남기면 '발표 직전에 받은 것'과 '발표 뒤에 받은 것'을 못 가른다.
        rec["ts"] = now_stamp()
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
