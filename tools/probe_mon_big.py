"""무인양품(7453)·아사히(2502)·코스모스약품(3349) 월매출을 어디서 받을 수 있나(11/23) — 로그에만 찍는다.
① 流通ニュース 목록에 이 회사 기사가 있나 ② 아사히 IR 월차 페이지가 무엇을 싣나(열리는데 표를 못 읽는다)."""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import monir  # noqa: E402
import scrape_mon_ir as IR  # noqa: E402
import scrape_mon_web as W  # noqa: E402

WANT = re.compile(r"良品|無印|MUJI|アサヒ|コスモス|しまむら")
hits = 0
for n in range(1, 15):
    page = W.get(W.page_url(n))
    if not page:
        print("목록", n, "받기 실패")
        continue
    for href, t in re.findall(r'<a[^>]+href="([^"]+/sales/[^"]+)"[^>]*>(.*?)</a>', page, re.S):
        t = re.sub(r"<[^>]+>", "", t).strip()
        if WANT.search(t):
            hits += 1
            print("기사", n, href, t[:80])
print("流通ニュース 맞은 기사", hits)

for u in ("https://www.asahigroup-holdings.com/en/ir/financial_data/monthly_data/",
          "https://www.asahigroup-holdings.com/ir/financial_data/monthly_data/"):
    p = IR.get(u)
    print("\n### 아사히", u, len(p or ""))
    if not p:
        continue
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", p)))
    print("   글:", txt[:2500])
    for href, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', p, re.S):
        if re.search(r"\.(pdf|xlsx?|csv)", href, re.I):
            print("   파일", href, re.sub(r"<[^>]+>", "", t).strip()[:60])
    print("   monir.read:", sorted(monir.read(p).items())[-4:])
