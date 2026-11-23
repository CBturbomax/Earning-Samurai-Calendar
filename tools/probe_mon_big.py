"""큰 회사 월매출이 왜 자동으로 안 들어오나(11/23) — 로그에만 찍는다. 아무것도 저장하지 않는다.
패스트리(IR PDF)·이온(TDnet PDF)·PPIH(TDnet 암호 PDF + IR 표)·미쓰코시이세탄(IR) 을 실제로 받아 지금 규칙이 무엇을 읽는지 본다."""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import monir  # noqa: E402
import montable  # noqa: E402
import pdftext  # noqa: E402
import scrape_mon_ir as IR  # noqa: E402
import scrape_mon_jp as M  # noqa: E402


def text(data, n=70):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(data)
        f.flush()
        r = subprocess.run(["pdftotext", "-layout", "-l", "3", f.name, "-"], capture_output=True, timeout=60)
    out = r.stdout.decode("utf-8", "replace").splitlines()
    print(f"   pdftotext rc={r.returncode} err={r.stderr[:200]!r} 줄={len(out)}")
    for ln in out[:n]:
        if ln.strip():
            print("   |", ln[:200])


def rows(got):
    return " ".join(f"{x['period']}:{x.get('yoy')}/{x.get('rev')}[{x.get('metric','')[:14]}]" for x in (got or {}).get("rows", [])[-14:])


def pdf(url, ann, title, n=70):
    print(f"\n=== {url} ann={ann} title={title!r}")
    data = M.get(url) or IR.get_bytes(url)
    if not data:
        print("   받기 실패")
        return
    print(f"   {len(data)}B 암호={pdftext.is_encrypted(data)}")
    out = HERE / "probe_out"
    out.mkdir(exist_ok=True)
    (out / url.rstrip("/").split("/")[-1]).write_bytes(data)
    for pp in (True, False):
        try:
            print(f"   montable(prefer_poppler={pp}):", rows(montable.read(data, ann, title=title, prefer_poppler=pp)) or "없음")
        except Exception as e:  # noqa: BLE001
            print("   montable 터짐", e)
    try:
        print("   우리 해석기 글자:", len(pdftext.extract_text(data, 4)))
    except Exception as e:  # noqa: BLE001
        print("   우리 해석기 터짐", e)
    text(data, n)


def page(url):
    print(f"\n### 페이지 {url}")
    p = IR.get(url)
    if not p:
        print("   받기 실패")
        return ""
    print(f"   {len(p)}자")
    for u, lab, when in monir.pdf_links(p, url)[:12]:
        print("   PDF", u, "|", lab[:60], "|", when)
    try:
        got = sorted(monir.read(p).items())[-6:]
        print("   표:", str(got)[:600])
    except Exception as e:  # noqa: BLE001
        print("   표 읽기 터짐", e)
    return p


page("https://www.fastretailing.com/jp/ir/monthly/")
pdf("https://www.fastretailing.com/jp/ir/monthly/pdf/MonthlySales_2027.pdf", "2026-11-04", "2027年8月期")
pdf("https://www.release.tdnet.info/inbs/140120261110571736.pdf", "2026-11-10", "2026年10月度 月次売上高（イオン）")
pdf("https://www.release.tdnet.info/inbs/140120261111573874.pdf", "2026-11-11", "月次売上高（2026年10月度速報）", 90)
page("https://ppih.co.jp/ir/highlight/monthly/")
page("https://www.imhds.co.jp/corporate/ir/finance/monthly-report.html")
pdf("https://pdf.irpocket.com/C3099/xoA3/Laiu/Q5pL/ZbUp.pdf", "2026-11-16", "2026年10月 売上速報")
