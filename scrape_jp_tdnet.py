# -*- coding: utf-8 -*-
"""
일본 '이미 발표한' 실적 — TDnet 공시 목록에서 직접 받는다.

닛케이는 **앞으로의 예정만** 준다. 회사가 발표를 마치면 그 줄은 목록에서 빠지고,
과거 날짜를 넣어 다시 받아도 0건이 온다(2026-05-01 부터 69일치를 되받아 봤는데
전부 0건이었다). 그래서 발표를 이미 끝낸 회사는 **다음 분기 일정이 잡힐 때까지
캘린더에서 사라진다** — 트레져팩토리(3093)가 7월 10일에 1분기를 발표했는데
10월까지 사이트 어디에도 없었다.

게다가 닛케이는 데이터센터 IP 를 막는다. CI 에서는 첫 요청부터 껍데기가 와서
`EARNINGS_GIVE_UP=1` 로 곧장 접게 해두었고, 그 결과 앞으로의 일정에도 구멍이 난다.

TDnet 은 반대다. 회사가 발표하는 **그 순간** 결산단신이 올라오고, 목록은 한 달쯤
남는다. 미국을 SEC 에서 받듯 일본은 TDnet 이다. 다만 **남는 기간이 한 달쯤이라
그보다 앞선 발표는 이 경로로도 못 받는다** — 3093 은 사흘 차이로 창을 놓쳤다.

  목록  https://www.release.tdnet.info/inbs/I_list_{쪽:03d}_{YYYYMMDD}.html

`scrape_fin_jp.py` 가 같은 목록을 훑지만 하는 일이 다르다 — 그쪽은 zip 을 받아
**수치**를 뽑고, 여기는 목록 줄만 읽어 **캘린더 한 줄**을 만든다. 나눠 둔 이유는
워크플로가 다르기 때문이다(캘린더는 collect.yml, 수치는 numbers.yml — 같은 파일에
둘이 쓰지 않도록 갈라놓은 규칙). 목록 쪽은 HTML 한 장이라 값이 싸고, 받아둔 날은
캐시로 건너뛰므로 평소에는 이틀치만 다시 받는다.

**여기서 오는 것은 '지나간 발표'다.** 홍콩과 같은 성격이라 앞으로의 일정을 주지
않는다. 앞일은 여전히 닛케이 몫이고, 둘을 합쳐야 캘린더가 온전해진다.

**같은 목록에서 월차(月次)도 같이 건진다.** 일본 회사 상당수가 분기 실적과 별개로
매달 매출·KPI 를 적시공시로 낸다(2026-09 기준 30영업일에 208건·161곳). 목록을
어차피 전 줄 훑으므로 **요청을 한 번도 더 하지 않고** `data/monthly_jp.json` 에
따로 담는다 — 남의 서버를 두 배로 두드릴 이유가 없다(부문을 결산단신 zip 에서
같이 뽑는 것과 같은 규칙).

  python scrape_jp_tdnet.py            # 최근 45일까지 두드려 본다
  python scrape_jp_tdnet.py 21         # 최근 21일만
  python scrape_jp_tdnet.py --probe    # 응답 생김새만 떠보기
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "earnings_jp_past.json"
# 월차는 캘린더 줄이 아니라 따로 쌓는다 — 성격도 화면 자리도 다르다.
MONTH_OUT = HERE / "data" / "monthly_jp.json"

# 담는 형식이 바뀌면 올린다. 그래야 받아둔 헌 기록을 버리고 다시 받는다.
# 3: 같은 목록에서 월차(月次)를 같이 건지기 시작했다. 캐시된 날은 다시 파싱할
#    길이 없으므로 한 번 통째로 다시 받아 월차 쪽을 채운다.
TDNET_VER = 3
MONTH_VER = 1

LIST_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day}.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# TDnet 이 목록을 남겨두는 기간. 공식적으로는 31일인데 실제로 어디까지 주는지는
# 두드려 봐야 안다. 넉넉히 잡고 **없다고 답하는 날은 그냥 건너뛴다** — 0건으로
# 적으면 '그날 발표가 없었다'는 거짓말이 되고, 미수집으로 적으면 영영 못 받을 날을
# 구멍이라고 계속 광고하게 된다. 한 번 사라진 날은 다시 나타나지 않으므로
# `gone` 에 적어 두고 다음 실행부터는 두드리지도 않는다.
BACK_DAYS = int(os.environ.get("JP_TDNET_BACK_DAYS", "45"))
# 최근 며칠은 캐시를 무시하고 다시 받는다. 그날 안에도 공시가 계속 붙기 때문이다.
FRESH_DAYS = 2
PAUSE = 0.5
BACKOFF = (0, 15, 45, 120)
GIVE_UP_AFTER = int(os.environ.get("JP_TDNET_GIVE_UP", "3"))
MAX_PAGE = 40

# 결산단신만 쓴다. 「業績予想の修正」·「訂正」 은 실적 발표가 아니다.
TANSHIN = re.compile(r"決算短信")
NOT_TANSHIN = re.compile(r"予想|修正|訂正|延期|中止|取消")

# ── 월차(月次) 가려내기 ─────────────────────────────────────────────
# **제목에 통일된 형식이 없다.** 실측한 36건이 전부 제각각이었다 —
# 「2026年８月月次に関するお知らせ」(7685) · 「月次リユース売上高（速報）」 ·
# 「事業KPI 2026年9月期 8月度月次情報」. 그래서 한 가지 꼴을 잡는 대신
# **'月次' 같은 강한 낱말** 아니면 **'N월' + 매출/실적 낱말**로 가른다.
#
# 라운드원(4680)이 이 규칙을 만들게 한 회사다. 「第47期（2027年３月期）８月の
# 売上の状況に関するお知らせ（速報）」 — 월차 공시인데 제목에 '月次' 가 아예
# 없다. '月次' 만 찾던 시절에는 통째로 새어나갔다.
MON_STRONG = re.compile(r"月次|月商|月例|月別")
MON_MONTH = re.compile(r"(\d{1,2})\s*月(?!期)")
MON_SALES = re.compile(r"売上|販売|受注|業績|実績|概況|速報|稼働|来店|客数|入場|発電電力量|取扱")
# 결산단신·정정·예상수정은 월차가 아니다. 특히 「業績予想の修正」 은 '業績' 이
# 들어 있어 위 낱말 규칙에 걸리므로 반드시 먼저 쳐내야 한다.
MON_SKIP = re.compile(r"決算短信|決算説明|訂正|有価証券報告書|質疑応答|業績予想|配当予想|補足説明")

# 보고 대상 달. 「８月度」 → 8월, 「2027年5月期」 의 5월은 결산기말이지 보고
# 달이 아니므로 `(?!期)` 로 피한다. 「月度」·「月分」 을 먼저 보는 것도 같은
# 이유다 — 「2027年5月期 月次売上（8月度）」 에서 8을 집어야 한다.
PER_DO = re.compile(r"(\d{1,2})\s*月度")
PER_BUN = re.compile(r"(\d{1,2})\s*月分")

# 목록 표의 한 줄. 클래스 이름이 둘씩 붙어 있다(`oddnew-M kjTitle`) —
# `class="kjTitle"` 로 잡으면 한 건도 안 걸린다(scrape_fin_jp.py 에서 겪었다).
ROW_RE = re.compile(
    r'<td class="[^"]*kjTime"[^>]*>(?P<time>[^<]*)</td>\s*'
    r'<td class="[^"]*kjCode"[^>]*>(?P<code>[^<]*)</td>\s*'
    r'<td class="[^"]*kjName"[^>]*>(?P<name>[^<]*)</td>\s*'
    r'<td class="[^"]*kjTitle"[^>]*>(?P<titlecell>.*?)</td>',
    re.S)
TAGS = re.compile(r"<[^>]+>")
# 첨부(PDF) 주소. 목록 줄 안에 상대 경로로 들어 있다(`140120260917537893.pdf`).
# 결산단신 쪽은 zip 을 따로 받으므로 안 쓰지만, 월차는 **이 PDF 가 알맹이 전부**라
# 반드시 건져야 한다. 화면에서 회원님이 눌러 원문을 여는 링크가 된다.
HREF = re.compile(r'href="([^"]+)"')
DOC_BASE = "https://www.release.tdnet.info/inbs/"

# 제목에서 결산기와 분기를 읽는다. 닛케이와 같은 표기로 맞춰야 build.py 가
# 두 소스를 한 형식으로 다룬다(fy='3月期', kind='第１').
#
#   2026年3月期 第1四半期決算短信〔日本基準〕（連結）
#   2026年２月期 第２四半期（中間期）決算短信〔日本基準〕（非連結）
#   2026年12月期 決算短信〔IFRS〕（連結）          <- 분기 표기가 없으면 본결산
FY_RE = re.compile(r"(\d{4}|[０-９]{4})年\s*([0-9０-９]{1,2})\s*月期")
Q_RE = re.compile(r"第\s*([0-9０-９])\s*四半期")
# 「中間期決算短信」처럼 분기 번호 없이 중간결산이라고만 적는 회사가 있다.
MID_RE = re.compile(r"中間期?決算短信")

ZEN = str.maketrans("０１２３４５６７８９", "0123456789")

# TDnet 은 회사명 앞에 시장 구분을 한 글자로 붙인다 — `Ｐ－八光オート`(프라임),
# `Ｇ－インフォメティス`(그로스). 회사 이름이 아니므로 떼어낸다.
SECT_PREFIX = re.compile(r"^[ＰＳＧＥEPSG][－\-]\s*")


class Throttled(Exception):
    """막혔다. '그날 공시가 없다'와 다른 일이다."""


def get(url, timeout=30):
    """404 는 None(그런 쪽이 없다), 그 밖의 실패는 Throttled."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise Throttled(str(e))


def parse_title(title: str):
    """제목 -> (fy, kind). 못 읽으면 ('', '') — 짐작해 채우지 않는다."""
    t = title.translate(ZEN)
    fy = ""
    m = FY_RE.search(t)
    if m:
        fy = f"{int(m.group(2))}月期"
    q = Q_RE.search(t)
    if q:
        kind = "第" + "１２３４５"[int(q.group(1)) - 1] if q.group(1) in "12345" else ""
    elif MID_RE.search(t):
        kind = "第２"                       # 중간결산 = 2분기 누계
    else:
        kind = "本"                         # 분기 표기가 없으면 통기(본결산)
    return fy, kind


def is_monthly(title: str) -> bool:
    """월차 공시인가. 실측 양성 36건·음성 15건으로 맞춰 둔 규칙이다."""
    t = title.translate(ZEN)
    if MON_SKIP.search(t):
        return False
    if MON_STRONG.search(t):
        return True
    return bool(MON_MONTH.search(t) and MON_SALES.search(t))


def monthly_period(title: str, ann: str):
    """(보고 대상 달 'YYYY-MM', 제목에서 읽었나).

    제목에 달이 없는 공시가 있다(「月次リユース売上高（速報）」). 그때는 발표일의
    **전달**로 어림하고 그렇다고 적어 둔다 — 이 저장소가 발표 시각에 쓰는
    `시각정확도` 와 같은 규칙이다. 어림한 것을 사실처럼 적지 않는다.
    """
    t = title.translate(ZEN)
    m = PER_DO.search(t) or PER_BUN.search(t) or MON_MONTH.search(t)
    a = date.fromisoformat(ann)
    if m and 1 <= int(m.group(1)) <= 12:
        mo = int(m.group(1))
        # 발표는 늘 그 달이 끝난 뒤다. 달이 발표월보다 크면 지난해 것이다
        # (1월에 내는 12월분).
        return f"{a.year if mo <= a.month else a.year - 1:04d}-{mo:02d}", 1
    prev = a.replace(day=1) - timedelta(days=1)
    return f"{prev.year:04d}-{prev.month:02d}", 0


def listing(day: str, page: int):
    """하루치 목록 한 쪽. 코드는 다섯 자리로 오고 **앞 넉 자**가 종목 코드다."""
    txt = get(LIST_URL.format(page=page, day=day.replace("-", "")))
    if txt is None:
        return None                          # 그런 쪽이 없다 = 더 볼 것이 없다
    out = []
    for m in ROW_RE.finditer(txt):
        d = m.groupdict()
        href = HREF.search(d["titlecell"])
        out.append({
            "time": d["time"].strip(),
            "code": d["code"].strip()[:4],
            "name": SECT_PREFIX.sub(
                "", TAGS.sub("", d["name"]).replace("　", " ").strip()),
            "title": TAGS.sub("", d["titlecell"]).replace("　", " ").strip(),
            "doc": href.group(1) if href else "",
        })
    return out


def fetch_day(day: str, probe: bool = False):
    """그날 목록을 한 번 훑어 **결산단신**과 **월차**를 함께 건진다.

    돌려주는 것은 `(결산단신 줄, 월차 줄)`. 첫 쪽부터 없으면 `None` —
    TDnet 이 그날을 더는 안 준다는 뜻이다. '발표가 0건인 날'과 구별해야 한다.
    0건으로 적으면 거짓말이 된다.
    """
    got, page, seen = [], 1, set()
    mon, mon_seen = [], set()
    while page <= MAX_PAGE:
        rows = listing(day, page)
        if rows is None:
            if page == 1:
                return None
            break
        if probe:
            print(f"  {day} {page}쪽 · 줄 {len(rows)}")
            for r in rows[:5]:
                print(f"    {r['time']} {r['code']} {r['name']} | {r['title'][:60]}")
        if not rows:
            break
        for r in rows:
            # 월차는 결산단신과 **다른 줄**이라 같은 반복문에서 따로 담는다.
            if r["code"] and is_monthly(r["title"]) and r["code"] not in mon_seen:
                mon_seen.add(r["code"])
                per, pok = monthly_period(r["title"], day)
                mon.append({
                    "date": day,
                    "time": r["time"] if re.fullmatch(r"\d{2}:\d{2}", r["time"]) else "",
                    "code": r["code"],
                    "name": r["name"],
                    "period": per,
                    # 1 이면 제목에서 읽은 달, 0 이면 발표일에서 어림한 달이다.
                    "pok": pok,
                    "title": r["title"],
                    "doc": DOC_BASE + r["doc"] if r["doc"] else "",
                })
            if not TANSHIN.search(r["title"]) or NOT_TANSHIN.search(r["title"]):
                continue
            if not r["code"] or r["code"] in seen:
                continue          # 같은 회사가 연결·비연결로 두 번 내는 경우가 있다
            fy, kind = parse_title(r["title"])
            seen.add(r["code"])
            got.append({
                "date": day,
                "code": r["code"],
                "name": r["name"],
                "fy": fy,
                "kind": kind,
                # TDnet 은 **실제 공시 시각**을 준다. 일본에서 시각을 아는 건
                # 이 경로뿐이다 — 닛케이 예정에는 시각이 없어 15시로 어림한다.
                "time": r["time"] if re.fullmatch(r"\d{2}:\d{2}", r["time"]) else "",
                "sector": "",
                "market": "",
                "title": r["title"],
            })
        page += 1
        time.sleep(PAUSE)
    return got, mon


def load_cache():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}, set()
    if old.get("v") != TDNET_VER:
        print(f"  담는 형식이 바뀌었다(v{old.get('v')} -> v{TDNET_VER}). 처음부터 다시 받는다.")
        return {}, set()
    by_day = {d: [] for d in old.get("ok_days", [])}
    for r in old.get("rows", []):
        by_day.setdefault(r["date"], []).append(r)
    return by_day, set(old.get("gone", []))


def load_monthly():
    """월차 캐시. 결산단신 쪽과 판을 따로 센다 — 한쪽 형식이 바뀌었다고
    다른 쪽까지 버릴 이유가 없다."""
    try:
        old = json.loads(MONTH_OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if old.get("v") != MONTH_VER:
        return {}
    by_day = {}
    for r in old.get("rows", []):
        by_day.setdefault(r["date"], []).append(r)
    return by_day


def save_monthly(by_day: dict):
    """**쌓아 두고 지우지 않는다.** TDnet 목록은 한 달쯤만 남으므로 창 밖으로
    밀려난 달은 어느 길로도 다시 못 받는다. 화면의 월별 이력은 여기 쌓인 것이
    전부다 — 돌수록 길어진다(홍콩 부문 비중 스냅샷과 같은 이치).
    """
    days = sorted(by_day)
    rows = [r for d in days for r in by_day[d]]
    codes = {r["code"] for r in rows}
    payload = {
        "v": MONTH_VER,
        "source": "TDnet 적시공시 — 월차(月次)",
        "source_url": "https://www.release.tdnet.info/inbs/I_main_00.html",
        "note": ("결산단신을 훑는 그 목록에서 같이 건진다(요청 추가 없음). "
                 "첨부 PDF 는 TDnet 이 한 달쯤만 두므로 옛 링크는 끊긴다."),
        "count": len(rows),
        "companies": len(codes),
        "range": [days[0], days[-1]] if days else [],
        "rows": rows,
    }
    MONTH_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = MONTH_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MONTH_OUT)
    return len(rows), len(codes)


def save(by_day: dict, gone: set):
    ok_days = sorted(by_day)
    rows = [r for d in ok_days for r in by_day[d]]
    payload = {
        "v": TDNET_VER,
        "source": "TDnet 적시공시 (결산단신 — 이미 발표된 실적)",
        "source_url": "https://www.release.tdnet.info/inbs/I_main_00.html",
        "range": [ok_days[0], ok_days[-1]] if ok_days else [],
        "count": len(rows),
        "ok_days": ok_days,
        "per_day": {d: len(by_day[d]) for d in ok_days},
        # TDnet 이 더는 안 주는 날. 다시 두드리지 않으려고 적어 둔다.
        # 이 날들은 '수집 성공'도 '미수집'도 아니다 — 받을 길이 없는 날이다.
        "gone": sorted(gone),
        "rows": rows,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return len(rows), len(ok_days)


def main(back_days: int, probe: bool = False):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()

    if probe:
        for back in range(3):
            got = fetch_day((today - timedelta(days=back)).isoformat(), probe=True)
            if got:
                print(f"  -> 결산단신 {len(got[0])}건 · 월차 {len(got[1])}건")
                for r in got[1]:
                    flag = "" if r["pok"] else "  (달은 발표일에서 어림)"
                    print(f"     {r['code']} {r['name'][:14]} [{r['period']}]"
                          f" {r['title'][:44]}{flag}")
        return

    by_day, gone = load_cache()
    mon_day = load_monthly()
    failed, streak, expired = [], 0, 0
    fresh_from = today - timedelta(days=FRESH_DAYS)

    # 오래된 쪽부터 훑는다. 도중에 접혀도 새 날짜가 남게 하려면 반대가 낫지만,
    # 캐시가 있으면 헌 날은 어차피 건너뛰므로 순서대로 두는 편이 읽기 쉽다.
    for back in range(back_days, -1, -1):
        day = today - timedelta(days=back)
        key = day.isoformat()
        if key in by_day and day < fresh_from:
            continue
        if key in gone:
            continue
        try:
            got = fetch_day(key)
        except Throttled as e:
            failed.append(key)
            streak += 1
            print(f"{key} 실패: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("연속 실패 — 여기서 멈춘다. 다시 돌리면 이어서 받는다.",
                      file=sys.stderr, flush=True)
                break
            time.sleep(BACKOFF[min(streak, len(BACKOFF) - 1)])
            continue
        streak = 0
        if got is None:
            # TDnet 이 그날을 더는 안 준다. 0건으로 적으면 '발표가 없었다'는
            # 거짓말이 되고, 미수집으로 적으면 받을 길 없는 날을 구멍이라고
            # 계속 광고하게 된다. 둘 다 아니므로 따로 적어 두고 넘어간다.
            gone.add(key)
            expired += 1
            save(by_day, gone)
            continue
        rows, mon = got
        # 0건도 '수집 성공'으로 남긴다. 그래야 '발표 없는 날'과 '못 받은 날'이
        # 구분된다 — 세 스크래퍼가 다 같은 규칙이다.
        by_day[key] = rows
        # **월차는 빈 날이라고 지우지 않는다.** 이미 받아둔 날을 0건으로
        # 덮으면 창 밖으로 밀려나 다시 못 받을 이력이 사라진다.
        if mon or key not in mon_day:
            mon_day[key] = mon
        save(by_day, gone)
        save_monthly(mon_day)
        print(f"{key} {len(rows):>4}건" + (f" · 월차 {len(mon)}건" if mon else ""),
              flush=True)

    n, days = save(by_day, gone)
    mn, mc = save_monthly(mon_day)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    print(f"월차 {mn}건 / {mc}개사 -> {MONTH_OUT}")
    if expired:
        print(f"TDnet 이 더는 안 주는 날 {expired}일 (오늘 확인분). "
              f"목록에 남는 기간을 넘어선 것이라 받을 길이 없다.")
    if failed:
        print(f"미수집 {len(failed)}일: {failed[0]} ~ {failed[-1]} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    main(int(a[0]) if a else BACK_DAYS, "--probe" in sys.argv)
