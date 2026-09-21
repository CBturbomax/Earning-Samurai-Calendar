# -*- coding: utf-8 -*-
"""월매출 공시 PDF 에서 **달별 수치를 표로 읽는다**.

문장으로 집으려다 접었다. 평평하게 편 글에서 「売上高5月6月7月8月前年同月比
125.4%」 같은 것이 걸리는데, 그 125.4 는 문장이 아니라 **표의 한 칸**이라 어느
달 값인지 알 수 없다. 그대로 담으면 조용히 엉뚱한 달에 값이 붙는다 — 이 저장소가
가장 싫어하는 실패다. 실측 66건 중 문장 규칙에 걸린 14건에도 그런 것이 섞여 있었다.

그래서 좌표를 쓴다. 월매출 공시는 거의 전부 이 꼴이다.

      7月  8月  9月 10月 …      <- 달 이름표 줄
  月次 149  127   93            <- 금액 줄
  前年同期比 115.8 89.7 100.4   <- 전년비 줄

달 이름표 줄을 찾고, 아래 줄의 값들을 **x 가 가장 가까운 이름표**에 붙인다.
붙일 이름표가 없으면 그 값은 버린다. 지어내지 않는다.

**해(年)는 발표일에서 정한다.** 표에는 대개 달만 적혀 있다. 발표한 달보다 큰
달은 지난해 것이다(9월에 내는 표의 12월은 작년 12월). 회계연도 표기를 읽지
않아도 되는 대신, 열두 달을 넘겨 도는 표에는 쓰지 않는다.
"""
import re
from datetime import date

import pdftext

__all__ = ["read"]

ZEN = str.maketrans("０１２３４５６７８９．％，－　", "0123456789.%,- ")

MONTH_HDR = re.compile(r"^\(?(\d{1,2})月(度|分|期)?\)?$")
NUMCELL = re.compile(r"^[（(]?[-△▲]?\d[\d,]*(?:\.\d+)?[%]?[)）]?$")

# 금액 줄의 이름표. 넓게 잡되 **무엇의 값인지 적혀 있을 때만** 쓴다.
AMOUNT_LABEL = re.compile(
    r"売上高|売上収益|営業収益|営業収入|仕入高|受注高|取扱高|販売高|総取扱高|"
    r"流通総額|月商|月次|売上|チェーン全店|全店|既存店|合計|グループ")
# 전년비 줄의 이름표.
YOY_LABEL = re.compile(r"前年|対前年|昨対|YoY")
# **전년비에는 두 가지 자가 섞여 있다.** 「前年同月比111.2%」 는 비율이고
# 「対前年同月増減率41.5%」 는 증감폭이다. 같은 칸에 담으면 41.5% 가 '작년의
# 41.5%' 즉 반토막으로 읽힌다 — 실제로 테라프로브(6627)가 그렇게 실렸다.
# 증감폭이면 100 을 더해 **비율 하나로 맞춰** 담는다.
DELTA_LABEL = re.compile(r"増減率|増減|伸び率|成長率|前年差|増収率")
# 단위. 줄 어디에 적혀 있든 찾는다(이름표 안, 또는 옆 칸).
UNIT = (("百万円", 1_000_000), ("千円", 1_000), ("億円", 100_000_000),
        ("万円", 10_000), ("円", 1))
PCT = re.compile(r"[%％]")

# 누계·예상은 그 달의 값이 아니다. 회사 정보 줄(2654 의 「株式会社」)도 아니다 —
# 표가 아닌 줄에 달 이름표가 우연히 걸린 것이라, 그대로 두면 아무 숫자나 실린다.
SKIP_LABEL = re.compile(r"累計|累積|予想|計画|見通|通期|上期|下期|前年同月の|前期|"
                        r"株式会社|代表者|問合せ|電話|コード番号|各位|TEL")


def _norm(s: str) -> str:
    return s.translate(ZEN).replace(" ", "")


def _num(cell: str):
    """칸 -> 숫자. 괄호·△·▲ 는 음수 표기다."""
    t = _norm(cell)
    neg = t.startswith(("(", "（", "△", "▲", "-"))
    t = re.sub(r"[()（）△▲%％,\-]", "", t)
    if not t or not re.fullmatch(r"\d+(?:\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _years(cols, filled, ann: date):
    """머리줄의 달들에 해를 붙인다 -> [연도].

    **한 달씩 따로 정하면 안 된다.** '발표한 달보다 크면 지난해'만 쓰면
    회계연도가 발표한 달에서 시작하는 표에서 첫 칸이 올해로 붙는다(4058 의
    9월이 2026-09 가 됐다 — 9월은 아직 끝나지도 않았다).

    표의 달은 왼쪽에서 오른쪽으로 시간 순이다. **값이 있는 맨 오른쪽 칸**을
    기준으로 삼고(그것이 이번에 보고하는 달이다), 양쪽으로 훑으며 달 번호가
    거꾸로 가는 자리마다 해를 하나 넘긴다.
    """
    n = len(cols)
    yr = [None] * n
    anchor = max(filled) if filled else n - 1
    yr[anchor] = ann.year if cols[anchor] <= ann.month else ann.year - 1
    for i in range(anchor - 1, -1, -1):
        yr[i] = yr[i + 1] - 1 if cols[i] > cols[i + 1] else yr[i + 1]
    for i in range(anchor + 1, n):
        yr[i] = yr[i - 1] + 1 if cols[i] < cols[i - 1] else yr[i - 1]
    return yr


def _headers(cells):
    """달 이름표 줄이면 [(x, 달)] 을 준다. 아니면 None."""
    got = []
    for x, t in cells:
        m = MONTH_HDR.match(_norm(t))
        if m and 1 <= int(m.group(1)) <= 12:
            got.append((x, int(m.group(1))))
    # 셋은 있어야 표의 머리로 본다. 둘로는 본문의 '8月' 두 개와 못 가른다.
    if len(got) < 3:
        return None
    # 같은 달이 두 번 나오면 두 해가 섞인 표다(전년 비교표). 그건 안 다룬다 —
    # 어느 쪽이 올해인지 x 만으로는 모른다.
    if len({m for _, m in got}) != len(got):
        return None
    return got


def _assign(cells, hdr):
    """값 칸을 가장 가까운 이름표에 붙인다. 멀면 버린다."""
    xs = [x for x, _ in hdr]
    span = min(b - a for a, b in zip(xs, xs[1:])) if len(xs) > 1 else 40.0
    tol = max(span * 0.6, 8.0)
    out = {}
    for x, t in cells:
        v = _num(t)
        if v is None:
            continue
        best, bd = None, 1e9
        for hx, mo in hdr:
            d = abs(hx - x)
            if d < bd:
                best, bd = mo, d
        if best is not None and bd <= tol and best not in out:
            out[best] = v
    return out


def _label(cells, hdr):
    """줄의 이름표 — 첫 이름표 자리보다 왼쪽에 있는 글자 칸들."""
    left = min(x for x, _ in hdr)
    return "".join(_norm(t) for x, t in cells if x < left - 1 and not NUMCELL.match(_norm(t)))


def _unit(*texts):
    joined = "".join(texts)
    for name, mul in UNIT:
        if name in joined:
            return name, mul
    return "", 0


def read(data: bytes, ann: str, max_pages: int = 12):
    """PDF -> {'rows': [...], 'basis': ...} 또는 None.

    rows: [{'period': 'YYYY-MM', 'rev': 원화가 아닌 **엔**, 'yoy': 전년동월비(%),
            'metric': 무엇의 값인가, 'unit': 표기 단위}]
    """
    try:
        aday = date.fromisoformat(ann)
    except ValueError:
        return None
    try:
        table = pdftext.extract_cells(data, max_pages)
    except Exception:
        return None

    best = None
    for i, cells in enumerate(table):
        hdr = _headers(cells)
        if not hdr:
            continue
        amounts, yoys = [], []
        # 이름표 줄 아래로 여덟 줄까지 본다. 그 아래는 다른 표다.
        for cells2 in table[i + 1:i + 9]:
            if _headers(cells2):
                break
            lab = _label(cells2, hdr)
            if not lab or SKIP_LABEL.search(lab):
                continue
            vals = _assign(cells2, hdr)
            if len(vals) < 2:
                continue
            rowtext = "".join(t for _, t in cells2)
            # **전년비 줄은 이름표에 '전년'이 있어야 한다.** 한때 '％가 있고
            # 단위가 없으면 전년비'로 봤더니 9163·7059 의 「稼働率」(가동률)이
            # 전년비로 실렸다. 백분율이라고 다 전년비가 아니다.
            if YOY_LABEL.search(lab):
                if DELTA_LABEL.search(lab):
                    vals = {k: v + 100.0 for k, v in vals.items()}
                yoys.append((lab, vals))
            elif AMOUNT_LABEL.search(lab):
                unit, mul = _unit(lab, rowtext)
                if mul:
                    amounts.append((lab, unit, mul, vals))
        if not amounts and not yoys:
            continue
        # **가장 꽉 찬 표를 고르면 안 된다.** 지난 회계연도 표는 열두 달이 다
        # 차 있고 올해 표는 이번 달까지만 차 있어서, 개수로 고르면 늘 작년
        # 것이 이긴다 — 토요쿠모(4058)가 2025년 열두 달로 실렸다. 그 표가
        # 가리키는 **가장 최근 달**을 먼저 보고, 같으면 개수로 가른다.
        cols = [m for _x, m in hdr]
        have = set(amounts[0][3] if amounts else {}) | set(yoys[0][1] if yoys else {})
        filled = [k for k, m in enumerate(cols) if m in have]
        yrs = _years(cols, filled, aday)
        newest = max((yrs[k] * 12 + cols[k] for k in filled), default=0)
        cand = (newest, len(have), i, hdr, amounts, yoys)
        if best is None or cand[:2] > best[:2]:
            best = cand

    if best is None:
        return None
    _newest, _n, _i, hdr, amounts, yoys = best
    amt = amounts[0] if amounts else None
    yoy = yoys[0] if yoys else None
    cols = [m for _x, m in hdr]
    have = set(amt[3] if amt else {}) | set(yoy[1] if yoy else {})
    filled = [i for i, m in enumerate(cols) if m in have]
    yrs = _years(cols, filled, aday)
    out = []
    for i, mo in enumerate(cols):
        if mo not in have:
            continue
        y = yrs[i]
        rec = {"period": f"{y:04d}-{mo:02d}"}
        if amt and mo in amt[3]:
            rec["rev"] = amt[3][mo] * amt[2]
            rec["unit"] = amt[1]
            rec["metric"] = amt[0][:24]
        if yoy and mo in yoy[1]:
            rec["yoy"] = yoy[1][mo]
            rec.setdefault("metric", yoy[0][:24])
        if len(rec) > 1:
            out.append(rec)
    if not out:
        return None
    return {"rows": out,
            "amount_label": amt[0][:24] if amt else "",
            "yoy_label": yoy[0][:24] if yoy else ""}
