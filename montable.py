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

# **표가 스스로 「단위」를 적어 두는데 그것이 돈이 아니면 매출이 아니다.**
# 아즈원(7476)의 「※参考 営業日数（単位：日）」 표에서 「前年同月比 ±0 △2
# +1」 을 집어 98%·99% 로 실었다 — 날수 차이지 매출이 아니다. 요시크스의
# 「（単位：店）」(점포수)·오토서버의 「単位：台」(대수)·오로의 「（単位：千
# ライセンス）」도 같은 자리다. 적어 둔 단위에 円 이 없으면 그 표를 안 본다.
UNIT_DECL = re.compile(r"単位[：:]([^)）]{0,8})")

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
    """달 이름표 줄이면 [(x, 달, 해 또는 None)] 을 준다. 아니면 None.

    **「6」과 「月」이 다른 칸으로 갈라져 오는 공시가 있다**(아스쿨 2678 의
    「6 月 7 月 8 月」). 그러면 한 칸도 달로 안 읽혀 그 표가 통째로 버려진다.
    그래서 옆 칸과 붙여서도 본다 — MONTH_HDR 이 통째로(^…$) 맞아야 하는
    규칙이라 「月7」 같은 엉뚱한 짝은 저절로 걸러진다.
    """
    got = []
    for k, (x, t) in enumerate(cells):
        s = _norm(t)
        m = MONTH_HDR.match(s)
        if not m and k + 1 < len(cells):
            m = MONTH_HDR.match(s + _norm(cells[k + 1][1]))
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


# 표끼리 합쳐도 되는지는 **이름표가 같은가**로 가른다. 숫자·괄호·단위 표기는
# 표마다 달라서(「売上高(百万円)」·「売上高」) 지우고 견준다.
_KIND_DROP = re.compile(r"[\d\s()（）%％,.:：・]|百万|千|億|万|円")


def _same_kind(amt, yoy):
    lab = (amt[0] if amt else "") or (yoy[0] if yoy else "")
    return _KIND_DROP.sub("", _norm(lab))


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

    cands = []
    for i, cells in enumerate(table):
        hdr = _headers(cells)
        if not hdr:
            continue
        # **단위는 표 바깥에 적히기도 한다** — 「（単位：百万円）」 가 머리줄
        # 위에 한 줄로 서는 표가 흔하다. 값 줄에만 단위를 찾으면 그런 표가
        # 통째로 버려진다. 머리줄과 그 위 두 줄까지 본다.
        cap_txt = "".join(_norm(t) for row in table[max(0, i - 2):i + 1]
                          for _x, t in row)
        decl = UNIT_DECL.search(cap_txt)
        # **％는 막지 않는다.** 아즈원(7476)의 「（単位：日）」 를 막으려고 둔
        # 규칙인데, 「月次前年比（単位：％）」 로 **전년비만 내는 표**까지 같이
        # 막고 있었다 — 워크맨(7564)·시마추 같은 회사가 통째로 안 읽혔다.
        # 날수·점포수·대수는 여전히 막힌다.
        if decl and not re.search(r"[円%％]", decl.group(1)):
            continue
        cap_unit, cap_mul = _unit(cap_txt)
        # 표 바깥에 돈 단위가 적혀 있으면 그 표의 숫자는 **돈**이다.
        # 그럴 때는 제목이 「前年比」라 해도 값 줄을 비율로 읽지 않는다 —
        # 테라프로브(6627)·바이셀(7685)·업개러지(7134)의 백만엔 금액이
        # 전년비 칸에 실려 4,124% 같은 값이 나왔다.
        cap_yoy = bool(CAP_YOY.search(cap_txt)) and not cap_mul
        amounts, yoys, cap_yoys = [], [], []
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
            elif cap_mul and (AMOUNT_LABEL.search(lab or "")
                              and not lab_has_other(lab)
                              or tm and WEAK_LABEL.fullmatch(lab or "")):
                # 줄에는 단위가 없지만 **표 바깥에 돈 단위가 적혀 있다.**
                # 이름표가 매출 낱말이면 그대로 금액 줄이다(테라프로브 6627 의
                # 「月次売上高 4,124 …」 는 백만엔이지 4,124% 가 아니다).
                # 이름표가 결산기뿐일 만큼 약할 때는 **공시 제목에도 '매출'이
                # 적혀 있을 때만** 담는다(스기HD 의 「26年3月…」 표).
                amounts.append((lab or title_metric, cap_unit, cap_mul, vals))
            elif cap_yoy and not mul and AMOUNT_LABEL.search(lab or "") \
                    and not lab_has_other(lab):
                # 표 제목이 「前年比の推移」인데 값 줄에는 '전년'이 없는 표.
                # 매출 낱말 이름표에만 건다 — 같은 표의 객수·가동률 줄이
                # 딸려 들어오면 그게 더 나쁜 거짓말이다.
                if DELTA_LABEL.search(lab) or _looks_delta(vals):
                    vals = {k2: v + 100.0 for k2, v in vals.items()}
                cap_yoys.append((lab, vals))
        # 제목만 보고 읽은 줄은 **마지막 수단**이다. 같은 표에서 금액 줄이나
        # 제대로 된 전년비 줄을 찾았으면 그쪽이 옳다.
        if not amounts and not yoys:
            yoys = cap_yoys
        if not amounts and not yoys:
            continue
        # **표를 하나만 쓰고 버리면 안 된다.** 월매출 공시에는 지난 회계연도
        # 열두 달 표와 올해 표가 나란히 실리는 일이 흔한데, 하나만 고르면
        # 손에 든 스물넉 달이 대여섯 달로 준다 — 그러면 전년동월비를 견줄
        # 수가 없다. 같은 것을 재는 표끼리 **합친다.**
        #
        # 다만 **아무 표나 합치면 안 된다.** 한 공시에 全店 표와 既存店 표가
        # 따로 실리기도 하고 부문별 표가 붙기도 하는데, 섞으면 3월은 전점이고
        # 4월은 기존점인 막대가 선다. 그래서 **이름표가 같은 표끼리만** 묶고,
        # 묶음 중에서 가장 최근 달을 가리키는 쪽을 쓴다.
        cols = [m for _x, m, _y in hdr]
        given = [y for _x, _m, y in hdr]
        have = set(amounts[0][3] if amounts else {}) | set(yoys[0][1] if yoys else {})
        filled = sorted(have)
        yrs = _years(cols, filled, aday, given)
        newest = max((yrs[k] * 12 + cols[k] for k in filled), default=0)
        cands.append((newest, len(have), i, hdr, amounts, yoys))

    if not cands:
        return _sentence(table, aday)

    groups = {}
    for c in cands:
        amt = c[4][0] if c[4] else None
        yoy = c[5][0] if c[5] else None
        groups.setdefault(_same_kind(amt, yoy), []).append(c)
    # 묶음 고르기: 가장 최근 달이 먼저고, 같으면 달이 많은 쪽이다.
    pick = max(groups.values(),
               key=lambda g: (max(c[0] for c in g), sum(c[1] for c in g)))
    # 겹치는 달은 **새 표가 이긴다** — 속보 뒤에 확정치를 싣는 공시가 있다.
    pick.sort(key=lambda c: -c[0])

    got, amt_lab, yoy_lab, unit_of = {}, "", "", {}
    for _newest, _n, _i, hdr, amounts, yoys in pick:
        amt = amounts[0] if amounts else None
        yoy = yoys[0] if yoys else None
        cols = [m for _x, m, _y in hdr]
        given = [y for _x, _m, y in hdr]
        have = set(amt[3] if amt else {}) | set(yoy[1] if yoy else {})
        yrs = _years(cols, sorted(have), aday, given)
        if amt and not amt_lab:
            amt_lab = amt[0][:24]
        if yoy and not yoy_lab:
            yoy_lab = yoy[0][:24]
        for k, mo in enumerate(cols):
            if k not in have:
                continue
            p = f"{yrs[k]:04d}-{mo:02d}"
            rec = got.setdefault(p, {"period": p})
            if amt and k in amt[3] and "rev" not in rec:
                rec["rev"] = amt[3][k] * amt[2]
                rec["unit"] = amt[1]
                rec.setdefault("metric", amt[0][:24])
            if yoy and k in yoy[1] and "yoy" not in rec:
                rec["yoy"] = yoy[1][k]
                rec.setdefault("metric", yoy[0][:24])
    out = [r for _p, r in sorted(got.items()) if len(r) > 1]
    if not out:
        return None
    return {"rows": out, "amount_label": amt_lab, "yoy_label": yoy_lab}


# ── 스스로 시험 ─────────────────────────────────────────────────────────────
# `python montable.py` — 밖으로 못 나가는 자리라 **손으로 PDF 를 만들어** 본다.
# 여기 세 꼴은 실제 공시에서 겪은 것이다: 이름표가 값 줄 위에 따로 서고 단위가
# 값 줄 아래에 적히는 표(히로세통상 7185), 표 제목만 「前年比」인 표(스기HD
# 7649), 그리고 **집으면 안 되는** 가동률 표(9163·7059).
def _selftest():                                          # pragma: no cover
    _chars = ("0123456789,.()%１２３４５６７８９０ ："
              "月営業収益単位百万千円前年比の推移全店売上高客数稼働率既存"
              "参考日数当期次△±")
    code = {c: 33 + i for i, c in enumerate(dict.fromkeys(_chars))}

    def _add(t):                       # 시험 글월에 쓰인 글자를 그때그때 담는다
        for c in t:
            code.setdefault(c, 33 + len(code))

    def _pdf(lines):
        # **글자를 먼저 담고 나서** 글자표를 만든다. 거꾸로 하면 그 시험에서
        # 처음 쓰는 글자가 표에 안 들어가 원문 그대로(바이트)로 읽힌다 —
        # 「％」 하나 때문에 시험이 엉뚱하게 떨어졌다.
        for _y, cells in lines:
            for _x, s in cells:
                _add(s)
        cm = (b"/CIDInit /ProcSet findresource begin\n12 dict begin begincmap\n"
              b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
              b"%d beginbfchar\n" % len(code)
              + b"".join(b"<%02X> <%s>\n"
                         % (v, c.encode("utf-16-be").hex().upper().encode())
                         for c, v in sorted(code.items(), key=lambda kv: kv[1]))
              + b"endbfchar\nendcmap\nend end\n")
        for _y, cells in lines:
            for _x, t in cells:
                _add(t)
        cs = [b"BT\n/F1 10 Tf\n"]
        for y, cells in lines:
            for x, t in cells:
                cs.append(b"1 0 0 1 %d %d Tm (%s) Tj\n"
                          % (x, y, bytes(code[c] for c in t)
                             .replace(b"\\", b"\\\\").replace(b"(", b"\\(")
                             .replace(b")", b"\\)")))
        cs = b"".join(cs) + b"ET\n"
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 "
            b"/LastChar 126 /Widths [" + b" ".join(b"600" for _ in range(95))
            + b"] /ToUnicode 5 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cm) + cm + b"\nendstream",
            b"<< /Length %d >>\nstream\n" % len(cs) + cs + b"\nendstream",
        ]
        return (b"%PDF-1.4\n"
                + b"".join(b"%d 0 obj\n" % i + o + b"\nendobj\n"
                           for i, o in enumerate(objs, 1))
                + b"trailer << /Root 1 0 R >>\n%%EOF\n")

    def _pdf_tj(lines, kern=0):
        """한 줄을 **TJ 배열 하나**로 그린다.

        표를 TJ 로 그리는 공시가 많다 — 칸 사이를 배열의 음수로 건너뛴다.
        그걸 한 덩어리로 읽으면 「100.892.1104.1」 처럼 붙어 어느 달 값인지
        알 수 없고, 반대로 조각마다 쪼개면 「26年3月」의 月 이 떨어져 나간다.
        `kern` 을 주면 낱말 안을 잘게 끊어 그려 그것까지 시험한다.
        """
        for _y, cells in lines:
            for _x, s in cells:
                _add(s)
        cm = (b"/CIDInit /ProcSet findresource begin\n12 dict begin begincmap\n"
              b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
              b"%d beginbfchar\n" % len(code)
              + b"".join(b"<%02X> <%s>\n"
                         % (v, c.encode("utf-16-be").hex().upper().encode())
                         for c, v in sorted(code.items(), key=lambda kv: kv[1]))
              + b"endbfchar\nendcmap\nend end\n")

        def enc(s):
            return (bytes(code[c] for c in s).replace(b"\\", b"\\\\")
                    .replace(b"(", b"\\(").replace(b")", b"\\)"))

        cs = [b"BT\n/F1 10 Tf\n"]
        for y, cells in lines:
            x0 = cells[0][0]
            parts, cur = [], x0
            for x, s in cells:
                gap = x - cur
                if parts:
                    parts.append(b"%d" % round(-100 * gap))
                if kern and len(s) > 1:
                    # 낱말 안을 잘게 끊는다 — 여기서 쪼개지면 안 된다.
                    parts.append(b"(%s)%d(%s)" % (enc(s[:-1]), -kern,
                                                  enc(s[-1:])))
                else:
                    parts.append(b"(%s)" % enc(s))
                cur = x + len(s) * 6
            cs.append(b"1 0 0 1 %d %d Tm [%s] TJ\n"
                      % (x0, y, b" ".join(parts)))
        cs = b"".join(cs) + b"ET\n"
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 4 0 R >> >> /Contents 6 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 "
            b"/LastChar 126 /Widths [" + b" ".join(b"600" for _ in range(95))
            + b"] /ToUnicode 5 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cm) + cm + b"\nendstream",
            b"<< /Length %d >>\nstream\n" % len(cs) + cs + b"\nendstream",
        ]
        return (b"%PDF-1.4\n"
                + b"".join(b"%d 0 obj\n" % i + o + b"\nendobj\n"
                           for i, o in enumerate(objs, 1))
                + b"trailer << /Root 1 0 R >>\n%%EOF\n")

    hdr = [(100, "１月"), (140, "２月"), (180, "３月"), (220, "４月")]
    ok = True

    # 가) 이름표는 값 줄 **위**에, 단위는 값 줄 **아래**에.
    a = read(_pdf([(700, hdr), (680, [(40, "営業収益")]),
                   (660, [(100, "749"), (140, "832"), (180, "1,026"),
                          (220, "1,096")]),
                   (640, [(40, "(単位：百万円)")])]),
             "2026-05-10", 4, "2026年4月度 月次売上速報")
    if not a or len(a["rows"]) != 4 or a["rows"][0].get("rev") != 749e6:
        print("!! 가) 이름표 물려주기·아랫줄 단위", a)
        ok = False

    # 나) 표 제목만 「前年比」 — 매출 줄만 담고 객수 줄은 안 담는다.
    b = read(_pdf([(720, [(40, "前年比の推移")]), (700, hdr),
                   (680, [(40, "全店売上高")]),
                   (660, [(100, "108.5"), (140, "108.2"), (180, "109.3"),
                          (220, "104.5")]),
                   (640, [(40, "全店客数")]),
                   (620, [(100, "101.1"), (140, "100.2"), (180, "99.3"),
                          (220, "98.5")])]),
             "2026-05-10", 4, "2026年4月度 月次速報")
    if not b or len(b["rows"]) != 4 or abs(b["rows"][0].get("yoy", 0) - 108.5) > .01 \
            or "客数" in (b.get("yoy_label") or ""):
        print("!! 나) 제목이 전년비인 표", b)
        ok = False

    # 다) 가동률은 백분율이지만 전년비가 아니다. 집으면 안 된다.
    c = read(_pdf([(700, hdr), (680, [(40, "稼働率")]),
                   (660, [(100, "74.8"), (140, "75.2"), (180, "80.1"),
                          (220, "78.5")])]),
             "2026-05-10", 4, "2026年4月度 月次")
    if c:
        print("!! 다) 가동률을 집었다", c)
        ok = False

    # 라) 표가 「（単位：日）」 라고 적었으면 그 숫자는 매출이 아니다.
    d = read(_pdf([(720, [(40, "参考 営業日数")]), (710, [(40, "(単位：日)")]),
                   (700, hdr), (680, [(40, "当期")]),
                   (660, [(100, "21"), (140, "18"), (180, "22"), (220, "22")]),
                   (640, [(40, "前年同月比")]),
                   (620, [(100, "0"), (140, "2"), (180, "1"), (220, "0")])]),
             "2026-05-10", 4, "2026年4月度 月次業績")
    if d:
        print("!! 라) 영업일수 표를 집었다", d)
        ok = False

    # 마) 제목이 「前年比」여도 표에 돈 단위가 적혀 있으면 그 숫자는 **돈**이다.
    e = read(_pdf([(730, [(40, "前年比")]), (720, [(40, "(単位：百万円)")]),
                   (700, hdr), (680, [(40, "月次売上高")]),
                   (660, [(100, "4,124"), (140, "4,088"), (180, "4,509"),
                          (220, "4,541")])]),
             "2026-05-10", 4, "2026年4月度 月次売上高")
    if not e or e["rows"][0].get("yoy") is not None \
            or e["rows"][0].get("rev") != 4124e6:
        print("!! 마) 백만엔 금액이 전년비로 실렸다", e)
        ok = False

    # 바) 한 공시에 작년 표와 올해 표가 나란히 — **합쳐야 한다.**
    f = read(_pdf([
        (760, [(40, "(単位：百万円)")]),
        (740, [(100, "25年4月"), (140, "25年5月"), (180, "25年6月")]),
        (720, [(40, "売上高")]),
        (700, [(100, "100"), (140, "110"), (180, "120")]),
        (660, [(40, "(単位：百万円)")]),
        (640, [(100, "26年4月"), (140, "26年5月"), (180, "26年6月")]),
        (620, [(40, "売上高")]),
        (600, [(100, "130"), (140, "140"), (180, "150")])]),
        "2026-07-10", 4, "2026年6月度 月次売上高")
    if not f or len(f["rows"]) != 6 or f["rows"][0]["period"] != "2025-04" \
            or f["rows"][-1]["period"] != "2026-06" \
            or f["rows"][-1].get("rev") != 150e6:
        print("!! 바) 두 해 표 합치기", f and [r["period"] for r in f["rows"]])
        ok = False

    # 사) 全店 표와 既存店 표는 **다른 것**이다. 합치면 3월은 전점 4월은
    #     기존점인 막대가 선다 — 한쪽만 쓴다.
    g = read(_pdf([
        (740, [(40, "前年比")]),
        (720, [(100, "１月"), (140, "２月"), (180, "３月")]),
        (700, [(40, "全店売上高")]),
        (680, [(100, "105.0"), (140, "106.0"), (180, "107.0")]),
        (640, [(40, "前年比")]),
        (620, [(100, "１月"), (140, "２月"), (180, "３月")]),
        (600, [(40, "既存店売上高")]),
        (580, [(100, "101.0"), (140, "102.0"), (180, "103.0")])]),
        "2026-04-10", 4, "2026年3月度 月次売上")
    if not g or len(g["rows"]) != 3 or len({r["metric"] for r in g["rows"]}) != 1:
        print("!! 사) 전점·기존점 표를 섞었다", g)
        ok = False

    # (차) 표를 **TJ 배열 하나**로 그린 공시. 칸 사이는 크게 건너뛰고
    #      낱말 안은 잘게 끊는다 — 앞엣것은 갈라야 하고 뒤엣것은 붙여야 한다.
    for kern, what in ((0, "낱말 안 끊김 없음"), (40, "낱말 안을 잘게 끊음")):
        j = _pdf_tj([(700, hdr), (680, [(40, "売上高")]),
                     (660, [(100, "749"), (140, "832"), (180, "1,026"),
                            (220, "980")]),
                     (640, [(40, "(単位：百万円)")])], kern=kern)
        g = read(j, "2026-05-10")
        got = {r["period"]: r.get("rev") for r in (g or {}).get("rows") or []}
        if len(got) != 4 or got.get("2026-04") != 980_000_000:
            print(f"!! 차) TJ 표 ({what})", got)
            ok = False

    # (카) 전년비만 내는 표 — 표가 스스로 「（単位：％）」 라고 적어 둔다.
    #      날수·점포수 표를 막는 규칙이 여기까지 막아 워크맨(7564)이 통째로
    #      안 읽혔다. ％ 는 막지 않는다.
    k = _pdf([(720, [(60, "2027年3月期 月次前年比"), (440, "(単位：％)")]),
              (700, hdr),
              (680, [(40, "売上高"), (100, "132.8"), (140, "132.9"),
                     (180, "95.4"), (220, "118.6")]),
              (660, [(40, "客数"), (100, "122.0"), (140, "126.3"),
                     (180, "94.1"), (220, "113.1")])])
    g = read(k, "2026-05-10", title="2027年3月期 月次前年比速報に関するお知らせ")
    got = {r["period"]: r.get("yoy") for r in (g or {}).get("rows") or []}
    if len(got) != 4 or got.get("2026-01") != 132.8 or got.get("2026-04") != 118.6:
        print("!! 카) ％ 단위 전년비 표", got)
        ok = False

    print("montable 스스로 시험:", "통과" if ok else "떨어짐")
    return 0 if ok else 1


if __name__ == "__main__":                                # pragma: no cover
    import sys as _sys
    _sys.exit(_selftest())
