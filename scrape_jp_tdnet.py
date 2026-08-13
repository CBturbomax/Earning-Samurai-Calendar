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

# 담는 형식이 바뀌면 올린다. 그래야 받아둔 헌 기록을 버리고 다시 받는다.
TDNET_VER = 1

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

# 목록 표의 한 줄. 클래스 이름이 둘씩 붙어 있다(`oddnew-M kjTitle`) —
# `class="kjTitle"` 로 잡으면 한 건도 안 걸린다(scrape_fin_jp.py 에서 겪었다).
ROW_RE = re.compile(
    r'<td class="[^"]*kjTime"[^>]*>(?P<time>[^<]*)</td>\s*'
    r'<td class="[^"]*kjCode"[^>]*>(?P<code>[^<]*)</td>\s*'
    r'<td class="[^"]*kjName"[^>]*>(?P<name>[^<]*)</td>\s*'
    r'<td class="[^"]*kjTitle"[^>]*>(?P<titlecell>.*?)</td>',
    re.S)
TAGS = re.compile(r"<[^>]+>")

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


def listing(day: str, page: int):
    """하루치 목록 한 쪽. 코드는 다섯 자리로 오고 **앞 넉 자**가 종목 코드다."""
    txt = get(LIST_URL.format(page=page, day=day.replace("-", "")))
    if txt is None:
        return None                          # 그런 쪽이 없다 = 더 볼 것이 없다
    out = []
    for m in ROW_RE.finditer(txt):
        d = m.groupdict()
        out.append({
            "time": d["time"].strip(),
            "code": d["code"].strip()[:4],
            "name": TAGS.sub("", d["name"]).replace("　", " ").strip(),
            "title": TAGS.sub("", d["titlecell"]).replace("　", " ").strip(),
        })
    return out


def fetch_day(day: str, probe: bool = False):
    """그날 올라온 **결산단신**만 캘린더 줄로 만든다.

    첫 쪽부터 없으면 `None` — TDnet 이 그날을 더는 안 준다는 뜻이다.
    '발표가 0건인 날'과 구별해야 한다. 0건으로 적으면 거짓말이 된다.
    """
    got, page, seen = [], 1, set()
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
    return got


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
            fetch_day((today - timedelta(days=back)).isoformat(), probe=True)
        return

    by_day, gone = load_cache()
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
            rows = fetch_day(key)
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
        if rows is None:
            # TDnet 이 그날을 더는 안 준다. 0건으로 적으면 '발표가 없었다'는
            # 거짓말이 되고, 미수집으로 적으면 받을 길 없는 날을 구멍이라고
            # 계속 광고하게 된다. 둘 다 아니므로 따로 적어 두고 넘어간다.
            gone.add(key)
            expired += 1
            save(by_day, gone)
            continue
        # 0건도 '수집 성공'으로 남긴다. 그래야 '발표 없는 날'과 '못 받은 날'이
        # 구분된다 — 세 스크래퍼가 다 같은 규칙이다.
        by_day[key] = rows
        save(by_day, gone)
        print(f"{key} {len(rows):>4}건", flush=True)

    n, days = save(by_day, gone)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    if expired:
        print(f"TDnet 이 더는 안 주는 날 {expired}일 (오늘 확인분). "
              f"목록에 남는 기간을 넘어선 것이라 받을 길이 없다.")
    if failed:
        print(f"미수집 {len(failed)}일: {failed[0]} ~ {failed[-1]} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    main(int(a[0]) if a else BACK_DAYS, "--probe" in sys.argv)
