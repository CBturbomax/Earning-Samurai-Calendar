"""월매출 PDF — 우리 해석기 vs poppler 견주기(11/22). 아무것도 저장하지 않고 로그에만 찍는다.
A: 최근 「표없음」으로 버려진 공시를 poppler 로 읽으면 몇 건이 새로 읽히나(새로 읽힌 값도 전부 찍어 눈으로 본다)
B: 이미 잘 읽히던 공시를 poppler 로도 읽어 **값이 똑같은지** — 어긋나면 poppler 길에 오검출이 있다는 뜻이다."""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import montable  # noqa: E402
import pdftext  # noqa: E402
import scrape_mon_jp as M  # noqa: E402

rows = json.loads((HERE / "data" / "monthly_jp.json").read_text(encoding="utf-8"))["rows"]
nums = json.loads((HERE / "data" / "monthly_nums_jp.json").read_text(encoding="utf-8"))
skip = dict(nums["skip"]) if isinstance(nums["skip"], list) else nums["skip"]
done = set(nums["done"])
cut = (date.today() - timedelta(days=40)).isoformat()
recent = [r for r in rows if r.get("doc") and r["date"] >= cut]
A = [r for r in recent if skip.get(r["doc"]) == "표없음"][:90]
B = [r for r in recent if r["doc"] in done and r["doc"] not in skip][-50:]
print(f"A(표없음) {len(A)}건 · B(이미 읽힘) {len(B)}건 · poppler {pdftext.shutil.which('pdftotext') if hasattr(pdftext, 'shutil') else ''}")


def both(r):
    data = M.get(r["doc"])
    if not data:
        return None, None, "받기 실패"
    a = montable._read(data, r["date"], 12, r.get("title", ""), pdftext.extract_cells)
    b = montable._read(data, r["date"], 12, r.get("title", ""), pdftext.poppler_cells)
    return a, b, ""


def show(got):
    return " ".join(f"{x['period'][2:]}:{x.get('yoy') or ''}/{x.get('rev') or ''}" for x in (got or {}).get("rows", [])[-6:])


na = nm = 0
print("\n== A: 표없음 → poppler ==")
for r in A:
    a, b, err = both(r)
    time.sleep(0.3)
    if err:
        print(f"  {r['code']} {err}")
        continue
    if b and b.get("rows"):
        na += 1
        nm += len(b["rows"])
        print(f"  ✓ {r['code']} {r['name'][:12]} {r['date']} 대상 {r['period']} | {show(b)}")
    else:
        print(f"  · {r['code']} {r['name'][:12]} 여전히 못 읽음")
print(f"A 요약: {len(A)}건 중 새로 읽힘 {na}건 · {nm}달")

same = diff = only_old = only_new = 0
print("\n== B: 이미 읽히던 공시 — 값 대조 ==")
for r in B:
    a, b, err = both(r)
    time.sleep(0.3)
    if err:
        continue
    ra = {x["period"]: (x.get("yoy"), x.get("rev")) for x in (a or {}).get("rows", [])}
    rb = {x["period"]: (x.get("yoy"), x.get("rev")) for x in (b or {}).get("rows", [])}
    if not rb:
        only_old += 1
        continue
    bad = [p for p in ra if p in rb and ra[p] != rb[p]]
    extra = [p for p in rb if p not in ra]
    if bad or extra:
        diff += 1
        print(f"  ✗ {r['code']} {r['name'][:12]} 어긋남 {bad[:4]} 더 읽은 달 {extra[:4]} | 우리 {show(a)} | poppler {show(b)}")
    else:
        same += 1
print(f"B 요약: 같음 {same} · 어긋남 {diff} · poppler 만 못 읽음 {only_old}")
