"""나스닥 캘린더에 안 실리는 미국 상장 외국 회사의 실적 발표 — SEC 6-K 에서.

회원님이 매그넘 아이스크림(MICC)을 짚어 물으셨다("왜 어닝사무라이에 없지?").
나스닥 실적 캘린더는 외국 민간 발행사(FPI)를 상당수 안 싣는다 — 유니레버·
리오틴토·디아지오·BAT·페로비알·매그넘이 5월 이후 한 번도 안 나왔다(99차 실측:
시총 10억 달러+ 보통주 중 수십 곳). 이 회사들은 실적을 SEC 에 **6-K** 로 낸다.

  우주  나스닥 스크리너(전 상장 종목·시총) − 나스닥 캘린더에 이미 있는 종목
        − 우선주·채권·워런트 따위. 하루 네 번 다시 받는다.
  잣대  6-K 가 실적인지(100차 실측):
        · XBRL 이 붙은 6-K → 반기·분기 재무제표다(MICC 7/30 · UL 7/28 · RIO 7/29).
        · 설명·파일명에 실적 낱말(results · trading statement · Q3 … ) → 실적.
          「NOTICE/NOTIFICATION OF RESULTS」·「release date」·「RESULT OF AGM」처럼
          예고·주총·채권 공시는 뺀다.
        · 설명이 「FORM 6-K」뿐이면 본문 첫머리를 읽어 같은 잣대로 가린다.
  시각  SEC 접수 시각(UTC → 동부)이라 정확도 1 — 「방금 발표」 피드에도 뜬다.

나스닥처럼 '앞으로의 예정'은 없다(홍콩과 같은 '지나간 발표'). 결과는
data/earnings_us_fpi.json 이고 build.py 가 미국 캘린더에 합친다.
"""
import gzip
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "earnings_us_fpi.json"
SEC_UA = os.environ.get("SEC_UA", "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)")
NAS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MIN_CAP = float(os.environ.get("FPI_MIN_CAP", "0.3"))      # 십억 달러
DAYS = int(os.environ.get("FPI_DAYS", "200"))
RECHECK_MIN = int(os.environ.get("FPI_RECHECK_MIN", "15"))
SECS = int(os.environ.get("FPI_SECS", "240"))   # main() 에서 다시 읽는다(속보 루프가 남은 시간을 넘긴다)
SCREEN_HOURS = 6

NOT_COMMON = re.compile(
    r"(?i)depositary shares? (?:each |represent)|preferred|perpetual|\bnotes?\b|debenture|"
    r"subordinated|\bunits?\b|warrant|\brights?\b|zones|fixed[- ]to|floating|mandator|"
    r"first mortgage|tangible equity|series [a-z]\b|\d\.\d+%|\d%")
GOOD = re.compile(
    r"(?i)\bresults?\b|trading (?:update|statement)|earnings release|quarterly report|"
    r"interim (?:report|financial|results)|half[- ]?year|first half|\b[1-4]q\b|\bq[1-4]\b|"
    r"\bh1\b|\b1h\b|nine months|preliminary|operating (?:update|review)|"
    r"financial statements|first quarter|second quarter|third quarter|fourth quarter|full[- ]year")
BAD = re.compile(
    r"(?i)notice of|notification of|release date|date of|conference call|webcast|\bagm\b|"
    r"annual general|tender|restated|dividend|voting rights|own shares|pdmr|bond|notes due|"
    r"buy[- ]?back|repurchase|director|holding\(s\)|scrip|delisting|invitation|presentation date|"
    r"capital markets day|will (?:publish|announce|report|release)|to (?:publish|announce|report|release)|"
    r"ahead of|pre-close|forward[- ]looking|outlined|as a result")
GENERIC = re.compile(r"(?i)^(?:form )?6-k$|report of foreign|press release|^$")
# 본문으로 가릴 때는 더 좁게 — 보도자료 꼬리의 "actual results may differ" 같은
# 상투구가 아무 6-K 에나 들어 있다. '기간 + results' 나 trading statement 꼴만.
STRONG = re.compile(
    r"(?i)(?:\bq[1-4]\b|\b[1-4]q\b|\bh[12]\b|first|second|third|fourth|half[- ]?year|full[- ]?year|"
    r"interim|annual|quarter(?:ly)?|preliminary|nine[- ]month|20\d\d)\s+(?:[\w&,.-]+\s+){0,4}?"
    r"(?:results|trading (?:update|statement)|earnings)\b|trading (?:update|statement)|"
    r"results for the (?:quarter|half|year|period|three|six|nine|first|second|third|fourth)")


def get(url, ua=SEC_UA, timeout=40, accept=None):
    h = {"User-Agent": ua, "Accept-Encoding": "gzip"}
    if accept:
        h["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        b = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            b = gzip.decompress(b)
        return b.decode("utf-8", "replace")


def et(utc_iso):
    """SEC acceptanceDateTime(UTC, 끝에 Z) → 동부 'YYYY-MM-DD HH:MM'."""
    t = datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    y = t.year
    start = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    start += timedelta(days=(6 - start.weekday()) % 7)        # 3월 둘째 일요일 2시(동부)
    end = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    end += timedelta(days=(6 - end.weekday()) % 7)            # 11월 첫 일요일
    off = -4 if start <= t < end else -5
    return (t + timedelta(hours=off)).strftime("%Y-%m-%d %H:%M")


def clean_name(n):
    n = re.sub(r"(?i)\s+(american depositary shares?.*|ordinary shares?.*|common stock.*|"
               r"class [a-z] .*|sponsored ads.*|ads\b.*|depositary receipt.*)$", "", n or "")
    return n.strip(" ,")


def screener(old):
    """나스닥 스크리너 — 전 상장 종목·시총. 여섯 시간마다만 다시 받는다."""
    s = old.get("screen") or {}
    if s.get("ts", "") > (datetime.now(timezone.utc) - timedelta(hours=SCREEN_HOURS)).isoformat():
        return s
    t = get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true",
            NAS_UA, accept="application/json, text/plain, */*")
    rows = (json.loads(t).get("data") or {}).get("rows") or []
    if len(rows) < 3000:
        raise RuntimeError(f"스크리너가 {len(rows)}줄뿐 — 껍데기로 본다")
    keep = {}
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        try:
            cap = float(r.get("marketCap") or 0) / 1e9
        except ValueError:
            cap = 0
        if not sym or "^" in sym or "/" in sym or cap < MIN_CAP or NOT_COMMON.search(r.get("name") or ""):
            continue
        keep[sym] = [round(cap, 1), clean_name(r.get("name")), r.get("sector") or "", r.get("country") or ""]
    return {"ts": datetime.now(timezone.utc).isoformat(), "rows": keep}


def calendar_codes():
    try:
        rows = json.loads((HERE / "data" / "earnings_us.json").read_text(encoding="utf-8"))["rows"]
    except (ValueError, OSError, KeyError):
        return set()
    return {r["code"] for r in rows}


def kind_of(text, doc):
    t = (text or "").lower()
    m = re.search(r"(\d{4})(\d{2})(\d{2})", doc or "")
    end = f"{m.group(1)}년 {int(m.group(2))}월 분기" if m and 1 <= int(m.group(2)) <= 12 else ""
    if re.search(r"\bq1\b|\b1q\b|first quarter", t):
        k = "1Q"
    elif re.search(r"\bq3\b|\b3q\b|third quarter|nine[- ]months?", t):
        k = "3Q"
    elif re.search(r"half[- ]?year|first half|\bh1\b|\b1h\b|interim|\bq2\b|\b2q\b|second quarter", t):
        k = "반기"
    elif re.search(r"full[- ]?year|preliminary|annual results|\bq4\b|\b4q\b|fourth quarter", t):
        k = "연간"
    else:
        k = "실적"
    return end, k


def judge(desc, doc, xbrl, fetch_text):
    """실적 6-K 면 (참, 판단 근거 글). fetch_text 는 본문이 필요할 때만 부른다."""
    label = f"{desc} {doc.replace('_', ' ').replace('-', ' ')}"
    if BAD.search(desc or ""):
        return False, ""
    if xbrl:
        return True, label
    if GOOD.search(label) and not BAD.search(label):
        return True, label
    if not GENERIC.search((desc or "").strip()):
        return False, ""
    text = fetch_text()
    head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))[:6000]
    for m in STRONG.finditer(head):
        win = head[max(0, m.start() - 80):m.end() + 80]
        if not BAD.search(win):
            return True, win
    return False, ""


def main():
    t0 = time.time()
    SECS = int(os.environ.get("FPI_SECS", "240"))
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        old = {}
    seen = old.get("seen", {})          # 접수번호 → 1(실적)/0(아님)
    checked = old.get("checked", {})    # 종목 → 마지막으로 본 시각
    rows = {(r["code"], r["acc"]): r for r in old.get("rows", [])}
    screen = screener(old)
    cal = calendar_codes()
    uni = {s: v for s, v in screen["rows"].items()
           if s not in cal and s.replace(".", "-") not in cal and s.replace("-", ".") not in cal}
    # SEC 티커 목록(1MB)은 3분 루프마다 받지 않는다 — 러너 안에 반나절 둔다.
    cache = HERE / ".cache" / "sec_tickers.json"
    try:
        if time.time() - cache.stat().st_mtime > 12 * 3600:
            raise OSError
        ciks = json.loads(cache.read_text())
    except (OSError, ValueError):
        try:
            tk = json.loads(get("https://www.sec.gov/files/company_tickers.json"))
        except Exception as e:                                # noqa: BLE001
            print(f"SEC 티커 목록 실패: {e}", file=sys.stderr)
            return 1
        ciks = {v["ticker"].upper(): v["cik_str"] for v in tk.values()}
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps(ciks))
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    now = datetime.now(timezone.utc)
    due = sorted(uni, key=lambda s: (checked.get(s, ""), -uni[s][0]))
    fpi = n_new = 0
    for sym in due:
        if time.time() - t0 > SECS:
            print("  시간이 다 됐다 — 다음 실행에서 이어서")
            break
        if checked.get(sym, "") > (now - timedelta(minutes=RECHECK_MIN)).isoformat():
            continue
        cik = ciks.get(sym.upper()) or ciks.get(sym.upper().replace(".", "-"))
        if not cik:
            checked[sym] = now.isoformat()
            continue
        try:
            sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
        except Exception as e:                                # noqa: BLE001
            print(f"  {sym} 제출 목록 실패: {e}", file=sys.stderr)
            continue
        time.sleep(0.12)
        checked[sym] = now.isoformat()
        r = sub.get("filings", {}).get("recent", {})
        forms = r.get("form", [])
        if "6-K" not in forms:
            continue                                         # 국내 기업·펀드 — 여기 몫이 아니다
        fpi += 1
        for i, f in enumerate(forms):
            if f != "6-K" or r["filingDate"][i] < since:
                continue
            acc = r["accessionNumber"][i]
            if acc in seen:
                continue
            doc = r["primaryDocument"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"

            def fetch(u=url):
                time.sleep(0.12)
                try:
                    return get(u)[:60000]
                except Exception:                            # noqa: BLE001
                    return ""
            ok, why = judge(r["primaryDocDescription"][i] or "", doc, r["isXBRL"][i], fetch)
            seen[acc] = 1 if ok else 0
            if not ok:
                continue
            when = et(r["acceptanceDateTime"][i])
            fy, kind = kind_of(why, doc if r["isXBRL"][i] else "")
            cap, name, sector, country = uni[sym]
            rows[(sym, acc)] = {"date": when[:10], "code": sym, "name": name or sub.get("name", sym),
                                "fy": fy, "kind": kind, "sector": sector, "market": "",
                                "cap": cap, "acc": acc, "accepted": when, "country": country,
                                "url": url, "why": why[:160]}
            n_new += 1
    # 한 번의 발표가 6-K 여러 장으로 온다 — 보도자료 본·XBRL 재무제표 본, 그리고
    # 며칠 뒤의 정식 반기보고서(스미스앤드네퓨 8/4 실적 → 8/14 XBRL, 프루덴셜 8/27
    # 실적 → 9/2 「HALF-YEAR REPORT」). 같은 회사의 실적 6-K 가 25일 안에 이어지면
    # 첫 장 하나만 남긴다(분기 실적끼리는 석 달 떨어져 있다).
    out, last = [], {}
    for r in sorted(rows.values(), key=lambda r: (r["code"], r["accepted"])):
        prev = last.get(r["code"])
        if prev and (date.fromisoformat(r["date"]) - date.fromisoformat(prev)).days <= 25:
            continue
        last[r["code"]] = r["date"]
        out.append(r)
    out.sort(key=lambda r: (r["date"], r["code"]))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "source": "SEC EDGAR 6-K (나스닥 캘린더에 없는 미국 상장 외국 회사)",
        "note": "실적 6-K 의 접수 시각(동부). 앞으로의 예정은 없다.",
        "screen": screen, "checked": checked, "seen": seen, "rows": out,
    }, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"외국 상장사 6-K: 대상 {len(uni)} · 이번에 본 FPI {fpi} · 새 실적 {n_new} · 누적 {len(out)}건 "
          f"({time.time() - t0:.0f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
