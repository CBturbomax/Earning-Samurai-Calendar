# -*- coding: utf-8 -*-
"""
data/earnings*.json  ->  index.html (단일 파일, 외부 의존 없음)

일본·미국·홍콩 세 시장을 한 페이지에 합친다. 시장마다 수집 소스도 원본 언어도
다르지만, 화면에 올라가는 행의 모양은 하나로 맞춘다 — pack_* 가 그 일을 한다.

주간 캘린더는 클라이언트에서 그린다. 주(週)를 넘길 때마다 서버가 없으니,
전 기간 데이터를 JSON으로 심어두고 JS가 해당 주만 잘라 렌더한다.

세 시장이 다 있어야 돌아가는 건 아니다. data/ 에 있는 것만 싣고, 없는 시장은
"미수집"으로 적는다. 없는 걸 빈 화면으로 두면 '발표가 없는 것'처럼 보인다.
"""
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import companies
import companies_hk
import companies_us
from markets import (CAP_STEPS, HK_TYPICAL, HKT, HOLIDAYS, JP_TYPICAL, KST,
                     MARKET_KO, MARKET_ORDER, MARKETS, SECTOR_KO, TIMING_KO,
                     US_AMC, US_BMO, US_EDT, US_EST, US_SECTOR_KO, USD_KRW,
                     holiday_ko)
from translit import to_korean

HERE = Path(__file__).parent
OUT = HERE / "index.html"

# 시장별 주목종목 사전. 코드가 시장 사이에 겹치므로(일본 8035 / 홍콩 08035)
# 합칠 때는 "jp:8035" 처럼 시장을 앞에 붙여 키를 만든다.
DICTS = {"jp": companies, "us": companies_us, "hk": companies_hk}

# 결산종별 표기를 짧게. 원문은 第１/第２/第３/本.
KIND_MAP = {"第１": "1Q", "第２": "2Q", "第３": "3Q", "本": "본결산"}



def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def us_utc_offset(d: date) -> int:
    """미국 동부 표준시 오프셋. 3월 둘째 일요일 ~ 11월 첫째 일요일이 서머타임."""
    def nth_sunday(year, month, n):
        first = date(year, month, 1)
        first += timedelta(days=(6 - first.weekday()) % 7)     # 그 달 첫 일요일
        return first + timedelta(weeks=n - 1)
    start = nth_sunday(d.year, 3, 2)
    end = nth_sunday(d.year, 11, 1)
    return US_EDT if start <= d < end else US_EST


def to_kst(mkt: str, day: str, hhmm: str, timing: str):
    """현지 발표 시점을 한국 시각으로 옮긴다.

    돌려주는 값: (한국날짜, 'HH:MM', 정확도)
      정확도 1 = 원본에 실제 시각이 있었다 (홍콩)
      정확도 0 = 통상 시각으로 어림했다 (미국 장전/장후, 일본 15시)
    시각을 전혀 모르면 ('', 0) 으로 두고 날짜만 옮기지 않는다 —
    모르는 걸 아는 척하면 캘린더가 조용히 거짓말을 한다.
    """
    d = date.fromisoformat(day)
    if mkt == "jp":
        # JST 와 KST 는 둘 다 UTC+9 라 시차가 없다. 날짜도 그대로다.
        return day, "%02d:%02d" % JP_TYPICAL, 0
    if mkt == "hk":
        if hhmm:
            h, m = int(hhmm[:2]), int(hhmm[3:5])
            exact = 1
        else:
            h, m = HK_TYPICAL
            exact = 0
        shift = KST - HKT                                       # 한국이 1시간 빠르다
    else:                                                       # 미국
        if timing == "장전":
            h, m = US_BMO
        elif timing == "장후":
            h, m = US_AMC
        else:
            return "", "", 0                                    # 시각을 모르면 옮기지 않는다
        exact = 0
        shift = KST - us_utc_offset(d)
    total = h * 60 + m + shift * 60
    day_shift, mins = divmod(total, 24 * 60)
    return (d + timedelta(days=day_shift)).isoformat(), "%02d:%02d" % divmod(mins, 60), exact


def load(mkt: str):
    """시장 하나치 수집 결과를 읽는다. 없으면 None — 있는 것만 싣는다."""
    path = HERE / "data" / MARKETS[mkt]["data"]
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"  ! {path.name} 읽기 실패: {e}")
        return None
    raw.setdefault("rows", [])
    raw.setdefault("ok_days", sorted({r["date"] for r in raw["rows"]}))
    return raw


# ── 시장별 행 다듬기 ───────────────────────────────────────────────
# 어느 시장이든 결과는 같은 모양으로 나온다:
#   [날짜, 코드, 한글명, 결산기, 분기, 업종, 거래소, 원문, 변환등급, 시장, 발표시각, 시총]
# 뒤 세 칸(시장·발표시각·시총)이 이번에 늘어난 자리다. 시각과 시총은 미국만 있다.

# 일본·홍콩 시총. 원본 소스가 안 줘서 따로 받아둔 것(scrape_caps.py).
# 없으면 0 — 그 시장에는 규모 필터가 걸리지 않고, 화면이 그렇게 적는다.
def load_extra():
    """따로 받아둔 시총·업종. 원본 소스가 안 주는 것들이다."""
    p = HERE / "data" / "caps.json"
    if not p.exists():
        return {}, {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("caps", {}), d.get("sectors", {})
    except (ValueError, OSError) as e:
        print(f"  ! caps.json 읽기 실패: {e}")
        return {}, {}


CAPS, SECTORS = load_extra()


def q_label(end, mid="", q=""):
    """분기말 날짜 -> '2Q26'. **끝난 날이 아니라 기간의 한가운데**로 가른다.

    회계 분기는 달력에 딱 맞지 않는다. 끝난 날로 가르면 이렇게 어긋난다.

      코카콜라  1~3월 분기가 4월 3일에 끝난다  -> 2Q26 (틀림, 1Q26 이 맞다)
      모토로라  4~6월 분기가 7월 4일에 끝난다  -> 3Q26 (틀림, 2Q26 이 맞다)

    수집기가 실제 한가운데(`mid`)를 담아 주면 그걸 쓴다. 없으면 끝나기 45일
    전으로 어림한다 — 13주 분기에는 맞지만 코스트코의 16주 분기에서는 빗나가
    라벨이 겹쳤다. 그래서 `mid` 가 있는 쪽이 옳다.
    """
    if q:
        return q                     # SEC 가 스스로 매긴 것. 그게 정답이다.
    d = date.fromisoformat(mid) if mid else \
        date.fromisoformat(end) - timedelta(days=45)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def q_index(label):
    """'2Q26' -> 정수. 분기끼리 앞뒤를 견주려면 숫자여야 한다."""
    try:
        q, y = label.split("Q")
        return (2000 + int(y)) * 4 + int(q) - 1
    except (ValueError, AttributeError):
        return None


def q_name(i):
    return f"{i % 4 + 1}Q{(i // 4) % 100:02d}"


def unstack(labels):
    """겹친 분기 이름을 뒤로 밀어 하나씩 떨어뜨린다.

    회계 분기가 달력과 여섯 주쯤 어긋나면 두 분기가 같은 달력 분기에 떨어진다.
    코스트코가 그렇다 — 11월~2월 분기와 2월~5월 분기가 둘 다 1Q26 이 됐다.
    SEC 프레임으로도 안 풀린다. SEC 는 잘 맞아떨어지는 분기에만 프레임을
    매기므로 코스트코는 넷 중 셋만 프레임이 있고, 그 셋만으로는 나머지 하나가
    갈 자리가 없다.

    자료는 종료일 순으로 정렬돼 있으니 **분기 이름도 반드시 뒤로 갈수록 커야
    한다.** 앞엣것보다 작거나 같으면 바로 다음 분기로 민다. 중간이 비어 있는
    것(수집이 덜 된 구간)은 그대로 둔다 — 없는 분기를 지어내지 않는다.
    """
    out, prev = [], None
    for lab in labels:
        i = q_index(lab)
        if i is None:
            out.append(lab)
            continue
        if prev is not None and i <= prev:
            i = prev + 1
        out.append(q_name(i))
        prev = i
    return out


def pack_fin(rec):
    """화면에 실을 것만 골라 담는다. 점은 [라벨, 매출, 영업이익].

    라벨은 받아둔 값을 쓰지 않고 여기서 다시 매긴다 — 종료일만 있으면 되므로
    표기 규칙을 고칠 때 4천 종목을 다시 받지 않아도 된다.
    """
    out = {k: rec[k] for k in ("freq", "eps", "cur", "src") if rec.get(k)}
    if rec.get("freq") in ("Q", "H"):
        pts = rec.get("points") or []
        labs = unstack([q_label(p["end"], p.get("mid", ""), p.get("q", ""))
                        if p.get("end") else p["label"] for p in pts])
        out["points"] = [[lab, p["rev"], p.get("opi")]
                         for lab, p in zip(labs, pts)]
    return out


def load_fin():
    """따로 받아둔 실적 수치(매출·영업이익 시계열, 발표 완료 여부).

    출처가 셋이다. 미국은 SEC(financials.json), 일본·홍콩은 stockanalysis
    (financials_intl.json), 그리고 일본은 **발표 당일치를 TDnet**에서 따로
    받는다(financials_jp.json). 열쇠는 `시장:코드` 로 맞춘다 — 일본 8035 와
    홍콩 08035 는 다른 회사이므로 코드만으로는 가를 수 없다.
    """
    out = {}
    for name, prefix in (("financials.json", "us:"),
                         ("financials_intl.json", ""),
                         ("financials_jp.json", "")):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError) as e:
            print(f"  ! {name} 읽기 실패: {e}")
            continue
        for k, v in got.items():
            # 예전 financials.json 은 열쇠가 'AAPL' 이었다. 'us:' 를 붙여 옮긴다.
            key = k if ":" in k else prefix + k
            out[key] = merge_fin(out.get(key), v)
    return out


def last_end(rec):
    pts = (rec or {}).get("points") or []
    return pts[-1].get("end", "") if pts else ""


def merge_fin(a, b):
    """한 종목에 두 소스가 있으면 합친다.

    SEC 는 1Q19 까지 깊지만 **실적 발표가 아니라 10-Q 가 올라와야** 값이 생긴다.
    그 사이가 며칠에서 몇 주다 — 루멘텀은 8/11 에 6월 분기를 발표했는데 SEC 쪽은
    3월 분기에서 멈춰 있었다. stockanalysis 는 발표 당일 반영되지만 20분기뿐이다.

    그래서 갈아치우지 않고 **깊은 쪽을 뼈대로 삼고 빠진 최근 분기를 메운다.**
    겹치는 분기는 공식 자료(SEC)를 남긴다. 통화가 다르면 섞지 않는다 — 그건
    같은 회사의 다른 보고 기준이라 한 막대그래프에 올리면 안 된다.
    """
    if not a:
        return b
    if not b:
        return a
    if a.get("freq") != b.get("freq") or (a.get("cur") or "") != (b.get("cur") or ""):
        # 섞지 않는다 — 통화나 주기가 다르면 같은 막대그래프에 못 올린다.
        # 고를 때는 **점이 많은 쪽**이 먼저다. 최신만 보면 방금 받은 한 분기짜리가
        # 스무 분기짜리를 밀어낸다.
        ka = (len(a.get("points") or []), last_end(a))
        kb = (len(b.get("points") or []), last_end(b))
        return a if ka >= kb else b

    # 뼈대는 **점이 많은 쪽**, 겹치는 분기는 **공식 자료 쪽**을 남긴다.
    #   sec   미국 공식 재무제표
    #   tdnet 일본 공식 결산단신 — 발표 당일에 나온다
    #   sa    stockanalysis — 20분기로 깊지만 일본은 며칠 늦는다
    # 예전에는 'sec 이면 뼈대' 하나로만 갈랐는데, 일본은 sec 이 없어서 그 규칙이
    # 아무 일도 안 했다. 그러면 TDnet 이 방금 받아온 새 분기가 stockanalysis 의
    # 헌 줄에 덮여 사라진다.
    rank = {"sec": 3, "tdnet": 2, "mix": 1, "sa": 1, "yahoo": 0}
    pa, pb = rank.get(a.get("src"), 0), rank.get(b.get("src"), 0)
    base, extra = (a, b) if len(a.get("points") or []) >= len(b.get("points") or []) else (b, a)
    win = a if pa >= pb else b               # 겹치는 분기를 가져갈 쪽
    lose = b if win is a else a
    by_end = {p["end"]: p for p in lose.get("points") or [] if p.get("end")}
    by_end.update({p["end"]: p for p in win.get("points") or [] if p.get("end")})
    pts = [by_end[e] for e in sorted(by_end)]
    mixed = len(pts) > len(base.get("points") or [])
    out = dict(base)
    out["points"] = pts
    out["src"] = "mix" if mixed else base.get("src")
    if not out.get("eps") and extra.get("eps"):
        out["eps"] = extra["eps"]
    return out


FIN = load_fin()


def load_seg():
    """사업부별 매출. 열쇠는 미국 코드라 'us:' 를 붙여 맞춘다."""
    p = HERE / "data" / "segments.json"
    if not p.exists():
        return {}
    try:
        got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError) as e:
        print(f"  ! segments.json 읽기 실패: {e}")
        return {}
    return {(k if ":" in k else "us:" + k): v for k, v in got.items() if v.get("names")}


SEG = load_seg()


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0.0


def seg_fit(rec, fin_rec):
    """부문 합을 총매출과 대보고, **두 배로 부푼 것만** 걸러낸다.

    처음에는 안 맞는 부문을 하나씩 빼서 총매출에 맞추게 했다. 그게 더 나빴다.
    실제 자료를 대보니 어긋나는 이유가 넷인데 셋은 부문 잘못이 아니었다.

    - 웨이스트매니지먼트: 부문을 **상계 전 총액**으로 낸다. 늘 8% 넘친다. 정상이다.
    - 존슨컨트롤스: 부문은 멀쩡한데 **총매출 쪽이 틀렸다**(6,442 -> 1,004 -> 447).
    - 캐터필러: 부문 이름이 깔끔히 바뀐 정상 케이스인데 총매출 오류에 휘말렸다.
    - 버텍스: 같은 이름이 표에 두 줄 있어 정확히 두 배가 됐다. 이건 진짜 잘못인데
      **수집기에서 고쳤다**(`parse()` 가 이름 중복을 걸러낸다).

    맞추려 든 결과 웨이스트매니지먼트는 가장 큰 부문(Collection)이 빠져 37%만
    남았다. 매출 대부분이 사라진 그림이 어긋난 그림보다 나을 리 없다.

    그래서 지금은 **부문을 지우지 않는다.** 총매출을 25% 넘게 웃도는 분기가
    과반이면 — 같은 줄이 두 번 실린 신호다 — 그 종목만 통째로 싣지 않는다.
    총매출 자체가 못 미더울 수 있으므로 어림한 어긋남으로는 판단하지 않는다.

    돌려주는 값: (이름, 점, 총매출 대비 비율) — 못 쓰겠으면 None.
    비율은 분기마다 고르게 나올 때만 준다. 들쭉날쭉하면 대볼 총매출이 못 미더운
    것이라 None 이다.
    """
    names = list(rec.get("names") or [])
    pts = [r for r in rec.get("pts") or [] if r and r[0]]
    if len(names) < 2 or len(pts) < 2:
        return None

    rev = {p["end"]: p["rev"] for p in (fin_rec or {}).get("points") or []
           if p.get("end") and p.get("rev")}
    shared = [r for r in pts if rev.get(r[0])]
    if len(shared) < 4:
        return names, pts, None                # 대볼 총매출이 없다. 그대로 싣는다.

    rs = sorted(sum(v or 0 for v in r[1:]) / rev[r[0]] for r in shared)
    # 한 분기가 튀는 것으로 판을 뒤집지 않도록 위아래를 조금 깎고 본다.
    lo, hi = rs[len(rs) // 10], rs[-1 - len(rs) // 10]
    med = _median(rs)

    # **높이보다 고르기가 갈라준다.** 같은 줄이 두 번 실렸으면 비율이 분기마다
    # 비슷하게 높다(버텍스 1.72~2.00, 메르카도리브레 1.00~1.46). 반대로 총매출
    # 쪽이 망가진 경우는 널을 뛴다(존슨컨트롤스 1.00~42.56) — 그건 부문 잘못이
    # 아니므로 부문 차트까지 뺏을 이유가 없다.
    if med > 1.25 and hi - lo < 1.0:
        return None                            # 부풀었다. 싣지 않는다.

    # "총매출의 몇 %" 는 두 수치가 서로 아귀가 맞을 때만 적는다. 흔들리는데
    # 적으면 틀린 근거로 적는 셈이다.
    return names, pts, (med if hi - lo < 0.10 else None)


def pack_seg(rec, fin_rec):
    """[분기 라벨, 부문1, 부문2, …]. 라벨은 종료일에서 다시 매긴다."""
    fit = seg_fit(rec, fin_rec)
    if not fit:
        return None
    names, pts, med = fit
    out = {"names": names, "pts": [[q_label(r[0])] + r[1:] for r in pts]}
    # 총매출의 몇 %를 덮는지. 부문이 전부를 설명하지 않는 회사가 흔하다
    # (본사 몫·기타). 막대 높이를 총매출로 오해하지 않도록 적어둔다.
    if med is not None and med < 0.95:
        out["cov"] = round(med * 100)
    return out


def pack_jp(r):
    """일본만 기계 변환을 거친다. 원본이 일본어라 그대로는 훑어보기가 안 된다."""
    ko, lvl = to_korean(r["name"], companies.NOTABLE.get(r["code"], ("",))[0])
    return [r["date"], r["code"], ko,
            r.get("fy", "").replace("月期", "월 결산"),
            KIND_MAP.get(r.get("kind", ""), r.get("kind", "")),
            SECTOR_KO.get(r.get("sector", ""), r.get("sector", "")),
            MARKET_KO.get(r.get("market", ""), r.get("market", "")),
            r["name"], lvl, "jp", "", CAPS.get("jp:" + r["code"], 0)
            ] + list(to_kst("jp", r["date"], "", ""))


# 홍콩 결산기는 원본이 공시 문서 제목이라 통째로 영어 한 문장이다.
#   'ANNOUNCEMENT OF THE RESULTS FOR THE THREE MONTHS ENDED 31 MARCH 2026'
# 그대로 실으면 칸을 넘겨 문장 중간에서 잘린다. 어느 기간인지만 뽑아 적는다.
# 표현이 회사마다 제각각이라(31 MARCH 2026 / MARCH 31, 2026 / 31ST MARCH ...)
# 몇 갈래로 나눠 본다. 그래도 못 읽으면 **원문을 그대로 둔다** — 짐작해 넣지 않는다.
HK_MONTH = {m: i + 1 for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
     "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"])}
HK_SPAN = {"THREE": "분기", "SIX": "반기", "NINE": "누적 9개월", "TWELVE": "연간"}
HK_ORD = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4}
_MON = "|".join(HK_MONTH)
# '(기간) ENDED (날짜)' — 날짜는 '31 MARCH 2026' 과 'MARCH 31, 2026' 둘 다 온다.
HK_ENDED = re.compile(
    r"(?:(THREE|SIX|NINE|TWELVE)\s+MONTHS|(?:HALF-YEAR\s+)?(YEAR|PERIOD))\s+ENDED\s+"
    r"(?:\d{1,2}(?:ST|ND|RD|TH)?\s+(" + _MON + r")|(" + _MON + r")\s+\d{1,2})"
    r"[,\s]+(\d{4})")
HK_QUARTER = re.compile(r"(FIRST|SECOND|THIRD|FOURTH)\s+QUARTER")
HK_KIND = re.compile(r"(INTERIM|ANNUAL|FINAL)\s+RESULTS")
HK_YEAR = re.compile(r"(?:19|20)(\d{2})")


def hk_period(title):
    """공시 제목 -> '2026년 3월 분기' 처럼. 못 읽으면 빈 문자열."""
    t = (title or "").upper()

    m = HK_ENDED.search(t)
    if m:
        span = HK_SPAN.get(m.group(1) or "") or ("연간" if m.group(2) == "YEAR" else "반기")
        mon = HK_MONTH.get(m.group(3) or m.group(4) or "")
        if mon:
            return f"{m.group(5)}년 {mon}월 {span}"

    # 연도는 제목 어딘가에 있다. 여러 개면 뒤엣것이 결산 연도다(2025/2026 처럼).
    years = HK_YEAR.findall(t)
    year = "20" + years[-1] if years else ""

    m = HK_QUARTER.search(t)
    if m and year:
        return f"{year}년 {HK_ORD[m.group(1)]}분기"

    m = HK_KIND.search(t)
    if m and year:
        return f"{year}년 " + ("반기" if m.group(1) == "INTERIM" else "연간")
    if year and "INTERIM" in t:
        return f"{year}년 반기"
    if year and "ANNUAL" in t:
        return f"{year}년 연간"
    return ""


def pack_en(r, mkt):
    """미국·홍콩은 원본이 영문이라 그대로도 읽힌다. 사전에 있으면 한글명을 쓰고
    없으면 영문명을 그대로 둔다 — 억지 음차는 오히려 못 알아보게 만든다.
    지어낸 표기가 아니므로 '기계 변환'(등급 0) 점선은 붙지 않는다."""
    cur = DICTS[mkt].NOTABLE.get(r["code"])
    name = r.get("name", "")
    # 미국의 time 은 장전/장후 구분, 홍콩의 time 은 실제 시각(HH:MM)이다.
    raw_time = r.get("time", "")
    timing = TIMING_KO.get(raw_time, "") if mkt == "us" else ""
    hhmm = raw_time if mkt == "hk" else ""
    # 업종은 원본에 없다. 미국은 나스닥 스크리너에서 따로 받아둔 것을 붙인다.
    sec = r.get("sector", "") or SECTORS.get(mkt + ":" + r["code"], "")
    fy = r.get("fy", "")
    if mkt == "hk":
        fy = hk_period(fy) or fy
    return [r["date"], r["code"], cur[0] if cur else name,
            fy, r.get("kind", ""),
            US_SECTOR_KO.get(sec, sec),
            MARKET_KO.get(r.get("market", ""), r.get("market", "")),
            name, 2, mkt, timing,
            # 미국은 나스닥이 시총을 같이 주고, 홍콩은 따로 받아둔 것을 붙인다.
            r.get("cap", 0) or CAPS.get(mkt + ":" + r["code"], 0)
            ] + list(to_kst(mkt, r["date"], hhmm, timing))


def build():
    data = {m: load(m) for m in MARKET_ORDER}
    have = [m for m in MARKET_ORDER if data[m]]
    if not have:
        raise SystemExit("data/ 에 수집 결과가 하나도 없습니다. scrape*.py 를 먼저 돌리세요.")

    packed, ok_days, sources, mkt_meta = [], {}, [], []
    for m in MARKET_ORDER:
        raw = data[m]
        cfg = MARKETS[m]
        rows = raw["rows"] if raw else []
        packed += [pack_jp(r) if m == "jp" else pack_en(r, m) for r in rows]
        ok_days[m] = raw["ok_days"] if raw else []
        if raw:
            sources.append({
                "mkt": m, "name": raw.get("source", ""),
                "url": raw.get("source_url", ""), "count": len(rows),
                "range": ([ok_days[m][0], ok_days[m][-1]] if ok_days[m] else []),
            })
        mkt_meta.append({
            "id": m, "ko": cfg["ko"], "flag": cfg["flag"], "accent": cfg["accent"],
            "count": len(rows), "note": cfg["note"], "scraper": cfg["scraper"],
            "has": bool(raw),
        })

    # 한 날짜 안에서는 시장 순 -> 시총 큰 순 -> 코드 순.
    # 시총은 미국만 있어서 나머지 시장은 자연히 코드 순으로 남는다.
    # 하루 700건씩 쏟아지는 미국에서 앞 12개만 펼쳐 보일 때 큰 게 먼저 오게 하려는 것.
    order = {m: i for i, m in enumerate(MARKET_ORDER)}
    packed.sort(key=lambda p: (p[0], order[p[9]], -p[11], p[1]))

    notable = {}
    for m in MARKET_ORDER:
        for code, v in DICTS[m].NOTABLE.items():
            notable[m + ":" + code] = list(v)

    per_day = Counter(p[0] for p in packed)
    notable_hits = sum(1 for p in packed if p[9] + ":" + p[1] in notable)
    all_ok = sorted({d for m in ok_days for d in ok_days[m]})

    # 데이터가 있는 주만 네비게이션에 노출한다.
    # 한국 시간으로 보면 미국 장후 발표가 다음 날로 밀리므로, 그 날짜도 포함한다.
    kdays = {p[12] for p in packed if p[12]}
    weeks = sorted({monday_of(date.fromisoformat(d)).isoformat()
                    for d in set(all_ok) | kdays})

    today = date.today().isoformat()
    default_week = monday_of(date.fromisoformat(today)).isoformat()
    if default_week not in weeks and weeks:
        default_week = min(weeks, key=lambda w: abs(
            (date.fromisoformat(w) - date.fromisoformat(today)).days))

    # 시장별 시총 수집률 (종목 단위로 센다 — 한 종목이 여러 날 나올 수 있다)
    cap_cover = {}
    for m in MARKET_ORDER:
        codes = {p[1]: p[11] for p in packed if p[9] == m}
        if codes:
            cap_cover[m] = round(sum(1 for v in codes.values() if v) / len(codes), 4)

    # 사업부별 매출은 총매출과 대본 뒤에 싣는다. 두 배로 부푼 종목은 조용히
    # 지우지 않고 몇 종목을 뺐는지 수집 기록에 적는다.
    on_screen = {p[9] + ":" + p[1] for p in packed}
    seg, tossed = {}, []
    for s, rec in SEG.items():
        if s not in on_screen:
            continue
        got = pack_seg(rec, FIN.get(s))
        if got:
            seg[s] = got
        else:
            tossed.append(s.split(":")[-1])
    if SEG:
        note = f"  사업부별 매출 {len(seg):,}종목"
        if tossed:
            note += (f" · 합이 총매출보다 고르게 부풀어 뺀 종목 {len(tossed)}"
                     f" ({', '.join(sorted(tossed)[:6])})")
        print(note)

    payload = {
        "rows": packed,
        "notable": notable,
        "groupOrder": {m: DICTS[m].GROUP_ORDER for m in MARKET_ORDER},
        "holidays": {m: {d: holiday_ko(n) for d, n in HOLIDAYS[m].items()}
                     for m in MARKET_ORDER},
        "okDays": ok_days,
        "markets": mkt_meta,
        "weeks": weeks,
        "defaultWeek": default_week,
        "today": today,
        "sources": sources,
        # 규모 필터 눈금. 원 단위로 매기되 실제 비교는 달러(십억)로 한다.
        "capSteps": [{"jo": j, "usdB": round(j * 1e12 / USD_KRW / 1e9, 2)}
                     for j in CAP_STEPS],
        "usdKrw": USD_KRW,
        # 시총 데이터가 있는 시장. 없는 시장에는 규모 필터를 적용할 수 없다.
        "capMarkets": sorted({p[9] for p in packed if p[11]}),
        "capCover": cap_cover,
        # **시총이 캘린더 원본에 같이 오는 시장.** 규모 필터에서 '시총을 모르는
        # 종목'을 감춰도 되는지는 수집률이 아니라 이걸로 갈라야 한다.
        # 미국은 나스닥이 시총을 같이 주므로 비어 있으면 정말 값이 없는 종목이다.
        # 일본·홍콩은 따로 받아 붙이는 거라 비어 있으면 '아직 못 받았다'는 뜻이고,
        # 거기엔 히로세전기(8,828억엔) 같은 회사가 섞여 있다 — 지우면 안 된다.
        # (수집률로 갈랐다면 일본 97.6%·홍콩 99.2%라 오히려 그쪽이 지워졌을 것이다.)
        "capInline": [m for m in MARKET_ORDER if MARKETS[m].get("cap")],
        # 실적 수치. 캘린더에 실린 종목 것만, 그중에서도 알맹이가 있는 것만
        # 싣는다. 수집 쪽에는 '두드려 봤지만 자료가 없더라'는 표시만 남은 기록도
        # 있는데(v/ts/none), 그건 다음에 또 두드릴지 정하는 데만 쓰고 화면에는
        # 필요 없다. 그대로 실으면 index.html 만 몇 배로 부푼다.
        # 연간 수치는 **점을 싣지 않는다.** 화면에 그리지 않기로 했으므로 실어봐야
        # 파일만 무거워진다. 다만 'freq' 는 남겨서 "분기를 못 구했다"와
        # "아직 안 받았다"를 화면에서 가려 말할 수 있게 한다.
        #
        # 점은 [라벨, 매출, 영업이익] 배열로 눕힌다. 이름표를 종목마다 스무 번씩
        # 되풀이하면 그것만으로 파일이 반 메가 늘어난다. 종료일과 순이익은
        # 화면에서 안 쓰므로 빼고, 자료 파일에는 그대로 남겨 둔다.
        "fin": {s: pack_fin(rec) for s, rec in FIN.items()
                if (rec.get("points") or rec.get("eps"))
                and s in {p[9] + ":" + p[1] for p in packed}},
        # 사업부별 매출. "매출이 늘었다"보다 "어디서 늘었다"가 중요할 때가 있다.
        # 지금은 미국 종목만 — 일본·홍콩은 소스에 부문 페이지가 없다.
        # 합이 총매출과 안 맞는 종목은 여기서 걸러진다(seg_fit).
        "seg": seg,
    }

    # **러너는 UTC 로 돈다.** 예전에는 datetime.now() 에 "KST" 만 붙였는데,
    # 그러면 화면에 늘 아홉 시간 뒤처진 시각이 뜬다 — 오후 5시에 봤는데
    # "갱신 07:49 KST" 라고 적혀 있으니 하루 종일 안 돌아간 것처럼 보인다.
    stamp = (datetime.now(timezone.utc) + timedelta(hours=9)
             ).strftime("%Y-%m-%d %H:%M KST")
    parts = " · ".join(f'{MARKETS[m]["flag"]} {MARKETS[m]["ko"]} <b>{len(data[m]["rows"]):,}</b>'
                       for m in have)
    head = (f'{parts} · 합계 <b>{len(packed):,}건</b> · '
            f'수집 <b>{all_ok[0]} ~ {all_ok[-1]}</b> · 갱신 {stamp}'
            if all_ok else f'{parts} · 갱신 {stamp}')

    # 기계 변환은 일본에만 해당한다. 미국·홍콩은 영문 원문을 그대로 쓴다.
    jp_lvl = Counter(p[8] for p in packed if p[9] == "jp")
    jp_total = sum(jp_lvl.values())
    tl_note = (f'일본 회사명 {jp_total:,}건 중 <b>{jp_lvl[2] + jp_lvl[1]:,}건</b>은 사전 표기, '
               f'<b>{jp_lvl[0]:,}건</b>은 기계 변환입니다. '
               f'미국·홍콩은 원본이 영문이라 사전에 있으면 한글명, 없으면 영문명을 그대로 씁니다.'
               if jp_total else
               '미국·홍콩은 원본이 영문이라 사전에 있으면 한글명, 없으면 영문명을 그대로 씁니다.')

    # 시장 색은 markets.py 한 군데서만 정한다. CSS 변수로 흘려보낸다.
    mkt_css = "\n".join(
        f'.m-{m} {{ --mk:{MARKETS[m]["accent"]}; }}' for m in MARKET_ORDER)

    html = TEMPLATE.replace("__HEAD__", head) \
                   .replace("__TLNOTE__", tl_note) \
                   .replace("__MKTCSS__", mkt_css) \
                   .replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                                   separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"{OUT}  ({kb:,.0f} KB)")
    for m in MARKET_ORDER:
        n = len(data[m]["rows"]) if data[m] else 0
        days = len(ok_days[m])
        state = f"{n:>6,}건 / {days:>3}일" if data[m] else "     미수집 — " + MARKETS[m]["scraper"]
        print(f"  {MARKETS[m]['flag']} {MARKETS[m]['ko']:<3} {state}")
    print(f"  합계 {len(packed):,}건 / 주목 {notable_hits:,}건 / {len(weeks)}주")
    if per_day:
        busiest = max(per_day.items(), key=lambda kv: kv[1])
        print(f"  최다 {busiest[0]} {busiest[1]:,}건")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<!-- GitHub Pages는 같은 URL에 새 파일을 덮어쓴다. 캐시가 남으면 지난주 일정을
     이번주로 착각하게 되므로 매번 새로 받도록 강제한다. -->
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Earning Samurai — 글로벌 실적발표 캘린더</title>
<!-- 대표 아이콘. 외부 파일을 받지 않도록 SVG를 그대로 심는다 (투구 + 선글라스). -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='13' fill='%230f1419'/%3E%3Cpath d='M13 27C10 13 16 6 21 5c-1 9 4 13 8 15z' fill='%23FFC01E'/%3E%3Cpath d='M51 27c3-14-3-21-8-22 1 9-4 13-8 15z' fill='%23FFC01E'/%3E%3Cpath d='M32 12c-11 0-19 8-19 18h38c0-10-8-18-19-18z' fill='%231b1b1b'/%3E%3Ccircle cx='32' cy='39' r='17' fill='%23FFC01E'/%3E%3Crect x='15' y='33' width='34' height='4' rx='2' fill='%23111'/%3E%3Crect x='16' y='33' width='13' height='10' rx='3.5' fill='%23111'/%3E%3Crect x='35' y='33' width='13' height='10' rx='3.5' fill='%23111'/%3E%3Cpath d='M25 47q7 5 14 0' stroke='%23111' stroke-width='2.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=42dot+Sans:wght@300..800&display=swap"
      rel="stylesheet">
<style>
:root {
  --bg:#0f1419; --fg:#ffffff; --a1:#F0435A; --a2:#5B9BD5; --a3:#FFB020;
  --panel:#161d24; --line:#243039; --mute:#93a4b1; --ok:#7FD1A4;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:32px 28px 80px;
  background:var(--bg); color:var(--fg);
  font-family:'42dot Sans','Noto Sans JP','Yu Gothic','Meiryo',
              'Malgun Gothic',sans-serif;
  font-size:20px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1680px; margin:0 auto; }
.topline {
  font-size:19px; color:var(--mute); margin:0 0 10px; padding:9px 16px;
  background:var(--panel); border:1px solid var(--line); border-radius:8px;
  border-left:5px solid var(--a1); display:inline-block;
}
.topline b { color:var(--fg); }
h1 { font-size:38px; font-weight:800; margin:0 0 6px; letter-spacing:-.5px;
     display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
h1 .jp { color:var(--a3); }
h1 .byline { font-size:20px; font-weight:600; color:var(--mute); letter-spacing:0; }
/* 투구 아이콘 — 파비콘과 같은 그림. 외부 파일을 받지 않게 SVG를 심는다. */
h1 .mark {
  width:44px; height:44px; flex:0 0 auto; border-radius:10px;
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='13' fill='%23161d24'/%3E%3Cpath d='M13 27C10 13 16 6 21 5c-1 9 4 13 8 15z' fill='%23FFC01E'/%3E%3Cpath d='M51 27c3-14-3-21-8-22 1 9-4 13-8 15z' fill='%23FFC01E'/%3E%3Cpath d='M32 12c-11 0-19 8-19 18h38c0-10-8-18-19-18z' fill='%231b1b1b'/%3E%3Ccircle cx='32' cy='39' r='17' fill='%23FFC01E'/%3E%3Crect x='15' y='33' width='34' height='4' rx='2' fill='%23111'/%3E%3Crect x='16' y='33' width='13' height='10' rx='3.5' fill='%23111'/%3E%3Crect x='35' y='33' width='13' height='10' rx='3.5' fill='%23111'/%3E%3Cpath d='M25 47q7 5 14 0' stroke='%23111' stroke-width='2.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") center/contain no-repeat;
}
h2 {
  font-size:26px; font-weight:700; margin:52px 0 14px;
  padding-left:14px; border-left:6px solid var(--a3);
}
h2 .n { color:var(--a3); margin-right:10px; }
.sub { color:var(--mute); font-size:20px; margin:0 0 4px; }
.meta { color:var(--mute); font-size:18px; font-weight:400; }

.cards { display:flex; flex-wrap:wrap; gap:16px; margin:18px 0 8px; }
.card {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 22px; min-width:190px;
}
.card .k { color:var(--mute); font-size:18px; }
.card .v { font-size:30px; font-weight:800; color:var(--a1); }
.card .v.sm { font-size:22px; }

/* ── 시장 탭 ───────────────────────────────────────────────── */
/* --mk 는 시장 강조색. markets.py 가 유일한 출처고 여기로 흘러온다. */
__MKTCSS__
/* 규모 필터가 닿지 않는 시장 안내 */
.capnote {
  background:#1a2129; border:1px solid var(--line); border-left:5px solid var(--a3);
  border-radius:8px; padding:11px 18px; margin:0 0 12px; color:#c9d6e0; font-size:18px;
}
.capnote b { color:var(--a3); }
.capnote .dim { color:var(--mute); font-size:16px; }

.mtabs { display:flex; flex-wrap:wrap; gap:10px; margin:20px 0 4px; }
.mtab {
  display:flex; align-items:center; gap:9px; background:var(--panel);
  border:1px solid var(--line); border-bottom:3px solid transparent;
  border-radius:10px; padding:10px 18px; cursor:pointer;
  font-family:inherit; color:var(--fg); font-size:19px; font-weight:700;
  line-height:1.2;
}
.mtab:hover { border-color:#31414f; }
.mtab .mchk { width:17px; height:17px; accent-color:var(--mk,var(--a2)); cursor:pointer; margin:0; }
.mtab .fl { font-size:21px; }
.mtab .n {
  color:var(--mute); font-weight:600; font-size:17px;
  font-variant-numeric:tabular-nums;
}
.mtab.m-all { --mk:#93a4b1; }
.mtab.on { background:#1b2530; border-bottom-color:var(--mk,var(--a2)); }
.mtab.on .n { color:var(--fg); }
/* 아직 수집하지 않은 시장. 눌러서 사유를 볼 수 있게 죽이지는 않는다. */
.mtab.empty { opacity:.55; }
.mtab.empty .n { color:#6b7b88; font-weight:400; }

.note {
  background:#1a2129; border:1px solid var(--line); border-left:5px solid var(--a3);
  border-radius:8px; padding:14px 20px; margin:14px 0; color:#c9d6e0; font-size:19px;
}

/* ── 알림 배너 ─────────────────────────────────────────────── */
.alertbar {
  background:linear-gradient(90deg,#2a1a20,#1a2129);
  border:1px solid #4a2530; border-left:5px solid var(--a1);
  border-radius:10px; padding:16px 20px; margin:16px 0;
}
.alertbar.none { border-left-color:var(--line); background:#151c23; }
.alertbar .ah { font-size:21px; font-weight:700; margin-bottom:6px; }
.alertbar .ah .cnt { color:var(--a1); }
.alertbar .arow {
  display:flex; flex-wrap:wrap; gap:8px; margin-top:10px;
}
.alertbar .hint { color:var(--mute); font-size:18px; }

/* ── 툴바 ──────────────────────────────────────────────────── */
.tools {
  display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:14px 0;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px;
}
select, input[type=search] {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 13px; font-size:19px; font-family:inherit;
}
select { max-width:230px; }
select:focus { outline:2px solid var(--a3); }
input[type=search] { width:min(420px,100%); padding:11px 16px; font-size:20px; }
input[type=search]:focus { outline:2px solid var(--a1); border-color:var(--a1); }
.chk {
  display:inline-flex; align-items:center; gap:8px; font-size:19px;
  cursor:pointer; user-select:none; white-space:nowrap;
}
.chk input { width:20px; height:20px; accent-color:var(--a1); cursor:pointer; }
.count { margin-left:auto; color:var(--mute); font-size:19px; }
.count b { color:var(--a1); font-size:22px; }

/* button 과 a 를 함께 받는다. 예전에는 button.btn 으로만 잡아서
   모달의 <a class="btn"> 링크가 맨 파란 글씨로 나왔다. */
.btn {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 16px; font-size:19px; font-family:inherit;
  cursor:pointer; text-decoration:none; display:inline-block; line-height:1.2;
}
.btn:hover { border-color:var(--a1); color:var(--a1); }
.btn.pri { background:var(--a1); border-color:var(--a1); color:#fff; font-weight:700; }
.btn.pri:hover { filter:brightness(1.12); color:#fff; }
button.btn:disabled { opacity:.4; cursor:default; }
button.btn:disabled:hover { border-color:var(--line); color:var(--fg); }

/* ── 주 네비게이션 ─────────────────────────────────────────── */
.weeknav {
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:12px 16px; margin:14px 0;
}
.weeknav .wlabel { font-size:24px; font-weight:800; letter-spacing:-.3px; }
.weeknav .wsum { color:var(--mute); font-size:18px; }
.weeknav .spacer { margin-left:auto; }

/* 나라 고르기 — 위쪽 탭과 같은 것을 캘린더 옆에도 둔다. 주를 넘기다가
   나라를 바꾸려고 맨 위까지 올라갔다 오지 않게. 둘은 늘 같이 움직인다. */
.mpick { display:flex; gap:6px; flex-wrap:wrap; }
.mpick .mp {
  font:inherit; font-size:17px; font-weight:700; cursor:pointer;
  background:#141c24; color:var(--mute); border:1px solid var(--line);
  border-radius:999px; padding:5px 13px; line-height:1.3;
}
.mpick .mp:hover:not(:disabled) { border-color:var(--a2); color:var(--fg); }
.mpick .mp.on { background:var(--a2); border-color:var(--a2); color:#0b1116; }
.mpick .mp:disabled { opacity:.4; cursor:not-allowed; }

/* ── 주간 캘린더 ───────────────────────────────────────────── */
.cal {
  display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:14px;
  align-items:start;
}
@media (max-width:1400px) { .cal { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media (max-width:900px)  { .cal { grid-template-columns:minmax(0,1fr); } }

.day {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  min-width:0; overflow:hidden;
}
.day.today { border-color:var(--a1); box-shadow:0 0 0 1px var(--a1) inset; }
.day.closed { opacity:.62; }
.dh {
  padding:12px 14px; border-bottom:1px solid var(--line); background:#1b232b;
  display:flex; align-items:baseline; gap:8px;
}
.dh .dow { font-size:18px; color:var(--mute); font-weight:700; }
.dh .dnum { font-size:26px; font-weight:800; }
.day.today .dh .dnum, .day.today .dh .dow { color:var(--a1); }
.dh .dcnt { margin-left:auto; font-size:18px; color:var(--mute); }
.dh .dcnt b { color:var(--a3); font-size:20px; }
.dh .todaytag {
  font-size:14px; font-weight:700; background:var(--a1); color:#fff;
  border-radius:4px; padding:2px 7px; margin-left:4px;
}
.dbody { padding:10px 10px 12px; }
.dsec {
  font-size:16px; color:var(--mute); font-weight:700; margin:6px 2px 7px;
  letter-spacing:.02em;
}
.dsec.star { color:var(--a3); }
.dsec.gap { color:#8a6d3b; font-weight:600; border-top:1px dashed #3a3222;
            padding-top:6px; margin-top:8px; }
/* 수집 전인 시장의 자리 */
.cal.none { display:block; }
.nodata {
  background:var(--panel); border:1px dashed #3a4550; border-radius:10px;
  padding:38px 26px; text-align:center; font-size:21px; color:#c9d6e0;
}
.nodata span { display:block; margin-top:10px; font-size:18px; color:var(--mute); }
.nodata b { color:var(--a3); font-weight:700; }
.empty { color:#55636e; font-size:18px; padding:16px 4px; text-align:center; }
.empty .why { display:block; color:var(--mute); font-size:17px; margin-top:4px; }

/* 종목 칩 — 왼쪽 띠 색이 시장이다. 세 시장을 한 칸에 섞어 놓아도 구분된다. */
.chip {
  display:flex; align-items:center; gap:7px; width:100%;
  background:#0f1620; border:1px solid #1e2831; border-radius:7px;
  padding:7px 9px 7px 12px; margin-bottom:5px; cursor:pointer; text-align:left;
  font-family:inherit; color:var(--fg); font-size:17px; line-height:1.3;
  box-shadow:inset 3px 0 0 0 var(--mk,transparent);
}
.chip .fl { flex:0 0 auto; font-size:15px; }
/* 발표 시각 — 미국만 원본에 있다. 장전/장후는 미국 실적을 볼 때 제일 먼저 보는 값. */
.chip .tm, .tm {
  flex:0 0 auto; font-size:13px; font-weight:700; border-radius:4px;
  padding:1px 5px; white-space:nowrap;
}
.tm.pre { color:var(--a3); border:1px solid #4a3a1c; }
.tm.post { color:var(--ok); border:1px solid #24463a; }
/* 한국 시간 표기. 원본에 실제 시각이 있으면 또렷하게, 어림한 것은 흐리게. */
.tm.exact { color:var(--ok); border:1px solid #24463a; font-variant-numeric:tabular-nums; }
.tm.approx { color:var(--mute); border:1px solid var(--line); font-variant-numeric:tabular-nums; }
/* 발표가 이미 나온 것. 예정 시각보다 '나왔다'가 더 중요한 소식이라 이걸 덮어쓴다. */
.tm.ok { color:#0b1116; background:var(--ok); border:1px solid var(--ok);
         font-variant-numeric:tabular-nums; }
/* 칩의 시총 표기 */
.chip .cc {
  flex:0 0 auto; font-size:13px; color:#8fb8dc; font-variant-numeric:tabular-nums;
}
.chip:hover { border-color:var(--a2); background:#16202b; }
.chip.big { background:#1d1418; border-color:#43242c; }
.chip.big:hover { border-color:var(--a1); }
.chip .cd {
  font-size:15px; font-weight:700; color:#8fb8dc; font-variant-numeric:tabular-nums;
  flex:0 0 auto;
}
.chip.big .cd { color:var(--a1); }
.chip .cn { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.chip .cq {
  flex:0 0 auto; font-size:14px; color:var(--mute); border:1px solid var(--line);
  border-radius:4px; padding:1px 5px;
}
.chip .st { flex:0 0 auto; font-size:16px; color:#3d4852; }
.chip .st.on { color:var(--a3); }
.chip.watch { border-color:var(--a3); }

/* 이미 실적이 나온 종목. 눌러보면 숫자가 있다는 뜻이라 눈에 띄어야 한다. */
.chip.done { border-color:#27503c; }
/* 주말에 나온 발표. 칸은 월요일에 얹었지만 실제 요일을 밝힌다 — 버크셔는
   원래 토요일 아침에 발표한다. 없는 일로 만들면 안 된다. */
.chip .we {
  flex:0 0 auto; font-size:13px; font-weight:800; border-radius:4px;
  padding:1px 5px; color:#f0b45a; border:1px solid #4a3a1c; white-space:nowrap;
}
.donetag {
  display:inline-block; background:#173026; border:1px solid #27503c; color:var(--ok);
  border-radius:5px; padding:1px 8px; font-size:15px; font-weight:700; margin-left:6px;
}
.dim { color:var(--mute); }
.dim em { font-style:normal; font-weight:700; margin-left:4px; }
.dim em.up { color:var(--ok); }
.dim em.dn { color:var(--a1); }
.more {
  width:100%; background:transparent; border:1px dashed var(--line);
  color:var(--mute); border-radius:7px; padding:7px; font-size:16px;
  cursor:pointer; font-family:inherit; margin-top:2px;
}
.more:hover { border-color:var(--a3); color:var(--a3); }

/* ── 표 ────────────────────────────────────────────────────── */
.scroll {
  max-height:640px; overflow:auto;
  border:1px solid var(--line); border-radius:10px;
}
table { border-collapse:separate; border-spacing:0; width:100%;
        font-variant-numeric:tabular-nums; }
thead th {
  position:sticky; top:0; z-index:2;
  background:#1b232b; color:#dce7ef; font-size:19px; font-weight:700;
  text-align:left; padding:13px 14px; white-space:nowrap;
  border-bottom:2px solid var(--line); cursor:pointer; user-select:none;
}
thead th:hover { color:var(--a3); }
thead th .ar { opacity:.35; font-size:15px; margin-left:5px; }
thead th.asc .ar, thead th.desc .ar { opacity:1; color:var(--a3); }
thead th.nos { cursor:default; }
thead th.nos:hover { color:#dce7ef; }
tbody td {
  padding:11px 14px; text-align:left; white-space:nowrap;
  border-bottom:1px solid #1c252d; font-size:19px;
}
tbody tr { background:#0f1419; }
tbody tr:nth-child(even) { background:#12191f; }
tbody tr:hover { background:#1d2833; }
td.code { color:#8fb8dc; font-weight:700; }
td.code.big { color:var(--a1); }
td.jp { color:var(--mute); font-size:17px; }
td.dim { color:var(--mute); font-size:18px; }
/* 기계 변환한 한글 표기는 점선을 깔아 구분한다. 마우스를 올리면 원문이 뜬다. */
.guess { border-bottom:1px dotted #3c4750; }
.sbtn { background:none; border:0; cursor:pointer; font-size:19px; color:#3d4852;
        padding:0 4px; font-family:inherit; }
.sbtn.on { color:var(--a3); }
.qtag {
  font-size:15px; border:1px solid var(--line); border-radius:4px;
  padding:1px 6px; color:var(--mute);
}
.qtag.q4 { color:var(--a3); border-color:#4a3a1c; }

/* ── 실적 시계열 ───────────────────────────────────────────── */
#mdFin { margin-top:18px; }
.finnote { color:var(--mute); font-size:17px; margin:10px 0 0; }
.finwrap { border-top:1px solid var(--line); padding-top:14px; }
.finhead { font-size:18px; font-weight:700; margin-bottom:8px; }
.finhead .warn { color:var(--a3); font-weight:400; font-size:16px; margin-left:10px; }
.finhead.sub { margin-top:14px; }
.finhead .dim { font-weight:400; font-size:16px; color:var(--mute); margin-left:8px; }
/* 어디까지 발표된 건지. X축 맨 오른쪽 눈금과 같은 값이다. */
.finhead .now {
  margin-left:10px; font-size:15px; font-weight:700; color:var(--a2);
  border:1px solid #2b4a63; background:#12212c; border-radius:5px; padding:1px 9px;
}
/* 분기가 스무 개 넘으면 900px 로는 숫자가 겹친다. 그림을 제 폭대로 그리고
   좁으면 이 칸 안에서만 옆으로 밀리게 한다 — 페이지 전체가 밀리면 안 된다. */
.finbox { overflow-x:auto; overflow-y:hidden; margin-bottom:6px; }
.finsvg { height:auto; display:block; min-width:880px; }
.finsvg .fb { fill:#5B9BD5; }
.finsvg .fl { fill:none; stroke-width:2.2; }
.finsvg .fl.opm { stroke:#ED7D31; }
.finsvg .fl.yoy { stroke:#5B9BD5; }
.finsvg .dot { stroke:#0f1419; stroke-width:1.2; }
.finsvg .dot.opm { fill:#ED7D31; }
.finsvg .dot.yoy { fill:#5B9BD5; }
.finsvg .fz { stroke:#28323c; }
.finsvg .fzero { stroke:#4a5661; }
.finsvg .fx { fill:var(--mute); font-size:12px; }
.finsvg .fx.opm { fill:#c98a4e; }
/* 맨 오른쪽 = 가장 최근 발표 분기. 어디까지 나온 건지 한눈에 보이게 표시한다. */
.finsvg .fx.now { fill:var(--fg); font-weight:800; }
/* 막대와 점에 붙는 숫자. 이게 이 그림의 요점이다 — 모양만 보고 값을 짐작하게
   두지 않는다. 색은 각 계열과 맞춘다. */
/* 막대와 점에 붙는 숫자. 서로 겹쳐도 읽히도록 바탕색 테두리를 두른다 —
   테두리를 글자 아래에 깔아야(paint-order) 획이 굵어 보이지 않는다. */
.finsvg .vn {
  font-size:12px; font-weight:700; font-variant-numeric:tabular-nums;
  stroke:var(--panel); stroke-width:3.5px; stroke-linejoin:round;
  paint-order:stroke fill;
}
.finsvg .vn.rev { fill:#dce9f5; }
/* 쌓은 막대 조각 안에 적는 숫자. 조각 색이 다 다르니 흰 글씨에 어두운 테두리. */
.finsvg .vn.seg { fill:#ffffff; stroke:rgba(0,0,0,.55); stroke-width:2.5px; font-size:11px; }
.finsvg .vn.opm { fill:#ED7D31; }
.finsvg .vn.yoy { fill:#8fc0ea; }
.finlegend { display:flex; gap:14px; flex-wrap:wrap; font-size:15px; color:var(--mute);
             align-items:center;
             margin-top:6px; align-items:center; }
.finlegend .lg::before { content:'■'; margin-right:4px; color:var(--c, inherit); }
.finlegend .rev::before { color:#5B9BD5; }
.finlegend .opm::before { color:#ED7D31; }
.finlegend .yoy::before { color:#5B9BD5; }
.finlegend .src { margin-left:auto; font-size:14px; }
.epsrow { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.epsbox { background:#141c24; border:1px solid var(--line); border-radius:7px;
          padding:7px 11px; font-size:15px; }
.epsbox b { display:block; color:var(--mute); font-size:13px; font-weight:600; }
.epsbox i { color:var(--mute); font-style:normal; }
.epsbox em { font-style:normal; margin-left:5px; font-weight:700; }
.epsbox em.up { color:var(--ok); }
.epsbox em.dn { color:var(--a1); }
.epsbox.next { border-style:dashed; color:var(--mute); }
/* 표의 시장 칸 — 왼쪽 띠로 캘린더 칩과 같은 색을 쓴다 */
.mcell { white-space:nowrap; box-shadow:inset 3px 0 0 0 var(--mk,transparent); }

/* ── 주목종목 그룹 ─────────────────────────────────────────── */
.groups { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr));
          gap:16px; margin-top:14px; }
.gbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px 10px; min-width:0;
        border-top:3px solid var(--mk,var(--line)); }
/* 전체 보기에서 시장별 묶음 머리 */
.gmkt { font-size:22px; font-weight:800; margin:26px 0 2px;
        padding-left:12px; border-left:5px solid var(--mk,var(--a2)); }
.gbox h3 { font-size:20px; margin:2px 0 10px; font-weight:700; }
.gbox h3 .gn { color:var(--mute); font-size:17px; font-weight:400; margin-left:8px; }
.grow { display:flex; align-items:center; gap:9px; padding:6px 2px;
        border-bottom:1px solid #1a222a; font-size:18px; }
.grow:last-child { border-bottom:0; }
.grow .gc { color:#8fb8dc; font-weight:700; font-size:16px; flex:0 0 46px; }
.grow .gk { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.grow .ge { color:var(--mute); font-size:15px; }
.grow .gd { flex:0 0 auto; color:var(--a3); font-size:17px; font-weight:700; }
.grow .gd.past { color:var(--mute); font-weight:400; }

/* ── 발표 건수 차트 ────────────────────────────────────────── */
.chartbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
            padding:14px 12px 8px; margin-top:14px; overflow-x:auto; }
svg.bars { display:block; width:100%; height:auto; min-width:900px; }
svg.bars text { font-family:inherit; fill:var(--mute); font-size:11px; }
svg.bars .vl { fill:var(--fg); font-size:11px; font-weight:700; }
svg.bars rect.b { fill:var(--a2); }
svg.bars rect.b.wk { fill:var(--a1); }
svg.bars rect.b:hover { fill:var(--a3); }

/* ── 상세 모달 ─────────────────────────────────────────────── */
.mdback { position:fixed; inset:0; background:rgba(6,10,14,.82); z-index:50;
          display:flex; align-items:center; justify-content:center; padding:24px; }
.mdback[hidden] { display:none; }
.md { background:var(--panel); border:1px solid var(--line); border-radius:12px;
      max-width:1000px; width:100%; padding:22px 24px;
      max-height:88vh; overflow-y:auto; }
.md .mt { font-size:26px; font-weight:800; margin:0 0 4px; }
.md .ms { color:var(--mute); font-size:18px; margin:0 0 14px; }
.md dl { display:grid; grid-template-columns:auto 1fr; gap:8px 16px; margin:0 0 18px;
         font-size:19px; }
.md dt { color:var(--mute); }
.md dd { margin:0; }
.md .mact { margin-top:18px; padding-top:14px; border-top:1px solid var(--line); display:flex; gap:10px; flex-wrap:wrap; }

.foot { color:var(--mute); font-size:17px; margin-top:56px; line-height:1.7;
        border-top:1px solid var(--line); padding-top:18px; }
.foot a { color:var(--a2); }
</style>
</head>
<body>
<div class="wrap">

<div class="topline">__HEAD__</div>
<h1><span class="mark" aria-hidden="true"></span>Earning <span class="jp">Samurai</span>
    <span class="byline">by CB</span></h1>
<p class="sub">미국 · 일본 · 홍콩 주간 실적발표 일정 — 누가 언제 발표하는지, 관심종목은 알림까지</p>

<div class="mtabs" id="mtabs"></div>
<div class="cards" id="cards"></div>

<h2><span class="n">1</span>관심종목 알림</h2>
<div id="alertbar" class="alertbar none"></div>
<div class="tools">
  <button class="btn pri" id="icsWatch">📅 관심종목 일정 내보내기 (.ics)</button>
  <button class="btn" id="icsWeek">이번 주 전체 .ics</button>
  <button class="btn" id="clearWatch">관심종목 비우기</button>
  <span class="count">★ 를 눌러 담으면 브라우저에 저장됩니다</span>
</div>

<h2><span class="n">2</span>주간 캘린더 <span class="meta" id="calMeta"></span></h2>
<div class="weeknav">
  <button class="btn" id="wPrev">← 이전 주</button>
  <div>
    <div class="wlabel" id="wLabel">—</div>
    <div class="wsum" id="wSum"></div>
  </div>
  <button class="btn" id="wNext">다음 주 →</button>
  <span class="spacer"></span>
  <button class="btn" id="wToday">오늘 주</button>
  <select id="wPick"></select>
  <span class="mpick" id="calMkts" title="나라를 켜고 끕니다. 맨 위 탭과 같이 움직입니다."></span>
  <select id="fCap" title="시가총액으로 거릅니다. 캘린더와 표에 함께 적용됩니다."></select>
  <label class="chk"><input type="checkbox" id="kstToggle" checked>한국 시간</label>
  <label class="chk"><input type="checkbox" id="onlyWatch">관심종목만</label>
  <label class="chk"><input type="checkbox" id="jpToggle">원문 보기</label>
</div>
<div class="capnote" id="capNote" hidden></div>
<div class="cal" id="cal"></div>

<h2><span class="n">3</span>테마별 관심 종목 <span class="meta" id="gMeta"></span></h2>
<div class="note">
  여기는 <b>직접 골라 넣은 목록</b>이라 빠진 회사가 있습니다. 캘린더와 표의 순서·필터는
  이 목록이 아니라 <b>시가총액</b>을 기준으로 하니, 큰 회사를 빠짐없이 보시려면
  위의 <b>규모 필터</b>를 쓰세요. 이 칸은 테마별로 묶어보고 싶을 때만 참고용입니다.
</div>
<div id="groups"></div>

<h2><span class="n">4</span>일자별 발표 건수 <span class="meta">막대를 누르면 그 주로 이동</span></h2>
<div class="chartbox"><svg class="bars" id="bars" viewBox="0 0 1400 260"
     preserveAspectRatio="xMinYMid meet"></svg></div>

<h2><span class="n">5</span>전체 종목 표</h2>
<div class="tools">
  <input type="search" id="q" placeholder="한글·원문·영문·코드 검색 — 엔비디아 / NVDA / 소니 / ソニー / 텐센트 / 00700" autocomplete="off">
  <select id="fSector"><option value="">전체 업종</option></select>
  <select id="fMarket"><option value="">전체 거래소</option></select>
  <select id="fKind"><option value="">전체 분기</option></select>
  <label class="chk"><input type="checkbox" id="tBig">주목종목만</label>
  <label class="chk"><input type="checkbox" id="tWatch">관심종목만</label>
  <label class="chk"><input type="checkbox" id="tFuture">오늘 이후만</label>
  <span class="count" id="tCnt"></span>
</div>
<div class="scroll">
  <table id="tAll">
    <thead><tr>
      <th class="nos" style="width:44px">★</th>
      <th data-k="0">발표일<span class="ar">▾</span></th>
      <th data-k="9">시장<span class="ar">▾</span></th>
      <th data-k="1">코드<span class="ar">▾</span></th>
      <th data-k="2">회사명<span class="ar">▾</span></th>
      <th data-k="7">원문<span class="ar">▾</span></th>
      <th data-k="10">시각<span class="ar">▾</span></th>
      <th data-k="4">분기<span class="ar">▾</span></th>
      <th data-k="3">결산기<span class="ar">▾</span></th>
      <th data-k="5">업종<span class="ar">▾</span></th>
      <th data-k="6">거래소<span class="ar">▾</span></th>
    </tr></thead>
    <tbody id="tBody"></tbody>
  </table>
</div>

<div class="foot">
  <span id="srcLink"></span>
  __TLNOTE__ 일본 지명·인명 한자는 훈독이라(小田原=오다와라) 기계 변환이 틀릴 수 있습니다.
  점선이 그어진 이름이 기계 변환분이고, 마우스를 올리면 원문이 뜹니다.
  캘린더의 <b>원문 보기</b> 체크로 통째로 바꿔 볼 수도 있습니다.<br>
  발표일은 예정일이며 회사 사정으로 바뀔 수 있습니다.
  발표 시각은 <b>미국만</b> 원본에 있습니다(장전 BMO / 장후 AMC).
  일본은 대부분 장 마감 후 15시 전후, 홍콩은 이사회 당일 장 마감 후 공시입니다.<br>
  날짜는 각 시장의 <b>현지 날짜</b>입니다. 미국 장후 발표는 한국 시각으로 다음 날 새벽이 됩니다.<br>
  🇭🇰 홍콩만 성격이 다릅니다. 미국·일본은 회사가 미리 신고한 <b>발표 예정일</b>이지만,
  홍콩은 그 제도가 약하고 거래소가 내던 이사회 캘린더도 없어져서
  <b>이미 공시된 실적</b>을 모읍니다. 즉 홍콩 탭에는 앞으로의 예정이 아니라
  지나간 발표가 실립니다.<br>
  <span id="gapNote"></span>
  관심종목은 이 브라우저에만 저장되며 서버로 전송되지 않습니다.
</div>
</div>

<div class="mdback" id="mdBack" hidden>
  <div class="md" role="dialog" aria-modal="true">
    <p class="mt" id="mdTitle"></p>
    <p class="ms" id="mdSub"></p>
    <dl id="mdList"></dl>
    <div id="mdFin"></div>
    <div class="mact">
      <button class="btn pri" id="mdStar">★ 관심종목</button>
      <a class="btn" id="mdLink1" target="_blank" rel="noopener">종목정보</a>
      <a class="btn" id="mdLink2" target="_blank" rel="noopener">공시</a>
      <button class="btn" id="mdClose">닫기 (ESC)</button>
    </div>
  </div>
</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
/* ══════════════════════════════════════════════════════════════
   글로벌 실적발표 캘린더 — 렌더링
   행은 배열로 들어온다. 자리 뜻:
     0 날짜  1 코드  2 한글명  3 결산기  4 분기  5 업종  6 거래소
     7 원문  8 변환등급  9 시장(jp/us/hk)  10 발표시각  11 시총(십억$)
   종목 하나를 가리키는 열쇠는 코드가 아니라 '시장:코드' 다.
   일본 8035 와 홍콩 08035 는 다른 회사다.
   ══════════════════════════════════════════════════════════════ */
const D = JSON.parse(document.getElementById('payload').textContent);
const ROWS = D.rows, NOTE = D.notable;
const MKTS = D.markets, MKT = Object.fromEntries(MKTS.map(m => [m.id, m]));
const LIVE = MKTS.filter(m => m.has).map(m => m.id);
const DOW = ['월','화','수','목','금','토','일'];
const LS_KEY = 'jpEarnWatch';

const keyOf = r => r[9] + ':' + r[1];
const noteOf = r => NOTE[keyOf(r)];

/* ── 한국 시간 ────────────────────────────────────────────────
   r[0]=현지 날짜, r[12]=한국 날짜, r[13]='HH:MM', r[14]=1이면 원본의 실제 시각.
   현지 날짜로 칸을 나누면 한국에서 볼 때 어긋난다 — 미국 장후 발표는
   한국 시각으로 다음 날 새벽이라, 현지 기준 '오늘'이 실제로는 내일이다.
   그래서 기본을 한국 시간으로 두고, 현지 시간으로 되돌리는 토글을 준다. */
let useKst = true;
const dateOf = r => (useKst && r[12]) ? r[12] : r[0];
const timeOf = r => (useKst && r[13]) ? r[13] + (r[14] ? '' : '경') : '';

/* 캘린더에서 이 발표가 놓일 칸.
   주말은 칸을 내주지 않는다 — 한 주에 열네 건 남짓 있을 뿐인데 칸 두 개가
   늘 비어 있어 화면만 넓어진다. 대신 **다음 월요일 칸**에 얹고 칩에
   「토」「일」을 붙여 실제로는 주말에 나온 것임을 밝힌다.
   (버크셔는 원래 토요일 아침에 발표한다. 없는 일로 만들면 안 된다.) */
const DOW_KO = ['월', '화', '수', '목', '금', '토', '일'];
function dowOf(d) { return (parse(d).getDay() + 6) % 7; }
function slotOf(r) {
  const d = dateOf(r), w = dowOf(d);
  return w < 5 ? d : addDays(d, 7 - w);
}

/* 보고 있는 시장. 여러 개를 동시에 켤 수 있다 — '미국+홍콩만' 같은 조합이 되도록.
   mkt 는 탭 하나만 켠 상태를 가리키는 값으로 남겨둔다(설명문·안내문이 이걸 본다). */
let picked = new Set(LIVE);
let mkt = '';                       // 딱 한 시장만 켜져 있으면 그 id, 아니면 ''
function syncMkt() {
  const on = [...picked];
  mkt = on.length === 1 ? on[0] : '';
}
/* 지금 켜 둔 시장들. 휴장·미수집·주목종목·막대가 전부 이걸 봐야 한다.
   'mkt 아니면 전부'로 두면 미국+홍콩만 켠 상태에서 일본 휴장이 그대로 뜬다 —
   보지도 않는 시장 때문에 "휴장"이라고 적히는 셈이라 거짓말이 된다. */
function onMkts() { return MKTS.map(m => m.id).filter(id => picked.has(id)); }

/* 지금 시장에 해당하는 행만. 시장을 바꿀 때마다 다시 만든다. */
let VIEW = [], byDate = new Map();
function reslice() {
  syncMkt();
  VIEW = picked.size === LIVE.length ? ROWS : ROWS.filter(r => picked.has(r[9]));
  byDate = new Map();
  for (const r of VIEW) {
    const d = slotOf(r);
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(r);
  }
  // 칸 안에서는 시총 큰 순. 접혀서 12개만 보일 때 큰 게 먼저 오게 한다.
  // 시총을 모르는 종목은 뒤로 민다 — 0으로 쳐서 섞으면 큰 회사가 밀린다.
  for (const list of byDate.values())
    list.sort((a, b) => (b[11] || -1) - (a[11] || -1) ||
                        (a[13] || '').localeCompare(b[13] || '') ||
                        a[1].localeCompare(b[1]));
}

/* 수집에 성공한 날 — 시장별로 따로 본다. 미국은 받았는데 일본은 못 받은 날이
   그냥 '발표 없음'으로 보이면 안 된다. */
const okSet = {};
for (const m of LIVE) okSet[m] = new Set(D.okDays[m] || []);
/* 그 날 아직 못 받은 시장들. 비어 있으면 구멍이 없다는 뜻. */
function missing(d) {
  return onMkts().filter(m => okSet[m] && !okSet[m].has(d));
}
/* 그 날의 휴장 사정. all=true 면 보고 있는 시장이 전부 쉰다.
   전체 보기에서 일본만 쉬는 날을 '휴장'이라 적으면 거짓말이 된다 —
   미국은 그날 멀쩡히 연다. 그래서 누가 쉬는지를 같이 적는다. */
function holidayInfo(d) {
  const ms = onMkts();
  const hit = ms.filter(m => D.holidays[m] && D.holidays[m][d]);
  if (!hit.length) return { text: '', all: false };
  const label = [...new Set(hit.map(m => D.holidays[m][d]))].join(' · ');
  if (hit.length === ms.length) return { text: label + ' · 휴장', all: true };
  return { text: hit.map(m => MKT[m].ko).join('·') + ' 휴장 (' + label + ')', all: false };
}

/* 원문 사명과 사전 영문명이 같은 경우가 흔하다. 같은 이름을 두 번 쓰지 않는다. */
function altOf(r) {
  const nt = NOTE[keyOf(r)];
  return [...new Set([r[7], nt ? nt[1] : ''])].filter(s => s && s !== r[2]);
}

/* 관심종목 — localStorage. 사파리 프라이빗 모드처럼 쓰기가 막힌 환경에서도
   페이지 전체가 죽지는 않게 감싼다. */
let watch = new Set();
try { watch = new Set(JSON.parse(localStorage.getItem(LS_KEY) || '[]')); } catch (e) {}
/* 예전에는 일본밖에 없어서 코드만 담았다. 이미 담아둔 것을 잃지 않도록 옮긴다. */
if ([...watch].some(k => !k.includes(':'))) {
  watch = new Set([...watch].map(k => k.includes(':') ? k : 'jp:' + k));
  try { localStorage.setItem(LS_KEY, JSON.stringify([...watch])); } catch (e) {}
}
function saveWatch() {
  try { localStorage.setItem(LS_KEY, JSON.stringify([...watch])); } catch (e) {}
}
function toggleWatch(k) {
  watch.has(k) ? watch.delete(k) : watch.add(k);
  saveWatch(); renderAll();
}

const pad = n => String(n).padStart(2, '0');
const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const parse = s => { const [y,m,d] = s.split('-').map(Number); return new Date(y, m-1, d); };
const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* r[2]=한글명, r[7]=원문, r[8]=변환등급(2 사전·1 단어사전·0 기계).
   '원문 보기'를 켜면 원문을 그대로 보여준다. 미국처럼 원문이 곧 한글명인
   (= 사전에 없어 영문을 그대로 쓴) 종목은 둘이 같으므로 한 번만 보인다. */
let showJp = false;
const nameOf = r => (showJp && r[7]) ? r[7] : r[2];
const bothOf = r => r[2] === r[7] ? r[2] : r[2] + ' · ' + r[7];

let week = D.defaultWeek;

/* ── 주 네비게이션 ────────────────────────────────────────── */
const wPick = document.getElementById('wPick');
/* 주별 건수는 시장을 바꾸면 달라지므로 매번 다시 채운다. */
function fillWeeks() {
  wPick.innerHTML = D.weeks.map(w =>
    '<option value="' + w + '">' + esc(fmtWeek(w)) + '  (' + countWeek(w) + '건)</option>'
  ).join('');
}
/* 한 주는 월~금 다섯 칸이다. 주말 발표는 slotOf 가 다음 월요일로 옮겨 놓았다. */
function weekDays(w) {
  const out = [];
  for (let i = 0; i < 5; i++) out.push(addDays(w, i));
  return out;
}
function countWeek(w) {
  return weekDays(w).reduce((s, d) => s + (byDate.get(d) || []).length, 0);
}
function fmtWeek(w) {
  const a = parse(w), b = parse(addDays(w, 4));
  return (a.getMonth()+1) + '/' + a.getDate() + ' ~ ' + (b.getMonth()+1) + '/' + b.getDate()
       + ' (' + a.getFullYear() + ')';
}
document.getElementById('wPrev').onclick = () => go(addDays(week, -7));
document.getElementById('wNext').onclick = () => go(addDays(week, 7));
document.getElementById('wToday').onclick = () => go(D.defaultWeek);
wPick.onchange = e => go(e.target.value);
function go(w) { week = w; renderAll(); }

/* ── 주간 캘린더 ──────────────────────────────────────────── */
const CHIP_LIMIT = 12;
const expanded = new Set();
/* 이 이상이면 칸에서 크게 띄운다. 10조원 — 수기 목록이 아니라 시총이 기준이다. */
const BIG_CAP = 10e12 / D.usdKrw / 1e9;

/* 시총은 십억 달러 단위로 들어온다. 한국식으로 억/조 달러로 고쳐 읽는다.
   1 십억 달러 = 10억 달러, 1000 십억 달러 = 1조 달러. */
function capKo(b) {
  if (!b) return '';
  const won = b * 1e9 * D.usdKrw;                      // 원으로도 같이 적는다
  const wonKo = won >= 1e12 ? (won / 1e12).toFixed(1) + '조원'
                            : Math.round(won / 1e8).toLocaleString() + '억원';
  const usd = b >= 1000 ? (b / 1000).toFixed(2) + '조 달러'
                        : Math.round(b * 10).toLocaleString() + '억 달러';
  return usd + ' (약 ' + wonKo + ')';
}

/* ── 규모 필터 ────────────────────────────────────────────────
   시총은 미국 소스에만 온다. 일본·홍콩은 원본에 없어서 거를 수가 없다.
   그 행들을 조용히 지워버리면 두 시장이 통째로 사라지므로 통과시키되,
   어느 시장에 적용되지 않는지 화면에 적는다. 없는 값을 지어내지 않는다. */
const capSel = document.getElementById('fCap');
capSel.innerHTML = '<option value="0">전체 규모</option>' +
  D.capSteps.map(s => '<option value="' + s.usdB + '">시총 ' + s.jo + '조원 이상</option>').join('');
const capMin = () => +capSel.value || 0;

/* 시총을 모르는 행을 어떻게 할 것인가 — 여기서 한 번 크게 뒤집혔다.
   전부 통과시키면 규모 필터를 켜도 껍데기 회사가 그대로 남는다. 그렇다고 전부
   지우면 회사가 조용히 사라진다 — 실제로 히로세전기(시총 8,828억엔)가 그렇게
   빠질 뻔했다.
   가르는 기준은 **시총이 어디서 왔는가**다. 미국은 나스닥이 캘린더와 함께 시총을
   주므로, 비어 있으면 정말 값이 없는 종목이다(SPAC·껍데기). 일본·홍콩은 따로
   받아 붙이는 거라 비어 있으면 '아직 못 받았다'는 뜻이다.
   수집률로 가르면 안 된다 — 일본 97.6%·홍콩 99.2%, 미국 85.7% 이라 거꾸로 된다. */
const CAP_INLINE = new Set(D.capInline || []);
const passCap = r => !capMin() ||
      (r[11] ? r[11] >= capMin() : !CAP_INLINE.has(r[9]));

/* 발표 시각 배지. 미국만 값이 있다. */
function timeTag(r) {
  if (!r[10]) return '';
  return '<span class="tm ' + (r[10] === '장전' ? 'pre' : 'post') + '">' + r[10] + '</span>';
}

/* ── 발표가 이미 나왔는가 ─────────────────────────────────────
   날짜로 어림잡지 않는다. 장후 발표는 예정일 저녁에야 나오므로 '날짜가 지났으니
   발표했겠지'로 치면 반나절을 틀린다. 대신 나스닥이 준 **실제 EPS 가 찍힌 분기**와
   그 행의 결산기를 맞춰본다. 숫자가 나왔으면 발표된 것이다.
   자료가 있는 건 미국뿐이라 일본·홍콩에는 배지를 달지 않는다 — 모르는 걸 안다고
   적지 않는다. (홍콩은 애초에 '이미 나온 공시'만 모으므로 전부 지나간 발표다.) */
const MON = { jan:1, feb:2, mar:3, apr:4, may:5, jun:6,
              jul:7, aug:8, sep:9, oct:10, nov:11, dec:12 };
/* '2026년 6월 분기' -> '2026-6' */
function fyKey(fy) {
  const m = /(\d{4})\D+(\d{1,2})\s*월/.exec(fy || '');
  return m ? m[1] + '-' + (+m[2]) : '';
}
/* 'Jun 2026' -> '2Q26'. 회원님이 보는 표기법으로 맞춘다 — 차트 축과 같은 말이어야
   "이 분기가 저 막대구나"가 바로 보인다. 나스닥이 주는 'Jun 2026' 은 분기말 달이라
   그 달이 속한 분기로 읽으면 된다. */
function epsQ(p) {
  const m = /([A-Za-z]{3})\D*(\d{4})/.exec(p || '');
  const n = m && MON[m[1].toLowerCase()];
  if (!n) return p || '';
  return Math.floor((n - 1) / 3 + 1) + 'Q' + String(m[2]).slice(2);
}
/* 'Jun 2026' -> '2026-6' */
function epsKey(p) {
  const m = /([A-Za-z]{3})\D*(\d{4})/.exec(p || '');
  const n = m && MON[m[1].toLowerCase()];
  return n ? m[2] + '-' + n : '';
}
function doneInfo(r) {
  if (r[9] !== 'us') return null;
  const f = D.fin['us:' + r[1]];
  if (!f || !f.eps || !f.eps.done) return null;
  const want = fyKey(r[3]);
  if (!want) return null;
  return f.eps.done.find(x => epsKey(x.period) === want) || null;
}

/* EPS 가 '$1.85' 처럼 기호를 달고 올 때가 있다. 숫자만 뽑는다. */
function num(v) {
  const n = parseFloat(String(v == null ? '' : v).replace(/[^0-9.\-]/g, ''));
  return isFinite(n) ? n : null;
}
/* 예상 대비 몇 %. 예상이 0 이면 나눌 수 없으니 비운다. */
function surprise(a, c) {
  a = num(a); c = num(c);
  if (a === null || !c) return '';
  const p = ((a - c) / Math.abs(c)) * 100;
  return '<em class="' + (p >= 0 ? 'up' : 'dn') + '">' + (p >= 0 ? '▲' : '▼') +
         Math.abs(p).toFixed(0) + '%</em>';
}

function chip(r, big) {
  const k = keyOf(r), on = watch.has(k), t = timeOf(r);
  const dn = doneInfo(r);
  // 발표가 끝났으면 ✓ 를 시각 자리에 넣는다. 칸을 하나 더 만들면 그만큼 회사 이름이
  // 잘려서, 정작 무슨 회사인지 안 보이게 된다.
  const badge = dn
    ? '<span class="tm ok" title="실적이 나왔습니다. 눌러보세요.">✓' + (t ? ' ' + t : '') + '</span>'
    : t ? '<span class="tm ' + (r[14] ? 'exact' : 'approx') + '">' + t + '</span>'
        : timeTag(r);
  // 주말 발표는 다음 월요일 칸에 얹혀 있다. 실제 요일을 칩에 적는다.
  const wd = dowOf(dateOf(r));
  const we = wd >= 5
    ? '<span class="we" title="' + dateOf(r) + ' (' + DOW_KO[wd] + ') 발표">' +
      DOW_KO[wd] + '</span>' : '';
  return '<button class="chip m-' + r[9] + (big ? ' big' : '') + (on ? ' watch' : '') +
         (dn ? ' done' : '') +
         '" data-key="' + esc(k) + '" data-date="' + dateOf(r) + '">' +
         (mkt ? '' : '<span class="fl">' + MKT[r[9]].flag + '</span>') + we +
         '<span class="cd">' + esc(r[1]) + '</span>' +
         '<span class="cn' + (r[8] === 0 ? ' guess' : '') + '" title="' + esc(bothOf(r)) +
         '">' + esc(nameOf(r)) + '</span>' + badge +
         (r[11] ? '<span class="cc">' + capShort(r[11]) + '</span>' : '') +
         '<span class="st' + (on ? ' on' : '') + '">' + (on ? '★' : '☆') + '</span>' +
         '</button>';
}

/* 칩에 넣을 짧은 시총 표기. 조원 단위로만 적는다. */
function capShort(b) {
  const jo = b * 1e9 * D.usdKrw / 1e12;
  return jo >= 100 ? Math.round(jo) + '조' : jo.toFixed(jo < 10 ? 1 : 0) + '조';
}

function renderCal() {
  const onlyWatch = document.getElementById('onlyWatch').checked;

  /* 아직 한 번도 수집하지 않은 시장. 빈 칸 일곱 개를 늘어놓으면 '발표가 없는 주'로
     읽힌다. 그건 사실이 아니므로 칸 대신 사유를 낸다. */
  if (mkt && !MKT[mkt].has) {
    const cal = document.getElementById('cal');
    cal.className = 'cal none';
    cal.innerHTML = '<div class="nodata">' + MKT[mkt].flag + ' ' + esc(MKT[mkt].ko) +
      ' 일정은 아직 수집하지 않았습니다.' +
      '<span>저장소에서 <b>python ' + esc(MKT[mkt].scraper) + '</b> 을 돌린 뒤 ' +
      '<b>python build.py</b> 로 다시 만들면 이 자리에 채워집니다.</span></div>';
    document.getElementById('wLabel').textContent = fmtWeek(week);
    document.getElementById('wSum').textContent = '미수집';
    wPick.value = D.weeks.includes(week) ? week : '';
    document.getElementById('wPrev').disabled = false;
    document.getElementById('wNext').disabled = false;
    return;
  }

  const shown = weekDays(week);
  const cal = document.getElementById('cal');
  cal.className = 'cal';

  let total = 0, bigTotal = 0, watchTotal = 0;
  cal.innerHTML = shown.map(d => {
    // 규모 필터를 제일 먼저 건다. 칸 위의 건수도 걸러진 뒤 숫자여야
    // '이 날 몇 건 보이는지'와 맞는다.
    let list = (byDate.get(d) || []).filter(passCap);
    total += list.length;
    watchTotal += list.filter(r => watch.has(keyOf(r))).length;
    if (onlyWatch) list = list.filter(r => watch.has(keyOf(r)));

    const dow = dowOf(d);
    const isToday = d === D.today;
    const hol = holidayInfo(d);
    const closed = hol.all && !list.length;

    // 시총 큰 순으로 이미 정렬돼 있다. 앞에서부터 자르면 큰 회사가 남는다.
    const key = week + d;
    const open = expanded.has(key);
    const shownList = open ? list : list.slice(0, CHIP_LIMIT);

    let body;
    if (!list.length) {
      let why = '';
      const miss = missing(d);
      if (hol.text) why = '<span class="why">' + esc(hol.text) + '</span>';
      else if (miss.length) why = '<span class="why">미수집 구간 · ' +
        miss.map(m => MKT[m].ko).join('·') + '</span>';
      body = '<div class="empty">발표 없음' + why + '</div>';
    } else {
      const miss = missing(d);
      // 시총 상위 몇 개는 크게, 나머지는 보통 크기로. 기준은 수기 목록이 아니라 시총이다.
      body = (hol.text ? '<div class="dsec gap">' + esc(hol.text) + '</div>' : '') +
             shownList.map((r, i) => chip(r, i < 3 && r[11] >= BIG_CAP)).join('') +
             (list.length > CHIP_LIMIT
               ? '<button class="more" data-key="' + key + '">' +
                 (open ? '접기' : '+' + (list.length - CHIP_LIMIT) + '개 더 보기') + '</button>'
               : '') +
             /* 한 시장은 받았고 다른 시장은 못 받은 날. 목록이 차 있어도
                다 받은 날처럼 보이면 안 된다. */
             (miss.length ? '<div class="dsec gap">' +
                miss.map(m => MKT[m].ko).join('·') + ' 미수집</div>' : '');
    }

    return '<div class="day' + (isToday ? ' today' : '') + (closed ? ' closed' : '') + '">' +
      '<div class="dh"><span class="dow">' + DOW[dow] + '</span>' +
      '<span class="dnum">' + parse(d).getDate() + '</span>' +
      (isToday ? '<span class="todaytag">오늘</span>' : '') +
      '<span class="dcnt"><b>' + list.length + '</b>건</span></div>' +
      '<div class="dbody">' + body + '</div></div>';
  }).join('');

  document.getElementById('wLabel').textContent = fmtWeek(week);
  document.getElementById('wSum').textContent =
    total.toLocaleString() + '건' + (useKst ? ' · 한국 시간' : ' · 현지 시간') +
    (watch.size ? ' · 관심 ' + watchTotal + '건' : '');
  wPick.value = D.weeks.includes(week) ? week : '';

  const wi = D.weeks.indexOf(week);
  document.getElementById('wPrev').disabled = wi === 0;
  document.getElementById('wNext').disabled = wi === D.weeks.length - 1;
}

/* ── 알림 배너 ────────────────────────────────────────────── */
function renderAlert() {
  const el = document.getElementById('alertbar');
  if (!watch.size) {
    el.className = 'alertbar none';
    el.innerHTML = '<div class="ah">관심종목이 비어 있습니다</div>' +
      '<div class="hint">아래 캘린더나 표에서 ☆ 를 누르면 여기에 모이고, ' +
      '발표일이 다가오면 D-day로 알려줍니다. .ics로 내보내 구글·아웃룩 캘린더에 넣으면 ' +
      '실제 알림도 받을 수 있습니다.</div>';
    return;
  }
  // 오늘 이후 예정만, 가까운 순으로. 알림은 시장 탭과 무관하게 전부 보여준다 —
  // 관심종목은 시장을 가려 담는 게 아니다.
  const up = ROWS.filter(r => watch.has(keyOf(r)) && r[0] >= D.today)
                 .sort((a, b) => a[0] < b[0] ? -1 : 1);
  const inWeek = up.filter(r => weekDays(week).includes(slotOf(r)));
  el.className = 'alertbar' + (up.length ? '' : ' none');
  const ddays = up.slice(0, 10).map(r => {
    const dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
    const tag = dd === 0 ? '오늘' : 'D-' + dd;
    return '<button class="chip big m-' + r[9] + '" data-key="' + esc(keyOf(r)) +
           '" data-date="' + r[0] + '" style="width:auto">' +
           '<span class="fl">' + MKT[r[9]].flag + '</span>' +
           '<span class="cd">' + tag + '</span>' +
           '<span class="cn">' + esc(nameOf(r)) + '</span>' + timeTag(r) +
           '<span class="cq">' + r[0].slice(5) + '</span></button>';
  }).join('');

  el.innerHTML =
    '<div class="ah">관심종목 <span class="cnt">' + watch.size + '</span>개 · ' +
    '앞으로 예정 <span class="cnt">' + up.length + '</span>건' +
    (inWeek.length ? ' · 이번 주 <span class="cnt">' + inWeek.length + '</span>건' : '') +
    '</div>' +
    (up.length ? '<div class="arow">' + ddays + '</div>'
               : '<div class="hint">수집 기간 안에 남은 발표 일정이 없습니다.</div>');
}

/* ── 주목종목 그룹 ────────────────────────────────────────── */
function renderGroups() {
  // 종목마다 한 줄만 남긴다. 오늘 이후 일정이 있으면 그 중 가장 이른 것,
  // 없으면 가장 최근 과거 일정. VIEW가 날짜 오름차순이라 한 번만 훑으면 된다.
  const first = new Map();
  for (const r of VIEW) {
    if (!noteOf(r)) continue;
    const k = keyOf(r), cur = first.get(k);
    if (!cur || (cur[0] < D.today && (r[0] >= D.today || r[0] > cur[0]))) first.set(k, r);
  }
  // 시장을 가로질러 보는 중이면 시장별로 묶어서 낸다. 테마 이름이 시장마다
  // 겹치므로(둘 다 '금융'이 있다) 한 통에 부으면 섞여버린다.
  const shownMkts = onMkts();
  let shown = 0, dictTotal = 0, html = '';
  for (const m of shownMkts) {
    const order = D.groupOrder[m] || [];
    const perGroup = {};
    for (const g of order) perGroup[g] = [];
    for (const [k, r] of first) {
      if (r[9] !== m) continue;
      const g = NOTE[k][2];
      if (perGroup[g]) perGroup[g].push(r);
    }
    const boxes = order.filter(g => perGroup[g].length).map(g => {
      const rs = perGroup[g].sort((a, b) => a[0] < b[0] ? -1 : 1);
      shown += rs.length;
      return '<div class="gbox m-' + m + '"><h3>' + esc(g) +
        '<span class="gn">' + rs.length + '종목</span></h3>' +
        rs.map(r => {
          const nt = NOTE[keyOf(r)], past = r[0] < D.today;
          return '<div class="grow"><span class="gc">' + esc(r[1]) + '</span>' +
            '<span class="gk">' + esc(nt[0]) +
            ' <span class="ge">' + esc(nt[1]) + '</span></span>' +
            '<span class="gd' + (past ? ' past' : '') + '">' + r[0].slice(5) +
            ' <span class="qtag">' + esc(r[4]) + '</span></span></div>';
        }).join('') + '</div>';
    }).join('');
    dictTotal += Object.keys(NOTE).filter(k => k.startsWith(m + ':')).length;
    if (!boxes) continue;
    if (!mkt) html += '<h3 class="gmkt m-' + m + '">' + MKT[m].flag + ' ' +
                      esc(MKT[m].ko) + '</h3>';
    html += '<div class="groups">' + boxes + '</div>';
  }
  document.getElementById('groups').innerHTML =
    html || '<div class="empty">수집된 주목종목 일정이 없습니다.</div>';
  document.getElementById('gMeta').textContent =
    shown + '종목 / 사전 등재 ' + dictTotal + '종목';
}

/* ── 일자별 막대 ──────────────────────────────────────────── */
function renderBars() {
  const src = [...new Set(onMkts().flatMap(m => D.okDays[m] || []))].sort();
  const days = src.filter(d => (byDate.get(d) || []).length);
  if (!days.length) { document.getElementById('bars').innerHTML = ''; return; }
  const W = 1400, H = 260, PAD_L = 8, PAD_B = 46, PAD_T = 24;
  const n = days.length || 1;
  const bw = (W - PAD_L * 2) / n;
  const max = Math.max(...days.map(d => byDate.get(d).length), 1);
  const wk = new Set(weekDays(week));
  const parts = days.map((d, i) => {
    const v = byDate.get(d).length;
    const h = (H - PAD_B - PAD_T) * v / max;
    const x = PAD_L + i * bw, y = H - PAD_B - h;
    const label = d.slice(5).replace('-', '/');
    return '<g><rect class="b' + (wk.has(d) ? ' wk' : '') + '" x="' + (x + bw * .12).toFixed(1) +
      '" y="' + y.toFixed(1) + '" width="' + (bw * .76).toFixed(1) + '" height="' +
      Math.max(h, 1).toFixed(1) + '" data-date="' + d + '"><title>' + label + ' · ' +
      v + '건</title></rect>' +
      (v >= max * .45 ? '<text class="vl" x="' + (x + bw / 2).toFixed(1) + '" y="' +
        (y - 5).toFixed(1) + '" text-anchor="middle">' + v + '</text>' : '') +
      '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - PAD_B + 16) +
      '" text-anchor="end" transform="rotate(-60 ' + (x + bw / 2).toFixed(1) + ' ' +
      (H - PAD_B + 16) + ')">' + label + '</text></g>';
  }).join('');
  document.getElementById('bars').innerHTML = parts;
}

/* ── 전체 표 ──────────────────────────────────────────────── */
let sortKey = 0, sortDir = 1;
function renderTable() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const fs = document.getElementById('fSector').value;
  const fm = document.getElementById('fMarket').value;
  const fk = document.getElementById('fKind').value;
  const tb = document.getElementById('tBig').checked;
  const tw = document.getElementById('tWatch').checked;
  const tf = document.getElementById('tFuture').checked;

  let list = VIEW.filter(r => {
    if (!passCap(r)) return false;
    if (fs && r[5] !== fs) return false;
    if (fm && r[6] !== fm) return false;
    if (fk && r[4] !== fk) return false;
    if (tb && !noteOf(r)) return false;
    if (tw && !watch.has(keyOf(r))) return false;
    if (tf && r[0] < D.today) return false;
    if (q) {
      const nt = noteOf(r), en = nt ? nt[1] : '';
      if (!(r[1] + r[2] + r[7] + en).toLowerCase().includes(q)) return false;
    }
    return true;
  });

  list.sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x === y) return a[0] < b[0] ? -1 : 1;
    return (x < y ? -1 : 1) * sortDir;
  });

  const CAP = 600;
  document.getElementById('tBody').innerHTML = list.slice(0, CAP).map(r => {
    const k = keyOf(r), nt = NOTE[k], on = watch.has(k);
    return '<tr class="m-' + r[9] + '" data-key="' + esc(k) + '" data-date="' + r[0] + '">' +
      '<td><button class="sbtn' + (on ? ' on' : '') + '" data-star="' + esc(k) + '">' +
      (on ? '★' : '☆') + '</button></td>' +
      '<td class="dim">' + r[0] + '</td>' +
      '<td class="mcell">' + MKT[r[9]].flag + ' ' + esc(MKT[r[9]].ko) + '</td>' +
      '<td class="code' + (nt ? ' big' : '') + '">' + esc(r[1]) + '</td>' +
      '<td class="' + (r[8] === 0 ? 'guess' : '') + '">' + esc(r[2]) + '</td>' +
      '<td class="jp">' + (r[7] === r[2] ? '' : esc(r[7])) + '</td>' +
      '<td>' + timeTag(r) + '</td>' +
      '<td><span class="qtag' + (r[4] === '본결산' ? ' q4' : '') + '">' + esc(r[4]) + '</span></td>' +
      '<td class="dim">' + esc(r[3]) + '</td>' +
      '<td class="dim">' + esc(r[5]) + '</td>' +
      '<td class="dim">' + esc(r[6]) + '</td></tr>';
  }).join('');
  document.getElementById('tCnt').innerHTML =
    '<b>' + list.length.toLocaleString() + '</b>건' +
    (list.length > CAP ? ' 중 ' + CAP + '건 표시 (검색으로 좁혀보세요)' : '');
}

/* ── 상세 모달 ────────────────────────────────────────────── */
let mdKey = null;

/* 시장마다 볼 곳이 다르다. [종목정보 이름, URL, 공시 이름, URL] */
function links(m, code) {
  if (m === 'jp') return ['닛케이 종목정보', 'https://www.nikkei.com/nkd/company/?scode=' + code,
                          '적시공시', 'https://www.nikkei.com/nkd/company/kigyo/?scode=' + code];
  if (m === 'us') return ['나스닥 종목정보',
                          'https://www.nasdaq.com/market-activity/stocks/' + code.toLowerCase(),
                          'SEC 공시',
                          'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker=' +
                          code + '&type=10-&dateb=&owner=include&count=40'];
  return ['HKEX 종목정보',
          'https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/Equities-Quote?sym=' +
          String(code).replace(/^0+/, '') + '&sc_lang=en',
          '홍콩 공시(HKEXnews)',
          'https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en'];
}

function openModal(k, dt) {
  const r = (byDate.get(dt) || []).find(x => keyOf(x) === k) ||
            ROWS.find(x => keyOf(x) === k);
  if (!r) return;
  mdKey = k;
  const nt = NOTE[k], m = r[9], code = r[1];
  document.getElementById('mdTitle').textContent =
    MKT[m].flag + ' ' + r[2] + ' (' + code + ')';
  document.getElementById('mdSub').textContent =
    altOf(r).concat(r[8] === 0 ? ['한글 표기는 기계 변환'] : []).join(' · ');
  const dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
  const dn = doneInfo(r);
  document.getElementById('mdList').innerHTML =
    '<dt>' + (dn ? '발표일' : '발표 예정일') + '</dt><dd>' +
    r[0] + ' (' + DOW[(parse(r[0]).getDay()+6)%7] + ') ' +
    (dd === 0 ? '· 오늘' : dd > 0 ? '· D-' + dd : '· ' + (-dd) + '일 전') +
    (dn ? ' <span class="donetag">✓ 발표 완료</span>' +
          (dn.actual ? ' <span class="dim">EPS ' + esc(String(dn.actual)) +
                       ' ' + surprise(dn.actual, dn.consensus) + '</span>' : '')
        : '') + '</dd>' +
    (r[10] ? '<dt>발표 시각</dt><dd>' + esc(r[10]) +
             (r[10] === '장전' ? ' (Before Open)' : ' (After Close)') + '</dd>' : '') +
    '<dt>분기</dt><dd>' +
      (dn ? '<b>' + esc(epsQ(dn.period)) + '</b> · ' : '') +
      esc([r[4], r[3]].filter(Boolean).join(' · ') || '—') + '</dd>' +
    (r[11] ? '<dt>시가총액</dt><dd>' + capKo(r[11]) + '</dd>' : '') +
    (r[5] ? '<dt>업종</dt><dd>' + esc(r[5]) + '</dd>' : '') +
    '<dt>시장</dt><dd>' + esc(MKT[m].ko) + (r[6] ? ' · ' + esc(r[6]) : '') + '</dd>' +
    (nt ? '<dt>테마</dt><dd>' + esc(nt[2]) + '</dd>' : '');
  const L = links(m, code);
  const a1 = document.getElementById('mdLink1'), a2 = document.getElementById('mdLink2');
  a1.textContent = L[0]; a1.href = L[1];
  a2.textContent = L[2]; a2.href = L[3];
  const sb = document.getElementById('mdStar');
  sb.textContent = watch.has(k) ? '★ 관심종목 해제' : '☆ 관심종목 담기';
  document.getElementById('mdFin').innerHTML = finBlock(m, code);
  document.getElementById('mdBack').hidden = false;
}

/* ── 실적 시계열 ──────────────────────────────────────────────
   매출 막대 + 영업이익률 선 + YoY. 수치는 SEC 가 받은 공식 재무제표다.
   미국 국내 기업만 분기가 있다 — 외국 기업(SEA·알리바바 등)은 SEC 에 연 1회만
   내므로 연간 막대가 나온다. 없는 분기를 지어내지 않고 그렇게 적는다. */
/* 통화별 자릿수. 엔·원은 자릿수가 커서 달러와 같은 눈금을 쓰면 못 읽는다.
   환산하지 않고 원래 통화 그대로 적는다 — 몇 년치를 오늘 환율로 환산하면
   매출 추세가 아니라 환율 추세가 된다. */
const CUR = {
  USD: '달러', JPY: '엔', HKD: '홍콩달러', CNY: '위안', EUR: '유로',
  GBP: '파운드', CHF: '스위스프랑', CAD: '캐나다달러', AUD: '호주달러',
  DKK: '덴마크크로네', SEK: '스웨덴크로나', NOK: '노르웨이크로네',
  BRL: '헤알', INR: '루피', KRW: '원', TWD: '대만달러', SGD: '싱가포르달러',
  MXN: '페소', ZAR: '란드', ILS: '셰켈', THB: '바트', IDR: '루피아',
};
/* 자릿수 눈금. 엔·원·위안은 억/조로 끊어야 읽히고, 달러·파운드는 B/M 이 익숙하다.
   모르는 통화는 달러식으로 끊고 통화 이름만 원문 그대로 적는다 — 지어내지 않는다. */
const CUR_BIG = { JPY: 1, KRW: 1, CNY: 1, TWD: 1, INR: 1, IDR: 1 };
const STEP_BIG = [[1e12, '조'], [1e8, '억'], [1e4, '만']];
const STEP_SM = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
function curOf(code) {
  const c = code || 'USD';
  return { ko: CUR[c] || c, steps: CUR_BIG[c] ? STEP_BIG : STEP_SM };
}
function finBlock(m, code) {
  const key = m + ':' + code;
  const f = D.fin[key], sg = D.seg[key];
  if (!f) return sg
    ? '<div class="finwrap">' + segChart(sg, 'USD') + '</div>'
    : '<p class="finnote">이 종목은 아직 실적 수치를 받지 않았습니다. ' +
      '시가총액 큰 종목부터 채우는 중입니다.</p>';
  let html = '';
  if (f.eps) {
    const u = f.eps.upcoming, d = f.eps.done.slice(-4);
    html += '<div class="epsrow">' +
      d.map(x => '<span class="epsbox"><b>' + esc(epsQ(x.period)) + '</b>' +
        'EPS ' + esc(String(x.actual)) +
        (x.consensus ? ' <i>(예상 ' + esc(String(x.consensus)) + ')</i>' : '') +
        surprise(x.actual, x.consensus) + '</span>').join('') +
      (u ? '<span class="epsbox next"><b>' + esc(epsQ(u.period)) + '</b>아직 발표 전' +
           (u.consensus ? ' <i>(예상 ' + u.consensus + ')</i>' : '') + '</span>' : '') +
      '</div>';
  }
  // **연간 막대는 그리지 않는다.** 연간으로는 "이번 분기가 작년 같은 분기보다
  // 나아졌나"를 볼 수 없어서 애초에 보려던 그림이 아니다. 분기(또는 홍콩 반기)만 낸다.
  const pts = (f.points || []).filter(() => f.freq === 'Q' || f.freq === 'H');
  if (pts.length >= 2) html += finChart(f);
  else if (f.freq === 'A')
    html += '<p class="finnote">이 종목은 <b>분기 실적을 못 구했습니다.</b> ' +
            '연 1회만 공시하는 회사이거나 아직 분기 자료를 받지 못한 경우입니다. ' +
            '(연간 수치는 추세를 볼 수 없어 싣지 않습니다.)</p>';
  else if (pts.length === 1) {
    const p = pts[0], U = curOf(f.cur);
    html += '<p class="finnote">받은 분기가 <b>' + esc(p[0]) + '</b> 하나뿐이라 ' +
            '추세를 그리지 못했습니다. (매출 ' + p[1].toLocaleString() + ' ' +
            esc(U.ko) + ')</p>';
  }
  // 사업부별은 매출·성장률 아래에 붙인다. 큰 그림을 먼저 보고 쪼개 보는 순서다.
  if (sg) html += segChart(sg, f.cur || 'USD');
  return html || '<p class="finnote">받아둔 수치가 없습니다.</p>';
}

const SRC_KO = { sec: 'SEC 공식 재무제표', sa: 'stockanalysis.com', yahoo: 'Yahoo Finance',
                 tdnet: 'TDnet 결산단신 (공식 공시)',
                 mix: 'SEC 공식 재무제표 + stockanalysis.com' };

/* 부문 색. 여덟이면 웬만한 회사는 덮는다. 그 이상은 되풀이한다. */
const SEG_COLORS = ['#5B9BD5', '#ED7D31', '#C0504D', '#4BA893', '#8E7CC3',
                    '#D6A02F', '#7BA7CC', '#B0736F'];

/* 사업부별 매출 — 쌓은 막대.
   "매출이 늘었다"보다 "어디서 늘었다"가 중요할 때가 있다. 로켓랩은 발사 서비스와
   우주 시스템이 따로 움직이고, SEA 는 쇼피·가레나·머니가 따로 논다.
   조각마다 숫자를 적되, 조각이 얇으면 글자가 삐져나오므로 생략한다. */
function segChart(sg, cur) {
  const pts = (sg.pts || []).slice(-22);
  if (pts.length < 2) return '';
  const names = sg.names || [];
  const tot = pts.map(r => r.slice(1).reduce((a, v) => a + (v || 0), 0));
  const maxRaw = Math.max(...tot, 1);
  const U = unitFor(maxRaw, cur);
  const sc = v => v / U.div;
  const rmax = niceMax(sc(maxRaw));

  const n = pts.length;
  const W = Math.max(880, n * 46), L = 66, R = 20, B = 42, T = 30, H = 340;
  const BASE = H - B, step = (W - L - R) / n;
  const cx = i => L + step * i + step / 2;
  const y = v => BASE - (BASE - T) * v / rmax;

  let body = '';
  pts.forEach((r, i) => {
    let acc = 0;
    names.forEach((nm, j) => {
      const v = r[j + 1];
      if (!v) return;
      const y0 = y(sc(acc)), y1 = y(sc(acc + v)), h = Math.max(y0 - y1, 0.6);
      const x = cx(i) - step * 0.34;
      body += '<rect x="' + x.toFixed(1) + '" y="' + y1.toFixed(1) +
        '" width="' + (step * 0.68).toFixed(1) + '" height="' + h.toFixed(1) +
        '" fill="' + SEG_COLORS[j % SEG_COLORS.length] + '"><title>' +
        esc(nm) + ' ' + fmtN(sc(v)) + '</title></rect>';
      // 조각이 얇으면 숫자가 삐져나온다. 넉넉할 때만 적는다.
      if (h >= 15)
        body += '<text class="vn seg" x="' + cx(i).toFixed(1) + '" y="' +
          (y1 + h / 2 + 4).toFixed(1) + '" text-anchor="middle">' + fmtN(sc(v)) + '</text>';
      acc += v;
    });
  });
  const axis = [0, rmax / 2, rmax].map(v =>
    '<line class="fz" x1="' + L + '" y1="' + y(v).toFixed(1) + '" x2="' + (W - R) +
    '" y2="' + y(v).toFixed(1) + '"/>' +
    '<text class="fx" x="' + (L - 8) + '" y="' + (y(v) + 4).toFixed(1) +
    '" text-anchor="end">' + fmtN(v) + '</text>').join('');
  const xlab = pts.map((r, i) =>
    '<text class="fx' + (i === n - 1 ? ' now' : '') + '" x="' + cx(i).toFixed(1) +
    '" y="' + (H - B + 18) + '" text-anchor="middle">' + r[0] + '</text>').join('');

  // 부문이 매출 전부를 설명하지 않는 회사가 흔하다(본사 몫·기타·조정). 그럴 때
  // 막대 높이를 총매출로 읽으면 틀린다. 얼마를 덮는지 적어둔다.
  return '<div class="finhead sub">사업부별 매출' +
    '<span class="dim">(단위: ' + esc(U.ko) +
    (sg.cov ? ' · 총매출의 ' + sg.cov + '%' : '') + ')</span></div>' +
    '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H + '" class="finsvg">' +
    axis + body + xlab + '</svg></div>' +
    '<div class="finlegend">' + names.map((nm, j) =>
      '<span class="lg" style="--c:' + SEG_COLORS[j % SEG_COLORS.length] + '">' +
      esc(nm) + '</span>').join('') + '</div>';
}

/* 숫자에서 단위를 뗀다. 막대마다 '억'·'B' 를 붙이면 자릿수가 눈에 안 들어온다.
   대신 눈금 하나를 골라 제목에 '(단위: bil JPY)' 로 한 번만 적는다.
   눈금은 제일 큰 값이 세 자리 이상 되는 것 중 가장 큰 것으로 고른다 —
   도요타는 bil JPY(13,525), 산리오는 mil JPY(55,500) 가 된다. */
const SCALES = [[1e12, 'tril'], [1e9, 'bil'], [1e6, 'mil'], [1e3, 'k'], [1, '']];
function unitFor(max, cur) {
  const hit = SCALES.find(([n]) => max / n >= 100) || SCALES[SCALES.length - 1];
  return { div: hit[0], ko: (hit[1] ? hit[1] + ' ' : '') + (cur || 'USD') };
}
const fmtN = v => {
  const a = Math.abs(v);
  return a === 0   ? '0'
       : a >= 100  ? Math.round(v).toLocaleString()
       : a >= 10   ? v.toFixed(1)
                   : v.toFixed(2);
};
/* 축 눈금은 딱 떨어지는 수로. 55,518 같은 값이 축에 적혀 있으면 읽는 데 방해만 된다. */
function niceMax(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v || 1)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8]) if (p * m >= v) return p * m;
  return p * 10;
}
const pcN = v => (v * 100).toFixed(0) + '%';

function finChart(f) {
  /* 그림 둘.
       (1) 매출 막대(왼쪽 축) + 영업이익률 선(오른쪽 축)  — 한 그래프에 겹쳐 그린다
       (2) 매출 성장률(YoY)

     영업이익률 축은 **그 종목의 값 언저리로 좁게** 잡는다. 0~100% 로 넓게 잡으면
     15%~27% 짜리 등락이 한 줄로 눌려 보이질 않는다. 회원님이 보내준 그림도
     오른쪽 축이 0~18% 로 좁게 잡혀 있어서 마진 흐름이 읽힌다. */
  const all = f.points;
  const back = f.freq === 'H' ? 2 : 4;
  // 성장률은 자르기 전에 계산한다 — 자른 뒤 계산하면 앞 네 분기가 빈다.
  const yoyAll = all.map((p, i) => {
    const prev = all[i - back];
    return (prev && prev[1]) ? p[1] / prev[1] - 1 : null;
  });
  const N = 22;                       // 이보다 촘촘하면 숫자가 서로 겹친다
  const pts = all.slice(-N), yoy = yoyAll.slice(-N);
  const latest = pts.length ? pts[pts.length - 1][0] : '';

  const rmaxRaw = Math.max(...pts.map(p => p[1]), 1);
  const U = unitFor(rmaxRaw, f.cur);
  const sc = v => v / U.div;

  const n = pts.length;
  const W = Math.max(880, n * 46), L = 66, R = 60, B = 42;
  const step = (W - L - R) / n;
  const cx = i => L + step * i + step / 2;
  const xlab = (H) => pts.map((p, i) =>
    '<text class="fx' + (i === n - 1 ? ' now' : '') + '" x="' + cx(i).toFixed(1) +
    '" y="' + (H - B + 18) + '" text-anchor="middle">' + p[0] + '</text>').join('');

  /* ── (1) 매출 + 영업이익률 ───────────────────────────────── */
  const H1 = 330, T1 = 40, BASE1 = H1 - B;
  const rmax = niceMax(sc(rmaxRaw));
  const ry = v => BASE1 - (BASE1 - T1) * v / rmax;

  // 영업이익률 축을 먼저 정해 둔다 — 매출 숫자를 막대 위에 쓸지 안에 쓸지
  // 정하려면 선이 어디를 지나는지 알아야 한다.
  const opm = pts.map((p, i) => (p[2] != null && p[1]) ? [i, p[2] / p[1]] : null).filter(Boolean);
  let oy = null;
  if (opm.length >= 2) {
    // 적자 회사는 이익률이 한 분기만 -989% 로 튀기도 한다. 그걸 축에 그대로
    // 반영하면 나머지 스무 분기가 한 줄로 눌려 아무것도 안 보인다. 축은
    // -100%~100% 안쪽 값들로만 잡고, 벗어난 점은 가장자리에 붙이되 **숫자는
    // 실제 값을 적는다** — 눌러 담되 속이지는 않는다.
    const all = opm.map(o => o[1]);
    const inr = all.filter(v => v >= -1 && v <= 1);
    const vs = inr.length >= 2 ? inr : all;
    const mn = Math.min(...vs), mx = Math.max(...vs);
    const span = (mx - mn) || 0.04;
    const lo = mn - span * 0.45, hi = mx + span * 0.12;
    oy = v => BASE1 - (BASE1 - T1) * (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo);
    oy.lo = lo; oy.hi = hi;
  }
  const opmAt = {};
  opm.forEach(o => { opmAt[o[0]] = oy(o[1]); });

  let bars = '';
  pts.forEach((p, i) => {
    const v = sc(p[1]), h = Math.max(BASE1 - ry(v), 1), x = cx(i) - step * 0.34;
    // 이익률 선이 막대 꼭대기를 지나가는 자리에서는 숫자끼리 겹친다. 막대 안에
    // 넣어 보려 했더니 막대가 글자보다 좁아 잘렸다. 글자에 바탕색 테두리를 둘러
    // 무엇 위에 놓이든 읽히게 한다(paint-order).
    const oyy = opmAt[i];
    const ty = (oyy != null && Math.abs(oyy - ry(v)) < 20) ? ry(v) - 17 : ry(v) - 6;
    bars += '<rect class="fb" x="' + x.toFixed(1) + '" y="' + ry(v).toFixed(1) +
      '" width="' + (step * 0.68).toFixed(1) + '" height="' + h.toFixed(1) + '"/>' +
      '<text class="vn rev" x="' + cx(i).toFixed(1) +
      '" y="' + ty.toFixed(1) + '" text-anchor="middle">' + fmtN(v) + '</text>';
  });
  const rAxis = [0, rmax / 2, rmax].map(v =>
    '<line class="fz" x1="' + L + '" y1="' + ry(v).toFixed(1) + '" x2="' + (W - R) +
    '" y2="' + ry(v).toFixed(1) + '"/>' +
    '<text class="fx" x="' + (L - 8) + '" y="' + (ry(v) + 4).toFixed(1) +
    '" text-anchor="end">' + fmtN(v) + '</text>').join('');

  // 영업이익률 — 오른쪽 축. 값 언저리로 좁게 잡아야 등락이 보인다.
  let opmSvg = '', opmAxis = '';
  if (oy) {
    const lo = oy.lo, hi = oy.hi;
    opmSvg = '<path class="fl opm" d="' +
      opm.map((o, j) => (j ? 'L' : 'M') + cx(o[0]).toFixed(1) + ',' + oy(o[1]).toFixed(1)).join(' ') +
      '"/>' + opm.map(o =>
      '<circle class="dot opm" cx="' + cx(o[0]).toFixed(1) + '" cy="' + oy(o[1]).toFixed(1) + '" r="3"/>' +
      '<text class="vn opm" x="' + cx(o[0]).toFixed(1) + '" y="' + (oy(o[1]) - 9).toFixed(1) +
      '" text-anchor="middle">' + pcN(o[1]) + '</text>').join('');
    opmAxis = [lo, (lo + hi) / 2, hi].map(v =>
      '<text class="fx opm" x="' + (W - R + 8) + '" y="' + (oy(v) + 4).toFixed(1) + '">' +
      pcN(v) + '</text>').join('');
  }

  /* ── (2) 매출 성장률 (YoY) ───────────────────────────────── */
  const have = yoy.map((v, i) => v == null ? null : [i, v]).filter(Boolean);
  let yoySvg = '';
  if (have.length >= 2) {
    const H2 = 220, T2 = 32, BASE2 = H2 - B;
    const vs = have.map(o => o[1]);
    let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
    const pad = (hi - lo) * 0.22 || 0.05;
    lo -= pad; hi += pad;
    const gy = v => BASE2 - (BASE2 - T2) * (v - lo) / (hi - lo);
    const ticks = (lo < 0 && hi > 0) ? [lo, 0, hi] : [lo, (lo + hi) / 2, hi];
    const axis = ticks.map(v =>
      '<line class="' + (v === 0 ? 'fzero' : 'fz') + '" x1="' + L + '" y1="' + gy(v).toFixed(1) +
      '" x2="' + (W - R) + '" y2="' + gy(v).toFixed(1) + '"/>' +
      '<text class="fx" x="' + (L - 8) + '" y="' + (gy(v) + 4).toFixed(1) +
      '" text-anchor="end">' + pcN(v) + '</text>').join('');
    yoySvg = '<div class="finhead sub">매출 성장률 <span class="dim">(YoY)</span></div>' +
      '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H2 + '" class="finsvg">' + axis +
      '<path class="fl yoy" d="' +
      have.map((o, j) => (j ? 'L' : 'M') + cx(o[0]).toFixed(1) + ',' + gy(o[1]).toFixed(1)).join(' ') +
      '"/>' + have.map(o =>
        '<circle class="dot yoy" cx="' + cx(o[0]).toFixed(1) + '" cy="' + gy(o[1]).toFixed(1) + '" r="3"/>' +
        '<text class="vn yoy" x="' + cx(o[0]).toFixed(1) + '" y="' + (gy(o[1]) - 9).toFixed(1) +
        '" text-anchor="middle">' + pcN(o[1]) + '</text>').join('') +
      xlab(H2) + '</svg></div>';
  }

  const per = f.freq === 'H' ? '반기' : '분기';
  const notes = [];
  if (f.freq === 'H') notes.push('홍콩은 반기 보고입니다');
  if (pts.length < 8) notes.push('받을 수 있었던 건 ' + pts.length + '개뿐입니다');

  return '<div class="finwrap">' +
    '<div class="finhead">' + per + ' 매출 · 영업이익률' +
    '<span class="dim">(단위: ' + esc(U.ko) + ')</span>' +
    (latest ? '<span class="now">최신 ' + esc(latest) + '</span>' : '') +
    (notes.length ? '<span class="warn">' + notes.join(' · ') + '</span>' : '') + '</div>' +
    '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H1 + '" class="finsvg">' +
    rAxis + bars + opmSvg + opmAxis + xlab(H1) + '</svg></div>' +
    '<div class="finlegend"><span class="lg rev">매출 (왼쪽)</span>' +
    '<span class="lg opm">영업이익률 (오른쪽)</span></div>' +
    yoySvg +
    '<div class="finlegend"><span class="lg yoy">매출 성장률 (YoY)</span>' +
    '<span class="src">출처 ' + SRC_KO[f.src || 'sec'] + '</span></div></div>';
}
function closeModal() { document.getElementById('mdBack').hidden = true; mdKey = null; }
document.getElementById('mdClose').onclick = closeModal;
document.getElementById('mdBack').onclick = e => {
  if (e.target.id === 'mdBack') closeModal();
};
document.getElementById('mdStar').onclick = () => { if (mdKey) { toggleWatch(mdKey); closeModal(); } };
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ── .ics 내보내기 ────────────────────────────────────────── */
function icsEscape(s) { return String(s).replace(/([,;\\])/g, '\\$1').replace(/\n/g, '\\n'); }

/* RFC 5545는 한 줄을 75옥텟으로 제한하고, 넘치면 다음 줄을 공백 한 칸으로
   시작해 잇게 한다. 한글·일본어는 글자당 3바이트라 DESCRIPTION이 쉽게 넘어간다.
   너그러운 클라이언트도 많지만, 엄격한 파서에서 통째로 깨지는 걸 막는다.
   바이트로 재되 글자 중간에서는 자르지 않는다. */
const ICS_ENC = new TextEncoder();
function icsFold(line) {
  if (ICS_ENC.encode(line).length <= 75) return line;
  const out = [];
  let cur = '', len = 0;
  for (const ch of line) {                 // 코드포인트 단위로 순회
    const n = ICS_ENC.encode(ch).length;
    const cap = out.length ? 74 : 75;      // 이어지는 줄은 공백 한 칸을 먹는다
    if (len + n > cap) { out.push(cur); cur = ''; len = 0; }
    cur += ch; len += n;
  }
  if (cur) out.push(cur);
  return out[0] + out.slice(1).map(s => '\r\n ' + s).join('');
}

function makeIcs(rows, calName) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  const L = ['BEGIN:VCALENDAR', 'VERSION:2.0',
             'PRODID:-//CB//Global Earnings Calendar//KO',
             'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:' + icsEscape(calName)];
  for (const r of rows) {
    const d = r[0].replace(/-/g, '');
    const end = addDays(r[0], 1).replace(/-/g, '');
    const k = keyOf(r), nt = NOTE[k], m = r[9];
    /* 날짜는 각 시장의 현지 날짜다. 종일 일정으로 넣어 시차를 건드리지 않는다 —
       구독하는 쪽 표준시로 환산하면 미국 장후 발표가 하루 밀려 보인다. */
    L.push('BEGIN:VEVENT',
      'UID:' + m + '-' + r[1] + '-' + d + '@cb-earnings',
      'DTSTAMP:' + stamp,
      'DTSTART;VALUE=DATE:' + d,
      'DTEND;VALUE=DATE:' + end,
      'SUMMARY:' + icsEscape(
        '[' + MKT[m].ko + '] ' + r[2] + ' ' + r[1] +
        (r[4] ? ' · ' + r[4] : '') + (r[10] ? ' · ' + r[10] : '')),
      'DESCRIPTION:' + icsEscape(
        altOf(r).join(' / ') +
        '\n' + [r[4], r[3], r[5], r[6]].filter(Boolean).join(' · ') +
        (r[10] ? '\n발표 시각: ' + r[10] : '') +
        (r[11] ? '\n시가총액: ' + capKo(r[11]) : '') +
        '\n' + links(m, r[1])[1]),
      'TRANSP:TRANSPARENT',
      'BEGIN:VALARM', 'TRIGGER:-P1D', 'ACTION:DISPLAY',
      'DESCRIPTION:' + icsEscape('내일 실적발표 — ' + r[2] + ' (' + MKT[m].ko + ')'),
      'END:VALARM', 'END:VEVENT');
  }
  L.push('END:VCALENDAR');
  return L.map(icsFold).join('\r\n') + '\r\n';
}
function download(name, text) {
  const blob = new Blob([text], { type: 'text/calendar;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
document.getElementById('icsWatch').onclick = () => {
  const rows = ROWS.filter(r => watch.has(keyOf(r)));
  if (!rows.length) { alert('관심종목이 없습니다. ☆ 를 눌러 먼저 담아주세요.'); return; }
  download('earnings-watchlist.ics', makeIcs(rows, '글로벌 실적발표 — 관심종목'));
};
document.getElementById('icsWeek').onclick = () => {
  const days = new Set(weekDays(week));
  const rows = VIEW.filter(r => days.has(slotOf(r)));
  const who = mkt ? MKT[mkt].ko : '글로벌';
  if (!rows.length) { alert('이번 주에는 발표 일정이 없습니다.'); return; }
  download('earnings-' + (mkt || 'all') + '-' + week + '.ics',
           makeIcs(rows, who + ' 실적발표 ' + fmtWeek(week)));
};
document.getElementById('clearWatch').onclick = () => {
  if (!watch.size) return;
  if (!confirm('관심종목 ' + watch.size + '개를 모두 비웁니다.')) return;
  watch.clear(); saveWatch(); renderAll();
};

/* ── 이벤트 위임 ──────────────────────────────────────────── */
document.addEventListener('click', e => {
  const more = e.target.closest('.more');
  if (more) {
    const k = more.dataset.key;
    expanded.has(k) ? expanded.delete(k) : expanded.add(k);
    renderCal();
    return;
  }
  const star = e.target.closest('[data-star]');
  if (star) { e.stopPropagation(); toggleWatch(star.dataset.star); return; }

  const chk = e.target.closest('[data-mchk]');
  if (chk) { toggleMarket(chk.dataset.mchk); return; }
  const tab = e.target.closest('.mtab');
  if (tab) { setMarket(tab.dataset.mkt); return; }

  const chipEl = e.target.closest('.chip');
  if (chipEl) {
    // 칩 안의 ★ 영역을 누르면 담기, 나머지는 상세 열기
    if (e.target.closest('.st')) toggleWatch(chipEl.dataset.key);
    else openModal(chipEl.dataset.key, chipEl.dataset.date);
    return;
  }
  const tr = e.target.closest('#tBody tr');
  if (tr) { openModal(tr.dataset.key, tr.dataset.date); return; }

  const bar = e.target.closest('rect.b');
  if (bar) {
    const d = parse(bar.dataset.date);
    go(iso(new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay()+6)%7))));
    document.getElementById('cal').scrollIntoView({ behavior:'smooth', block:'center' });
  }
});

document.querySelectorAll('#tAll thead th[data-k]').forEach(th => {
  th.onclick = () => {
    const k = +th.dataset.k;
    sortDir = (k === sortKey) ? -sortDir : 1;
    sortKey = k;
    document.querySelectorAll('#tAll thead th').forEach(x => x.classList.remove('asc','desc'));
    th.classList.add(sortDir === 1 ? 'asc' : 'desc');
    th.querySelector('.ar').textContent = sortDir === 1 ? '▴' : '▾';
    renderTable();
  };
});

/* 업종·거래소·분기 후보는 지금 보고 있는 시장의 데이터에서 뽑는다.
   일본 업종 36종을 미국 탭에서 고르게 두면 아무것도 안 걸린다. */
function fillFilters() {
  const opts = i => [...new Set(VIEW.map(r => r[i]).filter(Boolean))].sort();
  for (const [id, arr, all] of [['fSector', opts(5), '전체 업종'],
                                ['fMarket', opts(6), '전체 거래소'],
                                ['fKind', opts(4), '전체 분기']]) {
    const sel = document.getElementById(id), keep = sel.value;
    sel.innerHTML = '<option value="">' + all + '</option>' +
      arr.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
    if (arr.includes(keep)) sel.value = keep;
    sel.hidden = !arr.length;
  }
}
for (const id of ['fSector','fMarket','fKind'])
  document.getElementById(id).onchange = renderTable;

/* 규모 필터는 캘린더·표에 함께 걸리므로 전체를 다시 그린다. */
capSel.onchange = () => { expanded.clear(); renderAll(); };

/* 시총 자료가 없어 규모 필터가 닿지 않는 시장을 적어준다.
   조용히 빠져나가게 두면 '걸렀는데 왜 아직 많냐'가 된다. */
function renderCapNote() {
  const el = document.getElementById('capNote');
  if (!capMin()) { el.hidden = true; return; }
  const shown = onMkts();
  // 규모 필터가 무엇을 감췄고 무엇을 통과시켰는지 적는다. 조용히 지우지 않는다.
  const hid = {}, thru = {};
  for (const r of ROWS) {
    if (!shown.includes(r[9]) || r[11]) continue;
    (CAP_INLINE.has(r[9]) ? hid : thru)[r[9]] = ((CAP_INLINE.has(r[9]) ? hid : thru)[r[9]] || new Set()).add(r[1]);
  }
  const bits = [];
  for (const m of shown) {
    if (hid[m]) bits.push(MKT[m].flag + ' ' + MKT[m].ko + ' <b>' + hid[m].size +
      '종목</b>은 원본에 시총이 없어 숨겼습니다');
    if (thru[m]) bits.push(MKT[m].flag + ' ' + MKT[m].ko + ' <b>' + thru[m].size +
      '종목</b>은 시총을 아직 못 받아 <b>그대로 보입니다</b>');
  }
  if (!bits.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = bits.join(' · ') +
    ' <span class="dim">(1조원 ≈ $' + (1e12 / D.usdKrw / 1e9).toFixed(2) +
    'B, 환율 ' + D.usdKrw.toLocaleString() + '원 어림)</span>';
}
document.getElementById('q').oninput = renderTable;
for (const id of ['tBig','tWatch','tFuture']) document.getElementById(id).onchange = renderTable;
document.getElementById('onlyWatch').onchange = renderCal;
/* 한국 시간으로 보면 미국 장후 발표가 다음 날 칸으로 옮겨간다.
   날짜 묶음 자체가 달라지므로 다시 자른 뒤 전부 그린다. */
document.getElementById('kstToggle').onchange = e => {
  useKst = e.target.checked; expanded.clear(); reslice(); fillWeeks(); renderAll();
};
document.getElementById('jpToggle').onchange = e => { showJp = e.target.checked; renderCal(); };

/* ── 시장 탭 ──────────────────────────────────────────────── */
/* 탭을 누르면 그 시장만 켠다. 여러 시장을 같이 보려면 체크박스를 쓴다. */
function setMarket(m) {
  picked = m ? new Set([m]) : new Set(LIVE);
  refresh();
}
function toggleMarket(m) {
  picked.has(m) ? picked.delete(m) : picked.add(m);
  if (!picked.size) picked = new Set(LIVE);   // 전부 끄면 아무것도 안 보이므로 되돌린다
  refresh();
}
function refresh() {
  expanded.clear(); reslice(); fillFilters(); fillWeeks(); renderAll();
}
function renderTabs() {
  const all = picked.size === LIVE.length;
  const tabs = [{ id: '', flag: '🌐', ko: '전체', n: ROWS.length, has: true, on: all }]
    .concat(MKTS.map(m => ({ id: m.id, flag: m.flag, ko: m.ko, n: m.count,
                             has: m.has, on: !all && picked.has(m.id) })));
  document.getElementById('mtabs').innerHTML = tabs.map(t =>
    '<div class="mtab m-' + (t.id || 'all') + (t.on ? ' on' : '') +
    (t.has ? '' : ' empty') + '" data-mkt="' + t.id + '">' +
    (t.id ? '<input type="checkbox" class="mchk" data-mchk="' + t.id + '"' +
            (picked.has(t.id) ? ' checked' : '') + (t.has ? '' : ' disabled') +
            ' title="여러 시장을 같이 보려면 체크하세요">' : '') +
    '<span class="fl">' + t.flag + '</span>' + esc(t.ko) +
    '<span class="n">' + (t.has ? t.n.toLocaleString() + '건' : '미수집') + '</span></div>'
  ).join('');

  // 캘린더 옆에도 같은 것을 둔다. 같은 data-mchk 를 쓰므로 어느 쪽을 눌러도 같다.
  document.getElementById('calMkts').innerHTML = MKTS.map(m =>
    '<button class="mp' + (picked.has(m.id) ? ' on' : '') + '" data-mchk="' + m.id + '"' +
    (m.has ? '' : ' disabled title="아직 수집하지 않았습니다"') + '>' +
    m.flag + ' ' + esc(m.ko) + '</button>').join('');
}

/* ── 요약 카드 ────────────────────────────────────────────── */
function renderCards() {
  const days = [...byDate.keys()].filter(d => byDate.get(d).length);
  const busiest = days.reduce((a, d) =>
    byDate.get(d).length > (a ? byDate.get(a).length : 0) ? d : a, '');
  const big = VIEW.filter(noteOf).length;
  const cards = [
    ['수집 발표', VIEW.length.toLocaleString(), ''],
    ['발표일 수', days.length.toLocaleString(), ''],
    ['주목종목 발표', big.toLocaleString(), ''],
    ['최다 발표일', busiest
      ? busiest + ' · ' + byDate.get(busiest).length.toLocaleString() + '건' : '—', 'sm'],
  ];
  document.getElementById('cards').innerHTML = cards.map(c =>
    '<div class="card"><div class="k">' + c[0] + '</div>' +
    '<div class="v ' + c[2] + '">' + c[1] + '</div></div>').join('');

  const cur = mkt ? MKT[mkt] : null;
  document.getElementById('calMeta').textContent = cur
    ? cur.flag + ' ' + cur.ko + ' 상장사 — ' + cur.note
    : '미국 · 일본 상장사 발표 예정 + 홍콩 공시';
}

/* ── 출처와 수집 구멍 ─────────────────────────────────────── */
/* 수집 구간에 구멍이 있으면 숨기지 않고 적는다. 빈 칸이 '발표가 없는 날'인지
   '아직 못 받은 날'인지 구분되지 않으면 캘린더를 믿을 수 없다. */
function renderFoot() {
  document.getElementById('srcLink').innerHTML =
    '출처 ' + D.sources.map(s => MKT[s.mkt].flag + ' ' + esc(s.name) +
      ' <a href="' + esc(s.url) + '" target="_blank" rel="noopener">↗</a>').join(' · ') +
    '<br>';

  let out = '';
  for (const m of LIVE) {
    const ds = D.okDays[m];
    if (!ds || !ds.length) continue;
    const gaps = [];
    for (let d = ds[0]; d <= ds[ds.length - 1]; d = addDays(d, 1)) {
      if (!okSet[m].has(d)) gaps.push(d);
    }
    if (gaps.length) {
      out += MKT[m].flag + ' ' + MKT[m].ko + ' 미수집 ' + gaps.length + '일 (' +
        (gaps.length > 12 ? gaps.slice(0, 12).join(', ') + ' 외 ' + (gaps.length - 12) + '일'
                          : gaps.join(', ')) + ')<br>';
    }
  }
  for (const m of MKTS.filter(x => !x.has)) {
    out += m.flag + ' ' + m.ko + ' — 아직 수집하지 않았습니다. <b>python ' +
           m.scraper + '</b> 을 돌리면 채워집니다.<br>';
  }
  document.getElementById('gapNote').innerHTML = out
    ? out + '캘린더에는 <b>미수집 구간</b>으로 표시됩니다.<br>' : '';
}

function renderAll() {
  renderTabs(); renderCards(); renderCapNote();
  renderCal(); renderAlert(); renderGroups(); renderBars(); renderTable();
}
reslice(); fillFilters(); fillWeeks(); renderFoot(); renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
