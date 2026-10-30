"""어닝사무라이 실시간 서버 — 공시가 뜨면 몇 초 안에 텔레그램과 사이트로.

회원님: "실적 뜨자마자 바로 텔레로 쏴주고, 사이트에도 바로바로." GitHub Actions 는
예약 실행이 밀리고 사이트를 다시 만들어 배포해야 해서 수십 분이 걸렸다. 이 서버는
Railway 에서 24시간 돌며 원본을 몇 초마다 직접 본다.

  미국  SEC '최신 공시' 피드(8-K, Item 2.02) 2초마다 → 보도자료 손익 표(pr_table)
  일본  TDnet 오늘 목록 첫 쪽 3초마다 → 결산단신 XBRL(scrape_fin_jp 의 읽기 함수)
  홍콩  HKEXnews 오늘 실적 공시 10초마다 → 알림(숫자는 PDF 라 사이트 쪽 수집이 맡는다)

새 실적 → 숫자 · 차트(매출·성장률 / 마진 / 사업부별) → 텔레그램 앨범 → 사이트
(`/stream` SSE 로 열린 화면에 바로 밀어 넣고, `/api/live` 로 처음 열 때 받는다).

이력(지난 분기들)은 사이트가 이미 가진 것을 쓴다 — GitHub Pages 의 index.html 에
실린 payload 와 data/*.json 을 20분마다 다시 받는다. 계산 규칙은 기존 수집기 함수를
그대로 불러 쓴다(같은 판단을 두 군데 적으면 반드시 갈라진다).

시각은 넷을 남긴다 — 공개(published) · 감지(detected) · 텔레그램(tg) · 사이트(site).
`/api/stats` 가 소스별 지연을 보여준다.
"""
import html
import io
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pr_table                    # noqa: E402
import scrape_fin_jp as fj         # noqa: E402
import scrape_hk as hk             # noqa: E402
import scrape_live_us as lu        # noqa: E402
import scrape_fin_intl as fi       # noqa: E402
from markets import excluded       # noqa: E402

import brief                       # noqa: E402
import charts                      # noqa: E402
import shot                        # noqa: E402

DATA = Path(os.environ.get("DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
SEC_UA = os.environ.get("SEC_UA", "Earning Samurai Calendar (cbpark@wisdomasset.co.kr)")
SITE = os.environ.get("SITE_URL", "https://cbturbomax.github.io/Earning-Samurai-Calendar/")
RAW = "https://raw.githubusercontent.com/CBturbomax/Earning-Samurai-Calendar/main/data/"
PORT = int(os.environ.get("PORT", "8080"))
US_EVERY = float(os.environ.get("US_EVERY", "2"))
JP_EVERY = float(os.environ.get("JP_EVERY", "3"))
HK_EVERY = float(os.environ.get("HK_EVERY", "10"))
QUIET = os.environ.get("QUIET_START", "1") == "1"   # 켤 때 이미 떠 있던 공시는 알리지 않는다
# 다만 막 뜬 것(재배포 중에 나온 공시)까지 삼키면 안 된다 — 이 분 안의 것은 그대로 알린다.
# 같은 공시를 두 번 보내는 것은 events.json(볼륨)의 id 로 막힌다.
QUIET_MIN = int(os.environ.get("QUIET_MIN", "20"))


def stale(t):
    """켤 때 건너뛸 만큼 오래된 공시인가. t: 시간대가 붙은 datetime."""
    return QUIET and (datetime.now(timezone.utc) - t).total_seconds() > QUIET_MIN * 60

ET, JST, KST, HKT = (ZoneInfo("America/New_York"), ZoneInfo("Asia/Tokyo"),
                     ZoneInfo("Asia/Seoul"), ZoneInfo("Asia/Hong_Kong"))
FLAG = {"us": "🇺🇸", "jp": "🇯🇵", "hk": "🇭🇰"}
CUR_SIGN = {"USD": "$", "JPY": "¥", "HKD": "HK$", "CNY": "¥", "EUR": "€", "GBP": "£"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LOGS = []
brief.LOG = lambda *a: log(*a)


def log(*a):
    line = " ".join(str(x) for x in (datetime.now(KST).strftime("%m-%d %H:%M:%S"),) + a)
    print(line, flush=True)
    LOGS.append(line[:1500])
    del LOGS[:-300]


def http_get(url, ua=None, timeout=20, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": ua or SEC_UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


# ── 이력 ──────────────────────────────────────────────────────────────────
class History:
    """사이트가 가진 것 — 종목 이름·시총·지난 분기·사업부. 20분마다 새로."""

    FILES = ("financials.json", "financials_intl.json", "financials_jp.json",
             "caps.json", "earnings_us.json", "earnings_us_fpi.json", "guidance_hist_jp.json", "segments_jp.json")

    def __init__(self):
        self.lock = threading.Lock()
        self.D = {"rows": [], "fin": {}, "seg": {}}
        self.rows_by_key = {}
        self.ind = {}
        self.ts = 0
        self.html = ""

    def refresh(self):
        try:
            if os.environ.get("HISTORY_LOCAL"):                 # 시험용 — 저장소의 index.html
                t = (ROOT / "index.html").read_text(encoding="utf-8")
            else:
                t = http_get(SITE + "index.html?t=" + str(int(time.time())), ua="Mozilla/5.0", timeout=90)
            i = t.index('<script id="payload" type="application/json">')
            i = t.index(">", i) + 1
            D = json.loads(t[i:t.index("</script>", i)])
            self.html = t
        except Exception as e:                                   # noqa: BLE001
            log("이력: 사이트 payload 실패", e)
            D = None
        for fn in (() if os.environ.get("HISTORY_LOCAL") else self.FILES):   # 기존 수집기 함수가 읽는 자리
            try:
                b = http_get(RAW + fn, ua="Mozilla/5.0", timeout=120, binary=True)
                json.loads(b)
                (ROOT / "data" / fn).write_bytes(b)
            except Exception as e:                               # noqa: BLE001
                log("이력:", fn, "실패", e)
        lu._FIN = None                                          # sec_prior 캐시 비우기
        try:
            ind = json.loads((ROOT / "data" / "caps.json").read_text()).get("industries", {})
        except (OSError, ValueError):
            ind = {}
        with self.lock:
            if D:
                self.D = D
                by = {}
                for r in D["rows"]:
                    by.setdefault(f"{r[9]}:{r[1]}", []).append(r)
                self.rows_by_key = by
            self.ind = ind
            self.ts = time.time()
        shot.reload()
        log(f"이력 새로: 종목 {len(self.rows_by_key):,} · 실적 {len(self.D.get('fin', {})):,}")

    def row(self, key, day=None):
        """그 종목의 캘린더 줄 — 날짜가 가장 가까운 것."""
        rs = self.rows_by_key.get(key) or []
        if not rs:
            return None
        if not day:
            return rs[-1]
        return min(rs, key=lambda r: abs((date.fromisoformat(r[0]) - date.fromisoformat(day)).days))

    def skip(self, mkt, code, name):
        return excluded(mkt, name or "", "", self.ind.get(f"{mkt}:{code}", "") or "")


H = History()


# ── 사건(새 실적) 보관 · 방송 ─────────────────────────────────────────────
class Store:
    def __init__(self):
        self.path = DATA / "events.json"
        self.lock = threading.Lock()
        try:
            self.events = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.events = []
        self.subs = []                                          # SSE 손님들의 큐
        self.seen = set(e["id"] for e in self.events)

    def add(self, ev):
        with self.lock:
            if ev["id"] in self.seen:
                return False
            self.seen.add(ev["id"])
            self.events.append(ev)
            self.events = self.events[-600:]
            self._save()
        return True

    def update(self, ev):
        with self.lock:
            for i, e in enumerate(self.events):
                if e["id"] == ev["id"]:
                    self.events[i] = ev
            self._save()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.events, ensure_ascii=False))
        tmp.replace(self.path)

    def broadcast(self, ev):
        msg = "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass
        ev["t_site"] = now_iso()


S = Store()


# ── 일본 가이던스 변천 ───────────────────────────────────────────────────
STAGES = ("최초", "1Q", "2Q", "3Q")


def stage_of(fy_end, published):
    """결산기 끝(YYYY-MM-DD)과 공시일 사이 달 수로 어느 결산단신의 예상인지 가른다."""
    m = (int(fy_end[:4]) * 12 + int(fy_end[5:7])) - (int(published[:4]) * 12 + int(published[5:7]))
    return "최초" if m >= 10 else "1Q" if m >= 7 else "2Q" if m >= 4 else "3Q" if m >= 0 else None


class Guide:
    """회계연도마다 {최초·1Q·2Q·3Q: [매출, 영업이익]}.

    옛 이력은 저장소의 data/guidance_hist_jp.json(InvestingWiki 결산단신 이력에서 뜬 스냅샷),
    그 뒤로는 서버가 결산단신을 볼 때마다 /data/guide_jp.json 에 쌓는다. 실제(연간 실적)는
    사이트의 연간 표(D.ann.y)에서 붙인다."""

    def __init__(self):
        self.path = DATA / "guide_jp.json"
        self.lock = threading.Lock()
        try:
            self.own = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.own = {}

    def add(self, code, fc, published):
        if not fc or not fc.get("end") or fc.get("rev") is None:
            return None
        st = stage_of(fc["end"], published)
        if not st:
            return None
        fy = fc["end"][:7]
        with self.lock:
            before = self.get(code).get(fy, {})
            prev = next((before[s] for s in reversed(STAGES[:STAGES.index(st)]) if s in before), None)
            self.own.setdefault(code, {}).setdefault(fy, {})[st] = [fc.get("rev"), fc.get("opi")]
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.own))
            tmp.replace(self.path)
        return {"stage": st, "prev": prev, "now": [fc.get("rev"), fc.get("opi")], "end": fc["end"]}

    def get(self, code, ann=None):
        try:
            seed = json.loads((ROOT / "data" / "guidance_hist_jp.json").read_text()).get(code, {})
        except (OSError, ValueError):
            seed = {}
        out = {fy: dict(v) for fy, v in seed.items()}
        for fy, v in (self.own.get(code) or {}).items():
            out.setdefault(fy, {}).update(v)
        for y in (ann or {}).get("y") or []:
            if not y[5] and y[1]:                       # 확정 실적만
                out.setdefault(y[0][:7], {}).setdefault("실제", [y[1], y[2]])
        return out


GD = Guide()


# ── 텔레그램 ─────────────────────────────────────────────────────────────
class Telegram:
    """구독: 봇에게 아무 말이나(또는 /start) 보낸 대화방. /stop 이면 빠진다."""

    def __init__(self):
        self.path = DATA / "subscribers.json"
        try:
            self.subs = set(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            self.subs = set()
        # 기존 봇이 보내던 대화방(TELEGRAM_CHAT_ID)은 /start 없이 바로 받는다.
        for cid in re.split(r"[,\s]+", os.environ.get("TELEGRAM_CHAT_ID", "")):
            if cid.lstrip("-").isdigit():
                self.subs.add(int(cid))
        self.offset = 0

    def api(self, method, data=None, files=None, timeout=60):
        url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
        if files:
            body, ctype = multipart(data or {}, files)
        else:
            body, ctype = urllib.parse.urlencode(data or {}).encode(), "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def save(self):
        self.path.write_text(json.dumps(sorted(self.subs)))

    def poll(self):
        """새 구독자를 받는다(롱폴링)."""
        while True:
            if not TG_TOKEN:
                time.sleep(60)
                continue
            try:
                r = self.api("getUpdates", {"offset": self.offset, "timeout": 50}, timeout=70)
                for u in r.get("result", []):
                    self.offset = u["update_id"] + 1
                    m = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
                    chat = m.get("chat") or {}
                    cid = chat.get("id")
                    if not cid:
                        continue
                    text = (m.get("text") or "").strip()
                    if text.startswith("/stop"):
                        self.subs.discard(cid)
                        self.save()
                        self.send_text(cid, "알림을 껐습니다. 다시 받으려면 /start")
                    elif cid not in self.subs:
                        self.subs.add(cid)
                        self.save()
                        self.send_text(cid, "어닝사무라이 실적 알림을 켰습니다 🔔\n미국·일본·홍콩 실적이 뜨면 바로 보내드립니다.\n끄려면 /stop")
            except Exception as e:                               # noqa: BLE001
                log("텔레그램 폴링:", e)
                time.sleep(5)

    def send_text(self, cid, text, reply=None):
        try:
            d = {"chat_id": cid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"}
            if reply:
                d.update(reply_to_message_id=reply, allow_sending_without_reply="true")
            return self.api("sendMessage", d).get("result", {}).get("message_id")
        except Exception as e:                                   # noqa: BLE001
            log("텔레그램 전송 실패", cid, e)

    def reply(self, ids, text, png=None):
        """앞서 보낸 앨범에 답장으로 덧붙인다 — '왜'·AI 판독은 몇십 초 늦게 오므로."""
        if not TG_TOKEN:
            return
        for cid in list(self.subs):
            r = (ids or {}).get(cid)
            try:
                if png:
                    d = {"chat_id": cid, "caption": text[:1024], "parse_mode": "HTML"}
                    if r:
                        d.update(reply_to_message_id=r, allow_sending_without_reply="true")
                    self.api("sendPhoto", d, files={"photo": ("s.png", png, "image/png")})
                elif text:
                    self.send_text(cid, text, r)
            except Exception as e:                               # noqa: BLE001
                log("텔레그램 답장 실패", cid, e)

    def send_album(self, caption, pngs):
        """-> {대화방: 첫 메시지 번호} — 답장을 달 자리."""
        ids = {}
        if not TG_TOKEN:
            return ids
        for cid in list(self.subs):
            try:
                if not pngs:
                    ids[cid] = self.send_text(cid, caption)
                    continue
                media, files = [], {}
                for i, png in enumerate(pngs):
                    files[f"p{i}"] = (f"p{i}.png", png, "image/png")
                    item = {"type": "photo", "media": f"attach://p{i}"}
                    if i == 0:
                        item.update(caption=caption, parse_mode="HTML")
                    media.append(item)
                if len(pngs) == 1:
                    r = self.api("sendPhoto", {"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
                                 files={"photo": files["p0"]}).get("result", {})
                    ids[cid] = r.get("message_id")
                else:
                    r = self.api("sendMediaGroup", {"chat_id": cid, "media": json.dumps(media)}, files=files)
                    ids[cid] = ((r.get("result") or [{}])[0]).get("message_id")
            except Exception as e:                               # noqa: BLE001
                log("텔레그램 앨범 실패", cid, e)
        return ids


def multipart(fields, files):
    b = "----es" + str(int(time.time() * 1000))
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (fn, data, ct) in files.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                  f"Content-Type: {ct}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


TG = Telegram()
POOL = ThreadPoolExecutor(8)


# ── 사건 하나를 끝까지 ───────────────────────────────────────────────────
def fmt_money(v, cur):
    if v is None:
        return "N/A"
    sign = CUR_SIGN.get(cur, "")
    if cur in ("JPY",):
        return f"{sign}{v / 1e8:,.0f}억"
    return f"{sign}{v / 1e6:,.0f}M"


def yoy_of(points, label, rev):
    """같은 분기 전년 값과 견준다(라벨 'NQyy')."""
    m = re.match(r"(\d)([QH])(\d\d)", label or "")
    if not m or rev is None:
        return None
    prev = f"{m.group(1)}{m.group(2)}{int(m.group(3)) - 1:02d}"
    for p in points:
        if p[0] == prev and p[1]:
            return (rev / p[1] - 1) * 100
    return None


def fname(eid):
    return re.sub(r"[^A-Za-z0-9]+", "_", eid).strip("_")


# ── 텔레그램으로 보낼 것만 고른다(사이트에는 전부 뜬다) ─────────────────
# 회원님 규칙(10/29): 은행·증권·보험·화학·철강(·리츠)은 빼고, 시총 1조 원 이상은
# 무조건, 그 밑은 「잘 나왔을 때만」 — 매출 +15%↑ · 영업이익 +15%↑ 또는 흑자 전환 ·
# 분기 매출 역대 최고. 세 시장 같은 규칙. 숫자가 없는 작은 회사(홍콩 PDF 등)는 못 가린다.
TG_OUT_JP = {"은행", "증권", "증권·선물", "보험", "화학", "철강", "銀行業", "証券、商品先物取引業", "保険業", "化学", "鉄鋼"}
TG_OUT_IND = re.compile(
    r"(?i)\bbanks?\b|savings institutions|insurance|insurers|investment bankers|brokers|capital markets|"
    r"^chemicals$|major chemicals|specialty chemicals|agricultural chemicals|steel|real estate investment trusts|reit")
BIG_KRW = float(os.environ.get("TG_BIG_KRW", "1e12"))
GROW = float(os.environ.get("TG_GROW_PCT", "15"))


def tg_pick(ev, pts):
    """(보낼까, 왜). pts 는 이번 분기까지 넣은 이력."""
    if ev["mkt"] == "jp" and (ev.get("sector") or "") in TG_OUT_JP:
        return False, "업종 제외"
    if ev["mkt"] in ("us", "hk") and TG_OUT_IND.search(H.ind.get(ev["key"], "") or ""):
        return False, "업종 제외"
    krw = (ev.get("cap") or 0) * 1e9 * float(H.D.get("usdKrw") or 1400)
    if krw >= BIG_KRW:
        return True, "시총 1조 원 이상"
    p = ev.get("point")
    if not p or p[1] is None:
        return False, "작은 회사 · 숫자 없음"
    lab, rev, opi = p[0], p[1], p[2]
    if ev.get("yoy") is not None and ev["yoy"] >= GROW:
        return True, f"매출 +{ev['yoy']:.0f}%"
    prev = next((q for q in pts if q[0] == _prev_year(lab)), None)
    if opi is not None and prev and prev[2] is not None:
        if prev[2] <= 0 < opi:
            return True, "흑자 전환"
        if prev[2] > 0 and (opi / prev[2] - 1) * 100 >= GROW:
            return True, f"영업이익 +{(opi / prev[2] - 1) * 100:.0f}%"
    older = [q[1] for q in pts if q[0] != lab and q[1]]
    if older and rev > max(older):
        return True, "분기 매출 역대 최고"
    return False, "작은 회사 · 기준 미달"


def _prev_year(label):
    m = re.match(r"(\d)([QH])(\d\d)", label or "")
    return f"{m.group(1)}{m.group(2)}{int(m.group(3)) - 1:02d}" if m else ""


def live_hist(mkt, code):
    """사이트에 분기·반기 이력이 없는 회사(신규 상장 홍콩주 등) — 그 자리에서 stockanalysis 를 받는다.
    회원님(10/30): "원문을 못 읽으면 실적 사이트에서라도 긁어와서 이번 발표를 붙여라." """
    url = fi.url_for(mkt, code)
    if not url:
        return None
    try:
        txt = fi.get(url, timeout=12)
        rows, cur = fi.parse(txt) if txt else ({}, "")
    except Exception as e:                                       # noqa: BLE001
        log("이력 즉석 받기 실패", mkt, code, e)
        return None
    ends = sorted(rows)
    if len(ends) < 2:
        return None
    gaps = sorted((date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(ends, ends[1:]))
    half = 150 <= gaps[len(gaps) // 2] <= 220

    def lab(e):
        if half:
            return f"{1 if int(e[5:7]) <= 6 else 2}H{e[2:4]}"
        return fi.label_of(e)
    return {"freq": "H" if half else "Q", "cur": cur, "src": "sa",
            "points": [[lab(e), rows[e][0], rows[e][1]] for e in ends if rows[e][0]]}


def finish(ev, point=None):
    """숫자가 있으면 차트를 그리고 · 보관 · 사이트 · 텔레그램."""
    key = ev["key"]
    with H.lock:
        fin = dict(H.D.get("fin", {}).get(key) or {})
        seg = H.D.get("seg", {}).get(key)
    if len(fin.get("points") or []) < 2 or fin.get("freq") not in ("Q", "H"):
        got = live_hist(ev["mkt"], ev["code"])
        if got:
            fin = got
            ev["finAll"] = got                                   # 사이트·그림이 이 이력으로 그린다
    pts = [list(p) for p in fin.get("points") or []]
    cur = ev.get("cur") or fin.get("cur") or {"us": "USD", "jp": "JPY", "hk": "HKD"}[ev["mkt"]]
    ev["cur"] = cur
    if point:
        lab = point["label"]
        pts = [p for p in pts if p[0] != lab] + [[lab, point["rev"], point.get("opi")]]
        ev["point"] = [lab, point["rev"], point.get("opi"), point.get("ni"), point.get("eps")]
        ev["yoy"] = yoy_of(pts, lab, point["rev"])
        m = re.match(r"(\d)([QH])(\d\d)", lab)
        pv = next((p for p in pts if m and p[0] == f"{m.group(1)}{m.group(2)}{int(m.group(3)) - 1:02d}"), None)
        o, po = point.get("opi"), pv[2] if pv and len(pv) > 2 else None
        if o is not None and po is not None:
            if po > 0:
                ev["opiYoy"] = (o / po - 1) * 100
            elif o > 0:
                ev["turn"] = "흑자 전환"
            else:
                ev["turn"] = "적자 지속"
    text = ev.pop("_text", "")
    more = ev.pop("_more", None)
    box = {}
    names = seg_names_now(seg)
    # '왜'는 본문만 읽고 바로 — IR 첨부는 따로 읽어 사업부 숫자만 채운다(답장을 늦추지 않게).
    th = threading.Thread(target=lambda: box.update(b=brief.why(ev["name"], text, names=names)), daemon=True)
    th.start()
    with H.lock:
        ann = H.D.get("ann", {}).get(key)
        G = H.D.get("fcst", {}).get(key)
    gh = GD.get(ev["code"], ann) if ev["mkt"] == "jp" else None
    if gh and sum(1 for v in gh.values() for s in v if s != "실제") < 3:
        gh = None                                                # 한두 칸짜리 변천은 안 그린다
    pngs = None
    if H.html:
        try:                                                     # 사이트 화면 그대로 찍는다
            pngs = shot.render({k: v for k, v in ev.items() if not k.startswith("_")}, gh)
        except Exception:                                        # noqa: BLE001
            log("사이트 그림 실패", key, traceback.format_exc(limit=2))
    if not pngs:
        log("사이트 그림 없음 — matplotlib 로", key)
        try:
            pngs = charts.render(ev, pts, seg, fin.get("freq", "Q"), ann, G, gh)
        except Exception:                                        # noqa: BLE001
            log("차트 실패", key, traceback.format_exc(limit=2))
            pngs = []
    for i, png in enumerate(pngs):
        (DATA / "charts").mkdir(exist_ok=True)
        (DATA / "charts" / f"{fname(ev['id'])}_{i}.png").write_bytes(png)
    ev["charts"] = [f"/chart/{fname(ev['id'])}_{i}.png" for i in range(len(pngs))]
    if not ev.get("replay") and not S.add(ev):
        return
    replay = ev.get("replay")
    if replay:                                   # 시험 — 사이트·보관은 안 건드리고 텔레그램으로만
        send, why = True, "시험"
    else:
        S.broadcast(ev)
        send, why = tg_pick(ev, pts)
    ev["tg"], ev["tgWhy"] = send, why
    ids = {}
    if send:                                     # ① 그림·핵심 숫자 먼저 — 몇 초 안에
        ids = TG.send_album(("🧪 <i>지난 공시 다시 보내기(시험)</i>\n" if replay else "") + caption(ev), pngs)
        ev["t_tg"] = now_iso()
    if not replay:
        S.update(ev)
    log(f"실적 {ev['mkt']}:{ev['code']} {ev['name']} · 공개 {ev.get('published')} 감지 {ev['detected']} "
        f"텔레그램 {'보냄 ' + ev['t_tg'] if send else '안 보냄'} ({why}) · 그림 {len(pngs)}")
    th.join(100)                                 # ② '왜'는 받는 대로 답장으로
    b = box.get("b")
    if more:                                     # ③ IR 자료 — 본문에 사업부 표가 없었을 때만 따로 읽는다
        def ir():
            extra = more()
            if not extra.strip():
                return
            b2 = brief.why(ev["name"], text + extra, names=names)
            if b2:
                ser_add_q(key, b2, ev.get("cur") or "USD")
            if b2 and b2.get("segs"):
                e2 = dict(ev, brief=dict(ev.get("brief") or b or {}, segs=b2["segs"], unit=b2.get("unit"),
                                         basis=b2.get("basis"), scale=b2.get("scale")))
                sp = seg_ai_try(e2, e2["brief"], seg)
                if sp and not ev.get("replay"):
                    ev["segPoint"] = sp
                    S.update(ev)
                    S.broadcast(ev)
                log("IR 자료 사업부", key, "받음" if sp else "못 씀")
        threading.Thread(target=ir, daemon=True).start()
    if not b:
        return
    ev["brief"] = b
    ser_add_q(key, b, ev.get("cur") or "USD")
    sp = None if ev.get("segPoint") else seg_ai_try(ev, b, seg)
    if sp:
        ev["segPoint"] = sp                     # 사이트 부문 막대에 이번 분기를 곧바로 얹는다
    if not replay:
        S.update(ev)
        S.broadcast(ev)                          # 사이트 상세창에도 '방금 발표 사업부별'
    if send:
        text = followup(ev)
        segpng = None
        # 그림이 하나도 없던 회사(이력 없는 신규 상장 등)는 원문 손익 표의 이번/전년 비교를,
        # 사이트에 사업부별이 없는 회사는 원문 사업부 표를 그려 붙인다(둘 다 AI 판독, 표에 그렇게 적는다).
        alt = (SEGAI.get(key) or {}).get("alt") and SEGAI[key].get("label") == (ev.get("point") or [None])[0]
        want = "fin" if b.get("fin") and not pngs else ""     # 한 분기짜리 사업부 표는 안 보낸다(시계열이 아니면 의미 없다)
        if want and H.html:
            try:
                segpng = (shot.render(dict({k: v for k, v in ev.items() if not k.startswith("_")}, onlySeg=want)) or [None])[0]
            except Exception:                    # noqa: BLE001
                segpng = None
        if text or segpng:
            TG.reply(ids, text or f"<b>{html.escape(ev['name'])}</b> 사업부별 매출(공시 원문 AI 판독)", segpng)
        log("답장", key, "글" if text else "-", "AI 표" if segpng else "-",
            "| 숫자:", (b.get("key") or "")[:80], "| 왜:", ((b.get("why") or [""])[0])[:80])


# ── 사업부별 매출 — 보도자료 표를 AI 가 읽은 것을 사이트 부문 막대에 잇는다 ─────
# 미국 부문 자료는 10-Q(XBRL)가 나와야 생기는데 그게 며칠~몇 주 늦다. 회원님(10/30): "3분기 실적은
# 가져오는데 왜 3분기 브레이크다운은 못 가져오냐." 실적 발표 보도자료에 부문 표가 이미 있으므로
# 그걸 읽어 /data/segs_ai.json 에 담고, 수집 루프가 /api/segs 를 받아 빌드가 잇는다(build.py merge_seg_ai).
SEGAI_PATH = DATA / "segs_ai.json"
SEGAI_LOCK = threading.Lock()
try:
    SEGAI = json.loads(SEGAI_PATH.read_text())
except (OSError, ValueError):
    SEGAI = {}


# 회사의 '지금 기준' 부문 시계열. 두 길로 모인다 — (1) 보도자료마다 이번·전년 같은 분기 두 칸,
# (2) 설명자료·투자자 발표자료의 분기별 사업부 추이 표(여러 열을 통째로). 네 칸이 넘으면 사이트 막대를 대신한다.
# 회원님(10/30): "이번 분기만 따로 보는 건 의미가 없다. 시계열이 중요하다." · "공시랑 IR 자료에 넘치는 게 자료다."
SER_PATH = DATA / "segs_series.json"
try:
    SER = json.loads(SER_PATH.read_text())
except (OSError, ValueError):
    SER = {}


def prev_year(label):
    m = re.match(r"(\d)Q(\d\d)$", label or "")
    return f"{m.group(1)}Q{int(m.group(2)) - 1:02d}" if m else None


def _ser_put(key, cur, items):
    """items: [(라벨, 이름, 값)] — 닮은 이름은 같은 부문으로 잇는다."""
    from difflib import SequenceMatcher
    norm = lambda x: re.sub(r"[\s·,&()\-]|및|부문|사업", "", x.lower())   # noqa: E731
    with SEGAI_LOCK:
        r = SER.setdefault(key, {"names": [], "pts": {}, "cur": cur})
        for lab, n, v in items:
            if not lab or v is None:
                continue
            hit = next((x for x in r["names"] if norm(x) == norm(n)
                        or SequenceMatcher(None, norm(x), norm(n)).ratio() >= 0.8), None)
            if not hit:
                r["names"].append(n)
                hit = n
            r["pts"].setdefault(lab, {})[hit] = v
        tmp = SER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(SER, ensure_ascii=False))
        tmp.replace(SER_PATH)


def ser_add(key, label, b, cur):
    """보도자료 한 장 -> 이번 분기·전년 같은 분기 두 칸."""
    sc = b.get("scale") or 0
    if not sc or not b.get("segs") or re.search(r"누계|9|6개월|반기|연간|12", b.get("basis") or ""):
        return
    items = []
    for x in b["segs"]:
        items.append((label, x["n"], x["v"] * sc if x.get("v") is not None else None))
        items.append((prev_year(label), x["n"], x["p"] * sc if x.get("p") is not None else None))
    _ser_put(key, cur, items)


def ser_add_q(key, b, cur):
    """설명자료의 분기별 사업부 추이 표(여러 열) -> 그 분기들 전부."""
    sc = b.get("scale") or 0
    if not sc or not b.get("segq"):
        return 0
    items = []
    for col in b["segq"]:
        y, m = int(col["end"][:4]), int(col["end"][5:7])
        lab = f"{(m - 1) // 3 + 1}Q{y % 100:02d}"
        items += [(lab, n, v * sc) for n, v in col["vals"].items()]
    _ser_put(key, cur, items)
    log("분기 추이 표", key, len(b["segq"]), "분기")
    return len(b["segq"])


def seg_names_now(seg):
    """사이트 부문 막대에서 마지막 칸에 값이 있는 이름 — 지금 쓰는 기준."""
    if not seg or not seg.get("pts"):
        return None
    last = seg["pts"][-1]
    return [n for i, n in enumerate(seg.get("names") or []) if i + 1 < len(last) and last[i + 1] is not None] or None


def ytd_months(basis):
    """'9개월 누계'·'중간기(6개월)'·'반기'·'연간' -> 누계 개월 수. 분기 단독이면 3, 모르면 0."""
    m = re.search(r"(\d+)\s*개월", basis or "")
    if m:
        return int(m.group(1))
    if re.search(r"반기|중간|상반기|上期|中間", basis or ""):
        return 6
    if re.search(r"연간|통기|通期", basis or ""):
        return 12
    return 0 if re.search(r"누계", basis or "") else 3


def prev_q(label):
    m = re.match(r"(\d)Q(\d\d)$", label or "")
    if not m:
        return None
    q, y = int(m.group(1)), int(m.group(2))
    return f"{q - 1}Q{y:02d}" if q > 1 else f"4Q{y - 1:02d}"


def ytd_to_quarter(b, seg, label, months):
    """누계 부문 값 -> 이번 분기 단독. 앞 분기들(같은 회계연도)의 사이트 부문 값을 이름으로 맞춰 뺀다."""
    if not seg or not seg.get("pts"):
        return None
    from difflib import SequenceMatcher
    norm = lambda x: re.sub(r"[\s·,&()\-]|및|부문|사업", "", x.lower())   # noqa: E731
    rows = {p[0]: p for p in seg["pts"]}
    names = seg.get("names") or []
    need, lab = [], label
    for _ in range(months // 3 - 1):
        lab = prev_q(lab)
        if lab not in rows:
            return None
        need.append(rows[lab])
    out = []
    for x in b["segs"]:
        n = max(names, key=lambda m: SequenceMatcher(None, norm(m), norm(x["n"])).ratio(), default=None)
        if not n or SequenceMatcher(None, norm(n), norm(x["n"])).ratio() < 0.6:
            return None
        i = names.index(n) + 1
        prior = [r[i] for r in need]
        if any(v is None for v in prior) or x.get("v") is None:
            return None
        q = x["v"] * (b.get("scale") or 0) - sum(prior)
        if q <= 0:
            return None
        out.append({"n": n, "v": q / (b.get("scale") or 1), "y": None, "p": None})
    return dict(b, segs=out, basis="3개월(누계에서 앞 분기를 빼서)")


def seg_ai_try(ev, b, seg, point=None):
    """AI 판독 부문 표를 받아들일지. 이름이 사이트와 맞고, 합이 그 분기 총매출과 아귀가 맞을 때만."""
    p = point or ev.get("point")
    if not p or not b.get("segs") or not b.get("scale"):
        return None
    basis = b.get("basis") or ""
    if ev["mkt"] == "hk":
        return None
    ytd = ytd_months(basis)
    if ytd and ytd > 3:
        # 일본 설명자료는 대개 누계(6·9·12개월)다. 같은 회계연도의 앞 분기들을 우리가 갖고 있으면
        # 빼서 이번 분기 단독으로 되돌린다 — 없으면 못 쓴다(누계를 분기 칸에 앉히면 막대가 부푼다).
        b = ytd_to_quarter(b, seg, p[0], ytd)
        if not b:
            log("AI 부문 누계라 못 씀", ev["key"], basis)
            return None
    if seg and seg.get("pts") and seg["pts"][-1][0] == p[0]:
        return None                              # 그 분기가 이미 있다(공식 자료) — 손대지 않는다
    vals = {x["n"]: x["v"] * b["scale"] for x in b["segs"] if x.get("v") is not None}
    yoy = {x["n"]: x.get("y") for x in b["segs"]}
    raw_vals = dict(vals)
    alt = False                                  # 사이트 막대와 기준이 달라 못 잇는 것 — '이번 분기 표'로 따로 싣는다
    now = seg_names_now(seg)
    if now:
        # AI 가 이름을 줄이거나 조금 바꿔 쓴다(Waters Analytical and Material). 사이트 이름마다
        # 가장 닮은 AI 이름을 하나씩 짝짓는다 — 글자 그대로·포함·닮음 0.6 이상.
        from difflib import SequenceMatcher
        norm = lambda x: re.sub(r"[\s·,&()\-]|and|부문|사업", "", x.lower())   # noqa: E731
        left, mapped = dict(vals), {}
        for n in now:
            best = max(left, key=lambda a: (norm(a) == norm(n), norm(n) in norm(a) or norm(a) in norm(n),
                                            SequenceMatcher(None, norm(a), norm(n)).ratio()), default=None)
            if best is None:
                continue
            ok = (norm(best) == norm(n) or (min(len(norm(best)), len(norm(n))) >= 4 and
                  (norm(n) in norm(best) or norm(best) in norm(n)))
                  or SequenceMatcher(None, norm(best), norm(n)).ratio() >= 0.6)
            if ok:
                mapped[n] = left.pop(best)
        vals = dict(mapped, **{k: v for k, v in left.items() if k not in mapped})
        hit = [n for n in now if n in mapped]
        if len(hit) < max(2, round(len(now) * 0.6)):
            log("AI 부문 따로(이름 다름)", ev["key"], list(raw_vals)[:6], "사이트", now[:6])
            alt = True
            names, row = list(raw_vals), list(raw_vals.values())
        else:
            names, row = now, [vals.get(n) for n in now]
    else:
        alt = True                               # 사이트에 부문 막대가 없다 — 한 칸짜리 막대는 못 그린다
        names, row = list(vals), list(vals.values())
    tot, rev = sum(v for v in row if v), p[1]
    if not rev or not (0.85 <= tot / rev <= 1.6):
        log("AI 부문 거절(합)", ev["key"], round(tot / 1e6), "총매출", rev and round(rev / 1e6))
        return None
    rec = {"label": p[0], "names": names, "vals": row, "ts": now_iso(), "src": ev.get("url", ""),
           "cur": ev.get("cur") or {"us": "USD", "jp": "JPY", "hk": "HKD"}[ev["mkt"]],
           "y": [yoy.get(n) for n in names] if alt else None}
    if alt:
        rec["alt"] = 1
        ser_add(ev["key"], p[0], b, rec["cur"])
    with SEGAI_LOCK:
        SEGAI[ev["key"]] = rec
        tmp = SEGAI_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(SEGAI, ensure_ascii=False))
        tmp.replace(SEGAI_PATH)
    log("AI 부문 받음", ev["key"], p[0], len(names), "개", "(따로)" if alt else "")
    return None if alt else rec


def segfill(n):
    """이미 이번 분기를 발표했는데 부문이 뒤처진 미국 회사 — 보도자료를 다시 읽어 메운다."""
    import scrape_pr_us as pr
    with H.lock:
        fin, segs = dict(H.D.get("fin", {})), dict(H.D.get("seg", {}))
    newest = {}
    for k, f in fin.items():
        if k.startswith("us:") and f.get("points"):
            newest[k] = f["points"][-1]
    import collections
    # 가장 흔한 최신 분기 — 최댓값을 쓰면 라벨이 엉뚱한 한 종목(2Q31)에 끌려간다.
    cnt = collections.Counter(p[0] for p in newest.values())
    ok = [lab for lab, c in cnt.items() if c >= 50 and re.match(r"\dQ\d\d$", lab)]
    top = max(ok, key=lambda l: (l[2:], l[0])) if ok else None   # 50곳 넘게 낸 가장 새 분기 = 이번 시즌
    rev_map = {v: k for k, v in cik_ticker().items()}
    todo = []
    for k, p in newest.items():
        s = segs.get(k)
        if p[0] != top or (s and s["pts"][-1][0] == top) or k in SEGAI:
            continue
        cik = rev_map.get(k[3:].replace(".", "-"))
        if cik:
            todo.append((k, cik, p))
    todo = todo[:n]
    log("부문 메우기 시작", len(todo), "종목", top)
    got = [0]

    def one(item):
        k, cik, p = item
        try:
            hit = pr.latest_earnings_8k(cik)
            raw = lu.ex99_html(cik, hit[0]) if hit else None
            if not raw:
                return
            names = seg_names_now(segs.get(k))
            b = brief.why(k[3:], brief.us_text(raw), names=names)
            if b:
                ser_add_q(k, b, "USD")
                seg_ai_try({"key": k, "mkt": "us", "url": "", "cur": "USD"}, b, segs.get(k), [p[0], p[1]])
                if k in SEGAI:
                    got[0] += 1
        except Exception as e:                                   # noqa: BLE001
            log("부문 메우기 실패", k, repr(e)[:120])
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo))
    log("부문 메우기 끝", got[0], "/", len(todo))


def serfill(n, back=4):
    """기준이 달라 못 이은 미국 회사 — 지난 실적 보도자료 네 장을 읽어 지금 기준 시계열(8분기)을 만든다."""
    import scrape_pr_us as pr
    with H.lock:
        fin = dict(H.D.get("fin", {}))
    rev_map = {v: k for k, v in cik_ticker().items()}
    todo = [k for k, r in list(SEGAI.items()) if k.startswith("us:") and r.get("alt")
            and len((SER.get(k) or {}).get("pts") or {}) < 8][:n]
    log("부문 시계열 만들기 시작", len(todo), "종목")
    done = [0]

    def one(k):
        cik = rev_map.get(k[3:].replace(".", "-"))
        f = fin.get(k) or {}
        labs = [p[0] for p in f.get("points") or []]
        cur = SEGAI[k].get("label")
        if not cik or cur not in labs:
            return
        try:
            blob = pr.get(pr.SUBS.format(cik=cik), binary=False)
            rec = json.loads(blob)["filings"]["recent"] if blob else {}
        except Exception:                                        # noqa: BLE001
            return
        accs = [a.replace("-", "") for a, fm, it in zip(rec.get("accessionNumber", []), rec.get("form", []),
                                                       rec.get("items", [])) if fm == "8-K" and "2.02" in (it or "")]
        i0 = labs.index(cur)
        for j, acc in enumerate(accs[:back]):
            if i0 - j < 0:
                break
            lab, rev = labs[i0 - j], f["points"][i0 - j][1]
            try:
                raw = lu.ex99_html(cik, acc)
                if not raw:
                    continue
                b = brief.why(k[3:], brief.us_text(raw) + us_extra_text(cik, acc), names=(SER.get(k) or {}).get("names"))
            except Exception as e:                               # noqa: BLE001
                log("시계열 실패", k, repr(e)[:100])
                continue
            if not b:
                continue
            ser_add_q(k, b, "USD")
            if b.get("segs") and b.get("scale"):
                tot = sum((x["v"] or 0) * b["scale"] for x in b["segs"])
                if rev and 0.85 <= tot / rev <= 1.6:             # 그 분기 총매출과 맞는 장만
                    ser_add(k, lab, b, "USD")
        if len((SER.get(k) or {}).get("pts") or {}) >= 4:
            done[0] += 1
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo))
    log("부문 시계열 만들기 끝", done[0], "/", len(todo))


def segfill_jp(n):
    """이번 시즌 이미 발표했는데 부문이 없는 일본 회사 — TDnet(한 달 보관)의 결산단신·설명자료를 다시 읽는다."""
    with H.lock:
        fin, segs, rows = dict(H.D.get("fin", {})), dict(H.D.get("seg", {})), dict(H.rows_by_key)
    import collections
    newest = {k: f["points"][-1] for k, f in fin.items() if k.startswith("jp:") and f.get("points")}
    cnt = collections.Counter(p[0] for p in newest.values())
    ok = [lab for lab, c in cnt.items() if c >= 30 and re.match(r"\dQ\d\d$", lab)]
    top = max(ok, key=lambda l: (l[2:], l[0])) if ok else None
    cutoff = (datetime.now(JST) - timedelta(days=30)).strftime("%Y-%m-%d")
    todo = []
    for k, p in newest.items():
        s = segs.get(k)
        if p[0] != top or (s and s["pts"][-1][0] == top) or k in SEGAI:
            continue
        days = sorted({r[0] for r in rows.get(k, []) if cutoff <= r[0] <= datetime.now(JST).strftime("%Y-%m-%d")})
        if days:
            todo.append((k, days[-1], p))
    todo.sort(key=lambda t: -max((r[11] or 0) for r in rows.get(t[0], [[0] * 12])))   # 큰 회사부터
    todo = todo[:n]
    log("일본 부문 메우기 시작", len(todo), "종목", top)
    got = [0]

    def one(item):
        k, day, p = item
        code = k[3:]
        try:
            lst = []
            for page in (1, 2, 3, 4, 5, 6):
                got_rows = fj.listing(day, page)
                if not got_rows:
                    break
                lst += got_rows
            row = next((r for r in lst if r["code"] == code and r["zip"] and fj.TANSHIN.search(r["title"])), None)
            text = ""
            if row:
                blob = fj.get("https://www.release.tdnet.info/inbs/" + row["zip"], binary=True)
                if blob:
                    text = fj.read_narrative(blob) + "\n\n[세그먼트 정보]\n" + jp_seg_text(blob)
            text += "\n\n[결산 설명자료(IR)]\n" + jp_ir_text(code, day, tries=1)
            if len(text) < 200:
                return
            b = brief.why(code, text, names=seg_names_now(segs.get(k)))
            if b:
                ser_add_q(k, b, "JPY")
                seg_ai_try({"key": k, "mkt": "jp", "url": "", "cur": "JPY"}, b, segs.get(k), [p[0], p[1]])
                if k in SEGAI:
                    got[0] += 1
        except Exception as e:                                   # noqa: BLE001
            log("일본 부문 메우기 실패", k, repr(e)[:120])
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo))
    log("일본 부문 메우기 끝", got[0], "/", len(todo))


def followup(ev):
    """앨범 뒤에 붙는 답장 — 회사가 한 설명을 쉬운 말로."""
    e = html.escape
    b = ev.get("brief") or {}
    lines = []
    if b.get("key") and not ev.get("point"):
        lines.append("📊 <b>" + e(b["key"]) + "</b>")
    if b.get("why"):
        lines += ["💡 <b>왜 이렇게 나왔나</b> <i>(회사 설명)</i>"] + [f"• {e(x)}" for x in b["why"]]
    if b.get("guide"):
        lines += ["", f"🗓 {e(b['guide'])}"]
    if b.get("watch"):
        lines += ["", "⚠️ <b>주의</b>"] + [f"• {e(x)}" for x in b["watch"]]
    return "\n".join(lines).strip()


def pct(v):
    return f"{v:+.1f}%".replace("+0.0%", "0%")


def caption(ev):
    """숫자는 차트가 말한다. 글은 한 줄 요점 + '왜' 몇 줄."""
    e = html.escape
    lines = [f"{FLAG[ev['mkt']]} <b>{e(ev['name'])} ({e(ev['code'])})</b>"
             + (f" · {e(ev['point'][0])} 실적" if ev.get("point") else " 실적 발표")]
    if ev.get("kstTime"):
        lines.append(f"🕒 {ev['kstDate'][5:].replace('-', '/')} {ev['kstTime']} · {e(ev.get('src', ''))}")
    p = ev.get("point")
    key = []
    if p:
        lab, rev, opi = p[0], p[1], p[2]
        if ev.get("yoy") is not None:
            key.append(f"매출 {pct(ev['yoy'])}")
        if ev.get("opiYoy") is not None:
            key.append(f"영업이익 {pct(ev['opiYoy'])}")
        elif ev.get("turn"):
            key.append(ev["turn"])
        if opi is not None and rev:
            key.append(f"이익률 {opi / rev * 100:.1f}%")
    g = ev.get("guide")
    if g and g.get("prev"):
        d = [f"{n} {pct((b / a - 1) * 100)}" for n, a, b in zip(("매출", "영업이익"), g["prev"], g["now"])
             if a and b is not None and a > 0]
        if d:
            up = all(x.split()[-1].startswith("+") for x in d)
            dn = all(x.split()[-1].startswith("-") for x in d)
            gl = ("📈 가이던스 상향 " if up else "📉 가이던스 하향 " if dn else "🔁 가이던스 수정 ") + " · ".join(d)
        else:
            gl = ""
    else:
        gl = ""
    if key:
        lines.append("<b>" + e(" · ".join(key)) + "</b>")
    if gl:
        lines.append("<b>" + e(gl) + "</b>")
    if not p and ev.get("title"):
        lines.append(e(ev["title"][:160]))
    links = [f"<a href=\"{e(ev['url'])}\">원문</a>"] if ev.get("url") else []
    links.append(f"<a href=\"{SITE}\">어닝사무라이</a>")
    lines += ["", "🔗 " + " · ".join(links)]
    out = "\n".join(lines)
    if len(out) > 1024:
        out = out[:1000] + "…"
    return out


def base_event(mkt, code, name, published_local, tz, src, url, title=""):
    """published_local: 현지 'YYYY-MM-DD HH:MM'."""
    t = datetime.strptime(published_local, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
    k = t.astimezone(KST)
    key = f"{mkt}:{code}"
    with H.lock:
        r = H.row(key, published_local[:10])
    return {
        "id": f"{key}:{published_local}", "key": key, "mkt": mkt, "code": code,
        "name": (r[2] if r else "") or name, "orig": name, "date": published_local[:10],
        "time": published_local[11:], "kstDate": k.strftime("%Y-%m-%d"), "kstTime": k.strftime("%H:%M"),
        "fy": r[3] if r else "", "kind": r[4] if r else "", "sector": r[5] if r else "",
        "cap": r[11] if r else 0, "published": t.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "detected": now_iso(), "src": src, "url": url, "title": title,
    }


# ── 미국 ────────────────────────────────────────────────────────────────
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb="
        "&owner=include&start=0&count=40&output=atom")
ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)
_TICK = {"ts": 0, "map": {}}


def cik_ticker():
    if time.time() - _TICK["ts"] > 12 * 3600 or not _TICK["map"]:
        try:
            tk = json.loads(http_get("https://www.sec.gov/files/company_tickers.json", timeout=60))
            m = {}
            for v in tk.values():
                m.setdefault(int(v["cik_str"]), v["ticker"].upper())   # 첫 줄이 보통주
            _TICK.update(ts=time.time(), map=m)
        except Exception as e:                                   # noqa: BLE001
            log("SEC 티커 목록 실패", e)
    return _TICK["map"]


def us_period(key, filed):
    """'Sep 2026' 꼴. 캘린더의 결산기가 있으면 그것, 없으면 접수일 앞의 분기말."""
    with H.lock:
        r = H.row(key, filed)
    if r and r[3] and abs((date.fromisoformat(r[0]) - date.fromisoformat(filed)).days) <= 5:
        p = lu.period_of(r[3])
        if p:
            return p
    d = date.fromisoformat(filed) - timedelta(days=20)
    q_end_month = ((d.month - 1) // 3) * 3
    y = d.year if q_end_month else d.year - 1
    q_end_month = q_end_month or 12
    return f"{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[q_end_month - 1]} {y}"


def label_of(period):
    mon, y = period.split()
    mi = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split().index(mon) + 1
    return f"{(mi - 1) // 3 + 1}Q{int(y) % 100:02d}"


def us_extra_text(cik, acc):
    """같은 8-K 에 붙은 보도자료 말고 다른 첨부(투자자 발표자료 EX-99.2 등, htm·pdf) — 부문 표가 거기 있는 회사가 많다.
    회원님(10/30): "IR 자료도 같이 읽어라." """
    try:
        blob = lu.sec_get(lu.FILES.format(cik=cik, acc=acc), timeout=15, binary=False)
        items = json.loads(blob)["directory"]["item"] if blob else []
        ex = sorted([(it["name"], int(it.get("size") or 0)) for it in items
                     if re.search(r"ex[-_]?99|99[-_.]?[2-9]|presentation|slides|supplement", it["name"], re.I)
                     and it["name"].lower().endswith((".htm", ".html", ".pdf"))], key=lambda t: -t[1])
        if len(ex) < 2:
            return ""
        out = []
        for name, _sz in ex[1:3]:                               # 가장 큰 것(보도자료)은 이미 읽었다
            raw = lu.sec_get(lu.DOC.format(cik=cik, acc=acc, name=name), timeout=25)
            if not raw:
                continue
            t = brief.hk_text(raw) if name.lower().endswith(".pdf") else brief.plain(raw.decode("utf-8", "replace"))
            i = max(t.lower().find("segment"), 0)
            out.append(f"\n\n[투자자 자료 {name}]\n" + t[max(0, i - 300):max(0, i - 300) + 7000])
        if out:
            log("미국 IR 첨부", acc, len(out), "개")
        return "".join(out)
    except Exception as e:                                       # noqa: BLE001
        log("미국 IR 첨부 실패", acc, repr(e)[:120])
        return ""


def us_process(cik, acc, name, upd, replay=False):
    tick = cik_ticker().get(cik)
    if not tick:
        return
    code = tick.replace("-", ".")
    if H.skip("us", code, name):
        return
    pub_et = datetime.fromisoformat(upd).astimezone(ET).strftime("%Y-%m-%d %H:%M")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
    ev = base_event("us", code, name, pub_et, ET, "SEC 8-K 보도자료", url)
    ev["replay"] = replay
    point = None
    try:
        raw = lu.ex99_html(cik, acc.replace("-", ""))
        ev["_text"] = brief.us_text(raw)
        ev["_more"] = lambda: us_extra_text(cik, acc.replace("-", ""))   # IR 첨부는 요약 단계에서(앨범을 늦추지 않게)
        period = us_period(ev["key"], pub_et[:10])
        prior = lu.sec_prior(code, period)
        got = pr_table.read(raw, prior) if raw and prior else None
        if got and got.get("rev"):
            point = {"label": label_of(period), "rev": got["rev"], "opi": got.get("opi"),
                     "ni": got.get("ni"), "eps": got.get("eps")}
    except Exception:                                            # noqa: BLE001
        log("미국 표 읽기 실패", code, traceback.format_exc(limit=2))
    finish(ev, point)


def us_watch():
    seen = set()
    first = True
    while True:
        t0 = time.time()
        try:
            for form in ("8-K",):
                xml = http_get(FEED.format(form=form), timeout=15)
                for e in ENTRY.findall(xml):
                    acc = re.search(r"AccNo:\s*</b>\s*([\d-]+)|AccNo:\s*([\d-]+)", html.unescape(e))
                    acc = acc and (acc.group(1) or acc.group(2))
                    if not acc or acc in seen:
                        continue
                    seen.add(acc)
                    summ = html.unescape(e)
                    if "2.02" not in summ:
                        continue
                    m = re.search(r"<title>[^<]*?-\s*(.*?)\s*\((\d{10})\)", e)
                    upd = re.search(r"<updated>([^<]+)</updated>", e)
                    if not (m and upd):
                        continue
                    if first and stale(datetime.fromisoformat(upd.group(1).replace("Z", "+00:00"))):
                        continue
                    POOL.submit(safe, us_process, int(m.group(2)), acc, html.unescape(m.group(1)), upd.group(1))
            first = False
        except Exception as e:                                   # noqa: BLE001
            log("미국 피드:", e)
            time.sleep(5)
        time.sleep(max(0.2, US_EVERY - (time.time() - t0)))


# ── 일본 ────────────────────────────────────────────────────────────────
def jp_seg_text(blob):
    """결산단신 첨부에서 セグメント情報 절의 글(표 포함)만 — AI 가 사업부 표를 읽게."""
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return ""
    best = ""
    for n in z.namelist():
        if "Attachment" not in n or not n.lower().endswith(".htm"):
            continue
        t = brief.plain(z.read(n).decode("utf-8", "replace"))
        i = t.find("報告セグメントごとの売上")
        if i < 0:
            i = t.find("セグメント情報")
        if i >= 0 and len(t) - i > len(best):
            best = t[i:i + 6000]
    return best


IR_TITLE = re.compile(r"説明資料|補足資料|プレゼンテーション|決算概要|Presentation|ハイライト")


def jp_ir_text(code, day, tries=3):
    """설명자료가 결산단신보다 몇십 초 늦게 올라오는 회사가 있다 — 30초 간격으로 세 번까지 본다.
    (요약은 그림 앨범을 보낸 뒤에 붙으므로 기다려도 알림이 늦지 않는다.)"""
    for n in range(tries):
        t = _jp_ir_text(code, day)
        if t or n == tries - 1:
            return t
        time.sleep(30)
    return ""


def _jp_ir_text(code, day):
    """같은 날 TDnet 에 올라온 그 회사의 결산 설명자료(IR PDF) 글 — 분기 사업부 표가 대개 여기 있다.
    회원님(10/30): "IR 자료랑 IR 홈페이지 같은 것도 활용하면 되잖아." """
    try:
        rows = []
        for page in (1, 2, 3, 4):
            txt = fj.get(fj.LIST_URL.format(page=page, day=day.replace("-", "")))
            if not txt:
                break
            for m in fj.ROW_RE.finditer(txt):
                d = m.groupdict()
                if d["code"].strip()[:4] == code:
                    rows.append(d)
        for d in rows:
            title = fj.TAGS.sub("", d["titlecell"]).strip()
            link = fj.LINK_RE.search(d["titlecell"] or "")
            if not link or not IR_TITLE.search(title) or not link.group(1).lower().endswith(".pdf"):
                continue
            pdf = fj.get("https://www.release.tdnet.info/inbs/" + link.group(1), binary=True)
            t = brief.hk_text(pdf) if pdf else ""
            i = t.find("セグメント")
            log("일본 IR 자료", code, title[:40], len(t))
            return t[:20000]                            # 추이 표가 여러 쪽에 흩어져 있다 — 앞 20,000자 통째로
    except Exception as e:                                       # noqa: BLE001
        log("일본 IR 자료 실패", code, repr(e)[:120])
    return ""


def jp_seg_now(key, blob, label, seg):
    """결산단신 XBRL 의 부문 누계 + 지난 누계(segments_jp.json raw) -> 이번 분기 단독 부문 값을 **AI 없이** 곧바로.
    이름은 전년 같은 분기 값이 사이트 막대와 원 단위까지 같은 것끼리 짝짓는다(그래서 표기가 달라도 안전하다).
    회원님(10/30): "실적 방금 쏴준 거 또 3분기 거 안 가져왔잖아." — 알림 그림에 이번 분기 사업부가 들어가게."""
    if not seg or not seg.get("pts"):
        return None
    try:
        raw = json.loads((ROOT / "data" / "segments_jp.json").read_text()).get("raw", {}).get(key, {})
    except (OSError, ValueError):
        raw = {}
    pts = {}
    for span, row in raw.items():
        st, _, en = span.partition("/")
        if st and en:
            pts[(st, en)] = fj.seg_clean(row)
    for st, en, row in fj.read_segments(blob):
        pts[(st, en)] = fj.seg_clean(row)
    q = fj.seg_quarters([(st, en, r) for (st, en), r in pts.items() if len(r) >= 2])
    if not q:
        return None
    end = max(q)
    cur = q[end]
    pend = f"{int(end[:4]) - 1}{end[4:]}"
    prev = q.get(pend) or next((v for k, v in q.items() if k[:7] == pend[:7]), None)
    rows = {p[0]: p for p in seg["pts"]}
    site_prev = rows.get(prev_year(label))
    if not prev or not site_prev:
        return None
    names = seg["names"]
    out = {}
    for n, v in prev.items():
        for i, sn in enumerate(names):
            sv = site_prev[i + 1]
            if sv and abs(v - sv) <= max(1, abs(sv) * 0.005) and n in cur:
                out[sn] = cur[n]
    now = [n for i, n in enumerate(names) if rows[seg["pts"][-1][0]][i + 1] is not None]
    if len(out) < max(2, len(now) - 0):
        log("XBRL 부문 짝 못 맞춤", key, list(cur)[:5], names[:5])
        return None
    log("XBRL 부문 이번 분기", key, label, len(out), "개")
    return {"label": label, "names": names, "vals": [out.get(n) for n in names]}


def jp_process(row, day, replay=False):
    code = row["code"]
    if H.skip("jp", code, row["name"]):
        return
    ev = base_event("jp", code, row["name"], f"{day} {row['time']}", JST, "TDnet 결산단신",
                    fj.LIST_URL.format(page=1, day=day.replace("-", "")), row["title"])
    ev["replay"] = replay
    point = blob = None
    try:
        zurl = "https://www.release.tdnet.info/inbs/" + row["zip"]
        blob = fj.get(zurl, binary=True)
        if blob:
            vals = fj.read_summary(blob)
            ev["_text"] = fj.read_narrative(blob) + "\n\n[세그먼트 정보]\n" + jp_seg_text(blob)
            ev["_more"] = lambda: "\n\n[결산 설명자료(IR)]\n" + jp_ir_text(code, day)
            if vals:
                ev["guide"] = GD.add(code, fj.forecast_from(vals), day)
            prior = fj.base_series().get(f"jp:{code}", {})
            q = fj.quarter_from(vals, prior) if vals else None
            if q:
                point = {"label": q["label"], "rev": q["rev"], "opi": q.get("opi"), "ni": q.get("ni")}
                with H.lock:
                    sg = H.D.get("seg", {}).get(f"jp:{code}")
                try:
                    sp = jp_seg_now(f"jp:{code}", blob, q["label"], sg)
                    if sp:
                        ev["segPoint"] = sp           # 그림(사이트 화면)에 이번 분기 사업부가 바로 들어간다
                except Exception:                     # noqa: BLE001
                    log("XBRL 부문 실패", code, traceback.format_exc(limit=2))
            ev["url"] = zurl
    except Exception:                                            # noqa: BLE001
        log("일본 결산단신 실패", code, traceback.format_exc(limit=2))
    log("일본", code, "zip" if blob else "zip 없음", "수치" if point else "수치 없음", "설명", len(ev.get("_text") or ""))
    finish(ev, point)


def jp_watch():
    seen = set()
    first = True
    while True:
        t0 = time.time()
        day = datetime.now(JST).strftime("%Y-%m-%d")
        try:
            for r in fj.listing(day, 1):
                k = (day, r["code"], r["time"], r["title"])
                if k in seen:
                    continue
                seen.add(k)
                if not (fj.TANSHIN.search(r["title"]) and not fj.NOT_TANSHIN.search(r["title"]) and r["zip"]):
                    continue
                if first and (not r["time"] or stale(datetime.strptime(f"{day} {r['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=JST))):
                    continue
                POOL.submit(safe, jp_process, r, day)
            first = False
        except Exception as e:                                   # noqa: BLE001
            log("일본 목록:", e)
            time.sleep(5)
        time.sleep(max(0.2, JP_EVERY - (time.time() - t0)))


# ── 홍콩 ────────────────────────────────────────────────────────────────
# 실적 공시 분류에 같이 걸려 오는 안내 공시(설명회 소집·이사회 날짜 등)는 실적이 아니다.
HK_NOT = re.compile(r"CONVEN|PRESENTATION|BRIEFING|BOARD MEETING|DATE OF|NOTICE OF|POSTPONE|DELAY|"
                    r"說明會|業績會|董事會會議", re.I)


def hk_process(ev):
    """홍콩은 숫자를 못 읽지만(PDF) 회사 설명은 PDF 글에서 뽑는다."""
    if brief.enabled() and ev.get("url", "").lower().endswith(".pdf"):
        try:
            ev["_text"] = brief.hk_text(http_get(ev["url"], ua="Mozilla/5.0", timeout=30, binary=True))
            log("홍콩 PDF", ev["code"], "글", len(ev["_text"]))
        except Exception as e:                                   # noqa: BLE001
            log("홍콩 PDF 실패", ev["code"], e)
    finish(ev, None)


def hk_watch():
    seen = set()
    sent = set()
    first = True
    while True:
        t0 = time.time()
        today = datetime.now(HKT).date()
        try:
            for r in hk.fetch_day(today):
                k = (r["date"], r["code"], r["time"], r["fy"])
                if k in seen:
                    continue
                seen.add(k)
                if not r["time"] or first and stale(datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=HKT)):
                    sent.add((r["date"], r["code"]))
                    continue
                if HK_NOT.search(r["fy"] or "") or (r["date"], r["code"]) in sent:
                    continue
                sent.add((r["date"], r["code"]))
                if H.skip("hk", r["code"], r["name"]):
                    continue
                ev = base_event("hk", r["code"], r["name"], f"{r['date']} {r['time']}", HKT,
                                "HKEXnews 실적 공시", r["link"], r["fy"])
                POOL.submit(safe, hk_process, ev)
            first = False
        except Exception as e:                                   # noqa: BLE001
            log("홍콩:", e)
            time.sleep(10)
        time.sleep(max(1, HK_EVERY - (time.time() - t0)))


def safe(fn, *a):
    try:
        fn(*a)
    except Exception:                                            # noqa: BLE001
        log("작업 실패", fn.__name__, traceback.format_exc(limit=3))


# ── 다시 보내기(시험) ──────────────────────────────────────────────────
# 저장소 맨 위 replay.txt 를 고치면(첫 줄 = 아무 새 이름, 다음 줄 = "us 3" · "jp 2" · "hk 2")
# 오늘 나온 실제 공시 몇 건을 지금 모양으로 다시 그려 텔레그램에만 보낸다. 서버가 켜질 때
# 이미 있던 이름은 안 돌린다. railway.json 의 watchPatterns 밖이라 재배포도 안 일어난다.
REPLAY_URL = "https://api.github.com/repos/CBturbomax/Earning-Samurai-Calendar/contents/replay.txt?ref=main"


def replay_run(lines):
    for ln in lines:
        f = re.match(r"segfill\s+(\d+)", ln.strip())
        if f:
            segfill(int(f.group(1)))
            continue
        f = re.match(r"jpcode\s+(\w+)", ln.strip())
        if f:
            for back in range(0, 5):
                day = (datetime.now(JST) - timedelta(days=back)).strftime("%Y-%m-%d")
                rows = []
                for page in range(1, 8):
                    got = fj.listing(day, page)
                    if not got:
                        break
                    rows += got
                hit = [r for r in rows if r["code"] == f.group(1) and fj.TANSHIN.search(r["title"]) and r["zip"]]
                if hit:
                    jp_process(hit[0], day, True)
                    break
            continue
        f = re.match(r"serfill\s+(\d+)", ln.strip())
        if f:
            serfill(int(f.group(1)))
            continue
        f = re.match(r"segfilljp\s+(\d+)", ln.strip())
        if f:
            segfill_jp(int(f.group(1)))
            continue
        m = re.match(r"(us|jp|hk)\s+(\d+)", ln.strip())
        if not m:
            continue
        mkt, n = m.group(1), min(int(m.group(2)), 5)
        if mkt == "us":
            xml = http_get(FEED.format(form="8-K").replace("count=40", "count=100"), timeout=15)
            got = 0
            for e in ENTRY.findall(xml):
                if "2.02" not in html.unescape(e) or got >= n:
                    continue
                acc = re.search(r"AccNo:\s*</b>\s*([\d-]+)|AccNo:\s*([\d-]+)", html.unescape(e))
                t = re.search(r"<title>[^<]*?-\s*(.*?)\s*\((\d{10})\)", e)
                upd = re.search(r"<updated>([^<]+)</updated>", e)
                if acc and t and upd and cik_ticker().get(int(t.group(2))):
                    us_process(int(t.group(2)), acc.group(1) or acc.group(2), html.unescape(t.group(1)), upd.group(1), True)
                    got += 1
        elif mkt == "jp":
            for back in range(0, 5):
                day = (datetime.now(JST) - timedelta(days=back)).strftime("%Y-%m-%d")
                rows = [r for r in fj.listing(day, 1) if fj.TANSHIN.search(r["title"])
                        and not fj.NOT_TANSHIN.search(r["title"]) and r["zip"] and not H.skip("jp", r["code"], r["name"])]
                if rows:
                    for r in rows[:n]:
                        jp_process(r, day, True)
                    break
        else:
            for back in range(0, 4):
                d = (datetime.now(HKT) - timedelta(days=back)).date()
                rows = [r for r in hk.fetch_day(d) if r["time"] and not HK_NOT.search(r["fy"] or "")
                        and not H.skip("hk", r["code"], r["name"])]
                if rows:
                    for r in sorted(rows, key=lambda r: -((H.row(f"hk:{r['code']}") or [0] * 12)[11] or 0))[:n]:
                        ev = base_event("hk", r["code"], r["name"], f"{r['date']} {r['time']}", HKT,
                                        "HKEXnews 실적 공시", r["link"], r["fy"])
                        ev["replay"] = True
                        hk_process(ev)
                    break


def replay_loop():
    path = DATA / "replay_id"
    try:
        last = path.read_text().strip()
    except OSError:
        last = None
    while True:
        try:
            req = urllib.request.Request(REPLAY_URL, headers={"Accept": "application/vnd.github.raw",
                                                               "User-Agent": "earning-samurai"})
            with urllib.request.urlopen(req, timeout=20) as r:
                lines = r.read().decode().splitlines()
            rid = lines[0].strip() if lines else ""
            if rid and rid != last:
                path.write_text(rid)
                if last is not None:
                    log("다시 보내기 요청", rid, lines[1:])
                    replay_run(lines[1:])
                last = rid
        except Exception as e:                                   # noqa: BLE001
            if "404" not in str(e):
                log("다시 보내기:", e)
            if last is None:
                last = ""
        time.sleep(90)


def history_loop():
    """사이트가 다시 빌드되면 1분 안에 따라 받는다 — 텔레그램 그림이 늘 최신 사이트와 같게."""
    last, t0 = None, time.time()
    while True:
        time.sleep(60)
        try:
            v = http_get(SITE + "version.txt?t=" + str(int(time.time())), ua="Mozilla/5.0", timeout=20).strip()
        except Exception:                                        # noqa: BLE001
            v = None
        if (v and last and v != last) or time.time() - t0 > 20 * 60:
            safe(H.refresh)
            t0 = time.time()
        last = v or last


# ── HTTP ────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def head(self, code=200, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/health"):
            self.head()
            self.wfile.write(json.dumps({"ok": True, "events": len(S.events), "subs": len(TG.subs),
                                         "history": int(time.time() - H.ts),
                                         "volume": os.path.ismount(str(DATA)), "llm": LLM_TEST}).encode())
        elif path == "/api/live":
            self.head()
            cut = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            self.wfile.write(json.dumps({"ts": now_iso(), "events": [e for e in S.events if e.get("detected", "") >= cut]},
                                        ensure_ascii=False).encode())
        elif path == "/api/test" and os.environ.get("TEST_INJECT"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ev = base_event("us", qs.get("code", ["MRK"])[0], "test", datetime.now(ET).strftime("%Y-%m-%d %H:%M"),
                            ET, "시험", "https://example.com")
            POOL.submit(safe, finish, ev, {"label": "3Q26", "rev": 18085e6, "opi": 5900e6, "ni": 5065e6, "eps": 1.98})
            self.head()
            self.wfile.write(b'{"ok":true}')
        elif path == "/api/segs":                                # 수집 루프가 받아 data/segments_ai.json 으로
            self.head()
            with SEGAI_LOCK:
                self.wfile.write(json.dumps(dict(SEGAI, _series=SER), ensure_ascii=False).encode())
        elif path == "/api/log":                                 # 진단 — 최근 로그 300줄(토큰 같은 비밀은 안 찍는다)
            self.head(ctype="text/plain; charset=utf-8")
            self.wfile.write("\n".join(LOGS).encode())
        elif path == "/api/stats":
            self.head()
            self.wfile.write(json.dumps(stats(), ensure_ascii=False).encode())
        elif path.startswith("/chart/"):
            f = DATA / "charts" / Path(path).name
            if f.exists() and f.suffix == ".png":
                self.head(ctype="image/png", extra={"Cache-Control": "max-age=86400"})
                self.wfile.write(f.read_bytes())
            else:
                self.head(404)
        elif path == "/stream":
            self.head(ctype="text/event-stream", extra={"Connection": "keep-alive"})
            q = queue.Queue(maxsize=200)
            S.subs.append(q)
            try:
                self.wfile.write(b": hi\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=20)
                    except queue.Empty:
                        msg = ": ping\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                if q in S.subs:
                    S.subs.remove(q)
        else:
            self.head(404)


def stats():
    """소스별 지연(초): 공개 → 감지 → 텔레그램."""
    out = {}
    for e in S.events[-300:]:
        try:
            p = datetime.fromisoformat(e["published"])
            d = datetime.fromisoformat(e["detected"])
            t = datetime.fromisoformat(e["t_tg"]) if e.get("t_tg") else None
        except (KeyError, ValueError):
            continue
        s = out.setdefault(e["mkt"], {"n": 0, "detect": [], "telegram": []})
        s["n"] += 1
        s["detect"].append((d - p).total_seconds())
        if t:
            s["telegram"].append((t - p).total_seconds())
    for s in out.values():
        for k in ("detect", "telegram"):
            v = sorted(s[k])
            s[k] = {"median": v[len(v) // 2], "p90": v[int(len(v) * 0.9)]} if v else None
    return out


LLM_TEST = {"state": "대기"}


def llm_selftest():
    """켤 때 '왜' 요약을 한 번 돌려 본다 — 토큰이 살아 있는지 /health 로 보인다."""
    if not brief.enabled():
        LLM_TEST.update(state="키 없음")
        return
    t0 = time.time()
    r = brief.why("시험회사", "Revenue rose 18% year over year, driven by strong demand for AI data center "
                  "servers in North America and price increases. Operating margin improved due to lower "
                  "component costs. The company raised its full-year revenue outlook.")
    LLM_TEST.update(state="정상" if r else "실패", sec=round(time.time() - t0, 1),
                    via="claude-max" if brief.CC_TOKEN else "api", sample=(r or {}).get("why", [])[:1])


def main():
    log("시작 — 텔레그램", "켜짐" if TG_TOKEN else "꺼짐(TG_BOT_TOKEN 없음)", "· 구독", len(TG.subs))
    H.refresh()
    shot.start(lambda: H.html)
    for fn in (TG.poll, us_watch, jp_watch, hk_watch, history_loop, llm_selftest, replay_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
