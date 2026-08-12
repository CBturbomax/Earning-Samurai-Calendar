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
두세 배가 된다. **축 하나만** 골라 쓴다.

고르는 차례는 사업부문 -> 제품·서비스 -> 지역이다. 사업부문이 없는 회사(애플의
영업부문은 지역이다)는 제품으로 내려간다. 어느 축을 썼는지 화면에 적는다.

**두 축이 걸린 행은 따로 담아 두었다가 필요한 회사에만 쓴다.** 엑슨은 부문별
매출이 늘 `BusinessSegments;Geographical` 로 오고 한 축짜리 행은 몇 줄뿐이다.
록히드마틴은 거기에 제품/서비스·주요고객까지 겹쳐 있다. 그래서
(1) 한 축짜리로 부문이 둘 이상 나오면 그쪽을 쓰고,
(2) 아니면 두 축 행을 쓰되 **아래 축을 하나만** 골라 더한다(셋을 다 더하면 세 배),
(3) 아래 축은 그 부문을 남김없이 나누는 것(제품·지역)만 쓴다 — 주요고객은 큰
    고객만 적은 것이라 더해도 합계가 안 된다.

**상계 행을 담지 않는다.** `ConsolidationItems=IntersegmentEliminations` 는 부문
사이 거래를 빼는 음수 행이다. `ConsolidationItems` 는 `OperatingSegments` 일
때만 받고, 아예 없어도 받는다.

**이름만 바꾼 부문을 합친다.** AMD 가 `DataCenter` 를 `Datacenter` 로 고쳤는데,
새 이름에는 결산 분기 연간값만 있어 되돌릴 앞 분기가 없다. 대소문자·띄어쓰기·
`and` 를 지운 이름으로 같은 것인지 보고 합친다(값이 어긋나면 합치지 않는다).

**태그를 섞지 않는다.** 3분기는 A 태그, 연간은 B 태그로 적힌 회사에서
'연간 − 앞 세 분기'가 엉뚱한 값을 낸다. 태그마다 따로 되돌린다.

**누계를 분기로 되돌린다.** `qtrs` 가 기간 길이다(1=분기, 4=연간). 10-Q 는 대개
3개월과 누계를 함께 싣지만, 결산 분기는 10-K 에 **연간 값으로만** 실린다.
총매출에서 겪은 것과 같은 문제라 같은 방법으로 되살린다 —
12개월 − 같은 날 시작한 9개월, 그것이 없으면 연간 − 앞 세 분기.

**0 과 음수는 담지 않는다.** 0 은 '그 부문이 없던 때'고, 음수는 조정 항목이다.

## 언제 도는가

SEC 는 이 zip 을 **분기에 한 번** 낸다. 그래서 이 수집기도 분기에 한 번만 일한다 —
받아둔 분기 목록이 그대로면 아무것도 안 하고 곧장 끝낸다. 새 zip 이 나오면 그때
열두 분기를 통째로 다시 훑는다(1GB · 2분쯤). 누계를 되돌리려면 여러 분기를
한꺼번에 들고 있어야 해서 나눠 받지 않는다.

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

# 2: 아래 축을 하나만 고른다 · 종류주에 전부 실어 준다 · 소계는 넷 이상일 때만 뺀다
# 3: 한 축짜리 행이 몇 줄 섞여 있어도 두 축 행을 버리지 않는다(엑슨)
# 4: 더 잘게 쪼갠 쪽을 쓴다 · '부문 합계' 줄을 뺀다(셰브런)
SEG_SEC_VER = 4
# 열두 분기(3년)면 화면에 그리는 스물두 칸의 절반을 넘고, 큰 종목은
# stockanalysis 가 스무 분기를 채워 준다. 한 분기 zip 이 85MB 라 열여섯으로
# 늘리면 한 번에 1.4GB 다 — 남의 서버에서 그만큼 받을 이유가 없다.
QUARTERS = int(os.environ.get("SEG_SEC_QUARTERS", "12"))
MIN_PTS = int(os.environ.get("SEG_SEC_MIN_PTS", "4"))      # 이보다 적으면 안 싣는다
ZIP_PAUSE = float(os.environ.get("SEG_SEC_PAUSE", "2"))    # zip 사이 쉬는 시간
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
# 부문을 **남김없이** 나누는 아래 축. 이것으로만 더해 부문 합계를 만든다.
SUB_AXES = {"ProductOrService", "Geographical"}

# 부문이 아니라 조정·상계 줄. 담으면 매출이 부풀거나 음수가 섞인다.
# `Aggregation` 은 셰브런이 쓴다 —
# `ReportableSegmentAggregationBeforeOtherOperatingSegment` 는 부문이 아니라
# **부문을 다 더한 값**이다. 그대로 두면 화면에 부문 대신 그 한 줄과
# 'All Other' 만 뜬다. ('Aggregates' 는 안 걸린다 — 벌컨머티리얼즈의 골재 부문.)
BAD_MEMBER = re.compile(
    r"Elimination|Intersegment|Reconcil|Consolidat|SegmentTotal|Aggregation|"
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
    다시 쪼갠 값이라 그대로 쌓으면 겹친다. 그래서 아래 축 이름을 함께 돌려주어
    따로 담아 두었다가, **한 축짜리로 부문이 둘도 안 나오는 회사**에만 쓴다 —
    엑슨·록히드마틴이 그렇다. 아래 축으로 더하면 부문 합계가 나온다.

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
    # **아래 축은 빠짐없이 갈라야 한다.** 제품/서비스와 지역은 그 부문을 남김없이
    # 나눈 것이라 더하면 부문 합계가 된다. `MajorCustomers`(주요 고객)는 큰
    # 고객만 적은 것이라 더해도 합계가 안 되고, 그걸 부문 매출이라 적으면
    # 실제보다 작게 나온다. 록히드마틴에 그 축이 섞여 있었다.
    if b[0] not in SUB_AXES:
        return None
    if BAD_MEMBER.search(a[1]) or BAD_MEMBER.search(b[1]):
        return None
    return a[0], a[1], b[0]


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
            axis, member, sub_axis = got
            cik, filed = sub
            tag = p[it]
            if sub_axis == "solo":
                slot = (facts.setdefault(cik, {}).setdefault(axis, {})
                        .setdefault(member, {}).setdefault(tag, {}))
                key = (p[idd], qtrs)
                old = slot.get(key)
                if old is None or filed > old[1]:   # 나중에 낸 서류가 이긴다
                    slot[key] = (val, filed)
                    kept += 1
            else:
                # 아래 축(제품·지역)으로 갈린 값들. **서류 한 장 안에서** 더해야
                # 부문 합계가 된다 — 서류가 다르면 같은 분기를 두 번 더한다.
                # 그렇다고 (접수번호, 종료일, 길이)로 담으면 서류 수만큼 늘어나
                # 자료가 몇 배로 부푼다(실제로 실행이 메모리에 눌려 기어갔다).
                # 그래서 칸 하나만 두고 **더 나중 서류가 오면 그 자리를 비운다.**
                # num.txt 는 접수번호 순이라 한 서류의 줄이 붙어 있고, zip 도
                # 오래된 분기부터 넣으므로 나중 서류가 늘 뒤에 온다.
                #
                # 처음에는 '한 축짜리 행이 이미 있으면 담지 않는다'로 걸렀는데,
                # 그러면 엑슨이 통째로 빠진다 — 어느 한 분기에 한 축짜리 행이
                # 몇 줄 섞여 있으면 그 뒤로 두 축 행을 하나도 안 담고, 그 몇
                # 줄로는 부문이 둘도 안 나온다. 지금은 다 담고 **쓸지 말지는
                # 아래에서** 가린다.
                #
                # **아래 축마다 따로 담는다.** 한 회사가 같은 부문을 제품으로도,
                # 지역으로도 갈라 낸다(록히드마틴). 한 칸에 다 더하면 부문 매출이
                # 곱절이 된다.
                slot = (pairs.setdefault(cik, {}).setdefault(axis, {})
                        .setdefault(sub_axis, {})
                        .setdefault(member, {}).setdefault(tag, {}))
                key = (p[idd], qtrs)
                cur = slot.get(key)
                if cur is None or filed > cur[1]:
                    slot[key] = (val, filed, p[ia])
                elif cur[2] == p[ia]:
                    slot[key] = (cur[0] + val, cur[1], cur[2])
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
        # **부문이 셋뿐이면 손대지 않는다.** 셋이면 '하나가 나머지 둘의 합'이
        # 우연히 맞을 수 있는데(엑슨의 업스트림이 그랬다), 그걸 소계로 보고
        # 빼면 진짜 부문이 조용히 사라진다. 이 저장소에서 제일 하면 안 되는 일이다.
        # 남겨서 매출이 부풀면 `seg_fit` 이 그 종목을 안 실을 뿐이다.
        if len(names) < 4:
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


def usable(series):
    """쓸 만한 부문 묶음인가. (분기 수, 부문 수) 를 돌려준다. 아니면 None."""
    if len(series) < 2:
        return None                           # 부문이 하나뿐이면 나눌 게 없다
    ends = set()
    for s in series.values():
        ends |= set(s)
    if len(ends) < MIN_PTS:
        return None
    return len(ends), len(series), sorted(ends)


def candidates(by_axis, by_pair):
    """축마다 쓸 만한 부문 묶음 하나씩. {축: (series, 종료일들)}

    한 축짜리 행이 있으면 그쪽이 맞다. 없거나 **부문이 둘도 안 될 때**만 두 축
    행을 쓴다 — 엑슨은 어느 분기에 한 축짜리 행이 몇 줄 섞여 있는데, 그것만으로
    가리면 그 몇 줄 때문에 진짜 사업부문(업스트림·에너지제품·화학)을 통째로
    버리게 된다.

    두 축 행은 **아래 축을 하나만** 고른다. 록히드마틴은 같은 부문을 제품으로도
    지역으로도 갈라 내므로 다 더하면 매출이 곱절이 된다.
    """
    out = {}
    for axis, members in by_axis.items():
        s = series_of(members)
        got = usable(s)
        if got:
            out[axis] = (s, got)

    for axis, subs in by_pair.items():
        best = out.get(axis)
        for _sub, members in subs.items():
            s = series_of({m: {t: {k: (v[0], v[1]) for k, v in raw.items()}
                               for t, raw in by_tag.items()}
                           for m, by_tag in members.items()})
            got = usable(s)
            # **더 잘게 쪼갠 쪽을 쓴다.** 셰브런은 한 축짜리에 '부문 합계'와
            # 'All Other' 두 줄뿐인데 두 축 쪽에는 업스트림·다운스트림이 있다.
            # 한 축짜리라고 무조건 이기게 두면 화면에 아무 뜻 없는 두 줄이 뜬다.
            if got and (best is None or (got[1], got[0]) > (best[1][1], best[1][0])):
                best = (s, got)
        if best:
            out[axis] = best
    return out


def build_stock(by_axis, by_pair):
    """축 하나를 고른다. 사업부문 > 제품 > 지역, 같은 등급이면 분기가 많은 쪽."""
    cand = candidates(by_axis, by_pair)
    if not cand:
        return None
    axis = max(cand, key=lambda a: (axis_rank(a), cand[a][1][0], cand[a][1][1]))
    series, (_n, _m, ends) = cand[axis]
    rank = axis_rank(axis)

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
        # **한 CIK 에 종류주가 여럿이면 전부에 실어 준다.** 대표 하나만 골랐더니
        # 버크셔 A 에는 부문이 뜨고 B 에는 안 떴다 — 같은 회사인데 화면이 갈렸다.
        by_cik.setdefault(c, []).append(sym)
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
    for i, q in enumerate(quarters):
        blob = None
        if i:
            time.sleep(ZIP_PAUSE)            # 남의 서버다. 몰아치지 않는다.
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
        if not rec:
            continue
        rec = dedupe_names(rec)
        for sym in by_cik.get(cik, []):
            stocks[sym] = rec

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
