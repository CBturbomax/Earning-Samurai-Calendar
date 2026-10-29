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
from markets import excluded       # noqa: E402

import brief                       # noqa: E402
import charts                      # noqa: E402

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


def log(*a):
    print(datetime.now(KST).strftime("%m-%d %H:%M:%S"), *a, flush=True)


def http_get(url, ua=None, timeout=20, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": ua or SEC_UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    return b if binary else b.decode("utf-8", "replace")


# ── 이력 ──────────────────────────────────────────────────────────────────
class History:
    """사이트가 가진 것 — 종목 이름·시총·지난 분기·사업부. 20분마다 새로."""

    FILES = ("financials.json", "financials_intl.json", "financials_jp.json",
             "caps.json", "earnings_us.json", "earnings_us_fpi.json", "guidance_hist_jp.json")

    def __init__(self):
        self.lock = threading.Lock()
        self.D = {"rows": [], "fin": {}, "seg": {}}
        self.rows_by_key = {}
        self.ind = {}
        self.ts = 0

    def refresh(self):
        try:
            if os.environ.get("HISTORY_LOCAL"):                 # 시험용 — 저장소의 index.html
                t = (ROOT / "index.html").read_text(encoding="utf-8")
            else:
                t = http_get(SITE + "index.html?t=" + str(int(time.time())), ua="Mozilla/5.0", timeout=90)
            i = t.index('<script id="payload" type="application/json">')
            i = t.index(">", i) + 1
            D = json.loads(t[i:t.index("</script>", i)])
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
        return {"stage": st, "prev": prev, "now": [fc.get("rev"), fc.get("opi")]}

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

    def send_text(self, cid, text):
        try:
            self.api("sendMessage", {"chat_id": cid, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": "true"})
        except Exception as e:                                   # noqa: BLE001
            log("텔레그램 전송 실패", cid, e)

    def send_album(self, caption, pngs):
        if not TG_TOKEN:
            return
        for cid in list(self.subs):
            try:
                if not pngs:
                    self.send_text(cid, caption)
                    continue
                media, files = [], {}
                for i, png in enumerate(pngs):
                    files[f"p{i}"] = (f"p{i}.png", png, "image/png")
                    item = {"type": "photo", "media": f"attach://p{i}"}
                    if i == 0:
                        item.update(caption=caption, parse_mode="HTML")
                    media.append(item)
                if len(pngs) == 1:
                    self.api("sendPhoto", {"chat_id": cid, "caption": caption, "parse_mode": "HTML"},
                             files={"photo": files["p0"]})
                else:
                    self.api("sendMediaGroup", {"chat_id": cid, "media": json.dumps(media)}, files=files)
            except Exception as e:                               # noqa: BLE001
                log("텔레그램 앨범 실패", cid, e)


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


def finish(ev, point=None):
    """숫자가 있으면 차트를 그리고 · 보관 · 사이트 · 텔레그램."""
    key = ev["key"]
    with H.lock:
        fin = dict(H.D.get("fin", {}).get(key) or {})
        seg = H.D.get("seg", {}).get(key)
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
    box = {}
    th = threading.Thread(target=lambda: box.update(b=brief.why(ev["name"], text)), daemon=True)
    th.start()                                                   # 차트 그리는 동안 요약을 받는다
    with H.lock:
        ann = H.D.get("ann", {}).get(key)
        G = H.D.get("fcst", {}).get(key)
    gh = GD.get(ev["code"], ann) if ev["mkt"] == "jp" else None
    pngs = []
    try:
        pngs = charts.render(ev, pts, seg, fin.get("freq", "Q"), ann, G, gh)
    except Exception:                                            # noqa: BLE001
        log("차트 실패", key, traceback.format_exc(limit=2))
    th.join(45)
    if box.get("b"):
        ev["brief"] = box["b"]
    for i, png in enumerate(pngs):
        (DATA / "charts").mkdir(exist_ok=True)
        (DATA / "charts" / f"{fname(ev['id'])}_{i}.png").write_bytes(png)
    ev["charts"] = [f"/chart/{fname(ev['id'])}_{i}.png" for i in range(len(pngs))]
    if not S.add(ev):
        return
    S.broadcast(ev)
    send, why = tg_pick(ev, pts)
    ev["tg"], ev["tgWhy"] = send, why
    if send:
        TG.send_album(caption(ev), pngs)
        ev["t_tg"] = now_iso()
    S.update(ev)
    log(f"실적 {ev['mkt']}:{ev['code']} {ev['name']} · 공개 {ev.get('published')} 감지 {ev['detected']} "
        f"텔레그램 {'보냄 ' + ev['t_tg'] if send else '안 보냄'} ({why})")


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
    b = ev.get("brief") or {}
    if b.get("why"):
        lines += ["", "💡 <b>왜 이렇게 나왔나</b> <i>(회사 설명)</i>"] + [f"• {e(x)}" for x in b["why"]]
    if b.get("guide"):
        lines += ["", f"🗓 {e(b['guide'])}"]
    if b.get("watch"):
        lines += ["", "⚠️ <b>주의</b>"] + [f"• {e(x)}" for x in b["watch"]]
    if not b and not p and ev.get("title"):
        lines.append(e(ev["title"][:160]))
    links = [f"<a href=\"{e(ev['url'])}\">원문</a>"] if ev.get("url") else []
    links.append(f"<a href=\"{SITE}\">어닝사무라이</a>")
    lines += ["", "🔗 " + " · ".join(links)]
    out = "\n".join(lines)
    while len(out) > 1024 and b:                    # 사진 설명 한도 — 주의부터 한 줄씩 덜어낸다
        for k in ("watch", "guide", "why"):
            if b.get(k):
                b[k] = b[k][:-1] if isinstance(b[k], list) else ""
                break
        else:
            break
        ev["brief"] = b
        return caption(ev)
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


def us_process(cik, acc, name, upd):
    tick = cik_ticker().get(cik)
    if not tick:
        return
    code = tick.replace("-", ".")
    if H.skip("us", code, name):
        return
    pub_et = datetime.fromisoformat(upd).astimezone(ET).strftime("%Y-%m-%d %H:%M")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
    ev = base_event("us", code, name, pub_et, ET, "SEC 8-K 보도자료", url)
    point = None
    try:
        raw = lu.ex99_html(cik, acc.replace("-", ""))
        ev["_text"] = brief.plain(raw)[:brief.LIMIT]
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
def jp_process(row, day):
    code = row["code"]
    if H.skip("jp", code, row["name"]):
        return
    ev = base_event("jp", code, row["name"], f"{day} {row['time']}", JST, "TDnet 결산단신",
                    fj.LIST_URL.format(page=1, day=day.replace("-", "")), row["title"])
    point = None
    try:
        blob = fj.get(row["zip"], binary=True)
        if blob:
            vals = fj.read_summary(blob)
            ev["_text"] = fj.read_narrative(blob)
            if vals:
                ev["guide"] = GD.add(code, fj.forecast_from(vals), day)
            prior = fj.base_series().get(f"jp:{code}", {})
            q = fj.quarter_from(vals, prior) if vals else None
            if q:
                point = {"label": q["label"], "rev": q["rev"], "opi": q.get("opi"), "ni": q.get("ni")}
            ev["url"] = row["zip"]
    except Exception:                                            # noqa: BLE001
        log("일본 결산단신 실패", code, traceback.format_exc(limit=2))
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


def history_loop():
    while True:
        time.sleep(20 * 60)
        safe(H.refresh)


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
                                         "volume": os.path.ismount(str(DATA))}).encode())
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


def main():
    log("시작 — 텔레그램", "켜짐" if TG_TOKEN else "꺼짐(TG_BOT_TOKEN 없음)", "· 구독", len(TG.subs))
    H.refresh()
    for fn in (TG.poll, us_watch, jp_watch, hk_watch, history_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
