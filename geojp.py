# -*- coding: utf-8 -*-
"""일본 결산단신 속 **태그 없는 표**에서 지역별 매출과 수주(受注高·受注残高)를 읽는다.

**왜 표를 읽나**(떠보기 2026-11-02). 결산단신 XBRL 에 지역 축 태그는 없다 — 사업부를 지역으로
나눈 회사(마키타·덴소)만 세그먼트 장에 태그가 있다. 그런데 첨부(qualitative.htm · 세그먼트 장)에
「地域別に関する情報」「所在地別売上高」「地域別売上収益」 표가 **HTML 표로** 실려 있다 — 미쓰비시전기·
TDK·무라타·다케다·히타치건기·고마쓰·르네사스·NEC·니덱·레이저텍. 수주도 같다 — 마키노(受注高·
受注残高)·구덴코(工事受注高). PDF 보다 훨씬 곧게 읽힌다.

**열을 짐작하지 않는다 — 같은 공시의 연결 매출로 맞춘다.** 표마다 칸이 제각각이다(금액·구성비·
증감·増減率, 3개월치 표와 누계 표가 나란히, 단위 百万円·億円). 그래서 칸마다 지역 값을 더해 **같은 zip
요약(Summary)의 누계 매출과 1.5% 안에서 맞는 칸**만 쓴다. 맞으면 열도 단위도 기간도 한꺼번에 정해진다.
안 맞으면 버린다 — 고마쓰의 「건설기계 부문의 지역별」 표가 이렇게 걸러진다(회사 전체가 아니다).

**소계를 두 번 세지 않는다.** 「海外計」 처럼 앞뒤 몇 줄을 더한 줄, 「北米のうち米国」 같은 うち 줄,
합계 줄을 뺀다. 아랫줄이 들여 쓰여 있고(앞 칸이 빈) 소계가 내어 쓰여 있으면 **소계를 남긴다** — 고마쓰·
히타치건기는 회사가 크게 묶은 지역(米州·欧州…)이 따로 있다. 들여쓰기가 없으면 아랫줄을 남긴다
(미쓰비시전기의 海外計 는 빼고 北米·アジア·欧州 를 남긴다).

수주는 맞춰 볼 총계가 없다. 그래서 더 좁게 읽는다 — 앞글이나 머리줄에 受注高/受注残高 가 있고,
합계 줄(또는 줄 하나)이 있고, 그 칸이 **위 줄들의 합과 맞는** 금액 칸일 때만 담는다. 예상·계획 표는 뺀다.

python geojp.py      스스로 시험(손으로 만든 표 몇 꼴)
"""
import html as H
import io
import re
import zipfile

TAGS = re.compile(r"<[^>]+>")
TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t[dh]>", re.S | re.I)
SPAN = re.compile(r'colspan\s*=\s*"?(\d+)', re.I)
RSPAN = re.compile(r'rowspan\s*=\s*"?(\d+)', re.I)

GEO_HEAD = re.compile(r"地域|所在地|仕向|国別|エリア|販売先|国内・海外|海外売上")
GEO_WORD = re.compile(
    r"日本|国内|海外|米|北米|南米|中南米|アメリカ|欧|ヨーロッパ|アジア|中国|中華|台湾|韓国|インド|"
    r"東南|大洋州|オセアニア|豪州|中東|中近東|アフリカ|ロシア|CIS|ＣＩＳ|EMEA|ＥＭＥＡ|"
    r"その他の地域|その他地域|Americas|Europe|Asia|China|Japan")
TOTAL = re.compile(r"(合\s*計|総\s*計)$|^(計|連結|全社|Total)$|^売上(高|収益)?\s*計$|^連結(合計|計|売上)", re.I)
SUBNOTE = re.compile(r"うち|内訳|（注|\(注|※")
FCST = re.compile(r"予想|見通し|計画|目標|予定")
ORD_HEAD = re.compile(r"受注")
PCT_CELL = re.compile(r"[%％]")
NUM = re.compile(r"^[△▲\-－−]?\s*[\d,，]+(\.\d+)?$")
SCALES = (1e6, 1e8, 1e3, 1e4, 1.0)


def clean(s):
    return re.sub(r"\s+", " ", H.unescape(TAGS.sub(" ", s))).replace("　", " ").strip()


def num(t):
    """'1,285,251' -> 1285251.0 · '△2,658' -> -2658 · 그 밖은 None. 백분율은 None."""
    t = (t or "").strip()
    if not t or PCT_CELL.search(t):
        return None
    t = t.replace("，", ",").replace("－", "-").replace("−", "-").replace(" ", "")
    if not NUM.match(t):
        return None
    neg = t[0] in "△▲-"
    v = float(re.sub(r"[^\d.]", "", t))
    return -v if neg else v


def rows_of(tb):
    """표 -> [[칸 글자…]]. colspan 은 오른쪽으로, rowspan 은 아래로 빈 칸을 채워 **격자**를 만든다.

    히타치의 「国内・海外売上収益」 표는 왼쪽에 「海外」 칸이 네 줄을 덮는다(rowspan). 안 펼치면
    둘째 줄부터 칸이 하나씩 모자라 금액이 구성비 자리로 밀린다.
    """
    out, hang = [], {}                 # hang[열] = 아래로 더 덮을 줄 수
    for r in ROW.findall(tb):
        cells, col = [], 0
        for _k, attrs, inner in CELL.findall(r):
            while hang.get(col):
                cells.append("")
                hang[col] -= 1
                col += 1
            span = SPAN.search(attrs)
            rs = RSPAN.search(attrs)
            w = min(int(span.group(1)), 12) if span else 1
            for j in range(w):
                cells.append(clean(inner) if j == 0 else "")
                if rs and int(rs.group(1)) > 1:
                    hang[col] = min(int(rs.group(1)), 40) - 1
                col += 1
        while hang.get(col):
            cells.append("")
            hang[col] -= 1
            col += 1
        if any(cells):
            out.append(cells)
    return out


OPEN_T = re.compile(r"<table\b", re.I)
CLOSE_T = re.compile(r"</table\s*>", re.I)


def innermost(txt):
    """표 안에 표가 든 문서(TDK 옛 공시는 쪽 전체가 바깥 표다)에서 **가장 안쪽 표**만 (시작, 끝)으로.
    `<table.*?</table>` 로 잡으면 바깥 표의 머리부터 첫 안쪽 표 끝까지가 한 덩이로 잡혀 엉뚱한 줄이 섞인다."""
    ev = sorted([(m.start(), 1) for m in OPEN_T.finditer(txt)] + [(m.end(), -1) for m in CLOSE_T.finditer(txt)])
    stack, out = [], []
    for p, k in ev:
        if k == 1:
            stack.append([p, False])
        elif stack:
            st, has_child = stack.pop()
            if not has_child:
                out.append((st, p))
            if stack:
                stack[-1][1] = True
    return sorted(out)


def tables(blob):
    """zip -> [(앞글, 줄들)]. 요약(Summary)은 XBRL 이라 빼고 첨부 htm 만 본다.
    같은 표가 qualitative.htm 과 세그먼트 장에 두 번 실리므로 줄 모양으로 겹침을 지운다."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return []
    out, seen = [], set()
    for n in z.namelist():
        if not n.lower().endswith(".htm") or "Summary" in n:
            continue
        try:
            txt = z.read(n).decode("utf-8", "replace")
        except (KeyError, OSError):
            continue
        pos = 0
        for st, en in innermost(txt):
            before = clean(txt[max(pos, st - 2000):st])[-220:]
            pos = en
            rows = rows_of(txt[st:en])
            if len(rows) < 2:
                continue
            key = repr(rows)
            if key in seen:
                continue
            seen.add(key)
            out.append((before, rows))
    return out


# ── PDF 결산단신(2019~2022년쯤 — zip 에 본문 표가 없던 시절) ─────────────────
#
# **그 시절 zip 에는 재무제표 장만 있다**(떠보기 11/02 — 2019~2021 미쓰비시전기·히타치·다케다·마키노 zip 에 qualitative·
# 세그먼트 장이 없다). 지역·수주 표는 결산단신 PDF 에만 있다. PDF 를 줄·칸으로 펴서(pdftext.extract_cells) **HTML 표와
# 같은 꼴**(앞글, 줄들)로 넘기면, 열·단위·기간을 연결 매출로 맞추는 규칙(geo_from)과 수주 규칙(orders_from)이 그대로 돈다.
# 칸이 비면 PDF 에는 그 칸이 아예 없어 자리가 밀린다 — 그래서 합이 연결 매출과 맞는 칸만 쓰는 검증이 더 중요하다.

def pdf_tables(pdf):
    """결산단신 PDF -> [(앞글, 줄들)]. 지역·수주 낱말이 든 줄을 제목으로 보고 그 아래 서른 줄을 한 표로 본다."""
    try:
        import pdftext
        lines = pdftext.extract_cells(pdf, max_pages=40)
    except Exception:                                   # noqa: BLE001 — 암호·깨진 PDF 는 못 읽은 것으로
        return []
    rows = []
    for cells in lines:
        r = [re.sub(r"\s+", " ", t).strip() for _x, t in sorted(cells) if t.strip()]
        # 이름이 글자마다 칸으로 쪼개져 온다(「日 | 本」·「ア | ジ | ア」) — 첫 숫자 칸 앞의 글자 칸을 하나로 붙인다
        k = next((i for i, c in enumerate(r) if num(c) is not None), len(r))
        if k > 1:
            r = ["".join(r[:k])] + r[k:]
        if r:
            rows.append(r)
    out = []
    for i, r in enumerate(rows):
        txt = " ".join(r)
        if len(txt) > 80 or not (GEO_HEAD.search(txt) or ORD_HEAD.search(txt)):
            continue
        before = " ".join(" ".join(x) for x in rows[max(0, i - 3):i + 1])
        body = rows[i + 1:i + 31]
        if len(body) >= 2:
            out.append((before[-220:], body))
    return out


def pdf_orders(pdf):
    """결산단신 PDF 의 수주 표 -> [{"kind", "cur", "prev"}]. **글자의 가로 자리로 칸을 가른다.**

    PDF 는 빈 칸이 아예 없어 칸 차례로는 당기·전기를 못 가른다(구덴코 418,000 통기 예상을 집은 까닭). 그래서 제목
    (受注高·受注残高 — 예상·계획 제목은 뺀다) 아래 열두 줄 안에서 **머리줄 글자의 x** 를 모아 두고, 합계 줄 숫자마다
    왼쪽으로 가장 가까운 기간 머리(前…/当…)와 종류 머리(金額/構成比)를 붙인다. 当·金額 인 칸이 당기, 前·金額 이 전기다.
    둘 다 못 찾으면 안 담는다. 이 값은 build.py 가 **다른 공시와 맞을 때만** 싣는다(그해 당기 = 이듬해 전기)."""
    try:
        import pdftext
        lines = pdftext.extract_cells(pdf, max_pages=40)
    except Exception:                                   # noqa: BLE001
        return []
    lines = [sorted(c) for c in lines if c]
    out = []
    for i, cells in enumerate(lines):
        txt = "".join(t for _x, t in cells)
        if len(txt) > 40 or "受注" not in txt or FCST.search(txt):
            continue
        kind = "受注残高" if "残" in txt else ("受注高" if re.search(r"受注(高|実績|状況|の状況)", txt) else None)
        if not kind:
            continue
        head = []
        for cells2 in lines[i + 1:i + 13]:
            t2 = "".join(t for _x, t in cells2)
            nums = [(x, num(t)) for x, t in cells2 if num(t) is not None]
            if re.search(r"受注|売上|生産|予想", t2) and not nums and head:
                break                                   # 다음 표 제목
            if not nums or re.search(r"\d{4}[.年]", t2):
                head += [(x, t) for x, t in cells2]
                continue
            lab = "".join(t for x, t in cells2 if num(t) is None and x < nums[0][0])
            if not TOTAL.search(lab.replace(" ", "")):
                continue
            got = {}
            for x, v in nums:
                if v is None or v <= 0:
                    continue
                per = max(((hx, ht) for hx, ht in head if hx <= x + 25 and re.search(r"前|当|増減|比較", ht)),
                          default=None)
                typ = max(((hx, ht) for hx, ht in head if hx <= x + 25 and re.search(r"金額|構成|比率|増減|%|％", ht)),
                          default=None)
                if not per or (typ and not re.search(r"金額", typ[1])):
                    continue
                k = _kind_of(per[1])
                if k and k not in got:
                    got[k] = v
            if "cur" in got:
                sc = 1e8 if "億円" in "".join(t for _x, t in head) else 1e6
                out.append({"kind": kind, "cur": got["cur"] * sc / 1e6, "prev": (got.get("prev") or 0) * sc / 1e6 or None})
            break
    seen, uniq = set(), []
    for o in out:
        if o["kind"] not in seen:
            seen.add(o["kind"])
            uniq.append(o)
    return uniq


# ── 줄 하나 -> (이름, 들여쓰기, 값들) ──────────────────────────────────

def parse_row(cells):
    """앞쪽 빈 칸 = 들여쓰기. 첫 글자 칸이 이름. 값은 **칸의 절대 자리**로 센다.

    이름 칸이 colspan 으로 두 칸을 덮는 줄(히타치건기의 「米州 | (빈) | 160,034」)과 들여 쓴
    아랫줄(「(빈) | 北米 | 144,452」)이 섞여 있어, 이름 뒤부터 세면 칸이 한 자리씩 어긋난다.
    colspan 을 펼쳐 두었으므로 절대 자리가 곧 같은 열이다.
    """
    i = 0
    while i < len(cells) and not cells[i]:
        i += 1
    if i >= len(cells):
        return None
    label = cells[i]
    if num(label) is not None:
        return None                     # 이름이 숫자 — 백분율만 적힌 아랫줄(마키타) 따위
    vals = [None] * (i + 1) + [num(c) for c in cells[i + 1:]]
    label = re.sub(r"\s*[（(]注[^）)]*[）)][\s\d０-９、,，]*|※[\d０-９]*", "", label)   # 「欧州 （注）２」 -> 「欧州」
    label = re.sub(r"(?<=[^\x00-\x7f]) +(?=[^\x00-\x7f])", "", label)      # 「米 州」 -> 「米州」
    return label.strip(), i > 0, vals


def _close(a, b, tol):
    return b and abs(a - b) <= abs(b) * tol


def leaf_set(items, k, coarse=True):
    """[(이름, 들여씀, 값)] (값은 칸 k) -> 소계·합계를 뺀 [(이름, 값)]. 합계 줄 값도 돌려준다."""
    rows, total = [], None
    for name, ind, vals in items:
        v = vals[k] if k < len(vals) else None
        if TOTAL.search(name):
            if v is not None:
                total = v
            break                       # 합계 뒤는 다른 표(収益認識の時期·うち 줄)다
        if SUBNOTE.search(name[:3]) or v is None:
            continue
        rows.append([name, ind, v])
    # 소계 — 바로 앞(또는 뒤)에 붙은 둘 이상의 줄을 더한 값과 같은 줄
    changed = True
    while changed and len(rows) > 2:
        changed = False
        for i, (name, ind, v) in enumerate(rows):
            for lo, hi in _runs(len(rows), i):
                run = rows[lo:hi]
                if len(run) < 2 or abs(sum(r[2] for r in run) - v) > max(3, abs(v) * 2e-5):
                    continue
                if coarse and not ind and all(r[1] for r in run):
                    del rows[lo:hi]      # 들여 쓴 아랫줄 -> 회사가 묶은 큰 지역(소계)을 남긴다
                else:
                    del rows[i]          # 아랫줄을 남긴다
                changed = True
                break
            if changed:
                break
    out = [(r[0], r[2]) for r in rows]
    # 큰 지역으로 묶었더니 「日本 · 海外」 둘뿐이면(히타치) 아랫줄이 낫다
    if coarse and len(out) < 3:
        fine = leaf_set(items, k, coarse=False)[0]
        if len(fine) > len(out):
            return fine, total
    return out, total


def _runs(n, i):
    """줄 i 바로 앞에서 끝나는 묶음들과 바로 뒤에서 시작하는 묶음들."""
    for lo in range(i - 1, -1, -1):
        if i - lo >= 2:
            yield lo, i
    for hi in range(i + 2, n + 1):
        if hi - (i + 1) >= 2:
            yield i + 1, hi


def transpose(rows):
    """지역이 가로로 선 표(고마쓰 【地域別情報】: 머리줄에 지역, 줄마다 기간)를 세로로 돌린다."""
    for h, head in enumerate(rows):
        labs = [c for c in head if c]
        if len(labs) < 2 or sum(1 for c in labs if GEO_WORD.search(c)) < 2:
            continue
        body = [r for r in rows[h + 1:] if any(num(c) is not None for c in r)]
        if not body:
            return None
        n = len(labs)
        cols = []
        for r in body:
            nums = [num(c) for c in r if num(c) is not None]
            if len(nums) < n:
                return None
            cols.append(nums[-n:])       # 오른쪽 끝에 맞춰 짝짓는다
        return [[lab] + [str(c[j]) for c in cols] for j, lab in enumerate(labs)]
    return None


# ── 지역 ──────────────────────────────────────────────────────────

def _try_block(items, headed, pri, spans, got):
    """한 덩이(합계 줄로 끊은 것)에서 칸마다 지역 합을 내어 누계 매출과 맞는 것을 got 에 담는다."""
    end = next((i for i, x in enumerate(items) if TOTAL.search(x[0])), len(items))
    geo_n = sum(1 for x in items[:end] if GEO_WORD.search(x[0]) and not x[0].startswith("その他"))
    # 머리글에 지역 낱말이 없으면(캐논 「売上高明細表」) 지역 이름이 셋은 있어야 한다
    if geo_n < (2 if headed else 3):
        return
    width = max((len(x[2]) for x in items), default=0)
    for k in range(width):
        leaf, total = leaf_set(items, k)
        if len(leaf) < 2 or sum(1 for nm, _ in leaf if GEO_WORD.search(nm)) < len(leaf) - 1:
            continue
        # 지역 매출은 음수가 없다 — 증감 칸(△1,294)이 우연히 매출과 맞은 적이 있다(미쓰비시전기 2018 PDF)
        if any(v <= 0 for _, v in leaf):
            continue
        s = sum(v for _, v in leaf)
        if total and not _close(s, total, 0.005):
            # 「アジア」 아래 「中国」 처럼 '그중' 줄이나 「海外」 소계가 이름표 없이 섞이면 합이 넘친다(미쓰비시전기 PDF 는
            # 둘 다 있다). **두 줄까지** 빼서 합계 줄과 **원 단위까지** 맞는 조합이 **딱 하나**일 때만(0.1% 로는 북미·중국처럼
            # 비슷한 두 줄이 둘 다 맞았다) 그 줄들을 뺀다.
            from itertools import combinations
            sols = [c for size in (1, 2) for c in combinations(range(len(leaf)), size)
                    if abs(s - sum(leaf[i][1] for i in c) - total) <= max(3, abs(total) * 2e-5)]
            sols = [c for c in sols if len(c) == min(len(x) for x in sols)] if sols else []
            if len(sols) != 1 or len(leaf) - len(sols[0]) < 2:
                continue
            leaf = [x for i, x in enumerate(leaf) if i not in sols[0]]
            s = total
        # **칸 하나는 가장 잘 맞는 기간 하나에만** 붙인다. 두 해 매출이 1% 남짓 차이면(미쓰비시전기 2018·2019 2분기)
        # 1.5% 잣대에 둘 다 들어가 전년 칸이 올해 자리에도 앉았다. 같은 기간에 더 잘 맞는 칸이 오면 그쪽이 이긴다.
        best = None
        for span, rev in spans.items():
            if not rev:
                continue
            for sc in SCALES:
                err = abs(s * sc - rev) / rev
                if err <= 0.015 and (best is None or err < best[0]):
                    best = (err, span, sc)
        if best:
            err, span, sc = best
            old = got.get(span)
            if old is None or (pri, -err) > (old[0], -old[2]):
                got[span] = (pri, {nm: v * sc for nm, v in leaf}, err)


def geo_from(tbls, spans):
    """표들 + {(시작, 종료): 누계 매출} -> {(시작, 종료): {지역: 값(엔)}}.

    spans 는 같은 공시 요약의 누계 매출(이번 기간·전년 같은 기간). 칸 합계가 그것과 맞는 표·칸만 쓴다.
    **고객 기준을 출하처 기준보다 먼저 쓴다.** 니덱은 「所在地別(출하처)」 과 「地域別(고객)」 표를 둘 다
    싣고 둘 다 연결 매출과 맞는다. '어디에 팔았나'가 우리가 보려는 것이다.
    """
    got = {}
    for before, rows in tbls:
        head = before + " " + " ".join(" ".join(r) for r in rows[:3])
        if FCST.search(before[-25:] + " ".join(rows[0])):
            continue
        headed = bool(GEO_HEAD.search(head))
        tail = before[-70:]
        pri = 1 if ("所在地" in tail and "顧客" not in tail) else 2
        for cand in (rows, transpose(rows)):
            if not cand:
                continue
            allitems = [x for x in (parse_row(r) for r in cand) if x]
            # 합계 줄마다 끊어 덩이로 본다 — 캐논의 「売上高明細表」 는 사업부 덩이 + 합계 + 지역 덩이다
            starts = [0] + [i + 1 for i, x in enumerate(allitems) if TOTAL.search(x[0])]
            blocks = [allitems[a:] for a in starts if a < len(allitems)]
            for items in blocks:
                _try_block(items, headed, pri, spans, got)
    return {k: v[1] for k, v in got.items()}


# ── 수주 ──────────────────────────────────────────────────────────

def _head_of(rows, k):
    """머리줄들에서 칸 k 의 이름 — colspan 을 펼쳐 두었으므로 왼쪽으로 가장 가까운 글자가 그 칸을 덮은 이름이다."""
    out = []
    for r in rows:
        if any(num(c) is not None for c in r[1:]) and not any(re.search(r"[年月期]", c) for c in r):
            break                                   # 값 줄에 닿았다
        j = min(k, len(r) - 1)
        while j >= 0 and not r[j]:
            j -= 1
        if j >= 0:
            out.append(r[j])
    return " ".join(out)


def _kind_of(h):
    """칸 이름 -> 'cur'(당기) · 'prev'(전기) · None(증감·비율·예상·통기)."""
    if re.search(r"増減|比較|比率|構成|前年(同期)?比|率|予想|計画|見通し|%|％", h):
        return None
    if re.search(r"当", h):
        return "cur"
    if re.search(r"前(中間|第|年同|連結|四半期|事業|期)", h) and not re.search(r"前期末|前期(実績)?$", h):
        return "prev"
    return None


def orders_from(tbls):
    """표들 -> [{"kind": "受注高"|"受注残高", "cur": 값, "prev": 값|None, "head", "before", "solo"}].

    합계 줄(없으면 줄 하나)의 금액 칸을 **머리줄 칸 이름으로** 가른다 — 「当中間…」 칸이 당기, 「前中間…」 칸이
    전기, 「増減·構成比·前年同期比·予想」 칸은 버린다. 가지마처럼 공종·국내외로 몇 겹 묶인 표도 합계 줄은 하나다.
    머리줄로 못 가르면 마키노 꼴(금액 칸 하나 + 비율)일 때만 그 하나를 당기로 읽는다.
    """
    out = []
    for before, rows in tbls:
        head = " ".join(" ".join(r) for r in rows[:3])
        near = before[-30:]
        ctx = near + " " + head
        # 「生産、受注及び販売の状況 ①売上高」 처럼 **바로 앞 제목이 매출·생산이면** 그 표는 수주가 아니다
        # (가지마·구덴코에서 매출 표를 수주로 읽을 뻔했다). 수주 낱말은 바로 앞 제목이나 머리줄에 있어야 한다.
        tail = re.split(r"[①-⑳]|\(\d\)|（\d）", near)[-1]
        if re.search(r"売上|販売|生産|完成工事|繰越|手持", tail) and "受注" not in tail:
            continue
        if not ORD_HEAD.search(ctx) or FCST.search(near + " " + head):
            continue
        items = [(x, r) for x, r in ((parse_row(r), r) for r in rows) if x]
        tot = next((x for x, r in items if TOTAL.search(x[0])), None)
        valued = [x for x, r in items if any(v is not None for v in x[2])]
        if tot is None:
            if len(valued) != 1:
                continue
            tot = valued[0]
        both = "受注高" in ctx and "受注残" in ctx
        cols = [(k, v) for k, v in enumerate(tot[2]) if v is not None and v > 0 and not 99 <= v <= 101]
        named = [(k, v, _kind_of(_head_of(rows, k)), _head_of(rows, k)) for k, v in cols]
        cur = [(k, v, h) for k, v, kd, h in named if kd == "cur"]
        prev = [(k, v, h) for k, v, kd, h in named if kd == "prev"]
        res = []
        if cur:
            if both:
                for kind in ("受注高", "受注残高"):
                    pick = lambda xs: next((x for x in xs if (kind == "受注残高") == ("残" in x[2])), None)  # noqa: E731
                    c, p_ = pick(cur), pick(prev)
                    if c:
                        res.append((kind, c[1], p_[1] if p_ else None))
            else:
                kind = "受注残高" if "受注残" in ctx and "受注高" not in ctx.replace("受注残高", "") else "受注高"
                res.append((kind, cur[0][1], prev[0][1] if prev else None))
        elif len(cols) == 1 and not both:
            kind = "受注残高" if "受注残" in ctx and "受注高" not in ctx.replace("受注残高", "") else "受注高"
            res.append((kind, cols[0][1], None))
        for kind, c, p_ in res:
            solo = "個別" in before[-80:] or (re.search(r"事業年度|会計期間", head) and "連結" not in head)
            out.append({"kind": kind, "cur": c, "prev": p_, "head": head[:200], "before": before[-90:],
                        "solo": bool(solo)})
    # 연결 표가 있으면 개별(個別) 표는 버린다
    for kind in ("受注高", "受注残高"):
        if any(o["kind"] == kind and not o["solo"] for o in out):
            out = [o for o in out if not (o["kind"] == kind and o["solo"])]
    return out


# ── 스스로 시험 ────────────────────────────────────────────────────

def _t(rows):
    return "<table>" + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</table>"


def _selftest():
    import zipfile as Z

    def zipped(*tbls):
        b = io.BytesIO()
        with Z.ZipFile(b, "w") as z:
            z.writestr("XBRLData/Attachment/qualitative.htm", "".join(tbls))
        return b.getvalue()

    # (가) 미쓰비시전기 꼴 — 海外計 는 빼고 연결합계로 맞춘다. 단위 百万円.
    a = "(地域別に関する情報） 顧客の所在地別に分類した売上高" + _t([
        ["", "前中間連結会計期間", "当中間連結会計期間"],
        ["", "外部顧客", "割合", "外部顧客", "割合"],
        ["日本", "1,285,251", "47.0％", "1,418,228", "45.4％"],
        ["北米", "425,875", "15.6％", "447,919", "14.3％"],
        ["アジア", "589,585", "21.6％", "742,652", "23.8％"],
        ["欧州", "381,696", "14.0％", "455,992", "14.6％"],
        ["その他", "50,097", "1.8％", "60,356", "1.9％"],
        ["海外計", "1,447,253", "53.0％", "1,706,919", "54.6％"],
        ["連結合計", "2,732,504", "100.0％", "3,125,147", "100.0％"],
        ["北米のうち、米国", "363,808", "13.3％", "384,729", "12.3％"]])
    sp = {("2026-04-01", "2026-09-30"): 3125147e6, ("2025-04-01", "2025-09-30"): 2732504e6}
    g = geo_from(tables(zipped(a)), sp)
    cur = g[("2026-04-01", "2026-09-30")]
    assert list(cur) == ["日本", "北米", "アジア", "欧州", "その他"], cur
    assert cur["日本"] == 1418228e6 and ("2025-04-01", "2025-09-30") in g

    # (나) 히타치건기 꼴 — 들여 쓴 아랫줄 + 내어 쓴 소계 -> 소계(큰 지역)를 남긴다.
    b = "販売先地域別の売上収益" + _t([
        ["", "前中間", "当中間", "前年比"],
        ["", "北米", "144,452", "22.1", "145,723", "20.9", "1,271", "0.9"],
        ["", "中南米", "15,582", "2.4", "26,200", "3.8", "10,618", "68.1"],
        ["米州", "", "160,034", "24.5", "171,923", "24.6", "11,889", "7.4"],
        ["欧州", "", "92,356", "14.1", "100,435", "14.4", "8,079", "8.7"],
        ["日本", "", "100,000", "15.3", "110,000", "15.8", "10,000", "10.0"],
        ["合計", "", "352,390", "100.0", "382,358", "100.0", "29,968", "8.5"]])
    g = geo_from(tables(zipped(b)), {("2026-04-01", "2026-09-30"): 382358e6})
    assert list(g[("2026-04-01", "2026-09-30")]) == ["米州", "欧州", "日本"], g

    # (다) 고마쓰 꼴 — 지역이 가로. 건설기계 부문 표(합이 연결과 안 맞음)는 버린다.
    c1 = "建設機械・車両部門の地域別売上高" + _t([
        ["", "前中間", "当中間"], ["米州", "799,064", "1,028,199"], ["日本", "141,583", "136,050"]])
    c2 = "【地域別情報】" + _t([
        ["", "米州", "欧州・ アフリカ・ 中近東", "オセアニア・ アジア※・ CIS", "日本", "連結"],
        ["前中間連結会計期間", "853,908", "352,436", "500,071", "185,172", "1,891,587"],
        ["当中間連結会計期間", "1,080,280", "392,399", "499,903", "186,057", "2,158,639"]])
    g = geo_from(tables(zipped(c1, c2)), {("2026-04-01", "2026-09-30"): 2158639e6,
                                          ("2025-04-01", "2025-09-30"): 1891587e6})
    cur = g[("2026-04-01", "2026-09-30")]
    assert len(cur) == 4 and cur["日本"] == 186057e6, cur
    assert g[("2025-04-01", "2025-09-30")]["米州"] == 853908e6

    # (라) 억엔 단위(다케다) · 증감 칸은 합이 안 맞아 저절로 빠진다
    d = "地域別売上収益 ( 単位 : 億円 )" + _t([
        ["売上収益:", "前年同期", "当期", "増減額"],
        ["日本", "2,191", "2,139", "△52"], ["米国", "10,919", "11,307", "388"],
        ["その他", "9,085", "10,624", "1,539"], ["合計", "22,195", "24,070", "1,875"]])
    g = geo_from(tables(zipped(d)), {("2026-04-01", "2026-09-30"): 2407000e6})
    assert g[("2026-04-01", "2026-09-30")]["米国"] == 11307e8, g

    # (마) 사업부 표는 지역이 아니다 — 앞글에 「地域」 이 있어도 이름이 지역이 아니면 안 읽는다
    e = "地域別に関する情報" + _t([["", "前", "当"], ["消化器系疾患", "6,928", "7,768"],
                                ["希少疾患", "3,805", "3,957"], ["合計", "10,733", "11,725"]])
    assert not geo_from(tables(zipped(e)), {("2026-04-01", "2026-09-30"): 11725e6})

    # (바) 수주 — 마키노 꼴(당기 금액 + 구성비 + 전년비) / 구덴코 꼴(전기·당기·증감)
    f1 = "受注及び販売の状況 １．受注の状況" + _t([
        ["受注高", "（百万円未満切捨て）"], ["セグメントの名称", "当中間連結会計期間", "前年同期比"],
        ["金額", "比率（%）", "比率（%）"], ["Ⅰ", "37,147", "20.9", "+59.7"], ["Ⅱ", "78,237", "44.0", "+26.0"],
        ["Ⅲ", "62,555", "35.1", "+52.0"], ["合計", "177,939", "100.0", "+42.2"]])
    f2 = "②工事受注高" + _t([
        ["期 別 部門別", "前中間連結会計期間", "", "当中間連結会計期間", "", "比較増減", ""],
        ["", "金額(百万円)", "構成比(%)", "金額(百万円)", "構成比(%)", "金額(百万円)", "増減(%)"],
        ["配電線工事", "27,875", "10.1", "29,516", "10.6", "1,640", "5.9"],
        ["屋内線工事", "157,715", "57.3", "145,829", "52.4", "△11,885", "△7.5"],
        ["空調管工事", "89,666", "32.6", "103,060", "37.0", "13,394", "14.9"],
        ["合計", "275,256", "100", "278,405", "100", "3,149", "1.1"]])
    f3 = "⑥通期の売上高・工事受注高の予想" + _t([["部門別", "売上高", "工事受注高"], ["合計", "500,000", "495,000"]])
    o = orders_from(tables(zipped(f1, f2, f3)))
    assert len(o) == 2, o
    assert (o[0]["prev"], o[0]["cur"]) == (None, 177939), o[0]
    assert (o[1]["prev"], o[1]["cur"]) == (275256, 278405), o[1]
    print("geojp 스스로 시험: 통과 (6꼴)")


if __name__ == "__main__":
    _selftest()


# ── 사업부(PDF) ──────────────────────────────────────────────────────
# 2021 년 이전 결산단신 zip 에는 본문이 없어 사업부 표가 PDF 에만 있다(미쓰비시전기·도쿄일렉트론의 결산 분기가 해마다 빈
# 까닭, 11/05). 표 생김새: 줄마다 사업부, 칸은 [전년 매출 | 전년 이익 | 올해 매출 | 올해 이익 | 증감…], 아래에 「計」(부문 합)와
# 「連結合計」(연결 매출). **칸 하나를 두 번 검산**한다 — 부문 합 = 「計」(원 단위), 「連結合計」 = 그 기간 연결 매출(1.5% 안).
SEG_HEAD = re.compile(r"セグメント|事業の種類別|事業別|部門別")
SEG_SKIP = re.compile(r"消\s*去|全\s*社|調\s*整|本社|内部|振替|セグメント間")
SEG_GROSS = re.compile(r"^(計|小計|セグメント計|報告セグメント計)$")
SEG_CONS = re.compile(r"^連結(合計|計)?$|^(合\s*計|総\s*計)$")


def seg_lines(pdf):
    """PDF -> 칸으로 나눈 줄들. 우리 해석기가 못 열면(암호) pdftotext."""
    import pdftext
    # pdftotext 가 먼저다 — 우리 해석기는 숫자 글꼴을 못 푸는 PDF(미쓰비시전기 2019)에서 숫자를 통째로 빠뜨린다
    try:
        rows = [[c.strip() for c in ln.split(" | ")] for ln in pdftext.layout_lines(pdf, 40)]
    except Exception:                                   # noqa: BLE001
        rows = []
    if not rows:
        try:
            for cells in pdftext.extract_cells(pdf, max_pages=40):
                rows.append([re.sub(r"\s+", " ", t).strip() for _x, t in sorted(cells) if t.strip()])
        except Exception:                               # noqa: BLE001
            rows = []
    out = []
    for r in rows:
        k = next((i for i, c in enumerate(r) if num(c) is not None), len(r))
        if k > 1:                                       # 「家 | 庭 | 電 | 器」 -> 「家庭電器」
            r = ["".join(r[:k])] + r[k:]
        if r:
            r[0] = re.sub(r"(?<=[^\x00-\x7f]) +(?=[^\x00-\x7f])", "", r[0])
            out.append(r)
    return out


def seg_from_pdf(pdf, spans):
    """결산단신 PDF + {(시작, 종료): 누계 매출} -> {(시작, 종료): {부문: 값(엔)}}. 검산을 못 넘으면 그 칸은 버린다."""
    rows = seg_lines(pdf)
    got = {}
    for i, r in enumerate(rows):
        txt = " ".join(r)
        if len(txt) > 60 or not SEG_HEAD.search(txt) or GEO_HEAD.search(txt):
            continue
        body = rows[i + 1:i + 30]
        segs, gross, cons = [], None, None
        for b in body:
            name, vals = b[0], [num(c) for c in b[1:]]
            if not any(v is not None for v in vals):
                continue
            if SEG_GROSS.match(name):
                gross = vals
                continue
            if SEG_CONS.match(name):
                cons = vals
                break
            if SEG_SKIP.search(name) or num(name) is not None:
                continue
            if gross is None:
                segs.append((name, vals))
        if len(segs) < 2 or not cons:
            continue
        width = min(len(cons), max(len(v) for _, v in segs))
        for k in range(width):
            c = cons[k]
            if not c or c <= 0:
                continue
            leaf = [(n, v[k]) for n, v in segs if k < len(v) and v[k] is not None]
            if len(leaf) < 2 or len(leaf) != len(segs) or any(v <= 0 for _, v in leaf):
                continue
            s = sum(v for _, v in leaf)
            if gross and k < len(gross) and gross[k] is not None:
                if abs(s - gross[k]) > max(3, abs(gross[k]) * 2e-5):
                    continue                            # 부문 합이 「計」와 안 맞는 칸(이익·증감 칸)
            elif abs(s - c) > max(3, abs(c) * 2e-5):
                continue                                # 「計」가 없으면 부문 합 = 연결 매출이어야 한다
            best = None
            for span, rev in spans.items():
                if not rev:
                    continue
                for sc in SCALES:
                    err = abs(c * sc - rev) / rev
                    if err <= 0.003 and (best is None or err < best[0]):   # 연결 매출은 요약과 같은 숫자 — 넉넉하면 전년 칸이 올해에 앉는다
                        best = (err, span, sc)
            if best:
                err, span, sc = best
                if span not in got or err < got[span][0]:
                    got[span] = (err, {n: v * sc for n, v in leaf})
    return {k: v[1] for k, v in got.items()}
