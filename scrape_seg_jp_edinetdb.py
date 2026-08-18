# -*- coding: utf-8 -*-
"""일본 부문별 매출의 **연간 이력**(2014년치부터) — EDINET DB 무료 API.

왜 이 소스인가. TDnet 결산단신은 한 달치만 남아 옛 분기가 없고, EDINET 공식
API 는 키 포털이 막혀 있다(가입을 마쳐도 키 화면이 「現在使用できない」).
우회로를 훑은 끝에 edinetdb.jp 가 남았다 — EDINET 有価証券報告書를 이미
파싱해 종목당 요청 한 번으로 **연간 세그먼트 12년치**를 준다. irbank 는
데이터센터 IP 를 403 으로 막지만(닛케이와 같은 벽) 여기는 열려 있는 것을
CI 에서 확인했다(probe 20차).

  인증   X-API-Key 헤더 (무료 100건/일 · https://edinetdb.jp/developers)
  경로   GET /v1/companies/{EDINET코드}/segments
  행     {fiscalYear, segmentName(일본어), revenue, operatingIncome, …}

**연간이다 — 분기가 아니다.** 옛 분기는 이 길로도 없다(有報 기준). 그래서
화면은 분기 차트와 **따로** 연간 차트를 단다. 섞으면 막대 높이가 거짓말이 된다.

**원자료를 그대로 담는다.** 조정 줄을 빼고 이름을 줄이는 일은 build.py 가
한다 — 규칙을 고칠 때 100건/일 예산으로 다시 받아야 한다면 그 규칙은 못 고친다.

종목코드 → EDINET코드 매핑은 EDINET 이 내는 코드리스트 zip(무인증, cp932 CSV)
한 방이다. 검색 API 로 종목마다 두드리면 그것만으로 하루 예산이 끝난다.
"""

import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_jp_hist.json"

BASE = "https://edinetdb.jp/v1"
CODELIST = ("https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/"
            "Edinetcode.zip")
KEY = os.environ.get("EDINETDB_KEY", "")
PER_RUN = int(os.environ.get("SEG_HIST_PER_RUN", "90"))   # 무료 100건/일에서 여유
REFRESH_DAYS = 180        # 有報는 연 1회 — 반년 지난 종목만 다시 받는다
PAUSE = 0.25
UA = {"User-Agent": "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)",
      "Accept": "application/json,*/*"}


class Blocked(Exception):
    pass


def get(url, key=False, timeout=30):
    h = dict(UA)
    if key:
        h["X-API-Key"] = KEY
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_out():
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def save_out(done, stocks, quiet=False):
    payload = {
        "source": "EDINET DB (edinetdb.jp) — 有価証券報告書의 보고 세그먼트, 연간",
        "note": ("옛 분기는 어느 길로도 없어 연간 이력이다. 조정·소거 줄 걸러내기와 "
                 "이름 차례는 build.py 가 한다 — 여기는 원자료 그대로."),
        "v": 1,
        "count": sum(1 for v in stocks.values() if v.get("rows")),
        "done": done,
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    if not quiet:
        print(f"{payload['count']:,}종목 연간 부문 이력 -> {OUT}")


def ecode_map():
    """종목코드(4자리) -> EDINET 코드. 공식 코드리스트 zip, 요청 한 번."""
    blob = get(CODELIST, timeout=60)
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    text = z.read(name).decode("cp932", "replace")
    rows = list(csv.reader(io.StringIO(text)))
    # 첫 줄은 갱신일 따위의 머리말, 둘째 줄이 열 이름이다.
    header = rows[1]
    e_i = next(i for i, c in enumerate(header) if "ＥＤＩＮＥＴコード" in c or "EDINET" in c)
    s_i = next(i for i, c in enumerate(header) if "証券コード" in c)
    out = {}
    for r in rows[2:]:
        if len(r) <= max(e_i, s_i):
            continue
        sec = (r[s_i] or "").strip()
        if len(sec) == 5:                 # '79360' 꼴 — 뒤 0 을 뗀 네 자리가 종목코드
            out[sec[:4]] = r[e_i].strip()
    print(f"  코드리스트: {len(out):,}종목 매핑")
    return out


def universe():
    """캘린더에 오르는 일본 종목을 시총 큰 순으로. 아식스는 맨 앞(검증용)."""
    codes = set()
    for name in ("earnings.json", "earnings_jp_past.json", "earnings_jp_sched.json"):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for r in d.get("rows", []):
            c = r.get("code") if isinstance(r, dict) else None
            if c:
                codes.add(c)
    caps = {}
    p = HERE / "data" / "caps.json"
    if p.exists():
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, OSError):
            pass
    order = sorted(codes, key=lambda c: -(caps.get("jp:" + c) or 0))
    if "7936" in codes:
        order.remove("7936")
        order.insert(0, "7936")
    return order


def fetch_segments(ecode):
    """한 종목의 연간 세그먼트 행들. (rows, 상태) — 상태는 'ok'/'none'."""
    try:
        body = get(f"{BASE}/companies/{ecode}/segments", key=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], "none"             # 부문을 안 나누는(못 찾은) 회사
        if e.code in (401, 403):
            raise Blocked(f"키 거부 HTTP {e.code} — EDINETDB_KEY 를 확인할 것")
        if e.code == 429:
            raise Blocked("오늘 한도(100건)를 다 썼다")
        raise Blocked(f"HTTP {e.code}")
    j = json.loads(body)
    data = j.get("data") or []
    meta = j.get("meta") or {}
    # 조용한 잘림 금지 — 다음 쪽이 있다는 낌새면 크게 적는다.
    if meta.get("next") or meta.get("has_more"):
        print(f"    ! {ecode}: 응답이 잘린 낌새(meta={meta}) — 파서를 손봐야 한다",
              file=sys.stderr, flush=True)
    rows = []
    for r in data:
        fy, nm = r.get("fiscalYear"), (r.get("segmentName") or "").strip()
        if not fy or not nm:
            continue
        rows.append([int(fy), nm, r.get("revenue"), r.get("operatingIncome")])
    return rows, "ok"


def collect():
    if not KEY:
        print("EDINETDB_KEY 가 없다 — 오늘은 쉰다.")
        return
    prev = load_out()
    done = prev.get("done", {})
    stocks = prev.get("stocks", {})
    emap = ecode_map()

    cutoff = (date.today() - timedelta(days=REFRESH_DAYS)).isoformat()
    queue = [c for c in universe() if c in emap
             and (c not in done or done[c] < cutoff)]
    print(f"  대기줄 {len(queue):,}종목 · 이번 실행 {min(PER_RUN, len(queue))}종목")

    got = skipped = 0
    for c in queue[:PER_RUN]:
        try:
            rows, status = fetch_segments(emap[c])
        except Blocked as e:
            print(f"  멈춤: {e}", file=sys.stderr, flush=True)
            break
        except Exception as e:
            print(f"    {c} 실패({type(e).__name__}) — 다음에 다시", flush=True)
            skipped += 1
            time.sleep(PAUSE)
            continue
        done[c] = date.today().isoformat()
        if rows:
            stocks["jp:" + c] = {"e": emap[c], "ts": done[c], "cur": "JPY",
                                 "rows": rows}
            got += 1
            yrs = sorted({r[0] for r in rows})
            print(f"    {c}: {len(rows)}행 · FY{yrs[0]}~FY{yrs[-1]}", flush=True)
        else:
            print(f"    {c}: 부문 없음", flush=True)
        if got and got % 15 == 0:
            save_out(done, stocks, quiet=True)
        time.sleep(PAUSE)
    save_out(done, stocks)
    print(f"  이번 실행: 받음 {got} · 실패(재시도 예정) {skipped}"
          f" · 남은 대기줄 {max(0, len(queue) - PER_RUN):,}")


def probe():
    """아식스 한 종목만 떠서 형식을 눈으로 본다. 예산 1건."""
    rows, status = fetch_segments("E02378")
    print(f"아식스: {status} · {len(rows)}행")
    for r in rows[:8]:
        print("  ", r)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        collect()
