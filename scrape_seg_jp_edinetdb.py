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

**분기도 준다 — `?period=quarterly`.** 오래 "연간뿐"이라 적어 두었는데 그건
매개변수를 안 써 본 것이었다(35차 떠보기). 아식스로 재보니 연간 82행이
**분기 223행**이 되고 `quarter` 칸이 붙는다. 다만 두 가지를 지켜야 한다.

  * **누계다**(`valueBasis: "ytd"`). 1분기는 그 분기지만 2분기는 상반기,
    3분기는 9개월치다. TDnet 결산단신과 똑같은 문제라 같은 방법으로 되돌린다.
  * **4분기가 없다.** 四半期報告書는 1~3분기만 낸다(2024년부터는 半期報告書라
    상반기뿐이다 — `docTypeCode` 160). 결산 분기는 **연간 − 3분기 누계**로
    되살려야 하므로 연간도 계속 받아 둔다.

그래서 한 종목에 두 벌을 담는다 — `q`(분기 누계)와 `rows`(연간). 분기 쪽을
먼저 채우고, 연간은 그 뒤에 받는다.

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
# 분기 쪽은 분기마다 새 공시가 오므로 더 자주 본다.
Q_REFRESH_DAYS = 80
# 담는 판. 올리면 모아둔 것을 다시 받는다(분기 모드로 갈아탈 때 한 번 올렸다).
HIST_VER = 2
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
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if d.get("v") != HIST_VER:
        # 담는 판이 바뀌었다. **연간 행은 그대로 둔다** — 그건 다시 받아도 같고,
        # 하루 100건짜리 예산으로 1,300종목을 다시 훑으면 두 주가 날아간다.
        # 분기 쪽만 비어 있으므로 분기 대기줄이 알아서 채운다.
        print(f"  담는 판이 바뀌었다(v {d.get('v')} -> {HIST_VER}). "
              f"연간은 두고 분기를 채운다.")
        d["v"] = HIST_VER
        d.setdefault("qdone", {})
    return d


def save_out(done, stocks, qdone=None, quiet=False):
    payload = {
        "source": ("EDINET DB (edinetdb.jp) — 보고 세그먼트. "
                   "분기(四半期·半期報告書, 누계)와 연간(有価証券報告書)."),
        "note": ("분기는 `q`, 연간은 `rows`. 분기 값은 **누계(ytd)**라 build.py 가 "
                 "앞 분기를 빼서 되돌리고, 4분기는 연간에서 3분기 누계를 빼 만든다. "
                 "조정·소거 줄 걸러내기와 이름 차례도 build.py 몫이다 — "
                 "여기는 원자료 그대로."),
        "v": HIST_VER,
        "count": sum(1 for v in stocks.values() if v.get("rows")),
        "qcount": sum(1 for v in stocks.values() if v.get("q")),
        "done": done,
        "qdone": qdone or {},
        "stocks": stocks,
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    if not quiet:
        print(f"분기 {payload['qcount']:,}종목 · 연간 {payload['count']:,}종목 -> {OUT}")


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


def fetch_segments(ecode, quarterly=False):
    """한 종목의 세그먼트 행들. (rows, 상태) — 상태는 'ok'/'none'.

    연간이면 `[회계연도, 부문명, 매출, 영업이익]`,
    분기면 `[회계연도, 분기, 부문명, 영문명, 매출, 영업이익, 서류종류, 값기준]`.
    **되돌리기(누계 -> 분기)는 여기서 안 한다** — build.py 몫이다. 규칙을 고칠
    때마다 하루 100건 예산으로 다시 받아야 한다면 그 규칙은 못 고친다.
    """
    url = f"{BASE}/companies/{ecode}/segments"
    if quarterly:
        url += "?period=quarterly"
    try:
        body = get(url, key=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return [], "none"             # 부문을 안 나누는(못 찾은) 회사
        if e.code in (401, 403):
            raise Blocked(f"키 거부 HTTP {e.code} — EDINETDB_KEY 를 확인할 것")
        if e.code == 429:
            raise Blocked("오늘 한도(100건)를 다 썼다")
        # 503 cache_warming 은 '자료 없음'이 아니다. 이 종목만 건너뛴다.
        if e.code == 503:
            return [], "later"
        raise Blocked(f"HTTP {e.code}")
    j = json.loads(body)
    data = j.get("data") or []
    meta = j.get("meta") or {}
    # 조용한 잘림 금지 — 다음 쪽이 있다는 낌새면 크게 적는다.
    if meta.get("next") or meta.get("has_more"):
        print(f"    ! {ecode}: 응답이 잘린 낌새(meta={meta}) — 파서를 손봐야 한다",
              file=sys.stderr, flush=True)
    # **분기를 달라고 했는데 연간이 오면 분기라고 적지 않는다.** 매개변수를
    # 조용히 무시하는 API 가 흔하다 — 그걸 분기로 담으면 막대가 거짓말을 한다.
    if quarterly and meta.get("period") not in (None, "quarterly"):
        raise Blocked(f"분기를 달라고 했는데 period={meta.get('period')} 가 왔다")
    rows = []
    for r in data:
        fy, nm = r.get("fiscalYear"), (r.get("segmentName") or "").strip()
        if not fy or not nm:
            continue
        if not quarterly:
            rows.append([int(fy), nm, r.get("revenue"), r.get("operatingIncome")])
            continue
        q = r.get("quarter")
        if q is None:
            continue                      # 분기 표시가 없으면 분기로 못 쓴다
        rows.append([int(fy), q, nm, (r.get("segmentNameEn") or "").strip(),
                     r.get("revenue"), r.get("operatingIncome"),
                     r.get("docTypeCode"), r.get("valueBasis") or meta.get("valueBasis")])
    return rows, "ok"


def collect():
    if not KEY:
        print("EDINETDB_KEY 가 없다 — 오늘은 쉰다.")
        return
    prev = load_out()
    done = prev.get("done", {})            # 연간을 받은 날
    qdone = prev.get("qdone", {})          # 분기를 받은 날
    stocks = prev.get("stocks", {})
    emap = ecode_map()

    today = date.today().isoformat()
    cut = (date.today() - timedelta(days=REFRESH_DAYS)).isoformat()
    qcut = (date.today() - timedelta(days=Q_REFRESH_DAYS)).isoformat()
    uni = [c for c in universe() if c in emap]
    # **분기가 먼저다.** 회원님이 원하시는 것이 분기 부문 이력이고, 연간은 이미
    # 72종목이 들어와 있다. 연간은 분기를 다 채운 뒤 남는 예산으로 받는다
    # (4분기를 '연간 − 3분기 누계'로 되살리는 데 쓴다).
    qqueue = [c for c in uni if c not in qdone or qdone[c] < qcut]
    aqueue = [c for c in uni if c not in done or done[c] < cut]
    plan = [(c, True) for c in qqueue[:PER_RUN]]
    if len(plan) < PER_RUN:
        plan += [(c, False) for c in aqueue[:PER_RUN - len(plan)]]
    print(f"  대기줄 분기 {len(qqueue):,} · 연간 {len(aqueue):,}"
          f" · 이번 실행 {len(plan)}건(분기 {sum(1 for _c, q in plan if q)})")

    gotq = gota = skipped = 0
    for c, quarterly in plan:
        try:
            rows, status = fetch_segments(emap[c], quarterly=quarterly)
        except Blocked as e:
            print(f"  멈춤: {e}", file=sys.stderr, flush=True)
            break
        except Exception as e:
            print(f"    {c} 실패({type(e).__name__}) — 다음에 다시", flush=True)
            skipped += 1
            time.sleep(PAUSE)
            continue
        if status == "later":
            # 서버가 자료를 데우는 중이다(503 cache_warming). 받은 날로 적지
            # 않는다 — 적으면 그 종목이 다음 주기까지 통째로 빠진다.
            print(f"    {c}: 서버가 준비 중 — 다음에 다시", flush=True)
            skipped += 1
            time.sleep(PAUSE)
            continue
        rec = stocks.setdefault("jp:" + c, {"e": emap[c], "cur": "JPY"})
        rec["e"], rec["cur"] = emap[c], "JPY"
        if quarterly:
            qdone[c] = today
            if rows:
                rec["q"], rec["qts"] = rows, today
                gotq += 1
                per = sorted({(r[0], r[1]) for r in rows})
                print(f"    {c}: 분기 {len(rows)}행 · "
                      f"{per[0][0]}Q{per[0][1]}~{per[-1][0]}Q{per[-1][1]}", flush=True)
            else:
                print(f"    {c}: 분기 부문 없음", flush=True)
        else:
            done[c] = today
            if rows:
                rec["rows"], rec["ts"] = rows, today
                gota += 1
                yrs = sorted({r[0] for r in rows})
                print(f"    {c}: 연간 {len(rows)}행 · FY{yrs[0]}~FY{yrs[-1]}", flush=True)
            else:
                print(f"    {c}: 연간 부문 없음", flush=True)
        if not rec.get("q") and not rec.get("rows"):
            stocks.pop("jp:" + c, None)
        if (gotq + gota) and (gotq + gota) % 15 == 0:
            save_out(done, stocks, qdone, quiet=True)
        time.sleep(PAUSE)
    save_out(done, stocks, qdone)
    print(f"  이번 실행: 분기 {gotq} · 연간 {gota} · 실패(재시도 예정) {skipped}"
          f" · 남은 대기줄 분기 {max(0, len(qqueue) - sum(1 for _c, q in plan if q)):,}")


def probe():
    """아식스 한 종목의 **분기** 응답을 통째로 본다. 예산 1건.

    값을 잘라서 찍지 않는다 — 35차에서 220자로 자르는 바람에 정작 봐야 할
    quarter·revenue 가 안 보였다.
    """
    rows, status = fetch_segments("E02378", quarterly=True)
    print(f"아식스 분기: {status} · {len(rows)}행")
    if not rows:
        return
    from collections import Counter
    print("  서류종류:", Counter(r[6] for r in rows).most_common())
    print("  값기준  :", Counter(r[7] for r in rows).most_common())
    print("  분기    :", Counter(r[1] for r in rows).most_common())
    yrs = sorted({r[0] for r in rows})
    print(f"  회계연도: {yrs}")
    print("  부문:", sorted({r[2] for r in rows}))
    print("  영문명:", sorted({r[3] for r in rows if r[3]})[:8])
    for fy in yrs[-3:]:
        print(f"  -- FY{fy}")
        for r in sorted((x for x in rows if x[0] == fy), key=lambda x: (x[1], x[2])):
            print(f"     Q{r[1]} {r[2][:18]:20s} 매출 {r[4]!s:>16s} "
                  f"영업익 {r[5]!s:>14s} ({r[6]}/{r[7]})")


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        collect()
