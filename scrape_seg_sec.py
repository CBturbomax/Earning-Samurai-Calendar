# -*- coding: utf-8 -*-
"""
사업부별 매출 — SEC 벌크 재무자료(Financial Statement Data Sets)에서 전 종목

stockanalysis 에서 받던 것은 **한 종목에 요청 한 번**이라 시총 상위부터 조금씩
채울 수밖에 없었다. 1,350종목을 두드려 223종목밖에 못 얻었다(부문을 안 나누는
회사가 대부분이고, 나머지는 아직 차례가 안 왔다). 회원님이 열어 보는 종목은
거의 늘 비어 있었다.

**SEC 가 부문별 수치를 공식으로 공개한다.** 다만 우리가 쓰던
`companyfacts`·`companyconcept` API 는 부문 축(dimension)을 떨어뜨리고 연결
합계만 준다 — 그래서 "SEC 로는 못 한다"고 적어 두었었는데, 그건 **API 얘기**였다.
분기마다 내는 벌크 zip 의 `num.txt` 에는 `segments` 칸이 그대로 있다.

    https://www.sec.gov/files/dera/data/financial-statement-data-sets/2026q1.zip
    num.txt 열: adsh tag version ddate qtrs uom segments coreg value footnote
    segments 예: BusinessSegments=Datacenter;ConsolidationItems=OperatingSegments;

zip 하나(85MB)에 그 분기에 접수된 **모든 제출 서류**가 들어 있다. 부문 축이 달린
매출 행만 세어도 2026q1 한 분기에 15만 건이다. 종목당 요청이 아니라 **분기당
요청 한 번**이라 미국 전 종목이 한꺼번에 들어온다.

## 조심할 것

**축을 섞지 않는다.** 같은 회사가 사업부문·제품·지역으로 여러 번 쪼개 낸다.
AMD 한 분기에 `BusinessSegments=Datacenter`, `Geographical=US`,
`BusinessSegments=Gaming;ProductOrService=Gaming` 이 다 있다. 섞어 쌓으면 매출이
두세 배가 된다. **축 하나만** 골라 쓰고, 두 축이 걸린 행은 버린다.

고르는 차례는 사업부문 -> 제품·서비스 -> 지역이다. 사업부문이 없는 회사(애플의
영업부문은 지역이다)는 제품으로 내려간다. 어느 축을 썼는지 화면에 적는다.

**상계 행을 담지 않는다.** `ConsolidationItems=IntersegmentEliminations` 는 부문
사이 거래를 빼는 음수 행이다. `ConsolidationItems` 는 `OperatingSegments` 일
때만 받고, 아예 없어도 받는다.

**누계를 분기로 되돌린다.** `qtrs` 가 기간 길이다(1=분기, 4=연간). 10-Q 는 대개
3개월과 누계를 함께 싣지만, 결산 분기는 10-K 에 **연간 값으로만** 실린다.
총매출에서 겪은 것과 같은 문제라 같은 방법으로 되살린다 —
12개월 − 같은 날 시작한 9개월, 그것이 없으면 연간 − 앞 세 분기.

**0 과 음수는 담지 않는다.** 0 은 '그 부문이 없던 때'고, 음수는 조정 항목이다.

## 언제 도는가

SEC 는 이 zip 을 **분기에 한 번** 낸다. 그래서 이 수집기도 분기에 한 번만 일한다 —
받아둔 분기 목록이 그대로면 아무것도 안 하고 곧장 끝낸다. 새 zip 이 나오면 그때
열여섯 분기를 통째로 다시 훑는다(4분쯤). 누계를 되돌리려면 여러 분기를 한꺼번에
들고 있어야 해서 나눠 받지 않는다.

벌크는 **접수된 분기 기준**이라 가장 최근 한두 분기는 아직 안 들어 있다
(2026-08-12 현재 2026q2 는 404). 그 구간은 stockanalysis 쪽이 메운다.

결과: data/segments_sec.json
"""
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_sec.json"

BULK = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{q}.zip"
SEC_UA = os.environ.get("SEC_UA", "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)")
TICKERS = "https://www.sec.gov/files/company_tickers.json"

SEG_SEC_VER = 1
QUARTERS = int(os.environ.get("SEG_SEC_QUARTERS", "16"))   # 몇 분기치 zip 을 훑나
MIN_PTS = int(os.environ.get("SEG_SEC_MIN_PTS", "4"))      # 이보다 적으면 안 싣는다
BACKOFF = (0, 10, 45)

# 매출 태그. scrape_fin.py 의 것과 같은 얼개인데, 부문 행에서 실제로 많이 쓰이는
# 것을 프로브로 세어 보고 순서를 매겼다(2026q1 기준):
#   RevenueFromContractWithCustomerExcludingAssessedTax 85,977 · Revenues 38,304
#   RevenueFromContractWithCustomerIncludingAssessedTax 11,826 · Revenue 7,959
# 앞엣것일수록 정확한 표현이라, 한 종목이 여러 태그를 쓰면 앞의 것만 쓴다.
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenue",
            "RevenueFromContractsWithCustomers",
            "SalesRevenueNet",
            "RevenueFromSaleOfGoods",
            "RegulatedAndUnregulatedOperatingRevenue",
            "RevenuesNetOfInterestExpense",
            "InsuranceRevenue"]
REV_RANK = {t: i for i, t in enumerate(REV_TAGS)}

# 축 이름은 DERA 가 앞뒤를 떼고 준다(us-gaap:StatementBusinessSegmentsAxis ->
# BusinessSegments). 그래도 다른 형태로 올 때를 대비해 한 번 더 다듬는다.
AXIS_PRI = {"ProductOrService": 2, "Geographical": 1, "StatementGeographical": 1}
AXIS_KO = {3: "사업부문", 2: "제품·서비스", 1: "지역"}

# 부문이 아니라 조정·상계 줄. 담으면 매출이 부풀거나 음수가 섞인다.
BAD_MEMBER = re.compile(
    r"Elimination|Intersegment|Reconcil|Consolidat|SegmentTotal|"
    r"TotalSegment|Unallocated|MaterialReconcilingItems", re.I)
SMALL_WORDS = {"and", "or", "of", "the", "for", "in", "to", "a", "an"}
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


class Blocked(Exception):
    """SEC 가 막았거나 그물이 끊겼다. '자료가 없다'와 다른 일이다."""


def fetch(url, timeout=240):
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return None                      # 아직 안 나온 분기
        raise Blocked(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Blocked(str(e))


def exists(q):
    """HEAD 로 있는지만 본다. 85MB 를 헛되이 받지 않는다."""
    req = urllib.request.Request(BULK.format(q=q), method="HEAD",
                                 headers={"User-Agent": SEC_UA})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return False
        raise Blocked(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise Blocked(str(e))


def qid(y, q):
    return f"{y}q{q}"


def next_qid(q):
    y, n = int(q[:4]), int(q[-1])
    return qid(y + 1, 1) if n == 4 else qid(y, n + 1)


def recent_quarters(n):
    """지금 나와 있는 것 중 최근 n개. 머리 쪽 두엇은 아직 안 나왔을 수 있다."""
    t = date.today()
    y, q = t.year, (t.month - 1) // 3 + 1
    out, tries = [], 0
    while len(out) < n and tries < n + 6:
        tries += 1
        if exists(qid(y, q)):
            out.append(qid(y, q))
        elif out:
            break                            # 중간에 구멍은 없다. 머리만 비어 있다.
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return list(reversed(out))               # 오래된 것부터


# ── num.txt 한 줄 읽기 ────────────────────────────────────────────────────

def norm_axis(a):
    a = a.split(":")[-1]
    if a.endswith("Axis"):
        a = a[:-4]
    if a.startswith("Statement"):
        a = a[len("Statement"):]
    return a


def axis_rank(a):
    """사업부문 3 · 제품 2 · 지역 1 · 그 밖 0(안 쓴다)."""
    if "Segment" in a:
        return 3
    return AXIS_PRI.get(a, 0)


def parse_seg(segments):
    """'A=x;ConsolidationItems=OperatingSegments;' -> ('A', 'x', 'solo').

    축이 둘 걸린 행(BusinessSegments=Gaming;ProductOrService=Gaming)은 부문 안을
    다시 쪼갠 값이라 그대로 쌓으면 겹친다. 그래서 따로 담아('pair') **부문만
    남는 행이 아예 없는 회사**에만 쓴다 — 록히드마틴이 그렇다. 부문별 매출을
    전부 '제품/서비스'로 한 번 더 갈라 내서, 한 축짜리 행이 하나도 없다.
    아래 축(제품·지역)으로 더하면 부문 합계가 나온다.

    못 쓰는 행이면 None.
    """
    pairs = []
    for part in segments.split(";"):
        if not part:
            continue
        k, _, v = part.partition("=")
        if not v:
            return None
        k = norm_axis(k)
        if k == "ConsolidationItems":
            # 부문 값이라고 못 박은 것만 받는다. 상계·조정 줄은 여기서 걸린다.
            if v.split(":")[-1] != "OperatingSegments":
                return None
            continue
        pairs.append((k, v.split(":")[-1]))

    if len(pairs) == 1:
        axis, member = pairs[0]
        if not axis_rank(axis) or BAD_MEMBER.search(member):
            return None
        return axis, member, "solo"

    if len(pairs) != 2:
        return None
    a, b = sorted(pairs, key=lambda p: -axis_rank(p[0]))
    if axis_rank(a[0]) <= axis_rank(b[0]):
        return None                          # 어느 쪽이 위인지 못 가른다
    if BAD_MEMBER.search(a[1]) or BAD_MEMBER.search(b[1]):
        return None
    return a[0], a[1], "pair"


def pretty(member):
    """'ClientAndGaming' -> 'Client and Gaming'. 머리글자 말(EMEA·US)은 붙여 둔다."""
    m = member
    for suf in ("Member", "Segments", "Segment"):
        while m.endswith(suf) and len(m) > len(suf):
            m = m[:-len(suf)]
    words = CAMEL.sub(" ", m).split()
    if not words:
        return member
    return " ".join(w if i == 0 or w.lower() not in SMALL_WORDS else w.lower()
                    for i, w in enumerate(words))


def month_end(y, m):
    return date(y, 12, 31) if m == 12 else date(y, m + 1, 1) - timedelta(days=1)


def shift_q(ds, k):
    """'20231231' 을 k 분기 옮긴다(음수면 뒤로). DERA 의 ddate 는 늘 월말이다."""
    y, m = int(ds[:4]), int(ds[4:6])
    m2 = m + 3 * k
    y += (m2 - 1) // 12
    return month_end(y, (m2 - 1) % 12 + 1).strftime("%Y%m%d")


def iso(ds):
    return f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"


# ── zip 하나 훑기 ────────────────────────────────────────────────────────

def scan(blob, want_cik, facts, pairs):
    """zip 한 덩이에서 부문별 매출 행을 뽑아 쌓는다.

    facts[cik][axis][member][tag][(ddate, qtrs)] = (value, filed)
    pairs[cik][axis][member][tag][(adsh, ddate, qtrs)] = 합계

    **태그를 섞지 않는다.** 한 회사가 도중에 매출 태그를 갈아타기도 하고, 두
    태그가 같은 분기를 다르게 적기도 한다. 섞어 놓고 누계를 빼면 엉뚱한 값이
    나온다 — 웨이스트매니지먼트의 4분기가 두 배로 튀었다.

    같은 값이 여러 서류에 실린다(10-K 가 지난해 것을 다시 싣는다). **나중에
    낸 서류**를 남긴다 — 정정하면 그쪽이 맞다.
    """
    z = zipfile.ZipFile(io.BytesIO(blob))

    # sub.txt: 접수번호 -> (CIK, 접수일). 우리 종목의 10-K/10-Q 만 남긴다.
    subs = {}
    with z.open("sub.txt") as f:
        cols = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
        ia, ic, ifm, ifd = (cols.index(c) for c in ("adsh", "cik", "form", "filed"))
        for raw in f:
            p = raw.decode("utf-8", "replace").rstrip("\r\n").split("\t")
            if len(p) <= ifd or not p[ifm].startswith("10-"):
                continue
            try:
                cik = int(p[ic])
            except ValueError:
                continue
            if cik in want_cik:
                subs[p[ia]] = (cik, p[ifd])
    if not subs:
        return 0

    kept = 0
    with z.open("num.txt") as f:
        cols = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
        try:
            ia, it, idd, iq, iu, isg, icg, iv = (
                cols.index(c) for c in
                ("adsh", "tag", "ddate", "qtrs", "uom", "segments", "coreg", "value"))
        except ValueError as e:
            raise Blocked(f"num.txt 모양이 달라졌다: {e}")
        for raw in f:
            p = raw.decode("utf-8", "replace").rstrip("\r\n").split("\t")
            if len(p) <= iv:
                continue
            seg = p[isg]
            if not seg or p[icg] or p[iu] != "USD":
                continue
            rank = REV_RANK.get(p[it])
            if rank is None:
                continue
            sub = subs.get(p[ia])
            if not sub:
                continue
            try:
                qtrs = int(p[iq])
                val = float(p[iv])
            except ValueError:
                continue
            if qtrs < 1 or qtrs > 4 or val <= 0:
                continue
            got = parse_seg(seg)
            if not got:
                continue
            axis, member, kind = got
            cik, filed = sub
            tag = p[it]
            if kind == "solo":
                slot = (facts.setdefault(cik, {}).setdefault(axis, {})
                        .setdefault(member, {}).setdefault(tag, {}))
                key = (p[idd], qtrs)
                old = slot.get(key)
                if old is None or filed > old[1]:   # 나중에 낸 서류가 이긴다
                    slot[key] = (val, filed)
                    kept += 1
            else:
                # 아래 축(제품·지역)으로 갈린 값들. 서류 한 장 안에서 더해야
                # 부문 합계가 된다 — 서류가 다르면 같은 분기를 두 번 더한다.
                slot = (pairs.setdefault(cik, {}).setdefault(axis, {})
                        .setdefault(member, {}).setdefault(tag, {}))
                key = (p[ia], p[idd], qtrs)
                cur = slot.get(key)
                slot[key] = ((cur[0] if cur else 0.0) + val, filed)
    return kept


# ── 쌓은 것을 분기 값으로 ────────────────────────────────────────────────

def quarterly(raw):
    """{(ddate, qtrs): (값, 접수일)} -> {ddate: 값}. 누계는 빼서 되돌린다."""
    plain = {k[0]: v[0] for k, v in raw.items() if k[1] == 1}

    # 누계 − 같은 날 시작한 한 분기 짧은 누계. 12개월 − 9개월 = 결산 분기.
    for (ds, q), v in raw.items():
        if q < 2 or ds in plain:
            continue
        prev = raw.get((shift_q(ds, -1), q - 1))
        if prev and v[0] > prev[0]:
            plain[ds] = v[0] - prev[0]

    # 그래도 없으면 연간 − 앞 세 분기. 10-Q 가 3개월치만 싣는 회사용이다.
    for (ds, q), v in raw.items():
        if q != 4 or ds in plain:
            continue
        back = [plain.get(shift_q(ds, -k)) for k in (1, 2, 3)]
        if all(back) and v[0] > sum(back):
            plain[ds] = v[0] - sum(back)
    return plain


def quarterly_merged(by_tag):
    """태그마다 따로 되돌린 뒤 합친다. 점이 가장 많은 태그가 뼈대다.

    태그를 섞어 놓고 누계를 빼면 안 된다 — 3분기는 A 태그로, 연간은 B 태그로
    적힌 회사에서 '연간 − 앞 세 분기'가 엉뚱한 값을 낸다. 뼈대 태그가 비운
    분기만 다른 태그로 메운다.
    """
    got = []
    for tag, raw in by_tag.items():
        s = quarterly(raw)
        if s:
            got.append((-len(s), REV_RANK.get(tag, 99), s))
    got.sort(key=lambda t: t[:2])
    out = {}
    for _n, _r, s in got:
        for k, v in s.items():
            out.setdefault(k, v)
    return out


NORM_DROP = re.compile(r"[^a-z0-9]|and")


def norm_name(m):
    """이름만 바뀐 같은 부문을 알아보는 열쇠.

    회사가 XBRL 이름을 슬쩍 고친다 — AMD 는 `DataCenter` 를 `Datacenter` 로,
    P&G 는 `FabricHomeCare` 를 `FabricandHomeCare` 로 바꿨다. 그러면 같은
    부문이 두 줄로 갈라지고, **새 이름에는 결산 분기 값만 있어서 되돌릴 앞
    분기가 없다** — AMD 의 데이터센터 4분기가 그렇게 통째로 비었다.
    대소문자·띄어쓰기·'and' 를 지운 것으로 같은 것인지 본다.
    """
    return NORM_DROP.sub("", pretty(m).lower())


def merge_members(members):
    """이름만 바뀐 부문을 하나로 합친다. 값이 어긋나면 합치지 않는다."""
    groups = {}
    for m, by_tag in members.items():
        groups.setdefault(norm_name(m), []).append((m, by_tag))

    out = {}
    for _key, items in groups.items():
        if len(items) == 1:
            out[items[0][0]] = items[0][1]
            continue
        # 점이 많은 쪽 이름을 남긴다 — 회사가 오래 쓴 이름이다.
        items.sort(key=lambda it: -sum(len(r) for r in it[1].values()))
        head, rest = items[0], items[1:]
        merged = {t: dict(r) for t, r in head[1].items()}
        for _m, by_tag in rest:
            for tag, raw in by_tag.items():
                slot = merged.setdefault(tag, {})
                for k, v in raw.items():
                    old = slot.get(k)
                    if old and abs(old[0] - v[0]) > abs(old[0]) * 0.01:
                        continue              # 값이 다르다. 같은 부문이 아니다.
                    if old is None or v[1] > old[1]:
                        slot[k] = v
        out[head[0]] = merged
    return out


def drop_subtotals(series):
    """다른 부문을 다 더한 것과 같은 줄은 소계다. 쌓으면 매출이 두 배가 된다.

    코스트코가 그렇다 — 'Product' 한 줄이 'Foods and Sundries'·'Non Foods'·
    'Fresh Foods'·'Other' 를 더한 값인데 표에 나란히 실린다. build.py 의
    `seg_fit` 이 나중에 걸러내기는 하지만, 그러면 **그 종목의 부문 차트가
    통째로 사라진다.** 소계 줄만 빼면 남길 수 있다.
    """
    for _ in range(2):
        names = list(series)
        if len(names) < 3:
            break
        worst = None
        for m in names:
            hit = tot = 0
            for e, v in series[m].items():
                others = sum(series[o].get(e) or 0 for o in names if o != m)
                if not v or not others:
                    continue
                tot += 1
                if abs(v - others) / v < 0.05:
                    hit += 1
            if tot >= 4 and hit / tot >= 0.6 and (worst is None or hit > worst[0]):
                worst = (hit, m)
        if not worst:
            break
        del series[worst[1]]
    return series


MIN_MEMBER_PTS = 3       # 분기가 이보다 적은 부문은 조각이라 싣지 않는다
MAX_MEMBERS = 10         # 색이 여덟이고, 열 줄이 넘으면 쌓은 막대를 못 읽는다


def series_of(members):
    """부문별 원자료 -> {부문: {종료일: 값}}. 합치고 거르는 일이 다 여기 있다."""
    series = {}
    for m, by_tag in merge_members(members).items():
        s = quarterly_merged(by_tag)
        if len(s) >= MIN_MEMBER_PTS:
            series[m] = s
    if len(series) > MAX_MEMBERS:
        big = sorted(series, key=lambda m: -sum(series[m].values()))[:MAX_MEMBERS]
        series = {m: series[m] for m in big}
    return drop_subtotals(series)


def pick_axis(by_axis):
    """축 하나를 고른다. 사업부문 > 제품 > 지역, 같은 등급이면 분기가 많은 쪽."""
    best = None
    for axis, members in by_axis.items():
        series = series_of(members)
        if len(series) < 2:
            continue                          # 부문이 하나뿐이면 나눌 게 없다
        ends = set()
        for s in series.values():
            ends |= set(s)
        if len(ends) < MIN_PTS:
            continue
        key = (axis_rank(axis), len(ends), len(series))
        if best is None or key > best[0]:
            best = (key, axis, series, sorted(ends))
    return best


def build_stock(by_axis, by_pair):
    """부문만 남는 행이 없으면(록히드마틴) 아래 축으로 더한 값을 쓴다."""
    got = pick_axis(by_axis)
    if not got and by_pair:
        # {(adsh, ddate, qtrs): 합} 을 {(ddate, qtrs): (값, 접수일)} 로 눕힌다.
        # 같은 분기가 여러 서류에 실리면 나중 것을 쓴다.
        flat = {}
        for axis, members in by_pair.items():
            for m, by_tag in members.items():
                for tag, raw in by_tag.items():
                    slot = (flat.setdefault(axis, {}).setdefault(m, {})
                            .setdefault(tag, {}))
                    for (_adsh, ds, q), v in raw.items():
                        old = slot.get((ds, q))
                        if old is None or v[1] > old[1]:
                            slot[(ds, q)] = v
        got = pick_axis(flat)
    if not got:
        return None
    (rank, _, _), _axis, series, ends = got

    # 마지막 분기의 값이 큰 순으로 놓는다 — 쌓은 막대의 아래쪽이 큰 부문이 된다.
    last = ends[-1]
    names = sorted(series, key=lambda m: (-(series[m].get(last) or 0), m))
    pts = [[iso(e)] + [series[m].get(e) for m in names] for e in ends]
    return {"v": SEG_SEC_VER, "axis": AXIS_KO.get(rank, "사업부문"),
            "names": [pretty(m) for m in names], "pts": pts}


def dedupe_names(rec):
    """다듬은 이름이 그래도 겹치면 뒤엣것에 번호를 붙인다."""
    seen, names = {}, []
    for n in rec["names"]:
        if n in seen:
            seen[n] += 1
            n = f"{n} ({seen[n]})"
        else:
            seen[n] = 1
        names.append(n)
    rec["names"] = names
    return rec


# ── 우리가 보는 종목 ─────────────────────────────────────────────────────

def want_tickers():
    p = HERE / "data" / "earnings_us.json"
    if not p.exists():
        return {}
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return {}
    caps = {}
    for r in rows:
        c = r.get("code")
        if c:
            caps[c] = max(caps.get(c, 0), r.get("cap") or 0)
    return caps


def cik_map():
    blob = fetch(TICKERS, timeout=60)
    if not blob:
        raise Blocked("티커 목록을 못 받았다")
    d = json.loads(blob.decode("utf-8", "replace"))
    return {v["ticker"].upper(): int(v["cik_str"]) for v in d.values()}


def cik_of(cikmap, sym):
    """나스닥은 BRK.B, SEC 는 BRK-B. 복수의결권은 같은 회사이고 CIK 도 하나다."""
    up = re.sub(r"[^A-Z.\-]", "", sym.upper())
    stem = up.split(".")[0].split("-")[0]
    for cand in (up, up.replace(".", "-"), stem, stem + "-A", stem + "-B"):
        if cand and cand in cikmap:
            return cikmap[cand]
    return None


def load_old():
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = load_old()

    # **먼저 값싼 확인부터.** 이 워크플로는 스무 분마다 돈다. SEC 는 이 zip 을
    # 분기에 한 번 낸다 — 나머지 스무 분마다 티커 목록을 받고 HEAD 를 스물두 번
    # 두드릴 이유가 없다. 다음 분기가 나왔는지만 **요청 한 번**으로 본다.
    if not probe and old.get("v") == SEG_SEC_VER and old.get("zips"):
        nxt = next_qid(old["zips"][-1])
        if not exists(nxt):
            print(f"  다음 분기({nxt})가 아직 안 나왔다. 아무것도 안 한다.")
            return
        print(f"  새 분기 {nxt} 가 나왔다. 다시 훑는다.")

    caps = want_tickers()
    if not caps:
        print("  earnings_us.json 이 없다. 할 일이 없다.")
        return
    cikmap = cik_map()
    want, by_cik = {}, {}
    for sym, cap in caps.items():
        c = cik_of(cikmap, sym)
        if not c:
            continue
        want[sym] = c
        # 한 CIK 에 종류주가 여럿이면 시총 큰 쪽을 대표로 삼는다.
        if c not in by_cik or cap > caps.get(by_cik[c], 0):
            by_cik[c] = sym
    print(f"  우리 종목 {len(caps):,}개 중 CIK 를 찾은 것 {len(want):,}개 "
          f"(회사 {len(by_cik):,}곳)")

    quarters = recent_quarters(QUARTERS)
    if not quarters:
        print("  벌크 zip 을 하나도 못 찾았다.")
        return
    print(f"  볼 분기: {' '.join(quarters)}")

    if not probe and old.get("zips") == quarters and old.get("v") == SEG_SEC_VER:
        print("  새로 나온 분기가 없다. 아무것도 안 한다.")
        return

    facts, pairs = {}, {}
    for q in quarters:
        blob = None
        for wait in BACKOFF:
            if wait:
                time.sleep(wait)
            blob = fetch(BULK.format(q=q))
            if blob:
                break
        if not blob:
            raise SystemExit(f"  {q} 를 못 받았다. 이번 실행은 접는다.")
        n = scan(blob, set(by_cik), facts, pairs)
        print(f"    {q}  {len(blob)/1e6:,.0f}MB  부문 행 {n:,}", flush=True)
        del blob

    stocks = {}
    for cik in set(facts) | set(pairs):
        rec = build_stock(facts.get(cik, {}), pairs.get(cik, {}))
        if rec:
            stocks[by_cik[cik]] = dedupe_names(rec)

    if probe:
        for sym in ("AAPL", "MSFT", "AMD", "RKLB", "KO", "WM", "JCI"):
            rec = stocks.get(sym)
            print(f"\n===== {sym}")
            if not rec:
                print("  부문 자료 없음")
                continue
            print(f"  [{rec['axis']}] {rec['names']}")
            print(f"  분기 {len(rec['pts'])}개  "
                  f"{rec['pts'][0][0]} ~ {rec['pts'][-1][0]}")
            for row in rec["pts"][-3:]:
                print("   ", row[0],
                      [f"{v/1e6:,.0f}M" if v else "-" for v in row[1:]])
        return

    payload = {
        "source": "SEC Financial Statement Data Sets (분기 벌크)",
        "note": ("축을 섞지 않는다 — 사업부문·제품·지역 중 하나만 쓴다. "
                 "누계는 빼서 분기로 되돌린다. 벌크는 접수 분기 기준이라 "
                 "가장 최근 한두 분기는 아직 안 들어 있다."),
        "v": SEG_SEC_VER,
        "zips": quarters,
        "ts": date.today().isoformat(),
        "count": len(stocks),
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)

    per = {}
    for rec in stocks.values():
        per[rec["axis"]] = per.get(rec["axis"], 0) + 1
    n = sorted(len(r["pts"]) for r in stocks.values())
    print(f"\n{len(stocks):,}종목 -> {OUT}  {per}")
    if n:
        print(f"  분기 수 중앙값 {n[len(n)//2]}개 (가장 적은 것 {n[0]} · 많은 것 {n[-1]})")


if __name__ == "__main__":
    main()
