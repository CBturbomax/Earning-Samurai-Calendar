"""일본 결산 발표 **예정 시각** — 트레이더스웹(traders.co.jp) 결산 캘린더.

JPX 공식 목록(scrape_jp_sched.py)은 날짜만 준다. 그래서 일본 앞일은 전부 「15:00경」이었다.
회원님(11/19): "일본도 정확히 언제인지 알려주는 데가 있을 거야." 떠보기(tools/probe_jptime.py)에서
가부탄·민카부 405, IR뱅크·인베스팅 403 이었고 **트레이더스웹만 CI 에서 열렸다** — 한 장에 오늘부터
앞으로의 발표가 전부(11/19 실측 442건) 「11/19 15:30 ＳＯＭＰＯＨＤ (8630/東P) 連2Q」 꼴로 실린다.
시각이 아직 안 정해진 회사는 「-」 다 — **지어 넣지 않고 비워 둔다.**

  data/times_jp.json  {"fetched": ISO, "rows": [{"date","time","code","kind"}...]}

한 장(약 1MB)이라 실행마다 통째로 다시 받는다. 받기에 실패하거나 줄을 못 읽으면 **헌 파일을 그대로 둔다**
(조용한 0건 금지 — 빈 목록으로 덮으면 화면의 시각이 전부 「경」으로 돌아간다).
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "times_jp.json"
URL = "https://www.traders.co.jp/market_jp/earnings_calendar"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
JST = timezone(timedelta(hours=9))

ROW = re.compile(r"<tr[^>]*>\s*<td[^>]*>\s*(\d\d)/(\d\d)\s*</td>\s*<td[^>]*>\s*([0-9:：\-－]+)\s*</td>(.*?)</tr>", re.S)
CODE = re.compile(r"\(\s*([0-9A-Z]{4})\s*/")
TDS = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def get(url):
    last = None
    for wait in (0, 15, 45):
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"받기 실패: {last!r}")


def parse(html, today):
    if "時刻" not in html or "決算種別" not in html:
        raise RuntimeError("결산 캘린더 표가 아니다(껍데기)")
    out = []
    for mo, dd, tm, rest in ROW.findall(html):
        m = CODE.search(rest)
        if not m:
            continue
        # 해는 적혀 있지 않다 — 오늘에서 가장 가까운 해(12월에 1월 발표가 실린다)
        y = today.year
        cands = []
        for yy in (y - 1, y, y + 1):
            try:
                cands.append(date(yy, int(mo), int(dd)))
            except ValueError:
                pass
        d = min(cands, key=lambda x: abs((x - today).days))
        tm = tm.replace("：", ":")
        tm = tm if re.fullmatch(r"\d{1,2}:\d\d", tm) else ""
        if tm and len(tm) == 4:
            tm = "0" + tm
        tds = [re.sub(r"<[^>]+>|\s+", "", t) for t in TDS.findall(rest)]
        kind = tds[1] if len(tds) > 1 else ""
        out.append({"date": d.isoformat(), "time": tm, "code": m.group(1), "kind": kind})
    return out


def main():
    today = datetime.now(JST).date()
    try:
        rows = parse(get(URL), today)
    except Exception as e:  # noqa: BLE001
        print(f"트레이더스웹: {e} — 헌 파일을 그대로 둔다")
        return 0
    if len(rows) < 20:
        print(f"트레이더스웹: {len(rows)}줄뿐 — 껍데기로 보고 헌 파일을 그대로 둔다")
        return 0
    timed = sum(1 for r in rows if r["time"])
    OUT.write_text(json.dumps({"source": "トレーダーズ・ウェブ 決算発表予定", "source_url": URL,
                               "fetched": datetime.now(JST).isoformat(timespec="minutes"),
                               "count": len(rows), "rows": rows}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"저장: {len(rows):,}줄 (시각 있음 {timed:,} · 미정 {len(rows) - timed:,}) · {rows[0]['date']} ~ {rows[-1]['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
