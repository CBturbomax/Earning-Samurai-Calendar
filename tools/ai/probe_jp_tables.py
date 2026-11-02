# -*- coding: utf-8 -*-
"""떠보기 2 — 결산단신 zip 의 **태그 없는 HTML 표** 중 지역·수주 표를 칸째 찍는다.
python tools/ai/probe_jp_tables.py [코드…]"""
import html as H
import io
import re
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402
import pdftext  # noqa: E402

CODES = sys.argv[1:] or (
    "6503 6762 7751 6981 4502 6305 6301 6501 6586 7741 6702 6701 4307 3626 6841 "
    "6845 6754 6728 6141 6113 6103 6135 6324 6504 6594 6506 1942 1959 1721 6366 "
    "1963 6330 6370 6368 6013 7004 9719 2327 7012 7011 1801 1812 1803 1802 6383 "
    "6723 6920 7735 6525 4062 6976 6479").split()
KEY = re.compile(r"地域|所在地|仕向|受注")
WANT, seen = set(CODES), set()
BASE = "https://www.release.tdnet.info/inbs/"
TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)


def clean(s):
    return re.sub(r"\s+", " ", H.unescape(F.TAGS.sub(" ", s))).strip()


def rows_of(tb):
    out = []
    for r in ROW.findall(tb):
        cells = [clean(c) for c in CELL.findall(r)]
        if any(cells):
            out.append(" | ".join(cells))
    return out


for back in range(40):
    if not WANT - seen:
        break
    day = (date.today() - timedelta(days=back)).isoformat()
    got, page = [], 1
    try:
        while page <= 40:
            rs = F.listing(day, page)
            if not rs:
                break
            got += rs
            page += 1
            time.sleep(0.25)
    except F.Throttled as e:
        print(day, "실패", e)
        continue
    by = {}
    for r in got:
        if r["code"] in WANT and r["code"] not in seen:
            by.setdefault(r["code"], []).append(r)
    for code, rs in by.items():
        tan = [r for r in rs if F.TANSHIN.search(r["title"])
               and not F.NOT_TANSHIN.search(r["title"]) and r["zip"]]
        if not tan:
            continue
        seen.add(code)
        r = tan[0]
        print(f"\n######## {code} {r['name'][:14]} {day} | {r['title'][:46]}")
        try:
            z = zipfile.ZipFile(io.BytesIO(F.get(BASE + r["zip"], binary=True)))
        except Exception as e:  # noqa: BLE001
            print("  zip 실패", e)
            continue
        shown = 0
        for n in z.namelist():
            if not n.lower().endswith(".htm") or "Summary" in n:
                continue
            txt = z.read(n).decode("utf-8", "replace")
            pos = 0
            for m in TABLE.finditer(txt):
                before = clean(txt[max(pos, m.start() - 1500):m.start()])[-160:]
                pos = m.end()
                rows = rows_of(m.group(0))
                body = " ".join(rows[:3])
                if not (KEY.search(before) or KEY.search(body)) or len(rows) < 3:
                    continue
                if shown >= 6:
                    break
                shown += 1
                print(f"  ── [{n.split('/')[-1][:24]}] 앞글: …{before}")
                for x in rows[:16]:
                    print("     ", x[:240])
        if not shown:
            print("  (지역·수주 표 없음)")
        for x in rs:
            if x["pdf"] and re.search(r"補足|説明|プレゼン|参考|ファクト|Fact", x["title"]):
                try:
                    lines = pdftext.extract_lines(F.get(BASE + x["pdf"], binary=True))
                except Exception as e:  # noqa: BLE001
                    print("  pdf 못 읽음", x["title"][:30], type(e).__name__)
                    continue
                hits = [ln for ln in lines if re.search(r"受注|地域別|所在地別|仕向", ln)]
                print(f"  ── PDF {x['title'][:36]} 줄 {len(lines)} · 지역/수주 줄 {len(hits)}")
                for h in hits[:6]:
                    print("      ", h[:200])
                time.sleep(0.25)
        time.sleep(0.25)
print("\n못 찾은 코드:", sorted(WANT - seen))
