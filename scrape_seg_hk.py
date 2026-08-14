# -*- coding: utf-8 -*-
"""
홍콩 부문별 매출 — 동화순(10jqka) F10 의 主营构成分析에서.

홍콩은 부문 매출을 기계로 읽을 공식 경로가 없다. 미국은 SEC XBRL, 일본은 TDnet
XBRL 인데 홍콩 공시는 PDF 한 장이다. 그래서 여덟 군데를 떠봤다(probe.yml 기록).

| 소스 | 결과 |
|---|---|
| stockanalysis (quote 주소) | 404 — 부문 페이지는 SEC 서류로 만들어 미국뿐 |
| 이스트머니 datacenter F10 | 웹 F10 에 주영구성 표 자체가 없다(30개 report 전수 확인) |
| AAstocks | 200 인데 본문 0바이트 (봇 차단) |
| etnet | 404 |
| futu | 1.2MB 서버렌더에 설명 텍스트만 — 숫자는 별도 API |
| gu.qq | 클라이언트 렌더 껍데기 |
| 쉐치우 | 400 (토큰 요구) |
| **동화순 basic.10jqka.com.cn** | **200 — 서버렌더 HTML 표. 이걸 쓴다** |

    https://basic.10jqka.com.cn/HK0700/operate.html
    -> 主营构成分析: 보고기간 셋(예: 2026-06-30 / 2026-03-31 / 2025-12-31)마다
       按业务分(사업별)·按地区分(지역별) 표. 항목명 + 점유율(%).

**금액 칸은 비어 있고 점유율만 준다.** 그래서 여기서는 **비중 스냅샷**만 저장하고,
절대 금액은 build.py 가 우리가 이미 가진 총매출(financials_intl)에 비중을 곱해
만든다 — 회사가 공시한 두 값(비중·총매출)의 곱이지 지어낸 값이 아니다.

**비중은 보고기간 누계 기준이다.** 중국계 F10 의 관례다 — 중간보고(06-30)는
상반기 누계, 연차(12-31)는 연간 누계의 구성비. 그래서 하반기 부문값은
「연간 누계 × 연간 비중 − 상반기 누계 × 상반기 비중」으로 되돌린다(build.py).
누계를 분기로 되돌리는 셈법은 이 저장소가 미국(SEC)·일본(TDnet)에서 쓰는 것과
같은 것이다.

**페이지에는 최근 세 보고기간만 실린다.** 반기 공시 회사면 1년 반, 분기 공시
회사(텐센트)면 아홉 달이다. 지난 스냅샷은 다시 얻을 길이 없으므로 **받은 것을
buried 하지 않고 계속 쌓는다** — segments_jp.json 의 raw 와 같은 이유다.
시간이 갈수록 그림이 길어진다.

부문 이름은 간체 중국어 그대로 둔다(零售店销售收入). 한국 한자음으로 읽으면
다른 말이 되는 건 회사 이름과 같아서, 원문 그대로가 규칙이다.

  python scrape_seg_hk.py            # 대기줄에서 SEG_HK_PER_RUN 개
  python scrape_seg_hk.py --probe 00700 09992   # 파서가 뭘 읽는지 본다

결과: data/segments_hk.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import scrape_fin_intl as si          # announcements()·Throttled 을 같이 쓴다

HERE = Path(__file__).parent
OUT = HERE / "data" / "segments_hk.json"

SEG_HK_VER = 1
URL = "https://basic.10jqka.com.cn/HK{code}/operate.html"

PER_RUN = int(os.environ.get("SEG_HK_PER_RUN", "250"))
STALE_DAYS = int(os.environ.get("SEG_HK_STALE_DAYS", "10"))
PAUSE = float(os.environ.get("SEG_HK_PAUSE", "0.5"))
GIVE_UP_AFTER = 6

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
TAGS = re.compile(r"<[^>]+>")
# 표의 축 표식. 사업별을 먼저 쓰고, 없으면 제품별. 지역별은 안 쓴다(SEC 와 같은 규칙).
AXIS_MARKS = ("按业务分", "按产品分")
TOTAL_ROW = ("营业额", "合计", "总计")


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise si.Throttled(f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise si.Throttled(str(e))


def cell(s):
    return re.sub(r"\s+", " ", TAGS.sub(" ", s)).strip()


def parse_income(html):
    """主营构成分析 -> {보고기간: [[부문, 비중%], …]} (사업별 축만).

    구조: 보고기간 날짜 목록이 먼저 오고, 기간마다 按业务分 표가 차례로 온다.
    표의 행은 [항목명, 매출(빈칸), 점유율]. 营业额/合计 행에서 그 표가 끝난다.

    **날짜 수와 표 수가 안 맞으면 그 종목은 버린다.** 어긋난 채 짝지으면
    지난 반기 비중이 이번 반기 것으로 붙는다 — 조용히 틀리느니 비워 둔다.
    """
    i = html.find('id="income"')
    if i < 0:
        return {}
    sec = html[i:]
    j = sec.find("免责声明")
    if j > 0:
        sec = sec[:j]

    # 보고기간 — 본문 표 앞에 나오는 날짜들. 두 벌 실리므로 차례를 지켜 중복만 뺀다.
    head = sec[:sec.find("<table") if "<table" in sec else 2000]
    dates, seen = [], set()
    for d in DATE_RE.findall(head):
        if d not in seen:
            seen.add(d)
            dates.append(d)
    if not dates:
        return {}

    # 기간마다 축 표식(按业务分)이 한 번씩 나온다. 표식 사이를 그 기간의 블록으로 본다.
    marks = []
    for m in re.finditer("|".join(AXIS_MARKS), sec):
        marks.append(m.start())
    if not marks or len(marks) != len(dates):
        return {}

    out = {}
    for k, start in enumerate(marks):
        end = marks[k + 1] if k + 1 < len(marks) else len(sec)
        block = sec[start:end]
        rows = []
        for tr in TR_RE.findall(block):
            tds = [cell(x) for x in TD_RE.findall(tr)]
            if len(tds) < 2:
                continue
            name = tds[0]
            if not name or name in ("项目名称",):
                continue
            if any(name.startswith(t) for t in TOTAL_ROW):
                break                        # 합계 줄에서 이 기간 표가 끝난다
            # 점유율은 마지막 숫자 칸. 금액 칸은 비어 온다.
            pct = None
            for v in reversed(tds[1:]):
                v = v.replace("%", "").replace(",", "")
                try:
                    pct = float(v)
                    break
                except ValueError:
                    continue
            if pct is None or pct < 0 or pct > 100.5:
                continue
            rows.append([name, pct])
        # 지역 표가 섞여 들어오면 비중 합이 200 에 다가간다. 95~105 만 믿는다.
        total = sum(p for _n, p in rows)
        if len(rows) >= 2 and 95 <= total <= 105:
            out[dates[k]] = rows
    return out


def load_old():
    if not OUT.exists():
        return {"v": SEG_HK_VER, "stocks": {}}
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"v": SEG_HK_VER, "stocks": {}}
    if d.get("v") != SEG_HK_VER:
        print(f"  담는 형식이 바뀌었다(v{d.get('v')} -> v{SEG_HK_VER}). 처음부터 다시 받는다.")
        return {"v": SEG_HK_VER, "stocks": {}}
    d.setdefault("stocks", {})
    return d


def save(old):
    have = sum(1 for v in old["stocks"].values() if v.get("snaps"))
    old["source"] = "10jqka(同花顺) 主营构成分析"
    old["note"] = ("보고기간 누계 기준의 부문 비중(%)만 온다. 절대 금액은 build.py 가 "
                   "총매출에 곱해 만든다. 페이지에 최근 세 기간만 실리므로 스냅샷을 "
                   "지우지 않고 쌓는다.")
    old["count"] = have
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return have


def targets():
    """홍콩 캘린더에 뜬 종목 전부, 시총 큰 순."""
    caps = {}
    p = HERE / "data" / "caps.json"
    if p.exists():
        try:
            caps = json.loads(p.read_text(encoding="utf-8")).get("caps", {})
        except (ValueError, OSError):
            pass
    f = HERE / "data" / "earnings_hk.json"
    if not f.exists():
        return {}
    try:
        rows = json.loads(f.read_text(encoding="utf-8")).get("rows", [])
    except (ValueError, OSError):
        return {}
    out = {}
    for r in rows:
        c = r.get("code")
        if c:
            out[c] = caps.get("hk:" + c) or 0
    return out


def queue(old, cand, ann):
    """방금 발표한 것 -> 못 받은 것 -> 오래된 것. 시총 큰 순."""
    stale = (date.today() - timedelta(days=STALE_DAYS)).isoformat()
    cold = (date.today() - timedelta(days=STALE_DAYS * 6)).isoformat()
    recent = (date.today() - timedelta(days=45)).isoformat()
    picks = []
    for code, cap in cand.items():
        rec = old["stocks"].get(code)
        last_ann = ann.get("hk:" + code, (9999, ""))[1]
        ts = (rec.get("ts") or "") if rec else ""
        if last_ann >= recent and ts < last_ann:
            picks.append((-1, -cap, code))
            continue
        if not rec:
            pri = 0
        elif not rec.get("snaps"):
            if ts >= cold:
                continue                     # 표가 없는 종목. 자주 안 두드린다.
            pri = 2
        elif ts < stale:
            pri = 1
        else:
            continue
        picks.append((pri, -cap, code))
    picks.sort()
    return [c for _p, _c, c in picks]


def main():
    probe_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--probe" in sys.argv:
        for code in probe_args or ("00700", "09992", "00941", "00005"):
            url = URL.format(code=f"{int(re.sub('[^0-9]', '', code)):04d}")
            print(f"\n===== {code}  {url}")
            html = get(url)
            if html is None:
                print("  404")
                continue
            snaps = parse_income(html)
            for d, rows in sorted(snaps.items()):
                print(f"  {d}  합 {sum(p for _n, p in rows):.1f}%")
                for n, p in rows:
                    print(f"    {p:6.2f}%  {n}")
        return

    OUT.parent.mkdir(parents=True, exist_ok=True)
    old = load_old()
    cand = targets()
    if not cand:
        print("data/earnings_hk.json 이 없다.")
        return
    ann = si.announcements()
    pending = queue(old, cand, ann)
    todo = pending[:PER_RUN]
    print(f"  받아야 할 종목 {len(pending):,}개 중 이번에 {len(todo)}개"
          f" (가진 것 {old.get('count', 0):,} / 후보 {len(cand):,})")

    today = date.today().isoformat()
    got = streak = 0
    for i, code in enumerate(todo):
        try:
            html = get(URL.format(code=f"{int(re.sub('[^0-9]', '', code)):04d}"))
        except si.Throttled as e:
            streak += 1
            print(f"    {code} 막힘: {e}", file=sys.stderr, flush=True)
            if streak >= GIVE_UP_AFTER:
                print("  연속으로 막혔다. 이번 실행은 여기서 접는다.",
                      file=sys.stderr, flush=True)
                break
            continue
        streak = 0
        rec = old["stocks"].setdefault(code, {})
        rec["ts"] = today
        if html is not None:
            snaps = parse_income(html)
            if snaps:
                # 스냅샷은 지우지 않고 쌓는다. 페이지가 세 기간만 실으므로
                # 오래된 것은 여기 말고는 남는 곳이 없다.
                rec.setdefault("snaps", {}).update(snaps)
                got += 1
        if i % 40 == 39:
            save(old)
            print(f"    {i+1}/{len(todo)} (표 얻음 {got})", flush=True)
        time.sleep(PAUSE)

    have = save(old)
    print(f"\n부문 비중 보유 {have:,}종목 -> {OUT}")


if __name__ == "__main__":
    main()
