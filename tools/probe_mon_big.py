"""아사히(2502) 월차 PDF 가 무엇을 싣나(11/23) — 로그에만."""
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import montable  # noqa: E402
import scrape_mon_ir as IR  # noqa: E402

for u, ann in (("https://www.asahigroup-holdings.com/pdf/en/ir/financial_data/monthly_data/2026/10.pdf", "2026-11-10"),
               ("https://www.asahigroup-holdings.com/pdf/ir/financial_data/monthly_data/2026/10.pdf", "2026-11-10")):
    d = IR.get_bytes(u)
    print("\n###", u, len(d or b""))
    if not d:
        continue
    g = montable.read(d, ann, title="2026 -Oct.-")
    print("rows", (g or {}).get("rows"), "\nseries", {k: sorted(v.items())[-3:] for k, v in ((g or {}).get("series") or {}).items()})
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(d)
        f.flush()
        print(subprocess.run(["pdftotext", "-layout", "-l", "2", f.name, "-"], capture_output=True).stdout.decode("utf-8", "replace")[:5000])
