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

__all__ = ["IR_SITES", "read", "find_monthly"]

ZEN = monweb.ZEN
TAG = re.compile(r"<[^>]+>")
SCRIPT = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
TABLE_AT = re.compile(r"(?is)<table[^>]*>(.*?)</table>")
A = re.compile(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>')

MONTH_CELL = re.compile(r"^\(?(\d{1,2})\s*月(度|分|次)?\)?$")
# 「2026年2月期」 처럼 **결산기말이 적힌** 표. 이게 있으면 해를 정확히 안다.
FY_END = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月期")
# 「2025年度」 — 4월 시작이 일본의 관례다.
FY_JP = re.compile(r"(20\d{2})\s*年度")
YEAR_ONLY = re.compile(r"(20\d{2})\s*年(?!\s*\d{1,2}\s*月期)")

# 값. **부호가 붙은 칸은 안 담는다** — 「△2.0」 이 전년비 증감률인지 비율인지
# 표마다 달라서, 비율로 읽으면 98 을 2 로 적는다. 놓치는 편이 낫다.
RATIO = re.compile(r"^(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[%]?$")


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


def _signed(cell: str) -> bool:
    return bool(re.match(r"^[+\-△▲]", _cell(cell)))


def _month(cell: str):
    m = MONTH_CELL.match(_cell(cell))
    if not m:
        return None
    v = int(m.group(1))
    return v if 1 <= v <= 12 else None


def _fy(text: str):
    """표 언저리 글에서 **회계연도**를 읽는다 -> (해, 결산기말 달) 또는 None.

    「2026年2月期」 면 (2026, 2) — 그 표의 달들은 결산기말보다 큰 달이 앞해다.
    「2025年度」 면 (2026, 3) 로 본다(4월 시작이 일본의 관례다).
    """
    got = FY_END.findall(text)
    if got:
        y, m = max((int(a), int(b)) for a, b in got)
        return y, m
    got = FY_JP.findall(text)
    if got:
        return max(int(y) for y in got) + 1, 3
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
    if fy:
        fy_y, fy_m = fy
        return [fy_y if m <= fy_m else fy_y - 1 for m in months]
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


def _label_ok(lab: str):
    """이름표 -> 'same' · 'all' · None. `monweb` 과 **같은 사전**을 쓴다."""
    if monweb.NOT_SALES.search(lab):
        return None
    same, allst = monweb.SAME.search(lab), monweb.ALL.search(lab)
    if not (same or allst):
        return None
    # 「既存店」·「全店」 만 적고 무엇의 값인지는 표 바깥이 말하는 표가 있다
    # (이온 기사가 그랬다). 매출 낱말이 없어도 받되, 객수·점포수는 위에서
    # 이미 쳐냈다.
    return "same" if same else "all"


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
    for i in range(hr + 1, len(rows)):
        r = rows[i]
        if _section_row(r):
            # 첫 절을 다 읽었으면 여기서 멈춘다. 아직 아무것도 못 읽었으면
            # 이 줄이 첫 절의 머리다.
            if pairs:
                break
            continue
        if any(_signed(r[c]) for c in cols):
            continue
        vals = [_ratio(r[c]) for c in cols]
        if sum(v is not None for v in vals) < 2:
            continue
        kind = _label_ok("".join(r[c] for c in range(first)))
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
    for c in range(c0 + 1, width):
        if any(_signed(rows[i][c]) for i in mrows):
            continue
        vals = [_ratio(rows[i][c]) for i in mrows]
        if sum(v is not None for v in vals) < 2:
            continue
        kind = _label_ok(labs[c])
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


# ── 月次 페이지 찾기 ────────────────────────────────────────────────────────
IR_HINT = re.compile(r"(IR情報|投資家|IRライブラリ|株主・投資家|^IR$)")
IR_PATH = re.compile(r"/(ir|investor)s?(/|$|\.)", re.I)


def find_monthly(page: str, base: str):
    """페이지에서 **月次로 가는 링크**를 찾는다 -> 주소 또는 ''."""
    import urllib.parse
    for href, lab in A.findall(page):
        t = _txt(lab)
        if "月次" in t or "月次" in href or "monthly" in href.lower():
            return urllib.parse.urljoin(base, href)
    return ""


def find_ir(page: str, base: str):
    """IR 로 가는 링크."""
    import urllib.parse
    for href, lab in A.findall(page):
        t = _txt(lab)
        if IR_HINT.search(t) or IR_PATH.search(href):
            return urllib.parse.urljoin(base, href)
    return ""


# 손으로 잇는 것은 **맨 위 주소 하나**다. 月次 페이지는 위 두 함수가 찾아
# 수집기 파일에 적어 두고, 못 찾을 때만 여기 둘째 칸에 직접 적는다.
# **못 여는 곳은 넣지 않는다**(84차): 시마무라 8227·ABC마트 2670 은 403,
# 비쿠카메라 3048 은 timeout 이다.
IR_SITES = {
    "8267": ("イオン", "https://www.aeon.info/", ""),
    "3086": ("Ｊフロント", "https://www.j-front-retailing.com/", ""),
    "9843": ("ニトリ", "https://www.nitorihd.co.jp/", ""),
    "9983": ("ファストリ", "https://www.fastretailing.com/jp/", ""),
    "7550": ("ゼンショー", "https://www.zensho.co.jp/jp/", ""),
    "3382": ("セブン＆アイ", "https://www.7andi.com/", ""),
    "9831": ("ヤマダ", "https://www.yamada-holdings.jp/", ""),
    "3563": ("スシロー", "https://food-and-life.co.jp/", ""),
    "7564": ("ワークマン", "https://www.workman.co.jp/", ""),
    "2695": ("くら寿司", "https://www.kurasushi.co.jp/", ""),
    "7606": ("ユナイテッドアローズ", "https://www.united-arrows.co.jp/", ""),
    "3097": ("物語コーポ", "https://www.monogatari.co.jp/", ""),
    "3197": ("すかいらーく", "https://corp.skylark.co.jp/", ""),
    "4666": ("パーク２４", "https://www.park24.co.jp/", ""),
    "8016": ("オンワードＨＤ", "https://www.onward-hd.co.jp/", ""),
    "7476": ("アズワン", "https://www.as-1.co.jp/", ""),
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

    # (하) **표 위 제목의 회계연도도 본다.** 표 안에 적히지 않고 바로 위
    #     제목에만 있는 일이 흔하다.
    lead = """<h3>2025年2月期 月次売上高</h3><table>
      <tr><th></th><th>12月</th><th>1月</th><th>2月</th></tr>
      <tr><th>既存店売上高</th><td>104.1</td><td>99.8</td><td>101.2</td></tr>
      </table>"""
    eq("(하) 제목의 회계연도", sorted(read(lead, T)),
       ["2024-12", "2025-01", "2025-02"])

    if bad:
        print("monir 스스로 시험 실패:")
        for b in bad:
            print("  -", b)
        raise SystemExit(1)
    print("monir 스스로 시험: 통과")


if __name__ == "__main__":                                # pragma: no cover
    _selftest()
