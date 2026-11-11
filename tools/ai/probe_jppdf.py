"""결산단신 PDF 에서 'セグメント' 가 든 쪽이 어떻게 생겼는지 떠본다(AI 안 부름). python tools/ai/probe_jppdf.py 코드:pdf이름 …"""
import re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import scrape_geo_jp as G

for spec in sys.argv[1:]:
    code, pn = spec.split(":")
    pdf = G.get(f"https://www2.jpx.co.jp/disc/{code}0/{pn}", timeout=60)
    print(f"== {spec} bytes {len(pdf or b'')}")
    if not pdf:
        continue
    r = subprocess.run(["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, timeout=90)
    pages = r.stdout.decode("utf-8", "replace").split("\f")
    print("pages", len(pages), "stderr", r.stderr.decode("utf-8", "replace")[:200])
    for i, pg in enumerate(pages):
        flat = re.sub(r"[ 　]", "", pg)
        if "セグメント" in flat:
            nums = len(re.findall(r"\d{1,3},\d{3}", pg))
            print(f"-- page {i + 1} · 숫자 {nums} · 外部顧客 {'外部顧客' in flat} · 報告セグメント {'報告セグメント' in flat} · セグメント情報 {'セグメント情報' in flat}")
            print(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "\n".join(l for l in pg.splitlines() if l.strip())[:1500]))
