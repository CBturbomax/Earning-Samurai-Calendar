# -*- coding: utf-8 -*-
"""일본 월매출 — **流通ニュース(ryutsuu.biz) 의 월차 기사**에서 모은다.

`scrape_mon_jp.py`(TDnet 첨부 PDF)와 **다른 우주를 메운다.** TDnet 적시공시로
월매출을 내는 회사는 우리 목록에 209곳인데, 니토리·패스트리·시마무라·젠쇼·
요시노야·세븐&아이·야마다·비쿠카메라처럼 **자사 IR 페이지에만 올리는 큰 회사**
는 그 목록에 아예 없다. 실측한 큰 소매·외식 33곳 중 23곳이 그랬다.

**여기만 열린다.** 월매출을 모아 두는 여덟 곳을 두드려 봤고(월차Web·irbank·
minkabu·가부탄·ullet 은 데이터센터 IP 차단, 有報キャッチャー 는 서비스 종료,
JPX TDnet API·QUICK 은 유료) 이 사이트만 200 을 준다. **막힌 곳을 다시
두드리지 말 것** — 한 번 두드리면 그 IP 가 막힌다(월차Web 이 그랬다).

값은 **전년동월비(%)뿐**이고 금액은 없다. 기사에 금액이 적히는 일도 있지만
표에는 비율만 온다. 지어 넣지 않는다.

  목록   https://www.ryutsuu.biz/sales/        (그 뒤는 /sales/page/N/)
  기사   https://www.ryutsuu.biz/sales/q013144.html   (앞글자가 해다)

**목록은 여든 쪽이 넘는다.** 한동안 여덟 쪽에서 끊기는 줄 알았는데 그건
우리 정규식이 2026년 기사(`s…`)만 찾았기 때문이었다 — 9쪽부터 2025년
기사(`r…`)라 링크가 0건이 되어 수집기가 스스로 "끝"이라 적었다. 한 쪽에
50건이고 한 달에 50건쯤이라 **2024년 1월은 서른몇 쪽 뒤**고, 거기까지
거슬러 간다(`START`). 받아 둔 기사는 `done` 으로 건너뛴다.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import monweb

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "monthly_web_jp.json"

VER = 1
# 읽는 규칙의 판. 올리면 **본 기사 기록만** 비우고 모아둔 값은 그대로 둔다 —
# 창 밖으로 밀려난 달을 영영 잃지 않기 위해서다(월매출 수치 쪽과 같은 규칙).
RULE_VER = 4

BASE = "https://www.ryutsuu.biz/sales/"
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36"),
      "Accept-Language": "ja,en;q=0.8"}

PER_RUN = int(os.environ.get("WEB_PER_RUN", "120"))
BUDGET = float(os.environ.get("WEB_SECS", "300"))
# 남의 서버다. 한 번 두드리고 쉰다.
PAUSE = float(os.environ.get("WEB_PAUSE", "0.8"))
# 한 실행에서 새로 거슬러 갈 쪽 수. 첫 몇 바퀴만 일하고 그 뒤에는 앞쪽만 본다.
DEEP_PER_RUN = int(os.environ.get("WEB_DEEP", "4"))
# **어디까지 거슬러 갈까.** 한 쪽이 50건이고 한 달에 50건쯤 실리므로 2024년
# 1월은 서른몇 쪽 뒤다. 주소만 보고 그 달을 알 수 있으므로(monweb.url_ym)
# 받아 보지 않고 끊는다.
START = (2024, 1)


# **연속으로 못 받으면 그 바퀴를 접는다.** 한 번 7분 동안 아무것도 안 들어온
# 실행이 있었다. 처음에는 사이트가 우리를 막은 줄 알았는데 두드려 보니 1초에
# 200 이었다(81차) — 진짜 원인은 **워크플로가 낡은 커밋을 체크아웃해** 앞
# 실행이 방금 받아 둔 것을 못 보고 같은 250건을 다시 받은 것이었다. 그래도
# 이 안전장치는 남긴다: 정말 막혔을 때 25초씩 250번을 두드리면 예산만 태우고
# 로그에는 아무것도 안 남아 **겉으로는 멀쩡해 보인다.**
GIVE_UP_AFTER = int(os.environ.get("WEB_GIVE_UP", "6"))


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read(1_500_000).decode("utf-8", "ignore")
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, OSError, ValueError) as e:
        print(f"  ! {url} {type(e).__name__} {str(e)[:40]}")
        return ""


def load():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}, set(), 0, False, []
    by = old.get("codes") or {}
    done = set(old.get("done") or [])
    deep = int(old.get("deep") or 0)
    end = bool(old.get("deep_done"))
    # 아직 못 본 '지난달 기사' 줄. 한 실행에 다 못 보므로 파일에 남긴다 —
    # 안 남기면 다음 실행이 이미 본 기사를 건너뛰어 그 링크에 영영 못 닿는다.
    queue = list(old.get("queue") or [])
    if old.get("rv") != RULE_VER:
        print(f"  읽는 규칙이 바뀌었다(rv {old.get('rv')} -> {RULE_VER})."
              f" 모아둔 값은 두고 본 기사 기록만 비운다.")
        done, deep, end, queue = set(), 0, False, []
    return by, done, deep, end, queue


def save(by, done, deep, end, queue=()):
    months = sum(len(v.get("months") or {}) for v in by.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": VER,
        "rv": RULE_VER,
        "source": "流通ニュース(ryutsuu.biz) 월차 기사",
        "source_url": BASE,
        "note": ("소매·외식 대기업의 달별 전년동월비(%). 금액은 오지 않는다. "
                 "브랜드 이름을 종목코드로 잇는 사전(monweb.BRANDS)에 있는 "
                 "회사만 담는다 — 아는 것만 잇고 짐작하지 않는다."),
        "codes": by,
        "companies": len(by),
        "months": months,
        # 주소를 정렬해 뒤에서 자르면 앞글자가 작은 옛 해(q=2024)부터
        # 잘려 나간다. 이력을 2024년까지 채우면 2천 건이 넘으므로 넉넉히
        # 둔다 — 지워도 값은 안 잃지만 그 기사를 다시 받게 된다.
        "done": sorted(done)[-12000:],
        "deep": deep,
        "deep_done": end,
        "queue": sorted(set(queue))[:4000],
    }
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return len(by), months


def merge(by, rec):
    """읽어낸 달을 회사 기록에 얹는다. **새 기사가 헌 값을 이긴다.**"""
    cur = by.setdefault(rec["code"], {"name": rec["name"], "brand": rec["brand"],
                                      "months": {}})
    cur["name"] = rec["name"]
    cur["brand"] = rec["brand"]
    n = 0
    for p, v in rec["months"].items():
        old = cur["months"].get(p)
        if old and old.get("day", "") > rec["day"]:
            continue
        m = {"day": rec["day"], "doc": rec["doc"]}
        m.update(v)
        cur["months"][p] = m
        n += 1
    return n


def page_url(n):
    return BASE if n <= 1 else f"{BASE}page/{n}/"


def main():
    if "--probe" in sys.argv:
        html = get(BASE)
        links = monweb.article_links(html)
        print(f"목록 {len(html)//1024}KB · 기사 {len(links)}")
        for u in links[:6]:
            print("  ", u)
        if links:
            page = get(links[0])
            got = monweb.read(page, links[0])
            print(json.dumps(got, ensure_ascii=False, indent=1)[:900])
            print("  지난달 기사:", monweb.older(page, links[0]))
        return

    by, done, deep, end, queue = load()
    t0 = time.time()

    # 어느 쪽을 볼까 — 앞 두 쪽은 늘 보고(새 기사), 그 뒤는 조금씩 거슬러 간다.
    pages = [1, 2]
    if not end:
        pages += list(range(max(deep, 2) + 1, max(deep, 2) + 1 + DEEP_PER_RUN))
    todo, seen_pages = [], 0
    for p in pages:
        if time.time() - t0 > BUDGET:
            break
        html = get(page_url(p))
        time.sleep(PAUSE)
        links = monweb.article_links(html)
        if not links:
            if p > 2:
                end = True
                print(f"  {p}쪽에 기사가 없다 — 여기가 끝이다.")
            break
        seen_pages = max(seen_pages, p)
        fresh = [u for u in links if (monweb.url_ym(u) or START) >= START]
        if p > 2 and not fresh:
            end = True
            print(f"  {p}쪽은 전부 {START[0]}년 {START[1]}월보다 옛 기사다 — 여기까지.")
            break
        todo += [u for u in fresh if u not in done]
    if seen_pages > deep:
        deep = seen_pages

    # 목록에서 온 새 기사가 먼저고, 그 뒤가 **거슬러 가기 줄**이다.
    # 목록 쪽은 두 달치뿐이라 이력은 이 줄로만 길어진다.
    todo = list(dict.fromkeys(todo + [u for u in queue if u not in done]))
    queue = todo[PER_RUN:]
    todo = todo[:PER_RUN]
    print(f"  볼 기사 {len(todo)}건 (본 기사 {len(done)} · 거슬러 갈 줄 "
          f"{len(queue)} · 쪽 {deep}{' · 끝까지' if end else ''})")

    added = new_docs = miss = 0
    for u in todo:
        if time.time() - t0 > BUDGET:
            print("  시간이 다 됐다 — 여기까지 담고 다음 실행에서 잇는다.")
            break
        page = get(u)
        time.sleep(PAUSE)
        if not page:
            miss += 1
            if miss >= GIVE_UP_AFTER:
                # **연속으로 막히면 접는다.** 계속 두드리면 예산만 태우고
                # 로그에는 아무것도 안 남아, 겉으로는 멀쩡해 보인다.
                print(f"  {miss}건 잇달아 못 받았다 — 이 바퀴는 접는다."
                      f" (남의 뉴스 서버다)")
                break
            continue
        miss = 0
        done.add(u)
        new_docs += 1
        try:
            recs = monweb.read(page, u)
        except Exception as e:                       # noqa: BLE001
            print(f"  ! 뜯다 터졌다 {u} {type(e).__name__}: {str(e)[:50]}")
            continue
        for rec in recs:
            added += merge(by, rec)
        # 이 기사가 가리키는 지난달 기사를 줄에 세운다.
        for u2 in monweb.older(page, u):
            if u2 not in done and (monweb.url_ym(u2) or START) >= START:
                queue.append(u2)
        if new_docs % 30 == 0:
            save(by, done, deep, end, queue)

    queue = [u for u in dict.fromkeys(queue) if u not in done]
    codes, months = save(by, done, deep, end, queue)
    print(f"  기사 {new_docs}건 · 달 {added}개 담았다 -> 종목 {codes} · 달 {months}"
          f" · 거슬러 갈 줄 {len(queue)}")


if __name__ == "__main__":
    main()
