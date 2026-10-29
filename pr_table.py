"""미국 실적 보도자료(8-K EX-99)의 손익 표에서 이번 분기 숫자를 읽는다.

회원님: "공시에서 직접 읽어오면 되잖아. 다른 애들은 이미 그렇게 하고 있다."
SEC 의 회사 재무(companyfacts)는 10-Q 가 나와야(며칠~몇 주 뒤) 그 분기가 생기고,
나스닥 EPS 는 하루쯤 늦다. 그런데 **회사는 발표하는 그 순간 8-K 에 보도자료를
붙여 낸다** — 손익 표가 거기 다 있다. 그걸 읽는다.

보도자료 표는 회사마다 생김새가 다르다(표본 40건, 2026-10-28):

  - ADP   이번 분기 | 전년 분기
  - 보잉  9개월 누계 | 전년 누계 | 이번 분기 | 전년 분기   ← 누계가 앞에 온다
  - 치폴레 금액 | 비율(%) | 금액 | 비율(%)                  ← 비율 열이 끼어 있다

그래서 "첫 숫자가 이번 분기" 같은 가정을 하지 않는다. **스스로 맞춰 본다:**
표에는 이번 분기 옆에 **전년 같은 분기**가 나란히 있다. 그 값을 우리가 이미 가진
SEC 공식 수치(전년 같은 분기 매출)와 대조해, 맞는 열을 찾고 그 바로 앞 열을
이번 분기로 쓴다. 단위(천 달러·백만 달러)도 이 대조로 정해진다. **맞는 열이
없으면 아무것도 안 낸다** — 틀린 숫자보다 없는 숫자가 낫다.
"""
import html as _html
import re
from html.parser import HTMLParser

SCALES = (1.0, 1e3, 1e6, 1e9)
NUM = re.compile(r"^\(?-?\$?\(?\s*[\d,]+(?:\.\d+)?\s*\)?$")

# 이름표는 넓게 받는다 — 'Service revenues and sales'(CHE) 같은 이름도 있다.
# 안전장치는 이름이 아니라 **전년 열이 SEC 수치와 맞느냐**다.
REV = re.compile(r"revenue|sales", re.I)
REV_BAD = re.compile(r"cost|expense|%|percent|growth|per\s|deferred|unearned|other\s+revenue", re.I)
OPI = re.compile(r"^(total\s+)?(operating\s+(income|profit|earnings|loss)|"
                 r"(income|earnings|profit|loss)(\s*[/(]\s*\(?loss\)?\)?)?\s+from\s+operations|"
                 r"operating\s+income\s*\(loss\))", re.I)
NI = re.compile(r"^net\s+(income|earnings|loss|\(loss\)|income\s*\(loss\)|earnings\s*\(loss\)|"
                r"\(loss\)\s*income|loss\s*/\s*income|income\s*/\s*\(loss\)|earnings\s*/\s*\(loss\))", re.I)
NI_BAD = re.compile(r"noncontrolling|non-controlling|per\s+share|margin|%|before|"
                    r"preferred|participating|continuing|discontinued|adjusted", re.I)
EPS = re.compile(r"diluted", re.I)
EPS_BAD = re.compile(r"shares|weighted|average|adjusted|non-gaap", re.I)


class _Tables(HTMLParser):
    """바깥 표만 줄·칸으로 편다. 칸의 글자는 공백을 하나로 줄인다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.cur, self.row, self.cell, self.depth = [], None, None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.cur = []
        elif tag == "tr" and self.cur is not None:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).replace("​", "").strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row) and self.cur is not None:
                self.cur.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0 and self.cur is not None:
                self.tables.append(self.cur)
                self.cur = None

    def handle_data(self, d):
        if self.cell is not None:
            self.cell.append(d)


def tables(raw):
    p = _Tables()
    try:
        p.feed(_html.unescape(raw) if "&#" in raw[:200] else raw)
    except Exception:
        pass
    return p.tables


def to_num(s):
    t = s.replace("\xa0", "").replace(" ", "").replace("$", "")
    if t in ("—", "–", "-", "—%", ""):
        return None
    neg = t.startswith("(") or t.endswith(")") or t.startswith("-")
    t = t.strip("()-")
    if not re.fullmatch(r"[\d,]+(?:\.\d+)?", t):
        return None
    v = float(t.replace(",", ""))
    return -v if neg else v


def row_split(row):
    """(이름표, [숫자...]). 칸이 '$' 와 숫자로 갈라지고 '(' ')' 가 따로 오기도 한다.

    비율 열은 뺀다 — 숫자 바로 뒤 칸이 '%' 이거나, 그 줄 최댓값의 1% 도 안 되는
    소수(치폴레의 99.5·10.3)는 금액이 아니다.
    """
    label = ""
    vals = []
    cells = [c for c in row if c not in ("", "$")]
    i = 0
    while i < len(cells):
        c = cells[i]
        joined = c
        # '(1,234' + ')' 처럼 괄호가 다음 칸으로 넘어간 경우
        if c.startswith("(") and not c.endswith(")") and i + 1 < len(cells) and cells[i + 1].startswith(")"):
            joined = c + ")"
            i += 1
        v = to_num(joined)
        nxt = cells[i + 1] if i + 1 < len(cells) else ""
        if v is None:
            if not vals and not label:
                label = c
            elif not vals and not to_num(c) and c not in ("%", ")"):
                label = (label + " " + c).strip() if len(label) < 3 else label
        else:
            vals.append((v, nxt.startswith("%")))
        i += 1
    nums = [v for v, pct in vals if not pct]
    big = max((abs(v) for v in nums), default=0)
    if big >= 1000:
        nums = [v for v in nums if abs(v) >= big * 0.01 or v == 0]
    return re.sub(r"\s*\(\w\)\s*$", "", label).strip(" :"), nums


def _match(v, ref):
    return ref and v is not None and abs(v - ref) <= abs(ref) * 0.006


YEAR = re.compile(r"\b(20\d\d)\b")


def ascending(t):
    """머리줄 연도가 '2025 | 2026' 처럼 오르는 차례인가(작년이 앞). 대부분은 내림이다."""
    for r in t[:8]:
        ys = [int(y) for c in r for y in YEAR.findall(c)]
        if len(ys) >= 2 and ys[0] != ys[1]:
            return ys[0] < ys[1]
    return False


def header_years(t, width):
    """머리줄에서 열마다의 연도. 숫자 열 수와 연도 수가 같은 줄이 있을 때만."""
    for r in t[:8]:
        ys = [int(y) for c in r for y in YEAR.findall(c)]
        if len(ys) == width:
            return ys
    return None


def _known(v, known):
    return any(k and abs(v - k) <= abs(k) * 0.003 for k in known)


def read(raw, prior):
    """보도자료 HTML 과 전년 같은 분기 SEC 수치({rev, opi, ni}) -> 이번 분기.

    돌려주는 값: {"rev", "opi", "ni", "eps", "scale", "check": {...}} 또는 None.
    """
    if not prior or not prior.get("rev"):
        return None
    # **맞는 표를 전부 읽고 가장 많이 채워지는 표를 쓴다.** 앞쪽 요약 표에는 매출만
    # 있는 일이 흔하다(CHRW) — 처음 맞은 표에서 멈추면 순이익·EPS 를 놓친다.
    best = None
    for t in tables(raw):
        r = _read_table(t, prior)
        if r:
            score = (sum(r.get(k) is not None for k in ("opi", "ni", "eps")), -r["_err"])
            if best is None or score > best[0]:
                best = (score, r)
    if not best:
        return None
    out = best[1]
    out.pop("_err", None)
    return out


def _read_table(t, prior):
    rows = [row_split(r) for r in t]
    step = 1 if ascending(t) else -1        # 이번 분기 = 전년 열의 바로 뒤(오름) / 앞(내림)
    # 허용 범위 안의 후보 중 **가장 정확히 맞는 줄**을 쓴다 — 치폴레의 'Food and
    # beverage revenue' 전년 값이 총매출과 0.5% 차이라 범위에 걸렸다.
    found = None
    for label, nums in rows:
        if not label or not REV.search(label) or REV_BAD.search(label) or len(nums) < 2:
            continue
        ys = header_years(t, len(nums))
        known = prior.get("known") or []
        for j in range(len(nums)):
            for sc in SCALES:
                if not _match(nums[j] * sc, prior["rev"]):
                    continue
                err = abs(nums[j] * sc - prior["rev"]) / abs(prior["rev"])
                # 후보 열: 머리줄 연도가 있으면 '전년+1' 인 열들(가까운 것부터),
                # 없으면 같은 방향으로 이웃 열들. **이미 가진 중간 분기 값과 같은
                # 열은 건너뛴다** — BKR 은 「26.9 | 26.6 | 25.9」, BNL 은 다섯 분기를
                # 나란히 싣는다. 바로 옆 열이 이번 분기가 아니다.
                if ys:
                    order = sorted((i for i in range(len(nums)) if ys[i] == ys[j] + 1),
                                   key=lambda i: abs(i - j))
                else:
                    order = [j + step * k for k in range(1, len(nums))
                             if 0 <= j + step * k < len(nums)]
                for i in order:
                    cur = nums[i] * sc
                    if _known(cur, known):
                        continue
                    # **누계 열을 집지 않는다.** SEC 쪽 전년 값이 누계로 잘못 들어 있으면
                    # 누계 열이 대조를 통과한다(TRU 3,868M · CVLT 626M — 분기는 그 절반·
                    # 삼분의 일). 바로 앞 분기 값의 0.5~2배를 벗어나면 분기 값이 아니다.
                    if known and not 0.5 < cur / known[-1] < 2.0:
                        continue
                    # 전년 값과 크기가 너무 다르면(비율 열·누계 열) 버린다.
                    if 0.3 < cur / (nums[j] * sc) < 3.5:
                        if found is None or err < found[5]:
                            found = (i, j, sc, len(nums), cur, err)
                        break
    if not found:
        return None
    if True:
        ic, ip, sc, width, rev, err0 = found
        out = {"rev": rev, "scale": sc, "check": {"rev": [prior["rev"], None]}, "_err": err0}

        # 값 없는 줄은 구역 머리다(「Diluted—」, 「Weighted average shares:」).
        sections, sec = [], ""
        for label, nums in rows:
            if label and not nums:
                sec = label
            sections.append(sec)

        def pick(rx, bad, key, small=False):
            cands = []
            for (label, nums), sec in zip(rows, sections):
                if small:
                    # 주식 수 구역의 'Diluted' 는 EPS 가 아니다(ABG 의 18.0 은 1,800만 주).
                    if re.search(r"weighted|shares\s+(outstanding|used)|average\s+shares", sec + " " + label, re.I):
                        continue
                    hit = (rx.search(label) and not (bad and bad.search(label))) or \
                          (EPS.search(sec) and re.match(r"net\s+(income|earnings|loss)", label, re.I))
                    if not hit:
                        continue
                elif not label or not rx.search(label) or (bad and bad.search(label)):
                    continue
                if len(nums) != width:
                    continue
                v, pv = nums[ic], nums[ip]
                if small:
                    if abs(v) < 1000 and abs(v - round(v)) > 1e-9 or abs(v) < 50:
                        cands.append((v, pv))
                    continue
                cands.append((v * sc, pv * sc))
            if not cands:
                return None
            ref = prior.get(key)
            if ref is not None and not small:
                # 매출과 같은 잣대(0.6%)다. 2% 로 두었더니 조정(non-GAAP) 줄이나 비지배
                # 지분 포함 줄이 걸렸다(WM 영업이익 1,329 vs SEC 1,279).
                ok = sorted((c for c in cands if _match(c[1], ref)), key=lambda c: abs(c[1] - ref))
                if not ok:
                    return None                  # SEC 와 안 맞으면 안 싣는다
                out["check"][key] = [ref, ok[0][1]]
                return ok[0][0]
            return cands[0][0]

        out["opi"] = pick(OPI, None, "opi")
        out["ni"] = pick(NI, NI_BAD, "ni")
        out["eps"] = pick(EPS, EPS_BAD, "eps", small=True)
        return out
    return None
