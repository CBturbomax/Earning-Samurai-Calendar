"""JPX 決算発表予定日 — 일본 '앞으로의 일정'의 공식 소스.

닛케이가 데이터센터 IP 를 막아 CI 에서는 앞일에 구멍이 났다(8/17 이후가 통째로
「일본 미수집」으로 섰다). 같은 것을 JPX(일본거래소)가 공식 엑셀로 낸다 —
결산기말이 같은 달인 회사끼리 한 파일씩, 주 단위쯤으로 다시 컴파일한다(probe 8차).

  페이지  https://www.jpx.co.jp/listing/event-schedules/financial-announcement/
  파일    …-att/kessan06_0807.xlsx   6월에 분기말·기말을 맞은 회사 (8/7 컴파일)
          …-att/kessan07_0807.xlsx   7월에 분기말·기말을 맞은 회사
          …-att/kessan.xlsx          접미사 없는 최신 갱신분

xlsx 는 zip 속 XML 이라 표준 라이브러리로 읽힌다(sharedStrings + sheet1).
날짜 칸은 엑셀 시리얼 숫자다(1899-12-30 기준).

**행 어휘를 닛케이 것에 맞춘다.** build.py 의 merge 가 (code, fy, kind) 로 중복을
가리므로 여기가 어긋나면 같은 분기가 두 줄로 선다.

  kind    第１四半期 -> 第１ (전각 숫자 그대로) · 通期/期末/本決算 -> 本
  fy      決算期末 칸(회계연도 말일)의 달 -> '3月期'
  sector  도쿄증권 33업종 이름 그대로 싣는다 — 닛케이 업종(サービス)과 표기가
          달라서(サービス業), 한글 표기는 markets.py 의 SECTOR_KO 에 더해 두었다.

같은 회사·같은 분기가 여러 파일에 있으면 **나중 파일이 이긴다** — 접미사의
컴파일 날짜 오름차순으로 읽고, 접미사 없는 kessan.xlsx 를 맨 뒤에 둔다(예정일을
바꾼 회사가 거기 실린다).
"""

import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "earnings_jp_sched.json"

BASE = "https://www.jpx.co.jp"
PAGE = BASE + "/listing/event-schedules/financial-announcement/index.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 Chrome/124.0 Safari/537.36")

JST = timezone(timedelta(hours=9))
# 엑셀 시리얼 날짜의 원점. 1900-02-29 버그 때문에 12-30 이다.
EPOCH = date(1899, 12, 30)

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def get(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "*/*", "Accept-Language": "ja,en;q=0.8"})
    last = None
    for wait in (0, 15, 45):
        if wait:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001 — 재시도 후 던진다
            last = e
    raise last


def cells_of(data):
    """xlsx 한 장 -> [{열글자: 값(str)}]. 공유 문자열을 되살리고 셀 종류를 가른다."""
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(
                "{%s}t" % NS["m"])))
    sheet = ElementTree.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter("{%s}row" % NS["m"]):
        cur = {}
        for c in row.findall("m:c", NS):
            ref = c.get("r") or ""
            col = "".join(ch for ch in ref if ch.isalpha())
            t = c.get("t") or ""
            if t == "inlineStr":
                v = "".join(x.text or "" for x in c.iter("{%s}t" % NS["m"]))
            else:
                ve = c.find("m:v", NS)
                v = ve.text if ve is not None and ve.text else ""
                if t == "s" and v != "":
                    v = shared[int(v)]
            if v != "":
                cur[col] = v
        if cur:
            rows.append(cur)
    return rows


def serial_date(v):
    """엑셀 시리얼(또는 이미 날짜 문자열)을 ISO 날짜로. 못 읽으면 ''."""
    s = str(v).strip()
    m = re.match(r"^(\d{4})[/年.-](\d{1,2})[/月.-](\d{1,2})", _half(s))
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    try:
        n = float(s)
    except ValueError:
        return ""
    if not 20000 < n < 80000:                       # 1954~2118년 밖이면 날짜가 아니다
        return ""
    return (EPOCH + timedelta(days=int(n))).isoformat()


_FULL = str.maketrans("０１２３４５６７８９", "0123456789")


def _half(s):
    return str(s).translate(_FULL)


def kind_of(s):
    """種別 -> 닛케이 어휘. 第１四半期 -> 第１ · 通期/期末/本決算 -> 本"""
    s = str(s).strip()
    if s.startswith("第") and len(s) >= 2:
        return s[:2]
    if "通期" in s or "本決算" in s or "期末" in s or "決算" == s:
        return "本"
    return s


def fy_of(v):
    """決算期末(회계연도 말일) -> '3月期'. 시리얼 날짜와 '３月' 문자열 둘 다 읽는다."""
    iso = serial_date(v)
    if iso:
        return f"{int(iso[5:7])}月期"
    m = re.search(r"(\d{1,2})月", _half(v))
    return f"{int(m.group(1))}月期" if m else ""


def header_map(rows):
    """머리행에서 열 -> 뜻 을 찾는다. 파일 판이 바뀌어 열이 밀려도 따라간다."""
    for r in rows[:5]:
        text = {col: str(v) for col, v in r.items()}
        if any("コード" in v for v in text.values()):
            cols = {}
            for col, v in text.items():
                if "発表" in v and "日" in v:
                    cols["date"] = col
                elif "コード" in v:
                    cols["code"] = col
                elif "会社名" in v:
                    cols["name"] = col
                elif "決算期末" in v:
                    cols["fyend"] = col
                elif "業種名" in v:
                    cols["sector"] = col
                elif "種別" in v:
                    cols["kind"] = col
            return cols, rows.index(r)
    return {}, -1


def parse_file(data, fname):
    rows = cells_of(data)
    cols, hdr = header_map(rows)
    if "code" not in cols or "name" not in cols:
        print(f"  ! {fname}: 머리행을 못 찾았다 — 건너뛴다")
        return [], ""
    # 발표일 칸이 머리에 안 잡히면(제목이 그림이거나 병합) 남은 열 중 값이
    # 시리얼 날짜인 첫 열을 쓴다.
    if "date" not in cols:
        for col in ("A", "B", "C"):
            if col in cols.values():
                continue
            if any(serial_date(r.get(col, "")) for r in rows[hdr + 1:hdr + 6]):
                cols["date"] = col
                break
    stamp = ""
    for r in rows[:hdr + 1]:
        for v in r.values():
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*現在", _half(str(v)))
            if m:
                stamp = "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    out = []
    for r in rows[hdr + 1:]:
        day = serial_date(r.get(cols.get("date", ""), ""))
        code = _half(r.get(cols["code"], "")).strip().upper()
        name = str(r.get(cols["name"], "")).strip()
        if not day or not re.fullmatch(r"[0-9A-Z]{4}", code) or not name:
            continue
        out.append({
            "date": day, "code": code, "name": name,
            "fy": fy_of(r.get(cols.get("fyend", ""), "")),
            "kind": kind_of(r.get(cols.get("kind", ""), "")),
            "sector": str(r.get(cols.get("sector", ""), "")).strip(),
            "market": "東証",
        })
    return out, stamp


def fetch_all():
    body = get(PAGE).decode("utf-8", "replace")
    links = []
    for m in re.finditer(r'href="([^"]+/kessan[^"]*\.xlsx)"', body):
        l = m.group(1)
        if l not in links:
            links.append(l)
    if not links:
        raise RuntimeError("kessan*.xlsx 링크가 하나도 없다 — 페이지가 바뀌었나")
    files = []
    for l in links:
        url = l if l.startswith("http") else BASE + l
        fname = l.rsplit("/", 1)[-1]
        try:
            data = get(url)
        except Exception as e:                      # noqa: BLE001
            print(f"  ! {fname}: 못 받았다 {e}")
            continue
        rows, stamp = parse_file(data, fname)
        files.append((fname, stamp, rows))
        time.sleep(0.5)
    # 컴파일 날짜 오름차순, 접미사 없는 최신 갱신분(kessan.xlsx)을 맨 뒤에.
    files.sort(key=lambda f: (f[0] == "kessan.xlsx", f[1], f[0]))
    return files


def main():
    probe = "--probe" in sys.argv
    files = fetch_all()
    merged = {}
    for fname, stamp, rows in files:
        for r in rows:
            merged[(r["code"], r["fy"], r["kind"])] = r
        if probe and rows:
            ds = sorted(r["date"] for r in rows)
            print(f"  {fname} ({stamp or '기준일 없음'}): {len(rows):,}행 "
                  f"{ds[0]} ~ {ds[-1]}")
            for r in rows[:3]:
                print("    ", r)
    rows = sorted(merged.values(), key=lambda r: (r["date"], r["code"]))
    today = datetime.now(JST).date().isoformat()
    upcoming = [r for r in rows if r["date"] >= today]
    if probe:
        from collections import Counter
        print("  kind:", Counter(r["kind"] for r in rows))
        print("  fy:", Counter(r["fy"] for r in rows).most_common(6))
        print("  sector 예:", sorted({r["sector"] for r in rows})[:12])
        print(f"  합계 {len(rows):,}행 · 오늘({today}) 이후 {len(upcoming):,}행")
        return
    if not rows:
        raise SystemExit("한 행도 못 읽었다 — 조용한 0건을 만들지 않는다")
    # 수집한 날: 오늘부터 목록의 마지막 발표일까지. 그 사이 비는 날은 '발표가
    # 없는 날'이지 구멍이 아니다 — 거래소의 전체 목록이 그렇게 말한 것이다.
    # 목록 너머(마지막 발표일 뒤)는 아직 신고가 안 쌓인 구간이라 ok 로 안 적는다.
    last = max(r["date"] for r in rows)
    ok, d = [], date.fromisoformat(today)
    while d.isoformat() <= last:
        ok.append(d.isoformat())
        d += timedelta(days=1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "JPX 결산발표 예정일",
        "source_url": PAGE,
        "fetched": datetime.now(JST).isoformat(timespec="minutes"),
        "files": [f[0] for f in files],
        "count": len(rows),
        "ok_days": ok,
        "rows": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {len(rows):,}행 (오늘 이후 {len(upcoming):,}) · "
          f"ok {today}~{last} -> {OUT.name}")


if __name__ == "__main__":
    main()
