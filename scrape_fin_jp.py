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

LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day}.html"
ZIP_URL = "https://www.release.tdnet.info/inbs/{doc}.zip"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

BACKOFF = (0, 5, 20, 60)
PAUSE = float(os.environ.get("JP_PAUSE", "0.5"))
BACK_DAYS = int(os.environ.get("JP_BACK_DAYS", "10"))   # 며칠치를 훑나
GIVE_UP_AFTER = 6
JP_VER = 1

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
    # **이미 뜯어본 공시는 다시 내려받지 않는다.** 열흘치를 훑으므로 이게 없으면
    # 매 실행마다 사백 건을 다시 받는다. 공시 번호는 문서마다 하나뿐이라 열쇠로 쓴다.
    done = set(load_done())

    got = skipped = streak = 0
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
            if r["zip"] in done:
                continue                    # 지난 실행에서 이미 봤다
            done.add(r["zip"])
            try:
                blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
                streak = 0
            except Throttled as e:
                streak += 1
                print(f"    {r['code']} 막힘: {e}", file=sys.stderr, flush=True)
                if streak >= GIVE_UP_AFTER:
                    print("  연속으로 막혔다. 여기서 접는다.", file=sys.stderr, flush=True)
                    return save(stocks, done, got, skipped)
                continue
            time.sleep(PAUSE)
            if not blob:
                continue
            vals = read_summary(blob)
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
                save(stocks, done, got, skipped, quiet=True)
                print(f"    ...{got}건", flush=True)
    return save(stocks, done, got, skipped)


def load_done():
    """지난 실행에서 이미 뜯어본 공시 번호."""
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("docs", [])
    except (ValueError, OSError):
        return []


def save(stocks, done, got, skipped, quiet=False):
    payload = {
        "source": "TDnet 適時開示 결산단신 (inline XBRL)",
        "note": ("발표 당일에 올라온다. 누계로 실리므로 앞 분기를 빼 분기값으로 "
                 "되돌린다 — 되돌릴 밑절미가 없으면 담지 않는다."),
        "count": len(stocks),
        # 이미 본 공시. TDnet 은 한 달치만 남기므로 이만큼이면 넉넉하다.
        "docs": sorted(done)[-6000:],
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
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
    if "--probe" in sys.argv:
        n = [a for a in sys.argv[1:] if a.isdigit()]
        return probe(int(n[0]) if n else 3)
    return collect()


if __name__ == "__main__":
    main()
