# -*- coding: utf-8 -*-
"""流通ニュース(ryutsuu.biz) 의 월차 기사에서 **달별 전년동월비**를 읽는다.

**왜 이 소스인가.** 일본에는 대만(MOPS)처럼 월매출을 의무로 내는 제도가 없어서
모아 파는 곳은 유료고(JPX TDnet API 24만엔·QUICK), 공짜로 모아 두는 곳은 전부
데이터센터 IP 를 막는다(월차Web·irbank·minkabu·가부탄·ullet). 두드려 본 여덟
곳 가운데 **여기만 열린다**(200·119KB).

**그리고 여기에만 있는 회사가 많다.** TDnet 적시공시로 월매출을 내는 회사는
우리 목록에 209곳인데, 니토리·패스트리·시마무라·젠쇼·요시노야·세븐&아이·
야마다·비쿠카메라처럼 **자사 IR 페이지에만 올리는 큰 회사**는 그 목록에 아예
없다. 실측한 큰 소매·외식 33곳 중 **23곳이 우리에게 없던 회사**였다.

**기사가 표를 통째로 준다.** 두 꼴이다.

    月度  売上高(全店前年比) 売上高(既存店前年比) 客数 客単価
    4月       8.6％増            3.7％増        …          <- 한 회사, 여러 달
    …
    8月       4.4％増            2.0％増

    8月     既存店売上高前年同月比  全店売上高前年同月比
    ユニクロ        1.3%減              2.2%減            <- 한 달, 여러 회사
    しまむら        5.3%増              5.8%増

**회사 이름은 브랜드다.** 「ユニクロ」·「すき家」·「Joshin」 처럼 상장사 이름이
아니라 브랜드로 적는다. 그래서 `BRANDS` 에 **아는 것만** 손으로 적는다 —
회사 이름 사전과 같은 규칙이다. 잘못 이으면 남의 회사 숫자가 붙으므로
**자회사·비상장·통합으로 사라진 이름은 넣지 않는다**(ローソン 은 2024년에
상장폐지, ファミマ·オーケー·카인즈는 비상장, いなげや·カスミ 는 USMH 자회사).

**기사 제목의 수치로 스스로 검산한다.** 「バロー／8月の既存店売上高2.0％増」 의
2.0 이 표의 마지막 달 既存店 과 안 맞으면 그 기사를 버린다 — 회사가 둘 이상
실린 기사(바로의 슈퍼와 드럭스토어)에서 엉뚱한 표를 집는 것을 막는다.
"""
import html as _html
import re
from datetime import date

__all__ = ["BRANDS", "read", "article_links"]

# 브랜드/약칭 -> (종목코드, 상장사 이름). **아는 것만** 적는다.
BRANDS = {
    # 편의점·슈퍼
    "セブン-イレブン": ("3382", "セブン＆アイ・ホールディングス"),
    "セブンイレブン": ("3382", "セブン＆アイ・ホールディングス"),
    "ミニストップ": ("9946", "ミニストップ"),
    "イオン": ("8267", "イオン"),
    "ヤオコー": ("8279", "ヤオコー"),
    "ライフ": ("8194", "ライフコーポレーション"),
    "ベルク": ("9974", "ベルク"),
    "バロー": ("9956", "バローホールディングス"),
    "オークワ": ("8217", "オークワ"),
    "アークス": ("9948", "アークス"),
    "マックスバリュ東海": ("8198", "マックスバリュ東海"),
    "アクシアル リテイリング": ("8255", "アクシアル リテイリング"),
    "アクシアル": ("8255", "アクシアル リテイリング"),
    "平和堂": ("8276", "平和堂"),
    "イズミ": ("8273", "イズミ"),
    "サンエー": ("2659", "サンエー"),
    "マキヤ": ("9890", "マキヤ"),
    # 드럭스토어
    "ツルハHD": ("3391", "ツルハホールディングス"),
    "ツルハ": ("3391", "ツルハホールディングス"),
    "コスモス薬品": ("3349", "コスモス薬品"),
    "スギ薬局": ("7649", "スギホールディングス"),
    "スギHD": ("7649", "スギホールディングス"),
    "マツキヨココカラ": ("3088", "マツキヨココカラ＆カンパニー"),
    "クスリのアオキ": ("3549", "クスリのアオキホールディングス"),
    "薬王堂": ("7679", "薬王堂ホールディングス"),
    "サツドラ": ("3544", "サツドラホールディングス"),
    # 홈센터
    "コメリ": ("8218", "コメリ"),
    "DCM": ("3050", "DCM"),
    "コーナン商事": ("7516", "コーナン商事"),
    "アークランズ": ("9842", "アークランズ"),
    "ナフコ": ("2790", "ナフコ"),
    "ジョイフル本田": ("3191", "ジョイフル本田"),
    # 가전
    "ヤマダHD": ("9831", "ヤマダホールディングス"),
    "ヤマダデンキ": ("9831", "ヤマダホールディングス"),
    "ビックカメラ": ("3048", "ビックカメラ"),
    "エディオン": ("2730", "エディオン"),
    "ケーズデンキ": ("8282", "ケーズホールディングス"),
    "ケーズHD": ("8282", "ケーズホールディングス"),
    "Joshin": ("8173", "上新電機"),
    "上新電機": ("8173", "上新電機"),
    "ノジマ": ("7419", "ノジマ"),
    # 의류·잡화
    "ユニクロ": ("9983", "ファーストリテイリング"),
    "ジーユー": ("9983", "ファーストリテイリング"),
    "しまむら": ("8227", "しまむら"),
    "アダストリア": ("2685", "アンドエスティホールディングス"),
    "ハニーズ": ("2792", "ハニーズホールディングス"),
    "西松屋": ("7545", "西松屋チェーン"),
    "西松屋チェーン": ("7545", "西松屋チェーン"),
    "ワークマン": ("7564", "ワークマン"),
    "ABCマート": ("2670", "エービーシー・マート"),
    "ユナイテッドアローズ": ("7606", "ユナイテッドアローズ"),
    "パルグループ": ("2726", "パルグループホールディングス"),
    "コックス": ("9876", "コックス"),
    # 외식
    "マクドナルド": ("2702", "日本マクドナルドホールディングス"),
    "日本マクドナルド": ("2702", "日本マクドナルドホールディングス"),
    "すき家": ("7550", "ゼンショーホールディングス"),
    "ゼンショー": ("7550", "ゼンショーホールディングス"),
    "吉野家": ("9861", "吉野家ホールディングス"),
    "松屋": ("9887", "松屋フーズホールディングス"),
    "松屋フーズ": ("9887", "松屋フーズホールディングス"),
    "モスバーガー": ("8153", "モスフードサービス"),
    "モスフードサービス": ("8153", "モスフードサービス"),
    "サイゼリヤ": ("7581", "サイゼリヤ"),
    "くら寿司": ("2695", "くら寿司"),
    "スシロー": ("3563", "FOOD & LIFE COMPANIES"),
    "王将フードサービス": ("9936", "王将フードサービス"),
    "餃子の王将": ("9936", "王将フードサービス"),
    "リンガーハット": ("8200", "リンガーハット"),
    "物語コーポレーション": ("3097", "物語コーポレーション"),
    "ドトール": ("3087", "ドトール・日レスホールディングス"),
    "日本KFC": ("9873", "日本KFCホールディングス"),
    "ケンタッキー": ("9873", "日本KFCホールディングス"),
    "ハイデイ日高": ("7611", "ハイデイ日高"),
    "トリドール": ("3397", "トリドールホールディングス"),
    "丸亀製麺": ("3397", "トリドールホールディングス"),
    "大戸屋": ("2705", "大戸屋ホールディングス"),
    "幸楽苑": ("7554", "幸楽苑ホールディングス"),
    "コロワイド": ("7616", "コロワイド"),
    # 백화점·기타
    "三越伊勢丹": ("3099", "三越伊勢丹ホールディングス"),
    "J.フロント": ("3086", "J.フロント リテイリング"),
    "Jフロント": ("3086", "J.フロント リテイリング"),
    "高島屋": ("8233", "高島屋"),
    "H2O": ("8242", "エイチ・ツー・オー リテイリング"),
    "ニトリ": ("9843", "ニトリホールディングス"),
    "良品計画": ("7453", "良品計画"),
    "パンパシフィック": ("7532", "パン・パシフィック・インターナショナルホールディングス"),
    "PPIH": ("7532", "パン・パシフィック・インターナショナルホールディングス"),
    "ドン・キホーテ": ("7532", "パン・パシフィック・インターナショナルホールディングス"),
    "トライアル": ("141A", "トライアルホールディングス"),
    "ゲオ": ("2681", "ゲオホールディングス"),
    "ハードオフ": ("2674", "ハードオフコーポレーション"),
    "セリア": ("2782", "セリア"),
}

TAG = re.compile(r"<[^>]+>")
ROW_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
CELL_RE = re.compile(r"(?is)<(t[dh])([^>]*)>(.*?)</\1>")
SPAN_RE = re.compile(r'(?i)\b(colspan|rowspan)\s*=\s*"?\'?(\d+)')
TABLE_RE = re.compile(r"(?is)<table[^>]*>(.*?)</table>")
MAIN_RE = re.compile(r"(?is)<main[^>]*>(.*?)</main>")
LINK_RE = re.compile(r'href="(https://www\.ryutsuu\.biz/sales/s\d+\.html)"')
DATE_RE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
MONTH_CELL = re.compile(r"^\(?(\d{1,2})月(度|分)?\)?$")
# 「2.0％増」·「1.3%減」. 「横ばい」·「－」 는 담지 않는다 — 0 인지 없는 것인지 모른다.
VAL_RE = re.compile(r"^([\d.]+)\s*[%％]?\s*(増|減)$")
ZEN = str.maketrans("０１２３４５６７８９．％，－　", "0123456789.%,- ")


def _txt(s: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    return re.sub(r"\s+", " ", _html.unescape(TAG.sub("", s))).strip()


def article_links(page_html: str):
    """목록 한 쪽에서 기사 주소를 뽑는다(차례를 지킨다)."""
    out, seen = [], set()
    for u in LINK_RE.findall(page_html):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _grid(tb: str):
    """표 -> 네모난 칸 배열. **colspan·rowspan 을 풀어 준다.**

    머리줄이 두 줄로 겹쳐 오고(「売上高」이 두 칸을 덮고 「月度」가 두 줄을 덮는다)
    칸 수가 줄마다 다르다. 안 풀면 어느 칸이 既存店 이고 어느 칸이 客数 인지
    알 수 없어, 객수를 매출로 싣게 된다.
    """
    rows, pend = [], {}
    for ri, tr in enumerate(ROW_RE.findall(tb)):
        out, ci = [], 0
        for _tag, attrs, body in CELL_RE.findall(tr):
            while (ri, ci) in pend:
                out.append(pend.pop((ri, ci)))
                ci += 1
            sp = {k.lower(): int(v) for k, v in SPAN_RE.findall(attrs)}
            cs, rs = min(sp.get("colspan", 1), 12), min(sp.get("rowspan", 1), 12)
            t = _txt(body)
            for _ in range(cs):
                out.append(t)
                for r2 in range(1, rs):
                    pend[(ri + r2, ci)] = t
                ci += 1
        while (ri, ci) in pend:
            out.append(pend.pop((ri, ci)))
            ci += 1
        rows.append(out)
    return rows


def _val(cell: str):
    """「2.0％増」 -> 102.0. 비율 하나로 맞춰 담는다(montable 과 같은 약속)."""
    m = VAL_RE.match(cell.translate(ZEN).replace(" ", ""))
    if not m:
        return None
    v = float(m.group(1))
    return 100.0 + (v if m.group(2) == "増" else -v)


# 열 이름표 — 무엇의 값인가. 매출이 아닌 열(객수·객단가)은 담지 않는다.
SALES = re.compile(r"売上")
NOT_SALES = re.compile(r"客数|客単価|店舗数|客単|坪|人数|件数")
SAME = re.compile(r"既存店|既存")
ALL = re.compile(r"全店|全社|チェーン全店|グループ")


def _cols(rows, n_hdr, width):
    """열마다 (같은가게인가, 매출인가) 를 정한다. 머리줄을 세로로 이어 읽는다."""
    out = []
    for c in range(width):
        lab = "".join(rows[r][c] for r in range(n_hdr)
                      if c < len(rows[r]))
        out.append(lab)
    return out


def _years(months, last_year, last_month):
    """표의 달들에 해를 붙인다. 맨 아랫줄이 보고하는 달이다 — 거기서 거꾸로
    올라가며 달 번호가 커지는 자리마다 해를 하나 뺀다(montable 과 같은 규칙)."""
    yrs = [None] * len(months)
    yrs[-1] = last_year
    for i in range(len(months) - 2, -1, -1):
        yrs[i] = yrs[i + 1] - 1 if months[i] > months[i + 1] else yrs[i + 1]
    return yrs


TITLE_SAME = re.compile(r"既存店[^、。]*?([\d.]+)[%％](増|減)")
TITLE_ALL = re.compile(r"全店[^、。]*?([\d.]+)[%％](増|減)")


def _check(title, got):
    """기사 제목의 수치와 표의 마지막 달이 맞는가. 안 맞으면 그 표가 아니다."""
    t = title.translate(ZEN)
    for pat, key in ((TITLE_SAME, "same"), (TITLE_ALL, "all")):
        m = pat.search(t)
        if not m:
            continue
        want = 100.0 + (float(m.group(1)) if m.group(2) == "増"
                        else -float(m.group(1)))
        have = got.get(key)
        return have is not None and abs(have - want) < 0.05
    return True                       # 제목에 수치가 없으면 검산할 것이 없다


def read(page: str, url: str):
    """기사 한 장 -> [{code, brand, months: {'YYYY-MM': {same, all}}}].

    못 읽으면 빈 목록이다. **지어내지 않는다.**
    """
    m = MAIN_RE.search(page)
    body = m.group(1) if m else page
    dm = DATE_RE.search(_txt(body))
    if not dm:
        return []
    day = date(int(dm.group(1)), int(dm.group(2)), int(dm.group(3)))
    tm = re.search(r"(?is)<title>(.*?)</title>", page)
    title = _txt(tm.group(1)) if tm else ""
    head = title.split("／")[0].strip() if "／" in title else ""

    out, used_single = [], False
    for tb in TABLE_RE.findall(body):
        rows = [r for r in _grid(tb) if r]
        if len(rows) < 3:
            continue
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        mrows = [i for i, r in enumerate(rows) if MONTH_CELL.match(r[0].translate(ZEN))]

        # (가) 달이 세로로 선 표 — 한 회사, 여러 달
        if len(mrows) >= 3 and mrows[0] >= 1:
            if used_single or head not in BRANDS:
                continue
            n_hdr = mrows[0]
            labs = _cols(rows, n_hdr, width)
            months = [int(MONTH_CELL.match(rows[i][0].translate(ZEN)).group(1))
                      for i in mrows]
            yrs = _years(months, day.year if months[-1] <= day.month
                         else day.year - 1, months[-1])
            got = {}
            for k, i in enumerate(mrows):
                rec = {}
                for c in range(1, width):
                    lab = labs[c]
                    if not SALES.search(lab) or NOT_SALES.search(lab):
                        continue
                    v = _val(rows[i][c])
                    if v is None:
                        continue
                    if SAME.search(lab):
                        rec.setdefault("same", v)
                    elif ALL.search(lab):
                        rec.setdefault("all", v)
                if rec:
                    got[f"{yrs[k]:04d}-{months[k]:02d}"] = rec
            if not got:
                continue
            last = got[max(got)]
            if not _check(title, last):
                continue
            code, name = BRANDS[head]
            out.append({"code": code, "brand": head, "name": name,
                        "day": day.isoformat(), "doc": url, "months": got})
            used_single = True
            continue

        # (나) 달이 머리에 하나, 줄마다 회사 — 한 달, 여러 회사
        mh = MONTH_CELL.match(rows[0][0].translate(ZEN))
        if not mh:
            continue
        mo = int(mh.group(1))
        yr = day.year if mo <= day.month else day.year - 1
        labs = rows[0]
        per = f"{yr:04d}-{mo:02d}"
        for r in rows[1:]:
            brand = r[0].strip()
            if brand not in BRANDS:
                continue
            rec = {}
            for c in range(1, width):
                lab = labs[c]
                if not SALES.search(lab) or NOT_SALES.search(lab):
                    continue
                v = _val(r[c])
                if v is None:
                    continue
                if SAME.search(lab):
                    rec.setdefault("same", v)
                elif ALL.search(lab):
                    rec.setdefault("all", v)
            if not rec:
                continue
            code, name = BRANDS[brand]
            out.append({"code": code, "brand": brand, "name": name,
                        "day": day.isoformat(), "doc": url,
                        "months": {per: rec}})
    return out


# ── 스스로 시험 ─────────────────────────────────────────────────────────────
# `python monweb.py` — 실제 기사에서 본 두 꼴을 그대로 밟는다.
def _selftest():                                          # pragma: no cover
    ok = True

    # (가) 바로 — 한 회사, 다섯 달. 기사에 표가 둘이다(슈퍼·드럭스토어).
    #      제목의 2.0％増 과 맞는 **첫 표만** 담아야 한다.
    a = """<html><title>バロー／8月の既存店売上高2.0％増、客数0.7％減 | 流通ニュース</title>
    <main>2026年09月18日 10:30 ／ 月次
    <table>
      <tr><th rowspan="2">月度</th><th colspan="2">売上高</th><th>客数</th><th>客単価</th></tr>
      <tr><th>全店 前年比</th><th>既存店 前年比</th><th>既存店 前年比</th><th>既存店 前年比</th></tr>
      <tr><td>4月</td><td>8.6％増</td><td>3.7％増</td><td>1.0％増</td><td>2.6％増</td></tr>
      <tr><td>5月</td><td>12.1％増</td><td>7.6％増</td><td>3.9％増</td><td>3.6％増</td></tr>
      <tr><td>6月</td><td>2.2％増</td><td>0.9％減</td><td>2.5％減</td><td>1.7％増</td></tr>
      <tr><td>7月</td><td>3.9％増</td><td>1.9％増</td><td>0.9％減</td><td>2.7％増</td></tr>
      <tr><td>8月</td><td>4.4％増</td><td>2.0％増</td><td>0.7％減</td><td>2.8％増</td></tr>
    </table>
    <table>
      <tr><th rowspan="2">月度</th><th colspan="2">売上高</th><th>客数</th><th>客単価</th></tr>
      <tr><th>全店 前年比</th><th>既存店 前年比</th><th>既存店 前年比</th><th>既存店 前年比</th></tr>
      <tr><td>4月</td><td>2.2％増</td><td>1.2％減</td><td>4.9％減</td><td>3.9％増</td></tr>
      <tr><td>5月</td><td>3.9％増</td><td>0.7％増</td><td>1.9％減</td><td>2.7％増</td></tr>
      <tr><td>6月</td><td>1.0％減</td><td>4.0％減</td><td>5.7％減</td><td>1.8％増</td></tr>
      <tr><td>7月</td><td>3.4％増</td><td>0.7％増</td><td>3.0％減</td><td>3.7％増</td></tr>
      <tr><td>8月</td><td>4.2％増</td><td>1.5％増</td><td>1.7％減</td><td>3.3％増</td></tr>
    </table></main></html>"""
    g = read(a, "https://www.ryutsuu.biz/sales/s091872.html")
    if len(g) != 1 or g[0]["code"] != "9956" \
            or g[0]["months"].get("2026-08") != {"all": 104.4, "same": 102.0} \
            or g[0]["months"].get("2026-04") != {"all": 108.6, "same": 103.7} \
            or len(g[0]["months"]) != 5:
        print("!! 가) 한 회사 여러 달", g)
        ok = False

    # (나) 캐주얼 의류 4사 — 한 달, 여러 회사.
    b = """<html><title>カジュアル衣料4社／8月の既存店売上高はユニクロ1.3％減、しまむら5.3％増 | 流通ニュース</title>
    <main>2026年09月11日 10:05 ／ 月次
    <table>
      <tr><th>8月</th><th>既存店売上高前年同月比</th><th>全店売上高前年同月比</th></tr>
      <tr><td>ユニクロ</td><td>1.3%減</td><td>2.2%減</td></tr>
      <tr><td>しまむら</td><td>5.3%増</td><td>5.8%増</td></tr>
      <tr><td>アダストリア</td><td>1.9%増</td><td>3.1%増</td></tr>
      <tr><td>ハニーズ</td><td>4.3％減</td><td>4.9％減</td></tr>
    </table></main></html>"""
    g = read(b, "https://www.ryutsuu.biz/sales/s091142.html")
    by = {x["code"]: x for x in g}
    if len(g) != 4 or by["9983"]["months"]["2026-08"] != {"same": 98.7, "all": 97.8} \
            or by["8227"]["months"]["2026-08"] != {"same": 105.3, "all": 105.8}:
        print("!! 나) 한 달 여러 회사", g)
        ok = False

    # (다) 제목의 수치와 표가 안 맞으면 버린다.
    c = a.replace("バロー／8月の既存店売上高2.0％増", "バロー／8月の既存店売上高9.9％増")
    if read(c, "u"):
        print("!! 다) 검산에 안 걸렸다")
        ok = False

    # (라) 객수·객단가 열은 담지 않는다(위 (가)에서 이미 확인) + 모르는 브랜드는 건너뛴다.
    d = b.replace("ユニクロ", "どこかの非上場店")
    g = read(d, "u")
    if any(x["brand"] == "どこかの非上場店" for x in g) or len(g) != 3:
        print("!! 라) 모르는 브랜드", g)
        ok = False

    print("monweb 스스로 시험:", "통과" if ok else "떨어짐")
    return 0 if ok else 1


if __name__ == "__main__":                                # pragma: no cover
    import sys as _sys
    _sys.exit(_selftest())
