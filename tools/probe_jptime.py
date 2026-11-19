"""일본 결산 발표 '예정 시각'을 주는 곳 떠보기(11/19 회원님: "일본도 정확히 언제인지 알려주는 데가 있을 거야").
JPX 공식 목록은 날짜만 준다. 각 후보를 한 번씩만 두드려 상태·크기·시각 꼴(HH:MM)과 그 앞뒤 글을 적는다."""
import re, sys, urllib.request, gzip
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
DAY = sys.argv[1] if len(sys.argv) > 1 else "2026/11/20"
D8 = DAY.replace("/", "")
CANDS = [
    ("traders", f"https://www.traders.co.jp/market_jp/earnings_calendar/all/all/1?term={DAY}"),
    ("traders0", "https://www.traders.co.jp/market_jp/earnings_calendar"),
    ("kabutan", f"https://kabutan.jp/warning/?mode=4_1&date={D8}"),
    ("kabutan_sched", f"https://kabutan.jp/news/marketnews/?category=3"),
    ("minkabu", f"https://minkabu.jp/financial_item_schedule?date={DAY.replace('/', '-')}"),
    ("yahoojp", f"https://finance.yahoo.co.jp/stocks/schedule/settlement?date={D8}"),
    ("nikkei", f"https://www.nikkei.com/markets/kigyo/money-schedule/kessan/?ResultFlag=1&kwd=&KessanMonth=&SearchDate1={DAY}&SearchDate2={DAY}&Gcode=%20&hm=1"),
    ("kabuyoho", "https://kabuyoho.jp/sp/kessan"),
    ("investing", "https://jp.investing.com/earnings-calendar/"),
    ("sbi", "https://www.sbisec.co.jp/ETGate/WPLETmgR001Control?OutSide=on&getFlg=on&burl=search_market&cat1=market&cat2=info&dir=info&file=market_kessan.html"),
    ("monex", "https://info.monex.co.jp/report/kessan/index.html"),
    ("rakuten", "https://www.rakuten-sec.co.jp/web/market/calendar/"),
    ("matsui", "https://finance.matsui.co.jp/kessan-schedule/index"),
    ("irbank", f"https://irbank.net/market/kessan?d={DAY.replace('/', '-')}"),
    ("kabupro", "https://www.kabupro.jp/kessan/"),
    ("ullet", "https://www.ullet.com/kessan/"),
    ("jpx_en", "https://www.jpx.co.jp/english/listing/event-schedules/financial-announcement/index.html"),
]
TIME = re.compile(r"(?<!\d)(0?[7-9]|1[0-9]|2[0-3])[:：]([0-5]\d)(?!\d)")
for name, url in CANDS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8", "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=25) as r:
            b = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                b = gzip.decompress(b)
            code = r.status
    except Exception as e:  # noqa: BLE001
        print(f"== {name} 실패 {repr(e)[:120]}  {url}\n")
        continue
    t = b.decode("utf-8", "replace")
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    hits = list(TIME.finditer(txt))
    print(f"== {name} {code} {len(b):,}B 시각꼴 {len(hits)}개  {url}")
    for m in hits[:8]:
        print("   …" + txt[max(0, m.start() - 70):m.end() + 40] + "…")
    if "決算" not in txt:
        print("   (본문에 '決算' 없음)")
    print()
