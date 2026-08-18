# -*- coding: utf-8 -*-
"""
부문별 매출의 **최근 두세 분기** — EDGAR 제출 서류에서 직접.

`scrape_seg_sec.py` 가 받는 SEC 벌크(Financial Statement Data Sets)는 **접수된
분기 기준**이라 구조적으로 늦다. 6월 결산을 7월에 신고하면 그 서류는 2026q3
묶음에 들어가고, 그 묶음은 10월에나 나온다. 재보니 이랬다.

    부문 자료가 있는 1,600종목
      총매출과 같은 분기까지 있다      277  (17%)
      한 분기 뒤짐                    243  (15%)
      **두 분기 뒤짐                  972  (61%)**
      세 분기 이상                    108  ( 7%)

메우라고 붙여 둔 stockanalysis(`scrape_fin_seg.py`)는 **1,784종목 중 1,484종목이
빈 기록**이었다. 그쪽은 부문 페이지가 있는 종목이 얼마 안 된다.

그런데 **서류 자체는 낸 그날 공개된다.** 벌크가 늦는 것이지 자료가 없는 게 아니다.
2019년부터 모든 10-Q/10-K 는 인라인 XBRL 이고, SEC 가 거기서 뽑은 인스턴스
(`aapl-20260628_htm.xml`)를 같은 폴더에 같이 올린다. 그 안에는 문맥(context)마다
부문 축이 그대로 붙어 있다 — 벌크의 `segments` 칸과 같은 것이다.

    <context id="c-42">
      <entity><segment>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >amd:DataCenterMember</xbrldi:explicitMember>
      </segment></entity>
      <period><startDate>2026-03-30</startDate><endDate>2026-06-28</endDate></period>
    </context>
    <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
      contextRef="c-42" unitRef="usd">3240000000</...>

그래서 **가리는 규칙은 벌크와 한 벌을 쓴다.** 축을 고르는 법, 상계 줄을 빼는 법,
누계를 분기로 되돌리는 법이 다 `scrape_seg_sec.py` 에 있고 여기서 그대로 불러 쓴다.
같은 판단을 두 군데 적어 두면 반드시 갈라진다.

## 어떻게 찾나

종목마다 두드리면 2,300번이다. 대신 **분기 색인 한 장**으로 그 분기에 접수된 모든
서류를 한 번에 받는다.

    https://www.sec.gov/Archives/edgar/full-index/2026/QTR3/form.idx

거기서 우리 종목의 10-Q/10-K 만 골라, 서류마다 두 번 두드린다.

    index.json      폴더 목록에서 인스턴스 파일 이름을 찾는다 (몇 KB)
    …_htm.xml       인스턴스 (gzip 으로 받으면 300KB 쯤)

**gzip 을 꼭 켠다.** XML 은 10:1 로 줄어서, 3MB짜리가 300KB 로 온다. 이걸 안 켜면
한 번 돌 때 몇 GB 를 받게 된다.

## 조심할 것

**이미 뜯어본 서류는 다시 받지 않는다.** 접수번호를 `done` 에 적어 둔다. 이게
없으면 매 실행마다 수천 건을 다시 받는다.

**뒤진 종목부터 채운다.** 총매출은 2Q26 까지 있는데 부문은 4Q25 에 멈춘 종목이
회원님이 실제로 열어 보는 종목이다. 시총 큰 순으로 그런 종목부터 간다.

**한 서류에 여러 기간이 실린다.** 2분기 10-Q 에는 이번 3개월·상반기 누계·전년
같은 기간이 다 있다. 그래서 서류 두세 장이면 대여섯 분기가 모인다.

**빈손으로 끝내지 않는다.** 색인이 404 면 그 분기가 아직 없는 것이고, 인스턴스가
없으면(옛 서류·비XBRL) 그 서류만 건너뛴다. 연속으로 막히면 접는다.

결과: data/segments_edgar.json  (벌크와 같은 모양이라 build.py 가 이어 붙인다)
"""
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import scrape_seg_sec as ss          # 축·상계·누계 규칙을 한 벌로 쓴다

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_edgar.json"

EDGAR_VER = 1

IDX = "https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx"
FOLDER = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"

# 한 실행에 뜯어볼 서류 수. 실제로 재보니 **서류 하나에 0.4초**다(700건 4.7분).
# 값은 거의 다 그물에서 치른다 — 뜯는 것 자체는 3.5MB 인스턴스가 0.09초다.
# 처음 한 바퀴가 7,678건이라 700씩으로는 하루가 넘게 걸렸다. 2,200이면 15분이고
# 단계 한도(25분) 안이며, SEC 초당 10건 제한에도 절반쯤(초당 5건)이다.
PER_RUN = int(os.environ.get("SEG_EDGAR_PER_RUN", "2200"))
# 색인을 몇 분기치 볼까. 3이면 아홉 달치 — 벌크가 늦는 폭(두 분기)을 넉넉히 덮는다.
IDX_QUARTERS = int(os.environ.get("SEG_EDGAR_IDX_QUARTERS", "3"))
# **한 실행에** 회사마다 몇 장까지 볼까. 10-Q 한 장에 이번 분기와 전년 같은
# 분기가 함께 실리므로 두 장이면 네댓 분기가 모인다. 이렇게 잘라 두면 첫 실행이
# 모든 회사의 **가장 최근 서류**부터 훑는다 — 한 회사의 옛 서류 세 장을 받느라
# 다른 회사가 뒤로 밀리지 않는다. 남은 옛 서류는 다음 실행에서 이어 받는다.
PER_CIK = int(os.environ.get("SEG_EDGAR_PER_CIK", "2"))
PAUSE = float(os.environ.get("SEG_EDGAR_PAUSE", "0.13"))   # SEC 는 초당 10건까지
GIVE_UP_AFTER = 8

# 인스턴스가 아닌 XBRL 곁다리 파일들. 이름으로 걸러낸다.
NOT_INSTANCE = re.compile(r"_(cal|def|lab|pre)\.xml$|^FilingSummary\.xml$", re.I)


def get(url, timeout=120, binary=True):
    """gzip 을 켜서 받는다. 404 는 None, 그 밖의 실패는 Blocked."""
    req = urllib.request.Request(url, headers={
        "User-Agent": ss.SEC_UA,
        "Accept-Encoding": "gzip",
        "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw if binary else raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ss.Blocked(f"HTTP {e.code} {url}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, EOFError) as e:
        raise ss.Blocked(f"{e} {url}")


# ── 어느 서류를 볼까 ─────────────────────────────────────────────────────

def recent_index_quarters(n):
    """오늘부터 뒤로 n 분기. [(2026, 3), (2026, 2), ...]"""
    t = date.today()
    y, q = t.year, (t.month - 1) // 3 + 1
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


IDX_ROW = re.compile(r"^(10-[KQ][^ ]*)\s{2,}(.+?)\s{2,}(\d+)\s{2,}"
                     r"(\d{4}-\d{2}-\d{2})\s{2,}(\S+)\s*$")


def filings(y, q, want_cik):
    """분기 색인 한 장 -> [(cik, 접수번호, 접수일, 서식)]. 우리 종목만."""
    blob = get(IDX.format(y=y, q=q), timeout=240)
    if blob is None:
        return None                       # 아직 없는 분기
    out, seen10 = [], 0
    for line in blob.decode("latin-1").splitlines():
        m = IDX_ROW.match(line)
        if not m:
            # **조용한 0건을 만들지 않는다.** 색인 모양이 바뀌면 정규식이 아무것도
            # 못 잡는데, 그러면 '새 서류가 없다'와 똑같이 보인다. 10-Q 로 시작하는
            # 줄이 있는데 하나도 못 읽었으면 원문을 찍어 남긴다.
            if line.startswith("10-Q") or line.startswith("10-K"):
                seen10 += 1
                if seen10 <= 3 and not out:
                    print(f"  ! 색인 줄을 못 읽었다: {line[:120]!r}", file=sys.stderr)
            continue
        form, _name, cik, filed, path = m.groups()
        cik = int(cik)
        if cik not in want_cik:
            continue
        # edgar/data/320193/0000320193-26-000073.txt -> 0000320193-26-000073
        acc = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if len(acc) != 20:
            continue
        out.append((cik, acc, filed, form))
    return out


def instance_url(cik, acc):
    """폴더 목록에서 인스턴스 XML 을 찾는다. 없으면 None(비XBRL 서류)."""
    body = get(FOLDER.format(cik=cik, acc=acc.replace("-", "")) + "/index.json",
               timeout=60, binary=False)
    if body is None:
        return None
    try:
        items = json.loads(body)["directory"]["item"]
    except (ValueError, KeyError, TypeError):
        return None
    best = None
    for it in items:
        n = it.get("name", "")
        if not n.lower().endswith(".xml") or NOT_INSTANCE.search(n):
            continue
        # 인라인 XBRL 에서 뽑은 인스턴스는 `…_htm.xml` 이다. 그게 없으면
        # 옛 방식으로 따로 올린 인스턴스(`abc-20260630.xml`)를 쓴다.
        score = 2 if n.lower().endswith("_htm.xml") else 1
        size = it.get("size") or 0
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        if best is None or (score, size) > (best[0], best[1]):
            best = (score, size, n)
    if not best:
        return None
    return FOLDER.format(cik=cik, acc=acc.replace("-", "")) + "/" + best[2]


# ── 인스턴스 뜯기 ────────────────────────────────────────────────────────

def local(tag):
    return tag.rsplit("}", 1)[-1]


def strip_member(v):
    """'amd:DataCenterMember' -> 'DataCenter'. 벌크의 segments 칸과 같은 모양."""
    v = v.split(":")[-1]
    return v[:-6] if v.endswith("Member") and len(v) > 6 else v


def qtrs_of(start, end):
    """기간 길이를 분기 수로. 벌크의 `qtrs` 와 같은 뜻이다(1=분기 … 4=연간)."""
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    days = (e - s).days
    # 52/53주 회계력이라 며칠씩 어긋난다. 벌크와 같은 폭으로 본다.
    for q, lo, hi in ((1, 60, 125), (2, 150, 220), (3, 240, 310), (4, 330, 400)):
        if lo <= days <= hi:
            return q
    return None


def ddate_of(end):
    """종료일을 월말로 반올림한다 — 벌크의 ddate 가 그렇게 온다.
    엔비디아의 1월 28일 결산이 벌크에서는 1월 31일이다. 맞춰 두어야 이어 붙는다."""
    d = date.fromisoformat(end)
    # 다음 달 초에 가까우면 그 달 말이 아니라 다음 달 말이다(4월 2일 결산 따위).
    if d.day <= 6:
        m, y = (12, d.year - 1) if d.month == 1 else (d.month - 1, d.year)
        return ss.month_end(y, m).strftime("%Y%m%d")
    return ss.month_end(d.year, d.month).strftime("%Y%m%d")


def parse_instance(blob):
    """인스턴스 -> [(axis, member, sub_axis, tag, ddate, qtrs, value)]

    문맥을 먼저 다 읽고 사실을 훑는다. 두 번 훑는 대신 한 번에 담아 두는데,
    큰 서류가 30MB 까지 가므로 다 쓴 요소는 바로 버린다.
    """
    ctx = {}          # id -> (axis, member, sub_axis, ddate, qtrs)
    facts = []
    root = None
    try:
        it = ET.iterparse(io.BytesIO(blob), events=("start", "end"))
        for ev, el in it:
            if ev == "start":
                if root is None:
                    root = el
                continue
            name = local(el.tag)
            if name == "context":
                cid = el.get("id")
                dims = []
                for m in el.iter():
                    if local(m.tag) != "explicitMember":
                        continue
                    dim = ss.norm_axis(m.get("dimension") or "")
                    val = strip_member((m.text or "").strip())
                    if dim and val:
                        dims.append(f"{dim}={val}")
                start = endd = ""
                for p in el.iter():
                    lp = local(p.tag)
                    if lp == "startDate":
                        start = (p.text or "").strip()
                    elif lp == "endDate":
                        endd = (p.text or "").strip()
                if cid and dims and start and endd:
                    q = qtrs_of(start, endd)
                    got = ss.parse_seg(";".join(dims) + ";") if q else None
                    if got:
                        ctx[cid] = (got[0], got[1], got[2], ddate_of(endd), q)
                el.clear()
                if root is not None:
                    root.clear()
            elif el.get("contextRef"):
                tag = name
                # **회계기준을 가리지 않는다.** 미국 국내 기업은 us-gaap,
                # 외국 기업(20-F·6-K)은 ifrs-full 로 태그한다 — 아메르스포츠·
                # ARM·HSBC 가 그렇다. 이름(Revenue 등)은 REV_TAGS 가 이미 둘 다
                # 담고 있으므로 네임스페이스만 넓히면 같은 파서가 쓰인다.
                if tag in ss.REV_RANK and ("us-gaap" in el.tag or "ifrs" in el.tag):
                    slot = ctx.get(el.get("contextRef"))
                    txt = (el.text or "").strip().replace(",", "")
                    if slot and txt and el.get("{http://www.w3.org/2001/XMLSchema-instance}nil") != "true":
                        try:
                            val = float(txt)
                        except ValueError:
                            val = 0.0
                        if val > 0:
                            facts.append(slot + (tag, val))
                el.clear()
                if root is not None:
                    root.clear()
    except ET.ParseError as e:
        raise ss.Blocked(f"인스턴스를 못 읽었다: {e}")
    return facts


# ── 쌓기 ────────────────────────────────────────────────────────────────

def absorb(facts_by_cik, pairs_by_cik, cik, filed, acc, rows):
    """벌크의 `scan()` 과 같은 모양으로 담는다. 나중에 낸 서류가 이긴다."""
    for axis, member, sub_axis, ddate, qtrs, tag, val in rows:
        if sub_axis == "solo":
            slot = (facts_by_cik.setdefault(cik, {}).setdefault(axis, {})
                    .setdefault(member, {}).setdefault(tag, {}))
            key = (ddate, qtrs)
            old = slot.get(key)
            if old is None or filed > old[1]:
                slot[key] = (val, filed)
        else:
            slot = (pairs_by_cik.setdefault(cik, {}).setdefault(axis, {})
                    .setdefault(sub_axis, {})
                    .setdefault(member, {}).setdefault(tag, {}))
            key = (ddate, qtrs)
            cur = slot.get(key)
            if cur is None or filed > cur[1]:
                slot[key] = (val, filed, acc)
            elif cur[2] == acc:                 # 같은 서류 안에서만 더한다
                slot[key] = (cur[0] + val, cur[1], cur[2])


# ── 어느 종목이 급한가 ───────────────────────────────────────────────────

def seg_last():
    """종목별로 지금 가진 부문 자료의 마지막 분기."""
    out = {}
    for name in ("segments_sec.json", "segments.json", "segments_edgar.json"):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            continue
        for t, r in got.items():
            if not r.get("pts"):
                continue
            e = max(p0[0] for p0 in r["pts"])
            if e > out.get(t, ""):
                out[t] = e
    return out


def fin_last():
    """종목별 총매출의 마지막 분기. 이것보다 뒤진 만큼이 구멍이다."""
    out = {}
    for name in ("financials.json", "financials_intl.json"):
        p = HERE / "data" / name
        if not p.exists():
            continue
        try:
            got = json.loads(p.read_text(encoding="utf-8")).get("stocks", {})
        except (ValueError, OSError):
            continue
        for t, r in got.items():
            pts = r.get("points") or []
            if not pts:
                continue
            e = max(p0["end"] for p0 in pts)
            if e > out.get(t, ""):
                out[t] = e
    return out


def priority(caps, cikmap):
    """(급한 정도, 시총) 순으로 종목을 늘어놓는다. cik -> [티커] 도 같이."""
    sl, fl = seg_last(), fin_last()
    order, by_cik = [], {}
    for t, cap in caps.items():
        cik = ss.cik_of(cikmap, t)
        if not cik:
            continue
        by_cik.setdefault(cik, []).append(t)
        # 뒤진 날수. 부문 자료가 아예 없으면 제일 급하다.
        f, s = fl.get(t), sl.get(t)
        if not f:
            gap = 0
        elif not s:
            gap = 9999
        else:
            gap = (date.fromisoformat(f) - date.fromisoformat(s)).days
        order.append((gap >= 60, cap, cik, t))
    order.sort(key=lambda x: (-x[0], -x[1]))
    return order, by_cik


def load_old():
    if not OUT.exists():
        return {"v": EDGAR_VER, "stocks": {}, "done": []}
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"v": EDGAR_VER, "stocks": {}, "done": []}
    if d.get("v") != EDGAR_VER:
        print(f"  담는 형식이 바뀌었다(v{d.get('v')} -> v{EDGAR_VER}). 처음부터 다시 받는다.")
        return {"v": EDGAR_VER, "stocks": {}, "done": []}
    d.setdefault("stocks", {})
    d.setdefault("done", [])
    return d


def save(old, facts_by_cik, pairs_by_cik, by_cik, done):
    """쌓은 것을 종목 기록으로. 축·상계·누계 규칙은 벌크와 한 벌이다.

    다만 **몇 분기부터 실을지는 여기서 낮춘다.** 벌크는 그 기록 하나가 차트
    전부라 네 분기를 요구하지만, 여기서 만드는 것은 **벌크 뒤에 이어 붙일
    꼬리**다. 서류 두 장에서 네 분기가 안 나온다고 버리면 정작 메우려던 최근
    분기를 못 메운다. 이어 붙일 때 부문 이름이 맞는지는 build.py 가 따로 본다.
    """
    keep = (ss.MIN_PTS, ss.MIN_MEMBER_PTS)
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = 3, 2
    made = 0
    for cik in set(facts_by_cik) | set(pairs_by_cik):
        rec = ss.build_stock(facts_by_cik.get(cik, {}), pairs_by_cik.get(cik, {}))
        if not rec:
            continue
        rec = ss.dedupe_names(rec)
        rec["v"] = EDGAR_VER
        for t in by_cik.get(cik, []):
            old["stocks"][t] = rec
            made += 1
    ss.MIN_PTS, ss.MIN_MEMBER_PTS = keep
    old["v"] = EDGAR_VER
    old["source"] = "SEC EDGAR 제출 서류(인라인 XBRL 인스턴스)"
    old["note"] = ("벌크가 아직 안 실은 최근 분기를 메운다. 판단 규칙은 "
                   "scrape_seg_sec.py 와 한 벌을 쓴다.")
    old["done"] = sorted(done)[-60000:]        # 접수번호는 20자라 넉넉하다
    old["count"] = len(old["stocks"])
    old["ts"] = date.today().isoformat()
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return made


def main():
    probe = "--probe" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = load_old()
    done = set(old["done"])

    caps = ss.want_tickers()
    if not caps:
        print("data/earnings_us.json 이 없다. 미국 일정을 먼저 받아야 한다.")
        return
    cikmap = ss.cik_map()
    order, by_cik = priority(caps, cikmap)
    want_cik = {cik for _g, _c, cik, _t in order}
    print(f"미국 {len(caps):,}종목 · CIK 를 찾은 것 {len(want_cik):,}"
          f" · 부문이 뒤진 종목 {sum(1 for g, *_ in order if g):,}")

    # 분기 색인. 한 장에 그 분기 접수분이 다 있다.
    todo, seen_acc = [], set()
    for y, q in recent_index_quarters(IDX_QUARTERS):
        rows = filings(y, q, want_cik)
        if rows is None:
            print(f"  {y}QTR{q} 색인 없음 (아직 안 나온 분기)")
            continue
        print(f"  {y}QTR{q} 색인 · 우리 종목 서류 {len(rows):,}건")
        for cik, acc, filed, form in rows:
            if acc in done or acc in seen_acc:
                continue
            seen_acc.add(acc)
            todo.append((cik, acc, filed, form))
        time.sleep(PAUSE)

    if not todo:
        print("새로 뜯어볼 서류가 없다.")
        save(old, {}, {}, by_cik, done)
        return

    # 급한 종목부터. 같은 회사는 옛 서류부터 담아야 '나중 서류가 이긴다'가 맞는다.
    # 한 CIK 에 티커가 둘일 수 있다(BRK.A/BRK.B). 더 급한 쪽 차례를 쓴다.
    rank = {}
    for i, (_g, _c, cik, _t) in enumerate(order):
        rank[cik] = min(rank.get(cik, i), i)
    # 회사마다 최근 PER_CIK 장만. 옛 분기는 벌크가 이미 갖고 있다.
    if PER_CIK:
        per, trimmed = {}, []
        for row in sorted(todo, key=lambda x: x[2], reverse=True):
            n = per.get(row[0], 0)
            if n >= PER_CIK:
                continue
            per[row[0]] = n + 1
            trimmed.append(row)
        print(f"  회사마다 최근 {PER_CIK}장만: {len(todo):,} -> {len(trimmed):,}건")
        todo = trimmed
    todo.sort(key=lambda x: (rank.get(x[0], 1 << 30), x[2]))
    print(f"뜯어볼 서류 {len(todo):,}건 중 이번에 {min(PER_RUN, len(todo)):,}건")

    if probe:
        for cik, acc, filed, form in todo[:3]:
            url = instance_url(cik, acc)
            print(f"\n  CIK {cik} {acc} {form} {filed}\n  -> {url}")
            if not url:
                continue
            blob = get(url)
            print(f"     {len(blob):,} bytes")
            rows = parse_instance(blob)
            print(f"     부문 매출 행 {len(rows)}")
            for r in rows[:6]:
                print("      ", r)
        return

    facts_by_cik, pairs_by_cik = {}, {}
    ok = miss = 0
    streak = 0
    for cik, acc, filed, _form in todo[:PER_RUN]:
        try:
            url = instance_url(cik, acc)
            time.sleep(PAUSE)
            if not url:
                done.add(acc)               # 비XBRL 서류. 다시 볼 것 없다.
                miss += 1
                continue
            blob = get(url)
            time.sleep(PAUSE)
        except ss.Blocked as e:
            streak += 1
            print(f"  ! {acc}: {e}", file=sys.stderr)
            if streak >= GIVE_UP_AFTER:
                print("연속 실패 — 여기서 멈춘다. 받은 만큼은 저장한다.", file=sys.stderr)
                break
            time.sleep(5)
            continue
        streak = 0
        if blob is None:
            done.add(acc)
            miss += 1
            continue
        try:
            rows = parse_instance(blob)
        except ss.Blocked as e:
            print(f"  ! {acc}: {e}", file=sys.stderr)
            done.add(acc)
            continue
        absorb(facts_by_cik, pairs_by_cik, cik, filed, acc, rows)
        done.add(acc)
        ok += 1
        if ok % 50 == 0:
            print(f"  {ok:,}건 · 부문을 담은 회사 {len(facts_by_cik) + len(pairs_by_cik):,}",
                  flush=True)

    made = save(old, facts_by_cik, pairs_by_cik, by_cik, done)
    left = max(0, len(todo) - PER_RUN)
    print(f"\n서류 {ok:,}건 · 부문이 없던 서류 {miss:,}건 · 기록을 만든 종목 {made:,}")
    print(f"총 {old['count']:,}종목 -> {OUT}" + (f" · 남은 서류 {left:,}건" if left else ""))


if __name__ == "__main__":
    main()
