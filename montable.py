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

# 달 이름표는 세 꼴로 온다. 「8月」·「8月度」 만 보던 시절에는 **해가 붙어
# 오는 표**(스기HD 의 「26年3月 26年4月 …」)를 통째로 못 읽었다. 해가 적혀
# 있으면 그게 정답이므로 발표일로 짐작하지 않고 그대로 쓴다.
MONTH_HDR = re.compile(r"^\(?(?:(\d{2}|\d{4})年)?(\d{1,2})月(度|分|期)?\)?$")
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

# **표 제목이 「전년비」라고 적힌 표**가 있다. 스기HD 의 「前年比の推移 / %
# Change Over Previous Year」 가 그렇다 — 값 줄의 이름표는 「全店売上高」뿐이라
# '전년' 이 없어 전년비로 못 알아봤고, 통화 단위도 없어 금액으로도 못 알아봤다.
# 회사가 표 위에 적어 둔 것을 읽는 것이지 지어내는 것이 아니다. 다만 이 길로
# 담을 때는 **이름표가 매출 낱말일 때만** 담는다(객수·가동률이 섞이지 않게).
CAP_YOY = re.compile(r"前年比|前年同月比|前年同期比|対前年|昨対|"
                     r"ChangeOverPreviousYear")

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


def _years(cols, filled, ann: date, given=None):
    """머리줄의 달들에 해를 붙인다 -> [연도].

    **한 달씩 따로 정하면 안 된다.** '발표한 달보다 크면 지난해'만 쓰면
    회계연도가 발표한 달에서 시작하는 표에서 첫 칸이 올해로 붙는다(4058 의
    9월이 2026-09 가 됐다 — 9월은 아직 끝나지도 않았다).

    표의 달은 왼쪽에서 오른쪽으로 시간 순이다. **값이 있는 맨 오른쪽 칸**을
    기준으로 삼고(그것이 이번에 보고하는 달이다), 양쪽으로 훑으며 달 번호가
    거꾸로 가는 자리마다 해를 하나 넘긴다.
    """
    n = len(cols)
    # **이름표에 해가 적혀 있으면 그것이 정답이다**(「26年3月」). 짐작할 이유가 없다.
    if given and all(g is not None for g in given):
        return list(given)
    yr = [None] * n
    anchor = max(filled) if filled else n - 1
    yr[anchor] = ann.year if cols[anchor] <= ann.month else ann.year - 1
    for i in range(anchor - 1, -1, -1):
        yr[i] = yr[i + 1] - 1 if cols[i] > cols[i + 1] else yr[i + 1]
    for i in range(anchor + 1, n):
        yr[i] = yr[i - 1] + 1 if cols[i] < cols[i - 1] else yr[i - 1]
    return yr


def _headers(cells):
    """달 이름표 줄이면 [(x, 달, 해 또는 None)] 을 준다. 아니면 None."""
    got = []
    for x, t in cells:
        m = MONTH_HDR.match(_norm(t))
        if m and 1 <= int(m.group(2)) <= 12:
            y = m.group(1)
            if y is not None:
                y = int(y)
                y += 2000 if y < 100 else 0
            got.append((x, int(m.group(2)), y))
    # 셋은 있어야 표의 머리로 본다. 둘로는 본문의 '8月' 두 개와 못 가른다.
    if len(got) < 3:
        return None
    # 같은 (해, 달)이 두 번 나오면 두 해가 섞인 표다(전년 비교표). 그건 안
    # 다룬다 — 어느 쪽이 올해인지 x 만으로는 모른다. 해가 적혀 있으면 같은
    # 달이 두 번 나와도 서로 다른 달이므로 괜찮다.
    if len({(y, m) for _, m, y in got}) != len(got):
        return None
    return got


def _assign(cells, hdr):
    """값 칸을 가장 가까운 이름표에 붙인다. 멀면 버린다. 열쇠는 이름표의 차례다."""
    xs = [x for x, _m, _y in hdr]
    span = min(b - a for a, b in zip(xs, xs[1:])) if len(xs) > 1 else 40.0
    tol = max(span * 0.6, 8.0)
    out = {}
    for x, t in cells:
        v = _num(t)
        if v is None:
            continue
        best, bd = None, 1e9
        for k, (hx, _mo, _yr) in enumerate(hdr):
            d = abs(hx - x)
            if d < bd:
                best, bd = k, d
        if best is not None and bd <= tol and best not in out:
            out[best] = v
    return out


def _label(cells, hdr):
    """줄의 이름표 — 첫 이름표 자리보다 왼쪽에 있는 글자 칸들."""
    left = min(x for x, _m, _y in hdr)
    return "".join(_norm(t) for x, t in cells if x < left - 1 and not NUMCELL.match(_norm(t)))


# 이름표에 이 낱말이 있으면 매출 줄이 아니다. 약한 이름표를 받아들일 때만 쓴다.
OTHER_LABEL = re.compile(r"客数|客単価|店舗数|会員数|人数|件数|稼働|坪|面積|"
                         r"社数|口座|台数|席数|利益|原価|在庫|日数|日祝|営業日|"
                         r"従業員|単価|人員")


# 약한 이름표 — 결산기·당기·빈칸. 이만큼 좁혀야 **지역 줄**을 안 집는다.
# 사카이이사(9039)의 「北海道・東北地区」 가 전사 매출로 실렸던 자리다.
WEAK_LABEL = re.compile(r"|当期|今期|当月|\d{2,4}年\d{1,2}月期?|"
                        r"\d{2,4}年\d{1,2}月期\d{0,2}|第\d+期")


def lab_has_other(lab: str) -> bool:
    return bool(OTHER_LABEL.search(lab or ""))


def _looks_delta(vals) -> bool:
    """이름표에 '増減率'이 없어도 값이 증감폭이면 그렇게 본다.

    「全店前年比（%）」 에 8.4 가 오면 그것은 +8.4% 이지 '작년의 8.4%'가 아니다
    (오토박스가 그랬다 — 비율로 읽으면 매출이 9할 줄어든 것이 된다). 음수가
    섞여 있거나 값이 대체로 30 보다 작으면 증감폭이다.
    """
    vs = [v for v in vals.values()]
    if not vs:
        return False
    if any(v < 0 for v in vs):
        return True
    vs = sorted(abs(v) for v in vs)
    return vs[len(vs) // 2] < 30


def _unit(*texts):
    joined = "".join(texts)
    for name, mul in UNIT:
        if name in joined:
            return name, mul
    return "", 0


TITLE_METRIC = re.compile(r"売上高|売上収益|営業収益|仕入高|受注高|取扱高|"
                          r"販売高|月次売上|売上")


N = r"\d[\d,]*(?:\.\d+)?"
# **금액은 단위가 겹쳐 온다** — 「482億42百万円」·「1兆2,345億円」. 앞에서
# `(숫자)(단위)?円` 하나만 찾았더니 482億을 건너뛰고 「42百万円」만 잡아
# 고베물산의 482억엔이 0.42억엔으로 실렸다. 토막을 전부 모아 더한다.
#
# **되풀이(`(?:…)+円`)로 쓰면 안 된다.** 숫자가 길게 늘어선 표에서 뒤에 円 이
# 없으면 갈래가 기하급수로 불어나 **한 건이 영영 안 끝난다** — 수집기가 매
# 실행 12분을 넘겨 죽은 것이 이것이었다. 단위마다 자리를 못박아 되풀이를 없앤다.
AMT_PIECE = rf"({N})(兆|億|百万|万|千)?"
AMT_RE = re.compile(rf"(?:{N}兆)?(?:{N}億)?(?:{N}百万)?(?:{N}万)?(?:{N}千)?(?:{N})?円")
PIECE_RE = re.compile(AMT_PIECE)
S_MONTH = re.compile(r"(\d{1,2})月")
S_METRIC = re.compile(r"売上高|売上収益|営業収益|仕入高|受注高|取扱高|販売高|営業収入")
S_YOY = re.compile(rf"(?:前年同月比|前年同期比|前年比)({N})[%]の?(増|減)?")
MULT = {"兆": 10 ** 12, "億": 10 ** 8, "百万": 10 ** 6, "万": 10 ** 4, "千": 10 ** 3,
        None: 1, "": 1}


def _sentence(table, aday: date):
    """표가 없을 때 **문장 한 줄**에서 그 달치만 건진다.

    「8月のグループ連結売上高は15,154百万円、前年同月比8％の増収」(9997) 처럼
    달·무엇·금액·전년비가 **한 문장 안에** 다 있는 공시가 있다. 표가 아니라
    문장이므로 어느 칸인지 헷갈릴 일이 없다 — 넷이 다 있을 때만 담는다.
    하나라도 없으면 담지 않는다.
    """
    flat = "".join(_norm(t) for row in table for _x, t in row)
    for sent in flat.split("。"):
        if len(sent) > 400:
            continue
        mo, met = S_MONTH.search(sent), S_METRIC.search(sent)
        amt, yoy = AMT_RE.search(sent), S_YOY.search(sent)
        if not (mo and met and amt and yoy):
            continue
        if not re.search(r"\d", amt.group(0)):     # 숫자 없는 '円' 은 금액이 아니다
            continue
        m = int(mo.group(1))
        if not 1 <= m <= 12:
            continue
        y = aday.year if m <= aday.month else aday.year - 1
        v = 0.0
        for num, un in PIECE_RE.findall(amt.group(0)):
            if num:
                v += float(num.replace(",", "")) * MULT[un or None]
        r = float(yoy.group(1))
        if yoy.group(2):                      # 増/減 는 증감폭이다
            r = 100 + (r if yoy.group(2) == "増" else -r)
        return {"rows": [{"period": f"{y:04d}-{m:02d}", "rev": v, "yoy": r,
                          "unit": "円",
                          "metric": met.group(0)}],
                "amount_label": met.group(0), "yoy_label": "前年同月比"}
    return None


def read(data: bytes, ann: str, max_pages: int = 12, title: str = ""):
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
    # 이름표가 약한 줄에 붙일 이름. 공시 제목이 「月次売上速報」 라면 그 표의
    # 금액은 매출이다 — 지어내는 것이 아니라 회사가 제목에 적어 둔 것이다.
    tm = TITLE_METRIC.search(_norm(title or ""))
    title_metric = tm.group(0) if tm else "월매출"

    best = None
    for i, cells in enumerate(table):
        hdr = _headers(cells)
        if not hdr:
            continue
        # **단위는 표 바깥에 적히기도 한다** — 「（単位：百万円）」 가 머리줄
        # 위에 한 줄로 서는 표가 흔하다. 값 줄에만 단위를 찾으면 그런 표가
        # 통째로 버려진다. 머리줄과 그 위 두 줄까지 본다.
        cap_txt = "".join(_norm(t) for row in table[max(0, i - 2):i + 1]
                          for _x, t in row)
        cap_unit, cap_mul = _unit(cap_txt)
        cap_yoy = bool(CAP_YOY.search(cap_txt))
        amounts, yoys = [], []
        # **이름과 숫자가 다른 줄에 찍히는 공시가 많다.** 실측: '표없음'으로
        # 버린 209건 중 **92건**이 이것 하나였다. 히로세통상(7185)이 그 꼴이다.
        #
        #     営業収益                       <- 이름표만 있는 줄
        #     749  832  1,026 …  759         <- 값만 있는 줄 (이름표가 빈다)
        #     (単位：百万円)                  <- 단위는 값 **아래**에
        #
        # 한 줄만 보면 이름표도 단위도 없는 숫자 열두 개라 통째로 버려진다.
        # 그래서 값 없는 줄의 이름표를 다음 값 줄에 **물려주고**, 단위는 바로
        # 아랫줄까지 본다. 지어내는 것이 아니라 같은 표의 같은 줄을 읽는 것이다.
        pending = ""
        # 이름표 줄 아래로 여덟 줄까지 본다. 그 아래는 다른 표다.
        for k in range(i + 1, min(i + 9, len(table))):
            cells2 = table[k]
            if _headers(cells2):
                break
            lab = _label(cells2, hdr)
            vals = _assign(cells2, hdr)
            if len(vals) < 2:
                # 값이 없는 줄은 다음 줄의 이름표일 수 있다. 단위 안내줄
                # (「(単位：百万円)」)은 이름이 아니므로 물려주지 않는다 —
                # 그것이 이름표가 되면 매출 줄이 단위 줄로 둔갑한다.
                if lab and "単位" not in lab:
                    pending = lab
                continue
            if not lab:
                lab, pending = pending, ""
            else:
                pending = ""
            if SKIP_LABEL.search(lab or ""):
                continue
            rowtext = "".join(_norm(t) for _, t in cells2)
            # **전년비 줄은 이름표에 '전년'이 있어야 한다.** 한때 '％가 있고
            # 단위가 없으면 전년비'로 봤더니 9163·7059 의 「稼働率」(가동률)이
            # 전년비로 실렸다. 백분율이라고 다 전년비가 아니다.
            if YOY_LABEL.search(lab) and not lab_has_other(lab):
                if DELTA_LABEL.search(lab) or _looks_delta(vals):
                    vals = {k2: v + 100.0 for k2, v in vals.items()}
                yoys.append((lab, vals))
                continue
            unit, mul = _unit(lab, rowtext)
            if not mul and k + 1 < len(table):
                # 단위가 값 **아래** 줄에 적히는 표(7185). 「単位」라고 적힌
                # 줄만 본다 — 아무 아랫줄이나 보면 다음 표의 단위를 끌어온다.
                nxt = "".join(_norm(t) for _x, t in table[k + 1])
                if "単位" in nxt:
                    unit, mul = _unit(nxt)
            # **이름표가 약해도 줄에 통화 단위가 있으면 금액 줄이다.**
            # 값 줄의 이름표가 결산기(「2026年12月期」)뿐인 공시가 흔한데,
            # 낱말 사전만 보면 그런 표가 통째로 버려진다(3276·3983).
            if mul and (AMOUNT_LABEL.search(lab or "") or "円" in rowtext):
                amounts.append((lab or title_metric, unit, mul, vals))
            elif cap_mul and tm and WEAK_LABEL.fullmatch(lab or ""):
                # 이름표가 약하고 줄에도 단위가 없다 — 그래도 **표 바깥 단위·
                # 공시 제목의 '매출'·머리줄 아래 첫 줄** 셋이 함께 가리키면
                # 금액 줄로 본다(스기HD 의 「26年3月…」 표가 그랬다).
                # 셋 중 하나라도 없으면 담지 않는다.
                amounts.append((lab or title_metric, cap_unit, cap_mul, vals))
            elif cap_yoy and not mul and AMOUNT_LABEL.search(lab or "") \
                    and not lab_has_other(lab):
                # 표 제목이 「前年比の推移」인데 값 줄에는 '전년'이 없는 표.
                # 매출 낱말 이름표에만 건다 — 같은 표의 객수·가동률 줄이
                # 딸려 들어오면 그게 더 나쁜 거짓말이다.
                if DELTA_LABEL.search(lab) or _looks_delta(vals):
                    vals = {k2: v + 100.0 for k2, v in vals.items()}
                yoys.append((lab, vals))
        if not amounts and not yoys:
            continue
        # **가장 꽉 찬 표를 고르면 안 된다.** 지난 회계연도 표는 열두 달이 다
        # 차 있고 올해 표는 이번 달까지만 차 있어서, 개수로 고르면 늘 작년
        # 것이 이긴다 — 토요쿠모(4058)가 2025년 열두 달로 실렸다. 그 표가
        # 가리키는 **가장 최근 달**을 먼저 보고, 같으면 개수로 가른다.
        cols = [m for _x, m, _y in hdr]
        given = [y for _x, _m, y in hdr]
        have = set(amounts[0][3] if amounts else {}) | set(yoys[0][1] if yoys else {})
        filled = sorted(have)
        yrs = _years(cols, filled, aday, given)
        newest = max((yrs[k] * 12 + cols[k] for k in filled), default=0)
        cand = (newest, len(have), i, hdr, amounts, yoys)
        if best is None or cand[:2] > best[:2]:
            best = cand

    if best is None:
        return _sentence(table, aday)
    _newest, _n, _i, hdr, amounts, yoys = best
    amt = amounts[0] if amounts else None
    yoy = yoys[0] if yoys else None
    cols = [m for _x, m, _y in hdr]
    given = [y for _x, _m, y in hdr]
    have = set(amt[3] if amt else {}) | set(yoy[1] if yoy else {})
    yrs = _years(cols, sorted(have), aday, given)
    out = []
    for i, mo in enumerate(cols):
        if i not in have:
            continue
        y = yrs[i]
        rec = {"period": f"{y:04d}-{mo:02d}"}
        if amt and i in amt[3]:
            rec["rev"] = amt[3][i] * amt[2]
            rec["unit"] = amt[1]
            rec["metric"] = amt[0][:24]
        if yoy and i in yoy[1]:
            rec["yoy"] = yoy[1][i]
            rec.setdefault("metric", yoy[0][:24])
        if len(rec) > 1:
            out.append(rec)
    if not out:
        return None
    return {"rows": out,
            "amount_label": amt[0][:24] if amt else "",
            "yoy_label": yoy[0][:24] if yoy else ""}
