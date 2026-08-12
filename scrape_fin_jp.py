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
NOT_TANSHIN = re.compile(r"予想|修正|訂正")

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
NAME_REV = ("NetSales", "OperatingRevenues", "Revenue", "NetSalesOfCompletedConstructionContracts",
            "OrdinaryIncomeBanks", "OperatingRevenuesSpecific", "GrossOperatingRevenues")
NAME_OPI = ("OperatingIncome", "OperatingIncomeLoss", "OrdinaryIncome")
NAME_NI = ("ProfitAttributableToOwnersOfParent", "NetIncome", "ProfitLoss")

# 문맥 이름. 결산단신은 '당기'와 '전년동기'를 함께 싣는다. 당기만 쓴다.
CUR_CTX = re.compile(r"CurrentAccumulatedQ|CurrentYTD|CurrentQuarter|CurrentYear", re.I)
PRI_CTX = re.compile(r"Prior|Previous", re.I)
# 연결이 있으면 연결(Consolidated)을 쓴다. 없으면 단체(NonConsolidated).
CONS_CTX = re.compile(r"Consolidated", re.I)
NONCONS_CTX = re.compile(r"NonConsolidated", re.I)


def local(tag):
    return tag.rsplit("}", 1)[-1]


def read_summary(blob):
    """결산단신 zip -> {항목: 값} + 기간 정보.

    zip 안에 `Summary/` 폴더가 있고 그 안에 요약 XBRL 인스턴스가 들어 있다.
    첨부 재무제표(Attachment/)는 회사마다 제각각이라 요약만 본다.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return None
    names = [n for n in z.namelist()
             if "Summary" in n and n.endswith(".xbrl")]
    if not names:
        names = [n for n in z.namelist() if n.endswith(".xbrl")]
    if not names:
        return None
    try:
        root = ElementTree.fromstring(z.read(names[0]))
    except ElementTree.ParseError:
        return None

    # 문맥(context) -> 기간
    periods = {}
    for ctx in root.iter():
        if local(ctx.tag) != "context":
            continue
        cid = ctx.get("id") or ""
        s = e = ""
        for node in ctx.iter():
            t = local(node.tag)
            if t == "startDate":
                s = (node.text or "").strip()
            elif t == "endDate":
                e = (node.text or "").strip()
            elif t == "instant":
                e = (node.text or "").strip()
        periods[cid] = (s, e)

    vals = {}
    for node in root.iter():
        cid = node.get("contextRef")
        if not cid or node.text is None:
            continue
        name = local(node.tag)
        txt = (node.text or "").strip().replace(",", "")
        if not re.fullmatch(r"-?\d+(\.\d+)?", txt):
            continue
        vals.setdefault(name, []).append((cid, float(txt), periods.get(cid, ("", ""))))
    return vals


def pick(vals, names):
    """당기 연결 값을 고른다. 없으면 단체. 전년동기는 쓰지 않는다."""
    best = None
    for name in names:
        for cid, v, (s, e) in vals.get(name, []):
            if PRI_CTX.search(cid) or not e:
                continue
            score = (2 if CONS_CTX.search(cid) else
                     1 if not NONCONS_CTX.search(cid) else 0)
            if CUR_CTX.search(cid):
                score += 4
            if best is None or score > best[0]:
                best = (score, v, s, e)
        if best and best[0] >= 6:
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
               if TANSHIN.search(r["title"]) and not NOT_TANSHIN.search(r["title"])]
        print(f"  그중 결산단신 {len(tan)}건")
        for r in tan[:3]:
            print(f"    {r['time']} {r['code']} {r['name'][:16]} | {r['title'][:40]}")
        if not tan:
            continue
        r = tan[0]
        print(f"  -> XBRL 받아본다: {r['zip']}")
        try:
            blob = get("https://www.release.tdnet.info/inbs/" + r["zip"], binary=True)
        except Throttled as e:
            print("    zip 실패:", e)
            continue
        if blob is None:
            print("    zip 이 없다")
            continue
        print(f"    zip {len(blob):,} 바이트")
        vals = read_summary(blob)
        if not vals:
            print("    XBRL 을 못 읽었다")
            continue
        print(f"    항목 {len(vals)}가지")
        for label, names in (("매출", NAME_REV), ("영업이익", NAME_OPI), ("순이익", NAME_NI)):
            got = pick(vals, names)
            if got:
                _sc, v, s, e = got
                print(f"      {label:5s} {v:>18,.0f}   {s} ~ {e}")
            else:
                print(f"      {label:5s} 못 찾음")


def main():
    if "--survey" in sys.argv:
        return survey()
    if "--rows" in sys.argv:
        return dump_rows()
    if "--probe" in sys.argv:
        n = [a for a in sys.argv[1:] if a.isdigit()]
        return probe(int(n[0]) if n else 3)
    print("아직 수집 본체는 없다. --probe 로 소스부터 확인한다.")


if __name__ == "__main__":
    main()
