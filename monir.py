# -*- coding: utf-8 -*-
"""일본 월매출 — **회사 IR 페이지의 月次 표**를 읽는다.

TDnet 첨부(`scrape_mon_jp.py`)도 流通ニュース(`scrape_mon_web.py`)도 못 닿는
자리를 메운다. 회원님이 "가용한 정보를 다 긁어서라도" 라고 하신 자리다.

**떠보고 나서 붙였다(84·85차).** 큰 소매·외식 24곳의 제 사이트를 두드려
**21곳이 열렸고 16곳에서 月次 페이지를 찾았다.** 집계 사이트(irbank·minkabu·
가부탄·월차Web)가 전부 데이터센터 IP 를 막는 것과 딴판이다 — 회사 제 사이트는
막을 이유가 없다. 못 연 곳은 시마무라·ABC마트(403), 비쿠카메라(timeout)다.
**막힌 곳을 다시 두드리지 말 것.**

月次 페이지의 생김새가 셋이다.

  표(HTML)    니토리 · 스카이락 · 온워드 · 유나이티드애로우즈 · 세븐＆아이
  PDF 목록    J프론트(2007년치까지 96건) · 야마다 · 쿠라스시 · 패스트리
  JS 로 그림  이온 · 젠쇼 · 물어코퍼 · 파크24  (표도 PDF 도 안 실려 온다)

여기서 읽는 것은 **표**다. PDF 목록은 이미 있는 연장(`pdftext`+`montable`)으로
읽고, JS 쪽은 자료를 어디서 받아오는지 따로 떠봐야 한다.

**회사마다 파서를 쓰지 않는다.** 손으로 잇는 것은 **주소 하나**뿐이고(`IR_SITES`),
표를 읽는 규칙은 하나다. 규칙이 못 읽는 표는 **안 읽고 넘어간다** — 회사마다
예외를 두기 시작하면 백 개의 파서가 되고, 사이트를 고칠 때마다 깨진다.
"""
import re
from collections import Counter
from datetime import date

import monweb

__all__ = ["IR_SITES", "read", "find_monthly", "find_ir",
           "archive_links", "pdf_links", "pdf_when"]

ZEN = monweb.ZEN
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
TABLE_AT = re.compile(r"(?is)<table[^>]*>(.*?)</table>")
A = re.compile(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>')

MONTH_CELL = re.compile(r"^\(?(?:(?:\d{2}|\d{4})年)?(\d{1,2})\s*月(度|分|次)?\)?$")
# 「2026年2月期」 처럼 **결산기말이 적힌** 표. 이게 있으면 해를 정확히 안다.
FY_END = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月期")
# 「2025年度」 — 4월 시작이 일본의 관례다.
FY_JP = re.compile(r"(20\d{2})\s*年度")
YEAR_ONLY = re.compile(r"(20\d{2})\s*年(?!\s*\d{1,2}\s*月期)")

# 값. **부호가 붙은 칸은 안 담는다** — 「△2.0」 이 전년비 증감률인지 비율인지
# 표마다 달라서, 비율로 읽으면 98 을 2 로 적는다. 놓치는 편이 낫다.
RATIO = re.compile(r"^(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[%]?$")
DELTA = re.compile(r"^([+\-△▲]?)\s*(\d{1,3}(?:\.\d+)?)\s*[%％]?$")
YOY_CONTEXT = re.compile(r"前年(?:同月|同期)?比|前年比|前年対比|前年同期比|伸び率|増減率")
GENERIC_SALES = re.compile(r"売上|営業収益|営業収入|取扱高|販売高|月商")
GENERIC_NOT_SALES = re.compile(r"利益|粗利|原価|客数|客単価|店舗数|在庫|面積|坪|人員")


def _txt(s: str) -> str:
    import html as H
    return re.sub(r"\s+", " ", H.unescape(TAG.sub(" ", s))).strip()


def _cell(s: str) -> str:
    return s.translate(ZEN).replace(" ", "")


def _ratio(cell: str):
    """「112.0%」·「112.0」 -> 112.0. 아니면 None.

    비율로 볼 수 있는 폭을 20~500 으로 둔다. 店舗数(787)·客数 같은 열은
    이름표에서 이미 걸러지지만, 폭까지 걸어 두면 한 겹 더 막힌다.
    """
    m = RATIO.match(_cell(cell))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v if 20.0 <= v <= 500.0 else None


def _delta_ratio(cell: str):
    """「3.3」「▲1.1」「-0.6%」 같은 **증감률** -> 103.3 / 98.9 / 99.4.

    月次ページ 중 상당수가 103.3 이 아니라 +3.3 식으로 쓴다. 표 문맥이
    '前年対比/伸び率'임이 확인된 경우에만 이 변환을 쓴다.
    """
    m = DELTA.match(_cell(cell))
    if not m:
        return None
    v = float(m.group(2))
    if m.group(1) in ("-", "△", "▲"):
        v = -v
    # 월매출 증감률로 보기 어려운 값은 버린다.
    return 100.0 + v if -80.0 <= v <= 200.0 else None


def _row_ratios(cells, delta_hint=False):
    """한 줄의 비율 칸들. 100대 지수인지 +3.3식 증감률인지 자동 판별."""
    if delta_hint:
        raw = [_cell(x) for x in cells]
        nums = []
        signed = False
        for x in raw:
            m = DELTA.match(x)
            if m:
                nums.append(float(m.group(2)))
                signed = signed or bool(m.group(1))
        # 부호가 있거나, 대부분 값이 작은 수면 '증감률' 표다.
        small = nums and sum(v <= 60 for v in nums) >= max(2, len(nums) // 2)
        if signed or small:
            return [_delta_ratio(x) for x in cells]
    return [_ratio(x) for x in cells]


def _signed(cell: str) -> bool:
    return bool(re.match(r"^[+\-△▲]", _cell(cell)))


def _month(cell: str):
    m = MONTH_CELL.match(_cell(cell))
    if not m:
        return None
    v = int(m.group(1))
    return v if 1 <= v <= 12 else None


def _fy(text: str):
    """표 언저리 글에서 **회계연도**를 읽는다 -> ('end'|'start', 해) 또는 None.

    「2026年2月期」 는 **끝나는 해와 달**이다 — 결산기말보다 큰 달은 앞해다.
    「2025年度」 는 **시작하는 해**다. 결산기말이 몇 월인지 말해 주지 않으므로
    달 번호가 거꾸로 가는 자리에서 해를 하나 넘기며 읽는다.

    **4월 시작이라고 넘겨짚지 말 것.** 「2025年度」 를 (2026, 3월결산)으로
    보았더니 2월 결산인 온워드(8016)의 3月 이 한 해 앞으로 밀렸다 —
    35달 중 다섯 달이 그렇게 어긋났다. 회계연도의 첫 달은 표가 스스로
    말해 준다(맨 왼쪽 달 칸).
    """
    got = FY_END.findall(text)
    if got:
        y, m = max((int(a), int(b)) for a, b in got)
        return ("end", y, m)
    got = FY_JP.findall(text)
    if got:
        return ("start", max(int(y) for y in got), 0)
    return None


def _years(months, filled, fy, today):
    """달 목록에 해를 붙인다.

    회계연도를 알면 그걸로 가른다 — 결산기말보다 큰 달은 앞해다.

    모르면 **값이 있는 마지막 달**에 오늘을 맞추고 양쪽으로 훑는다. 표의
    마지막 *이름표*에 맞추면 안 된다 — 회사 사이트의 표는 회계연도 열두 달을
    미리 그려 두고 아직 안 온 달을 비워 두기 때문에, 이름표에 맞추면 통째로
    한 해가 밀린다. 니토리(4月~3月 중 8月까지만 참)와 스카이락(1月~12月 중
    8月까지)이 그래서 0달이었다. `montable._years` 가 값 있는 맨 오른쪽 칸을
    기준으로 삼는 것과 같은 규칙이다.
    """
    if fy and fy[0] == "end":
        _k, fy_y, fy_m = fy
        return [fy_y if m <= fy_m else fy_y - 1 for m in months]
    if fy:
        # 「YYYY年度」 — 그 해에 시작한다. 달 번호가 거꾸로 가는 자리마다
        # 해를 하나 넘긴다(결산기말이 몇 월인지 몰라도 된다).
        out, y = [], fy[1]
        for i, m in enumerate(months):
            if i and m < months[i - 1]:
                y += 1
            out.append(y)
        return out
    idx = max((i for i, f in enumerate(filled) if f), default=len(months) - 1)
    out = [None] * len(months)
    out[idx] = today.year if months[idx] <= today.month else today.year - 1
    for i in range(idx - 1, -1, -1):
        out[i] = out[i + 1] - 1 if months[i] > months[i + 1] else out[i + 1]
    for i in range(idx + 1, len(months)):
        out[i] = out[i - 1] + 1 if months[i] < months[i - 1] else out[i - 1]
    return out


def _all_text(rows):
    """표 전체의 글. 회계연도 표기(「2026年2月期」)는 머리줄 위에도 아래에도
    캡션에도 앉는다 — 한 군데만 보면 놓친다."""
    return " ".join(x for r in rows for x in r)


def _label_ok(lab: str, context: str = ""):
    """이름표 -> same · all · yoy · None.

    행 이름이 그냥 「前年比」여도 표 제목이 매출임을 명시하면 월매출로 읽는다.
    반대로 利益/粗利/客数 같은 비매출 표는 문맥까지 함께 막는다.
    """
    if monweb.NOT_SALES.search(lab) or GENERIC_NOT_SALES.search(lab):
        return None
    same, allst = monweb.SAME.search(lab), monweb.ALL.search(lab)
    if same:
        return "same"
    if allst:
        return "all"
    if GENERIC_SALES.search(lab):
        return "yoy"
    if (YOY_CONTEXT.search(lab) and GENERIC_SALES.search(context)
            and not GENERIC_NOT_SALES.search(context)):
        return "yoy"
    return None


# 한 표에서 **이름표가 같은 계열이 둘 이상**이면 그 표를 안 읽는다.
# 세븐＆아이·스시로처럼 브랜드마다 줄이 선 표가 그렇다 — 어느 줄이 회사
# 전체인지 규칙으로 못 가르므로, 엉뚱한 브랜드를 회사 매출로 싣느니 버린다.
def _series(rows, width, today, near, lead=""):
    """표 하나 -> {'YYYY-MM': {'same'|'all': 비율}} 또는 {}.

    `lead` 는 **표 바로 앞의 글**이다. 회계연도가 표 안이 아니라 그 위
    제목에 적히는 일이 흔해서(「2026年2月期 月次売上高」), 그것까지 봐야
    옛 회계연도 표를 제 해에 앉힐 수 있다.
    """
    # (가) 달이 **가로**로 선 표 — 머리줄에 1月…12月
    for r in range(min(4, len(rows))):
        cols = [c for c in range(width) if _month(rows[r][c]) is not None]
        if len(cols) >= 3:
            return _horizontal(rows, width, r, cols, today, near, lead)
    # (나) 달이 **세로**로 선 표 — 첫 칸(또는 둘째 칸)에 4月…
    for c0 in (0, 1):
        mrows = [i for i in range(len(rows))
                 if width > c0 and _month(rows[i][c0]) is not None]
        if len(mrows) >= 3 and mrows[0] >= 1:
            return _vertical(rows, width, c0, mrows, today, near, lead)
    return {}


def _collect(pairs, months, years, today, near, fy):
    """(이름표종류, 값목록) 들 -> 달별 기록.

    **이름표가 같은 계열이 둘 이상이면 그 종류만 버린다.** 세븐＆아이의
    해외사업 표는 7-Eleven,Inc. 과 7-Eleven Australia 의 既存店 이 나란히
    서는데, 어느 쪽이 회사인지 규칙으로 못 가른다. 표를 통째로 버리는 대신
    **갈리는 종류만** 버리면 유나이티드애로우즈처럼 「全社」 한 줄이 또렷한
    표는 살아난다.
    """
    kinds = Counter(k for k, _ in pairs)
    use = [(k, v) for k, v in pairs if kinds[k] == 1]
    if not use:
        return {}
    got = {}
    for kind, vals in use:
        for i, v in enumerate(vals):
            if v is None:
                continue
            got.setdefault(f"{years[i]:04d}-{months[i]:02d}", {})[kind] = v
    if not got:
        return {}
    cur = today.strftime("%Y-%m")
    if fy:
        # 회계연도가 적혀 있으면 그 해를 그대로 믿되, **앞날은 담지 않는다.**
        return {p: v for p, v in got.items() if p < cur}
    # **당월 값은 아직 나올 수 없다.** 월매출은 다음 달 초에 나온다 — 9월
    # 28일에 9월치가 찍힌 표는 우리가 해를 잘못 짚은 것이다. 스시로 페이지의
    # 둘째 표(지난 회계연도)가 그렇게 2026-09 를 만들었다. 어느 해인지 모르는
    # 채로 한 해를 밀어 맞추느니 그 표를 통째로 버린다.
    if max(got) >= cur:
        return {}
    # **지금 갱신되는 표만 읽는다.** 회계연도가 적혀 있지 않은 옛 표를 오늘에
    # 맞춰 읽으면 지난해 값이 올해 자리에 앉는다.
    newest = max(got)
    gap = (today.year - int(newest[:4])) * 12 + (today.month - int(newest[5:]))
    return {} if gap > near else got


def _section_row(row):
    """**절 이름 한 줄** — 칸이 전부 같은 글자고 값이 없는 줄.

    온워드의 표가 한 장에 「合計」 절과 「店舗売上」 절을 담는데, 두 절 모두
    「既存店」 줄을 갖고 있어 같은 종류가 둘로 잡혔다. 절이 바뀌는 자리를
    알면 **첫 절만** 읽을 수 있다 — 회사가 맨 위에 둔 것이 회사 전체다.
    """
    vals = [x for x in row if x.strip()]
    return len(vals) >= 3 and len(set(vals)) == 1 and _ratio(vals[0]) is None


def _horizontal(rows, width, hr, cols, today, near, lead=""):
    months = [_month(rows[hr][c]) for c in cols]
    first = min(cols)
    pairs = []
    ctx = lead + " " + _all_text(rows)
    delta_hint = bool(YOY_CONTEXT.search(ctx))
    for i in range(hr + 1, len(rows)):
        r = rows[i]
        if _section_row(r):
            if pairs:
                break
            continue
        vals = _row_ratios([r[c] for c in cols], delta_hint)
        if sum(v is not None for v in vals) < 2:
            continue
        lab = "".join(r[c] for c in range(first))
        if "累計" in lab or "累積" in lab:
            continue
        kind = _label_ok(lab, lead)
        if kind:
            pairs.append((kind, vals))
    if not pairs:
        return {}
    filled = [any(v[i] is not None for _k, v in pairs) for i in range(len(months))]
    fy = _fy(lead + " " + _all_text(rows))
    return _collect(pairs, months, _years(months, filled, fy, today),
                    today, near, fy)


def _vertical(rows, width, c0, mrows, today, near, lead=""):
    n_hdr = mrows[0]
    months = [_month(rows[i][c0]) for i in mrows]
    labs = ["".join(rows[r][c] for r in range(n_hdr)) for c in range(width)]
    pairs = []
    ctx = lead + " " + _all_text(rows)
    delta_hint = bool(YOY_CONTEXT.search(ctx))
    for c in range(c0 + 1, width):
        vals = _row_ratios([rows[i][c] for i in mrows], delta_hint)
        if sum(v is not None for v in vals) < 2:
            continue
        # 累計 열은 그 달의 단월 값이 아니다. 단월/누계가 나란히 서는
        # Create SD 같은 표에서 같은 kind가 둘로 잡혀 전부 버려지는 것을 막는다.
        if "累計" in labs[c] or "累積" in labs[c]:
            continue
        kind = _label_ok(labs[c], lead)
        if kind:
            pairs.append((kind, vals))
    if not pairs:
        return {}
    filled = [any(v[i] is not None for _k, v in pairs) for i in range(len(months))]
    fy = _fy(lead + " " + _all_text(rows))
    return _collect(pairs, months, _years(months, filled, fy, today),
                    today, near, fy)


def read(page: str, today=None, near: int = 3):
    """月次 페이지 한 장 -> {'YYYY-MM': {'same': 비율, 'all': 비율}}.

    `near` 는 **표가 얼마나 새것이어야 하는가**다. 회계연도가 적혀 있지 않은
    표는 맨 뒤 달을 오늘에 맞춰 읽으므로, 오늘에서 `near` 달 넘게 떨어진
    표는 옛 표로 보고 버린다. 회계연도가 적힌 표에는 이 잣대를 안 건다.

    못 읽으면 빈 사전이다. **지어내지 않는다.**
    """
    today = today or date.today()
    body = SCRIPT.sub(" ", page)
    out, at = {}, 0
    for m in TABLE_AT.finditer(body):
        lead = _txt(body[max(at, m.start() - 400):m.start()])[-160:]
        at = m.end()
        rows = [r for r in monweb.grid(m.group(1)) if r]
        if len(rows) < 2:
            continue
        width = max(len(r) for r in rows)
        if width < 3:
            continue
        rows = [[_txt(x) for x in r] + [""] * (width - len(r)) for r in rows]
        got = _series(rows, width, today, near, lead)
        if not got:
            continue
        # **이미 담은 달을 다시 내는 표는 옛 회계연도 표다 — 버린다.**
        # 회사 사이트는 같은 생김새의 표를 회계연도마다 하나씩 쌓아 둔다.
        # 스시로 페이지의 둘째 표가 첫 표와 달이 똑같은데 값이 달랐다 —
        # 합치면 어느 해 값인지 모르는 숫자가 섞인다. 앞엣것이 새것이다.
        if any(p in out for p in got):
            continue
        out.update(got)
    return out


# ── PDF 목록 ────────────────────────────────────────────────────────────────
# 月次 페이지의 둘째 꼴이다(85차). 표를 안 싣고 **달마다 PDF 한 장**을 거는
# 회사가 많다 — J프론트는 2007년치까지 96건, 야마다는 36건이다. 그 PDF 는
# TDnet 첨부와 같은 꼴이라 **이미 있는 연장**(`pdftext`+`montable`)이 읽는다.
#
# 여기서 하는 일은 둘뿐이다. **월매출 PDF 만 고르고**, 그 PDF 가 **언제 것인지**
# 말해 주는 것. `montable` 은 발표일에서 해를 정하므로 이 값이 어긋나면
# 엉뚱한 달에 숫자가 붙는다 — 그래서 **못 알아보면 안 읽는다.**
PDF_A = re.compile(r"""(?is)<a[^>]+href=["']([^"']+?\.pdf(?:\?[^"']*)?)["'][^>]*>(.*?)</a>""")
# 월매출 PDF 를 가리키는 말. 결산단신·유가증권보고서·회사안내는 여기 없다.
PDF_WANT = re.compile(r"(月次|月度|月別|売上|sokuho|monthly|getuji|month|"
                      r"(?:20\d{2}年\s*)?\d{1,2}月(?:度|分)?|"
                      r"20\d{2}\s*[-–—]?\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))",
                      re.I)
PDF_SKIP = re.compile(r"(決算短信|有価証券報告書|説明資料|会社案内|中期経営計画"
                      r"|統合報告書|コーポレート・?ガバナンス|招集通知|アニュアル)")

HEISEI = re.compile(r"平成\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月")
LAB_YM = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(?:度|次|分)")
# 「2026年8月 月次の売上状況について」 — 달과 「月次」 가 떨어져 있는 꼴.
# **월매출이라는 말이 있을 때만** 맨 달을 보고 읽는다. 「N月期」(결산기말)는
# 피한다 — montable·monweb 과 같은 자리다.
LAB_MON = re.compile(r"月次|月度|月別|月間")
LAB_ANY_YM = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?!\s*期)")
EN_MONTH = {m.lower(): i + 1 for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"))}
LAB_EN_YM = re.compile(
    r"(20\d{2})\s*[-–—]?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*",
    re.I)
URL_YMD = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")
URL_YM = re.compile(r"(?<!\d)(20\d{2})(\d{2})(?!\d)")
URL_PATH_YM = re.compile(r"/(20\d{2})/(0?[1-9]|1[0-2])(?:[^/]*?)\.pdf(?:$|\?)", re.I)


def _next_month(y, m):
    return date(y + (m == 12), 1 if m == 12 else m + 1, 1)


# **이름표는 앞머리만 믿는다.** J프론트의 목록은 `<a>` 가 망가져 있어 옆
# 항목의 글까지 딸려 온다(「2月度連結営業報告 （PDF 115KB） 1月度連…」).
# 그대로 두면 옆 항목의 「平成19年8月」 을 이 PDF 의 날로 읽는다.
LAB_CUT = re.compile(r"(（\s*PDF|\(\s*PDF|\d+\s*KB|\d+\s*MB)")


def _lab_head(label: str) -> str:
    m = LAB_CUT.search(label)
    return (label[:m.start()] if m else label)[:40]


def pdf_when(url: str, label: str):
    """그 PDF 가 **언제 나온 것인가** -> 'YYYY-MM-DD' 또는 None.

    `montable` 이 해를 정하는 데 쓰는 값이다. 어긋나면 엉뚱한 달에 숫자가
    붙으므로 **못 알아보면 None 을 낸다**(그 PDF 는 안 읽는다).

    보고 대상 달만 알 때는 **그 다음 달 1일**을 발표일로 삼는다 — 월매출은
    다음 달 초에 나온다. 어림이지만 해를 가르는 데는 그것으로 넉넉하다.
    """
    lab = _lab_head(label).translate(ZEN)
    m = HEISEI.search(lab)                       # 「平成19年7月度」 (J프론트)
    if m:
        y = 1988 + int(m.group(1))
        mo = int(m.group(2))
        if 1 <= mo <= 12 and 1989 <= y <= 2019:
            return _next_month(y, mo).isoformat()
    m = LAB_YM.search(lab)                       # 「2026年8月度」
    if m and 1 <= int(m.group(2)) <= 12:
        return _next_month(int(m.group(1)), int(m.group(2))).isoformat()
    m = LAB_EN_YM.search(lab)                    # 「2026 -Jun.-」
    if m:
        mo = EN_MONTH.get(m.group(2).lower())
        if mo:
            return _next_month(int(m.group(1)), mo).isoformat()
    if LAB_MON.search(lab):                      # 「2026年8月 月次の売上状況」
        m = LAB_ANY_YM.search(lab)
        if m and 1 <= int(m.group(2)) <= 12:
            return _next_month(int(m.group(1)), int(m.group(2))).isoformat()
    name = url.rsplit("/", 1)[-1]
    for m in URL_YMD.finditer(name):             # 「260907.pdf」 = 발표일
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            got = date(y, mo, d)
        except ValueError:
            continue
        if 2000 <= y <= 2099:
            return got.isoformat()
    m = URL_YM.search(name)                      # 「sokuho202608.pdf」 = 대상 달
    if m and 1 <= int(m.group(2)) <= 12:
        return _next_month(int(m.group(1)), int(m.group(2))).isoformat()
    m = URL_PATH_YM.search(url)                  # 「/2026/08.pdf」 = 대상 달
    if m:
        return _next_month(int(m.group(1)), int(m.group(2))).isoformat()
    m = FY_END.search(lab)                       # 「2026年8月期」 = 결산기말
    if m and 1 <= int(m.group(2)) <= 12:
        return _next_month(int(m.group(1)), int(m.group(2))).isoformat()
    return None


def pdf_links(page: str, base: str):
    """月次 페이지 -> [(주소, 이름표, 발표일)]. **언제인지 모르면 안 담는다.**"""
    import urllib.parse
    out, seen = [], set()
    for href, lab in PDF_A.findall(SCRIPT.sub(" ", page)):
        t = _txt(lab)
        if PDF_SKIP.search(t) or not (PDF_WANT.search(t) or PDF_WANT.search(href)):
            continue
        u = urllib.parse.urljoin(base, href)
        # 같은 파일에 물음표만 붙여 두 번 거는 목록이 있다(패스트리).
        key = u.split("?")[0]
        if key in seen:
            continue
        when = pdf_when(u, t)
        if not when:
            continue
        seen.add(key)
        out.append((key, _lab_head(t).strip(), when))
    return out


# ── 月次 페이지 찾기 ────────────────────────────────────────────────────────
IR_HINT = re.compile(r"(IR情報|投資家|IRライブラリ|株主・投資家|^IR$)")
IR_PATH = re.compile(r"/(ir|investor)s?(/|$|\.)", re.I)
MONTHLY_LINK_HINT = re.compile(
    r"(月次|月度(?:売上|実績|営業|速報|動向)|月別(?:売上|実績|営業)|売上(?:高)?速報|"
    r"営業概況|営業報告|既存店(?:売上)?|全店(?:売上)?|月次KPI|主要KPI|"
    r"月次受注|受注速報|輸送実績|取扱高|Monthly(?:\s+Sales|\s+Results|\s+Data|"
    r"\s+Report|\s+KPI)?)", re.I)
MONTHLY_PATH_HINT = re.compile(
    r"(monthly|getuji|getsuj|month[-_]?sales|monthly[-_]?sales|sales[-_]?flash|"
    r"monthly[-_]?data|monthly[-_]?results)", re.I)


def find_monthly(page: str, base: str):
    """페이지에서 가장 그럴듯한 月次 페이지를 점수로 고른다.

    첫 매치를 바로 택하면 「既存店」이라는 말만 있는 전략 페이지를 월차로
    오인할 수 있다. 月次/Monthly와 URL 경로를 강하게 우선하고 약한 힌트는
    보조점수로만 쓴다.
    """
    import urllib.parse
    cand = []
    for href, lab in A.findall(page):
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        t = _txt(lab)
        u = urllib.parse.urljoin(base, href)
        if u.split("?")[0].lower().endswith(".pdf"):
            continue
        score = 0
        if re.search(r"月次|月度|月別|Monthly", t, re.I):
            score += 100
        if MONTHLY_PATH_HINT.search(href):
            score += 80
        if re.search(r"売上|営業概況|営業報告|KPI|取扱高|輸送実績", t, re.I):
            score += 35
        if re.search(r"既存店|全店", t):
            score += 8
        if MONTHLY_LINK_HINT.search(href):
            score += 30
        if score:
            cand.append((score, -len(u), u))
    return max(cand)[2] if cand else ""


ARCHIVE_LINK_HINT = re.compile(
    r"(20\d{2}年(?:\d{1,2}月期|度)|過去|バックナンバー|Back\s*Number|Archive)", re.I)
ARCHIVE_PATH_HINT = re.compile(r"(?:backnumber|archive|/monthly/20\d{2}(?:/|$))", re.I)


def archive_links(page: str, base: str, limit: int = 4):
    """월차 페이지에서 과거 회계연도 HTML 페이지를 찾는다.

    PDF는 별도 수집기가 처리한다. 같은 도메인의 연도/백넘버 링크만 따라가며
    현재 페이지 자체는 제외한다.
    """
    import urllib.parse
    base_host = urllib.parse.urlparse(base).netloc
    cur = base.split("?")[0].rstrip("/")
    out, seen = [], set()
    for href, lab in A.findall(page):
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        t = _txt(lab)
        if not (ARCHIVE_LINK_HINT.search(t) or ARCHIVE_PATH_HINT.search(href)):
            continue
        u = urllib.parse.urljoin(base, href).split("#")[0]
        if u.split("?")[0].lower().endswith(".pdf"):
            continue
        if urllib.parse.urlparse(u).netloc != base_host:
            continue
        key = u.split("?")[0].rstrip("/")
        if key == cur or key in seen:
            continue
        seen.add(key)
        out.append(u)
        if len(out) >= limit:
            break
    return out

def has_monthly_pdf(page: str, base: str) -> bool:
    """이 페이지에 **월매출 PDF 가 걸려 있는가.** 있으면 여기가 목록이다."""
    return bool(pdf_links(page, base))


def find_ir(page: str, base: str):
    """IR 로 가는 링크. `javascript:void(0)` 같은 것은 링크가 아니다 —
    실제로 그걸 주소로 알고 두드리다 실패한 곳이 둘 있었다(92차)."""
    import urllib.parse
    for href, lab in A.findall(page):
        if href.lower().startswith(("javascript:", "mailto:", "tel:")):
            continue
        t = _txt(lab)
        if IR_HINT.search(t) or IR_PATH.search(href):
            return urllib.parse.urljoin(base, href)
    return ""


# 손으로 잇는 것은 **맨 위 주소 하나**다. 月次 페이지는 위 두 함수가 찾아
# 수집기 파일에 적어 두고, 못 찾을 때만 여기 둘째 칸에 직접 적는다.
# **못 여는 곳은 넣지 않는다**(84차): 시마무라 8227·ABC마트 2670 은 403,
# 비쿠카메라 3048 은 timeout 이다.
IR_SITES = {
    # ── 84·85·86·88차로 열리는 것을 확인한 곳 ─────────────────────────────
    "8267": ("イオン", "https://www.aeon.info/",
             "https://www.aeon.info/ir/library/monthly/"),
    "3086": ("Ｊフロント", "https://www.j-front-retailing.com/",
             "https://www.j-front-retailing.com/ir/finance/monthly.html"),
    "9843": ("ニトリ", "https://www.nitorihd.co.jp/", ""),
    "9983": ("ファストリ", "https://www.fastretailing.com/jp/", ""),
    "7550": ("ゼンショー", "https://www.zensho.co.jp/jp/",
             "https://www.zensho.co.jp/jp/ir/finance/monthly/"),
    "3382": ("セブン＆アイ", "https://www.7andi.com/", ""),
    "9831": ("ヤマダ", "https://www.yamada-holdings.jp/",
             "https://www.yamada-holdings.jp/ir/monthly.html"),
    "3563": ("スシロー", "https://food-and-life.co.jp/", ""),
    "7564": ("ワークマン", "https://www.workman.co.jp/", ""),
    "2695": ("くら寿司", "https://www.kurasushi.co.jp/", ""),
    "7606": ("ユナイテッドアローズ", "https://www.united-arrows.co.jp/", ""),
    "3097": ("物語コーポ", "https://www.monogatari.co.jp/", ""),
    "3197": ("すかいらーく", "https://corp.skylark.co.jp/",
             "https://corp.skylark.co.jp/ir/financial/performance/"),
    "4666": ("パーク２４", "https://www.park24.co.jp/",
             "https://www.park24.co.jp/ir/financial/monthly.html"),
    "8016": ("オンワードＨＤ", "https://www.onward-hd.co.jp/", ""),
    "7476": ("アズワン", "https://www.as-1.co.jp/", ""),
    # ── 90차에 더한 곳 ─────────────────────────────────────────────────
    # 수치가 없는 소매·외식과, 적시공시도 流通ニュース 도 안 닿는 큰 회사들.
    # 月次 페이지를 못 찾으면 수집기가 `sites` 에 적어 두고 사흘 동안 안
    # 두드린다 — 값도 없이 남의 서버를 두드리지 않기 위해서다.
    "2702": ("日本マクドナルド", "https://www.mcd-holdings.co.jp/", ""),
    "3092": ("ＺＯＺＯ", "https://corp.zozo.com/", ""),
    "8237": ("松屋", "https://www.matsuya.com/",
             "https://www.matsuya.com/corp/ir/monthly-highlight/"),
    "8153": ("モスフードサービス", "https://www.mos.co.jp/",
             "https://www.mos.co.jp/company/ir/library/monthly_info/"),
    "3399": ("山岡家", "https://www.yamaokaya.com/", ""),
    "3608": ("ＴＳＩ ＨＤ", "https://www.tsi-holdings.com/", ""),
    "2698": ("キャンドゥ", "https://www.cando-web.co.jp/", ""),
    "9823": ("マミーマートＨＤ", "https://www.mammymart.co.jp/", ""),
    "2780": ("コメ兵ＨＤ", "https://www.komehyo.co.jp/", ""),
    "9278": ("ブックオフＧＨＤ", "https://www.bookoffgroup.co.jp/", ""),
    "8142": ("トーホー", "https://www.to-ho.co.jp/", ""),
    "3093": ("トレファク", "https://www.treasure-f.com/", ""),
    "9861": ("吉野家ＨＤ", "https://www.yoshinoya-holdings.com/", ""),
    "3387": ("クリエイトＲ", "https://www.createrestaurants.com/", ""),
    "7581": ("サイゼリヤ", "https://www.saizeriya.co.jp/", ""),
    "3050": ("ＤＣＭ", "https://www.dcm-hldgs.co.jp/", ""),
    "8273": ("イズミ", "https://www.izumi.co.jp/", ""),
    "9948": ("アークス", "https://www.arcs-g.co.jp/", ""),
    "7545": ("西松屋チェーン", "https://www.24028.jp/", ""),
    "3549": ("クスリのアオキＨＤ", "https://www.kusuri-aoki.co.jp/",
             "https://kusuri-aoki-hd.co.jp/ir/finance/monthry/"),
    "9832": ("オートバックス", "https://www.autobacs.co.jp/", ""),
    "2664": ("カワチ薬品", "https://www.kawachi.co.jp/", ""),
    "3222": ("ＵＳＭＨ", "https://www.usmh.co.jp/", ""),
    "7532": ("ＰＰＩＨ", "https://ppih.co.jp/", ""),
    # **막힌 곳은 넣지 않는다**(92차): 게오HD 2681 · 아오야마상사 8219 는 403,
    # 겐키드러그 9267 은 주소가 안 닿는다. 다시 두드리지 말 것.
    "8279": ("ヤオコー", "https://www.yaoko-net.com/", ""),
    "7616": ("コロワイド", "https://www.colowide.co.jp/", ""),

    # ── 대형 월차 회사: 자동 discovery 를 기다리지 않고 공식 URL을 우선 연결 ──
    "7453": ("良品計画", "https://www.ryohin-keikaku.jp/",
             "https://www.ryohin-keikaku.jp/ir/monthly"),
    "9989": ("サンドラッグ", "https://www.sundrug.co.jp/",
             "https://www.sundrug.co.jp/ir/irdata/monthly"),
    "9142": ("ＪＲ九州", "https://www.jrkyushu.co.jp/",
             "https://www.jrkyushu.co.jp/company/ir/finance/monthly/"),
    "9020": ("ＪＲ東日本", "https://www.jreast.co.jp/",
             "https://www.jreast.co.jp/company/ir/library/monthly/"),
    "8233": ("高島屋", "https://www.takashimaya.co.jp/",
             "https://www.takashimaya.co.jp/corp/topics/"),
    "8242": ("Ｈ２Ｏリテイリング", "https://www.h2o-retailing.co.jp/",
             "https://www.h2o-retailing.co.jp/ja/ir/library/monthly.html"),
    "3099": ("三越伊勢丹ＨＤ", "https://www.imhds.co.jp/",
             "https://www.imhds.co.jp/corporate/ir/finance/monthly-report.html"),
    "9021": ("ＪＲ西日本", "https://www.westjr.co.jp/",
             "https://www.westjr.co.jp/company/ir/finance/monthly/"),
    "9022": ("ＪＲ東海", "https://company.jr-central.co.jp/", ""),
    "9023": ("東京メトロ", "https://www.tokyometro.jp/corporate/", ""),
    "3148": ("クリエイトＳＤＨＤ", "https://www.createsdhd.co.jp/",
             "https://www.createsdhd.co.jp/ir/monthly/"),
    "2502": ("アサヒグループ", "https://www.asahigroup-holdings.com/",
             "https://www.asahigroup-holdings.com/ir/financial_data/monthly_data/"),
    "2670": ("ＡＢＣマート", "https://www.abc-mart.co.jp/",
             "https://www.abc-mart.co.jp/ir/getsujijoho.html"),
    "3038": ("神戸物産", "https://www.kobebussan.co.jp/",
             "https://www.kobebussan.co.jp/ir/monthly.php"),
    "2593": ("伊藤園", "https://www.itoen.co.jp/",
             "https://www.itoen.co.jp/ir/library/monthly_sales_backnumber/"),
}


# ── 스스로 시험 ─────────────────────────────────────────────────────────────
# `python monir.py` — 85차에 실제로 본 다섯 꼴을 그대로 밟는다.
# **규칙을 넓히면 반드시 뭔가가 새로 새어든다. 넓히기 전후로 돌린다.**
def _selftest():                                          # pragma: no cover
    T = date(2026, 9, 28)
    bad = []

    def eq(name, got, want):
        if got != want:
            bad.append(f"{name}\n      받음 {got}\n      바람 {want}")

    # (가) 달이 **세로** · 머리줄이 두 겹 — 니토리(9843) 꼴.
    #     客数·店舗数 열은 이름표에서 걸러져야 한다.
    nitori = """<table>
      <tr><th></th><th colspan="2">売上（%）</th><th colspan="2">客数（%）</th>
          <th>店舗数</th></tr>
      <tr><th></th><th>既存店</th><th>全店</th><th>既存店</th><th>全店</th>
          <th>全店</th></tr>
      <tr><td>4月</td><td>96.8</td><td>99.8</td><td>96.5</td><td>100.3</td>
          <td>787</td></tr>
      <tr><td>5月</td><td>107.1</td><td>110.5</td><td>107.5</td><td>111.9</td>
          <td>788</td></tr>
      <tr><td>6月</td><td>101.0</td><td>104.2</td><td>100.1</td><td>103.0</td>
          <td>790</td></tr>
      <tr><td>7月</td><td>99.4</td><td>102.8</td><td>98.0</td><td>101.5</td>
          <td>791</td></tr>
      <tr><td>8月</td><td>103.3</td><td>106.6</td><td>102.2</td><td>105.0</td>
          <td>793</td></tr></table>"""
    eq("(가) 달이 세로", read(nitori, T), {
        "2026-04": {"same": 96.8, "all": 99.8},
        "2026-05": {"same": 107.1, "all": 110.5},
        "2026-06": {"same": 101.0, "all": 104.2},
        "2026-07": {"same": 99.4, "all": 102.8},
        "2026-08": {"same": 103.3, "all": 106.6}})

    # (나) 달이 **가로** · rowspan 으로 묶인 이름표 — 스카이락(3197) 꼴.
    #     「累計」 열은 달이 아니므로 안 들어가야 한다.
    skylark = """<table>
      <tr><th colspan="2"></th><th>6月</th><th>7月</th><th>8月</th>
          <th>累計</th></tr>
      <tr><th rowspan="2">全店</th><th>売上高前年比</th><td>105.2%</td>
          <td>108.5%</td><td>107.2％</td><td>108.1%</td></tr>
      <tr><th>客数前年比</th><td>98.8%</td><td>100.7%</td><td>100.4%</td>
          <td>101.3%</td></tr>
      <tr><th rowspan="2">既存店</th><th>売上高前年比</th><td>101.9%</td>
          <td>105.8%</td><td>104.6％</td><td>105.5%</td></tr>
      <tr><th>客数前年比</th><td>95.5%</td><td>98.1%</td><td>97.9%</td>
          <td>98.4%</td></tr></table>"""
    eq("(나) 달이 가로", read(skylark, T), {
        "2026-06": {"all": 105.2, "same": 101.9},
        "2026-07": {"all": 108.5, "same": 105.8},
        "2026-08": {"all": 107.2, "same": 104.6}})

    # (다) **브랜드마다 줄이 선 표는 안 읽는다** — 세븐＆아이(3382)·스시로(3563)
    #     꼴이다. 어느 줄이 회사 전체인지 규칙으로 못 가르므로, 엉뚱한 브랜드를
    #     회사 매출로 싣느니 통째로 버린다. **오검출 하나가 놓침 하나보다 나쁘다.**
    multi = """<table>
      <tr><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>Ａブランド既存店売上高</th><td>103.1</td><td>104.0</td>
          <td>105.2</td></tr>
      <tr><th>Ｂブランド既存店売上高</th><td>98.2</td><td>97.1</td>
          <td>99.0</td></tr></table>"""
    eq("(다) 브랜드별 표는 버린다", read(multi, T), {})

    # (라) **회계연도가 적힌 표**는 그 해를 그대로 믿는다 — 「2026年2月期」 의
    #     3月 은 2025년 3월이다. 오늘에 맞춰 읽으면 한 해가 통째로 밀린다.
    fy = """<table>
      <tr><td colspan="4">2026年2月期 月次売上高</td></tr>
      <tr><th></th><th>12月</th><th>1月</th><th>2月</th></tr>
      <tr><th>既存店売上高</th><td>104.1</td><td>99.8</td><td>101.2</td></tr>
      </table>"""
    eq("(라) 회계연도 표기", read(fy, T), {
        "2025-12": {"same": 104.1},
        "2026-01": {"same": 99.8},
        "2026-02": {"same": 101.2}})

    # (마) **부호가 붙은 칸은 안 담는다.** 「△2.0」 이 증감률인지 비율인지
    #     표마다 달라, 비율로 읽으면 98 을 2 로 적는다.
    signed = """<table>
      <tr><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>既存店売上高</th><td>△2.0</td><td>1.5</td><td>3.1</td></tr>
      </table>"""
    eq("(마) 부호 붙은 칸", read(signed, T), {})

    # (마2) 표가 **前年対比伸び率**이라고 명시하면 +3.3/▲1.1은
    # 103.3/98.9로 읽는다 — 츠루하HD 공식 월차 꼴.
    delta = """<h3>連結月次前年対比伸び率</h3><table>
      <tr><th></th><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>既存店</th><th>単月売上</th><td>▲1.1</td><td>1.7</td><td>1.5</td></tr>
      <tr><th>既存店</th><th>客数</th><td>▲4.2</td><td>▲1.8</td><td>▲2.0</td></tr>
      </table>"""
    eq("(마2) 증감률 표", read(delta, T), {
        "2026-06": {"same": 98.9},
        "2026-07": {"same": 101.7},
        "2026-08": {"same": 101.5}})

    # (마3) 既存店/全店이 없어도 명확한 売上高 전년비 한 줄은 generic yoy로 읽는다.
    generic = """<table>
      <tr><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>売上高前年比</th><td>106.1</td><td>108.2</td><td>109.4</td></tr>
      </table>"""
    eq("(마3) 일반 월매출 YoY", read(generic, T), {
        "2026-06": {"yoy": 106.1},
        "2026-07": {"yoy": 108.2},
        "2026-08": {"yoy": 109.4}})

    # (바) **낡은 표는 안 읽는다.** 회계연도 표기가 없는데 맨 뒤 달이 오늘에서
    #     멀면 지난해 표다 — 오늘에 맞춰 읽으면 옛 값이 새 값 자리에 앉는다
    #     (ABC마트 카드가 2024년 값을 맨 위에 걸던 것과 같은 병이다).
    old = """<table>
      <tr><th></th><th>1月</th><th>2月</th><th>3月</th></tr>
      <tr><th>既存店売上高</th><td>104.1</td><td>99.8</td><td>101.2</td></tr>
      </table>"""
    eq("(바) 낡은 표", read(old, T), {})

    # (사) 月次 링크 찾기
    eq("(사) 링크 찾기",
       find_monthly('<a href="/ir/monthly/">月次売上高</a>',
                    "https://x.co.jp/"),
       "https://x.co.jp/ir/monthly/")

    # (아) **값이 있는 마지막 달에 해를 맞춘다** — 니토리(9843) 꼴.
    #     회사 사이트는 회계연도 열두 달을 미리 그려 두고 안 온 달을 비운다.
    #     표의 마지막 이름표(3月)에 맞추면 4月~12月 이 통째로 지난해가 된다.
    nitori12 = ("""<table>
      <tr><th></th><th colspan="2">売上（%）</th><th>店舗数</th></tr>
      <tr><th></th><th>既存店</th><th>全店</th><th>全店</th></tr>"""
                + "".join(f"<tr><td>{m}月</td><td>{v[0]}</td><td>{v[1]}</td>"
                          f"<td>79{m%10}</td></tr>"
                          for m, v in [(4, ("96.8", "99.8")),
                                       (5, ("107.1", "110.5")),
                                       (6, ("86.8", "89.2")),
                                       (7, ("103.7", "106.2")),
                                       (8, ("104.4", "107.1"))])
                + "".join(f"<tr><td>{m}月</td><td></td><td></td><td></td></tr>"
                          for m in (9, 10, 11, 12, 1, 2, 3))
                + "</table>")
    eq("(아) 값 있는 마지막 달", sorted(read(nitori12, T)),
       ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"])

    # (자) 같은 병이 **달이 가로로 선 표**에도 있다 — 스카이락(3197) 꼴.
    #     1月~12月 중 8月까지만 차 있는데 12月 에 맞추면 한 해가 밀린다.
    sky12 = ("<table><tr><th></th><th></th>"
             + "".join(f"<th>{m}月</th>" for m in range(1, 13))
             + "<th>累計</th></tr>"
             + '<tr><th>全店</th><th>売上高前年比</th>'
             + "".join(f"<td>{v}%</td>" for v in
                       ("112.0", "107.4", "103.9", "108.4", "114.9",
                        "105.2", "108.5", "107.2"))
             + "<td></td><td></td><td></td><td></td><td>108.1%</td></tr>"
             + '<tr><th>既存店</th><th>客数前年比</th>'
             + "".join(f"<td>{v}%</td>" for v in
                       ("105.6", "100.2", "97.4", "101.4", "106.0",
                        "98.8", "100.7", "100.4"))
             + "<td></td><td></td><td></td><td></td><td>101.3%</td></tr>"
             + "</table>")
    got = read(sky12, T)
    eq("(자) 가로 표의 마지막 찬 달", sorted(got),
       [f"2026-{m:02d}" for m in range(1, 9)])
    eq("(자) 객수는 안 담는다", got["2026-08"], {"all": 107.2})

    # (차) **한 표에 절이 둘이면 첫 절만 읽는다** — 온워드(8016) 꼴.
    #     「合計」 절과 「店舗売上」 절이 둘 다 「既存店」 줄을 갖고 있어
    #     같은 종류가 둘로 잡혔고, 그 바람에 표가 통째로 버려졌다.
    onward = """<table>
      <tr><th colspan="3"></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><td colspan="6">合計</td></tr>
      <tr><td></td><td colspan="2">既存店</td><td>92.5</td><td>104.8</td>
          <td>96.3</td></tr>
      <tr><td></td><td colspan="2">全店</td><td>92.3</td><td>104.8</td>
          <td>96.4</td></tr>
      <tr><td colspan="6">店舗売上</td></tr>
      <tr><td></td><td></td><td>既存店</td><td>92.2</td><td>105.2</td>
          <td>98.4</td></tr></table>"""
    eq("(차) 첫 절만", read(onward, T), {
        "2026-06": {"same": 92.5, "all": 92.3},
        "2026-07": {"same": 104.8, "all": 104.8},
        "2026-08": {"same": 96.3, "all": 96.4}})

    # (카) **갈리는 종류만 버린다** — 유나이티드애로우즈(7606) 꼴.
    #     브랜드마다 既存 줄이 서지만 「全社」 는 한 줄뿐이다. 표를 통째로
    #     버리면 그 한 줄까지 잃는다.
    ua = """<table>
      <tr><th colspan="2"></th><th>６月</th><th>７月</th><th>８月</th></tr>
      <tr><td>Company Total</td><td>全社</td><td>101.4</td><td>107.0</td>
          <td>111.1</td></tr>
      <tr><td>Retail</td><td>小売 既存</td><td>95.4</td><td>105.8</td>
          <td>110.3</td></tr>
      <tr><td>Online</td><td>ネット通販 既存</td><td>112.2</td><td>103.1</td>
          <td>111.5</td></tr></table>"""
    eq("(카) 全社 한 줄은 살린다", read(ua, T), {
        "2026-06": {"all": 101.4}, "2026-07": {"all": 107.0},
        "2026-08": {"all": 111.1}})

    # (타) **당월 값이 찍힌 표는 해를 잘못 짚은 것이다.** 월매출은 다음 달
    #     초에 나온다 — 9월 28일에 9월치가 있을 수 없다. 스시로(3563)
    #     페이지의 둘째 표(지난 회계연도)가 그렇게 2026-09 를 만들었다.
    cur_month = """<table>
      <tr><th></th><th>7月</th><th>8月</th><th>9月</th></tr>
      <tr><th>既存店売上高</th><td>110.2</td><td>109.4</td><td>104.7</td></tr>
      </table>"""
    eq("(타) 당월 값", read(cur_month, T), {})

    # (파) **한 페이지에 같은 달의 표가 둘이면 뒤엣것은 옛 회계연도다.**
    two = """<table>
      <tr><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>既存店売上高</th><td>100.4</td><td>107.7</td><td>109.6</td></tr>
      </table><table>
      <tr><th></th><th>6月</th><th>7月</th><th>8月</th></tr>
      <tr><th>既存店売上高</th><td>114.5</td><td>110.2</td><td>114.8</td></tr>
      </table>"""
    eq("(파) 달이 겹치는 뒤 표", read(two, T), {
        "2026-06": {"same": 100.4}, "2026-07": {"same": 107.7},
        "2026-08": {"same": 109.6}})

    # (거) **「YYYY年度」 는 시작하는 해다 — 4월 시작이라고 넘겨짚지 않는다.**
    #     온워드(8016)는 2월 결산이라 「2025年度」 가 2025년 3월에 시작한다.
    #     4월 시작으로 보면 3月 한 칸이 한 해 앞으로 밀린다(35달 중 다섯 달이
    #     그렇게 어긋났다). 회계연도의 첫 달은 표가 스스로 말해 준다.
    nendo = """<h3>2021年度 月次売上高</h3><table>
      <tr><th colspan="3"></th><th>3月</th><th>4月</th><th>5月</th></tr>
      <tr><td></td><td colspan="2">既存店</td><td>116.1</td><td>198.7</td>
          <td>127.8</td></tr></table>"""
    eq("(거) 年度 는 시작하는 해", sorted(read(nendo, T)),
       ["2021-03", "2021-04", "2021-05"])

    # 하반기 표도 같은 규칙으로 읽힌다 — 달 번호가 거꾸로 가는 자리에서 해가
    # 하나 넘어간다(결산기말이 몇 월인지 몰라도 된다).
    nendo2 = """<h3>2021年度 月次売上高</h3><table>
      <tr><th colspan="3"></th><th>12月</th><th>1月</th><th>2月</th></tr>
      <tr><td></td><td colspan="2">既存店</td><td>104.1</td><td>99.8</td>
          <td>101.2</td></tr></table>"""
    eq("(거) 年度 하반기", sorted(read(nendo2, T)),
       ["2021-12", "2022-01", "2022-02"])

    # (하) **표 위 제목의 회계연도도 본다.** 표 안에 적히지 않고 바로 위
    #     제목에만 있는 일이 흔하다.
    lead = """<h3>2025年2月期 月次売上高</h3><table>
      <tr><th></th><th>12月</th><th>1月</th><th>2月</th></tr>
      <tr><th>既存店売上高</th><td>104.1</td><td>99.8</td><td>101.2</td></tr>
      </table>"""
    eq("(하) 제목의 회계연도", sorted(read(lead, T)),
       ["2024-12", "2025-01", "2025-02"])

    # (너) **PDF 가 언제 것인지 못 알아보면 안 읽는다.** montable 이 발표일에서
    #     해를 정하므로 이 값이 어긋나면 엉뚱한 달에 숫자가 붙는다.
    for u, lab, want in [
        # 야마다: 파일 이름이 발표일(YYMMDD)
        ("https://x.jp/ir/monthly/2026/260907.pdf", "8月 月次速報", "2026-09-07"),
        # J프론트: 이름표가 연호. 보고 달의 **다음 달 1일**을 발표일로 삼는다
        ("https://x.jp/ir/pdf/monthly/h19_2007/mon0707.pdf",
         "平成19年7月度 （PDF 73KB）", "2007-08-01"),
        # Asahi 영문 archive 이름표
        ("https://x.jp/ir/monthly/202606.pdf",
         "2026 -Jun.-", "2026-07-01"),
        ("https://x.jp/_data/ir_monthly/1102_renketsu110315.pdf",
         "2月度連結営業報告", "2011-03-15"),
        # 패스트리: 한 장에 회계연도 열두 달. 결산기말 다음 달.
        ("https://x.jp/ir/monthly/pdf/MonthlySales_2026.pdf",
         "2026年8月期 (80KB)", "2026-09-01"),
        # UA: 이름표는 결산기말(2027年3月期)인데 **파일 이름이 보고 달**이다.
        # 이름표를 먼저 믿으면 앞날(2027-04)이 된다.
        ("https://x.jp/wp-content/uploads/2026/09/sokuho202608.pdf",
         "2027年3月期 月次売上概況", "2026-09-01"),
        # 알 길이 없는 것과 월매출이 아닌 것은 None
        ("https://x.jp/ir/upload/hp0916month.pdf", "月次報告書を更新しました", None),
        ("https://x.jp/ir/pdf/2026q2.pdf", "決算短信", None),
    ]:
        eq("(너) PDF 발표일 " + u[-22:], pdf_when(u, lab), want)

    # 목록에서 고르는 것도 같은 규칙이다 — 결산단신·설명자료는 안 담고,
    # 언제인지 모르는 것도 안 담는다.
    lst = ("""<a href="/ir/monthly/2026/260907.pdf">8月 月次速報</a>"""
           """<a href="/ir/pdf/2026q2.pdf">決算短信</a>"""
           """<a href="/ir/upload/hp0916month.pdf">月次報告書</a>""")
    eq("(너) PDF 목록", pdf_links(lst, "https://x.jp/"),
       [("https://x.jp/ir/monthly/2026/260907.pdf", "8月 月次速報", "2026-09-07")])

    # **이름표는 앞머리만 믿는다.** J프론트의 목록은 `<a>` 가 망가져 옆 항목의
    # 글까지 딸려 온다 — 그대로 두면 옆 항목의 연호를 이 PDF 의 날로 읽는다.
    eq("(너) 딸려 온 이름표",
       pdf_when("https://x.jp/_data/ir_monthly/1102_renketsu110315.pdf",
                "2月度連結営業報告 （PDF 115KB） 1月度連結営業報告 平成19年8月度"),
       "2011-03-15")
    # **달과 「月次」 가 떨어져 있는 꼴**(브ックオフ 9278). 월매출이라는 말이
    # 있을 때만 맨 달을 읽고, 「N月期」(결산기말)는 피한다.
    eq("(너) 달과 月次 가 떨어진 이름표",
       pdf_when("https://ssl4.eir-parts.net/doc/9278/tdnet/2881878/00.pdf",
                "2026年8月 月次の売上状況について"), "2026-09-01")
    eq("(너) 월매출이 아닌 공시",
       pdf_when("https://ssl4.eir-parts.net/doc/9278/tdnet/2789577/00.pdf",
                "業績予想の修正に関するお知らせ"), None)

    # (더) **PDF 는 月次 '페이지' 로 고르지 않는다.** 달마다 PDF 한 장을 거는
    #     회사에서 첫 PDF 를 페이지로 잡으면 그 한 장만 보고 목록을 놓친다 —
    #     그럴 때는 **그 PDF 들이 걸린 페이지 자체**가 목록이다.
    both = ('<a href="https://s.jp/doc/9278/tdnet/2881878/00.pdf">'
            '2026年8月 月次の売上状況について</a>'
            '<a href="/ir/monthly/">月次売上</a>')
    eq("(더) PDF 는 페이지가 아니다", find_monthly(both, "https://x.jp/"),
       "https://x.jp/ir/monthly/")
    only_pdf = ('<a href="https://s.jp/doc/9278/tdnet/2881878/00.pdf">'
                '2026年8月 月次の売上状況について</a>')
    eq("(더) PDF 만 있으면 여기가 목록", find_monthly(only_pdf, "https://x.jp/"), "")
    eq("(더) PDF 목록인가", has_monthly_pdf(only_pdf, "https://x.jp/"), True)
    # `javascript:void(0)` 는 링크가 아니다 — 그걸 주소로 알고 두드리다
    # 실패한 곳이 둘 있었다(92차).
    eq("(더) javascript 는 링크가 아니다",
       find_ir('<a href="javascript:void(0);">IR情報</a>'
               '<a href="/ir/top/">株主・投資家情報</a>', "https://x.jp/"),
       "https://x.jp/ir/top/")

    # 같은 파일에 물음표만 붙여 두 번 거는 목록이 있다(패스트리).
    # (더) **PDF 는 月次 '페이지' 로 고르지 않는다.** 달마다 PDF 한 장을 거는
    #     회사에서 첫 PDF 를 페이지로 잡으면 그 한 장만 보고 목록을 놓친다 —
    #     그럴 때는 **그 PDF 들이 걸린 페이지 자체**가 목록이다.
    both = ('<a href="https://s.jp/doc/9278/tdnet/2881878/00.pdf">'
            '2026年8月 月次の売上状況について</a>'
            '<a href="/ir/monthly/">月次売上</a>')
    eq("(더) PDF 는 페이지가 아니다", find_monthly(both, "https://x.jp/"),
       "https://x.jp/ir/monthly/")
    only_pdf = ('<a href="https://s.jp/doc/9278/tdnet/2881878/00.pdf">'
                '2026年8月 月次の売上状況について</a>')
    eq("(더) PDF 만 있으면 여기가 목록", find_monthly(only_pdf, "https://x.jp/"), "")
    eq("(더) PDF 목록인가", has_monthly_pdf(only_pdf, "https://x.jp/"), True)
    # `javascript:void(0)` 는 링크가 아니다 — 그걸 주소로 알고 두드리다
    # 실패한 곳이 둘 있었다(92차).
    eq("(더) javascript 는 링크가 아니다",
       find_ir('<a href="javascript:void(0);">IR情報</a>'
               '<a href="/ir/top/">株主・投資家情報</a>', "https://x.jp/"),
       "https://x.jp/ir/top/")

    eq("(너) 물음표만 다른 같은 파일",
       len(pdf_links('<a href="/p/MonthlySales_2026.pdf?=0902">2026年8月期</a>'
                     '<a href="/p/MonthlySales_2026.pdf">2026年8月期 (80KB)</a>',
                     "https://x.jp/")), 1)

    # v6: URL 경로 자체가 대상월인 PDF (아사히 꼴).
    eq("(버) /YYYY/MM.pdf 대상월",
       pdf_when("https://x.jp/monthly/2026/08.pdf", "Monthly Sales"),
       "2026-09-01")

    # v6: 약한 既存店 링크가 먼저 있어도 진짜 月次 링크를 골라야 한다.
    scored = ('<a href="/ir/strategy/existing_store/">既存店の成長</a>'
              '<a href="/ir/financial/monthly/">月次売上高</a>')
    eq("(서) 월차 링크 점수", find_monthly("".join(scored), "https://x.jp/"),
       "https://x.jp/ir/financial/monthly/")

    # v6: 행 이름이 前年比뿐이어도 표 제목이 매출이면 읽는다.
    eq("(어) 문맥형 前年比", _label_ok("前年比", "月次売上高"), "yoy")
    eq("(어) 비매출 문맥 차단", _label_ok("前年比", "粗利益"), None)
    # v7: 달 이름표에 연도가 붙는 HTML 표 (무인양품/Create SD 꼴).
    ym = """<h3>2026年8月期 国内売上</h3><table>
      <tr><th></th><th>既存店 売上</th><th>全店 売上</th></tr>
      <tr><td>25年9月</td><td>98.9</td><td>108.2</td></tr>
      <tr><td>25年10月</td><td>115.8</td><td>126.2</td></tr>
      <tr><td>26年1月</td><td>102.6</td><td>110.2</td></tr>
      </table>"""
    eq("(저) 연도 붙은 월칸", read(ym, T), {
        "2025-09": {"same": 98.9, "all": 108.2},
        "2025-10": {"same": 115.8, "all": 126.2},
        "2026-01": {"same": 102.6, "all": 110.2}})

    # v7: 과거 회계연도 HTML 링크를 같은 도메인에서만 따라간다.
    arc = ('<a href="/ir/monthly/2025/domestic">2025年8月期</a>'
           '<a href="/ir/monthly/2024/domestic">2024年8月期</a>'
           '<a href="https://other.example/2023">2023年度</a>')
    eq("(처) 과거 월차 링크", archive_links("".join(arc), "https://x.jp/ir/monthly/"),
       ["https://x.jp/ir/monthly/2025/domestic",
        "https://x.jp/ir/monthly/2024/domestic"])
    # v7: 단월/누계가 나란히 있는 표에서는 누계 열을 버린다.
    cum = """<h3>2027年5月期 月次業績</h3><table>
      <tr><th></th><th colspan="2">既存店</th><th colspan="2">全店</th></tr>
      <tr><th></th><th>単月 売上</th><th>累計 売上</th>
          <th>単月 売上</th><th>累計 売上</th></tr>
      <tr><td>26年6月</td><td>100.0</td><td>100.0</td><td>103.6</td><td>103.6</td></tr>
      <tr><td>26年7月</td><td>103.4</td><td>101.7</td><td>107.1</td><td>105.3</td></tr>
      <tr><td>26年8月</td><td>101.8</td><td>101.7</td><td>105.5</td><td>105.4</td></tr>
      </table>"""
    eq("(커) 단월/누계 열", read(cum, T), {
        "2026-06": {"same": 100.0, "all": 103.6},
        "2026-07": {"same": 103.4, "all": 107.1},
        "2026-08": {"same": 101.8, "all": 105.5}})
    # v7: 가로형 표도 단월/누계를 섞지 않는다(산드럭 꼴).
    hc = """<h3>2026年度 売上高前年同月比較表</h3><table>
      <tr><th></th><th></th><th>4月</th><th>5月</th><th>6月</th></tr>
      <tr><th rowspan="2">既存店</th><th>単月</th><td>2.9</td><td>4.6</td><td>-4.4</td></tr>
      <tr><th>累計</th><td>2.9</td><td>3.7</td><td>0.9</td></tr>
      <tr><th rowspan="2">全店</th><th>単月</th><td>5.9</td><td>7.3</td><td>-1.9</td></tr>
      <tr><th>累計</th><td>5.9</td><td>6.6</td><td>3.6</td></tr>
      </table>"""
    eq("(터) 가로 단월/누계", read(hc, T), {
        "2026-04": {"same": 102.9, "all": 105.9},
        "2026-05": {"same": 104.6, "all": 107.3},
        "2026-06": {"same": 95.6, "all": 98.1}})
    if bad:
        print("monir 스스로 시험 실패:")
        for b in bad:
            print("  -", b)
        raise SystemExit(1)
    print("monir 스스로 시험: 통과")


if __name__ == "__main__":                                # pragma: no cover
    _selftest()
