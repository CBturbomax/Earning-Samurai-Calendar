# -*- coding: utf-8 -*-
"""
일본 실적 수치 — 발표 당일에 받는다. 출처: TDnet(적시공시) 결산단신 XBRL

**왜 따로 만드나.** stockanalysis 는 일본 실적을 며칠 뒤에 싣는다. 실제로 재봤다.

    소니   7/31 발표 -> 8/12 에 2Q26 있음   (12일 뒤)
    도요타 8/4  발표 -> 8/12 에 2Q26 있음   (8일 뒤)
    오늘 발표한 208종목 -> 6월 분기가 들어온 것 **0건**

수집 주기를 20분에서 1분으로 줄여도 소용없다. 소스에 없는 것은 못 가져온다.
미국은 SEC 에서 직접 받으니 일본도 그렇게 해야 한다.

**TDnet 이 일본판 EDGAR 다.** 회사가 실적을 발표하는 그 순간 결산단신(決算短信)을
여기에 올리고, 요약 XBRL 이 같이 붙는다. 장 마감 15시 발표면 15시에 올라온다.

  목록  https://www.release.tdnet.info/inbs/I_list_{쪽:03d}_{YYYYMMDD}.html
  XBRL  https://www.release.tdnet.info/inbs/{문서번호}.zip

요약 XBRL 은 `tse-ed-t`(일반사업)·`tse-re-t`(부동산)·`tse-qc-t` 같은 namespace 로
`NetSales`, `OperatingIncome` 을 담는다. 누적값이라 미국과 같은 문제가 있다 —
2분기 발표는 상반기 누적이므로 앞 분기를 빼야 분기 값이 된다.

표준 라이브러리만 쓴다(zipfile · xml.etree).

결과: data/financials_jp.json  (financials_intl.json 과 같은 꼴)
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).parent
OUT = HERE / "data" / "financials_jp.json"
SEG_OUT = HERE / "data" / "segments_jp.json"

LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day}.html"
ZIP_URL = "https://www.release.tdnet.info/inbs/{doc}.zip"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BACKOFF = (0, 5, 20, 60)
PAUSE = float(os.environ.get("JP_PAUSE", "0.5"))
BACK_DAYS = int(os.environ.get("JP_BACK_DAYS", "10"))   # 며칠치를 훑나
GIVE_UP_AFTER = 6
JP_VER = 1
# 2: 문맥을 zip 전체에서 모은다 · 수식어를 부문으로 읽지 않는다
# 3: 합계 줄을 뺀다(전사 합계·보고부문 계) · 문자가 든 증권코드 접두사
# 4: 원자료에 XBRL 이름을 그대로 담는다 — 이름 규칙을 고쳐도 다시 안 받는다
SEG_JP_VER = 4

# 결산단신인지 가리는 말. '決算短信' 이 들어가면 실적 발표다.
# (「業績予想の修正」 같은 것은 실적 발표가 아니라 예상 수정이라 뺀다.)
TANSHIN = re.compile(r"決算短信")
NOT_TANSHIN = re.compile(r"予想|修正|訂正|延期|中止|取消")
# 결산단신이라도 XBRL 이 없으면 수치를 못 뽑는다. zip 이 달린 줄만 쓴다.

# 목록 표의 한 줄. 실제 생김새는 이렇다(원문을 찍어 확인했다).
#
#   <td class="oddnew-L kjTime" noWrap>18:30</td>
#   <td class="oddnew-M kjCode" noWrap>389A0</td>
#   <td class="oddnew-M kjName" noWrap>Ｐ－八光オート  </td>
#   <td class="oddnew-M kjTitle" align="left"><a href="140120260812518506.pdf" ...>제목</a></td>
#   <td class="oddnew-M kjXbrl" noWrap align="center"> </td>
#
# **클래스 이름이 둘씩 붙어 있다**(`oddnew-M kjTitle`). 처음에 `class="kjTitle"`
# 로 잡았다가 하루 종일 0건을 봤다. 그리고 **XBRL 은 제목이 아니라 kjXbrl 칸**에
# 따로 달린다 — 결산단신에만 있고 「업적예상 수정」 같은 것에는 없다.
ROW_RE = re.compile(
    r'<td class="[^"]*kjTime"[^>]*>(?P<time>[^<]*)</td>\s*'
    r'<td class="[^"]*kjCode"[^>]*>(?P<code>[^<]*)</td>\s*'
    r'<td class="[^"]*kjName"[^>]*>(?P<name>[^<]*)</td>\s*'
    r'<td class="[^"]*kjTitle"[^>]*>(?P<titlecell>.*?)</td>\s*'
    r'<td class="[^"]*kjXbrl"[^>]*>(?P<xbrlcell>.*?)</td>',
    re.S)
LINK_RE = re.compile(r'href="([^"]+)"')
ZIP_RE = re.compile(r'href="([^"]+\.zip)"')

TAGS = re.compile(r"<[^>]+>")


class Throttled(Exception):
    """막혔다. '자료가 없다'와 다른 일이다."""


def get(url, binary=False, timeout=30):
    """404 는 None(그런 문서가 없다), 그 밖의 실패는 Throttled."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return raw if binary else raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise Throttled(str(e))


def listing(day, page=1):
    """하루치 목록 한 쪽 -> [{time, code, name, title, zip}].

    코드는 다섯 자리로 온다(`389A0`). 마지막 한 자리는 검사용이라 **앞 넉 자**가
    우리가 쓰는 종목 코드다.
    """
    txt = get(LIST_URL.format(page=page, day=day.replace("-", "")))
    if txt is None:
        return []
    out = []
    for m in ROW_RE.finditer(txt):
        d = m.groupdict()
        zip_m = ZIP_RE.search(d["xbrlcell"] or "")
        out.append({
            "time": d["time"].strip(),
            "code": d["code"].strip()[:4],
            "name": TAGS.sub("", d["name"]).strip(),
            "title": TAGS.sub("", d["titlecell"]).strip(),
            "zip": zip_m.group(1) if zip_m else "",
        })
    return out


def announcements(day):
    """그날 올라온 **결산단신**만. 쪽을 넘겨 가며 다 훑는다."""
    got, page = [], 1
    while page <= 40:
        rows = listing(day, page)
        if not rows:
            break
        for r in rows:
            if (TANSHIN.search(r["title"]) and not NOT_TANSHIN.search(r["title"])
                    and r["zip"]):
                got.append(r)
        page += 1
        time.sleep(PAUSE)
    return got


# XBRL 안에서 찾을 항목. namespace 는 업종마다 다르므로 **끝 이름만** 본다.
# IFRS 로 내는 일본 회사도 늘고 있어 그쪽 이름도 같이 본다.
#
# **금융사 이름을 더 넣어도 소용없다 — 확인했다.** 도쿄해상·크레디세존·라이프넷
# 생명의 결산단신을 열어 보니 요약에 **매출 항목이 아예 없다.** 들어 있는 것은
# ProfitBeforeTaxIFRS · ProfitIFRS · ProfitAttributableToOwnersOfParentIFRS ·
# 주식수 · EPS 뿐이다. IFRS 금융사는 요약을 이익부터 시작한다.
# 없는 값을 다른 항목으로 대신 채우지 말 것 — 세전이익을 매출이라 적는 셈이다.
# 이 회사들은 stockanalysis 가 며칠 뒤에 매출을 실어 주므로 그때 메워진다.
NAME_REV = ("NetSales", "NetSalesIFRS", "Sales", "SalesIFRS", "Revenue", "RevenueIFRS",
            "OperatingRevenues", "OperatingRevenuesIFRS",
            "NetSalesOfCompletedConstructionContracts",
            "OrdinaryIncomeBanks", "GrossOperatingRevenues")
NAME_OPI = ("OperatingIncome", "OperatingIncomeIFRS", "OperatingIncomeLoss",
            "ProfitFromOperatingActivitiesIFRS", "OperatingProfitLossIFRS",
            "BusinessProfitIFRS", "OrdinaryIncome", "OrdinaryIncomeLoss")
NAME_NI = ("ProfitAttributableToOwnersOfParent",
           "ProfitAttributableToOwnersOfParentIFRS",
           "NetIncome", "NetIncomeLoss", "ProfitLoss",
           "ProfitLossAttributableToOwnersOfParent")

# 문맥 이름. 결산단신 한 장에 여러 기간이 함께 실린다.
#   CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember   <- 이번 분기 실적
#   PriorAccumulatedQ1Duration_ConsolidatedMember_ResultMember     <- 전년 같은 분기
#   CurrentYearDuration_ConsolidatedMember_ForecastMember          <- 회사의 연간 예상
#
# **예상을 실적으로 쓰면 안 된다.** 회사가 내놓은 전망이 그대로 매출 막대에
# 올라가면 지어낸 숫자를 싣는 셈이다. Forecast 는 무조건 버린다.
CUR_CTX = re.compile(r"Current", re.I)
PRI_CTX = re.compile(r"Prior|Previous", re.I)
FORECAST_CTX = re.compile(r"Forecast|Upper|Lower", re.I)
RESULT_CTX = re.compile(r"Result", re.I)
DUR_CTX = re.compile(r"Duration", re.I)
CONS_CTX = re.compile(r"(?<!Non)Consolidated", re.I)
NONCONS_CTX = re.compile(r"NonConsolidated", re.I)


def local(tag):
    return tag.rsplit("}", 1)[-1]


# 결산단신 zip 안에는 `.xbrl` 인스턴스가 **없다**. 원문을 찍어 보고 알았다.
#
#   XBRLData/Summary/tse-qcedjpsm-332A0-...-ixbrl.htm   <- 요약은 여기
#   XBRLData/Attachment/...-ixbrl.htm                    <- 재무제표 본문
#
# 즉 **inline XBRL** 이다 — 수치가 XHTML 안에 `<ix:nonFraction>` 으로 박혀 있고
# 자릿수는 `scale`, 부호는 `sign` 속성에 따로 적힌다(scale="6" 이면 백만 단위).
# XML 파서를 그대로 걸면 HTML 실체참조에 걸려 깨지므로 필요한 것만 집어낸다.
IX_FACT = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
IX_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
CTX_RE = re.compile(r'<(?:\w+:)?context\b[^>]*\bid="([^"]+)"(.*?)</(?:\w+:)?context>',
                    re.S | re.I)
DATE_TAG = re.compile(r"<(?:\w+:)?(startDate|endDate|instant)>\s*([\d-]+)\s*</", re.I)


def read_summary(blob):
    """결산단신 zip -> {항목: [(문맥, 값, (시작일, 종료일))]}.

    요약(Summary)만 본다 — 첨부(Attachment)는 회사마다 표가 제각각이다.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return None
    names = [n for n in z.namelist()
             if "Summary" in n and n.lower().endswith(("ixbrl.htm", ".xbrl"))]
    if not names:
        return None
    try:
        txt = z.read(names[0]).decode("utf-8", "replace")
    except (KeyError, OSError):
        return None

    periods = {}
    for cid, body in CTX_RE.findall(txt):
        st = en = ""
        for kind, val in DATE_TAG.findall(body):
            if kind.lower() == "startdate":
                st = val
            else:
                en = val
        periods[cid] = (st, en)

    vals = {}
    for attrs, inner in IX_FACT.findall(txt):
        a = dict(IX_ATTR.findall(attrs))
        name = (a.get("name") or "").rsplit(":", 1)[-1]
        cid = a.get("contextRef") or ""
        if not name or not cid or a.get("xsi:nil") == "true":
            continue
        digits = re.sub(r"[^\d.-]", "", TAGS.sub("", inner))
        if not re.fullmatch(r"-?\d+(\.\d+)?", digits or ""):
            continue
        v = float(digits)
        try:
            v *= 10 ** int(a.get("scale") or 0)
        except ValueError:
            pass
        if a.get("sign") == "-":
            v = -v
        vals.setdefault(name, []).append((cid, v, periods.get(cid, ("", ""))))
    return vals or None


# ── 부문별 매출 ──────────────────────────────────────────────────────────
#
# 미국은 SEC 벌크에 부문 축이 그대로 있어 zip 하나로 전 종목을 받았다. 일본에는
# 그런 벌크가 없다. 대신 **우리가 이미 받고 있는 결산단신 zip 안에** 들어 있다 —
# 첨부(Attachment)의 세그먼트 정보 장(`…qcsg…-ixbrl.htm`)이 그것이다. 원문을
# 열어 확인했다(NTT 2026년 1분기).
#
#   문맥 CurrentYTDDuration_tse-qcediffr-94320IntegratedICTBusinessReportableSegmentMember
#   항목 TransactionsWithExternalCustomersIFRS · InterSegmentTransactionsIFRS
#        OperatingRevenuesIFRS · OperatingProfitLossIFRS
#
# 그래서 **요청을 한 번도 더 하지 않는다.** 실적 수치를 뽑는 그 zip 에서 같이
# 뽑는다. 남의 서버를 두 배로 두드릴 이유가 없다.
SEG_CTX = re.compile(r"(Segment|Reportable)", re.I)
# **결산단신의 세그먼트 표에는 합계 줄이 늘 들어 있다.** 그것도 두 벌씩이다 —
# `TotalOfReportableSegmentsAndOthersMember`(보고부문 계)와 `EntityTotalMember`
# (전사 합계). 부문으로 세면 매출이 두 배 세 배가 된다. 처음에 이름을
# `^ReportableSegmentsMember$` 하나로만 걸렀다가 177종목에 그 두 줄이 다 실렸다.
SEG_TOTAL = re.compile(r"^Total|EntityTotal|TotalOfReportableSegments|"
                       r"^(Reportable)?Segments?(Total)?Member$", re.I)
# **`Consolidated` 를 여기 넣으면 안 된다.** TDnet 문맥 이름의 `ConsolidatedMember`
# 는 '조정 항목'이 아니라 **연결 기준**이라는 뜻이다. 넣었더니 부문 행이 통째로
# 걸러졌다. 마찬가지로 `Total` 은 SEG_TOTAL 이 이름 전체로 가려 준다.
SEG_SKIP = re.compile(r"Elimination|Adjustment|Reconcil|InterSegment|"
                      r"Other[A-Z]*Adjust", re.I)
# 문맥 이름은 '기간_수식어_부문' 이다. 수식어(연결·실적 구분)를 부문으로 읽지 않는다.
SEG_QUALIFIER = re.compile(
    r"^(Non)?Consolidated(Member)?$|^Result(Member)?$|^Member$", re.I)
# 부문 이름 앞에 붙는 회사별 접두사. `tse-qcediffr-94320IntegratedICT…` 처럼
# 스키마 이름과 증권코드가 통째로 붙어 온다.
# 접두사는 '스키마이름-스키마이름-증권코드5자리' 다. 증권코드는 숫자만이 아니다 —
# 438A 같은 회사는 `tse-qcedjpsm-438A0` 이라, `\d+` 로 잡으면 `438` 까지만 떨어져
# 부문 이름이 'A0 Merchant Platform Business' 로 나온다. 다섯 자리를 통째로 뗀다.
SEG_PREFIX = re.compile(r"^[a-z]+-[a-z0-9]+-[0-9A-Za-z]{5}")
SEG_SUFFIX = re.compile(r"(Reportable)?Segments?Member$|Member$")

# **외부 고객 매출을 쓴다.** 부문 매출에는 부문끼리 주고받은 것(InterSegment)이
# 섞여 있어, 그걸 더하면 회사 총매출보다 커진다. 외부 매출만 더해야 총매출이 된다.
SEG_REV = ["TransactionsWithExternalCustomersIFRS",
           "NetSalesToOutsideCustomers",
           "NetSalesToExternalCustomers",
           "SalesToExternalCustomers",
           "RevenueFromExternalCustomers",
           "TransactionsWithExternalCustomers",
           # 외부 매출이 따로 없는 회사는 부문 매출(내부 포함)을 쓴다.
           "OperatingRevenuesIFRS", "RevenueIFRS", "NetSales", "OperatingRevenues"]
SEG_REV_RANK = {t: i for i, t in enumerate(SEG_REV)}


def seg_member(cid):
    """문맥 이름에서 **부문 쪽만** 떼어낸다. 아니면 빈 문자열.

    'CurrentYTDDuration_ConsolidatedMember_tse-…IntegratedICTBusinessReportableSegmentMember'
    처럼 앞에 기간과 수식어가 붙는다. 첫 밑줄 뒤를 통째로 부문으로 읽으면
    'ConsolidatedMember_…' 가 되어 걸러진다.
    """
    parts = cid.split("_")[1:]
    for p in parts:
        if SEG_CTX.search(p) and not SEG_QUALIFIER.match(p):
            return p
    rest = [p for p in parts if not SEG_QUALIFIER.match(p)]
    return rest[-1] if rest else ""


# 회계기준이 정해 둔 '그 밖' 바구니. 이름이 그대로 나오면 범례 한 줄이 화면을
# 가로지른다. 뜻은 「보고부문에 넣지 않은 사업」이라 '기타'가 맞다.
SEG_RENAME = {
    "OperatingSegmentsNotIncludedInReportableSegmentsAndOther"
    "RevenueGeneratingBusinessActivities": "Other",
    "OtherOperatingSegments": "Other",
    "AllOtherSegments": "Other",
}


def seg_name(member):
    """'tse-qcediffr-94320IntegratedICTBusinessReportableSegmentMember'
       -> 'Integrated ICT Business'. 못 다듬으면 원래 이름."""
    m = SEG_PREFIX.sub("", member.split(":")[-1])
    m = SEG_SUFFIX.sub("", m) or m
    if m in SEG_RENAME:
        return SEG_RENAME[m]
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", m).strip()
    return out or member


def read_segments(blob):
    """결산단신 zip -> [(시작일, 종료일, {부문: 매출})]. 없으면 [].

    첨부에는 이번 기간과 **전년 같은 기간**이 함께 실린다(Prior1YTDDuration).
    둘 다 담으면 공시 한 건으로 두 점을 얻는다. 예상(Forecast)은 안 쓴다.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return []
    docs = {}
    for n in z.namelist():
        if not n.lower().endswith(("ixbrl.htm", ".xbrl")):
            continue
        try:
            docs[n] = z.read(n).decode("utf-8", "replace")
        except (KeyError, OSError):
            pass

    # **문맥은 zip 전체에서 모은다.** 결산단신의 첨부는 여러 장이 한 벌인데
    # `<xbrli:context>` 정의는 그중 한 장에만 들어 있다. 세그먼트 장만 읽으면
    # 문맥을 하나도 못 찾아 모든 값이 버려진다 — 실제로 481건을 훑고 0건을 얻었다.
    periods = {}
    for txt in docs.values():
        for cid, body in CTX_RE.findall(txt):
            if cid in periods:
                continue
            st = en = ""
            for kind, val in DATE_TAG.findall(body):
                if kind.lower() == "startdate":
                    st = val
                elif kind.lower() == "enddate":
                    en = val
            if st and en:
                periods[cid] = (st, en)

    out = {}
    for n, txt in docs.items():
        if "Attachment" not in n or not SEG_CTX.search(txt):
            continue

        for attrs, inner in IX_FACT.findall(txt):
            a = dict(IX_ATTR.findall(attrs))
            name = (a.get("name") or "").rsplit(":", 1)[-1]
            cid = a.get("contextRef") or ""
            rank = SEG_REV_RANK.get(name)
            if rank is None or a.get("xsi:nil") == "true":
                continue
            if FORECAST_CTX.search(cid) or cid not in periods:
                continue
            # **원자료에는 XBRL 이름을 그대로 담는다.** 다듬은 이름을 담아 두면
            # 합계 줄을 가리는 규칙이나 이름 줄이는 규칙을 고칠 때마다 열흘치를
            # 다시 내려받아야 한다. 거르고 다듬는 일은 저장할 때 한다.
            member = seg_member(cid)
            if not member:
                continue
            digits = re.sub(r"[^\d.-]", "", TAGS.sub("", inner))
            if not re.fullmatch(r"-?\d+(\.\d+)?", digits or ""):
                continue
            v = float(digits)
            try:
                v *= 10 ** int(a.get("scale") or 0)
            except ValueError:
                pass
            if a.get("sign") == "-":
                v = -v
            if v <= 0:
                continue
            slot = out.setdefault(periods[cid], {})
            old = slot.get(member)
            # 더 정확한 항목(외부 고객 매출)이 이긴다.
            if old is None or rank < old[1]:
                slot[member] = (v, rank)

    return [(st, en, {k: v[0] for k, v in segs.items()})
            for (st, en), segs in sorted(out.items()) if len(segs) >= 2]


def seg_clean(row):
    """원자료 한 줄(XBRL 이름) -> 화면에 쓸 {부문: 값}. 합계·조정 줄을 뺀다.

    거르고 다듬는 일이 여기 있는 까닭은, 규칙을 고칠 때 **다시 내려받지 않고**
    빌드만 다시 하면 되게 하려는 것이다. 처음에는 받을 때 다듬어 담았다가
    합계 줄 하나 빼려고 열흘치를 통째로 다시 받았다.
    """
    out = {}
    for m, v in row.items():
        if SEG_TOTAL.match(m) or SEG_SKIP.search(m):
            continue
        n = seg_name(m)
        out[n] = out.get(n, 0) + v          # 다듬은 이름이 겹치면 '기타'끼리다
    return out


def seg_quarters(points):
    """누계 점들 -> {종료일: {부문: 값}}. 같은 시작일의 앞 누계를 뺀다.

    결산단신의 부문 표는 **누계**다(1분기는 그 분기, 2분기는 상반기…).
    총매출에서 하던 것과 같다 — 다만 여기서는 시작일이 같이 실려 있으므로
    회계연도를 짐작할 필요가 없다.
    """
    by_start = {}
    for st, en, segs in points:
        by_start.setdefault(st, {})[en] = segs

    out = {}
    for st, ends in by_start.items():
        order = sorted(ends)
        for i, en in enumerate(order):
            days = (date.fromisoformat(en) - date.fromisoformat(st)).days
            if i == 0:
                if days <= 125:               # 첫 누계가 곧 1분기
                    out[en] = dict(ends[en])
                continue
            prev = ends[order[i - 1]]
            gap = (date.fromisoformat(en) - date.fromisoformat(order[i - 1])).days
            if not 60 <= gap <= 125:
                continue                      # 사이가 한 분기가 아니다
            cut = {k: ends[en][k] - prev.get(k, 0) for k in ends[en]
                   if ends[en][k] - prev.get(k, 0) > 0}
            if len(cut) >= 2:
                out[en] = cut
    return out


def drop_totals(q):
    """다른 부문을 다 더한 것과 같은 줄은 합계다 — 이름으로 못 거른 것을 값으로 잡는다.

    이름 목록은 언제나 모자란다. 회사가 제 나름의 합계 멤버를 만들어 쓰면 그대로
    부문으로 세어져 매출이 두 배가 된다. 그래서 마지막에 값으로 한 번 더 본다:
    **모든 기간에서** 나머지를 다 더한 것과 3% 안으로 같으면 합계다.
    한 기간이라도 어긋나면 손대지 않는다 — 진짜 부문을 지우는 쪽이 더 나쁘다.
    """
    for _ in range(2):
        names = set()
        for row in q.values():
            names |= set(row)
        if len(names) < 3:
            break
        hit_name = None
        for m in names:
            hit = tot = 0
            for row in q.values():
                v, others = row.get(m), sum(x for k, x in row.items() if k != m)
                if not v or not others:
                    continue
                tot += 1
                if abs(v - others) / v < 0.03:
                    hit += 1
            if tot >= 2 and hit == tot:
                hit_name = m
                break
        if not hit_name:
            break
        for row in q.values():
            row.pop(hit_name, None)
    return {e: row for e, row in q.items() if len(row) >= 2}


def pick(vals, names):
    """이번 기간 **실적**(예상 아님) 연결 값을 고른다.

    점수로 고른다. 예상(Forecast)은 아예 쓰지 않는다 — 회사 전망을 실적이라고
    싣는 것은 지어내는 것이다. 시점 값(Instant)도 뺀다 — 그건 재무상태다.
    """
    best = None
    for name in names:
        for cid, v, (st, en) in vals.get(name, []):
            if PRI_CTX.search(cid) or FORECAST_CTX.search(cid) or not en:
                continue
            if not DUR_CTX.search(cid):
                continue
            score = 0
            if CUR_CTX.search(cid):
                score += 8
            if RESULT_CTX.search(cid):
                score += 4
            if CONS_CTX.search(cid):
                score += 2
            elif not NONCONS_CTX.search(cid):
                score += 1
            if best is None or score > best[0]:
                best = (score, v, st, en)
        if best and best[0] >= 14:
            break
    return best


def raw(url, binary=False):
    """응답을 그대로 찍는다. **0건으로 넘기지 않는다** — 봉투가 깨졌으면 봐야 한다."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Language": "ja,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read()
            print(f"  {r.status} · {r.headers.get('Content-Type','?')} · {len(body):,} 바이트")
            return body
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} {e.reason}")
    except Exception as e:                      # noqa: BLE001 — 무엇이든 찍어야 한다
        print(f"  실패: {type(e).__name__} {e}")
    return None


def dump_rows():
    """TDnet 목록의 **표 한 줄이 실제로 어떻게 생겼는지** 그대로 찍는다.

    목록은 200 으로 잘 열리는데 우리 정규식이 0건을 냈다. 생김새를 안 보고
    정규식을 고치는 건 점치는 것이다.
    """
    for back in range(4):
        day = (date.today() - timedelta(days=back)).strftime("%Y%m%d")
        url = LIST_URL.format(page=1, day=day)
        print(f"\n===== {day}\n  {url}")
        body = raw(url)
        if not body:
            continue
        txt = body.decode("utf-8", "replace")
        i = txt.find("決算短信")
        if i < 0:
            i = txt.find("kjTitle")
        if i < 0:
            print("    kjTitle 이 없다. 그날은 공시가 없었을 수 있다.")
            continue
        # 그 줄이 시작하는 <tr> 부터 끝나는 </tr> 까지
        s = txt.rfind("<tr", 0, i)
        e = txt.find("</tr>", i)
        print("    --- 한 줄 원문 ---")
        print(txt[max(s, 0):e + 5][:1600])
        print("    --- 여기까지 ---")
        return


def dump_zip():
    """결산단신 zip **안에 무엇이 들었는지** 그대로 찍는다.

    zip 은 잘 받아지는데(44~75KB) XBRL 을 못 읽었다. 파일 이름이 다른지,
    XML 이 깨진 건지, 안 보고는 알 수 없다.
    """
    for back in range(4):
        day = (date.today() - timedelta(days=back)).isoformat()
        try:
            rows = listing(day, 1)
        except Throttled as e:
            print(f"{day} 목록 실패: {e}")
            continue
        tan = [r for r in rows
               if TANSHIN.search(r["title"]) and not NOT_TANSHIN.search(r["title"])
               and r["zip"]]
        if not tan:
            continue
        r = tan[0]
        print(f"\n===== {day} {r['code']} {r['name'][:14]} · {r['zip']}")
        blob = raw("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
        if not blob:
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile as e:
            print("  zip 이 아니다:", e)
            print("  앞 60바이트:", blob[:60])
            continue
        print("  안에 든 파일:")
        for n in z.namelist():
            print(f"    {n}")
        cands = [n for n in z.namelist() if n.lower().endswith(".xbrl")]
        if not cands:
            print("  .xbrl 이 없다")
            return
        body = z.read(cands[0])
        print(f"\n  {cands[0]} 앞 700자:")
        print("   ", body[:700].decode("utf-8", "replace").replace("\n", " ")[:700])
        try:
            root = ElementTree.fromstring(body)
            print(f"\n  XML 읽힘. 최상위 {local(root.tag)}, 자식 {len(list(root))}개")
        except ElementTree.ParseError as e:
            print("\n  XML 파싱 실패:", e)
        return


def dump_names(codes):
    """어떤 항목 이름을 쓰는지 **그 회사 공시에서 직접 뽑는다.**

    금융사는 `NetSales` 를 안 쓴다. 은행은 경상수익, 보험은 또 다른 이름이다.
    이름을 짐작해서 목록에 넣는 건 점치는 것이라, 실제 공시를 열어 본다.
    """
    want = set(codes)
    seen = set()
    for back in range(BACK_DAYS):
        if not want - seen:
            break
        day = (date.today() - timedelta(days=back)).isoformat()
        try:
            rows = announcements(day)
        except Throttled as e:
            print(f"{day} 목록 실패: {e}")
            continue
        for r in rows:
            if r["code"] not in want or r["code"] in seen:
                continue
            seen.add(r["code"])
            print(f"\n===== {r['code']} {r['name'][:18]} | {r['title'][:44]}")
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
            except Throttled as e:
                print("  실패:", e)
                continue
            time.sleep(PAUSE)
            vals = read_summary(blob) if blob else None
            if not vals:
                print("  요약 XBRL 을 못 읽었다")
                continue
            # 이번 기간 실적 문맥에 값이 있는 항목만, 큰 것부터
            rows_out = []
            for name, facts in vals.items():
                for cid, v, (st, en) in facts:
                    if (PRI_CTX.search(cid) or FORECAST_CTX.search(cid)
                            or not DUR_CTX.search(cid) or not CUR_CTX.search(cid)):
                        continue
                    rows_out.append((abs(v), name, v, st, en, cid))
                    break
            rows_out.sort(reverse=True)
            for _a, name, v, st, en, cid in rows_out[:14]:
                mark = ("  <- 매출로 잡힘" if name in NAME_REV else
                        "  <- 영업익" if name in NAME_OPI else
                        "  <- 순이익" if name in NAME_NI else "")
                print(f"    {name:46s} {v:>18,.0f}  {st}~{en}{mark}")


def dump_segments(codes):
    """**부문별 매출이 결산단신 안에 있는가**를 원문으로 확인한다.

    미국은 SEC 벌크에 부문 축이 그대로 있어 전 종목을 한꺼번에 받았다. 일본에도
    같은 것이 있는지 봐야 하는데, 요약(Summary)에는 확실히 없다 — 거기엔 연결
    합계만 있다. 첨부(Attachment)에 세그먼트 정보 표가 실리는데, 그것이
    inline XBRL 로 태그돼 있는지 아니면 그냥 HTML 표인지는 **열어 봐야 안다.**
    짐작으로 파서를 쓰지 않는다.
    """
    want = set(codes)
    seen = set()
    for back in range(BACK_DAYS):
        if not want - seen:
            break
        day = (date.today() - timedelta(days=back)).isoformat()
        try:
            rows = announcements(day)
        except Throttled as e:
            print(f"{day} 목록 실패: {e}")
            continue
        for r in rows:
            if (want and r["code"] not in want) or r["code"] in seen:
                continue
            seen.add(r["code"])
            print(f"\n===== {r['code']} {r['name'][:18]} | {r['title'][:44]}")
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
            except Throttled as e:
                print("  실패:", e)
                continue
            time.sleep(PAUSE)
            if not blob:
                continue
            try:
                z = zipfile.ZipFile(io.BytesIO(blob))
            except zipfile.BadZipFile as e:
                print("  zip 이 아니다:", e)
                continue
            # **진짜 파서를 태워 본다.** 눈으로 태그가 보이는 것과 우리 파서가
            # 뽑아내는 것은 다른 일이다 — 실제로 첫 실행이 481건에서 0건을 얻었다.
            got = read_segments(blob)
            print(f"  read_segments -> 기간 {len(got)}개")
            for st, en, row in got[:3]:
                print(f"    {st}~{en} {list(row)[:6]}")

            att = [n for n in z.namelist() if "Attachment" in n]
            print(f"  첨부 파일 {len(att)}개: {att[:6]}")
            ctx_here = sum(len(CTX_RE.findall(
                z.read(n).decode('utf-8', 'replace'))) for n in att
                if n.lower().endswith(("ixbrl.htm", ".xbrl")))
            print(f"  첨부 안의 <context> 정의 {ctx_here}개")
            for n in att:
                if not n.lower().endswith((".htm", ".html", ".xbrl")):
                    continue
                txt = z.read(n).decode("utf-8", "replace")
                facts = IX_FACT.findall(txt)
                seg_ctx, names = set(), {}
                for attrs, _inner in facts:
                    a = dict(IX_ATTR.findall(attrs))
                    cid = a.get("contextRef") or ""
                    nm = (a.get("name") or "").rsplit(":", 1)[-1]
                    if re.search(r"Segment|Reportable|セグメント", cid, re.I):
                        seg_ctx.add(cid)
                        names[nm] = names.get(nm, 0) + 1
                print(f"    {n.split('/')[-1][:52]:54s} ix {len(facts):>4}건 "
                      f"· 세그먼트 문맥 {len(seg_ctx)}")
                for cid in sorted(seg_ctx)[:8]:
                    print(f"        문맥 {cid}")
                for nm, c in sorted(names.items(), key=lambda kv: -kv[1])[:8]:
                    print(f"        항목 {nm}  {c}건")
                if "セグメント" in txt and not seg_ctx:
                    i = txt.find("セグメント")
                    print("        (세그먼트 글자는 있는데 태그가 없다) "
                          + TAGS.sub(" ", txt[i:i + 160]).replace("\n", " "))


def survey():
    """일본 실적을 **발표 당일에** 주는 곳을 찾는다. 되는 곳만 골라 쓴다."""
    day = date.today().strftime("%Y%m%d")
    cands = [
        ("TDnet 목록",      f"https://www.release.tdnet.info/inbs/I_list_001_{day}.html"),
        ("TDnet 첫 화면",   "https://www.release.tdnet.info/index.html"),
        ("TDnet inbs",      "https://www.release.tdnet.info/inbs/I_main_00.html"),
        ("EDINET v2",       "https://api.edinet-fsa.go.jp/api/v2/documents.json"
                            f"?date={date.today().isoformat()}&type=2"),
        ("야후재팬 실적",    "https://finance.yahoo.co.jp/quote/1379.T/performance"),
        ("가부탄",          "https://kabutan.jp/stock/finance?code=1379"),
        ("민카부",          "https://minkabu.jp/stock/1379/settlement"),
        ("IR뱅크",          "https://irbank.net/1379/results"),
        ("닛케이 결산",      "https://www.nikkei.com/nkd/company/kessan/?scode=1379"),
        ("JPX",             "https://www.jpx.co.jp/"),
    ]
    for label, url in cands:
        print(f"\n===== {label}\n  {url}")
        body = raw(url)
        if not body:
            continue
        txt = body.decode("utf-8", "replace")
        # 결산단신·매출 같은 낱말이 실제로 들어 있나
        for word in ("決算短信", "kjTitle", "売上高", "営業利益", "results"):
            if word in txt:
                print(f"    '{word}' 있음")
        head = re.sub(r"\s+", " ", TAGS.sub(" ", txt[:1200])).strip()
        print("    앞부분:", head[:300])


def probe(days=3):
    """TDnet 이 열리는지, 결산단신이 잡히는지, XBRL 이 읽히는지 눈으로 본다."""
    today = date.today()
    for back in range(days):
        day = (today - timedelta(days=back)).isoformat()
        print(f"\n===== {day}")
        try:
            rows = listing(day, 1)
        except Throttled as e:
            print("  목록 실패:", e)
            continue
        print(f"  1쪽에 {len(rows)}건")
        if not rows:
            continue
        tan = [r for r in rows
               if TANSHIN.search(r["title"]) and not NOT_TANSHIN.search(r["title"])
               and r["zip"]]
        print(f"  그중 결산단신 {len(tan)}건")
        for r in tan[:3]:
            print(f"    {r['time']} {r['code']} {r['name'][:16]} | {r['title'][:40]}")
        if not tan:
            continue
        for r in tan[:4]:
            print(f"  -> {r['code']} {r['name'][:14]} · {r['zip']}")
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
            except Throttled as e:
                print("     zip 실패:", e)
                continue
            if blob is None:
                print("     zip 이 없다")
                continue
            vals = read_summary(blob)
            if not vals:
                print(f"     zip {len(blob):,} 바이트인데 XBRL 을 못 읽었다")
                continue
            print(f"     zip {len(blob):,} 바이트 · 항목 {len(vals)}가지")
            for label, names in (("매출", NAME_REV), ("영업익", NAME_OPI), ("순이익", NAME_NI)):
                got = pick(vals, names)
                if got:
                    _sc, v, st, en = got
                    print(f"       {label} {v:>16,.0f}   {st} ~ {en}")
                else:
                    print(f"       {label} 못 찾음")
            time.sleep(PAUSE)


def q_label(mid):
    d = date.fromisoformat(mid)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def base_series():
    """이미 가진 일본 분기 시계열. 누적값을 분기로 되돌릴 때 밑절미가 된다."""
    out = {}
    for name in ("financials_intl.json", "financials_jp.json"):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            continue
        for k, rec in got.items():
            if not k.startswith("jp:"):
                continue
            for pt in rec.get("points") or []:
                if pt.get("end") and pt.get("rev") is not None:
                    out.setdefault(k, {})[pt["end"]] = pt
    return out


def quarter_from(vals, prior):
    """결산단신의 **누계**를 분기값으로 되돌린다. 못 되돌리면 None.

    결산단신은 누계를 싣는다. 1분기는 그 분기 자체지만 2분기는 상반기,
    3분기는 9개월, 결산은 1년치다. 그대로 막대에 올리면 분기가 아니라 누계
    그래프가 된다 — 미국 SEC 에서 겪은 것과 같은 문제다.

    되돌리는 법은 하나뿐이다. **같은 회계연도에서 앞서 끝난 분기들을 뺀다.**
    그 분기들이 우리에게 없거나 개수가 안 맞으면 **지어내지 않고 건너뛴다.**
    stockanalysis 가 지난 20분기를 이미 채워 두므로 대개는 갖고 있다.
    """
    rev = pick(vals, NAME_REV)
    if not rev:
        return None
    _s, ytd_rev, start, end = rev
    try:
        ds, de = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None
    days = (de - ds).days + 1
    if days < 60 or days > 400:
        return None

    def same_span(names):
        """같은 기간으로 신고된 값만 쓴다. 기간이 다르면 뺄셈이 어긋난다."""
        got = pick(vals, names)
        return got[1] if got and got[2] == start and got[3] == end else None

    if days <= 125:                       # 1분기 — 누계가 곧 분기다
        q_start = ds
        rq = ytd_rev
        oq, nq = same_span(NAME_OPI), same_span(NAME_NI)
    else:
        past = sorted((p for e, p in prior.items() if start <= e < end),
                      key=lambda p: p["end"])
        want = round((days - 91) / 91)    # 빼야 할 분기 수
        if want < 1 or len(past) != want:
            return None                   # 구멍이 있다. 어림하지 않는다.
        rq = ytd_rev - sum(p["rev"] for p in past)

        def cut(names, whole):
            v = same_span(names)
            parts = [p.get(whole) for p in past]
            if v is None or any(x is None for x in parts):
                return None
            return v - sum(parts)

        oq, nq = cut(NAME_OPI, "opi"), cut(NAME_NI, "ni")
        q_start = date.fromisoformat(past[-1]["end"]) + timedelta(days=1)

    if rq is None or q_start > de:
        return None
    mid = (q_start + (de - q_start) / 2).isoformat()
    return {"label": q_label(mid), "end": end, "mid": mid,
            "rev": rq, "opi": oq, "ni": nq}


# 결산단신 첨부의 설명 섹션 — 회사가 "왜 이랬는지"를 제 입으로 적는 자리.
#   （１）経営成績に関する説明 / 当四半期決算に関する定性的情報 / 経営成績等の概況
# 브리핑 칸의 일본 코멘트 원문이 된다. 다음 절(（２）·財政状態…)이 나오면 끊는다.
NARR_HEAD = re.compile(
    r"(?:経営成績(?:等)?(?:に関する説明|の概況|の状況)|"
    r"当[四半期中間]*決算に関する定性的情報|業績(?:等)?の概況)")
NARR_STOP = re.compile(
    r"（[２-９2-9]）|\(2\)|financial|財政状態|キャッシュ・フロー|"
    r"今後の見通し|連結業績予想|業績予想に関する")


def read_narrative(blob):
    """결산단신 zip -> 경영성적 설명 원문(일본어) 또는 ''.

    첨부 여러 장 중 설명 절이 있는 장을 찾아, 머리글 뒤부터 다음 절 앞까지
    (최대 1,600자) 뜯는다. 원문 그대로 담기만 한다 — 한국어는 briefs.py 에
    사람이(세션에서 일괄로) 옮긴다.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return ""
    best = ""
    for n in z.namelist():
        if "Attachment" not in n or not n.lower().endswith(("ixbrl.htm", ".htm")):
            continue
        try:
            txt = z.read(n).decode("utf-8", "replace")
        except (KeyError, OSError):
            continue
        plain = re.sub(r"\s+", " ", TAGS.sub(" ", txt))
        m = NARR_HEAD.search(plain)
        if not m:
            continue
        body = plain[m.end():m.end() + 4000]
        stop = NARR_STOP.search(body, 80)      # 머리글 바로 옆 재등장은 무시
        if stop:
            body = body[:stop.start()]
        body = body.strip()[:1600]
        if len(body) > len(best):
            best = body
    return best if len(best) >= 60 else ""


def forecast_from(vals):
    """회사의 통기 예상(가이던스)을 뽑는다 — 요약의 ForecastMember 문맥.

    실적(points)에는 절대 섞지 않는다. 여기 따로 담고 화면이 「회사 통기
    예상(공시)」이라고 적는다 — 회사가 공시한 수치지 우리의 추정이 아니다.
    Upper/Lower(범위 예상)는 안 쓴다. 본결산 공시에는 다음 회계연도 예상
    (NextYearDuration)이 실리므로 **종료일이 가장 늦은 문맥**을 고른다.
    같은 종료일이면 연결(Consolidated)을 비연결보다 앞세운다.
    """
    def pick(names):
        best = None
        for nm in names:
            for cid, v, (_st, en) in vals.get(nm, []):
                if not (FORECAST_CTX.search(cid) and DUR_CTX.search(cid)):
                    continue
                if re.search(r"Upper|Lower", cid, re.I):
                    continue
                cons = 1 if CONS_CTX.search(cid) and not NONCONS_CTX.search(cid) else 0
                rank = (en or "", cons)
                if best is None or rank > best[0]:
                    best = (rank, v, en)
            if best:
                break               # 실적과 같은 규칙 — 먼저 걸린 항목 이름을 쓴다
        return best
    r, o = pick(NAME_REV), pick(NAME_OPI)
    if not r and not o:
        return None
    end = (r or o)[2]
    if r and o and r[2] != o[2]:
        # 매출과 영업이익의 회계연도가 다르면 늦은 쪽 하나만 남긴다. 섞으면
        # 서로 다른 해의 예상을 한 줄에 적는 거짓말이 된다.
        if r[2] > o[2]:
            o = None
        else:
            r, end = None, o[2]
    out = {"end": end}
    if r:
        out["rev"] = r[1]
    if o:
        out["opi"] = o[1]
    return out


def collect():
    """최근 며칠치 결산단신을 훑어 분기 수치를 담는다."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            pass

    prior = base_series()
    stocks = {k: dict(v) for k, v in old.items()}
    seen = {k: {p["end"] for p in v.get("points") or []} for k, v in stocks.items()}
    # 부문별 매출은 **같은 zip 에서** 뽑는다. 요청을 한 번도 더 하지 않는다.
    # 담아 두는 것은 누계 점이고(시작일·종료일·부문별 값), 분기로 되돌리는 것은
    # 저장할 때 한다 — 그래야 다음 분기가 들어왔을 때 앞엣것과 이어서 뺄 수 있다.
    segs = load_seg_raw()
    # **이미 뜯어본 공시는 다시 내려받지 않는다.** 열흘치를 훑으므로 이게 없으면
    # 매 실행마다 사백 건을 다시 받는다. 공시 번호는 문서마다 하나뿐이라 열쇠로 쓴다.
    #
    # **본 것을 두 가지로 나눠 적는다.** 수치를 뽑은 공시와 부문을 뽑은 공시는
    # 다르다. 부문 수집기를 뒤에 붙였으므로 이번 시즌 공시는 수치 쪽에만 들어
    # 있고, 그대로 두면 부문은 앞으로 들어올 공시부터만 쌓여 이번 시즌을 통째로
    # 놓친다. 한 벌만 적었다가 실제로 그럴 뻔했다 — 파일이 생기는 순간 다시
    # 훑기가 멈춘다. **둘 다 본 공시만** 건너뛴다.
    done = set(load_done())
    seg_done = set(load_seg_done())
    if len(done - seg_done) > 50:
        print(f"  부문을 아직 안 본 공시가 {len(done - seg_done):,}건 있다. 다시 뜯는다.",
              flush=True)
    # 가이던스(통기 예상)도 부문과 같은 방식으로 나중에 붙었다 — 수치만 본 공시를
    # 한 번 더 뜯어 예상란을 채운다. 세 벌 다 본 공시만 건너뛴다.
    fcst_done = set(load_fcst_done())
    if len(done - fcst_done) > 50:
        print(f"  가이던스를 아직 안 본 공시가 {len(done - fcst_done):,}건 있다.",
              flush=True)
    narrs = load_narrs()

    got = skipped = streak = walked = 0
    for back in range(BACK_DAYS):
        day = (date.today() - timedelta(days=back)).isoformat()
        try:
            rows = announcements(day)
        except Throttled as e:
            print(f"  {day} 목록 실패: {e}", file=sys.stderr, flush=True)
            continue
        if not rows:
            continue
        print(f"  {day}: 결산단신 {len(rows)}건", flush=True)
        for r in rows:
            key = "jp:" + r["code"]
            if r["zip"] in done and r["zip"] in seg_done and r["zip"] in fcst_done:
                continue                    # 지난 실행에서 셋 다 봤다
            fresh = r["zip"] not in done
            fresh_fc = r["zip"] not in fcst_done
            done.add(r["zip"])
            seg_done.add(r["zip"])
            fcst_done.add(r["zip"])
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
                streak = 0
            except Throttled as e:
                streak += 1
                print(f"    {r['code']} 막힘: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print("  연속으로 막혔다. 여기서 접는다.", file=sys.stderr, flush=True)
                    return save(stocks, done, got, skipped, segs, seg_done, fcst_done, narrs)
                continue
            time.sleep(PAUSE)
            if not blob:
                continue
            # **문서 단위로도 중간 저장한다.** 백필(가이던스·설명을 아직 안 본
            # 공시 되훑기)은 새 분기(got)가 안 늘어 아래 저장이 안 걸리고,
            # 하루 500건짜리 날은 5분 예산 안에 못 끝나 날 단위 저장에도 못
            # 닿는다 — 그러면 매 실행 같은 자리를 다시 받고 아무것도 안 남는다.
            walked += 1
            if walked % 60 == 0:
                save(stocks, done, got, skipped, segs, seg_done, fcst_done,
                     narrs, quiet=True)
                print(f"    ...{walked}건 훑음", flush=True)

            for st, en, row in read_segments(blob):
                segs.setdefault(key, {})[st + "/" + en] = row

            if fresh_fc:
                narr = read_narrative(blob)
                cur = narrs.get(r["code"])
                # 뒤로 훑으므로 옛 공시가 나중에 온다 — 새 것을 덮지 않는다.
                if narr and (not cur or (cur.get("date") or "") <= day):
                    narrs[r["code"]] = {"date": day, "text": narr}
            vals = read_summary(blob) if (fresh or fresh_fc) else None
            if vals and fresh_fc:
                fc = forecast_from(vals)
                if fc:
                    fc["ts"] = day
                    rec = stocks.setdefault(key, {"v": JP_VER, "freq": "Q",
                                                  "cur": "JPY", "src": "tdnet",
                                                  "points": []})
                    old_fc = rec.get("fcst")
                    if old_fc and (old_fc.get("ts") or "") > day:
                        # 뒤로 훑으므로 옛 공시가 나중에 온다. 새 것을 덮지 않고,
                        # 같은 회계연도의 다른 값이면 '직전 예상'으로만 채운다 —
                        # 그게 상향/하향 폭의 밑절미다.
                        if (old_fc.get("end") == fc["end"]
                                and old_fc.get("prevRev") is None
                                and old_fc.get("prevOpi") is None
                                and (old_fc.get("rev"), old_fc.get("opi"))
                                    != (fc.get("rev"), fc.get("opi"))):
                            old_fc["prevRev"] = fc.get("rev")
                            old_fc["prevOpi"] = fc.get("opi")
                    else:
                        if old_fc and old_fc.get("end") == fc["end"]:
                            if (old_fc.get("rev"), old_fc.get("opi")) \
                                    != (fc.get("rev"), fc.get("opi")):
                                fc["prevRev"] = old_fc.get("rev")
                                fc["prevOpi"] = old_fc.get("opi")
                            else:           # 같은 값 재공시 — 이전 밑절미를 잇는다
                                fc["prevRev"] = old_fc.get("prevRev")
                                fc["prevOpi"] = old_fc.get("prevOpi")
                        rec["fcst"] = fc

            if not fresh:
                continue            # 수치는 지난번에 이미 뽑았다. 부문·예상만 보러 왔다.
            if not vals:
                skipped += 1
                continue
            pt = quarter_from(vals, prior.get(key, {}))
            if not pt:
                skipped += 1
                continue
            if pt["end"] in seen.get(key, set()):
                continue
            rec = stocks.setdefault(key, {"v": JP_VER, "freq": "Q", "cur": "JPY",
                                          "src": "tdnet", "points": []})
            rec["points"] = sorted([p for p in rec["points"] if p["end"] != pt["end"]] + [pt],
                                   key=lambda p: p["end"])[-8:]
            rec["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
            rec["v"] = JP_VER
            seen.setdefault(key, set()).add(pt["end"])
            prior.setdefault(key, {})[pt["end"]] = pt
            got += 1
            # **중간중간 써둔다.** 단계가 시간 제한에 걸려 죽으면 맨 끝의 저장이
            # 실행되지 않아 받은 것을 통째로 잃는다. 실제로 첫 실행이 그랬다 —
            # 5분에서 잘려 197종목을 담고도 파일이 안 생겼다.
            if got % 20 == 0:
                save(stocks, done, got, skipped, segs, seg_done, fcst_done, narrs, quiet=True)
                print(f"    ...{got}건", flush=True)
        # 부문만 보러 온 실행은 got 이 안 늘어 위 저장이 안 걸린다. 하루치를
        # 끝낼 때마다 써 둔다 — 시간 제한에 잘려도 받은 만큼은 남는다.
        save(stocks, done, got, skipped, segs, seg_done, fcst_done, narrs, quiet=True)
    return save(stocks, done, got, skipped, segs, seg_done, fcst_done, narrs)


def load_done():
    """지난 실행에서 이미 뜯어본 공시 번호."""
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("docs", [])
    except (ValueError, OSError):
        return []


def _seg_file():
    if not SEG_OUT.exists():
        return {}
    try:
        return json.loads(SEG_OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


BRIEFS_OUT = HERE / "data" / "briefs_jp.json"


def load_fcst_done():
    """가이던스까지 뜯어본 공시 번호. 부문(seg_done)과 같은 이유로 따로 적는다 —
    한 벌만 적으면 나중에 붙인 수집이 영영 안 돈다."""
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("fcst_docs", [])
    except (ValueError, OSError):
        return []


def load_narrs():
    """받아둔 경영성적 설명 원문. {코드: {date, text}}."""
    if not BRIEFS_OUT.exists():
        return {}
    try:
        return json.loads(BRIEFS_OUT.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError):
        return {}


def load_seg_raw():
    """받아둔 누계 점. {종목: {'시작/종료': {부문: 값}}}"""
    return _seg_file().get("raw", {})


def load_seg_done():
    """**부문까지** 뜯어본 공시 번호. 수치 쪽(`docs`)과 따로 적는다."""
    d = _seg_file()
    return d.get("docs", []) if d.get("v") == SEG_JP_VER else []


# 부문 이름이 그대로 남아 있는 누계 점은 종목당 이만큼만 들고 있는다. 스무 분기면
# 화면에 그리는 스물두 칸을 거의 채우고, 그 이상은 파일만 무거워진다.
SEG_KEEP_RAW = 24


def save_segments(raw, seg_done=(), quiet=False):
    """누계 점을 분기로 되돌려 화면용 자료를 만든다. 원자료도 같이 남긴다."""
    stocks = {}
    for key, spans in raw.items():
        pts = []
        for span, row in spans.items():
            st, _, en = span.partition("/")
            if not (st and en):
                continue
            clean = seg_clean(row)
            if len(clean) >= 2:
                pts.append((st, en, clean))
        q = drop_totals(seg_quarters(pts))
        if len(q) < 2:
            continue
        ends = sorted(q)[-20:]
        names = {}
        for e in ends:
            for n, v in q[e].items():
                names[n] = names.get(n, 0) + v
        order = sorted(names, key=lambda n: -names[n])[:10]
        if len(order) < 2:
            continue
        stocks[key] = {
            "v": SEG_JP_VER, "axis": "사업부문", "names": order,
            "pts": [[e] + [q[e].get(n) for n in order] for e in ends],
        }

    payload = {
        "source": "TDnet 결산단신 첨부 (세그먼트 정보, inline XBRL)",
        "note": ("외부 고객 매출을 쓴다 — 부문끼리 주고받은 것을 더하면 총매출보다 "
                 "커진다. 누계로 실리므로 같은 시작일의 앞 누계를 빼 분기로 되돌린다."),
        "v": SEG_JP_VER,
        "count": len(stocks),
        "docs": sorted(seg_done)[-6000:],
        "raw": {k: dict(sorted(v.items())[-SEG_KEEP_RAW:]) for k, v in raw.items()},
        "stocks": stocks,
    }
    tmp = SEG_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(SEG_OUT)
    if not quiet:
        print(f"{len(stocks):,}종목 부문별 매출 -> {SEG_OUT} "
              f"(누계 점을 가진 종목 {len(raw):,})")


def save(stocks, done, got, skipped, segs=None, seg_done=(), fcst_done=(),
         narrs=None, quiet=False):
    payload = {
        "source": "TDnet 適時開示 결산단신 (inline XBRL)",
        "note": ("발표 당일에 올라온다. 누계로 실리므로 앞 분기를 빼 분기값으로 "
                 "되돌린다 — 되돌릴 밑절미가 없으면 담지 않는다."),
        "count": len(stocks),
        # 이미 본 공시. TDnet 은 한 달치만 남기므로 이만큼이면 넉넉하다.
        "docs": sorted(done)[-6000:],
        # 가이던스(통기 예상)까지 뜯어본 공시. docs 와 따로 두는 이유는 부문의
        # seg_done 과 같다 — 한 벌만 적으면 나중에 붙인 수집이 영영 안 돈다.
        "fcst_docs": sorted(fcst_done)[-6000:],
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    if narrs is not None:
        bt = BRIEFS_OUT.with_suffix(".tmp")
        bt.write_text(json.dumps({
            "source": "TDnet 결산단신 첨부 — 経営成績に関する説明",
            "note": "원문(일본어)만 담는다. 한국어는 briefs.py 에 옮긴다.",
            "count": len(narrs), "stocks": narrs,
        }, ensure_ascii=False), encoding="utf-8")
        bt.replace(BRIEFS_OUT)
    if segs is not None:
        save_segments(segs, seg_done, quiet=quiet)
    if not quiet:
        print(f"\n{len(stocks):,}종목 -> {OUT}  (이번에 담은 분기 {got}개 · "
              f"되돌리지 못해 건너뛴 것 {skipped}개)")


def main():
    if "--survey" in sys.argv:
        return survey()
    if "--rows" in sys.argv:
        return dump_rows()
    if "--zip" in sys.argv:
        return dump_zip()
    if "--names" in sys.argv:
        i = sys.argv.index("--names")
        return dump_names([a for a in sys.argv[i + 1:] if not a.startswith("-")])
    if "--seg" in sys.argv:
        i = sys.argv.index("--seg")
        return dump_segments([a for a in sys.argv[i + 1:] if not a.startswith("-")])
    if "--probe" in sys.argv:
        n = [a for a in sys.argv[1:] if a.isdigit()]
        return probe(int(n[0]) if n else 3)
    return collect()


if __name__ == "__main__":
    main()
