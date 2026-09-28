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
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import monir
import montable
import scrape_caps

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "monthly_ir_jp.json"
UNIVERSE = HERE / "data" / "monthly_universe_jp.json"
COVERAGE = HERE / "data" / "monthly_coverage_jp.json"

VER = 1
# 읽는 규칙의 판. 올리면 **모아 둔 것을 통째로 비우고 다시 받는다.**
#
# 다른 월매출 수집기와 반대인데, 까닭이 있다. TDnet 첨부도 流通ニュース 기사도
# **창 밖으로 밀려나면 다시 못 받는다** — 그래서 그쪽은 규칙을 넓혀도 값은
# 그대로 두었다. 회사 IR 페이지는 **늘 거기 있다.** 다시 받는 값이 싸므로,
# 규칙이 아직 어린 지금은 **틀린 값을 안고 가느니 다시 받는 편**이 낫다
# (오검출 하나가 놓침 하나보다 나쁘다). 규칙이 굳으면 그때 바꾼다.
RULE_VER = 3

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36"),
      "Accept-Language": "ja,en;q=0.8"}

# **한 회사를 얼마나 자주 두드릴까.** 월매출은 한 달에 한 번 올라온다.
# 자주 두드릴 값이 없고 남의 서버다. 아직 한 달도 못 받은 회사만 매번 본다.
FRESH_HOURS = float(os.environ.get("IR_FRESH_HOURS", "6"))
# **月次 링크를 못 찾은 곳도 기억한다.** 안 적어 두면 매 실행마다 그 회사의
# 맨 위 페이지와 IR 페이지를 다시 받는다 — 값도 없이 남의 서버만 두드린다.
MISS_DAYS = float(os.environ.get("IR_MISS_DAYS", "3"))
BUDGET = float(os.environ.get("IR_SECS", "420"))
PAUSE = float(os.environ.get("IR_PAUSE", "1.0"))
# 연속으로 못 받으면 그 바퀴를 접는다(다른 수집기와 같은 안전장치).
GIVE_UP_AFTER = int(os.environ.get("IR_GIVE_UP", "10"))

# **月次 페이지의 둘째 꼴 — PDF 목록.** 표를 안 싣고 달마다(또는 회계연도마다)
# PDF 한 장을 거는 회사가 많다. 그 PDF 는 TDnet 첨부와 같은 꼴이라 이미 있는
# 연장(`pdftext`+`montable`)이 읽는다. 첨부는 무거우므로(수백 KB) 한 번 본
# 주소는 건너뛰고 한 바퀴에 조금씩만 본다.
PDF_PER_COMPANY = int(os.environ.get("IR_PDF_PER_COMPANY", "4"))
PDF_PER_RUN = int(os.environ.get("IR_PDF_PER_RUN", "20"))
# **표가 새것이어도 PDF 는 아직 안 본 것일 수 있다.** 달이 이미 쌓인 회사를
# 신선하다고 건너뛰면 그 회사의 PDF 를 영영 안 연다 — 실제로 첫 실행에서
# 그랬다. 옛 PDF 는 안 바뀌므로 하루에 한 번이면 넉넉하다.
PDF_FRESH_HOURS = float(os.environ.get("IR_PDF_FRESH_HOURS", "24"))
# 공식 홈페이지를 모르는 seed 종목은 Yahoo assetProfile 에서 조금씩만 채운다.
# 월차 잡이 10분마다 도니 한 바퀴에 12개면 하루 안에 300여 종목을 한 번 돈다.
HOME_PER_RUN = int(os.environ.get("IR_HOME_PER_RUN", "24"))
PROFILE_MISS_DAYS = float(os.environ.get("IR_PROFILE_MISS_DAYS", "14"))

# **수집일과 발표일은 완전히 다르다.**
# 회사의 rolling 月次 페이지가 "최종 업데이트"를 명시할 때만 실제 공개일 후보로 쓴다.
# 그런 표기가 없으면 빈칸으로 둔다. 오늘 긁었다고 오늘 발표한 것으로 만들지 않는다.
PAGE_UPDATED = re.compile(
    r"(?:最終更新日|最終更新|更新日|Last\s+Updated)\s*[:：]?\s*"
    r"(20\d{2})\s*[./年]\s*(\d{1,2})\s*[./月]\s*(\d{1,2})\s*日?",
    re.I)


def page_updated_day(page):
    text = monir._txt(page or "")
    m = PAGE_UPDATED.search(text)
    if not m:
        return ""
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def get_bytes(url, timeout=25):
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read(6_000_000)
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError, ValueError) as e:
        print(f"  ! {url} {type(e).__name__} {str(e)[:40]}")
        return b""


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
        return {"v": VER, "rv": RULE_VER, "sites": {}, "codes": {},
                "done": [], "skip": {}, "homes": {}, "profile_miss": {}}
    try:
        got = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"v": VER, "rv": RULE_VER, "sites": {}, "codes": {},
                "done": [], "skip": {}, "homes": {}, "profile_miss": {}}
    got.setdefault("sites", {})
    got.setdefault("codes", {})
    got.setdefault("done", [])
    got.setdefault("skip", {})
    got.setdefault("homes", {})
    got.setdefault("profile_miss", {})
    # **'못 찾았다'고 적어 둔 것도 규칙 판이 바뀌면 비운다.** 안 그러면 넓힌
    # 규칙이 옛 miss 에 영영 안 닿는다 — 월매출 목록에서 겪은 것과 같은 병이다
    # ('훑은 날'과 '모은 줄'은 다른 것이다).
    if got.get("rv") != RULE_VER:
        # 회사 IR 페이지는 늘 거기 있으므로 통째로 다시 받는다(위 주석).
        print(f"  규칙 판이 바뀌었다({got.get('rv')} -> {RULE_VER})"
              f" — 모아 둔 것을 비우고 다시 받는다")
        got["sites"], got["codes"] = {}, {}
        got["done"], got["skip"] = [], {}
        got["rv"] = RULE_VER
    return got


def save(rec):
    months = sum(len(v.get("months") or {}) for v in rec["codes"].values())
    pdf_months = sum(len(v.get("pdf") or {}) for v in rec["codes"].values())
    rec.update({
        "v": VER, "rv": RULE_VER,
        "source": "회사 IR 페이지 (月次 표)",
        "note": ("회사가 제 사이트에 올린 월차 표에서 읽는다. 값은 전년동월비"
                 "(%)뿐이고 금액은 없다. 회계연도가 적힌 표는 그 해로 읽으므로"
                 " 옛 회계연도 표를 함께 싣는 회사는 이력이 길다."),
        "companies": len(rec["codes"]),
        "months": months,
        "pdf_months": pdf_months,
        "done": sorted(rec["done"])[-4000:],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"  저장 {len(rec['codes'])}사 · 표 {months}달 · PDF {pdf_months}달"
          f" -> {OUT.name}")
    save_coverage(rec)


def universe_codes():
    """seed + 이미 관측한 월차 소스의 합집합.

    seed 파일은 discovery 출발점일 뿐 정답표가 아니다. TDnet/기사/IR 에 새 코드가
    먼저 나타나면 다음 실행부터 자동으로 universe 에 들어온다.
    """
    out, seen = [], set()
    try:
        d = json.loads(UNIVERSE.read_text(encoding="utf-8"))
        base = d.get("codes") or []
    except (ValueError, OSError):
        base = []
    for code in list(monir.IR_SITES) + [str(x).strip() for x in base]:
        if code and code not in seen:
            seen.add(code); out.append(code)
    for fn in ("monthly_jp.json", "monthly_web_jp.json", "monthly_ir_jp.json"):
        p = HERE / "data" / fn
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if fn == "monthly_jp.json":
            more = [str(r.get("code") or "") for r in d.get("rows") or []]
        else:
            more = list((d.get("codes") or {}).keys())
        for code in more:
            if code and code not in seen:
                seen.add(code); out.append(code)
    return out


def known_names():
    """저장소 안에서 이미 아는 일본 회사명을 모은다. 못 찾으면 코드 자체를 쓴다."""
    out = {code: row[0] for code, row in monir.IR_SITES.items()}
    for fn in ("earnings.json", "earnings_jp_past.json", "earnings_jp_sched.json",
               "monthly_jp.json"):
        p = HERE / "data" / fn
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for r in d.get("rows") or []:
            code = str(r.get("code") or "").strip()
            name = str(r.get("name") or "").strip()
            if code and name:
                out.setdefault(code, name)
    return out


def profile_home(code, rec, today):
    """Yahoo assetProfile 에서 공식 홈페이지를 한 번 찾아 cache 한다."""
    homes = rec.setdefault("homes", {})
    if homes.get(code):
        return homes[code]
    miss = rec.setdefault("profile_miss", {}).get(code)
    if miss:
        try:
            if (today - date.fromisoformat(miss)).days < PROFILE_MISS_DAYS:
                return ""
        except ValueError:
            pass
    try:
        p = scrape_caps.asset_profile(f"{code}.T")
        home = str(p.get("website") or "").strip()
    except Exception as e:
        print(f"  ! {code} 홈페이지 profile 실패: {type(e).__name__}")
        home = ""
    if home.startswith(("http://", "https://")):
        homes[code] = home
        rec["profile_miss"].pop(code, None)
        return home
    rec["profile_miss"][code] = today.isoformat()
    return ""


def iter_targets(rec, today):
    """고정 IR_SITES + 300여 seed 를 하나의 (code,name,home,fixed) 목록으로."""
    names = known_names()
    seed = universe_codes()
    order = []
    seen = set()
    for code in list(monir.IR_SITES) + seed:
        if code in seen:
            continue
        seen.add(code)
        order.append(code)

    # 공식 홈페이지를 아직 모르는 종목은 한 실행에 HOME_PER_RUN 개만 새로 찾는다.
    left = HOME_PER_RUN
    out = []
    for code in order:
        if code in monir.IR_SITES:
            name, home, fixed = monir.IR_SITES[code]
        else:
            name, fixed = names.get(code, code), ""
            home = rec.setdefault("homes", {}).get(code, "")
            due = True
            missed = rec.setdefault("profile_miss", {}).get(code)
            if missed:
                try:
                    due = (today - date.fromisoformat(missed)).days >= PROFILE_MISS_DAYS
                except ValueError:
                    pass
            if not home and due and left > 0:
                home = profile_home(code, rec, today)
                left -= 1
        if home:
            out.append((code, name, home, fixed))
    return out


def _load_codes_file(name):
    p = HERE / "data" / name
    if not p.exists():
        return set()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return set()
    if name == "monthly_jp.json":
        return {str(r.get("code") or "") for r in d.get("rows") or [] if r.get("code")}
    return set((d.get("codes") or {}).keys())


def save_coverage(rec):
    """왜 어떤 회사가 안 보이는지 한 파일에서 바로 알 수 있게 한다."""
    seed = universe_codes()
    td = _load_codes_file("monthly_jp.json")
    web = _load_codes_file("monthly_web_jp.json")
    ir = set((rec.get("codes") or {}).keys())
    sites = rec.get("sites") or {}
    homes = rec.get("homes") or {}
    rows = {}
    for code in seed:
        cr = (rec.get("codes") or {}).get(code) or {}
        ms = dict(cr.get("months") or {})
        ms.update(cr.get("pdf") or {})
        site = sites.get(code) or {}
        if code in ir and ms:
            status = "ok"
        elif site.get("page"):
            status = "parser_failed"
        elif site.get("miss"):
            status = "no_monthly_found"
        elif code in td or code in web:
            status = "covered_elsewhere"
        elif homes.get(code) or code in monir.IR_SITES:
            status = "pending_discovery"
        else:
            status = "home_pending"
        rows[code] = {
            "status": status,
            "home": homes.get(code) or (monir.IR_SITES.get(code) or ("", "", ""))[1],
            "source_url": site.get("page") or cr.get("page") or "",
            "latest_month": max(ms) if ms else "",
            "months": len(ms),
            "tdnet": code in td,
            "web": code in web,
            "ir": code in ir,
        }
    payload = {
        "v": 1,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": len(seed),
        "homes": sum(bool(v.get("home")) for v in rows.values()),
        "official_ir_data": sum(v["ir"] and bool(v["latest_month"]) for v in rows.values()),
        "covered_any": sum(v["tdnet"] or v["web"] or v["ir"] for v in rows.values()),
        "status": {k: sum(v["status"] == k for v in rows.values())
                   for k in sorted({v["status"] for v in rows.values()})},
        "codes": rows,
    }
    COVERAGE.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")


def read_pdfs(page, url, pdf, rec, today, budget):
    """월차 페이지에 걸린 PDF 를 기존 montable 규칙으로 읽는다."""
    done, skip = set(rec.get("done") or []), rec.setdefault("skip", {})
    cur = today.strftime("%Y-%m")
    new = n = 0
    looked = False
    for u, lab, when in monir.pdf_links(page, url):
        if budget <= 0 or n >= PDF_PER_COMPANY:
            break
        if u in done or u in skip:
            continue
        looked = True
        data = get_bytes(u)
        time.sleep(PAUSE)
        budget -= 1
        n += 1
        if not data:
            continue
        rec.setdefault("done", []).append(u)
        try:
            got = montable.read(data, when, title=lab)
        except Exception as e:
            skip[u] = type(e).__name__
            continue
        rows = (got or {}).get("rows") or []
        if not rows:
            skip[u] = "표없음"
            continue
        for r in rows:
            per = r.get("period") or ""
            if not per or per >= cur:
                continue
            # PDF 를 **오늘 내려받았다는 사실은 발표일이 아니다.**
            # 역사 수치에는 원문 URL만 보관하고, 공개일은 별도 이벤트 소스에서 정한다.
            row = {"doc": u}
            if r.get("rev") is not None:
                row["rev"] = r["rev"]
            if r.get("yoy") is not None:
                row["yoy"] = r["yoy"]
            if r.get("metric"):
                row["metric"] = r["metric"]
            if not ("rev" in row or "yoy" in row):
                continue
            if per not in pdf:
                new += 1
            pdf[per] = row
    return pdf, new, budget, looked


def discover(top):
    """맨 위 -> (月次 목록 주소, 두드린 횟수). 못 찾으면 ''.

    **PDF 만 걸린 페이지는 그 페이지가 곧 목록이다.** 첫 PDF 를 月次 '페이지'
    로 잡으면 그 한 장만 보고 나머지 달을 통째로 놓친다(브ックオフ 9278).
    """
    page = get(top)
    if not page:
        return "", 1
    url = monir.find_monthly(page, top)
    if url:
        return url, 1
    if monir.has_monthly_pdf(page, top):
        return top, 1
    ir = monir.find_ir(page, top)
    if not ir:
        # **맨 위에 IR 링크가 없는 곳이 넷 있었다**(92차 — 카와치약품·코메효·
        # 야마오카야·마츠야). 가장 흔한 자리를 한 번만 두드려 본다. 찾으면
        # 적어 두므로 값은 처음 한 번뿐이다.
        ir = top.rstrip("/") + "/ir/"
    time.sleep(PAUSE)
    ir_page = get(ir)
    if not ir_page:
        return "", 2
    url = monir.find_monthly(ir_page, ir)
    if url:
        return url, 2
    if monir.has_monthly_pdf(ir_page, ir):
        return ir, 2
    return "", 2


def main():
    rec = load()
    sites, codes = rec["sites"], rec["codes"]
    today = date.today()
    now = datetime.now(timezone.utc)
    t0 = time.time()
    fresh = new_months = miss = 0
    pdf_left = PDF_PER_RUN

    targets = iter_targets(rec, today)
    start = int(rec.get("cursor") or 0) % len(targets) if targets else 0
    targets = targets[start:] + targets[:start]
    for pos, (code, name, top, fixed) in enumerate(targets):
        if time.time() - t0 > BUDGET:
            print("  시간이 다 됐다 — 여기까지 저장하고 다음 실행에 잇는다")
            break
        if miss >= GIVE_UP_AFTER:
            print(f"  연속 {miss}번 못 받았다 — 이 바퀴는 접는다")
            break
        # 다음 실행은 여기 다음 회사에서 시작한다. 300개로 넓힌 뒤에도
        # 앞쪽 회사의 느린 응답/실패 때문에 뒤쪽이 영원히 굶지 않게 한다.
        # 중단 조건을 지난 뒤에만 옮겨, 아직 처리하지 않은 회사를 건너뛰지 않는다.
        rec["cursor"] = (start + pos + 1) % len(targets) if targets else 0
        cur = codes.get(code) or {}
        # 이미 달이 쌓인 회사는 자주 안 두드린다. 한 달에 한 번 올라오는 값이다.
        if cur.get("months") and cur.get("ts") and cur.get("pdf_ts"):
            try:
                age = (now - datetime.fromisoformat(cur["ts"])).total_seconds()
                p_age = (now - datetime.fromisoformat(
                    cur["pdf_ts"])).total_seconds()
                if age < FRESH_HOURS * 3600 and p_age < PDF_FRESH_HOURS * 3600:
                    continue
            except ValueError:
                pass
        site = sites.get(code) or {}
        url = fixed or site.get("page") or ""
        if not url:
            # 얼마 전에 못 찾은 곳은 건너뛴다.
            if site.get("miss"):
                try:
                    if (today - date.fromisoformat(site["miss"])).days < MISS_DAYS:
                        continue
                except ValueError:
                    pass
            url, _n = discover(top)
            time.sleep(PAUSE)
            if not url:
                print(f"  {code} {name}: 月次 링크 못 찾음")
                sites[code] = {"miss": today.isoformat()}
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
        # **PDF 목록 꼴도 같이 본다.** 표를 안 싣고 달마다 PDF 한 장을 거는
        # 회사가 많다 — 패스트리는 회계연도 한 장에 열두 달이라 세 장으로
        # 36달이 들어온다(91차). 못 읽는 PDF 는 그냥 지나간다.
        pdf = dict(cur.get("pdf") or {})
        looked = False
        if pdf_left > 0:
            pdf, n_new, pdf_left, looked = read_pdfs(page, url, pdf, rec,
                                                     today, pdf_left)
            new_months += n_new
        # **이미 받아 둔 달을 빈 결과로 덮지 않는다.** 회사 사이트가 회계연도를
        # 넘기며 옛 표를 내려도 우리 이력은 그대로 남아야 한다.
        months = {
            p: {k: val for k, val in (v or {}).items() if k != "day"}
            for p, v in (cur.get("months") or {}).items()
        }
        pdf = {
            p: {k: val for k, val in (v or {}).items() if k != "day"}
            for p, v in pdf.items()
        }
        for p, v in got.items():
            # 회사 표의 달별 수치에는 발표일을 억지로 달지 않는다.
            # "처음 본 날"도 발표일이 아니므로 저장하지 않는다.
            row = {k: val for k, val in v.items() if k in ("same", "all")}
            if p not in months:
                new_months += 1
            months[p] = row
        if not (months or pdf):
            print(f"  {code} {name}: 0달 ({url})")
            continue
        codes[code] = {"name": name, "page": url, "months": months,
                       "ts": now.isoformat(timespec="seconds")}
        updated = page_updated_day(page)
        if updated:
            codes[code]["updated"] = updated
        elif cur.get("updated"):
            codes[code]["updated"] = cur["updated"]
        if looked:
            codes[code]["pdf_ts"] = now.isoformat(timespec="seconds")
        elif cur.get("pdf_ts"):
            codes[code]["pdf_ts"] = cur["pdf_ts"]
        if pdf:
            codes[code]["pdf"] = pdf
        ks = sorted(months) or sorted(pdf)
        print(f"  {code} {name}: 표 {len(months)}달 · PDF {len(pdf)}달"
              f" ({ks[0]} ~ {ks[-1]})")

    print(f"  두드린 회사 {fresh} · 새 달 {new_months}")
    save(rec)


def print_coverage():
    """네트워크 없이 현재 저장 파일만으로 coverage 요약을 찍는다."""
    rec = load()
    save_coverage(rec)
    try:
        d = json.loads(COVERAGE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"coverage 읽기 실패: {e}")
        return
    print(f"월차 universe {d.get('seed', 0)}개 · 공식 홈페이지 {d.get('homes', 0)}개"
          f" · IR 숫자 {d.get('official_ir_data', 0)}개"
          f" · 어느 소스로든 coverage {d.get('covered_any', 0)}개")
    for k, v in sorted((d.get("status") or {}).items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    if "--coverage" in sys.argv:
        print_coverage()
    elif "--probe" in sys.argv:
        # 한 회사만 떠본다: python scrape_mon_ir.py --probe 9843
        code = sys.argv[sys.argv.index("--probe") + 1]
        if code in monir.IR_SITES:
            name, top, fixed = monir.IR_SITES[code]
        else:
            rec = load()
            name = known_names().get(code, code)
            top = rec.setdefault("homes", {}).get(code) or profile_home(code, rec, date.today())
            fixed = ""
        url = fixed or (discover(top)[0] if top else "")
        print(name, url)
        page = get(url) if url else ""
        print(f"  {len(page)//1024}KB · 표 {page.count('<table')}")
        for p, v in sorted(monir.read(page, date.today()).items()):
            print("   ", p, v)
    else:
        main()
