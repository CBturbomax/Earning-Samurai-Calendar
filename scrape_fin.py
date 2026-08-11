# -*- coding: utf-8 -*-
"""
미국 실적 '수치' 수집 — 출처: SEC XBRL (재무 시계열) + Nasdaq (발표 완료·서프라이즈)

지금까지는 '언제 발표하는가'만 다뤘다. 여기서는 '무엇을 발표했는가'를 받는다.

두 소스를 쓰는 이유가 각각 있다.

  SEC XBRL  — 회사가 제출한 공식 재무제표. 분기 매출·영업이익을 몇 년치 쭉 준다.
              다만 **미국 국내 기업(10-Q 제출)만** 분기가 있다. SEA·알리바바 같은
              외국 기업은 20-F(연 1회)만 내므로 분기가 아예 없다. 지어내지 않고
              연간만 담고, 화면에 '연간만 있음'이라고 적는다.

  Nasdaq    — 발표 완료 여부와 EPS 서프라이즈. 예정만 있으면 지났는지 알 수 없는데,
              여기서 '실제 EPS 가 찍혔는가'로 판단할 수 있다. 외국 기업도 나온다.

종목이 4천 개라 전부 받으면 오래 걸린다. 시가총액 상위부터 받고, 다음 실행 때
이어서 채운다. 이미 받은 종목은 건너뛴다.

결과: data/financials.json
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
OUT = HERE / "data" / "financials.json"

# SEC 는 연락처가 담긴 User-Agent 를 요구한다(그쪽 이용약관).
SEC_UA = os.environ.get("SEC_UA", "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)")
NAS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TICKERS = "https://www.sec.gov/files/company_tickers.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/{ns}/{tag}.json"
NAS_EPS = "https://api.nasdaq.com/api/quote/{sym}/eps"

# 매출 태그는 회사마다 다르다. 게다가 한 회사가 도중에 바꾸기도 한다.
# 그래서 하나만 고르지 않고 **여러 개를 받아 합친다**(아래 series 참고).
# 앞에 적은 것이 더 정확한 표현이라 겹치는 분기는 앞의 것을 남긴다.
#
# 앞 넷은 늘 받고, 그래도 분기가 모자랄 때만 뒤엣것까지 간다. 뒤엣것은 업종이
# 특수한 회사들(은행의 '이자 차감 후 순수익' 같은)이라 흔하지 않다.
REV_CORE = ["RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet"]
REV_MORE = ["RevenuesNetOfInterestExpense",     # 은행
            "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet"]
REV_TAGS = REV_CORE + REV_MORE                  # probe 에서 통째로 쓸 때용
OPI_TAG = "OperatingIncomeLoss"
NI_TAG = "NetIncomeLoss"

# **외국 기업은 회계 언어가 다르다.** 도요타·소니·HSBC·셸·노보노디스크는 미국
# 기준(us-gaap)이 아니라 국제 기준(IFRS)으로 낸다. us-gaap 만 물으면 이 회사들은
# 통째로 빈다 — 실제로 59종목이 그렇게 비었다. 못 찾으면 IFRS 로 한 번 더 묻는다.
IFRS_REV = ["Revenue", "RevenueFromContractsWithCustomers"]
IFRS_OPI = "ProfitLossFromOperatingActivities"
IFRS_NI = "ProfitLoss"

PER_RUN = int(os.environ.get("FIN_PER_RUN", "250"))   # 한 번에 받을 종목 수
SINCE = "2018-10-01"                                  # 1Q19 부터 보려면 조금 앞부터

# 저장 형식 번호. 받는 방식을 고치면 이 번호를 올린다. 그러면 이미 받아둔 기록도
# 헌 것으로 쳐서 다시 받는다.
#   2 -> 3  매출 태그를 합치도록. 그 전에는 첫 태그에서 멈춰 엔비디아가 6분기.
#   3 -> 4  IFRS(외국 기업) + 달러 아닌 통화 + 막힌 것과 없는 것을 가름.
FIN_VER = 4

BACKOFF = (0, 5, 20, 60)   # SEC 가 막으면 쉬었다 다시. 없는 태그(404)는 재시도 않는다.
GIVE_UP_AFTER = 5          # 연속 이만큼 막히면 이번 실행은 접는다

# 다시 받는 주기. 발표일 언저리 종목은 매일, 나머지는 이 간격으로.
NEAR_DAYS = int(os.environ.get("FIN_NEAR_DAYS", "4"))     # 발표일 ±4일이면 '언저리'
STALE_DAYS = int(os.environ.get("FIN_STALE_DAYS", "7"))   # 그 밖은 7일마다


class Throttled(Exception):
    """SEC 가 막았다. '그 회사에 자료가 없다'와 전혀 다른 일이다."""


def get(url, ua, timeout=30):
    """404 는 None(그 태그를 안 쓰는 회사), 그 밖의 실패는 Throttled.

    예전에는 모든 실패를 똑같이 '없음'으로 삼켰다. 그러면 SEC 가 잠깐 막았을
    뿐인데 그 종목을 '자료 없음'으로 찍어 두고 28일간 다시 안 물어본다.
    막힌 것과 없는 것은 반드시 갈라야 한다.
    """
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise Throttled(str(e))


def quarters(units):
    """companyconcept 응답에서 분기 값·연간 값을 갈라 담는다.

    기간 길이로 가른다 — 80~100일이면 분기, 350~380일이면 연간이다.

    열쇠는 **종료일 하나**다. 시작일까지 묶으면 안 된다: 태그마다, 또 매출과
    영업이익 사이에도 시작일이 하루씩 어긋날 때가 있어서 같은 분기가 둘로 갈라지고
    영업이익이 매출에 안 붙는다.

    같은 분기가 10-Q 와 나중의 10-K 에 중복해 실린다. 나중 제출본이 정정된 값이므로
    뒤에 나온 것으로 덮는다(응답이 제출 순서대로 온다).
    """
    q, a = {}, {}
    for x in units:
        s, e, v = x.get("start"), x.get("end"), x.get("val")
        if not s or not e or v is None or e < SINCE:
            continue
        days = (date.fromisoformat(e) - date.fromisoformat(s)).days
        if 80 <= days <= 100:
            q[e] = v
        elif 350 <= days <= 380:
            a[e] = v
    return q, a


def label_of(end):
    """분기말 날짜 -> '2Q26' 처럼 역년 기준 이름. 회원님이 보는 표기법이다."""
    d = date.fromisoformat(end)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def biggest_unit(d):
    """어느 통화로 담긴 값을 쓸지 고른다.

    미국 국내 기업은 USD 뿐이지만 **외국 기업은 본국 통화로 낸다** — 도요타는
    JPY, 노보노디스크는 DKK. USD 만 들여다보면 그 회사들이 통째로 빈다.
    (환산하지 않는다. 몇 년치를 오늘 환율로 바꾸면 매출 추세가 아니라
    환율 추세가 된다.) 값이 가장 많은 통화를 쓴다 — 외국 기업의 USD 는
    맨 뒷해만 붙여 주는 참고 환산인 경우가 많아서다.
    """
    best, cur = [], ""
    for k, v in (d.get("units") or {}).items():
        if len(v) > len(best):
            best, cur = v, k
    return best, cur


def one(cik, ns, tag):
    """태그 하나 -> (분기, 연간, 통화). 그 회사가 안 쓰는 태그면 빈 것."""
    for wait in BACKOFF:
        if wait:
            print(f"      SEC 가 막았다. {wait}초 쉬고 다시 ({tag})",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
        try:
            d = get(CONCEPT.format(cik=cik, ns=ns, tag=tag), SEC_UA)
        except Throttled:
            continue
        finally:
            time.sleep(0.15)   # SEC 는 초당 10건까지. 넉넉히 아래로 둔다.
        if d is None:
            return {}, {}, ""              # 404 — 이 회사는 이 태그를 안 쓴다
        units, cur = biggest_unit(d)
        q, a = quarters(units)
        return q, a, cur
    raise Throttled(f"{tag}: 네 번 다 실패")


def compatible(base, add):
    """두 태그가 같은 것을 재고 있나. 겹치는 기간의 값이 맞으면 같은 줄로 본다.

    태그를 마구 합치면 엉뚱한 줄이 섞여 들어온다(부문 매출이나 다른 정의).
    겹치는 데가 있는데 값이 어긋나면 그 태그는 통째로 버린다. 겹치는 데가
    아예 없으면(옛 태그와 새 태그가 시기를 나눠 갖는 경우) 받아들인다 —
    엔비디아가 그 경우다.
    """
    shared = [k for k in add if k in base]
    if not shared:
        return True
    for k in shared:
        b, a = base[k], add[k]
        if not b:
            continue
        if abs(a - b) / abs(b) > 0.05:
            return False
    return True


def merged(cik, ns, tags, into=None, enough=0):
    """여러 태그를 훑어 합친다. 먼저 걸리는 하나만 쓰면 안 된다 —
    회사가 도중에 태그를 갈아타기 때문이다. 엔비디아가 그랬다: 옛 태그에는
    2020년까지만 있어서, 첫 태그에서 멈추니 6분기밖에 안 나왔다.
    이미 채워진 분기는 건드리지 않는다(앞 태그가 더 정확한 표현이다).

    into 를 주면 거기에 이어 붙인다 — 회계 기준을 갈아탄 회사는 us-gaap 과
    IFRS 를 한 줄로 이어야 한다.
    enough 를 주면 그만큼 모였을 때 남은 태그를 건너뛴다."""
    mq, ma, cur = into if into else ({}, {}, "")
    for tag in tags:
        q, a, c = one(cik, ns, tag)
        if not compatible(mq, q) or not compatible(ma, a):
            continue                       # 다른 것을 재는 태그다. 섞지 않는다.
        cur = cur or c
        for k, v in q.items():
            mq.setdefault(k, v)
        for k, v in a.items():
            ma.setdefault(k, v)
        if enough and len(mq) >= enough:
            break
    return mq, ma, cur


def thin(rev_q, rev_a):
    """더 찾아볼 만큼 얇은가. 분기 12개(3년) 또는 연간 6개면 충분하다고 본다."""
    return len(rev_q) < 12 and len(rev_a) < 6


def series(cik):
    """한 종목의 분기 매출·영업이익·순이익 시계열.

    미국 기준(us-gaap)과 국제 기준(IFRS)을 **둘 다** 본다. 없으면 넘어가는 게
    아니라, 모자라면 더 찾는다 — 회사가 도중에 기준을 갈아타기 때문이다.
    도요타는 2021년에, 소니는 2022년에 IFRS 로 옮겼다. us-gaap 에서 몇 개
    건졌다고 거기서 멈추면 그 뒤 몇 해가 통째로 빈다(실제로 도요타가
    2019~2020 두 해만 나왔다).
    """
    got = merged(cik, "us-gaap", REV_CORE, enough=24)
    if thin(got[0], got[1]):
        got = merged(cik, "us-gaap", REV_MORE, into=got, enough=24)   # 은행 등
    used_ifrs = False
    if thin(got[0], got[1]):
        before = (len(got[0]), len(got[1]))
        got = merged(cik, "ifrs-full", IFRS_REV, into=got, enough=24)
        used_ifrs = (len(got[0]), len(got[1])) != before
    rev_q, rev_a, cur = got
    if not rev_q and not rev_a:
        return None

    # 영업이익·순이익도 매출을 찾은 쪽 기준으로 묻고, 기준을 갈아탄 회사는 둘 다.
    opi_q, opi_a, _ = one(cik, "us-gaap", OPI_TAG)
    ni_q, ni_a, _ = one(cik, "us-gaap", NI_TAG)
    if used_ifrs:
        for tag, tq, ta in ((IFRS_OPI, opi_q, opi_a), (IFRS_NI, ni_q, ni_a)):
            q2, a2, _ = one(cik, "ifrs-full", tag)
            for k, v in q2.items():
                tq.setdefault(k, v)
            for k, v in a2.items():
                ta.setdefault(k, v)

    # 분기가 있으면 분기로, 없으면(20-F 만 내는 외국 기업) 연간으로 낸다.
    # 다만 분기가 어쩌다 두어 개 섞여 있는 회사가 있다 — 그걸 붙잡으면 8년치
    # 연간을 버리고 막대 두 개짜리 그림을 그리게 된다. 분기는 여섯 개는 돼야 쓴다.
    use_q = len(rev_q) >= 6 or (bool(rev_q) and not rev_a)
    rev, opi, ni = (rev_q, opi_q, ni_q) if use_q else (rev_a, opi_a, ni_a)
    ends = sorted(rev)
    if not ends:
        return None
    return {
        "v": FIN_VER,
        "freq": "Q" if use_q else "A",
        "cur": cur or "USD",
        "src": "sec",
        "points": [{
            "label": label_of(e) if use_q else str(date.fromisoformat(e).year),
            "end": e,
            "rev": rev[e],
            "opi": opi.get(e),
            "ni": ni.get(e),
        } for e in ends],
    }


def reported(sym):
    """발표 완료 여부와 최근 EPS. UpcomingQuarter 는 아직 안 나온 분기다."""
    try:
        d = get(NAS_EPS.format(sym=sym), NAS_UA)
    except Exception:
        return None
    eps = (d.get("data") or {}).get("earningsPerShare") or []
    done, upcoming = [], None
    for e in eps:
        row = {"period": e.get("period"), "consensus": e.get("consensus"),
               "actual": e.get("earnings")}
        if e.get("type") == "UpcomingQuarter":
            upcoming = row
        elif row["actual"]:
            done.append(row)
    return {"done": done[-8:], "upcoming": upcoming} if (done or upcoming) else None


def targets():
    """{종목: (시총, 오늘까지 며칠)} — '며칠'은 가장 가까운 발표일까지의 거리다.

    발표일 언저리 종목은 값이 자주 바뀌므로 더 자주 다시 받아야 한다.
    """
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    today = date.today()
    out = {}
    for r in d.get("rows", []):
        c = r.get("code")
        if not c:
            continue
        cap = r.get("cap") or 0
        try:
            gap = abs((date.fromisoformat(r.get("date") or "") - today).days)
        except ValueError:
            gap = 9999
        old_cap, old_gap = out.get(c, (0, 9999))
        out[c] = (max(cap, old_cap), min(gap, old_gap))
    return out


def queue(old, cand):
    """이번 실행에서 받을 종목을 순서대로 고른다.

    0순위 아직 못 받았거나 저장 형식이 헌 것
    1순위 발표일 언저리인데 오늘 아직 안 받은 것 — 값이 지금 바뀌는 종목이다
    2순위 받은 지 오래된 것
    3순위 지난번에 아무것도 없던 것 — 아주 가끔만 다시 들춰본다
    같은 순위 안에서는 시가총액이 큰 쪽부터.
    """
    today = date.today().isoformat()
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 4)).isoformat()
    picks = []
    for sym, (cap, gap) in cand.items():
        rec = old.get(sym)
        empty = bool(rec) and not rec.get("points") and not rec.get("eps")
        if not rec or rec.get("v") != FIN_VER:
            pri = 0
        else:
            ts = rec.get("ts") or ""
            if empty:
                if ts >= cold:
                    continue      # 자료가 없는 종목이다. 자주 두드리지 않는다.
                pri = 3
            elif gap <= NEAR_DAYS and ts < today:
                pri = 1
            elif ts < stale:
                pri = 2
            else:
                continue          # 아직 싱싱하다. 건너뛴다.
        picks.append((pri, -cap, sym))
    picks.sort()
    return [s for _, _, s in picks]


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    print("  티커→CIK 목록 받는 중")
    cikmap = {}
    for v in get(TICKERS, SEC_UA).values():
        cikmap[v["ticker"].upper()] = v["cik_str"]
    print(f"  {len(cikmap):,}종목")

    cand = targets()
    pending = queue(old, cand)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개 "
          f"(가진 것 {len(old):,}개 / 후보 {len(cand):,}개)")

    today = date.today().isoformat()
    stocks = dict(old)
    got, blocked, streak = 0, 0, 0
    for i, sym in enumerate(todo):
        rec = {}
        cik = cikmap.get(re.sub(r"[^A-Z.]", "", sym.upper()))
        if cik:
            try:
                s = series(cik)
                streak = 0
                if s:
                    rec.update(s)
            except Throttled as e:
                # 막힌 것은 '자료 없음'이 아니다. 기록을 건드리지 않고 넘어가
                # 다음 실행에서 다시 물어본다. 여기서 ts 를 찍어 버리면
                # 잠깐 막혔을 뿐인 종목을 이레 동안 안 물어보게 된다.
                blocked += 1
                streak += 1
                print(f"    {sym} SEC 가 막았다: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print(f"  연속 {streak}종목이 막혔다. 이번 실행은 여기서 접는다.",
                          file=sys.stderr, flush=True)
                    break
                continue
            except Exception as e:
                print(f"    {sym} 재무 실패: {e}", file=sys.stderr)
        r = reported(sym)
        if r:
            rec["eps"] = r
        if rec:
            got += 1
        else:
            # 아무것도 못 받았다. 있던 것을 지우지는 않되 '못 받았다'고 적어 둔다.
            # 이 표시가 없으면 SEC 에 자료가 없는 종목이 매 실행 맨 앞을 차지해
            # 줄이 앞으로 나아가지 않는다.
            rec = dict(stocks.get(sym) or {})
            rec["none"] = 1
        rec["v"] = FIN_VER
        rec["ts"] = today
        stocks[sym] = rec
        if i % 25 == 24:
            print(f"    {i+1}/{len(todo)} (확보 {got})", flush=True)
            OUT.write_text(json.dumps({"stocks": stocks}, ensure_ascii=False),
                           encoding="utf-8")
        time.sleep(0.2)

    have = {k: v for k, v in stocks.items() if v.get("points") or v.get("eps")}
    payload = {
        "source": "SEC XBRL (재무 시계열) + Nasdaq (발표 완료·EPS)",
        "note": ("미국 국내 기업만 분기가 있다. SEA·알리바바 같은 외국 기업은 "
                 "SEC 에 20-F(연 1회)만 내므로 연간만 담긴다."),
        "count": len(have),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    nq = sum(1 for v in have.values() if v.get("freq") == "Q")
    na = sum(1 for v in have.values() if v.get("freq") == "A")
    ne = sum(1 for v in have.values() if v.get("eps"))
    print(f"\n{len(have):,}종목 -> {OUT} (자료 없는 종목 {len(stocks)-len(have):,} 표시만)")
    if blocked:
        print(f"  SEC 가 막아서 못 받은 종목 {blocked:,}개 — 다음 실행에서 다시 받는다")
    print(f"  분기 시계열 {nq:,} · 연간만 {na:,} · EPS(발표완료) {ne:,}")

    # 눈으로 한 번 본다. 태그를 갈아탄 회사를 놓치면 여기서 짧게 나온다.
    short = [(len(v["points"]), k) for k, v in have.items()
             if v.get("freq") == "Q" and len(v.get("points") or []) < 12]
    for k in ("AAPL", "NVDA", "MSFT", "AMZN", "GOOGL"):
        v = have.get(k)
        if v and v.get("points"):
            p = v["points"]
            print(f"  {k}: {v['freq']} {len(p)}개 {p[0]['label']}~{p[-1]['label']}")
    if short:
        short.sort()
        head = " ".join(f"{s}({n})" for n, s in short[:12])
        print(f"  ⚠ 분기가 12개 미만인 종목 {len(short)}개: {head}")


if __name__ == "__main__":
    main()
