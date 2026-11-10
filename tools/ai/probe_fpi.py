"""ai_fpi 가 6-K 에서 무엇을 골라 AI 에 넘기는지 떠본다(AI 안 부름). python tools/ai/probe_fpi.py CIK:ACC …"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import ai_fpi as P

for spec in sys.argv[1:]:
    cik, acc = spec.split(":")
    txt = P.release_html(int(cik), acc)
    print(f"== {spec} 글 {len(txt):,}자 · 표 {txt.count('=== TABLE ===')} · 총매출 줄 {bool(P.REVROW.search(txt))}")
    print(txt[500:2600])
