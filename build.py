# -*- coding: utf-8 -*-
"""
data/earnings*.json  ->  index.html (단일 파일, 외부 의존 없음)

일본·미국·홍콩 세 시장을 한 페이지에 합친다. 시장마다 수집 소스도 원본 언어도
다르지만, 화면에 올라가는 행의 모양은 하나로 맞춘다 — pack_* 가 그 일을 한다.

주간 캘린더는 클라이언트에서 그린다. 주(週)를 넘길 때마다 서버가 없으니,
전 기간 데이터를 JSON으로 심어두고 JS가 해당 주만 잘라 렌더한다.

세 시장이 다 있어야 돌아가는 건 아니다. data/ 에 있는 것만 싣고, 없는 시장은
"미수집"으로 적는다. 없는 걸 빈 화면으로 두면 '발표가 없는 것'처럼 보인다.
"""
import base64
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import companies
import companies_hk
import companies_us
from descriptions import DESC_KO
from markets import (CAP_STEPS, HK_TYPICAL, HKT, HOLIDAYS, JP_TYPICAL, KST,
                     MARKET_KO, MARKET_ORDER, MARKETS, SECTOR_KO, TIMING_KO,
                     US_AMC, US_BMO, US_EDT, US_EST, US_SECTOR_KO, USD_KRW,
                     holiday_ko)
from translit import to_korean

HERE = Path(__file__).parent
OUT = HERE / "index.html"

# 시장별 주목종목 사전. 코드가 시장 사이에 겹치므로(일본 8035 / 홍콩 08035)
# 합칠 때는 "jp:8035" 처럼 시장을 앞에 붙여 키를 만든다.
DICTS = {"jp": companies, "us": companies_us, "hk": companies_hk}

# 결산종별 표기를 짧게. 원문은 第１/第２/第３/本.
KIND_MAP = {"第１": "1Q", "第２": "2Q", "第３": "3Q", "本": "본결산"}

# ── 로고 ────────────────────────────────────────────────────────────────
# 회원님이 정한 어닝 사무라이 얼굴. **그림 파일을 두지 않고 SVG 로 그린다** —
# 이 저장소의 산출물은 index.html 한 장뿐이라, 그림을 따로 두면 그 규칙이 깨지고
# 캐시가 어긋났을 때 아이콘만 빈칸으로 뜬다. 한 군데서 만들어 파비콘과 제목 옆에
# 같이 쓴다(전에는 같은 SVG 를 두 곳에 손으로 붙여 넣어 두 그림이 달라졌다).
#
#   투구(검정) · 쿠와가타 뿔 둘(금) · 가운데 금색 문장 안에 오르는 화살표
#   노란 얼굴 · 검은 선글라스 · 웃는 입 · 양옆 어깨판(검정+금테)
LOGO_BG = "#141C33"        # 남색 바탕
LOGO_GOLD = "#F5B21C"
LOGO_GOLD_D = "#D9930F"    # 금색 그늘
LOGO_BLACK = "#17181C"
LOGO_FACE = "#FFC62B"

LOGO_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<rect width="128" height="128" rx="26" fill="{LOGO_BG}"/>
<g fill="{LOGO_BLACK}" stroke="{LOGO_GOLD}" stroke-width="2.6">
<rect x="7" y="54" width="20" height="56" rx="6"/>
<rect x="101" y="54" width="20" height="56" rx="6"/>
</g>
<g fill="{LOGO_GOLD}">
<rect x="7" y="54" width="20" height="7" rx="3.5"/>
<rect x="101" y="54" width="20" height="7" rx="3.5"/>
<circle cx="17" cy="73" r="4.6"/>
<circle cx="111" cy="73" r="4.6"/>
</g>
<g fill="none" stroke="{LOGO_GOLD}" stroke-width="14" stroke-linecap="round">
<path d="M46 56C30 46 16 30 14 9"/>
<path d="M82 56c16-10 30-26 32-47"/>
</g>
<circle cx="64" cy="76" r="38" fill="{LOGO_FACE}"/>
<path d="M23 62C23 37 41 21 64 21s41 16 41 41z" fill="{LOGO_BLACK}"/>
<rect x="19" y="53" width="90" height="11" rx="5.5" fill="{LOGO_BLACK}"/>
<circle cx="64" cy="39" r="13.5" fill="{LOGO_GOLD}"/>
<circle cx="64" cy="39" r="10.5" fill="{LOGO_BLACK}"/>
<path d="M56.5 43.5l5-5 3.5 3.5 5.5-7" fill="none" stroke="{LOGO_GOLD}"
      stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M73 32.5l-6.5.8 3.6 4.2z" fill="{LOGO_GOLD}"/>
<rect x="27" y="75" width="74" height="7" rx="3.5" fill="{LOGO_BLACK}"/>
<rect x="27" y="75" width="33" height="19" rx="8" fill="{LOGO_BLACK}"/>
<rect x="68" y="75" width="33" height="19" rx="8" fill="{LOGO_BLACK}"/>
<path d="M52 103q12 9 24 0" fill="none" stroke="{LOGO_GOLD_D}" stroke-width="4"
      stroke-linecap="round"/>
</svg>"""


# ── 국기 ────────────────────────────────────────────────────────────────
# **윈도우는 국기 이모지를 못 그린다.** 🇺🇸 는 문자 두 개(U+1F1FA U+1F1F8)를
# 폰트가 합쳐서 국기로 보여주는 것인데, 윈도우 기본 폰트에는 그 합침이 없어서
# 그냥 `US` 라는 글자 두 개로 뜬다. 맥·아이폰에서는 국기로 보이니 만든 사람은
# 모르고 지나간다. 그래서 국기는 이모지가 아니라 **SVG 로 그려 넣는다.**
#
# 표에 만 줄이 넘게 들어가므로 SVG 를 줄마다 심으면 안 된다. 스타일시트에
# 한 번만 담고 화면에서는 `<span class="fl fl-us">` 로 부른다.
FLAG_SVG = {
    "jp": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 15">'
           '<rect width="21" height="15" fill="#fff"/>'
           '<circle cx="10.5" cy="7.5" r="4.4" fill="#BC002D"/></svg>'),
    "us": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 15">'
           '<rect width="21" height="15" fill="#fff"/><g fill="#B22234">'
           '<rect width="21" height="1.15"/><rect y="2.3" width="21" height="1.15"/>'
           '<rect y="4.6" width="21" height="1.15"/><rect y="6.9" width="21" height="1.15"/>'
           '<rect y="9.2" width="21" height="1.15"/><rect y="11.5" width="21" height="1.15"/>'
           '<rect y="13.8" width="21" height="1.2"/></g>'
           '<rect width="9" height="8.05" fill="#3C3B6E"/><g fill="#fff">'
           '<circle cx="2" cy="2" r=".75"/><circle cx="4.5" cy="2" r=".75"/>'
           '<circle cx="7" cy="2" r=".75"/><circle cx="3.25" cy="4" r=".75"/>'
           '<circle cx="5.75" cy="4" r=".75"/><circle cx="2" cy="6" r=".75"/>'
           '<circle cx="4.5" cy="6" r=".75"/><circle cx="7" cy="6" r=".75"/></g></svg>'),
    # 홍콩기의 자형화(紫荊花)는 다섯 잎이다. 잎마다 별이 하나씩 더 있지만
    # 16px 에서는 안 보이므로 잎만 그린다.
    "hk": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 15">'
           '<rect width="21" height="15" fill="#DE2910"/>'
           '<g fill="#fff" transform="translate(10.5,7.5)">'
           '<ellipse cx="0" cy="-3.3" rx="1.2" ry="2.2"/>'
           '<ellipse cx="0" cy="-3.3" rx="1.2" ry="2.2" transform="rotate(72)"/>'
           '<ellipse cx="0" cy="-3.3" rx="1.2" ry="2.2" transform="rotate(144)"/>'
           '<ellipse cx="0" cy="-3.3" rx="1.2" ry="2.2" transform="rotate(216)"/>'
           '<ellipse cx="0" cy="-3.3" rx="1.2" ry="2.2" transform="rotate(288)"/>'
           '</g></svg>'),
}


def flag_css() -> str:
    """국기 한 벌을 스타일시트에 담는다."""
    return "\n".join(f'.fl-{m} {{ background-image:url("{data_uri(svg)}"); }}'
                     for m, svg in FLAG_SVG.items())


def flag_html(m: str) -> str:
    return f'<span class="fl fl-{m}" role="img" aria-label="{MARKETS[m]["ko"]}"></span>'


def data_uri(svg: str) -> str:
    """SVG 를 data: 주소로. `#` 과 따옴표만 바꾸면 브라우저가 그대로 읽는다."""
    one = " ".join(svg.split())
    return ("data:image/svg+xml,"
            + one.replace("#", "%23").replace('"', "'").replace("<", "%3C")
                 .replace(">", "%3E").replace("&", "%26"))


# 회원님이 그림 파일을 올려 두면 SVG 대신 그걸 쓴다. 저장소 맨 위에 `logo.png`
# (또는 .svg/.webp/.jpg) 라는 이름으로 두면 된다 — GitHub 웹에서 끌어다 놓으면 끝.
LOGO_TYPES = {".png": "image/png", ".svg": "image/svg+xml",
              ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def logo_uri() -> str:
    """올려 둔 그림이 있으면 그것, 없으면 SVG 로 그린 것.

    **그림도 index.html 안에 통째로 담는다.** 파일을 따로 두고 주소로 부르면
    산출물이 한 장이 아니게 되고, 캐시가 어긋났을 때 아이콘만 빈칸으로 뜬다.
    파비콘·제목 옆·홈화면 아이콘이 모두 이 한 곳에서 나온다.
    """
    for ext, mime in LOGO_TYPES.items():
        p = HERE / ("logo" + ext)
        if not p.exists():
            continue
        raw = p.read_bytes()
        # 파비콘까지 이 그림 하나로 쓰므로 너무 크면 페이지가 무거워진다.
        # 그래도 **말없이 무시하지는 않는다** — 회원님이 올린 것이 안 쓰이면
        # 왜 안 바뀌었는지 알 길이 없다.
        if len(raw) > 600_000:
            print(f"  ! {p.name} 이 {len(raw)/1024:,.0f}KB 라 페이지가 무거워집니다. "
                  f"그래도 그대로 씁니다.")
        print(f"  로고: {p.name} ({len(raw)/1024:,.0f}KB)")
        return f"data:{mime};base64," + base64.b64encode(raw).decode()
    return data_uri(LOGO_SVG)



def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def us_utc_offset(d: date) -> int:
    """미국 동부 표준시 오프셋. 3월 둘째 일요일 ~ 11월 첫째 일요일이 서머타임."""
    def nth_sunday(year, month, n):
        first = date(year, month, 1)
        first += timedelta(days=(6 - first.weekday()) % 7)     # 그 달 첫 일요일
        return first + timedelta(weeks=n - 1)
    start = nth_sunday(d.year, 3, 2)
    end = nth_sunday(d.year, 11, 1)
    return US_EDT if start <= d < end else US_EST


def to_kst(mkt: str, day: str, hhmm: str, timing: str):
    """현지 발표 시점을 한국 시각으로 옮긴다.

    돌려주는 값: (한국날짜, 'HH:MM', 정확도)
      정확도 1 = 원본에 실제 시각이 있었다 (홍콩)
      정확도 0 = 통상 시각으로 어림했다 (미국 장전/장후, 일본 15시)
    시각을 전혀 모르면 ('', 0) 으로 두고 날짜만 옮기지 않는다 —
    모르는 걸 아는 척하면 캘린더가 조용히 거짓말을 한다.
    """
    d = date.fromisoformat(day)
    if mkt == "jp":
        # JST 와 KST 는 둘 다 UTC+9 라 시차가 없다. 날짜도 그대로다.
        # TDnet 공시에는 실제 시각이 찍혀 온다 — 그럴 때만 정확도 1 이다.
        # 닛케이 예정에는 시각이 없어 15시로 어림한다(정확도 0).
        if hhmm:
            return day, hhmm, 1
        return day, "%02d:%02d" % JP_TYPICAL, 0
    if mkt == "hk":
        if hhmm:
            h, m = int(hhmm[:2]), int(hhmm[3:5])
            exact = 1
        else:
            h, m = HK_TYPICAL
            exact = 0
        shift = KST - HKT                                       # 한국이 1시간 빠르다
    else:                                                       # 미국
        if timing == "장전":
            h, m = US_BMO
        elif timing == "장후":
            h, m = US_AMC
        else:
            return "", "", 0                                    # 시각을 모르면 옮기지 않는다
        exact = 0
        shift = KST - us_utc_offset(d)
    total = h * 60 + m + shift * 60
    day_shift, mins = divmod(total, 24 * 60)
    return (d + timedelta(days=day_shift)).isoformat(), "%02d:%02d" % divmod(mins, 60), exact


def load(mkt: str):
    """시장 하나치 수집 결과를 읽는다. 없으면 None — 있는 것만 싣는다."""
    path = HERE / "data" / MARKETS[mkt]["data"]
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"  ! {path.name} 읽기 실패: {e}")
        return None
    raw.setdefault("rows", [])
    raw.setdefault("ok_days", sorted({r["date"] for r in raw["rows"]}))
    if mkt == "jp":
        merge_jp_past(raw)
        merge_jp_sched(raw)
    return raw


# 일본만 소스가 둘이다. 닛케이는 **앞으로의 예정**을 주고 TDnet 은 **이미 나온
# 공시**를 준다. 닛케이는 발표를 마친 줄을 목록에서 지워버리므로(과거 날짜를 넣어
# 되받아도 0건이다) 닛케이만 보면 발표를 끝낸 회사가 다음 분기까지 사라진다 —
# 트레져팩토리(3093)가 7월에 1분기를 내고 10월까지 사이트에서 없었다.
#
# 합칠 때 두 가지를 지킨다.
#   * **같은 분기를 두 줄로 만들지 않는다.** TDnet 에 실제 공시가 있으면 그
#     회사·그 결산기·그 분기의 닛케이 '예정' 줄은 지운다. 회사가 예정일을 옮겼을
#     때 옛 예정과 실제 발표가 나란히 남는 것을 막는다. 남기는 쪽은 실제 쪽이다.
#   * **수집한 날을 합친다.** 닛케이가 못 받은 날이라도 TDnet 이 받았으면 그 날은
#     구멍이 아니다. 캘린더의 '미수집 구간' 표시가 그만큼 줄어든다.
def merge_jp_past(raw: dict):
    path = HERE / "data" / "earnings_jp_past.json"
    if not path.exists():
        return
    try:
        past = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"  ! {path.name} 읽기 실패: {e}")
        return
    rows = past.get("rows", [])
    if not rows:
        return
    done = {(r["code"], r.get("fy", ""), r.get("kind", "")) for r in rows}
    # 같은 날 그 회사의 실제 공시가 있으면, 분기가 달라도 그날의 '예정' 줄은
    # 지운다. 아사히(2502)가 랜섬웨어로 결산이 밀려 8/14 에 1분기를 냈는데
    # 닛케이는 같은 날을 第２ 예정으로 갖고 있었다 — 분기가 달라 (code,fy,kind)
    # 판별을 통과해 같은 회사가 같은 날 두 줄로 섰다. 그날 정말 다른 분기가
    # 또 나오면 TDnet 이 실제 공시로 가져오므로 잃는 것이 없다.
    done_days = {(r["code"], r["date"]) for r in rows}
    kept = [r for r in raw["rows"]
            if (r["code"], r.get("fy", ""), r.get("kind", "")) not in done
            and (r["code"], r["date"]) not in done_days]
    dropped = len(raw["rows"]) - len(kept)
    # TDnet 목록에는 업종·거래소가 없고, **회사명은 줄임말이다** — 三菱ＵＦＪ,
    # アサヒ, ＯＢＣ. 그대로 두면 같은 회사가 지난주에는 「アサヒ」, 다음주에는
    # 「アサヒグループホールディングス」로 뜬다(3,284건 중 1,391건이 그랬다).
    # 같은 회사의 닛케이 줄에 있으면 그쪽 값을 옮겨 적는다. 지어내는 것이 아니라
    # 같은 회사의 같은 값이다. 닛케이에 없으면(646건) TDnet 줄임말을 그대로 둔다.
    side = {}
    for r in raw["rows"]:
        side.setdefault(r["code"], r)
    for r in rows:
        ref = side.get(r["code"])
        if not ref:
            continue
        if ref.get("name"):
            r["name"] = ref["name"]
        if not r.get("sector") and not r.get("market"):
            r["sector"] = ref.get("sector", "")
            r["market"] = ref.get("market", "")
    raw["rows"] = kept + rows
    raw["ok_days"] = sorted(set(raw["ok_days"]) | set(past.get("ok_days", [])))
    raw["source"] = (raw.get("source", "") + " + TDnet 결산단신(발표 완료분)").strip(" +")
    print(f"  일본 TDnet 발표 완료 {len(rows):,}건 / {len(past.get('ok_days', []))}일"
          f" · 닛케이 예정 {dropped:,}건을 실제 공시로 갈음")


# 일본의 셋째 소스 — JPX(일본거래소)의 결산발표 예정일 공식 엑셀.
# 닛케이가 데이터센터 IP 를 막아 CI 에서는 앞일이 자주 구멍이었다(8/17 이후가
# 통째로 「일본 미수집」으로 섰다). JPX 목록은 거래소에 신고된 전 종목 예정일이라
# 앞일을 공식으로 메운다(scrape_jp_sched.py, probe 8·9차).
#
# **이미 아는 줄을 이기지 못한다.** TDnet 은 실제 공시고 닛케이 예정은 (닿을 때는)
# 더 자주 갱신된다. 그래서 merge_jp_past 가 끝난 뒤에 불려, 아직 없는
# (code, fy, kind) 만 더한다 — 어휘를 닛케이 것에 맞춰 둔 이유다.
def merge_jp_sched(raw: dict):
    path = HERE / "data" / "earnings_jp_sched.json"
    if not path.exists():
        return
    try:
        sched = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"  ! {path.name} 읽기 실패: {e}")
        return
    rows = sched.get("rows", [])
    if not rows:
        return
    have = {(r["code"], r.get("fy", ""), r.get("kind", "")) for r in raw["rows"]}
    # 같은 회사가 이미 그날 줄을 갖고 있으면(실제든 예정이든) 더하지 않는다 —
    # 두 예정 소스가 같은 날을 다른 분기로 적어 두 줄이 되는 것을 막는다.
    have_days = {(r["code"], r["date"]) for r in raw["rows"]}
    add = [r for r in rows
           if (r["code"], r.get("fy", ""), r.get("kind", "")) not in have
           and (r["code"], r["date"]) not in have_days]
    raw["rows"] += add
    raw["ok_days"] = sorted(set(raw["ok_days"]) | set(sched.get("ok_days", [])))
    raw["source"] = (raw.get("source", "") + " + JPX 발표 예정일").strip(" +")
    print(f"  일본 JPX 예정 {len(rows):,}건 중 {len(add):,}건을 더함"
          f" (나머지는 닛케이·TDnet 에 이미 있음)")


# ── 시장별 행 다듬기 ───────────────────────────────────────────────
# 어느 시장이든 결과는 같은 모양으로 나온다:
#   [날짜, 코드, 한글명, 결산기, 분기, 업종, 거래소, 원문, 변환등급, 시장, 발표시각, 시총]
# 뒤 세 칸(시장·발표시각·시총)이 이번에 늘어난 자리다. 시각과 시총은 미국만 있다.

# 일본·홍콩 시총. 원본 소스가 안 줘서 따로 받아둔 것(scrape_caps.py).
# 없으면 0 — 그 시장에는 규모 필터가 걸리지 않고, 화면이 그렇게 적는다.
def load_extra():
    """따로 받아둔 시총·업종. 원본 소스가 안 주는 것들이다."""
    p = HERE / "data" / "caps.json"
    if not p.exists():
        return {}, {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("caps", {}), d.get("sectors", {})
    except (ValueError, OSError) as e:
        print(f"  ! caps.json 읽기 실패: {e}")
        return {}, {}


CAPS, SECTORS = load_extra()


def q_label(end, mid="", q=""):
    """분기말 날짜 -> '2Q26'. **끝난 날이 아니라 기간의 한가운데**로 가른다.

    회계 분기는 달력에 딱 맞지 않는다. 끝난 날로 가르면 이렇게 어긋난다.

      코카콜라  1~3월 분기가 4월 3일에 끝난다  -> 2Q26 (틀림, 1Q26 이 맞다)
      모토로라  4~6월 분기가 7월 4일에 끝난다  -> 3Q26 (틀림, 2Q26 이 맞다)

    수집기가 실제 한가운데(`mid`)를 담아 주면 그걸 쓴다. 없으면 끝나기 45일
    전으로 어림한다 — 13주 분기에는 맞지만 코스트코의 16주 분기에서는 빗나가
    라벨이 겹쳤다. 그래서 `mid` 가 있는 쪽이 옳다.
    """
    if q:
        return q                     # SEC 가 스스로 매긴 것. 그게 정답이다.
    d = date.fromisoformat(mid) if mid else \
        date.fromisoformat(end) - timedelta(days=45)
    return f"{(d.month - 1) // 3 + 1}Q{d.year % 100:02d}"


def h_label(end, mid=""):
    """'1H26'. **반기만 내는 회사는 반기답게 적는다.**

    홍콩에는 분기를 안 내고 반기만 내는 회사가 많다(695종목 중 215). 그걸
    2Q25·4Q25 처럼 분기 이름으로 적으면 사이가 빈 것처럼 보여 헷갈린다.

    한가운데를 담아 준 소스는 그걸 쓰고, 없으면 종료일에서 석 달을 되짚는다
    (반기의 한가운데다). stockanalysis 는 종료일만 준다.
    """
    d = (date.fromisoformat(mid) if mid
         else date.fromisoformat(end) - timedelta(days=90))
    return f"{1 if d.month <= 6 else 2}H{d.year % 100:02d}"


def per_index(label):
    """'2Q26'·'1H26' -> 정수. 앞뒤를 견주려면 숫자여야 한다."""
    try:
        n, kind, y = label[0], label[1], label[2:]
        slots = 4 if kind == "Q" else 2 if kind == "H" else 0
        if not slots:
            return None
        return (2000 + int(y)) * slots + int(n) - 1
    except (ValueError, IndexError, AttributeError):
        return None


def per_name(i, kind):
    slots = 4 if kind == "Q" else 2
    return f"{i % slots + 1}{kind}{(i // slots) % 100:02d}"


def unstack(labels, kind="Q"):
    """겹친 분기 이름을 뒤로 밀어 하나씩 떨어뜨린다.

    회계 분기가 달력과 여섯 주쯤 어긋나면 두 분기가 같은 달력 분기에 떨어진다.
    코스트코가 그렇다 — 11월~2월 분기와 2월~5월 분기가 둘 다 1Q26 이 됐다.
    SEC 프레임으로도 안 풀린다. SEC 는 잘 맞아떨어지는 분기에만 프레임을
    매기므로 코스트코는 넷 중 셋만 프레임이 있고, 그 셋만으로는 나머지 하나가
    갈 자리가 없다.

    자료는 종료일 순으로 정렬돼 있으니 **분기 이름도 반드시 뒤로 갈수록 커야
    한다.** 앞엣것보다 작거나 같으면 바로 다음 분기로 민다. 중간이 비어 있는
    것(수집이 덜 된 구간)은 그대로 둔다 — 없는 분기를 지어내지 않는다.
    """
    out, prev = [], None
    for lab in labels:
        i = per_index(lab)
        if i is None:
            out.append(lab)
            continue
        if prev is not None and i <= prev:
            i = prev + 1
        out.append(per_name(i, kind))
        prev = i
    return out


def pack_fin(rec):
    """화면에 실을 것만 골라 담는다. 점은 [라벨, 매출, 영업이익].

    라벨은 받아둔 값을 쓰지 않고 여기서 다시 매긴다 — 종료일만 있으면 되므로
    표기 규칙을 고칠 때 4천 종목을 다시 받지 않아도 된다.
    """
    out = {k: rec[k] for k in ("freq", "eps", "cur", "src") if rec.get(k)}
    if rec.get("freq") in ("Q", "H"):
        pts = rec.get("points") or []
        # **반기만 내는 회사는 반기답게 적는다.** 홍콩에 특히 많다. 점 사이가
        # 반년쯤이면 반기 보고다 — 그걸 2Q25·4Q25 로 적으면 사이가 빈 것처럼
        # 보여 헷갈린다. 화면 쪽은 이미 freq 가 'H' 면 「반기」로 쓰고 전년
        # 대비도 두 기간 전과 견준다.
        kind = "H" if half_yearly(pts) else "Q"
        out["freq"] = kind
        labs = unstack([(h_label(p["end"], p.get("mid", "")) if kind == "H"
                         else q_label(p["end"], p.get("mid", ""), p.get("q", "")))
                        if p.get("end") else p["label"] for p in pts], kind)
        out["points"] = [[lab, p["rev"], p.get("opi")]
                         for lab, p in zip(labs, pts)]
        # 가장 최근 분기의 **종료일**. 점마다 날짜를 실으면 파일이 1MB 늘어나므로
        # 종목당 한 칸만 담는다. 화면은 이걸로 '방금 발표한 분기를 우리가 갖고
        # 있나'를 가린다 — ✓ 를 진하게 달지 말지가 여기서 갈린다.
        last = [p["end"] for p in pts if p.get("end")]
        if last:
            out["last"] = max(last)
    return out


def half_yearly(pts):
    """점 사이가 반년쯤인가. 가운뎃값으로 본다 — 한두 군데 비어도 흔들리지 않게.

    섞여 있는 회사가 있다(분기로 갈아탄 곳). 그럴 때는 분기로 둔다 — 반기로
    적으면 최근에 낸 분기 둘이 한 칸에 겹친다.
    """
    ends = sorted(p["end"] for p in pts if p.get("end"))
    if len(ends) < 3:
        return False
    gaps = sorted((date.fromisoformat(b) - date.fromisoformat(a)).days
                  for a, b in zip(ends, ends[1:]))
    return 150 <= gaps[len(gaps) // 2] <= 220


def load_fin():
    """따로 받아둔 실적 수치(매출·영업이익 시계열, 발표 완료 여부).

    출처가 셋이다. 미국은 SEC(financials.json), 일본·홍콩은 stockanalysis
    (financials_intl.json), 그리고 일본은 **발표 당일치를 TDnet**에서 따로
    받는다(financials_jp.json). 열쇠는 `시장:코드` 로 맞춘다 — 일본 8035 와
    홍콩 08035 는 다른 회사이므로 코드만으로는 가를 수 없다.
    """
    out = {}
    for name, prefix in (("financials.json", "us:"),
                         ("financials_intl.json", ""),
                         ("financials_jp.json", "")):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError) as e:
            print(f"  ! {name} 읽기 실패: {e}")
            continue
        for k, v in got.items():
            # 예전 financials.json 은 열쇠가 'AAPL' 이었다. 'us:' 를 붙여 옮긴다.
            key = k if ":" in k else prefix + k
            out[key] = merge_fin(out.get(key), v)
    return out


def last_end(rec):
    pts = (rec or {}).get("points") or []
    return pts[-1].get("end", "") if pts else ""


def merge_fin(a, b):
    """한 종목에 두 소스가 있으면 합친다.

    SEC 는 1Q19 까지 깊지만 **실적 발표가 아니라 10-Q 가 올라와야** 값이 생긴다.
    그 사이가 며칠에서 몇 주다 — 루멘텀은 8/11 에 6월 분기를 발표했는데 SEC 쪽은
    3월 분기에서 멈춰 있었다. stockanalysis 는 발표 당일 반영되지만 20분기뿐이다.

    그래서 갈아치우지 않고 **깊은 쪽을 뼈대로 삼고 빠진 최근 분기를 메운다.**
    겹치는 분기는 공식 자료(SEC)를 남긴다. 통화가 다르면 섞지 않는다 — 그건
    같은 회사의 다른 보고 기준이라 한 막대그래프에 올리면 안 된다.
    """
    if not a:
        return b
    if not b:
        return a
    if a.get("freq") != b.get("freq") or (a.get("cur") or "") != (b.get("cur") or ""):
        # 섞지 않는다 — 통화나 주기가 다르면 같은 막대그래프에 못 올린다.
        # 고를 때는 **점이 많은 쪽**이 먼저다. 최신만 보면 방금 받은 한 분기짜리가
        # 스무 분기짜리를 밀어낸다.
        ka = (len(a.get("points") or []), last_end(a))
        kb = (len(b.get("points") or []), last_end(b))
        return a if ka >= kb else b

    # 뼈대는 **점이 많은 쪽**, 겹치는 분기는 **공식 자료 쪽**을 남긴다.
    #   sec   미국 공식 재무제표
    #   tdnet 일본 공식 결산단신 — 발표 당일에 나온다
    #   sa    stockanalysis — 20분기로 깊지만 일본은 며칠 늦는다
    # 예전에는 'sec 이면 뼈대' 하나로만 갈랐는데, 일본은 sec 이 없어서 그 규칙이
    # 아무 일도 안 했다. 그러면 TDnet 이 방금 받아온 새 분기가 stockanalysis 의
    # 헌 줄에 덮여 사라진다.
    rank = {"sec": 3, "tdnet": 2, "mix": 1, "sa": 1, "yahoo": 0}
    pa, pb = rank.get(a.get("src"), 0), rank.get(b.get("src"), 0)
    base, extra = (a, b) if len(a.get("points") or []) >= len(b.get("points") or []) else (b, a)
    win = a if pa >= pb else b               # 겹치는 분기를 가져갈 쪽
    lose = b if win is a else a
    by_end = {p["end"]: p for p in lose.get("points") or [] if p.get("end")}
    by_end.update({p["end"]: p for p in win.get("points") or [] if p.get("end")})
    pts = [by_end[e] for e in sorted(by_end)]
    mixed = len(pts) > len(base.get("points") or [])
    out = dict(base)
    out["points"] = pts
    out["src"] = "mix" if mixed else base.get("src")
    if not out.get("eps") and extra.get("eps"):
        out["eps"] = extra["eps"]
    return out


FIN = load_fin()


# ── 기준이 바뀐 부문 정리 ────────────────────────────────────────────
# 회사는 사업부 구분을 통째로 갈아엎는다. HPE 가 Compute·Storage·Intelligent Edge
# 를 Cloud&AI·Networking 으로 바꿨고, ADM·엔테그리스·메디목스도 그랬다. 그런데
# 옛 이름과 새 이름이 한 표에 다 실려 오면 **막대에 열두 줄이 서고 그중 셋만
# 최근 값이 있다.** 나머지 아홉은 뒤가 잘린 유령이라 색만 차지한다.
# 재보니 부문 차트 2,327종목 중 **806종목(35%)**이 그랬다.
#
# 그래서 **지금 쓰는 기준만 남긴다.** 최근 두 분기에 값이 한 번도 없는 부문은
# 옛 기준이므로 뺀다. 옛 기준으로만 채워지던 앞쪽 분기도 같이 잘라낸다 —
# 안 자르면 새 기준 셋이 전부 빈 막대가 앞에 늘어선다.
SEG_TAIL = 2         # 이 분기 수 안에 값이 없으면 '지금 안 쓰는 부문'
SEG_MIN_FILL = 0.5   # 살아 있는 부문의 절반도 안 차는 앞 분기는 잘라낸다


def _last_at(pts, val, i):
    """그 부문이 마지막으로 값을 가진 칸의 자리. 없으면 -1."""
    for j in range(len(pts) - 1, -1, -1):
        if val(pts[j], i) is not None:
            return j
    return -1


def current_basis(rec):
    """지금 쓰는 부문 기준만 남긴다. 남길 게 없으면 None."""
    names, pts = rec.get("names") or [], rec.get("pts") or []
    if len(names) < 2 or not pts:
        return rec
    val = lambda r, i: r[i + 1] if i + 1 < len(r) else None

    # 0) **값이 똑같은 두 줄은 같은 부문이다.** 회사가 부문 이름을 바꾸는 동안
    #    한동안 옛 이름과 새 이름이 둘 다 실린다. 아이하트미디어가 그랬다 —
    #    1Q23~3Q23 의 여섯 줄 중 셋이 나머지 셋과 **원 단위까지 같은 값**이라
    #    그 세 칸의 막대가 정확히 두 배였다. 겹치는 분기의 값이 하나도 안 어긋나면
    #    같은 것으로 보고 합친다(값이 다르면 손대지 않는다 — 진짜 다른 부문이다).
    same = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            both = [(val(r, i), val(r, j)) for r in pts
                    if val(r, i) is not None and val(r, j) is not None]
            if both and all(a_ == b_ for a_, b_ in both):
                same.setdefault(i, []).append(j)
    if same:
        drop = set()
        for i, js in same.items():
            if i in drop:
                continue
            for j in js:
                if j in drop:
                    continue
                # **최근까지 값이 있는 쪽 이름을 남긴다.** 이름을 바꿨으면 새
                # 이름이 뒤까지 이어지므로, 그쪽이 지금 회사가 쓰는 표기다.
                hi, lo = ((i, j) if _last_at(pts, val, i) >= _last_at(pts, val, j)
                          else (j, i))
                merged = [val(r, hi) if val(r, hi) is not None else val(r, lo) for r in pts]
                pts = [[r[0]] + [(merged[k] if x == hi else val(r, x))
                                 for x in range(len(names))]
                       for k, r in enumerate(pts)]
                drop.add(lo)
        if drop:
            live0 = [i for i in range(len(names)) if i not in drop]
            names = [names[i] for i in live0]
            pts = [[r[0]] + [val(r, i) for i in live0] for r in pts]
            rec = {**rec, "names": names, "pts": pts}

    # 1) 상계·조정 줄. 값이 대부분 음수인 것은 부문이 아니다(쌓으면 막대가 파인다).
    #    이름으로 거르지 않는 이유는 소스마다 표기가 달라서다 — 부호가 확실하다.
    #    이건 기준이 바뀐 것이 아니므로 **앞 분기를 자르는 근거로 쓰지 않는다.**
    keep = []
    for i in range(len(names)):
        vs = [v for v in (val(r, i) for r in pts) if v is not None]
        if vs and sum(1 for v in vs if v < 0) * 2 >= len(vs):
            continue
        keep.append(i)

    # 2) 최근 두 분기에 값이 없는 부문 = 옛 기준
    tail = pts[-SEG_TAIL:]
    live = [i for i in keep if any(val(r, i) is not None for r in tail)]
    if len(live) < 2:
        # 최근 분기가 통째로 빈 기록(꼬리를 아직 못 받은 종목)에는 손대지 않는다.
        # 멀쩡한 차트를 지우는 쪽이 더 나쁘다.
        live, dead = keep, []
    else:
        dead = [i for i in keep if i not in set(live)]
    if len(live) < 2:
        return None

    # **개편과 수집 구멍을 가른다.** 옛 부문이 끊긴 것만 보면 둘이 똑같이 생겼다.
    #   개편  — 옛 이름이 죽고 **새 이름이 그 자리에 선다**
    #   구멍  — 옛 이름이 끊기기만 하고 새로 생긴 것이 없다(우리가 못 받았거나
    #           그 사업을 팔았다)
    # 구멍인데 개편으로 보고 자르면 멀쩡한 5년치가 두 칸이 된다. 셈프라가
    # 그랬다 — 제출 서류에서 부문 둘만 잡혀 나머지 둘이 죽은 것처럼 보였다.
    # 그래서 **새 이름이 실제로 등장할 때만** 옛 부문을 빼고 앞을 자른다.
    if dead:
        end = max((j for i in dead for j in range(len(pts) - 1, -1, -1)
                   if val(pts[j], i) is not None), default=-1)
        born = [i for i in live
                if all(val(pts[j], i) is None for j in range(end + 1))]
        if not born:
            # 개편이 아니다. 끊긴 부문도 그대로 둔다 — 뒤가 빈 막대는 '그 사업이
            # 거기서 끝났다'는 사실이고, 지우면 그 사실이 없어진다.
            dead, live = [], keep
    if not dead and len(live) == len(names):
        return rec

    # 2b) **이름만 바꾼 부문 하나는 이어 붙인다.** 사라진 부문이 하나, 새로 생긴
    #     부문이 하나이고 둘의 기간이 겹치지 않으면 같은 사업을 이름만 바꾼 것이다
    #     (캐터필러의 'Energy and Transportation' -> 'Power & Energy'). 그걸 기준
    #     변경으로 보고 잘라 버리면 멀쩡한 5년치가 두 칸으로 준다.
    #     **둘 다 하나일 때만** 한다 — 여럿이 한꺼번에 갈리면 진짜 개편이라
    #     어느 것이 어느 것의 후신인지 알 길이 없다(HPE 가 그랬다).
    def span(i):
        js = [j for j in range(len(pts)) if val(pts[j], i) is not None]
        return (js[0], js[-1]) if js else None

    if len(dead) == 1 and len(live) >= 2:
        old = dead[0]
        so = span(old)
        fresh = [i for i in live if (sp := span(i)) and so and sp[0] > so[1]]
        if so and len(fresh) == 1:
            new = fresh[0]
            merged = []
            for r in pts:
                a_, b_ = val(r, old), val(r, new)
                merged.append(b_ if b_ is not None else a_)
            names = list(names)
            pts = [[r[0]] + [(merged[j] if i == new else val(r, i))
                             for i in range(len(names))]
                   for j, r in enumerate(pts)]
            dead = []                       # 이어 붙였으니 자를 이유가 없다

    # 3) **기준이 바뀐 지점에서 자른다.** 옛 부문이 마지막으로 값을 가진 분기까지가
    #    옛 기준이다. 그 앞을 남겨 두면 새 부문 자리가 빈 채로 막대가 서고, 기준이
    #    바뀌는 칸에서 높이가 껑충 뛴다(캐터필러가 그랬다 — 'Energy and
    #    Transportation' 이 'Power & Energy' 로 바뀌면서 마지막 두 칸만 키가 컸다).
    #
    #    부문이 **없어지지 않고 새로 생기기만** 했으면 기준이 바뀐 게 아니다.
    #    그때는 자르지 않는다 — 사업 하나가 늘어난 것뿐이라 옛 분기도 다 맞다.
    start = 0
    for i in dead:
        for j in range(len(pts) - 1, -1, -1):
            if val(pts[j], i) is not None:
                start = max(start, j + 1)
                break
    rows = [[r[0]] + [val(r, i) for i in live] for r in pts[start:]]
    # 새 기준으로 두 분기도 안 되면 그릴 것이 없다. 옛 기준을 섞어 그리느니
    # 이번 분기는 비워 둔다 — 다음 분기가 쌓이면 저절로 살아난다.
    if len(rows) < 2:
        return None
    return {**rec, "names": [names[i] for i in live], "pts": rows}


SEG_HK_SNAP = 20     # 스냅샷 날짜와 총매출 종료일이 이만큼 안이면 같은 기간으로 본다


def hk_seg_records():
    """동화순 비중 스냅샷 -> 부문 금액 기록. {hk:코드: {axis, names, pts}}

    동화순(scrape_seg_hk.py)은 보고기간 **누계 기준의 부문 비중(%)**만 준다.
    절대 금액은 우리가 이미 가진 총매출에 곱해 만든다 — 회사가 공시한 두 값의
    곱이지 지어낸 값이 아니다.

    누계를 기간값으로 되돌리는 규칙:
      * 스냅샷 날짜를 총매출 점(FIN)의 종료일에 붙인다(20일 안).
      * 같은 회계연도(총매출 라벨의 연도가 같은 것)의 누계 총매출을 만든다.
      * 그 회계연도 **첫 기간**의 스냅샷이면 비중 × 누계가 곧 그 기간 값이다.
      * 아니면 앞 스냅샷과의 차 — 연간 누계×연간 비중 − 상반기 누계×상반기 비중.
        **앞 기간 스냅샷이 없으면 그 기간은 버린다.** 두 기간에 걸친 값을 한
        기간 칸에 앉히면 막대가 거짓말을 한다.
      * 차가 음수로 크게 나오는 부문(기중 개편)은 그 칸을 비운다.
    """
    p = HERE / "data" / "segments_hk.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError) as e:
        print(f"  ! segments_hk.json 읽기 실패: {e}")
        return {}

    out = {}
    for code, rec in raw.items():
        snaps = rec.get("snaps") or {}
        if len(snaps) < 2:
            continue
        fin = FIN.get("hk:" + code)
        pts_fin = (fin or {}).get("points") or []
        if not pts_fin:
            continue

        # 총매출 점: 종료일 -> (라벨, 값, 회계연도). 라벨 끝 두 자리가 연도다
        # ('1H25'·'2H25'·'1Q26'). 연도를 못 읽는 라벨은 안 쓴다.
        fpts = []
        for q in pts_fin:
            lab, end, rev = q.get("label", ""), q.get("end", ""), q.get("rev")
            m = re.search(r"(\d{2})$", lab)
            if not (m and end and rev):
                continue
            fpts.append((end, lab, float(rev), m.group(1)))
        fpts.sort()
        if not fpts:
            continue

        def near(snap_day):
            sd = date.fromisoformat(snap_day)
            best = None
            for i, (end, _lab, _rev, _fy) in enumerate(fpts):
                gap = abs((date.fromisoformat(end) - sd).days)
                if gap <= SEG_HK_SNAP and (best is None or gap < best[0]):
                    best = (gap, i)
            return best[1] if best else None

        # 스냅샷마다: 그 기간까지의 회계연도 누계 총매출 × 비중 = 누계 부문값
        cums = {}                            # fin 색인 -> {부문: 누계값}
        for day, mix in snaps.items():
            i = near(day)
            if i is None:
                continue
            end, _lab, _rev, fy = fpts[i]
            cum_total = sum(r for e, _l, r, f in fpts[:i + 1] if f == fy)
            cums[i] = {n: pct / 100.0 * cum_total for n, pct in mix}

        rows = {}                            # fin 색인 -> {부문: 기간값}
        for i, seg_cum in sorted(cums.items()):
            fy = fpts[i][3]
            first = min(j for j, fp in enumerate(fpts) if fp[3] == fy)
            if i == first:
                rows[i] = dict(seg_cum)
                continue
            if (i - 1) not in cums or fpts[i - 1][3] != fy:
                continue                     # 앞 기간 스냅샷이 없다. 지어내지 않는다.
            prev = cums[i - 1]
            vals = {}
            for n, v in seg_cum.items():
                d = v - prev.get(n, 0.0)
                vals[n] = d if d > 0 else None
            rows[i] = vals
        if len(rows) < 2:
            continue

        names = []
        for i in sorted(rows):
            for n in rows[i]:
                if n not in names:
                    names.append(n)
        last = rows[max(rows)]
        names.sort(key=lambda n: -(last.get(n) or 0))
        pts = [[fpts[i][0]] + [rows[i].get(n) for n in names] for i in sorted(rows)]
        out["hk:" + code] = {"axis": "사업부문", "names": names, "pts": pts}
    return out


def load_seg():
    """사업부별 매출. 두 곳에서 온다. 열쇠는 미국 코드라 'us:' 를 붙여 맞춘다.

    | 파일 | 소스 | 성격 |
    |---|---|---|
    | `segments_sec.json` | SEC 분기 벌크 | zip 하나로 **미국 전 종목**. 최근 한두 분기가 빈다 |
    | `segments_jp.json` | TDnet 결산단신 첨부 | 일본. 실적 수치를 받는 그 zip 에서 같이 뽑는다 |
    | `segments.json` | stockanalysis | 미국. 종목마다 요청 한 번. 이름이 곱고 최근 분기까지 온다 |

    미국이 둘 다 있으면 **stockanalysis 를 쓴다.** 이름이 사람이 쓴 것이고
    ('Intelligent Cloud' 대 'IntelligentCloud'), 벌크가 아직 안 실은 최근
    분기까지 들어 있다. 벌크는 나머지 수천 종목을 메운다 — 그쪽이 없던 시절에는
    1,350종목을 두드려 223종목밖에 못 얻었다.
    """
    out = {}
    for name in ("segments_sec.json", "segments_jp.json",
                 "segments.json"):                        # 뒤엣것이 이긴다
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError) as e:
            print(f"  ! {name} 읽기 실패: {e}")
            continue
        n = 0
        for k, v in got.items():
            if not v.get("names"):
                continue
            out[k if ":" in k else "us:" + k] = v
            n += 1
        print(f"  {name}: {n:,}종목")

    # 벌크는 **접수된 분기 기준**이라 구조적으로 두 분기쯤 늦는다. 실제로 재보니
    # 부문 자료가 있는 1,600종목 중 61%가 두 분기 뒤졌다 — 총매출은 2Q26 까지
    # 그려지는데 부문 막대는 4Q25 에서 끊겼다. `scrape_seg_edgar.py` 가 그 구간을
    # 제출 서류에서 직접 받아 오므로, 여기서 **뒤쪽만 이어 붙인다.**
    # 통째로 갈아치우지 않는 이유는 서류 몇 장으로는 몇 년치가 안 나오기 때문이다.
    fresh = HERE / "data" / "segments_edgar.json"
    if fresh.exists():
        try:
            got = json.loads(fresh.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError) as e:
            print(f"  ! segments_edgar.json 읽기 실패: {e}")
            got = {}
        added = new = 0
        for k, v in got.items():
            key = k if ":" in k else "us:" + k
            base = out.get(key)
            if base is None:
                if v.get("names"):
                    out[key] = v
                    new += 1
                continue
            n_add = splice(base, v)
            added += bool(n_add)
        print(f"  segments_edgar.json: 최근 분기를 이어 붙인 종목 {added:,}"
              f" · 새로 생긴 종목 {new:,}")

    # 홍콩 — 동화순 비중 × 우리 총매출. 다른 소스가 홍콩을 아예 못 주므로
    # 겹칠 일은 없지만, 있으면 그쪽(직접 금액)이 이긴다.
    hk = hk_seg_records()
    added_hk = 0
    for k, v in hk.items():
        if k not in out:
            out[k] = v
            added_hk += 1
    if hk:
        print(f"  segments_hk.json: 비중×총매출로 만든 홍콩 종목 {added_hk:,}")

    # 기준이 바뀐 회사는 **지금 쓰는 기준만** 남긴다. 이어 붙인 뒤에 걸어야
    # '최근 두 분기'가 실제 최근이 된다.
    cut = gone = 0
    for k in list(out):
        got = current_basis(out[k])
        if got is None:
            del out[k]; gone += 1
        elif got is not out[k]:
            out[k] = got; cut += 1
    if cut or gone:
        print(f"  기준이 바뀐 종목 {cut:,}: 옛 부문을 뺐다"
              + (f" · 남는 부문이 없어 뺀 종목 {gone:,}" if gone else ""))
    return out


SEG_NORM = re.compile(r"[^a-z0-9]|and")


def seg_key(name: str) -> str:
    """부문 이름을 맞춰 보는 열쇠. 대소문자·띄어쓰기·'and' 를 지운다.
    수집기 쪽 `norm_name` 과 같은 규칙이다 — 한쪽만 고치면 안 붙는다."""
    return SEG_NORM.sub("", name.lower())


def splice(base, fresh):
    """헌 기록 **뒤에** 새 기록의 최근 분기만 잇는다. 붙인 분기 수를 돌려준다.

    통째로 바꾸지 않는 이유가 둘이다.
      * 제출 서류 몇 장으로는 대여섯 분기밖에 안 나온다. 그걸로 갈아치우면
        3년치 막대가 반토막 난다.
      * 부문 이름이 소스마다 조금씩 다르다('Intelligent Cloud' 대
        'IntelligentCloud'). 열쇠로 맞춰 보고, **맞는 게 적으면 아예 안 붙인다** —
        다른 회사의 다른 축을 이어 붙이는 것이 제일 나쁘다.
    """
    if not base.get("names") or not fresh.get("names") or not fresh.get("pts"):
        return 0
    if base.get("axis") != fresh.get("axis"):
        return 0
    fi = {seg_key(n): i for i, n in enumerate(fresh["names"])}
    hit = [fi.get(seg_key(n)) for n in base["names"]]
    # **잣대는 헌 기록의 이름 전부가 아니라 '지금 쓰는 것'이다.** 벌크 기록에는
    # 옛 이름이 같이 실려 있는 일이 흔해서(아이하트미디어는 같은 부문 셋이 옛
    # 이름으로도 들어 있어 여섯 줄이었다), 전부를 분모로 삼으면 멀쩡한 짝이
    # 5할로 떨어져 거절당한다. 마지막 분기에 값이 있는 이름만 센다.
    last_row = base["pts"][-1]
    liveb = [i for i in range(len(base["names"]))
             if i + 1 < len(last_row) and last_row[i + 1] is not None] or \
            list(range(len(base["names"])))
    cov = sum(hit[i] is not None for i in liveb)
    bk = {seg_key(n) for n in base["names"]}
    newborn = any(seg_key(n) not in bk for n in fresh["names"])
    # **반쪽짜리 꼬리는 붙이지 않는다.** 새 자료가 헌 기록의 부문 일부만 담고
    # 있으면, 이어 붙인 마지막 칸에서 나머지가 사라져 **막대가 뚝 떨어진다** —
    # 매출이 준 것처럼 보인다. 셈프라가 그랬다(넷 중 둘만 잡혔다).
    # 다만 **이름을 바꾼 경우**는 예외다. 그때는 덜 겹치는 게 당연하고
    # (콜게이트 'Pet Nutrition' -> 'Hills Pet Nutrition'), 뒤에서 current_basis 가
    # 정리한다. 하나도 안 겹치면 그건 이름 변경이 아니라 **다른 축**이다.
    if not (cov == len(liveb) or (cov and newborn)):
        return 0
    last = max(p[0] for p in base["pts"])
    add = []
    for p in sorted(fresh["pts"]):
        if p[0] <= last:
            continue
        # 열은 늘 **헌 기록의 이름 차례**를 따른다. 새 기록에 없는 부문은 빈칸이다.
        add.append([p[0]] + [(p[i + 1] if i is not None and i + 1 < len(p) else None)
                             for i in hit])
    if not add:
        return 0
    base["pts"] = base["pts"] + add
    return len(add)


SEG = load_seg()

# 조정·소거·전사공통 줄은 부문이 아니다. 이름이 일본어 원문 그대로라 어휘로 거른다.
# (분기 쪽은 XBRL 멤버 이름이라 영어 어휘로 거른다 — 소스가 달라 어휘도 다르다.)
SEG_HIST_SKIP = re.compile(r"調整|消去|全社|セグメント間|内部取引|合計")


def load_seg_hist():
    """일본 부문의 **연간** 이력 (EDINET DB — 有価証券報告書의 보고 세그먼트).

    TDnet 결산단신은 한 달치만 남아 옛 분기가 그 길에 없고, EDINET 공식 API 는
    키 포털이 막혀 있다. 그래서 연간 이력을 edinetdb.jp 에서 받는다(2014년치부터).
    **분기 차트에 섞지 않는다** — 연간 막대 옆에 반기 막대가 서면 높이가
    거짓말이 된다. 화면은 분기 차트 아래에 연간 차트를 따로 단다.

    걸러내기 규칙이 수집기가 아니라 여기 있는 까닭은 분기 부문의 원자료와
    같다 — 수집 쪽에 두면 규칙을 고칠 때마다 100건/일 예산으로 다시 받아야 한다.
    기준이 바뀐 회사 정리는 분기와 **같은 함수**(current_basis)를 쓴다.
    """
    p = HERE / "data" / "segments_jp_hist.json"
    if not p.exists():
        return {}
    try:
        got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError) as e:
        print(f"  ! segments_jp_hist.json 읽기 실패: {e}")
        return {}
    out = {}
    for key, v in got.items():
        by_fy = {}
        for fy, nm, rev, _opi in v.get("rows", []):
            # 음수·0 매출은 부문이 아니라 상계 줄이다. 쌓는 막대에 못 올린다.
            if SEG_HIST_SKIP.search(nm) or not rev or rev <= 0:
                continue
            by_fy.setdefault(fy, {})[nm] = int(rev)
        fys = sorted(by_fy)
        if len(fys) < 2:
            continue
        last = by_fy[fys[-1]]
        names = sorted({n for row in by_fy.values() for n in row},
                       key=lambda n: (-(last.get(n) or 0),
                                      -sum(row.get(n) or 0 for row in by_fy.values())))
        pts = [[f"FY{fy}"] + [by_fy[fy].get(n) for n in names]
               for fy in fys[-12:]]
        rec = current_basis({"ax": "연간 부문", "names": names, "pts": pts})
        if not rec or len(rec.get("names") or []) < 2:
            continue
        if len(rec["names"]) > 10:              # 범례가 화면을 가로지르면 못 읽는다
            keep = rec["names"][:10]
            idx = [rec["names"].index(n) for n in keep]
            rec = {"ax": rec.get("ax", "연간 부문"), "names": keep,
                   "pts": [[r[0]] + [r[i + 1] for i in idx] for r in rec["pts"]]}
        out[key] = rec
    if out:
        print(f"  segments_jp_hist.json: 연간 부문 이력 {len(out):,}종목")
    return out


SEG_HIST = load_seg_hist()


def load_desc():
    """받아둔 사업 설명 원문. 한국어가 없는 종목에만 쓴다.

    회사 설명이 아닌 것이 담겨 있으면 여기서 거른다 — stockanalysis SEO 껍데기
    ("Company profile for …"), 닛케이 페이지 소개문(【日本経済新聞】…), HTML
    속성 부스러기('…">')가 실제로 담겼었다. 수집기(scrape_desc.py 의 junk())가
    다시 받을 때까지는 빈칸이 낫다 — 페이지 광고문을 회사 설명이라고 싣는 것보다.
    """
    p = HERE / "data" / "desc.json"
    if not p.exists():
        return {}
    try:
        stocks = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError) as e:
        print(f"  ! desc.json 읽기 실패: {e}")
        return {}
    out, dropped = {}, 0
    for k, v in stocks.items():
        t = v.get("t") or ""
        if (t.startswith("Company profile for") or "日本経済新聞" in t
                or '">' in t[:120]):
            dropped += 1
            continue
        out[k] = v
    if dropped:
        print(f"  사업 설명 원문 중 껍데기 {dropped}건은 싣지 않는다")
    return out


DESC = load_desc()


def load_fcst():
    """회사가 공시한 통기 예상(가이던스). 일본 결산단신 요약의 예상란에서 온다
    (scrape_fin_jp.py 의 fcst) — 회사 자신이 공시한 수치지 우리의 추정이 아니다.
    직전 예상(prev)이 같이 있으면 화면이 상향/하향 폭을 계산해 적는다."""
    p = HERE / "data" / "financials_jp.json"
    if not p.exists():
        return {}
    try:
        stocks = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
    except (ValueError, OSError) as e:
        print(f"  ! financials_jp.json (fcst) 읽기 실패: {e}")
        return {}
    out = {}
    for code, rec in stocks.items():
        f = rec.get("fcst")
        if f and (f.get("rev") or f.get("opi")):
            out["jp:" + code] = f
    return out


# 공시 원문에서 옮긴 한국어 실적 코멘트 — {키: {"date": 발표일, "ko": 몇 줄,
# "src": 출처}}. 화면은 **그 발표가 그 종목의 가장 최근 발표일 때만** 낸다
# (briefBlock 이 발표일로 가른다). 원문 수집은 scrape_pr_us.py(미국 8-K
# 보도자료) 등이 하고, 한국어는 descriptions.py 처럼 사람이(세션에서 일괄로) 쓴다.
try:
    from briefs import BRIEFS
except ImportError:
    BRIEFS = {}


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else 0.0


SEG_SNAP = 20        # 부문 종료일과 총매출 종료일이 이만큼 안이면 같은 분기로 본다


def seg_align(pts, fin_rec):
    """부문 점마다 (분기 이름, 그 분기 총매출) 을 붙인다.

    **종료일이 소스마다 며칠씩 어긋난다.** SEC 벌크는 ddate 를 월말로 반올림해
    싣는다 — 엔비디아의 1월 28일 결산이 1월 31일로 온다. 그대로 대보면 총매출
    쪽 점과 하나도 안 맞아서 검산도 못 하고 이름도 따로 매기게 된다.

    스무 날 안에 있는 총매출 점을 같은 분기로 보고, **그쪽이 이미 매긴 이름을
    그대로 쓴다.** 그 이름은 SEC 프레임과 unstack 까지 거친 것이라 여기서 다시
    어림하는 것보다 옳다. 맞는 점이 없으면 종료일에서 어림해 매긴다.
    """
    fp = []
    for p in (fin_rec or {}).get("points") or []:
        if not p.get("end"):
            continue
        try:
            fp.append((date.fromisoformat(p["end"]), p))
        except ValueError:
            pass
    fp.sort()

    out = []
    for r in pts:
        try:
            d = date.fromisoformat(r[0])
        except (ValueError, TypeError):
            out.append((str(r[0]), None))
            continue
        best = None
        for fd, p in fp:
            gap = abs((fd - d).days)
            if gap <= SEG_SNAP and (best is None or gap < best[0]):
                best = (gap, p)
        if best:
            p = best[1]
            out.append((p.get("label") or q_label(r[0]), p.get("rev")))
        else:
            out.append((q_label(r[0]), None))
    return out


def seg_fit(rec, fin_rec):
    """부문 합을 총매출과 대보고, **두 배로 부푼 것만** 걸러낸다.

    처음에는 안 맞는 부문을 하나씩 빼서 총매출에 맞추게 했다. 그게 더 나빴다.
    실제 자료를 대보니 어긋나는 이유가 넷인데 셋은 부문 잘못이 아니었다.

    - 웨이스트매니지먼트: 부문을 **상계 전 총액**으로 낸다. 늘 8% 넘친다. 정상이다.
    - 존슨컨트롤스: 부문은 멀쩡한데 **총매출 쪽이 틀렸다**(6,442 -> 1,004 -> 447).
    - 캐터필러: 부문 이름이 깔끔히 바뀐 정상 케이스인데 총매출 오류에 휘말렸다.
    - 버텍스: 같은 이름이 표에 두 줄 있어 정확히 두 배가 됐다. 이건 진짜 잘못인데
      **수집기에서 고쳤다**(`parse()` 가 이름 중복을 걸러낸다).

    맞추려 든 결과 웨이스트매니지먼트는 가장 큰 부문(Collection)이 빠져 37%만
    남았다. 매출 대부분이 사라진 그림이 어긋난 그림보다 나을 리 없다.

    그래서 지금은 **부문을 지우지 않는다.** 총매출을 25% 넘게 웃도는 분기가
    과반이면 — 같은 줄이 두 번 실린 신호다 — 그 종목만 통째로 싣지 않는다.
    총매출 자체가 못 미더울 수 있으므로 어림한 어긋남으로는 판단하지 않는다.

    돌려주는 값: (이름, 점, 총매출 대비 비율) — 못 쓰겠으면 None.
    비율은 분기마다 고르게 나올 때만 준다. 들쭉날쭉하면 대볼 총매출이 못 미더운
    것이라 None 이다.
    """
    names = list(rec.get("names") or [])
    pts = [r for r in rec.get("pts") or [] if r and r[0]]
    if len(names) < 2 or len(pts) < 2:
        return None

    aligned = seg_align(pts, fin_rec)
    shared = [(r, rev) for r, (_lab, rev) in zip(pts, aligned) if rev]
    if len(shared) < 4:
        return names, pts, aligned, None       # 대볼 총매출이 없다. 그대로 싣는다.

    rs = sorted(sum(v or 0 for v in r[1:]) / rev for r, rev in shared)
    # 한 분기가 튀는 것으로 판을 뒤집지 않도록 위아래를 조금 깎고 본다.
    lo, hi = rs[len(rs) // 10], rs[-1 - len(rs) // 10]
    med = _median(rs)

    # **높이보다 고르기가 갈라준다.** 같은 줄이 두 번 실렸으면 비율이 분기마다
    # 비슷하게 높다(버텍스 1.72~2.00, 메르카도리브레 1.00~1.46). 반대로 총매출
    # 쪽이 망가진 경우는 널을 뛴다(존슨컨트롤스 1.00~42.56) — 그건 부문 잘못이
    # 아니므로 부문 차트까지 뺏을 이유가 없다.
    if med > 1.25 and hi - lo < 1.0:
        return None                            # 부풀었다. 싣지 않는다.

    # "총매출의 몇 %" 는 두 수치가 서로 아귀가 맞을 때만 적는다. 흔들리는데
    # 적으면 틀린 근거로 적는 셈이다.
    return names, pts, aligned, (med if hi - lo < 0.10 else None)


SEG_KEEP = 20        # 화면에 그리는 것이 스물두 분기라 그만큼만 싣는다


def pack_seg(rec, fin_rec):
    """[분기 라벨, 부문1, 부문2, …]. 라벨은 총매출 쪽 것을 빌려 온다."""
    fit = seg_fit(rec, fin_rec)
    if not fit:
        return None
    names, pts, aligned, med = fit
    # 값은 정수로 눕힌다. 부문 수치는 원 단위까지 의미가 없는데 '10863000000.0'
    # 처럼 실으면 종목마다 몇백 바이트씩 늘어난다.
    rows = [[lab] + [int(v) if v else None for v in r[1:]]
            for r, (lab, _rev) in zip(pts, aligned)][-SEG_KEEP:]
    out = {"names": names, "pts": rows}
    # 어느 축으로 쪼갠 것인가. 사업부문이 없어 제품이나 지역으로 내려간 회사가
    # 있다(애플의 영업부문은 지역이다). 화면 제목이 이걸로 갈린다.
    if rec.get("axis") and rec["axis"] != "사업부문":
        out["ax"] = rec["axis"]
    # 총매출의 몇 %를 덮는지. 부문이 전부를 설명하지 않는 회사가 흔하다
    # (본사 몫·기타). 막대 높이를 총매출로 오해하지 않도록 적어둔다.
    if med is not None and med < 0.95:
        out["cov"] = round(med * 100)
    return out


def pack_jp(r):
    """일본만 기계 변환을 거친다. 원본이 일본어라 그대로는 훑어보기가 안 된다."""
    ko, lvl = to_korean(r["name"], companies.NOTABLE.get(r["code"], ("",))[0])
    return [r["date"], r["code"], ko,
            r.get("fy", "").replace("月期", "월 결산"),
            KIND_MAP.get(r.get("kind", ""), r.get("kind", "")),
            SECTOR_KO.get(r.get("sector", ""), r.get("sector", "")),
            MARKET_KO.get(r.get("market", ""), r.get("market", "")),
            r["name"], lvl, "jp", "", CAPS.get("jp:" + r["code"], 0)
            # TDnet 줄에는 실제 공시 시각이 있다. 닛케이 줄에는 없어 빈칸이다.
            ] + list(to_kst("jp", r["date"], r.get("time", ""), ""))


# 홍콩 결산기는 원본이 공시 문서 제목이라 통째로 영어 한 문장이다.
#   'ANNOUNCEMENT OF THE RESULTS FOR THE THREE MONTHS ENDED 31 MARCH 2026'
# 그대로 실으면 칸을 넘겨 문장 중간에서 잘린다. 어느 기간인지만 뽑아 적는다.
# 표현이 회사마다 제각각이라(31 MARCH 2026 / MARCH 31, 2026 / 31ST MARCH ...)
# 몇 갈래로 나눠 본다. 그래도 못 읽으면 **원문을 그대로 둔다** — 짐작해 넣지 않는다.
HK_MONTH = {m: i + 1 for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
     "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"])}
HK_SPAN = {"THREE": "분기", "SIX": "반기", "NINE": "누적 9개월", "TWELVE": "연간"}
HK_ORD = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4}
_MON = "|".join(HK_MONTH)
# '(기간) ENDED (날짜)' — 날짜는 '31 MARCH 2026' 과 'MARCH 31, 2026' 둘 다 온다.
HK_ENDED = re.compile(
    r"(?:(THREE|SIX|NINE|TWELVE)\s+MONTHS|(?:HALF-YEAR\s+)?(YEAR|PERIOD))\s+ENDED\s+"
    r"(?:\d{1,2}(?:ST|ND|RD|TH)?\s+(" + _MON + r")|(" + _MON + r")\s+\d{1,2})"
    r"[,\s]+(\d{4})")
HK_QUARTER = re.compile(r"(FIRST|SECOND|THIRD|FOURTH)\s+QUARTER")
HK_KIND = re.compile(r"(INTERIM|ANNUAL|FINAL)\s+RESULTS")
HK_YEAR = re.compile(r"(?:19|20)(\d{2})")


def hk_period(title):
    """공시 제목 -> '2026년 3월 분기' 처럼. 못 읽으면 빈 문자열."""
    t = (title or "").upper()

    m = HK_ENDED.search(t)
    if m:
        span = HK_SPAN.get(m.group(1) or "") or ("연간" if m.group(2) == "YEAR" else "반기")
        mon = HK_MONTH.get(m.group(3) or m.group(4) or "")
        if mon:
            return f"{m.group(5)}년 {mon}월 {span}"

    # 연도는 제목 어딘가에 있다. 여러 개면 뒤엣것이 결산 연도다(2025/2026 처럼).
    years = HK_YEAR.findall(t)
    year = "20" + years[-1] if years else ""

    m = HK_QUARTER.search(t)
    if m and year:
        return f"{year}년 {HK_ORD[m.group(1)]}분기"

    m = HK_KIND.search(t)
    if m and year:
        return f"{year}년 " + ("반기" if m.group(1) == "INTERIM" else "연간")
    if year and "INTERIM" in t:
        return f"{year}년 반기"
    if year and "ANNUAL" in t:
        return f"{year}년 연간"
    return ""


def pack_en(r, mkt):
    """미국·홍콩은 원본이 영문이라 그대로도 읽힌다. 사전에 있으면 한글명을 쓰고
    없으면 영문명을 그대로 둔다 — 억지 음차는 오히려 못 알아보게 만든다.
    지어낸 표기가 아니므로 '기계 변환'(등급 0) 점선은 붙지 않는다.

    다만 홍콩은 원본이 '영문명'이 아니라 거래소 약칭이다(`BABA-W`, `PSBC`,
    `CMOC`). 그대로 두면 아는 회사도 못 알아보므로 NAMES 로 이름만 바꾼다."""
    cur = DICTS[mkt].NOTABLE.get(r["code"])
    # NOTABLE 은 ★까지 붙는 목록이라 이름만 고치고 싶을 때 쓸 수가 없다.
    # 그래서 시장마다 '이름만 바꾸는' NAMES 를 따로 둔다(없는 시장도 있다).
    ko = cur[0] if cur else getattr(DICTS[mkt], "NAMES", {}).get(r["code"], "")
    name = r.get("name", "")
    # 미국의 time 은 장전/장후 구분, 홍콩의 time 은 실제 시각(HH:MM)이다.
    raw_time = r.get("time", "")
    timing = TIMING_KO.get(raw_time, "") if mkt == "us" else ""
    hhmm = raw_time if mkt == "hk" else ""
    # 업종은 원본에 없다. 미국은 나스닥 스크리너에서 따로 받아둔 것을 붙인다.
    sec = r.get("sector", "") or SECTORS.get(mkt + ":" + r["code"], "")
    fy = r.get("fy", "")
    if mkt == "hk":
        fy = hk_period(fy) or fy
    return [r["date"], r["code"], ko or name,
            fy, r.get("kind", ""),
            US_SECTOR_KO.get(sec, sec),
            MARKET_KO.get(r.get("market", ""), r.get("market", "")),
            name, 2, mkt, timing,
            # 미국은 나스닥이 시총을 같이 주고, 홍콩은 따로 받아둔 것을 붙인다.
            r.get("cap", 0) or CAPS.get(mkt + ":" + r["code"], 0)
            ] + list(to_kst(mkt, r["date"], hhmm, timing))


def build():
    data = {m: load(m) for m in MARKET_ORDER}
    have = [m for m in MARKET_ORDER if data[m]]
    if not have:
        raise SystemExit("data/ 에 수집 결과가 하나도 없습니다. scrape*.py 를 먼저 돌리세요.")

    packed, ok_days, sources, mkt_meta = [], {}, [], []
    for m in MARKET_ORDER:
        raw = data[m]
        cfg = MARKETS[m]
        rows = raw["rows"] if raw else []
        packed += [pack_jp(r) if m == "jp" else pack_en(r, m) for r in rows]
        ok_days[m] = raw["ok_days"] if raw else []
        if raw:
            sources.append({
                "mkt": m, "name": raw.get("source", ""),
                "url": raw.get("source_url", ""), "count": len(rows),
                "range": ([ok_days[m][0], ok_days[m][-1]] if ok_days[m] else []),
            })
        mkt_meta.append({
            "id": m, "ko": cfg["ko"], "accent": cfg["accent"],   # 국기는 CSS(.fl-*)로 그린다
            "count": len(rows), "note": cfg["note"], "scraper": cfg["scraper"],
            "has": bool(raw),
        })

    # 한 날짜 안에서는 시장 순 -> 시총 큰 순 -> 코드 순.
    # 시총은 미국만 있어서 나머지 시장은 자연히 코드 순으로 남는다.
    # 하루 700건씩 쏟아지는 미국에서 앞 12개만 펼쳐 보일 때 큰 게 먼저 오게 하려는 것.
    order = {m: i for i, m in enumerate(MARKET_ORDER)}
    packed.sort(key=lambda p: (p[0], order[p[9]], -p[11], p[1]))

    notable = {}
    for m in MARKET_ORDER:
        for code, v in DICTS[m].NOTABLE.items():
            notable[m + ":" + code] = list(v)

    per_day = Counter(p[0] for p in packed)
    notable_hits = sum(1 for p in packed if p[9] + ":" + p[1] in notable)
    all_ok = sorted({d for m in ok_days for d in ok_days[m]})

    # 데이터가 있는 주만 네비게이션에 노출한다.
    # 한국 시간으로 보면 미국 장후 발표가 다음 날로 밀리므로, 그 날짜도 포함한다.
    kdays = {p[12] for p in packed if p[12]}
    weeks = sorted({monday_of(date.fromisoformat(d)).isoformat()
                    for d in set(all_ok) | kdays})

    # **러너는 UTC 다.** date.today() 를 그대로 쓰면 한국 시각 0시~9시 사이에
    # 만들어진 페이지의 '오늘'이 어제가 된다. 화면 쪽에서 브라우저 날짜로 다시
    # 정하지만(D.today), 여기 값도 맞춰 둔다 — 자바스크립트가 막힌 자리에서도
    # 하루 어긋난 페이지가 나가면 안 된다.
    today = (datetime.now(timezone.utc) + timedelta(hours=9)).date().isoformat()
    default_week = monday_of(date.fromisoformat(today)).isoformat()
    if default_week not in weeks and weeks:
        default_week = min(weeks, key=lambda w: abs(
            (date.fromisoformat(w) - date.fromisoformat(today)).days))

    # 시장별 시총 수집률 (종목 단위로 센다 — 한 종목이 여러 날 나올 수 있다)
    cap_cover = {}
    for m in MARKET_ORDER:
        codes = {p[1]: p[11] for p in packed if p[9] == m}
        if codes:
            cap_cover[m] = round(sum(1 for v in codes.values() if v) / len(codes), 4)

    # 사업부별 매출은 총매출과 대본 뒤에 싣는다. 두 배로 부푼 종목은 조용히
    # 지우지 않고 몇 종목을 뺐는지 수집 기록에 적는다.
    on_screen = {p[9] + ":" + p[1] for p in packed}
    seg, tossed = {}, []
    for s, rec in SEG.items():
        if s not in on_screen:
            continue
        got = pack_seg(rec, FIN.get(s))
        if got:
            seg[s] = got
        else:
            tossed.append(s.split(":")[-1])
    if SEG:
        note = f"  사업부별 매출 {len(seg):,}종목"
        if tossed:
            note += (f" · 합이 총매출보다 고르게 부풀어 뺀 종목 {len(tossed)}"
                     f" ({', '.join(sorted(tossed)[:6])})")
        print(note)

    # 실적 브리핑 재료 둘. 회사가 공시한 통기 예상(일본 결산단신의 예상란)과
    # 공시 원문에서 옮긴 한국어 코멘트(briefs.py). 코멘트는 (종목, 분기) 열쇠라
    # 다음 분기가 오면 자동으로 내려간다 — 낡은 말이 새 분기에 붙는 것을 막는다.
    fcst = {k: v for k, v in load_fcst().items() if k in on_screen}
    briefs = {k: v for k, v in BRIEFS.items() if k in on_screen}
    if fcst:
        print(f"  회사 통기 예상(가이던스) {len(fcst):,}종목")
    if briefs:
        print(f"  실적 코멘트(한국어) {len(briefs):,}종목")

    payload = {
        "rows": packed,
        "notable": notable,
        "groupOrder": {m: DICTS[m].GROUP_ORDER for m in MARKET_ORDER},
        "holidays": {m: {d: holiday_ko(n) for d, n in HOLIDAYS[m].items()}
                     for m in MARKET_ORDER},
        "okDays": ok_days,
        # 홍콩은 '이미 나온 공시'만 모은다. 그래서 내일 칸이 비어 있는 것은
        # 못 받아서가 아니라 아직 공시가 없어서다. 둘을 같은 말로 적으면
        # "홍콩은 왜 내일부터 수집을 안 하나"로 읽힌다(실제로 그렇게 읽혔다).
        "pastOnly": [m for m in MARKET_ORDER if MARKETS[m].get("past_only")],
        "markets": mkt_meta,
        "weeks": weeks,
        "defaultWeek": default_week,
        "today": today,
        "sources": sources,
        # 규모 필터 눈금. 원 단위로 매기되 실제 비교는 달러(십억)로 한다.
        "capSteps": [{"jo": j, "usdB": round(j * 1e12 / USD_KRW / 1e9, 2)}
                     for j in CAP_STEPS],
        "usdKrw": USD_KRW,
        # 시총 데이터가 있는 시장. 없는 시장에는 규모 필터를 적용할 수 없다.
        "capMarkets": sorted({p[9] for p in packed if p[11]}),
        "capCover": cap_cover,
        "fcst": fcst,
        "briefs": briefs,
        # **시총이 캘린더 원본에 같이 오는 시장.** 규모 필터에서 '시총을 모르는
        # 종목'을 감춰도 되는지는 수집률이 아니라 이걸로 갈라야 한다.
        # 미국은 나스닥이 시총을 같이 주므로 비어 있으면 정말 값이 없는 종목이다.
        # 일본·홍콩은 따로 받아 붙이는 거라 비어 있으면 '아직 못 받았다'는 뜻이고,
        # 거기엔 히로세전기(8,828억엔) 같은 회사가 섞여 있다 — 지우면 안 된다.
        # (수집률로 갈랐다면 일본 97.6%·홍콩 99.2%라 오히려 그쪽이 지워졌을 것이다.)
        "capInline": [m for m in MARKET_ORDER if MARKETS[m].get("cap")],
        # 실적 수치. 캘린더에 실린 종목 것만, 그중에서도 알맹이가 있는 것만
        # 싣는다. 수집 쪽에는 '두드려 봤지만 자료가 없더라'는 표시만 남은 기록도
        # 있는데(v/ts/none), 그건 다음에 또 두드릴지 정하는 데만 쓰고 화면에는
        # 필요 없다. 그대로 실으면 index.html 만 몇 배로 부푼다.
        # 연간 수치는 **점을 싣지 않는다.** 화면에 그리지 않기로 했으므로 실어봐야
        # 파일만 무거워진다. 다만 'freq' 는 남겨서 "분기를 못 구했다"와
        # "아직 안 받았다"를 화면에서 가려 말할 수 있게 한다.
        #
        # 점은 [라벨, 매출, 영업이익] 배열로 눕힌다. 이름표를 종목마다 스무 번씩
        # 되풀이하면 그것만으로 파일이 반 메가 늘어난다. 종료일과 순이익은
        # 화면에서 안 쓰므로 빼고, 자료 파일에는 그대로 남겨 둔다.
        "fin": {s: pack_fin(rec) for s, rec in FIN.items()
                if (rec.get("points") or rec.get("eps"))
                and s in {p[9] + ":" + p[1] for p in packed}},
        # 무엇을 파는 회사인가. 한국어는 사람이 쓴 것(descriptions.py), 원문은
        # 받아온 것(desc.json). 화면에 실린 종목 것만 싣는다.
        "descKo": {k: v for k, v in DESC_KO.items() if k in on_screen},
        "desc": {k: v["t"] for k, v in DESC.items()
                 if v.get("t") and k in on_screen and k not in DESC_KO},
        # 사업부별 매출. "매출이 늘었다"보다 "어디서 늘었다"가 중요할 때가 있다.
        # 지금은 미국 종목만 — 일본·홍콩은 소스에 부문 페이지가 없다.
        # 합이 총매출과 안 맞는 종목은 여기서 걸러진다(seg_fit).
        "seg": seg,
        # 일본 부문의 연간 이력(有報 기준, EDINET DB). 분기 차트와 섞지 않고
        # 아래에 따로 그린다 — 단위가 달라 한 차트에 서면 높이가 거짓말이 된다.
        "segH": {s: rec for s, rec in SEG_HIST.items() if s in on_screen},
    }

    # **러너는 UTC 로 돈다.** 예전에는 datetime.now() 에 "KST" 만 붙였는데,
    # 그러면 화면에 늘 아홉 시간 뒤처진 시각이 뜬다 — 오후 5시에 봤는데
    # "갱신 07:49 KST" 라고 적혀 있으니 하루 종일 안 돌아간 것처럼 보인다.
    stamp = (datetime.now(timezone.utc) + timedelta(hours=9)
             ).strftime("%Y-%m-%d %H:%M KST")
    parts = " · ".join(f'{flag_html(m)} {MARKETS[m]["ko"]} <b>{len(data[m]["rows"]):,}</b>'
                       for m in have)
    head = (f'{parts} · 합계 <b>{len(packed):,}건</b> · '
            f'수집 <b>{all_ok[0]} ~ {all_ok[-1]}</b> · 갱신 {stamp}'
            if all_ok else f'{parts} · 갱신 {stamp}')

    # 기계 변환은 일본에만 해당한다. 미국·홍콩은 영문 원문을 그대로 쓴다.
    jp_lvl = Counter(p[8] for p in packed if p[9] == "jp")
    jp_total = sum(jp_lvl.values())
    tl_note = (f'일본 회사명 {jp_total:,}건 중 <b>{jp_lvl[2] + jp_lvl[1]:,}건</b>은 사전 표기, '
               f'<b>{jp_lvl[0]:,}건</b>은 기계 변환입니다. '
               f'미국·홍콩은 원본이 영문이라 사전에 있으면 한글명, 없으면 영문명을 그대로 씁니다.'
               if jp_total else
               '미국·홍콩은 원본이 영문이라 사전에 있으면 한글명, 없으면 영문명을 그대로 씁니다.')

    # 시장 색은 markets.py 한 군데서만 정한다. CSS 변수로 흘려보낸다.
    mkt_css = "\n".join(
        f'.m-{m} {{ --mk:{MARKETS[m]["accent"]}; }}' for m in MARKET_ORDER)

    html = TEMPLATE.replace("__ICON__", logo_uri()) \
                   .replace("__FLAGCSS__", flag_css()) \
                   .replace("__HEAD__", head) \
                   .replace("__TLNOTE__", tl_note) \
                   .replace("__MKTCSS__", mkt_css) \
                   .replace("__DATA__", json.dumps(payload, ensure_ascii=False,
                                                   separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"{OUT}  ({kb:,.0f} KB)")
    for m in MARKET_ORDER:
        n = len(data[m]["rows"]) if data[m] else 0
        days = len(ok_days[m])
        state = f"{n:>6,}건 / {days:>3}일" if data[m] else "     미수집 — " + MARKETS[m]["scraper"]
        print(f"  {MARKETS[m]['flag']} {MARKETS[m]['ko']:<3} {state}")
    print(f"  합계 {len(packed):,}건 / 주목 {notable_hits:,}건 / {len(weeks)}주")
    if per_day:
        busiest = max(per_day.items(), key=lambda kv: kv[1])
        print(f"  최다 {busiest[0]} {busiest[1]:,}건")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<!-- **폰에서도 PC 화면 그대로 그린다.** width=device-width 로 두면 폰 폭(390px 쯤)이
     그대로 CSS 폭이 되어 @media 가 걸리고, 캘린더가 한 줄짜리로 쌓인다. 한 주를
     나란히 놓고 보는 것이 이 화면의 전부인데 세로로 쌓이면 그게 없어진다.
     폭을 1440 으로 못박으면 브라우저가 페이지 전체를 줄여 그리므로 데스크톱과
     같은 다섯 칸이 나오고, 손가락으로 확대해서 본다.
     좁은 화면에 맞춰 보고 싶으면 캘린더 아래 버튼으로 되돌린다(선택은 기억된다). -->
<meta name="viewport" id="vp" content="width=1440">
<script>
try {
  if (localStorage.getItem('esFit') === 'mobile')
    document.getElementById('vp').content = 'width=device-width, initial-scale=1';
} catch (e) {}
</script>
<!-- GitHub Pages는 같은 URL에 새 파일을 덮어쓴다. 캐시가 남으면 지난주 일정을
     이번주로 착각하게 되므로 매번 새로 받도록 강제한다. -->
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>Earning Samurai — 글로벌 실적발표 캘린더</title>
<!-- 대표 아이콘. 그림 파일을 따로 두지 않고 SVG 를 그대로 심는다.
     제목 옆 로고와 **같은 곳(build.py 의 LOGO_SVG)** 에서 온다. -->
<!-- rel 을 둘로 나누지 않고 한 줄에 적는다. 그림을 그대로 심으므로 link 를
     따로 두면 같은 84KB 가 한 벌 더 들어간다. -->
<link rel="icon apple-touch-icon" href="__ICON__">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=42dot+Sans:wght@300..800&display=swap"
      rel="stylesheet">
<style>
:root {
  --bg:#0f1419; --fg:#ffffff; --a1:#F0435A; --a2:#5B9BD5; --a3:#FFB020;
  --panel:#161d24; --line:#243039; --mute:#93a4b1; --ok:#7FD1A4;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:32px 28px 80px;
  background:var(--bg); color:var(--fg);
  font-family:'42dot Sans','Noto Sans JP','Yu Gothic','Meiryo',
              'Malgun Gothic',sans-serif;
  font-size:20px; line-height:1.55;
  -webkit-font-smoothing:antialiased;
}
.wrap { max-width:1680px; margin:0 auto; }
.topline {
  font-size:19px; color:var(--mute); margin:0 0 10px; padding:9px 16px;
  background:var(--panel); border:1px solid var(--line); border-radius:8px;
  border-left:5px solid var(--a1); display:inline-block;
}
.topline b { color:var(--fg); }
h1 { font-size:38px; font-weight:800; margin:0 0 6px; letter-spacing:-.5px;
     display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
h1 .jp { color:var(--a3); }
h1 .byline { font-size:20px; font-weight:600; color:var(--mute); letter-spacing:0; }
/* 투구 아이콘 — 파비콘과 **같은 그림**. 둘 다 build.py 의 LOGO_SVG 에서 온다.
   예전에는 같은 SVG 를 두 곳에 손으로 붙여 넣었더니 한쪽만 고쳐져 달라졌다. */
h1 .mark {
  width:52px; height:52px; flex:0 0 auto; border-radius:12px;
  background:url("__ICON__") center/contain no-repeat;
}
h2 {
  font-size:26px; font-weight:700; margin:52px 0 14px;
  padding-left:14px; border-left:6px solid var(--a3);
}
h2 .n { color:var(--a3); margin-right:10px; }
.sub { color:var(--mute); font-size:20px; margin:0 0 4px; }
.meta { color:var(--mute); font-size:18px; font-weight:400; }

.cards { display:flex; flex-wrap:wrap; gap:16px; margin:18px 0 8px; }
.card {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:16px 22px; min-width:190px;
}
.card .k { color:var(--mute); font-size:18px; }
.card .v { font-size:30px; font-weight:800; color:var(--a1); }
.card .v.sm { font-size:22px; }

/* ── 시장 탭 ───────────────────────────────────────────────── */
/* --mk 는 시장 강조색. markets.py 가 유일한 출처고 여기로 흘러온다. */
__MKTCSS__
.mtabs { display:flex; flex-wrap:wrap; gap:10px; margin:20px 0 4px; }
.mtab {
  display:flex; align-items:center; gap:9px; background:var(--panel);
  border:1px solid var(--line); border-bottom:3px solid transparent;
  border-radius:10px; padding:10px 18px; cursor:pointer;
  font-family:inherit; color:var(--fg); font-size:19px; font-weight:700;
  line-height:1.2;
}
.mtab:hover { border-color:#31414f; }
.mtab .mchk { width:17px; height:17px; accent-color:var(--mk,var(--a2)); cursor:pointer; margin:0; }
/* 국기. 이모지가 아니라 그림이다 — 윈도우는 국기 이모지를 못 그린다. */
.fl {
  display:inline-block; width:1.42em; height:1em; vertical-align:-.14em;
  border-radius:2px; background:center/100% 100% no-repeat;
  box-shadow:0 0 0 1px rgba(255,255,255,.14) inset;
}
__FLAGCSS__
.mtab .fl { width:1.5em; height:1.06em; }
.gl { font-size:20px; line-height:1; }
.mtab .n {
  color:var(--mute); font-weight:600; font-size:17px;
  font-variant-numeric:tabular-nums;
}
.mtab.m-all { --mk:#93a4b1; }
.mtab.on { background:#1b2530; border-bottom-color:var(--mk,var(--a2)); }
.mtab.on .n { color:var(--fg); }
/* 아직 수집하지 않은 시장. 눌러서 사유를 볼 수 있게 죽이지는 않는다. */
.mtab.empty { opacity:.55; }
.mtab.empty .n { color:#6b7b88; font-weight:400; }

.note {
  background:#1a2129; border:1px solid var(--line); border-left:5px solid var(--a3);
  border-radius:8px; padding:14px 20px; margin:14px 0; color:#c9d6e0; font-size:19px;
}

/* ── 알림 배너 ─────────────────────────────────────────────── */

/* ── 툴바 ──────────────────────────────────────────────────── */
.tools {
  display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:14px 0;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:14px 16px;
}
select, input[type=search] {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 13px; font-size:19px; font-family:inherit;
}
select { max-width:230px; }
select:focus { outline:2px solid var(--a3); }
input[type=search] { width:min(420px,100%); padding:11px 16px; font-size:20px; }
input[type=search]:focus { outline:2px solid var(--a1); border-color:var(--a1); }

/* ── 종목 바로 찾기 ────────────────────────────────────────────
   아래 표의 검색칸은 **보고 있는 것 안에서** 거른다 — 탭이 미국이면 일본 회사는
   안 나오고, 규모 필터에 걸린 회사도 안 나온다. 그건 훑어볼 때 쓰는 것이다.
   여기 것은 반대다: **가진 종목 전부**에서 찾아 곧장 그 회사 창을 연다. */
.find { position:relative; margin:14px 0 6px; max-width:640px; }
.find input[type=search] { width:100%; padding:14px 18px 14px 46px; font-size:21px; }
.find .mag { position:absolute; left:16px; top:50%; transform:translateY(-50%);
             color:var(--mute); font-size:20px; pointer-events:none; }
.fqlist { position:absolute; z-index:40; left:0; right:0; top:calc(100% + 6px);
          background:var(--panel); border:1px solid var(--line); border-radius:10px;
          box-shadow:0 14px 34px rgba(0,0,0,.5); overflow:hidden; }
.fqlist[hidden] { display:none; }
.fqi { display:flex; align-items:baseline; gap:10px; padding:11px 16px; cursor:pointer;
       border-bottom:1px solid var(--line); font-size:19px; }
.fqi:last-child { border-bottom:0; }
.fqi.on, .fqi:hover { background:#1b2530; }
.fqi .fqn { font-weight:700; }
.fqi .fqc { color:var(--mute); font-size:17px; }
.fqi .fqd { margin-left:auto; color:var(--mute); font-size:17px; white-space:nowrap; }
.fqnone { padding:12px 16px; color:var(--mute); font-size:18px; }
.chk {
  display:inline-flex; align-items:center; gap:8px; font-size:19px;
  cursor:pointer; user-select:none; white-space:nowrap;
}
.chk input { width:20px; height:20px; accent-color:var(--a1); cursor:pointer; }
.count { margin-left:auto; color:var(--mute); font-size:19px; }
.count b { color:var(--a1); font-size:22px; }

/* button 과 a 를 함께 받는다. 예전에는 button.btn 으로만 잡아서
   모달의 <a class="btn"> 링크가 맨 파란 글씨로 나왔다. */
.btn {
  background:#0b1015; color:var(--fg); border:1px solid var(--line);
  border-radius:8px; padding:11px 16px; font-size:19px; font-family:inherit;
  cursor:pointer; text-decoration:none; display:inline-block; line-height:1.2;
}
/* .btn 의 display 가 브라우저 기본 [hidden] 규칙을 이겨서, hidden 을 걸어도
   버튼이 그대로 보였다. 명시적으로 눌러 준다. */
.btn[hidden] { display:none; }
.btn:hover { border-color:var(--a1); color:var(--a1); }
.btn.pri { background:var(--a1); border-color:var(--a1); color:#fff; font-weight:700; }
.btn.pri:hover { filter:brightness(1.12); color:#fff; }
button.btn:disabled { opacity:.4; cursor:default; }
button.btn:disabled:hover { border-color:var(--line); color:var(--fg); }

/* ── 주 네비게이션 ─────────────────────────────────────────── */
.weeknav {
  display:flex; align-items:center; gap:14px; flex-wrap:wrap;
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  padding:12px 16px; margin:14px 0;
}
.weeknav .wlabel { font-size:24px; font-weight:800; letter-spacing:-.3px; }
.weeknav .wsum { color:var(--mute); font-size:18px; }
.weeknav .spacer { margin-left:auto; }

/* 나라 고르기 — 위쪽 탭과 같은 것을 캘린더 옆에도 둔다. 주를 넘기다가
   나라를 바꾸려고 맨 위까지 올라갔다 오지 않게. 둘은 늘 같이 움직인다. */
.mpick { display:flex; gap:6px; flex-wrap:wrap; }
.mpick .mp {
  font:inherit; font-size:17px; font-weight:700; cursor:pointer;
  background:#141c24; color:var(--mute); border:1px solid var(--line);
  border-radius:999px; padding:5px 13px; line-height:1.3;
}
.mpick .mp:hover:not(:disabled) { border-color:var(--a2); color:var(--fg); }
.mpick .mp.on { background:var(--a2); border-color:var(--a2); color:#0b1116; }
.mpick .mp:disabled { opacity:.4; cursor:not-allowed; }

/* ── 주간 캘린더 ───────────────────────────────────────────── */
.cal {
  display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:14px;
  align-items:start;
}
@media (max-width:1400px) { .cal { grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media (max-width:900px)  { .cal { grid-template-columns:minmax(0,1fr); } }

.day {
  background:var(--panel); border:1px solid var(--line); border-radius:10px;
  min-width:0; overflow:hidden;
}
.day.today { border-color:var(--a1); box-shadow:0 0 0 1px var(--a1) inset; }
.day.closed { opacity:.62; }
.dh {
  padding:12px 14px; border-bottom:1px solid var(--line); background:#1b232b;
  display:flex; align-items:baseline; gap:8px;
}
.dh .dow { font-size:18px; color:var(--mute); font-weight:700; }
.dh .dnum { font-size:26px; font-weight:800; }
.day.today .dh .dnum, .day.today .dh .dow { color:var(--a1); }
.dh .dcnt { margin-left:auto; font-size:18px; color:var(--mute); }
.dh .dcnt b { color:var(--a3); font-size:20px; }
.dh .todaytag {
  font-size:14px; font-weight:700; background:var(--a1); color:#fff;
  border-radius:4px; padding:2px 7px; margin-left:4px;
}
.dbody { padding:10px 10px 12px; }
.dsec {
  font-size:16px; color:var(--mute); font-weight:700; margin:6px 2px 7px;
  letter-spacing:.02em;
}
.dsec.star { color:var(--a3); }
.dsec.gap { color:#8a6d3b; font-weight:600; border-top:1px dashed #3a3222;
            padding-top:6px; margin-top:8px; }
/* 수집 전인 시장의 자리 */
.cal.none { display:block; }
.nodata {
  background:var(--panel); border:1px dashed #3a4550; border-radius:10px;
  padding:38px 26px; text-align:center; font-size:21px; color:#c9d6e0;
}
.nodata span { display:block; margin-top:10px; font-size:18px; color:var(--mute); }
.nodata b { color:var(--a3); font-weight:700; }
.empty { color:#55636e; font-size:18px; padding:16px 4px; text-align:center; }
.empty .why { display:block; color:var(--mute); font-size:17px; margin-top:4px; }

/* 종목 칩 — 왼쪽 띠 색이 시장이다. 세 시장을 한 칸에 섞어 놓아도 구분된다. */
.chip {
  display:flex; align-items:center; gap:7px; width:100%;
  background:#0f1620; border:1px solid #1e2831; border-radius:7px;
  padding:7px 9px 7px 12px; margin-bottom:5px; cursor:pointer; text-align:left;
  font-family:inherit; color:var(--fg); font-size:17px; line-height:1.3;
  box-shadow:inset 3px 0 0 0 var(--mk,transparent);
}
.chip .fl { flex:0 0 auto; font-size:15px; }
/* 발표 시각 — 미국만 원본에 있다. 장전/장후는 미국 실적을 볼 때 제일 먼저 보는 값. */
.chip .tm, .tm {
  flex:0 0 auto; font-size:13px; font-weight:700; border-radius:4px;
  padding:1px 5px; white-space:nowrap;
}
.tm.pre { color:var(--a3); border:1px solid #4a3a1c; }
.tm.post { color:var(--ok); border:1px solid #24463a; }
/* 한국 시간 표기. 원본에 실제 시각이 있으면 또렷하게, 어림한 것은 흐리게. */
.tm.exact { color:var(--ok); border:1px solid #24463a; font-variant-numeric:tabular-nums; }
.tm.approx { color:var(--mute); border:1px solid var(--line); font-variant-numeric:tabular-nums; }
/* 발표가 이미 나온 것. 예정 시각보다 '나왔다'가 더 중요한 소식이라 이걸 덮어쓴다. */
.tm.ok { color:#0b1116; background:var(--ok); border:1px solid var(--ok);
         font-variant-numeric:tabular-nums; }
/* 발표일은 지났는데 수치를 아직 못 받은 것. 발표된 건 확실하므로 ✓ 를 달되,
   눌러도 볼 게 없으니 흐리게 둔다 — 진한 ✓ 와 한눈에 갈려야 한다. */
.tm.over { color:var(--ok); border:1px solid #24463a; opacity:.55; }
/* 무엇을 파는 회사인가 — 팝업 맨 위 한 줄 */
.biz {
  margin:0 0 14px; padding:12px 14px; border-radius:10px;
  background:#141c24; border-left:3px solid var(--a1);
  font-size:19px; line-height:1.55; color:var(--fg);
}
.biz.raw { border-left-color:var(--line); color:var(--mute); font-size:17px; }
.biz .tagx {
  display:inline-block; margin-right:8px; padding:1px 7px; border-radius:5px;
  background:#1e2a35; color:#8fb8dc; font-size:13px; vertical-align:1px;
}

/* 실적 브리핑 — 차트를 글로 읽어주는 칸. 부문 차트 아래 선다. */
.brief {
  margin:16px 0 6px; padding:13px 16px 11px; border-radius:10px;
  background:#141c24; border-left:3px solid var(--a3);
}
.brief h4 {
  margin:0 0 8px; font-size:18px; color:var(--a3); font-weight:700;
}
.brief h4 .tagx {
  display:inline-block; margin-left:8px; padding:1px 7px; border-radius:5px;
  background:#1e2a35; color:#8fb8dc; font-size:13px; vertical-align:2px;
}
.brief ul { margin:0; padding-left:20px; }
.brief li { font-size:18px; line-height:1.6; color:var(--fg); margin:3px 0; }
.brief li i { color:var(--mute); font-style:normal; font-size:15px; }
.brief b.up { color:var(--ok); }
.brief b.dn, .brief span.dn { color:var(--a1); }
.brief .brko { color:#dbe7f0; }

/* 칩의 시총 표기 */
.chip .cc {
  flex:0 0 auto; font-size:13px; color:#8fb8dc; font-variant-numeric:tabular-nums;
}
.chip:hover { border-color:var(--a2); background:#16202b; }
.chip.big { background:#1d1418; border-color:#43242c; }
.chip.big:hover { border-color:var(--a1); }
.chip .cd {
  font-size:15px; font-weight:700; color:#8fb8dc; font-variant-numeric:tabular-nums;
  flex:0 0 auto;
}
.chip.big .cd { color:var(--a1); }
.chip .cn { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.chip .cq {
  flex:0 0 auto; font-size:14px; color:var(--mute); border:1px solid var(--line);
  border-radius:4px; padding:1px 5px;
}

/* 이미 실적이 나온 종목. 눌러보면 숫자가 있다는 뜻이라 눈에 띄어야 한다. */
.chip.done { border-color:#27503c; }
/* 주말에 나온 발표. 칸은 월요일에 얹었지만 실제 요일을 밝힌다 — 버크셔는
   원래 토요일 아침에 발표한다. 없는 일로 만들면 안 된다. */
.chip .we {
  flex:0 0 auto; font-size:13px; font-weight:800; border-radius:4px;
  padding:1px 5px; color:#f0b45a; border:1px solid #4a3a1c; white-space:nowrap;
}
.donetag {
  display:inline-block; background:#173026; border:1px solid #27503c; color:var(--ok);
  border-radius:5px; padding:1px 8px; font-size:15px; font-weight:700; margin-left:6px;
}
.dim { color:var(--mute); }
.dim em { font-style:normal; font-weight:700; margin-left:4px; }
.dim em.up { color:var(--ok); }
.dim em.dn { color:var(--a1); }
.more {
  width:100%; background:transparent; border:1px dashed var(--line);
  color:var(--mute); border-radius:7px; padding:7px; font-size:16px;
  cursor:pointer; font-family:inherit; margin-top:2px;
}
.more:hover { border-color:var(--a3); color:var(--a3); }

/* ── 표 ────────────────────────────────────────────────────── */
.scroll {
  max-height:640px; overflow:auto;
  border:1px solid var(--line); border-radius:10px;
}
table { border-collapse:separate; border-spacing:0; width:100%;
        font-variant-numeric:tabular-nums; }
thead th {
  position:sticky; top:0; z-index:2;
  background:#1b232b; color:#dce7ef; font-size:19px; font-weight:700;
  text-align:left; padding:13px 14px; white-space:nowrap;
  border-bottom:2px solid var(--line); cursor:pointer; user-select:none;
}
thead th:hover { color:var(--a3); }
thead th .ar { opacity:.35; font-size:15px; margin-left:5px; }
thead th.asc .ar, thead th.desc .ar { opacity:1; color:var(--a3); }
thead th.nos { cursor:default; }
thead th.nos:hover { color:#dce7ef; }
tbody td {
  padding:11px 14px; text-align:left; white-space:nowrap;
  border-bottom:1px solid #1c252d; font-size:19px;
}
tbody tr { background:#0f1419; }
tbody tr:nth-child(even) { background:#12191f; }
tbody tr:hover { background:#1d2833; }
td.code { color:#8fb8dc; font-weight:700; }
td.code.big { color:var(--a1); }
td.jp { color:var(--mute); font-size:17px; }
td.dim { color:var(--mute); font-size:18px; }
/* 기계 변환한 한글 표기는 점선을 깔아 구분한다. 마우스를 올리면 원문이 뜬다. */
.guess { border-bottom:1px dotted #3c4750; }
.qtag {
  font-size:15px; border:1px solid var(--line); border-radius:4px;
  padding:1px 6px; color:var(--mute);
}
.qtag.q4 { color:var(--a3); border-color:#4a3a1c; }

/* ── 실적 시계열 ───────────────────────────────────────────── */
#mdFin { margin-top:18px; }
.finnote { color:var(--mute); font-size:17px; margin:10px 0 0; }
.finwrap { border-top:1px solid var(--line); padding-top:14px; }
.finhead { font-size:18px; font-weight:700; margin-bottom:8px; }
.finhead .warn { color:var(--a3); font-weight:400; font-size:16px; margin-left:10px; }
.finhead.sub { margin-top:14px; }
.finhead .dim { font-weight:400; font-size:16px; color:var(--mute); margin-left:8px; }
/* 어디까지 발표된 건지. X축 맨 오른쪽 눈금과 같은 값이다. */
.finhead .now {
  margin-left:10px; font-size:15px; font-weight:700; color:var(--a2);
  border:1px solid #2b4a63; background:#12212c; border-radius:5px; padding:1px 9px;
}
/* 분기가 스무 개 넘으면 900px 로는 숫자가 겹친다. 그림을 제 폭대로 그리고
   좁으면 이 칸 안에서만 옆으로 밀리게 한다 — 페이지 전체가 밀리면 안 된다. */
.finbox { overflow-x:auto; overflow-y:hidden; margin-bottom:6px; }
.finsvg { height:auto; display:block; min-width:880px; }
.finsvg .fb { fill:#5B9BD5; }
.finsvg .fl { fill:none; stroke-width:2.2; }
.finsvg .fl.opm { stroke:#ED7D31; }
.finsvg .fl.yoy { stroke:#5B9BD5; }
.finsvg .dot { stroke:#0f1419; stroke-width:1.2; }
.finsvg .dot.opm { fill:#ED7D31; }
.finsvg .dot.yoy { fill:#5B9BD5; }
.finsvg .fz { stroke:#28323c; }
.finsvg .fzero { stroke:#4a5661; }
.finsvg .fx { fill:var(--mute); font-size:12px; }
.finsvg .fx.opm { fill:#c98a4e; }
/* 맨 오른쪽 = 가장 최근 발표 분기. 어디까지 나온 건지 한눈에 보이게 표시한다. */
.finsvg .fx.now { fill:var(--fg); font-weight:800; }
/* 막대와 점에 붙는 숫자. 이게 이 그림의 요점이다 — 모양만 보고 값을 짐작하게
   두지 않는다. 색은 각 계열과 맞춘다. */
/* 막대와 점에 붙는 숫자. 서로 겹쳐도 읽히도록 바탕색 테두리를 두른다 —
   테두리를 글자 아래에 깔아야(paint-order) 획이 굵어 보이지 않는다. */
.finsvg .vn {
  font-size:12px; font-weight:700; font-variant-numeric:tabular-nums;
  stroke:var(--panel); stroke-width:3.5px; stroke-linejoin:round;
  paint-order:stroke fill;
}
.finsvg .vn.rev { fill:#dce9f5; }
/* 쌓은 막대 조각 안에 적는 숫자. 조각 색이 다 다르니 흰 글씨에 어두운 테두리. */
.finsvg .vn.seg { fill:#ffffff; stroke:rgba(0,0,0,.55); stroke-width:2.5px; font-size:11px; }
.finsvg .vn.opm { fill:#ED7D31; }
.finsvg .vn.yoy { fill:#8fc0ea; }
.finlegend { display:flex; gap:14px; flex-wrap:wrap; font-size:15px; color:var(--mute);
             align-items:center;
             margin-top:6px; align-items:center; }
.finlegend .lg::before { content:'■'; margin-right:4px; color:var(--c, inherit); }
.finlegend .rev::before { color:#5B9BD5; }
.finlegend .opm::before { color:#ED7D31; }
.finlegend .yoy::before { color:#5B9BD5; }
.finlegend .src { margin-left:auto; font-size:14px; }
.epsrow { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.epsbox { background:#141c24; border:1px solid var(--line); border-radius:7px;
          padding:7px 11px; font-size:15px; }
.epsbox b { display:block; color:var(--mute); font-size:13px; font-weight:600; }
.epsbox i { color:var(--mute); font-style:normal; }
.epsbox em { font-style:normal; margin-left:5px; font-weight:700; }
.epsbox em.up { color:var(--ok); }
.epsbox em.dn { color:var(--a1); }
.epsbox.next { border-style:dashed; color:var(--mute); }
/* 표의 시장 칸 — 왼쪽 띠로 캘린더 칩과 같은 색을 쓴다 */
.mcell { white-space:nowrap; box-shadow:inset 3px 0 0 0 var(--mk,transparent); }

/* ── 주목종목 그룹 ─────────────────────────────────────────── */
.groups { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr));
          gap:16px; margin-top:14px; }
.gbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px 10px; min-width:0;
        border-top:3px solid var(--mk,var(--line)); }
/* 전체 보기에서 시장별 묶음 머리 */
.gmkt { font-size:22px; font-weight:800; margin:26px 0 2px;
        padding-left:12px; border-left:5px solid var(--mk,var(--a2)); }
.gbox h3 { font-size:20px; margin:2px 0 10px; font-weight:700; }
.gbox h3 .gn { color:var(--mute); font-size:17px; font-weight:400; margin-left:8px; }
.grow { display:flex; align-items:center; gap:9px; padding:6px 2px;
        border-bottom:1px solid #1a222a; font-size:18px; cursor:pointer; }
.grow:hover { background:#141b22; }
.grow:last-child { border-bottom:0; }
.grow .gc { color:#8fb8dc; font-weight:700; font-size:16px; flex:0 0 46px; }
.grow .gk { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
            white-space:nowrap; }
.grow .ge { color:var(--mute); font-size:15px; }
.grow .gd { flex:0 0 auto; color:var(--a3); font-size:17px; font-weight:700; }
.grow .gd.past { color:var(--mute); font-weight:400; }

/* ── 발표 건수 차트 ────────────────────────────────────────── */
.chartbox { background:var(--panel); border:1px solid var(--line); border-radius:10px;
            padding:14px 12px 8px; margin-top:14px; overflow-x:auto; }
svg.bars { display:block; width:100%; height:auto; min-width:900px; }
svg.bars text { font-family:inherit; fill:var(--mute); font-size:11px; }
svg.bars .vl { fill:var(--fg); font-size:11px; font-weight:700; }
svg.bars rect.b { fill:var(--a2); }
svg.bars rect.b.wk { fill:var(--a1); }
svg.bars rect.b:hover { fill:var(--a3); }

/* ── 상세 모달 ─────────────────────────────────────────────── */
.mdback { position:fixed; inset:0; background:rgba(6,10,14,.82); z-index:50;
          display:flex; align-items:center; justify-content:center; padding:24px; }
.mdback[hidden] { display:none; }
.md { background:var(--panel); border:1px solid var(--line); border-radius:12px;
      max-width:1000px; width:100%; padding:22px 24px;
      max-height:88vh; overflow-y:auto; }
.md .mt { font-size:26px; font-weight:800; margin:0 0 4px; }
.md .ms { color:var(--mute); font-size:18px; margin:0 0 14px; }
.md dl { display:grid; grid-template-columns:auto 1fr; gap:8px 16px; margin:0 0 18px;
         font-size:19px; }
.md dt { color:var(--mute); }
.md dd { margin:0; }
.md .mact { margin-top:18px; padding-top:14px; border-top:1px solid var(--line); display:flex; gap:10px; flex-wrap:wrap; }

.foot { color:var(--mute); font-size:17px; margin-top:56px; line-height:1.7;
        border-top:1px solid var(--line); padding-top:18px; }
.foot a { color:var(--a2); }
</style>
</head>
<body>
<div class="wrap">

<div class="topline">__HEAD__</div>
<h1><span class="mark" aria-hidden="true"></span>Earning <span class="jp">Samurai</span>
    <span class="byline">by CB</span></h1>
<p class="sub">미국 · 일본 · 홍콩 주간 실적발표 일정 — 누가 언제 무엇을 발표했는지</p>

<div class="find">
  <span class="mag" aria-hidden="true">🔎</span>
  <input type="search" id="fq" autocomplete="off" spellcheck="false"
         aria-label="종목 검색" aria-autocomplete="list" aria-controls="fqList"
         placeholder="종목 검색 — 엔비디아 / NVDA / 소니 / 6758 / 텐센트 / 00700">
  <div class="fqlist" id="fqList" hidden></div>
</div>

<div class="mtabs" id="mtabs"></div>
<div class="cards" id="cards"></div>

<h2><span class="n">1</span>주간 캘린더 <span class="meta" id="calMeta"></span></h2>
<div class="weeknav">
  <button class="btn" id="wPrev">← 이전 주</button>
  <div>
    <div class="wlabel" id="wLabel">—</div>
    <div class="wsum" id="wSum"></div>
  </div>
  <button class="btn" id="wNext">다음 주 →</button>
  <span class="spacer"></span>
  <button class="btn" id="wToday">이번 주</button>
  <select id="wPick"></select>
  <span class="mpick" id="calMkts" title="나라를 켜고 끕니다. 맨 위 탭과 같이 움직입니다."></span>
  <select id="fCap" title="시가총액으로 거릅니다. 캘린더와 표에 함께 적용됩니다."></select>
  <label class="chk"><input type="checkbox" id="kstToggle" checked>한국 시간</label>
  <label class="chk"><input type="checkbox" id="jpToggle">원문 보기</label>
</div>
<div class="cal" id="cal"></div>
<div class="tools">
  <button class="btn" id="fitBtn" hidden></button>
</div>

<h2><span class="n">2</span>테마별 주요 종목 실적발표일 <span class="meta" id="gMeta"></span></h2>
<div id="groups"></div>

<h2><span class="n">3</span>일자별 발표 건수 <span class="meta">막대를 누르면 그 주로 이동</span></h2>
<div class="chartbox"><svg class="bars" id="bars" viewBox="0 0 1400 260"
     preserveAspectRatio="xMinYMid meet"></svg></div>

<h2><span class="n">4</span>전체 종목 표</h2>
<div class="tools">
  <input type="search" id="q" placeholder="한글·원문·영문·코드 검색 — 엔비디아 / NVDA / 소니 / ソニー / 텐센트 / 00700" autocomplete="off">
  <select id="fSector"><option value="">전체 업종</option></select>
  <select id="fMarket"><option value="">전체 거래소</option></select>
  <select id="fKind"><option value="">전체 분기</option></select>
  <label class="chk"><input type="checkbox" id="tBig">주목종목만</label>
  <label class="chk"><input type="checkbox" id="tFuture">오늘 이후만</label>
  <span class="count" id="tCnt"></span>
</div>
<div class="scroll">
  <table id="tAll">
    <thead><tr>
      <th data-k="0">발표일<span class="ar">▾</span></th>
      <th data-k="9">시장<span class="ar">▾</span></th>
      <th data-k="1">코드<span class="ar">▾</span></th>
      <th data-k="2">회사명<span class="ar">▾</span></th>
      <th data-k="7">원문<span class="ar">▾</span></th>
      <th data-k="10">시각<span class="ar">▾</span></th>
      <th data-k="4">분기<span class="ar">▾</span></th>
      <th data-k="3">결산기<span class="ar">▾</span></th>
      <th data-k="5">업종<span class="ar">▾</span></th>
      <th data-k="6">거래소<span class="ar">▾</span></th>
    </tr></thead>
    <tbody id="tBody"></tbody>
  </table>
</div>

<div class="foot">
  <span id="srcLink"></span>
  __TLNOTE__ 일본 지명·인명 한자는 훈독이라(小田原=오다와라) 기계 변환이 틀릴 수 있습니다.
  점선이 그어진 이름이 기계 변환분이고, 마우스를 올리면 원문이 뜹니다.
  캘린더의 <b>원문 보기</b> 체크로 통째로 바꿔 볼 수도 있습니다.<br>
  발표일은 예정일이며 회사 사정으로 바뀔 수 있습니다.
  발표 시각은 <b>미국만</b> 원본에 있습니다(장전 BMO / 장후 AMC).
  일본은 대부분 장 마감 후 15시 전후, 홍콩은 이사회 당일 장 마감 후 공시입니다.<br>
  날짜는 각 시장의 <b>현지 날짜</b>입니다. 미국 장후 발표는 한국 시각으로 다음 날 새벽이 됩니다.<br>
  <span class="fl fl-hk" role="img" aria-label="홍콩"></span> 홍콩만 성격이 다릅니다. 미국·일본은 회사가 미리 신고한 <b>발표 예정일</b>이지만,
  홍콩은 그 제도가 약하고 거래소가 내던 이사회 캘린더도 없어져서
  <b>이미 공시된 실적</b>을 모읍니다. 즉 홍콩 탭에는 앞으로의 예정이 아니라
  지나간 발표가 실립니다.<br>
  <span id="gapNote"></span>
</div>
</div>

<div class="mdback" id="mdBack" hidden>
  <div class="md" role="dialog" aria-modal="true">
    <p class="mt" id="mdTitle"></p>
    <p class="ms" id="mdSub"></p>
    <dl id="mdList"></dl>
    <div id="mdFin"></div>
    <div class="mact">
      <a class="btn" id="mdLink1" target="_blank" rel="noopener">종목정보</a>
      <a class="btn" id="mdLink2" target="_blank" rel="noopener">공시</a>
      <button class="btn" id="mdClose">닫기 (ESC)</button>
    </div>
  </div>
</div>

<script id="payload" type="application/json">__DATA__</script>
<script>
/* ══════════════════════════════════════════════════════════════
   글로벌 실적발표 캘린더 — 렌더링
   행은 배열로 들어온다. 자리 뜻:
     0 날짜  1 코드  2 한글명  3 결산기  4 분기  5 업종  6 거래소
     7 원문  8 변환등급  9 시장(jp/us/hk)  10 발표시각  11 시총(십억$)
   종목 하나를 가리키는 열쇠는 코드가 아니라 '시장:코드' 다.
   일본 8035 와 홍콩 08035 는 다른 회사다.
   ══════════════════════════════════════════════════════════════ */
const D = JSON.parse(document.getElementById('payload').textContent);
const ROWS = D.rows, NOTE = D.notable;
const MKTS = D.markets, MKT = Object.fromEntries(MKTS.map(m => [m.id, m]));
/* 국기. **이모지가 아니라 그림이다** — 윈도우 기본 폰트는 국기 이모지를 못
   그려서 🇺🇸 가 그냥 'US' 라는 글자로 뜬다(맥에서는 국기로 보이니 만든 쪽은
   모르고 지나간다). 그림은 스타일시트에 한 벌만 담고 여기서는 클래스만 붙인다 —
   표에 만 줄이 넘게 들어가므로 줄마다 SVG 를 심으면 안 된다. */
const FL = m => '<span class="fl fl-' + m + '" role="img" aria-label="' +
                (MKT[m] ? MKT[m].ko : m) + '"></span>';
const LIVE = MKTS.filter(m => m.has).map(m => m.id);
const DOW = ['월','화','수','목','금','토','일'];

const keyOf = r => r[9] + ':' + r[1];
const noteOf = r => NOTE[keyOf(r)];

/* ── 한국 시간 ────────────────────────────────────────────────
   r[0]=현지 날짜, r[12]=한국 날짜, r[13]='HH:MM', r[14]=1이면 원본의 실제 시각.
   현지 날짜로 칸을 나누면 한국에서 볼 때 어긋난다 — 미국 장후 발표는
   한국 시각으로 다음 날 새벽이라, 현지 기준 '오늘'이 실제로는 내일이다.
   그래서 기본을 한국 시간으로 두고, 현지 시간으로 되돌리는 토글을 준다. */
let useKst = true;
const dateOf = r => (useKst && r[12]) ? r[12] : r[0];
const timeOf = r => (useKst && r[13]) ? r[13] + (r[14] ? '' : '경') : '';

/* 캘린더에서 이 발표가 놓일 칸.
   주말은 칸을 내주지 않는다 — 한 주에 열네 건 남짓 있을 뿐인데 칸 두 개가
   늘 비어 있어 화면만 넓어진다. 대신 **다음 월요일 칸**에 얹고 칩에
   「토」「일」을 붙여 실제로는 주말에 나온 것임을 밝힌다.
   (버크셔는 원래 토요일 아침에 발표한다. 없는 일로 만들면 안 된다.) */
const DOW_KO = ['월', '화', '수', '목', '금', '토', '일'];
function dowOf(d) { return (parse(d).getDay() + 6) % 7; }
function slotOf(r) {
  const d = dateOf(r), w = dowOf(d);
  return w < 5 ? d : addDays(d, 7 - w);
}

/* 보고 있는 시장. 여러 개를 동시에 켤 수 있다 — '미국+홍콩만' 같은 조합이 되도록.
   mkt 는 탭 하나만 켠 상태를 가리키는 값으로 남겨둔다(설명문·안내문이 이걸 본다). */
let picked = new Set(LIVE);
let mkt = '';                       // 딱 한 시장만 켜져 있으면 그 id, 아니면 ''
function syncMkt() {
  const on = [...picked];
  mkt = on.length === 1 ? on[0] : '';
}
/* 지금 켜 둔 시장들. 휴장·미수집·주목종목·막대가 전부 이걸 봐야 한다.
   'mkt 아니면 전부'로 두면 미국+홍콩만 켠 상태에서 일본 휴장이 그대로 뜬다 —
   보지도 않는 시장 때문에 "휴장"이라고 적히는 셈이라 거짓말이 된다. */
function onMkts() { return MKTS.map(m => m.id).filter(id => picked.has(id)); }

/* 지금 시장에 해당하는 행만. 시장을 바꿀 때마다 다시 만든다. */
let VIEW = [], byDate = new Map();
function reslice() {
  syncMkt();
  VIEW = picked.size === LIVE.length ? ROWS : ROWS.filter(r => picked.has(r[9]));
  byDate = new Map();
  for (const r of VIEW) {
    const d = slotOf(r);
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(r);
  }
  // 칸 안에서는 시총 큰 순. 접혀서 12개만 보일 때 큰 게 먼저 오게 한다.
  // 시총을 모르는 종목은 뒤로 민다 — 0으로 쳐서 섞으면 큰 회사가 밀린다.
  for (const list of byDate.values())
    list.sort((a, b) => (b[11] || -1) - (a[11] || -1) ||
                        (a[13] || '').localeCompare(b[13] || '') ||
                        a[1].localeCompare(b[1]));
}

/* 수집에 성공한 날 — 시장별로 따로 본다. 미국은 받았는데 일본은 못 받은 날이
   그냥 '발표 없음'으로 보이면 안 된다. */
const okSet = {};
for (const m of LIVE) okSet[m] = new Set(D.okDays[m] || []);

/* 홍콩은 '이미 나온 공시'만 모은다(D.pastOnly). 마지막으로 받은 날 뒤가
   비어 있는 건 못 받아서가 아니라 **아직 공시가 없어서**다. 그걸 '미수집'이라
   적으면 "홍콩은 왜 내일부터 수집을 안 하나"로 읽힌다. 그래서 그 구간은
   미수집에서 빼고 '아직 공시 전'이라고 따로 적는다. */
const PAST_ONLY = new Set(D.pastOnly || []);
const lastOk = {};
for (const m of LIVE) {
  const ds = D.okDays[m] || [];
  lastOk[m] = ds.length ? ds[ds.length - 1] : '';
}
const beyond = (m, d) => PAST_ONLY.has(m) && lastOk[m] && d > lastOk[m];

/* 그 날 아직 못 받은 시장들. 비어 있으면 구멍이 없다는 뜻. */
function missing(d) {
  return onMkts().filter(m => okSet[m] && !okSet[m].has(d) && !beyond(m, d));
}
/* 아직 공시가 나올 차례가 아닌 시장들. 구멍이 아니라 성격이다. */
function notYet(d) {
  return onMkts().filter(m => okSet[m] && !okSet[m].has(d) && beyond(m, d));
}
/* 그 날의 휴장 사정. all=true 면 보고 있는 시장이 전부 쉰다.
   전체 보기에서 일본만 쉬는 날을 '휴장'이라 적으면 거짓말이 된다 —
   미국은 그날 멀쩡히 연다. 그래서 누가 쉬는지를 같이 적는다. */
function holidayInfo(d) {
  const ms = onMkts();
  const hit = ms.filter(m => D.holidays[m] && D.holidays[m][d]);
  if (!hit.length) return { text: '', all: false };
  const label = [...new Set(hit.map(m => D.holidays[m][d]))].join(' · ');
  if (hit.length === ms.length) return { text: label + ' · 휴장', all: true };
  return { text: hit.map(m => MKT[m].ko).join('·') + ' 휴장 (' + label + ')', all: false };
}

/* 원문 사명과 사전 영문명이 같은 경우가 흔하다. 같은 이름을 두 번 쓰지 않는다. */
function altOf(r) {
  const nt = NOTE[keyOf(r)];
  return [...new Set([r[7], nt ? nt[1] : ''])].filter(s => s && s !== r[2]);
}

/* 관심종목(★ 담기)은 걷어냈다. 담아 봐야 하는 일이 없었고 — 칩·표·상세창·필터
   네 군데에 ★ 를 두느라 정작 회사 이름이 잘렸다. 브라우저에만 저장되던 것이라
   지운다고 서버에서 없어질 것도 없다. */

const pad = n => String(n).padStart(2, '0');
const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const parse = s => { const [y,m,d] = s.split('-').map(Number); return new Date(y, m-1, d); };
const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* r[2]=한글명, r[7]=원문, r[8]=변환등급(2 사전·1 단어사전·0 기계).
   '원문 보기'를 켜면 원문을 그대로 보여준다. 미국처럼 원문이 곧 한글명인
   (= 사전에 없어 영문을 그대로 쓴) 종목은 둘이 같으므로 한 번만 보인다. */
let showJp = false;
const nameOf = r => (showJp && r[7]) ? r[7] : r[2];
const bothOf = r => r[2] === r[7] ? r[2] : r[2] + ' · ' + r[7];

/* **오늘은 화면을 열 때 정한다.** 만들 때 박아 넣으면 날이 바뀌어도 다음 빌드가
   돌 때까지 어제로 남는다. 게다가 러너는 UTC 라, **한국 시각 0시~9시 사이에는
   언제나 어제**였다 — 13일 새벽에 열었더니 12일 주가 열려 있었다.
   여는 주도 여기서 다시 고른다. 그래야 빌드가 멈춰도 달력은 넘어간다. */
D.today = iso(new Date());
const mondayOf = s => {
  const d = parse(s);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return iso(d);
};
if (D.weeks && D.weeks.length) {
  const w = mondayOf(D.today);
  D.defaultWeek = D.weeks.includes(w) ? w : D.weeks.reduce((a, b) =>
    Math.abs(parse(b) - parse(D.today)) < Math.abs(parse(a) - parse(D.today)) ? b : a);
}

let week = D.defaultWeek;

/* ── 주 네비게이션 ────────────────────────────────────────── */
const wPick = document.getElementById('wPick');
/* 주별 건수는 시장을 바꾸면 달라지므로 매번 다시 채운다. */
function fillWeeks() {
  wPick.innerHTML = D.weeks.map(w =>
    '<option value="' + w + '">' + esc(fmtWeek(w)) + '  (' + countWeek(w) + '건)</option>'
  ).join('');
}
/* 한 주는 월~금 다섯 칸이다. 주말 발표는 slotOf 가 다음 월요일로 옮겨 놓았다. */
function weekDays(w) {
  const out = [];
  for (let i = 0; i < 5; i++) out.push(addDays(w, i));
  return out;
}
function countWeek(w) {
  return weekDays(w).reduce((s, d) => s + (byDate.get(d) || []).length, 0);
}
function fmtWeek(w) {
  const a = parse(w), b = parse(addDays(w, 4));
  return (a.getMonth()+1) + '/' + a.getDate() + ' ~ ' + (b.getMonth()+1) + '/' + b.getDate()
       + ' (' + a.getFullYear() + ')';
}
document.getElementById('wPrev').onclick = () => go(addDays(week, -7));
document.getElementById('wNext').onclick = () => go(addDays(week, 7));
document.getElementById('wToday').onclick = () => go(D.defaultWeek);
wPick.onchange = e => go(e.target.value);
function go(w) { week = w; renderAll(); }

/* ── 주간 캘린더 ──────────────────────────────────────────── */
const CHIP_LIMIT = 12;
const expanded = new Set();
/* 이 이상이면 칸에서 크게 띄운다. 10조원 — 수기 목록이 아니라 시총이 기준이다. */
const BIG_CAP = 10e12 / D.usdKrw / 1e9;

/* 시총은 십억 달러 단위로 들어온다. 한국식으로 억/조 달러로 고쳐 읽는다.
   1 십억 달러 = 10억 달러, 1000 십억 달러 = 1조 달러. */
function capKo(b) {
  if (!b) return '';
  const won = b * 1e9 * D.usdKrw;                      // 원으로도 같이 적는다
  const wonKo = won >= 1e12 ? (won / 1e12).toFixed(1) + '조원'
                            : Math.round(won / 1e8).toLocaleString() + '억원';
  const usd = b >= 1000 ? (b / 1000).toFixed(2) + '조 달러'
                        : Math.round(b * 10).toLocaleString() + '억 달러';
  return usd + ' (약 ' + wonKo + ')';
}

/* ── 규모 필터 ────────────────────────────────────────────────
   시총은 미국 소스에만 온다. 일본·홍콩은 원본에 없어서 거를 수가 없다.
   그 행들을 조용히 지워버리면 두 시장이 통째로 사라지므로 통과시키되,
   어느 시장에 적용되지 않는지 화면에 적는다. 없는 값을 지어내지 않는다. */
/* 어제. '발표일이 지났다'를 가르는 금이다. 오늘·어제는 장후 발표가 아직
   안 나왔을 수 있으므로 확실한 것으로 치지 않는다. */
/* `toISOString()` 을 쓰면 안 된다 — parse() 가 만든 것은 **현지 자정**이라
   UTC 로 옮기면 한국에서는 하루가 더 밀린다(어제가 그저께가 된다). iso() 로 적는다. */
const yesterday = (() => {
  const d = parse(D.today); d.setDate(d.getDate() - 1);
  return iso(d);
})();

const capSel = document.getElementById('fCap');
capSel.innerHTML = '<option value="0">전체 보기 (규모 무관)</option>' +
  D.capSteps.map(s => '<option value="' + s.usdB + '">시총 ' + s.jo + '조원 이상</option>').join('');
/* **1조원 이상을 기본으로 연다.** 전체를 열면 껍데기 회사가 화면을 뒤덮어
   정작 볼 회사가 안 보인다. 작은 회사를 보고 싶으면 '전체 보기'를 고르면 된다. */
const capDefault = (D.capSteps.find(s => s.jo === 1) || D.capSteps[0] || {}).usdB || 0;
capSel.value = String(capDefault);
const capMin = () => +capSel.value || 0;

/* 시총을 모르는 행을 어떻게 할 것인가 — 여기서 한 번 크게 뒤집혔다.
   전부 통과시키면 규모 필터를 켜도 껍데기 회사가 그대로 남는다. 그렇다고 전부
   지우면 회사가 조용히 사라진다 — 실제로 히로세전기(시총 8,828억엔)가 그렇게
   빠질 뻔했다.
   가르는 기준은 **시총이 어디서 왔는가**다. 미국은 나스닥이 캘린더와 함께 시총을
   주므로, 비어 있으면 정말 값이 없는 종목이다(SPAC·껍데기). 일본·홍콩은 따로
   받아 붙이는 거라 비어 있으면 '아직 못 받았다'는 뜻이다.
   수집률로 가르면 안 된다 — 일본 97.6%·홍콩 99.2%, 미국 85.7% 이라 거꾸로 된다. */
const CAP_INLINE = new Set(D.capInline || []);
const passCap = r => !capMin() ||
      (r[11] ? r[11] >= capMin() : !CAP_INLINE.has(r[9]));

/* 발표 시각 배지. 미국만 값이 있다. */
function timeTag(r) {
  if (!r[10]) return '';
  return '<span class="tm ' + (r[10] === '장전' ? 'pre' : 'post') + '">' + r[10] + '</span>';
}

/* ── 발표가 이미 나왔는가 ─────────────────────────────────────
   날짜로 어림잡지 않는다. 장후 발표는 예정일 저녁에야 나오므로 '날짜가 지났으니
   발표했겠지'로 치면 반나절을 틀린다. 대신 나스닥이 준 **실제 EPS 가 찍힌 분기**와
   그 행의 결산기를 맞춰본다. 숫자가 나왔으면 발표된 것이다.
   자료가 있는 건 미국뿐이라 일본·홍콩에는 배지를 달지 않는다 — 모르는 걸 안다고
   적지 않는다. (홍콩은 애초에 '이미 나온 공시'만 모으므로 전부 지나간 발표다.) */
const MON = { jan:1, feb:2, mar:3, apr:4, may:5, jun:6,
              jul:7, aug:8, sep:9, oct:10, nov:11, dec:12 };
/* '2026년 6월 분기' -> '2026-6' */
function fyKey(fy) {
  const m = /(\d{4})\D+(\d{1,2})\s*월/.exec(fy || '');
  return m ? m[1] + '-' + (+m[2]) : '';
}
/* 'Jun 2026' -> '2Q26'. 회원님이 보는 표기법으로 맞춘다 — 차트 축과 같은 말이어야
   "이 분기가 저 막대구나"가 바로 보인다. 나스닥이 주는 'Jun 2026' 은 분기말 달이라
   그 달이 속한 분기로 읽으면 된다. */
function epsQ(p) {
  const m = /([A-Za-z]{3})\D*(\d{4})/.exec(p || '');
  const n = m && MON[m[1].toLowerCase()];
  if (!n) return p || '';
  return Math.floor((n - 1) / 3 + 1) + 'Q' + String(m[2]).slice(2);
}
/* 'Jun 2026' -> '2026-6' */
function epsKey(p) {
  const m = /([A-Za-z]{3})\D*(\d{4})/.exec(p || '');
  const n = m && MON[m[1].toLowerCase()];
  return n ? m[2] + '-' + n : '';
}
function doneInfo(r) {
  if (r[9] !== 'us') return null;
  const f = D.fin['us:' + r[1]];
  if (!f || !f.eps || !f.eps.done) return null;
  const want = fyKey(r[3]);
  if (!want) return null;
  return f.eps.done.find(x => epsKey(x.period) === want) || null;
}

/* **발표일이 지났으면 발표된 것이다.**
   ✓ 는 '나스닥이 실제 EPS 를 실었나'로만 달고 있었다. 그런데 우리가 실적 수치를
   아직 안 받은 종목, 외국 기업처럼 나스닥이 EPS 를 안 주는 종목이 훨씬 많아서
   (지난주 미국 1,604건 중 ✓ 가 붙을 수 있는 건 832건뿐) **'우리가 모른다'와
   '발표 안 했다'가 화면에서 똑같이 보였다.** 지난 주를 열어 놓고 절반이 안 끝난
   것처럼 보이는 건 거짓말이다.

   그렇다고 날짜만으로 어림잡으면 안 된다 — 장후 발표는 예정일 저녁에야 나오므로
   당일에 '했겠지'로 치면 반나절을 틀린다. 그래서 **하루가 지난 날**만 확실한
   것으로 본다. 그 사이는 여전히 실제 EPS 가 있을 때만 ✓ 다. */
function pastDay(r) {
  const d = dateOf(r);
  return d && d < yesterday;
}

/* **방금 발표한 분기를 우리가 갖고 있나.**
   ✓ 를 나스닥 EPS(`eps.done`)로만 갈랐더니, 차트 수치는 멀쩡히 있는데 흐린 ✓ 가
   붙는 종목이 수두룩했다 — 나스닥은 외국 기업에 EPS 를 안 주고, 우리 수치는
   SEC·stockanalysis·TDnet 에서 따로 오기 때문이다. 눌러서 볼 게 있는데 "수치는
   아직"이라고 적는 건 거짓말이다.

   발표한 분기는 발표일 두어 달 앞에서 끝난다. 그러니 **우리가 가진 마지막 분기가
   발표일 100일 안쪽에서 끝났으면** 그 발표는 차트에 들어 있는 것이다. 시장마다
   결산기 표기가 달라도(일본 '3월 결산', 홍콩 영어 한 문장) 이 방법은 통한다. */
function haveNumbers(r) {
  const f = D.fin[keyOf(r)];
  if (!f || !f.last) return false;
  const d = parse(dateOf(r));
  d.setDate(d.getDate() - 100);
  return f.last >= d.toISOString().slice(0, 10);
}

/* EPS 가 '$1.85' 처럼 기호를 달고 올 때가 있다. 숫자만 뽑는다. */
function num(v) {
  const n = parseFloat(String(v == null ? '' : v).replace(/[^0-9.\-]/g, ''));
  return isFinite(n) ? n : null;
}
/* 예상 대비 몇 %. 예상이 0 이면 나눌 수 없으니 비운다. */
function surprise(a, c) {
  a = num(a); c = num(c);
  if (a === null || !c) return '';
  const p = ((a - c) / Math.abs(c)) * 100;
  return '<em class="' + (p >= 0 ? 'up' : 'dn') + '">' + (p >= 0 ? '▲' : '▼') +
         Math.abs(p).toFixed(0) + '%</em>';
}

function chip(r, big) {
  const k = keyOf(r), t = timeOf(r);
  const dn = doneInfo(r);
  // 발표가 끝났으면 ✓ 를 시각 자리에 넣는다. 칸을 하나 더 만들면 그만큼 회사 이름이
  // 잘려서, 정작 무슨 회사인지 안 보이게 된다.
  const past = pastDay(r);
  // 앞으로 발표할 것에 ✓ 를 달면 안 된다. 지난 분기 수치를 갖고 있다는 이유로
  // 다음 주 발표에 ✓ 가 붙으면 그게 제일 헷갈린다. 날짜가 지난 것만 본다.
  const got = dn || (past && haveNumbers(r));
  const badge = got
    ? '<span class="tm ok" title="실적이 나왔습니다. 눌러보세요.">✓' + (t ? ' ' + t : '') + '</span>'
    : past
    ? '<span class="tm over" title="발표일이 지났습니다. 수치는 아직 못 받았습니다.">✓</span>'
    : t ? '<span class="tm ' + (r[14] ? 'exact' : 'approx') + '">' + t + '</span>'
        : timeTag(r);
  // 주말 발표는 다음 월요일 칸에 얹혀 있다. 실제 요일을 칩에 적는다.
  const wd = dowOf(dateOf(r));
  const we = wd >= 5
    ? '<span class="we" title="' + dateOf(r) + ' (' + DOW_KO[wd] + ') 발표">' +
      DOW_KO[wd] + '</span>' : '';
  return '<button class="chip m-' + r[9] + (big ? ' big' : '') +
         (got ? ' done' : '') +
         '" data-key="' + esc(k) + '" data-date="' + dateOf(r) + '">' +
         (mkt ? '' : FL(r[9])) + we +
         '<span class="cd">' + esc(r[1]) + '</span>' +
         '<span class="cn' + (r[8] === 0 ? ' guess' : '') + '" title="' + esc(bothOf(r)) +
         '">' + esc(nameOf(r)) + '</span>' + badge +
         (r[11] ? '<span class="cc">' + capShort(r[11]) + '</span>' : '') +
         '</button>';
}

/* 칩에 넣을 짧은 시총 표기. 조원 단위로만 적는다. */
function capShort(b) {
  const jo = b * 1e9 * D.usdKrw / 1e12;
  return jo >= 100 ? Math.round(jo) + '조' : jo.toFixed(jo < 10 ? 1 : 0) + '조';
}

function renderCal() {

  /* 아직 한 번도 수집하지 않은 시장. 빈 칸 일곱 개를 늘어놓으면 '발표가 없는 주'로
     읽힌다. 그건 사실이 아니므로 칸 대신 사유를 낸다. */
  if (mkt && !MKT[mkt].has) {
    const cal = document.getElementById('cal');
    cal.className = 'cal none';
    cal.innerHTML = '<div class="nodata">' + FL(mkt) + ' ' + esc(MKT[mkt].ko) +
      ' 일정은 아직 수집하지 않았습니다.' +
      '<span>저장소에서 <b>python ' + esc(MKT[mkt].scraper) + '</b> 을 돌린 뒤 ' +
      '<b>python build.py</b> 로 다시 만들면 이 자리에 채워집니다.</span></div>';
    document.getElementById('wLabel').textContent = fmtWeek(week);
    document.getElementById('wSum').textContent = '미수집';
    wPick.value = D.weeks.includes(week) ? week : '';
    document.getElementById('wPrev').disabled = false;
    document.getElementById('wNext').disabled = false;
    return;
  }

  const shown = weekDays(week);
  const cal = document.getElementById('cal');
  cal.className = 'cal';

  let total = 0, bigTotal = 0;
  cal.innerHTML = shown.map(d => {
    // 규모 필터를 제일 먼저 건다. 칸 위의 건수도 걸러진 뒤 숫자여야
    // '이 날 몇 건 보이는지'와 맞는다.
    const list = (byDate.get(d) || []).filter(passCap);
    total += list.length;

    const dow = dowOf(d);
    const isToday = d === D.today;
    const hol = holidayInfo(d);
    const closed = hol.all && !list.length;

    // 시총 큰 순으로 이미 정렬돼 있다. 앞에서부터 자르면 큰 회사가 남는다.
    const key = week + d;
    const open = expanded.has(key);
    const shownList = open ? list : list.slice(0, CHIP_LIMIT);

    let body;
    if (!list.length) {
      let why = '';
      const miss = missing(d), soon = notYet(d);
      if (hol.text) why = '<span class="why">' + esc(hol.text) + '</span>';
      else if (miss.length) why = '<span class="why">미수집 구간 · ' +
        miss.map(m => MKT[m].ko).join('·') + '</span>';
      else if (soon.length) why = '<span class="why">' +
        soon.map(m => MKT[m].ko).join('·') + ' 아직 공시 전</span>';
      body = '<div class="empty">발표 없음' + why + '</div>';
    } else {
      const miss = missing(d), soon = notYet(d);
      // 시총 상위 몇 개는 크게, 나머지는 보통 크기로. 기준은 수기 목록이 아니라 시총이다.
      body = (hol.text ? '<div class="dsec gap">' + esc(hol.text) + '</div>' : '') +
             shownList.map((r, i) => chip(r, i < 3 && r[11] >= BIG_CAP)).join('') +
             (list.length > CHIP_LIMIT
               ? '<button class="more" data-key="' + key + '">' +
                 (open ? '접기' : '+' + (list.length - CHIP_LIMIT) + '개 더 보기') + '</button>'
               : '') +
             /* 한 시장은 받았고 다른 시장은 못 받은 날. 목록이 차 있어도
                다 받은 날처럼 보이면 안 된다. */
             (miss.length ? '<div class="dsec gap">' +
                miss.map(m => MKT[m].ko).join('·') + ' 미수집</div>' : '') +
             (soon.length ? '<div class="dsec gap">' +
                soon.map(m => MKT[m].ko).join('·') + ' 아직 공시 전</div>' : '');
    }

    return '<div class="day' + (isToday ? ' today' : '') + (closed ? ' closed' : '') + '">' +
      '<div class="dh"><span class="dow">' + DOW[dow] + '</span>' +
      '<span class="dnum">' + parse(d).getDate() + '</span>' +
      (isToday ? '<span class="todaytag">오늘</span>' : '') +
      '<span class="dcnt"><b>' + list.length + '</b>건</span></div>' +
      '<div class="dbody">' + body + '</div></div>';
  }).join('');

  document.getElementById('wLabel').textContent = fmtWeek(week);
  document.getElementById('wSum').textContent =
    total.toLocaleString() + '건' + (useKst ? ' · 한국 시간' : ' · 현지 시간');
  wPick.value = D.weeks.includes(week) ? week : '';

  const wi = D.weeks.indexOf(week);
  document.getElementById('wPrev').disabled = wi === 0;
  document.getElementById('wNext').disabled = wi === D.weeks.length - 1;
}

/* ── 주목종목 그룹 ────────────────────────────────────────── */
function renderGroups() {
  // 종목마다 한 줄만 남긴다. 오늘 이후 일정이 있으면 그 중 가장 이른 것,
  // 없으면 가장 최근 과거 일정. VIEW가 날짜 오름차순이라 한 번만 훑으면 된다.
  const first = new Map();
  for (const r of VIEW) {
    if (!noteOf(r)) continue;
    const k = keyOf(r), cur = first.get(k);
    if (!cur || (cur[0] < D.today && (r[0] >= D.today || r[0] > cur[0]))) first.set(k, r);
  }
  // 시장을 가로질러 보는 중이면 시장별로 묶어서 낸다. 테마 이름이 시장마다
  // 겹치므로(둘 다 '금융'이 있다) 한 통에 부으면 섞여버린다.
  const shownMkts = onMkts();
  let shown = 0, dictTotal = 0, html = '';
  for (const m of shownMkts) {
    const order = D.groupOrder[m] || [];
    const perGroup = {};
    for (const g of order) perGroup[g] = [];
    for (const [k, r] of first) {
      if (r[9] !== m) continue;
      const g = NOTE[k][2];
      if (perGroup[g]) perGroup[g].push(r);
    }
    const boxes = order.filter(g => perGroup[g].length).map(g => {
      const rs = perGroup[g].sort((a, b) => a[0] < b[0] ? -1 : 1);
      shown += rs.length;
      return '<div class="gbox m-' + m + '"><h3>' + esc(g) +
        '<span class="gn">' + rs.length + '종목</span></h3>' +
        rs.map(r => {
          const nt = NOTE[keyOf(r)], past = r[0] < D.today;
          // 칩·표와 같은 data-key/data-date 를 달아 둔다. 이벤트 위임이 그대로
          // 받아 같은 상세창을 연다 — 여기만 안 열리면 그게 더 헷갈린다.
          return '<div class="grow" data-key="' + esc(keyOf(r)) +
            '" data-date="' + r[0] + '"><span class="gc">' + esc(r[1]) + '</span>' +
            '<span class="gk">' + esc(nt[0]) +
            ' <span class="ge">' + esc(nt[1]) + '</span></span>' +
            '<span class="gd' + (past ? ' past' : '') + '">' + r[0].slice(5) +
            ' <span class="qtag">' + esc(r[4]) + '</span></span></div>';
        }).join('') + '</div>';
    }).join('');
    dictTotal += Object.keys(NOTE).filter(k => k.startsWith(m + ':')).length;
    if (!boxes) continue;
    if (!mkt) html += '<h3 class="gmkt m-' + m + '">' + FL(m) + ' ' +
                      esc(MKT[m].ko) + '</h3>';
    html += '<div class="groups">' + boxes + '</div>';
  }
  document.getElementById('groups').innerHTML =
    html || '<div class="empty">수집된 주목종목 일정이 없습니다.</div>';
  document.getElementById('gMeta').textContent =
    shown + '종목 / 사전 등재 ' + dictTotal + '종목';
}

/* ── 일자별 막대 ──────────────────────────────────────────── */
function renderBars() {
  const src = [...new Set(onMkts().flatMap(m => D.okDays[m] || []))].sort();
  const days = src.filter(d => (byDate.get(d) || []).length);
  if (!days.length) { document.getElementById('bars').innerHTML = ''; return; }
  const W = 1400, H = 260, PAD_L = 8, PAD_B = 46, PAD_T = 24;
  const n = days.length || 1;
  const bw = (W - PAD_L * 2) / n;
  const max = Math.max(...days.map(d => byDate.get(d).length), 1);
  const wk = new Set(weekDays(week));
  const parts = days.map((d, i) => {
    const v = byDate.get(d).length;
    const h = (H - PAD_B - PAD_T) * v / max;
    const x = PAD_L + i * bw, y = H - PAD_B - h;
    const label = d.slice(5).replace('-', '/');
    return '<g><rect class="b' + (wk.has(d) ? ' wk' : '') + '" x="' + (x + bw * .12).toFixed(1) +
      '" y="' + y.toFixed(1) + '" width="' + (bw * .76).toFixed(1) + '" height="' +
      Math.max(h, 1).toFixed(1) + '" data-date="' + d + '"><title>' + label + ' · ' +
      v + '건</title></rect>' +
      (v >= max * .45 ? '<text class="vl" x="' + (x + bw / 2).toFixed(1) + '" y="' +
        (y - 5).toFixed(1) + '" text-anchor="middle">' + v + '</text>' : '') +
      '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - PAD_B + 16) +
      '" text-anchor="end" transform="rotate(-60 ' + (x + bw / 2).toFixed(1) + ' ' +
      (H - PAD_B + 16) + ')">' + label + '</text></g>';
  }).join('');
  document.getElementById('bars').innerHTML = parts;
}

/* ── 전체 표 ──────────────────────────────────────────────── */
let sortKey = 0, sortDir = 1;
function renderTable() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const fs = document.getElementById('fSector').value;
  const fm = document.getElementById('fMarket').value;
  const fk = document.getElementById('fKind').value;
  const tb = document.getElementById('tBig').checked;
  const tf = document.getElementById('tFuture').checked;

  let list = VIEW.filter(r => {
    if (!passCap(r)) return false;
    if (fs && r[5] !== fs) return false;
    if (fm && r[6] !== fm) return false;
    if (fk && r[4] !== fk) return false;
    if (tb && !noteOf(r)) return false;
    if (tf && r[0] < D.today) return false;
    if (q) {
      const nt = noteOf(r), en = nt ? nt[1] : '';
      if (!(r[1] + r[2] + r[7] + en).toLowerCase().includes(q)) return false;
    }
    return true;
  });

  list.sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (x === y) return a[0] < b[0] ? -1 : 1;
    return (x < y ? -1 : 1) * sortDir;
  });

  const CAP = 600;
  document.getElementById('tBody').innerHTML = list.slice(0, CAP).map(r => {
    const k = keyOf(r), nt = NOTE[k];
    return '<tr class="m-' + r[9] + '" data-key="' + esc(k) + '" data-date="' + r[0] + '">' +
      '<td class="dim">' + r[0] + '</td>' +
      '<td class="mcell">' + FL(r[9]) + ' ' + esc(MKT[r[9]].ko) + '</td>' +
      '<td class="code' + (nt ? ' big' : '') + '">' + esc(r[1]) + '</td>' +
      '<td class="' + (r[8] === 0 ? 'guess' : '') + '">' + esc(r[2]) + '</td>' +
      '<td class="jp">' + (r[7] === r[2] ? '' : esc(r[7])) + '</td>' +
      '<td>' + timeTag(r) + '</td>' +
      '<td><span class="qtag' + (r[4] === '본결산' ? ' q4' : '') + '">' + esc(r[4]) + '</span></td>' +
      '<td class="dim">' + esc(r[3]) + '</td>' +
      '<td class="dim">' + esc(r[5]) + '</td>' +
      '<td class="dim">' + esc(r[6]) + '</td></tr>';
  }).join('');
  document.getElementById('tCnt').innerHTML =
    '<b>' + list.length.toLocaleString() + '</b>건' +
    (list.length > CAP ? ' 중 ' + CAP + '건 표시 (검색으로 좁혀보세요)' : '');
}

/* ── 상세 모달 ────────────────────────────────────────────── */
let mdKey = null;

/* 시장마다 볼 곳이 다르다. [종목정보 이름, URL, 공시 이름, URL] */
function links(m, code) {
  if (m === 'jp') return ['닛케이 종목정보', 'https://www.nikkei.com/nkd/company/?scode=' + code,
                          '적시공시', 'https://www.nikkei.com/nkd/company/kigyo/?scode=' + code];
  if (m === 'us') return ['나스닥 종목정보',
                          'https://www.nasdaq.com/market-activity/stocks/' + code.toLowerCase(),
                          'SEC 공시',
                          'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker=' +
                          code + '&type=10-&dateb=&owner=include&count=40'];
  return ['HKEX 종목정보',
          'https://www.hkex.com.hk/Market-Data/Securities-Prices/Equities/Equities-Quote?sym=' +
          String(code).replace(/^0+/, '') + '&sc_lang=en',
          '홍콩 공시(HKEXnews)',
          'https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en'];
}

function openModal(k, dt) {
  const r = (byDate.get(dt) || []).find(x => keyOf(x) === k) ||
            ROWS.find(x => keyOf(x) === k);
  if (!r) return;
  mdKey = k;
  const nt = NOTE[k], m = r[9], code = r[1];
  document.getElementById('mdTitle').innerHTML =
    FL(m) + ' ' + esc(r[2]) + ' (' + esc(code) + ')';
  document.getElementById('mdSub').textContent =
    altOf(r).concat(r[8] === 0 ? ['한글 표기는 기계 변환'] : []).join(' · ');
  const dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
  const dn = doneInfo(r);
  document.getElementById('mdList').innerHTML =
    '<dt>' + (dn ? '발표일' : '발표 예정일') + '</dt><dd>' +
    r[0] + ' (' + DOW[(parse(r[0]).getDay()+6)%7] + ') ' +
    (dd === 0 ? '· 오늘' : dd > 0 ? '· D-' + dd : '· ' + (-dd) + '일 전') +
    (dn ? ' <span class="donetag">✓ 발표 완료</span>' +
          (dn.actual ? ' <span class="dim">EPS ' + esc(String(dn.actual)) +
                       ' ' + surprise(dn.actual, dn.consensus) + '</span>' : '')
        : '') + '</dd>' +
    (r[10] ? '<dt>발표 시각</dt><dd>' + esc(r[10]) +
             (r[10] === '장전' ? ' (Before Open)' : ' (After Close)') + '</dd>' : '') +
    '<dt>분기</dt><dd>' +
      (dn ? '<b>' + esc(epsQ(dn.period)) + '</b> · ' : '') +
      esc([r[4], r[3]].filter(Boolean).join(' · ') || '—') + '</dd>' +
    (r[11] ? '<dt>시가총액</dt><dd>' + capKo(r[11]) + '</dd>' : '') +
    (r[5] ? '<dt>업종</dt><dd>' + esc(r[5]) + '</dd>' : '') +
    '<dt>시장</dt><dd>' + esc(MKT[m].ko) + (r[6] ? ' · ' + esc(r[6]) : '') + '</dd>' +
    (nt ? '<dt>테마</dt><dd>' + esc(nt[2]) + '</dd>' : '');
  const L = links(m, code);
  const a1 = document.getElementById('mdLink1'), a2 = document.getElementById('mdLink2');
  a1.textContent = L[0]; a1.href = L[1];
  a2.textContent = L[2]; a2.href = L[3];
  document.getElementById('mdFin').innerHTML = finBlock(m, code);
  document.getElementById('mdBack').hidden = false;
}

/* ── 실적 시계열 ──────────────────────────────────────────────
   매출 막대 + 영업이익률 선 + YoY. 수치는 SEC 가 받은 공식 재무제표다.
   미국 국내 기업만 분기가 있다 — 외국 기업(SEA·알리바바 등)은 SEC 에 연 1회만
   내므로 연간 막대가 나온다. 없는 분기를 지어내지 않고 그렇게 적는다. */
/* 통화별 자릿수. 엔·원은 자릿수가 커서 달러와 같은 눈금을 쓰면 못 읽는다.
   환산하지 않고 원래 통화 그대로 적는다 — 몇 년치를 오늘 환율로 환산하면
   매출 추세가 아니라 환율 추세가 된다. */
const CUR = {
  USD: '달러', JPY: '엔', HKD: '홍콩달러', CNY: '위안', EUR: '유로',
  GBP: '파운드', CHF: '스위스프랑', CAD: '캐나다달러', AUD: '호주달러',
  DKK: '덴마크크로네', SEK: '스웨덴크로나', NOK: '노르웨이크로네',
  BRL: '헤알', INR: '루피', KRW: '원', TWD: '대만달러', SGD: '싱가포르달러',
  MXN: '페소', ZAR: '란드', ILS: '셰켈', THB: '바트', IDR: '루피아',
};
/* 자릿수 눈금. 엔·원·위안은 억/조로 끊어야 읽히고, 달러·파운드는 B/M 이 익숙하다.
   모르는 통화는 달러식으로 끊고 통화 이름만 원문 그대로 적는다 — 지어내지 않는다. */
const CUR_BIG = { JPY: 1, KRW: 1, CNY: 1, TWD: 1, INR: 1, IDR: 1 };
const STEP_BIG = [[1e12, '조'], [1e8, '억'], [1e4, '만']];
const STEP_SM = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
function curOf(code) {
  const c = code || 'USD';
  return { ko: CUR[c] || c, steps: CUR_BIG[c] ? STEP_BIG : STEP_SM };
}
/* **무엇을 파는 회사인가.** 업종 이름만으로는 안 보인다 — 'ONEOK 에너지'라고
   적어봐야 뭘 파는지 모른다. 한국어 설명이 있으면 그걸 쓰고, 아직 안 쓴 종목은
   받아온 원문을 그대로 보인다. 원문은 영어·일본어라 「원문」이라고 적어 둔다. */
function bizLine(m, code) {
  const key = m + ':' + code;
  const ko = D.descKo && D.descKo[key];
  if (ko) return '<p class="biz">' + esc(ko) + '</p>';
  const raw = D.desc && D.desc[key];
  if (!raw) return '';
  return '<p class="biz raw"><span class="tagx">원문</span>' + esc(raw) + '</p>';
}

function finBlock(m, code) {
  const key = m + ':' + code;
  const f = D.fin[key], sg = D.seg[key], sh = D.segH && D.segH[key];
  const biz = bizLine(m, code);
  // 실적 수치가 없어도 부문은 있을 수 있다. 그때 통화를 'USD' 로 박아 두면
  // 일본 회사의 엔화 막대에 '단위: bil USD' 라고 적히는 거짓말이 된다.
  const MKT_CUR = { jp: 'JPY', hk: 'HKD' };
  if (!f) return biz + ((sg || sh)
    ? '<div class="finwrap">' + (sg ? segChart(sg, MKT_CUR[m] || 'USD') : '') +
      (sh ? segChart(sh, MKT_CUR[m] || 'USD') : '') + '</div>'
    : '<p class="finnote">이 종목은 아직 실적 수치를 받지 않았습니다. ' +
      '시가총액 큰 종목부터 채우는 중입니다.</p>');
  let html = biz;
  if (f.eps) {
    const u = f.eps.upcoming, d = f.eps.done.slice(-4);
    html += '<div class="epsrow">' +
      d.map(x => '<span class="epsbox"><b>' + esc(epsQ(x.period)) + '</b>' +
        'EPS ' + esc(String(x.actual)) +
        (x.consensus ? ' <i>(예상 ' + esc(String(x.consensus)) + ')</i>' : '') +
        surprise(x.actual, x.consensus) + '</span>').join('') +
      (u ? '<span class="epsbox next"><b>' + esc(epsQ(u.period)) + '</b>아직 발표 전' +
           (u.consensus ? ' <i>(예상 ' + u.consensus + ')</i>' : '') + '</span>' : '') +
      '</div>';
  }
  // **연간 막대는 그리지 않는다.** 연간으로는 "이번 분기가 작년 같은 분기보다
  // 나아졌나"를 볼 수 없어서 애초에 보려던 그림이 아니다. 분기(또는 홍콩 반기)만 낸다.
  const pts = (f.points || []).filter(() => f.freq === 'Q' || f.freq === 'H');
  if (pts.length >= 2) html += finChart(f);
  else if (f.freq === 'A')
    html += '<p class="finnote">이 종목은 <b>분기 실적을 못 구했습니다.</b> ' +
            '연 1회만 공시하는 회사이거나 아직 분기 자료를 받지 못한 경우입니다. ' +
            '(연간 수치는 추세를 볼 수 없어 싣지 않습니다.)</p>';
  else if (pts.length === 1) {
    const p = pts[0], U = curOf(f.cur);
    html += '<p class="finnote">받은 분기가 <b>' + esc(p[0]) + '</b> 하나뿐이라 ' +
            '추세를 그리지 못했습니다. (매출 ' + p[1].toLocaleString() + ' ' +
            esc(U.ko) + ')</p>';
  }
  // 사업부별은 매출·성장률 아래에 붙인다. 큰 그림을 먼저 보고 쪼개 보는 순서다.
  if (sg) html += segChart(sg, f.cur || 'USD');
  // 일본 연간 부문 이력(有報 기준). 분기 차트와 **섞지 않고** 아래에 따로 —
  // 연간 막대 옆에 반기 막대가 서면 높이가 거짓말이 된다.
  if (sh) html += segChart(sh, f.cur || 'USD');
  // 맨 아래 실적 브리핑 — 차트를 못 읽고 지나가도 요점은 남게.
  html += briefBlock(key, f, sg);
  return html || '<p class="finnote">받아둔 수치가 없습니다.</p>';
}

/* ── 실적 브리핑 ─────────────────────────────────────────────
   차트를 글로 읽어주는 칸. 캘린더이자 스크리닝 화면이라는 목적에서 왔다 —
   눌렀을 때 "얼마나, 어디가, 몇 분기째"가 한눈에 잡혀야 한다.

   **가진 수치에서만 만든다.** 매출·영업이익·부문(위 차트와 같은 자료)의 요약은
   기계가 만들고, 수치에 없는 것 — 왜 잘 됐는지, 회사가 뭐라고 했는지 — 은
   공시에서 수집·번역된 것이 있을 때만(D.briefs · D.fcst) 출처를 달아 낸다.
   없으면 그 줄이 안 나올 뿐, 지어내지 않는다.

   한국어 코멘트는 (종목, 분기) 열쇠다. 다음 분기가 나오면 자동으로 내려간다 —
   지난 분기 이야기가 새 분기 옆에 붙어 있는 것이 가장 나쁜 거짓말이라서다. */
function briefMoney(v, cur) {
  const U = curOf(cur);
  const a = Math.abs(v);
  const hit = U.steps.find(([n]) => a >= n);
  const num = hit ? (a / hit[0] >= 100 ? Math.round(v / hit[0]).toLocaleString()
                                       : (v / hit[0]).toFixed(1)) + hit[1]
                  : fmtN(v);
  return num + ' ' + U.ko;
}
const briefPct = r => (r >= 0 ? '+' : '') + Math.round(r * 100) + '%';
const briefCls = r => r >= 0 ? 'up' : 'dn';
function briefBlock(key, f, sg) {
  /* 숫자를 되읊지 않는다 — 매출·YoY·연속 증수 따위는 위 차트가 이미 말한다.
     회원님이 정확히 그렇게 짚었다: "이건 이미 차트로도 충분해. 이유를 알고
     싶은거야." 여기 서는 것은 차트에 없는 두 가지뿐이다.
       · 왜 잘/못 됐는지 — 공시(보도자료)가 설명한 이유를 옮긴 코멘트
       · 회사가 공시한 가이던스와 그 변화
     둘 다 없으면 칸 자체를 내지 않는다. */
  const lines = [];
  /* 회사 가이던스 — 일본 결산단신의 통기 예상. 회사가 공시한 수치다. */
  const fc = D.fcst && D.fcst[key];
  if (fc && (fc.rev || fc.opi)) {
    let s = '회사 통기 예상(공시): ';
    const parts = [];
    if (fc.rev) parts.push('매출 ' + briefMoney(fc.rev, (f && f.cur) || 'JPY'));
    if (fc.opi != null) parts.push('영업이익 ' + briefMoney(fc.opi, (f && f.cur) || 'JPY'));
    s += parts.join(' · ');
    if (fc.prevOpi && fc.opi != null) {
      const g = fc.opi / fc.prevOpi - 1;
      if (Math.round(g * 100))
        s += ' — 직전 예상 대비 영업이익 <b class="' + briefCls(g) + '">' +
             briefPct(g) + ' ' + (g > 0 ? '상향' : '하향') + '</b>';
      else s += ' — 직전 예상 유지';
    } else if (fc.prevRev && fc.rev) {
      const g = fc.rev / fc.prevRev - 1;
      if (Math.round(g * 100))
        s += ' — 직전 예상 대비 매출 <b class="' + briefCls(g) + '">' +
             briefPct(g) + ' ' + (g > 0 ? '상향' : '하향') + '</b>';
    }
    lines.push(s);
  }
  /* (6) 공시 원문에서 옮긴 한국어 코멘트.
     **그 종목의 가장 최근 발표에 대한 것일 때만 낸다.** 분기 라벨로 대조하면
     회계연도가 어긋난 회사에서 깨진다(AMAT 의 '3분기'가 우리 라벨로는 2Q26).
     발표일로 가른다 — 캘린더가 그 종목의 더 새로운 발표(오늘 이하)를 알고
     있으면 이 코멘트는 지난 분기 이야기이므로 내리지 않는다. */
  const br = D.briefs && D.briefs[key];
  if (br && br.ko && br.date) {
    let newest = '';
    ROWS.forEach(r => {
      if (keyOf(r) === key && r[0] <= D.today && r[0] > newest) newest = r[0];
    });
    const gap = newest ? (new Date(newest) - new Date(br.date)) / 864e5 : 0;
    if (gap <= 3)
      lines.push('<span class="brko">' + esc(br.ko) + '</span> <i>(' +
                 esc(br.date.slice(5).replace('-', '/')) + ' 발표 · 출처 ' +
                 esc(br.src || '실적 공시 보도자료') + ')</i>');
  }
  if (!lines.length) return '';
  return '<div class="brief"><h4>왜 이랬나 — 실적 브리핑</h4><ul>' +
    lines.map(l => '<li>' + l + '</li>').join('') + '</ul></div>';
}

const SRC_KO = { sec: 'SEC 공식 재무제표', sa: 'stockanalysis.com', yahoo: 'Yahoo Finance',
                 tdnet: 'TDnet 결산단신 (공식 공시)',
                 mix: 'SEC 공식 재무제표 + stockanalysis.com' };

/* 부문 색. 여덟이면 웬만한 회사는 덮는다. 그 이상은 되풀이한다. */
const SEG_COLORS = ['#5B9BD5', '#ED7D31', '#C0504D', '#4BA893', '#8E7CC3',
                    '#D6A02F', '#7BA7CC', '#B0736F'];

/* 사업부별 매출 — 쌓은 막대.
   "매출이 늘었다"보다 "어디서 늘었다"가 중요할 때가 있다. 로켓랩은 발사 서비스와
   우주 시스템이 따로 움직이고, SEA 는 쇼피·가레나·머니가 따로 논다.
   조각마다 숫자를 적되, 조각이 얇으면 글자가 삐져나오므로 생략한다. */
function segChart(sg, cur) {
  const pts = (sg.pts || []).slice(-22);
  if (pts.length < 2) return '';
  const names = sg.names || [];
  const tot = pts.map(r => r.slice(1).reduce((a, v) => a + (v || 0), 0));
  const maxRaw = Math.max(...tot, 1);
  const U = unitFor(maxRaw, cur);
  const sc = v => v / U.div;
  const rmax = niceMax(sc(maxRaw));

  const n = pts.length;
  const W = Math.max(880, n * 46), L = 66, R = 20, B = 42, T = 30, H = 340;
  const BASE = H - B, step = (W - L - R) / n;
  const cx = i => L + step * i + step / 2;
  const y = v => BASE - (BASE - T) * v / rmax;

  let body = '';
  pts.forEach((r, i) => {
    let acc = 0;
    names.forEach((nm, j) => {
      const v = r[j + 1];
      if (!v) return;
      const y0 = y(sc(acc)), y1 = y(sc(acc + v)), h = Math.max(y0 - y1, 0.6);
      const x = cx(i) - step * 0.34;
      body += '<rect x="' + x.toFixed(1) + '" y="' + y1.toFixed(1) +
        '" width="' + (step * 0.68).toFixed(1) + '" height="' + h.toFixed(1) +
        '" fill="' + SEG_COLORS[j % SEG_COLORS.length] + '"><title>' +
        esc(nm) + ' ' + fmtN(sc(v)) + '</title></rect>';
      // 조각이 얇으면 숫자가 삐져나온다. 넉넉할 때만 적는다.
      if (h >= 15)
        body += '<text class="vn seg" x="' + cx(i).toFixed(1) + '" y="' +
          (y1 + h / 2 + 4).toFixed(1) + '" text-anchor="middle">' + fmtN(sc(v)) + '</text>';
      acc += v;
    });
  });
  const axis = [0, rmax / 2, rmax].map(v =>
    '<line class="fz" x1="' + L + '" y1="' + y(v).toFixed(1) + '" x2="' + (W - R) +
    '" y2="' + y(v).toFixed(1) + '"/>' +
    '<text class="fx" x="' + (L - 8) + '" y="' + (y(v) + 4).toFixed(1) +
    '" text-anchor="end">' + fmtN(v) + '</text>').join('');
  const xlab = pts.map((r, i) =>
    '<text class="fx' + (i === n - 1 ? ' now' : '') + '" x="' + cx(i).toFixed(1) +
    '" y="' + (H - B + 18) + '" text-anchor="middle">' + r[0] + '</text>').join('');

  // 부문이 매출 전부를 설명하지 않는 회사가 흔하다(본사 몫·기타·조정). 그럴 때
  // 막대 높이를 총매출로 읽으면 틀린다. 얼마를 덮는지 적어둔다.
  // 사업부문이 없어 제품이나 지역으로 쪼갠 회사가 있다(애플의 영업부문은 지역).
  // 제목을 그대로 '사업부별'이라 쓰면 거짓말이라 축을 그대로 적는다.
  return '<div class="finhead sub">' + esc(sg.ax || '사업부') + '별 매출' +
    '<span class="dim">(단위: ' + esc(U.ko) +
    (sg.cov ? ' · 총매출의 ' + sg.cov + '%' : '') + ')</span></div>' +
    '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H + '" class="finsvg">' +
    axis + body + xlab + '</svg></div>' +
    '<div class="finlegend">' + names.map((nm, j) =>
      '<span class="lg" style="--c:' + SEG_COLORS[j % SEG_COLORS.length] + '">' +
      esc(nm) + '</span>').join('') + '</div>';
}

/* 숫자에서 단위를 뗀다. 막대마다 '억'·'B' 를 붙이면 자릿수가 눈에 안 들어온다.
   대신 눈금 하나를 골라 제목에 '(단위: bil JPY)' 로 한 번만 적는다.
   눈금은 제일 큰 값이 세 자리 이상 되는 것 중 가장 큰 것으로 고른다 —
   도요타는 bil JPY(13,525), 산리오는 mil JPY(55,500) 가 된다. */
const SCALES = [[1e12, 'tril'], [1e9, 'bil'], [1e6, 'mil'], [1e3, 'k'], [1, '']];
function unitFor(max, cur) {
  const hit = SCALES.find(([n]) => max / n >= 100) || SCALES[SCALES.length - 1];
  return { div: hit[0], ko: (hit[1] ? hit[1] + ' ' : '') + (cur || 'USD') };
}
const fmtN = v => {
  const a = Math.abs(v);
  return a === 0   ? '0'
       : a >= 100  ? Math.round(v).toLocaleString()
       : a >= 10   ? v.toFixed(1)
                   : v.toFixed(2);
};
/* 축 눈금은 딱 떨어지는 수로. 55,518 같은 값이 축에 적혀 있으면 읽는 데 방해만 된다. */
function niceMax(v) {
  const p = Math.pow(10, Math.floor(Math.log10(v || 1)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8]) if (p * m >= v) return p * m;
  return p * 10;
}
const pcN = v => (v * 100).toFixed(0) + '%';

function finChart(f) {
  /* 그림 둘.
       (1) 매출 막대(왼쪽 축) + 영업이익률 선(오른쪽 축)  — 한 그래프에 겹쳐 그린다
       (2) 매출 성장률(YoY)

     영업이익률 축은 **그 종목의 값 언저리로 좁게** 잡는다. 0~100% 로 넓게 잡으면
     15%~27% 짜리 등락이 한 줄로 눌려 보이질 않는다. 회원님이 보내준 그림도
     오른쪽 축이 0~18% 로 좁게 잡혀 있어서 마진 흐름이 읽힌다. */
  const all = f.points;
  const back = f.freq === 'H' ? 2 : 4;
  // 성장률은 자르기 전에 계산한다 — 자른 뒤 계산하면 앞 네 분기가 빈다.
  const yoyAll = all.map((p, i) => {
    const prev = all[i - back];
    return (prev && prev[1]) ? p[1] / prev[1] - 1 : null;
  });
  const N = 22;                       // 이보다 촘촘하면 숫자가 서로 겹친다
  const pts = all.slice(-N), yoy = yoyAll.slice(-N);
  const latest = pts.length ? pts[pts.length - 1][0] : '';

  const rmaxRaw = Math.max(...pts.map(p => p[1]), 1);
  const U = unitFor(rmaxRaw, f.cur);
  const sc = v => v / U.div;

  const n = pts.length;
  const W = Math.max(880, n * 46), L = 66, R = 60, B = 42;
  const step = (W - L - R) / n;
  const cx = i => L + step * i + step / 2;
  const xlab = (H) => pts.map((p, i) =>
    '<text class="fx' + (i === n - 1 ? ' now' : '') + '" x="' + cx(i).toFixed(1) +
    '" y="' + (H - B + 18) + '" text-anchor="middle">' + p[0] + '</text>').join('');

  /* ── (1) 매출 + 영업이익률 ───────────────────────────────── */
  const H1 = 330, T1 = 40, BASE1 = H1 - B;
  const rmax = niceMax(sc(rmaxRaw));
  const ry = v => BASE1 - (BASE1 - T1) * v / rmax;

  // 영업이익률 축을 먼저 정해 둔다 — 매출 숫자를 막대 위에 쓸지 안에 쓸지
  // 정하려면 선이 어디를 지나는지 알아야 한다.
  const opm = pts.map((p, i) => (p[2] != null && p[1]) ? [i, p[2] / p[1]] : null).filter(Boolean);
  let oy = null;
  if (opm.length >= 2) {
    // 적자 회사는 이익률이 한 분기만 -989% 로 튀기도 한다. 그걸 축에 그대로
    // 반영하면 나머지 스무 분기가 한 줄로 눌려 아무것도 안 보인다. 축은
    // -100%~100% 안쪽 값들로만 잡고, 벗어난 점은 가장자리에 붙이되 **숫자는
    // 실제 값을 적는다** — 눌러 담되 속이지는 않는다.
    const all = opm.map(o => o[1]);
    const inr = all.filter(v => v >= -1 && v <= 1);
    const vs = inr.length >= 2 ? inr : all;
    const mn = Math.min(...vs), mx = Math.max(...vs);
    const span = (mx - mn) || 0.04;
    const lo = mn - span * 0.45, hi = mx + span * 0.12;
    oy = v => BASE1 - (BASE1 - T1) * (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo);
    oy.lo = lo; oy.hi = hi;
  }
  const opmAt = {};
  opm.forEach(o => { opmAt[o[0]] = oy(o[1]); });

  let bars = '';
  pts.forEach((p, i) => {
    const v = sc(p[1]), h = Math.max(BASE1 - ry(v), 1), x = cx(i) - step * 0.34;
    // 이익률 선이 막대 꼭대기를 지나가는 자리에서는 숫자끼리 겹친다. 막대 안에
    // 넣어 보려 했더니 막대가 글자보다 좁아 잘렸다. 글자에 바탕색 테두리를 둘러
    // 무엇 위에 놓이든 읽히게 한다(paint-order).
    const oyy = opmAt[i];
    const ty = (oyy != null && Math.abs(oyy - ry(v)) < 20) ? ry(v) - 17 : ry(v) - 6;
    bars += '<rect class="fb" x="' + x.toFixed(1) + '" y="' + ry(v).toFixed(1) +
      '" width="' + (step * 0.68).toFixed(1) + '" height="' + h.toFixed(1) + '"/>' +
      '<text class="vn rev" x="' + cx(i).toFixed(1) +
      '" y="' + ty.toFixed(1) + '" text-anchor="middle">' + fmtN(v) + '</text>';
  });
  const rAxis = [0, rmax / 2, rmax].map(v =>
    '<line class="fz" x1="' + L + '" y1="' + ry(v).toFixed(1) + '" x2="' + (W - R) +
    '" y2="' + ry(v).toFixed(1) + '"/>' +
    '<text class="fx" x="' + (L - 8) + '" y="' + (ry(v) + 4).toFixed(1) +
    '" text-anchor="end">' + fmtN(v) + '</text>').join('');

  // 영업이익률 — 오른쪽 축. 값 언저리로 좁게 잡아야 등락이 보인다.
  let opmSvg = '', opmAxis = '';
  if (oy) {
    const lo = oy.lo, hi = oy.hi;
    opmSvg = '<path class="fl opm" d="' +
      opm.map((o, j) => (j ? 'L' : 'M') + cx(o[0]).toFixed(1) + ',' + oy(o[1]).toFixed(1)).join(' ') +
      '"/>' + opm.map(o =>
      '<circle class="dot opm" cx="' + cx(o[0]).toFixed(1) + '" cy="' + oy(o[1]).toFixed(1) + '" r="3"/>' +
      '<text class="vn opm" x="' + cx(o[0]).toFixed(1) + '" y="' + (oy(o[1]) - 9).toFixed(1) +
      '" text-anchor="middle">' + pcN(o[1]) + '</text>').join('');
    opmAxis = [lo, (lo + hi) / 2, hi].map(v =>
      '<text class="fx opm" x="' + (W - R + 8) + '" y="' + (oy(v) + 4).toFixed(1) + '">' +
      pcN(v) + '</text>').join('');
  }

  /* ── (2) 매출 성장률 (YoY) ───────────────────────────────── */
  const have = yoy.map((v, i) => v == null ? null : [i, v]).filter(Boolean);
  let yoySvg = '';
  if (have.length >= 2) {
    const H2 = 220, T2 = 32, BASE2 = H2 - B;
    const vs = have.map(o => o[1]);
    let lo = Math.min(0, ...vs), hi = Math.max(0, ...vs);
    const pad = (hi - lo) * 0.22 || 0.05;
    lo -= pad; hi += pad;
    const gy = v => BASE2 - (BASE2 - T2) * (v - lo) / (hi - lo);
    const ticks = (lo < 0 && hi > 0) ? [lo, 0, hi] : [lo, (lo + hi) / 2, hi];
    const axis = ticks.map(v =>
      '<line class="' + (v === 0 ? 'fzero' : 'fz') + '" x1="' + L + '" y1="' + gy(v).toFixed(1) +
      '" x2="' + (W - R) + '" y2="' + gy(v).toFixed(1) + '"/>' +
      '<text class="fx" x="' + (L - 8) + '" y="' + (gy(v) + 4).toFixed(1) +
      '" text-anchor="end">' + pcN(v) + '</text>').join('');
    yoySvg = '<div class="finhead sub">매출 성장률 <span class="dim">(YoY)</span></div>' +
      '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H2 + '" class="finsvg">' + axis +
      '<path class="fl yoy" d="' +
      have.map((o, j) => (j ? 'L' : 'M') + cx(o[0]).toFixed(1) + ',' + gy(o[1]).toFixed(1)).join(' ') +
      '"/>' + have.map(o =>
        '<circle class="dot yoy" cx="' + cx(o[0]).toFixed(1) + '" cy="' + gy(o[1]).toFixed(1) + '" r="3"/>' +
        '<text class="vn yoy" x="' + cx(o[0]).toFixed(1) + '" y="' + (gy(o[1]) - 9).toFixed(1) +
        '" text-anchor="middle">' + pcN(o[1]) + '</text>').join('') +
      xlab(H2) + '</svg></div>';
  }

  const per = f.freq === 'H' ? '반기' : '분기';
  const notes = [];
  // 반기만 내는 회사다. 홍콩에 특히 많지만 홍콩만은 아니므로 시장을 못박지 않는다.
  if (f.freq === 'H') notes.push('이 회사는 반기로만 공시합니다');
  if (pts.length < 8) notes.push('받을 수 있었던 건 ' + pts.length + '개뿐입니다');

  return '<div class="finwrap">' +
    '<div class="finhead">' + per + ' 매출 · 영업이익률' +
    '<span class="dim">(단위: ' + esc(U.ko) + ')</span>' +
    (latest ? '<span class="now">최신 ' + esc(latest) + '</span>' : '') +
    (notes.length ? '<span class="warn">' + notes.join(' · ') + '</span>' : '') + '</div>' +
    '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H1 + '" class="finsvg">' +
    rAxis + bars + opmSvg + opmAxis + xlab(H1) + '</svg></div>' +
    '<div class="finlegend"><span class="lg rev">매출 (왼쪽)</span>' +
    '<span class="lg opm">영업이익률 (오른쪽)</span></div>' +
    yoySvg +
    '<div class="finlegend"><span class="lg yoy">매출 성장률 (YoY)</span>' +
    '<span class="src">출처 ' + SRC_KO[f.src || 'sec'] + '</span></div></div>';
}
function closeModal() { document.getElementById('mdBack').hidden = true; mdKey = null; }
document.getElementById('mdClose').onclick = closeModal;
document.getElementById('mdBack').onclick = e => {
  if (e.target.id === 'mdBack') closeModal();
};
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

/* ── 폰에서 화면 폭 고르기 ─────────────────────────────────────
   기본은 PC 화면 그대로(폭 1440 고정)다. 한 주를 나란히 놓고 보는 것이 이 화면의
   전부인데, 폰 폭에 맞추면 캘린더가 세로로 쌓여 그게 없어진다.
   다만 글씨가 작아지므로 되돌릴 길을 남긴다. 버튼은 **좁은 기기에서만** 보인다 —
   screen.width 는 viewport 를 고정해도 기기 폭 그대로라 이 판단에 쓸 수 있다. */
const fitBtn = document.getElementById('fitBtn');
function fitMode() {
  try { return localStorage.getItem('esFit') === 'mobile' ? 'mobile' : 'pc'; }
  catch (e) { return 'pc'; }
}
if (screen.width < 900) {
  fitBtn.hidden = false;
  const paint = () => {
    fitBtn.textContent = fitMode() === 'pc' ? '📱 폰 화면에 맞추기' : '🖥 PC 화면으로 보기';
  };
  paint();
  fitBtn.onclick = () => {
    const next = fitMode() === 'pc' ? 'mobile' : 'pc';
    try { localStorage.setItem('esFit', next); } catch (e) {}
    document.getElementById('vp').content =
      next === 'mobile' ? 'width=device-width, initial-scale=1' : 'width=1440';
    paint();
  };
}

/* ── 이벤트 위임 ──────────────────────────────────────────── */
document.addEventListener('click', e => {
  const more = e.target.closest('.more');
  if (more) {
    const k = more.dataset.key;
    expanded.has(k) ? expanded.delete(k) : expanded.add(k);
    renderCal();
    return;
  }
  const chk = e.target.closest('[data-mchk]');
  if (chk) { toggleMarket(chk.dataset.mchk); return; }
  const tab = e.target.closest('.mtab');
  if (tab) { setMarket(tab.dataset.mkt); return; }

  const chipEl = e.target.closest('.chip');
  if (chipEl) {
    openModal(chipEl.dataset.key, chipEl.dataset.date);
    return;
  }
  const tr = e.target.closest('#tBody tr');
  if (tr) { openModal(tr.dataset.key, tr.dataset.date); return; }

  const grow = e.target.closest('.grow');
  if (grow) { openModal(grow.dataset.key, grow.dataset.date); return; }

  const bar = e.target.closest('rect.b');
  if (bar) {
    const d = parse(bar.dataset.date);
    go(iso(new Date(d.getFullYear(), d.getMonth(), d.getDate() - ((d.getDay()+6)%7))));
    document.getElementById('cal').scrollIntoView({ behavior:'smooth', block:'center' });
  }
});

document.querySelectorAll('#tAll thead th[data-k]').forEach(th => {
  th.onclick = () => {
    const k = +th.dataset.k;
    sortDir = (k === sortKey) ? -sortDir : 1;
    sortKey = k;
    document.querySelectorAll('#tAll thead th').forEach(x => x.classList.remove('asc','desc'));
    th.classList.add(sortDir === 1 ? 'asc' : 'desc');
    th.querySelector('.ar').textContent = sortDir === 1 ? '▴' : '▾';
    renderTable();
  };
});

/* 업종·거래소·분기 후보는 지금 보고 있는 시장의 데이터에서 뽑는다.
   일본 업종 36종을 미국 탭에서 고르게 두면 아무것도 안 걸린다. */
function fillFilters() {
  const opts = i => [...new Set(VIEW.map(r => r[i]).filter(Boolean))].sort();
  for (const [id, arr, all] of [['fSector', opts(5), '전체 업종'],
                                ['fMarket', opts(6), '전체 거래소'],
                                ['fKind', opts(4), '전체 분기']]) {
    const sel = document.getElementById(id), keep = sel.value;
    sel.innerHTML = '<option value="">' + all + '</option>' +
      arr.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
    if (arr.includes(keep)) sel.value = keep;
    sel.hidden = !arr.length;
  }
}
for (const id of ['fSector','fMarket','fKind'])
  document.getElementById(id).onchange = renderTable;

/* 규모 필터는 캘린더·표에 함께 걸리므로 전체를 다시 그린다. */
capSel.onchange = () => { expanded.clear(); renderAll(); };

/* 규모 필터가 무엇을 감췄고 무엇을 통과시켰는지 — **화면에는 안 낸다.**
   회원님이 뜻을 알고 계셔서 지우라고 하셨다(그 줄 하나가 캘린더 위를 다 먹었다).
   대신 콘솔에 남긴다. 조용히 없애지는 않는다 — 왜 걸렀는데 아직 많은지,
   왜 껍데기 회사가 남아 있는지 따질 자리는 있어야 한다.
   개발자 도구 콘솔에서 capReport() 를 치면 그때 숫자가 나온다. */
function capReport() {
  if (!capMin()) return '규모 필터가 꺼져 있습니다.';
  const shown = onMkts(), hid = {}, thru = {};
  for (const r of ROWS) {
    if (!shown.includes(r[9]) || r[11]) continue;
    const box = CAP_INLINE.has(r[9]) ? hid : thru;
    (box[r[9]] = box[r[9]] || new Set()).add(r[1]);
  }
  return shown.map(m =>
    MKT[m].ko + ' 숨김 ' + (hid[m] ? hid[m].size : 0) +
    '종목 · 시총 미수집이라 통과 ' + (thru[m] ? thru[m].size : 0) + '종목').join(' / ');
}
function renderCapNote() {}
document.getElementById('q').oninput = renderTable;
for (const id of ['tBig','tFuture']) document.getElementById(id).onchange = renderTable;
/* 한국 시간으로 보면 미국 장후 발표가 다음 날 칸으로 옮겨간다.
   날짜 묶음 자체가 달라지므로 다시 자른 뒤 전부 그린다. */
document.getElementById('kstToggle').onchange = e => {
  useKst = e.target.checked; expanded.clear(); reslice(); fillWeeks(); renderAll();
};
document.getElementById('jpToggle').onchange = e => { showJp = e.target.checked; renderCal(); };

/* ── 시장 탭 ──────────────────────────────────────────────── */
/* 탭을 누르면 그 시장만 켠다. 여러 시장을 같이 보려면 체크박스를 쓴다. */
function setMarket(m) {
  picked = m ? new Set([m]) : new Set(LIVE);
  refresh();
}
function toggleMarket(m) {
  picked.has(m) ? picked.delete(m) : picked.add(m);
  if (!picked.size) picked = new Set(LIVE);   // 전부 끄면 아무것도 안 보이므로 되돌린다
  refresh();
}
function refresh() {
  expanded.clear(); reslice(); fillFilters(); fillWeeks(); renderAll();
}
function renderTabs() {
  const all = picked.size === LIVE.length;
  const tabs = [{ id: '', ko: '전체', n: ROWS.length, has: true, on: all }]
    .concat(MKTS.map(m => ({ id: m.id, ko: m.ko, n: m.count,
                             has: m.has, on: !all && picked.has(m.id) })));
  document.getElementById('mtabs').innerHTML = tabs.map(t =>
    '<div class="mtab m-' + (t.id || 'all') + (t.on ? ' on' : '') +
    (t.has ? '' : ' empty') + '" data-mkt="' + t.id + '">' +
    (t.id ? '<input type="checkbox" class="mchk" data-mchk="' + t.id + '"' +
            (picked.has(t.id) ? ' checked' : '') + (t.has ? '' : ' disabled') +
            ' title="여러 시장을 같이 보려면 체크하세요">' : '') +
    (t.id ? FL(t.id) : '<span class="gl">🌐</span>') + esc(t.ko) +
    '<span class="n">' + (t.has ? t.n.toLocaleString() + '건' : '미수집') + '</span></div>'
  ).join('');

  // 캘린더 옆에도 같은 것을 둔다. 같은 data-mchk 를 쓰므로 어느 쪽을 눌러도 같다.
  document.getElementById('calMkts').innerHTML = MKTS.map(m =>
    '<button class="mp' + (picked.has(m.id) ? ' on' : '') + '" data-mchk="' + m.id + '"' +
    (m.has ? '' : ' disabled title="아직 수집하지 않았습니다"') + '>' +
    FL(m.id) + ' ' + esc(m.ko) + '</button>').join('');
}

/* ── 요약 카드 ────────────────────────────────────────────── */
function renderCards() {
  const days = [...byDate.keys()].filter(d => byDate.get(d).length);
  const busiest = days.reduce((a, d) =>
    byDate.get(d).length > (a ? byDate.get(a).length : 0) ? d : a, '');
  const big = VIEW.filter(noteOf).length;
  const cards = [
    ['수집 발표', VIEW.length.toLocaleString(), ''],
    ['발표일 수', days.length.toLocaleString(), ''],
    ['주목종목 발표', big.toLocaleString(), ''],
    ['최다 발표일', busiest
      ? busiest + ' · ' + byDate.get(busiest).length.toLocaleString() + '건' : '—', 'sm'],
  ];
  document.getElementById('cards').innerHTML = cards.map(c =>
    '<div class="card"><div class="k">' + c[0] + '</div>' +
    '<div class="v ' + c[2] + '">' + c[1] + '</div></div>').join('');

  const cur = mkt ? MKT[mkt] : null;
  document.getElementById('calMeta').textContent = cur
    ? cur.ko + ' 상장사 — ' + cur.note
    : '미국 · 일본 상장사 발표 예정 + 홍콩 공시';
}

/* ── 출처와 수집 구멍 ─────────────────────────────────────── */
/* 수집 구간에 구멍이 있으면 숨기지 않고 적는다. 빈 칸이 '발표가 없는 날'인지
   '아직 못 받은 날'인지 구분되지 않으면 캘린더를 믿을 수 없다. */
function renderFoot() {
  document.getElementById('srcLink').innerHTML =
    '출처 ' + D.sources.map(s => FL(s.mkt) + ' ' + esc(s.name) +
      ' <a href="' + esc(s.url) + '" target="_blank" rel="noopener">↗</a>').join(' · ') +
    '<br>';

  let out = '';
  for (const m of LIVE) {
    const ds = D.okDays[m];
    if (!ds || !ds.length) continue;
    const gaps = [];
    for (let d = ds[0]; d <= ds[ds.length - 1]; d = addDays(d, 1)) {
      if (!okSet[m].has(d)) gaps.push(d);
    }
    if (gaps.length) {
      out += FL(m) + ' ' + MKT[m].ko + ' 미수집 ' + gaps.length + '일 (' +
        (gaps.length > 12 ? gaps.slice(0, 12).join(', ') + ' 외 ' + (gaps.length - 12) + '일'
                          : gaps.join(', ')) + ')<br>';
    }
  }
  for (const m of MKTS.filter(x => !x.has)) {
    out += FL(m.id) + ' ' + m.ko + ' — 아직 수집하지 않았습니다. <b>python ' +
           m.scraper + '</b> 을 돌리면 채워집니다.<br>';
  }
  if (out) out += '캘린더에는 <b>미수집 구간</b>으로 표시됩니다.<br>';

  // 홍콩이 '내일부터 텅 빈' 이유. 못 받은 게 아니라 아직 나오지 않은 것이다.
  for (const m of LIVE) {
    if (!PAST_ONLY.has(m) || !lastOk[m] || !picked.has(m)) continue;
    // 시장 이름(일본·미국·홍콩)은 셋 다 받침으로 끝나므로 조사는 '은'이다.
    out += FL(m) + ' ' + MKT[m].ko + '은 <b>이미 나온 공시</b>를 모읍니다. ' +
      lastOk[m] + ' 까지가 지금 가진 전부이고, 그 뒤는 못 받은 게 아니라 ' +
      '<b>아직 공시가 없는 것</b>입니다. 회사가 발표하면 그날 채워집니다.<br>';
  }
  document.getElementById('gapNote').innerHTML = out;
}

/* ── 종목 바로 찾기 ────────────────────────────────────────────
   아래 표의 검색칸(#q)과 하는 일이 다르다. 그쪽은 **보고 있는 것 안에서** 거른다 —
   탭이 미국이면 일본 회사는 안 나오고, 규모 필터에 걸린 회사도 안 나온다.
   여기 것은 **가진 종목 전부**에서 찾아 곧장 그 회사 창을 연다.

   한 종목이 여러 날에 걸릴 수 있으므로(분기마다 한 줄) 종목당 한 줄만 보이고,
   **오늘에 가장 가까운 발표**를 대표로 쓴다 — 지금 궁금한 건 대개 그것이다. */
const FIND = (() => {
  const best = new Map();
  for (const r of ROWS) {
    const k = keyOf(r), cur = best.get(k);
    if (!cur || Math.abs(parse(r[0]) - parse(D.today)) <
                Math.abs(parse(cur[0]) - parse(D.today))) best.set(k, r);
  }
  return [...best.entries()].map(([k, r]) => {
    const nt = NOTE[k];
    return { k, r, cap: r[11] || 0,
             // 찾는 말: 코드 · 한글명 · 원문 · 주목종목 영문명
             hay: (r[1] + ' ' + r[2] + ' ' + (r[7] || '') + ' ' + (nt ? nt[1] : ''))
                    .toLowerCase(),
             code: r[1].toLowerCase(), name: (r[2] || '').toLowerCase() };
  });
})();

const fq = document.getElementById('fq'), fqList = document.getElementById('fqList');
let fqHits = [], fqAt = -1;

/* 코드가 딱 맞는 것 -> 이름이 그 말로 시작하는 것 -> 그냥 들어 있는 것.
   같은 등급이면 시총 큰 쪽. '소니' 를 쳤을 때 소니그룹이 먼저 와야 한다. */
function fqRank(x, q) {
  if (x.code === q) return 0;
  if (x.code.startsWith(q)) return 1;
  if (x.name.startsWith(q)) return 2;
  return x.hay.includes(q) ? 3 : -1;
}
function fqSearch(q) {
  q = q.trim().toLowerCase();
  if (q.length < 1) return [];
  const out = [];
  for (const x of FIND) {
    const g = fqRank(x, q);
    if (g >= 0) out.push([g, -x.cap, x]);
  }
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return out.slice(0, 8).map(t => t[2]);
}
function fqDraw() {
  if (!fqHits.length) {
    fqList.innerHTML = fq.value.trim()
      ? '<div class="fqnone">찾는 종목이 없습니다. 캘린더에 실린 기간 밖이거나 ' +
        '아직 수집하지 않은 종목일 수 있습니다.</div>' : '';
    fqList.hidden = !fq.value.trim();
    return;
  }
  fqList.innerHTML = fqHits.map((x, i) => {
    const r = x.r, dd = Math.round((parse(r[0]) - parse(D.today)) / 86400000);
    return '<div class="fqi' + (i === fqAt ? ' on' : '') + '" data-i="' + i + '">' +
      FL(r[9]) +
      '<span class="fqn">' + esc(r[2]) + '</span>' +
      '<span class="fqc">' + esc(r[1]) +
        (r[7] && r[7] !== r[2] ? ' · ' + esc(r[7]) : '') + '</span>' +
      '<span class="fqd">' + r[0] +
        (dd === 0 ? ' · 오늘' : dd > 0 ? ' · D-' + dd : ' · ' + (-dd) + '일 전') +
      '</span></div>';
  }).join('');
  fqList.hidden = false;
}
function fqClose() { fqList.hidden = true; fqAt = -1; }

/* 고른 종목의 창을 연다. 캘린더도 그 주로 옮겨 둔다 — 창을 닫았을 때
   엉뚱한 주가 남아 있으면 어디를 보고 있었는지 알 수 없다.
   그 시장이 꺼져 있으면 켜 준다. 안 그러면 옮겨 간 주가 비어 보인다. */
function fqOpen(i) {
  const x = fqHits[i];
  if (!x) return;
  const r = x.r, m = r[9];
  if (!picked.has(m)) { picked = new Set([m]); refresh(); }
  const w = mondayOf(slotOf(r));
  if (D.weeks.includes(w) && w !== week) go(w);
  fq.value = ''; fqClose(); fq.blur();
  openModal(x.k, slotOf(r));
}

fq.oninput = () => { fqHits = fqSearch(fq.value); fqAt = fqHits.length ? 0 : -1; fqDraw(); };
fq.onfocus = () => { if (fq.value.trim()) { fqHits = fqSearch(fq.value); fqDraw(); } };
fq.onkeydown = e => {
  if (e.key === 'Escape') { fq.value = ''; fqClose(); fq.blur(); return; }
  if (!fqHits.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    fqAt = (fqAt + (e.key === 'ArrowDown' ? 1 : fqHits.length - 1)) % fqHits.length;
    fqDraw();
  } else if (e.key === 'Enter') {
    e.preventDefault();
    fqOpen(fqAt < 0 ? 0 : fqAt);
  }
};
fqList.onmousedown = e => {          // click 이면 blur 가 먼저 와서 목록이 닫힌다
  const it = e.target.closest('.fqi');
  if (it) { e.preventDefault(); fqOpen(Number(it.dataset.i)); }
};
document.addEventListener('click', e => {
  if (!e.target.closest('.find')) fqClose();
});
/* 어디서든 '/' 를 누르면 검색칸으로 간다. 글자를 치던 중이면 그대로 둔다. */
document.addEventListener('keydown', e => {
  if (e.key === '/' && !/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) {
    e.preventDefault(); fq.focus(); fq.select();
  }
});

function renderAll() {
  renderTabs(); renderCards(); renderCapNote();
  renderCal(); renderGroups(); renderBars(); renderTable();
}
reslice(); fillFilters(); fillWeeks(); renderFoot(); renderAll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()
