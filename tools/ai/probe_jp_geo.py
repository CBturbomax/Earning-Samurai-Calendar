# -*- coding: utf-8 -*-
"""떠보기(probe) — 일본 결산단신 zip·보충자료 PDF 안에 **지역별 매출**과
**수주·수주잔고**가 어떤 꼴로 들어 있는지 원문으로 본다. 아무것도 쓰지 않는다.

python tools/ai/probe_jp_geo.py [코드…]
"""
import io
import re
import sys
import time
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_fin_jp as F  # noqa: E402

try:
    import pdftext
except Exception:  # noqa: BLE001
    pdftext = None

CODES = sys.argv[1:] or (
    "6758 6501 6301 6954 7011 6367 8035 4063 6981 6146 6273 7741 6857 6503 "
    "7012 6702 6326 6586 7309 6861 6762 4543 1801 1812 6383 6305 7013 6361 "
    "6268 6971 7751 6902 7267 4502 6098").split()
GEO = re.compile(r"Japan|America|Europe|Asia|China|Region|Area|Geograph|"
                 r"Overseas|Domestic|Oceania|Africa|MiddleEast|UnitedStates", re.I)
ORD = re.compile(r"Order|Backlog|受注", re.I)
WANT = set(CODES)
seen = set()
BASE = "https://www.release.tdnet.info/inbs/"


def all_rows(day):
    got, page = [], 1
    while page <= 40:
        rows = F.listing(day, page)
        if not rows:
            break
        got += rows
        page += 1
        time.sleep(0.3)
    return got


def snippet(txt, word, n=3, width=260):
    out, i = [], 0
    plain = re.sub(r"\s+", " ", F.TAGS.sub(" ", txt))
    while len(out) < n:
        i = plain.find(word, i)
        if i < 0:
            break
        out.append(plain[max(0, i - 40):i + width])
        i += width
    return out


for back in range(14):
    if not WANT - seen:
        break
    day = (date.today() - timedelta(days=back)).isoformat()
    try:
        rows = all_rows(day)
    except F.Throttled as e:
        print(day, "목록 실패", e)
        continue
    by = {}
    for r in rows:
        if r["code"] in WANT and r["code"] not in seen:
            by.setdefault(r["code"], []).append(r)
    for code, rs in by.items():
        tan = [r for r in rs if F.TANSHIN.search(r["title"])
               and not F.NOT_TANSHIN.search(r["title"]) and r["zip"]]
        if not tan:
            continue
        seen.add(code)
        r = tan[0]
        print(f"\n######## {code} {r['name'][:16]} {day} | {r['title'][:50]}")
        for x in rs:
            print("   목록:", x["title"][:70], "| pdf" if x["pdf"] else "",
                  "| zip" if x["zip"] else "")
        try:
            blob = F.get(BASE + r["zip"], binary=True)
        except F.Throttled as e:
            print("  zip 실패", e)
            continue
        z = zipfile.ZipFile(io.BytesIO(blob))
        names = [n for n in z.namelist() if n.lower().endswith(".htm")]
        print("  파일:", [n.split("/")[-1][:40] for n in names])
        geo_ctx, ord_names, axes = Counter(), Counter(), Counter()
        for n in names:
            txt = z.read(n).decode("utf-8", "replace")
            for attrs, inner in F.IX_FACT.findall(F.IX_EMPTY.sub("", txt)):
                a = dict(F.IX_ATTR.findall(attrs))
                cid = a.get("contextRef") or ""
                nm = (a.get("name") or "").rsplit(":", 1)[-1]
                for p in cid.split("_")[1:]:
                    axes[re.sub(r"^[a-z]+-[a-z0-9]+-[0-9A-Za-z]{5}", "", p)] += 1
                if GEO.search(cid):
                    geo_ctx[(n.split("/")[-1][:22], nm, cid[-70:])] += 1
                if ORD.search(nm) or ORD.search(cid):
                    ord_names[(nm, cid[-60:], F.TAGS.sub("", inner)[:20])] += 1
            for w in ("地域", "所在地", "受注残", "受注高", "受注"):
                if w in txt:
                    for s in snippet(txt, w, 2):
                        print(f"    [{n.split('/')[-1][:20]}] {w}: {s}")
                    break
        print("  문맥 토막 상위:", axes.most_common(25))
        print("  지역 문맥 fact:", len(geo_ctx))
        for k, c in list(geo_ctx.items())[:25]:
            print("     ", c, k)
        print("  수주 fact:", len(ord_names))
        for k, c in list(ord_names.items())[:20]:
            print("     ", c, k)
        # 보충자료 PDF — 지역·수주 표가 거기 있는지
        for x in rs:
            if x["pdf"] and re.search(r"補足|説明資料|プレゼン|Presentation|参考資料|ファクト",
                                      x["title"]):
                try:
                    pdf = F.get(BASE + x["pdf"], binary=True)
                except F.Throttled as e:
                    print("  pdf 실패", e)
                    continue
                if not pdf or not pdftext:
                    continue
                try:
                    lines = pdftext.extract_lines(pdf)
                except Exception as e:  # noqa: BLE001
                    print("  pdf 못 읽음", type(e).__name__, e)
                    continue
                txt = "\n".join(lines) if isinstance(lines, list) else str(lines)
                print(f"  ── PDF {x['title'][:40]} 줄 {txt.count(chr(10))}")
                for w in ("地域別", "所在地別", "仕向地", "受注残", "受注高", "受注"):
                    hits = [ln for ln in txt.split("\n") if w in ln][:4]
                    for h in hits:
                        print(f"     {w}: {h[:200]}")
                time.sleep(0.3)
        time.sleep(0.3)

print("\n못 찾은 코드:", sorted(WANT - seen))
