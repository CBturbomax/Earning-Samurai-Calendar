# -*- coding: utf-8 -*-
"""
일본 결산발표(決算発表) 스케줄 스크래퍼 — 출처: 日本経済新聞 決算発表スケジュール (QUICK 제공)
https://www.nikkei.com/markets/kigyo/money-schedule/kessan/

이 페이지는 날짜 '범위'를 못 받는다. SearchDate1=YYYY年MM, SearchDate2=DD 로
하루씩만 조회되고, 결과는 50건 단위 페이징(hm=1,2,3...)이다.
그래서 날짜 루프 × 페이지 루프 두 겹으로 긁는다.

결과: data/earnings.json
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = "https://www.nikkei.com/markets/kigyo/money-schedule/kessan/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

OUT = Path(__file__).parent / "data" / "earnings.json"

# 닛케이는 연속 요청 30~40건쯤에서 본문 없는 껍데기를 돌려주기 시작하고,
# 한 번 걸리면 꽤 오래 안 풀린다. 그래서 물러서는 폭을 넉넉히 잡았다.
BACKOFF = (0, 15, 45, 120, 300)

# 한 행이 통째로 <tr class="tr2"> ... </tr>. 셀 8개를 순서대로 뽑는다.
ROW_RE = re.compile(r'<tr class="tr2">(.*?)</tr>', re.S)
CELL_RE = re.compile(r'<t[hd][^>]*>(.*?)</t[hd]>', re.S)
TAG_RE = re.compile(r'<[^>]+>')
CNT_RE = re.compile(r'(\d+)～(\d+)件目を表示\(全(\d+)件\)')
ZERO_RE = re.compile(r'での検索結果：0件')


def clean(html: str) -> str:
    """셀 안의 태그·엔티티·전각공백을 걷어낸다."""
    t = TAG_RE.sub("", html)
    t = (t.replace("&nbsp;", " ").replace("　", " ")
          .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
          .replace("&quot;", '"').replace("&#39;", "'"))
    return " ".join(t.split())


def fetch(y: int, m: int, d: int, page: int, retries: int = 3) -> str:
    q = urllib.parse.urlencode({
        "ResultFlag": "1", "kwd": "", "KessanMonth": "",
        "SearchDate1": f"{y}年{m:02d}", "SearchDate2": f"{d:02d}",
        "Gcode": "", "hm": page,
    })
    req = urllib.request.Request(BASE + "?" + q, headers={
        "User-Agent": UA,
        "Accept-Language": "ja,en;q=0.8",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == retries - 1:
                raise
            # 잠깐 물러섰다 재시도. 상대 서버를 두들기지 않는다.
            time.sleep(2.0 * (attempt + 1))
            print(f"    retry {attempt+1} ({e})", file=sys.stderr)
    return ""


def parse_rows(html: str):
    out = []
    for block in ROW_RE.findall(html):
        cells = [clean(c) for c in CELL_RE.findall(block)]
        if len(cells) < 8:
            continue
        d, code, name, _info, fy, kind, sector, market = cells[:8]
        if not re.match(r"^\d{4}/\d{1,2}/\d{1,2}$", d):
            continue
        y, mo, dy = (int(x) for x in d.split("/"))
        out.append({
            "date": f"{y:04d}-{mo:02d}-{dy:02d}",
            "code": code,
            "name": name,
            "fy": fy,        # 3月期 / 12月期 ...
            "kind": kind,    # 第１ / 第２ / 第３ / 本
            "sector": sector,
            "market": market,
        })
    return out


class Throttled(Exception):
    """레이트리밋에 걸리면 닛케이는 본문 없는 껍데기 페이지를 돌려준다.
    이걸 '0건'으로 삼키면 발표가 있는 날이 조용히 빈 날이 되어버린다."""


def fetch_valid(y: int, m: int, d: int, page: int) -> str:
    """건수 표시나 '0件' 문구 중 하나는 반드시 있어야 정상 응답이다."""
    for wait in BACKOFF:
        if wait:
            print(f"    throttled, {wait}s 대기 후 재시도", file=sys.stderr, flush=True)
            time.sleep(wait)
        html = fetch(y, m, d, page)
        if CNT_RE.search(html) or ZERO_RE.search(html):
            return html
    raise Throttled(f"{y}-{m:02d}-{d:02d} p{page}: 유효 응답 실패")


def scrape_day(day: date, sleep: float = 1.5):
    html = fetch_valid(day.year, day.month, day.day, 1)
    if ZERO_RE.search(html):
        return []

    rows = parse_rows(html)
    m = CNT_RE.search(html)
    total = int(m.group(3)) if m else len(rows)

    page = 2
    while len(rows) < total and page <= 40:
        time.sleep(sleep)
        rows += parse_rows(fetch_valid(day.year, day.month, day.day, page))
        page += 1

    if len(rows) < total:
        raise Throttled(f"{day}: {len(rows)}/{total}건만 수집")

    # 같은 코드가 여러 페이지에 겹쳐 나오는 경우를 막는다.
    seen, uniq = set(), []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        uniq.append(r)
    return uniq


def load_cache():
    """이미 성공한 날은 다시 긁지 않는다. 레이트리밋에 걸려도 이어서 돌린다."""
    if not OUT.exists():
        return {}
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    by_day = {d: [] for d in old.get("ok_days", [])}
    for r in old.get("rows", []):
        by_day.setdefault(r["date"], []).append(r)
    return by_day


def save(by_day: dict, start: date, end: date):
    """하루 받을 때마다 저장한다. 레이트리밋에 걸려 중간에 죽어도
    이미 받아둔 날이 통째로 날아가지 않도록. 임시파일에 쓰고 갈아끼운다."""
    ok_days = sorted(by_day)
    rows = [r for d in ok_days for r in by_day[d]]
    payload = {
        "source": "日本経済新聞 決算発表スケジュール (QUICK提供)",
        "source_url": BASE,
        "range": [start.isoformat(), end.isoformat()],
        "count": len(rows),
        "ok_days": ok_days,
        "per_day": {d: len(by_day[d]) for d in ok_days},
        "rows": rows,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return len(rows), len(ok_days)


def main(start: date, end: date):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    by_day = load_cache()
    failed = []

    day = start
    while day <= end:
        key = day.isoformat()
        if key in by_day:
            print(f"{key} {len(by_day[key]):>4}건 (캐시)", flush=True)
        else:
            try:
                by_day[key] = scrape_day(day)
                print(f"{key} {len(by_day[key]):>4}건", flush=True)
                save(by_day, start, end)
            except Exception as e:          # 하루 실패가 전체를 죽이지 않게
                failed.append(key)
                print(f"{key} 실패: {e}", file=sys.stderr, flush=True)
            time.sleep(1.5)
        day += timedelta(days=1)

    n, days = save(by_day, start, end)
    print(f"\n총 {n}건 / {days}일 -> {OUT}")
    if failed:
        print(f"미수집 {len(failed)}일: {', '.join(failed)} (재실행하면 이어서 받는다)")


if __name__ == "__main__":
    a = sys.argv[1:]
    s = date.fromisoformat(a[0]) if a else date(2026, 7, 20)
    e = date.fromisoformat(a[1]) if len(a) > 1 else date(2026, 9, 30)
    main(s, e)
