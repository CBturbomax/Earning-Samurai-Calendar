# -*- coding: utf-8 -*-
"""일본 월매출 — **회사 IR 페이지의 月次 표**에서 모은다.

`scrape_mon_jp.py`(TDnet 첨부 PDF)도 `scrape_mon_web.py`(流通ニュース)도 못
닿는 자리를 메운다. 회원님이 "가용한 정보를 다 긁어서라도" 라고 하신 자리다.

**떠보고 붙였다(84·85·86차).** 큰 소매·외식 24곳의 제 사이트를 두드려 21곳이
열리고 16곳에서 月次 페이지를 찾았다 — 집계 사이트(irbank·minkabu·가부탄·
월차Web)가 전부 데이터센터 IP 를 막는 것과 딴판이다. **막힌 곳은 다시
두드리지 않는다**: 시마무라 8227·ABC마트 2670(403) · 비쿠카메라 3048(timeout).

손으로 잇는 것은 **맨 위 주소 하나**다(`monir.IR_SITES`). 月次 페이지는
수집기가 찾아 `sites` 에 적어 두고 다음부터는 곧장 그리로 간다. 표를 읽는
규칙은 `monir.read` 하나뿐이다 — 회사마다 예외를 두면 백 개의 파서가 된다.

**값은 전년동월비(%)뿐이다.** 금액을 내는 표도 있지만 규칙이 읽는 것은 비율
하나다. 지어 넣지 않는다.

**쌓아 두고 지우지 않는다.** 회사 사이트는 대개 이번 회계연도만 싣는다.
돌수록 이력이 길어지므로 이미 받아 둔 달을 빈 결과로 덮지 않는다.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import monir

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "monthly_ir_jp.json"

VER = 1
# 읽는 규칙의 판. 올리면 **찾아 둔 月次 주소만** 비우고 모아 둔 값은 그대로
# 둔다 — 회사 사이트에서 내려간 달을 영영 잃지 않기 위해서다.
RULE_VER = 1

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36"),
      "Accept-Language": "ja,en;q=0.8"}

# **한 회사를 얼마나 자주 두드릴까.** 월매출은 한 달에 한 번 올라온다.
# 자주 두드릴 값이 없고 남의 서버다. 아직 한 달도 못 받은 회사만 매번 본다.
FRESH_HOURS = float(os.environ.get("IR_FRESH_HOURS", "6"))
BUDGET = float(os.environ.get("IR_SECS", "420"))
PAUSE = float(os.environ.get("IR_PAUSE", "1.0"))
# 연속으로 못 받으면 그 바퀴를 접는다(다른 수집기와 같은 안전장치).
GIVE_UP_AFTER = int(os.environ.get("IR_GIVE_UP", "6"))


def get(url, timeout=15):
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            raw = r.read(1_500_000)
            head = raw[:2500].decode("ascii", "ignore").lower()
            enc = ("cp932" if "shift_jis" in head or "shift-jis" in head
                   else "euc-jp" if "euc-jp" in head else "utf-8")
            return raw.decode(enc, "ignore")
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError, ValueError) as e:
        print(f"  ! {url} {type(e).__name__} {str(e)[:40]}")
        return ""


def load():
    if not OUT.exists():
        return {"v": VER, "rv": RULE_VER, "sites": {}, "codes": {}}
    try:
        got = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"v": VER, "rv": RULE_VER, "sites": {}, "codes": {}}
    got.setdefault("sites", {})
    got.setdefault("codes", {})
    if got.get("rv") != RULE_VER:
        # **찾아 둔 주소만 비운다.** 모아 둔 값은 그대로 둔다 — 규칙을 넓혔는데
        # 값까지 지우면 회사 사이트에서 내려간 달을 영영 잃는다.
        print(f"  규칙 판이 바뀌었다({got.get('rv')} -> {RULE_VER})"
              f" — 찾아 둔 月次 주소를 비운다")
        got["sites"] = {}
        got["rv"] = RULE_VER
    return got


def save(rec):
    months = sum(len(v.get("months") or {}) for v in rec["codes"].values())
    rec.update({
        "v": VER, "rv": RULE_VER,
        "source": "회사 IR 페이지 (月次 표)",
        "note": ("회사가 제 사이트에 올린 월차 표에서 읽는다. 값은 전년동월비"
                 "(%)뿐이고 금액은 없다. 회사 사이트는 대개 이번 회계연도만"
                 " 실으므로 이력은 여기 쌓인 만큼이다."),
        "companies": len(rec["codes"]),
        "months": months,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"  저장 {len(rec['codes'])}사 · {months}달 -> {OUT.name}")


def discover(top):
    """맨 위 -> (月次 주소, 두드린 횟수). 못 찾으면 ''."""
    page = get(top)
    if not page:
        return "", 1
    url = monir.find_monthly(page, top)
    if url:
        return url, 1
    ir = monir.find_ir(page, top)
    if not ir:
        return "", 1
    time.sleep(PAUSE)
    ir_page = get(ir)
    if not ir_page:
        return "", 2
    return monir.find_monthly(ir_page, ir), 2


def main():
    rec = load()
    sites, codes = rec["sites"], rec["codes"]
    today = date.today()
    now = datetime.now(timezone.utc)
    t0 = time.time()
    fresh = new_months = miss = 0

    for code, (name, top, fixed) in monir.IR_SITES.items():
        if time.time() - t0 > BUDGET:
            print("  시간이 다 됐다 — 여기까지 저장하고 다음 실행에 잇는다")
            break
        if miss >= GIVE_UP_AFTER:
            print(f"  연속 {miss}번 못 받았다 — 이 바퀴는 접는다")
            break
        cur = codes.get(code) or {}
        # 이미 달이 쌓인 회사는 자주 안 두드린다. 한 달에 한 번 올라오는 값이다.
        if cur.get("months") and cur.get("ts"):
            try:
                age = (now - datetime.fromisoformat(cur["ts"])).total_seconds()
                if age < FRESH_HOURS * 3600:
                    continue
            except ValueError:
                pass
        site = sites.get(code) or {}
        url = fixed or site.get("page") or ""
        if not url:
            url, _n = discover(top)
            time.sleep(PAUSE)
            if not url:
                print(f"  {code} {name}: 月次 링크 못 찾음")
                miss += 1
                continue
            sites[code] = {"page": url, "found": today.isoformat()}
        page = get(url)
        time.sleep(PAUSE)
        if not page:
            # 주소가 바뀌었을 수 있다 — 다음 실행에 다시 찾게 비워 둔다.
            sites.pop(code, None)
            miss += 1
            continue
        miss = 0
        got = monir.read(page, today)
        fresh += 1
        # **이미 받아 둔 달을 빈 결과로 덮지 않는다.** 회사 사이트가 회계연도를
        # 넘기며 옛 표를 내려도 우리 이력은 그대로 남아야 한다.
        months = dict(cur.get("months") or {})
        for p, v in got.items():
            # **처음 본 날을 지킨다.** 회사 IR 표에는 공시일이 안 적혀 있어
            # 우리가 아는 날은 '우리가 처음 본 날'뿐이다. 실행할 때마다
            # 오늘로 덮으면 그 날짜가 날마다 흔들려 아무 뜻이 없어진다.
            row = {"day": (months.get(p) or {}).get("day") or today.isoformat()}
            row.update({k: val for k, val in v.items() if k in ("same", "all")})
            if p not in months:
                new_months += 1
            months[p] = row
        if not months:
            print(f"  {code} {name}: 0달 ({url})")
            continue
        codes[code] = {"name": name, "page": url, "months": months,
                       "ts": now.isoformat(timespec="seconds")}
        ks = sorted(months)
        print(f"  {code} {name}: {len(months)}달 {ks[0]} ~ {ks[-1]}")

    print(f"  두드린 회사 {fresh} · 새 달 {new_months}")
    save(rec)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        # 한 회사만 떠본다: python scrape_mon_ir.py --probe 9843
        code = sys.argv[sys.argv.index("--probe") + 1]
        name, top, fixed = monir.IR_SITES[code]
        url = fixed or discover(top)[0]
        print(name, url)
        page = get(url) if url else ""
        print(f"  {len(page)//1024}KB · 표 {page.count('<table')}")
        for p, v in sorted(monir.read(page, date.today()).items()):
            print("   ", p, v)
    else:
        main()
