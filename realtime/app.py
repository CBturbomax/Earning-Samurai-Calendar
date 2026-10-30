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
import scrape_jp_tdnet as jt       # noqa: E402
import montable                    # noqa: E402
import pdftext                     # noqa: E402
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
QUIET_MIN = int(os.environ.get("QUIET_MIN", "180"))   # 켤 때 지난 3시간 공시를 다시 본다 — 보낸 것은 S 가 거른다(10/30 누락)


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
        # 일본 TDnet 줄은 업종이 비어 온다 — 사이트 캘린더의 그 회사 업종(한국어)으로 한 번 더(부동산·전력·가스, 10/30).
        r = self.row(f"{mkt}:{code}") if mkt == "jp" else None
        if r and r[5] in ("부동산", "전력", "가스", "전기·가스", "은행", "육운", "해운", "창고", "창고·운수"):
            return True
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
            self.events = self.events[-5000:]            # 하루 수백 건 — 켤 때 다시 훑어도 보낸 것을 또 보내지 않게 넉넉히
            self._save()
        return True

    def get(self, eid):
        with self.lock:
            return next((e for e in reversed(self.events) if e["id"] == eid), None)

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

    def __init__(self, name="guide_jp.json", seed="guidance_hist_jp.json"):
        self.path, self.seed = DATA / name, seed
        self.lock = threading.Lock()
        try:
            self.own = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.own = {}

    def add(self, code, fc, published):
        if not fc or not fc.get("end") or all(fc.get(k) is None for k in ("rev", "opi", "ni")):
            return None
        st = stage_of(fc["end"], published)
        if not st:
            return None
        fy = fc["end"][:7]
        with self.lock:
            before = self.get(code).get(fy, {})
            prev = next((before[s] for s in reversed(STAGES[:STAGES.index(st)]) if s in before), None)
            now = [fc.get("rev"), fc.get("opi"), fc.get("ni")]
            self._put(code, fy, st, now)
        return {"stage": st, "prev": prev, "now": now, "end": fc["end"]}

    def _put(self, code, fy, st, vals):
        self.own.setdefault(code, {}).setdefault(fy, {})[st] = vals
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.own))
        tmp.replace(self.path)

    def put_us(self, code, published, g):
        """미국 — 보도자료 연간 전망 {fy_end, rev, op, eps} 를 그 단계에 적는다. [매출, 영업이익, EPS]."""
        st = stage_of(g["fy_end"] + "-28", published)
        if not st:
            return None
        with self.lock:
            before = self.get(code).get(g["fy_end"], {})
            prev = next((before[s] for s in reversed(STAGES[:STAGES.index(st)]) if s in before), None)
            now = [g.get("rev"), g.get("op"), g.get("eps")]
            self._put(code, g["fy_end"], st, now)
        return {"stage": st, "prev": prev, "now": now, "end": g["fy_end"]}

    def get(self, code, ann=None):
        try:
            seed = json.loads((ROOT / "data" / self.seed).read_text()).get(code, {}) if self.seed else {}
        except (OSError, ValueError):
            seed = {}
        out = {fy: dict(v) for fy, v in seed.items()}
        for fy, v in (self.own.get(code) or {}).items():
            if fy != "none":                            # 연간 전망을 안 내는 회사 표시
                out.setdefault(fy, {}).update(v)
        for y in (ann or {}).get("y") or []:
            if not y[5] and y[1]:                       # 확정 실적만
                out.setdefault(y[0][:7], {}).setdefault("실제", [y[1], y[2]])
        return out


GD = Guide()
GU = Guide("guide_us.json", None)                 # 미국 — 실적 보도자료의 연간 전망(AI 판독)


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
# 회원님 규칙(10/30 갱신): 리츠·금융(은행·증권·보험·기타 금융)·화학·철강·신약 개발 바이오는 빼고,
# 시총 1,000억 원 밑도 뺀다. 그 밖은 **전부** 보낸다(앞서 두었던 '작은 회사는 잘 나왔을 때만'은 걷었다).
# 세 시장 같은 규칙. 사이트에는 여전히 전부 뜬다.
TG_OUT_JP = {"은행", "증권", "증권·선물", "보험", "기타금융", "기타 금융", "화학", "철강", "리츠", "부동산투자신탁",
             "銀行業", "証券、商品先物取引業", "保険業", "その他金融業", "化学", "鉄鋼", "REIT"}
TG_OUT_IND = re.compile(
    r"(?i)\bbanks?\b|savings institutions|insurance|insurers|investment bankers|brokers|capital markets|"
    r"finance|financial|credit services|asset management|investment managers|mortgage|"
    r"^chemicals$|chemicals|steel|real estate investment trusts|\breit\b|biotechnology")
BIO_DESC = re.compile(r"創薬|新薬開発|バイオベンチャー|신약|바이오벤처|drug discovery|clinical[- ]stage|biopharmaceutical company",
                      re.I)
MIN_KRW = float(os.environ.get("TG_MIN_KRW", "1e11"))      # 1,000억 원


def guide_boom(ev):
    """가이던스를 크게 올렸나 — 관심 범위 밖(업종·시총)이어도 보낸다(회원님 10/30).
    이익(영업·경상·순) +30% 이상, 또는 매출 +15% 이상 올린 것. 직전 회사 예상 대비."""
    pairs = []
    g = ev.get("guide")
    if g and g.get("prev"):
        names = ("매출", "영업이익", "EPS" if ev["mkt"] == "us" else "순이익")
        pairs += [(n, a, b) for n, a, b in zip(names, g["prev"], g["now"])]
    p = ev.get("preInfo")
    if p and p.get("what") == "가이던스 수정":
        pairs += [(r["n"], r.get("prev"), r.get("now")) for r in p["rows"]]
    best = None
    for n, a, b in pairs:
        if a and b is not None and a > 0:
            c = (b / a - 1) * 100
            if (n == "매출" and c >= 15) or (n != "매출" and c >= 30):
                if not best or c > best[1]:
                    best = (n, c)
    return f"🚀 가이던스 급상향 ({best[0]} {pct(best[1])})" if best else ""


def tg_pick(ev, pts):
    """(보낼까, 왜)."""
    boom = guide_boom(ev)
    if boom:
        return True, boom
    if ev["mkt"] == "jp" and (ev.get("sector") or "") in TG_OUT_JP:
        return False, "업종 제외"
    if TG_OUT_IND.search(H.ind.get(ev["key"], "") or ""):
        return False, "업종 제외"
    # 일본 TDnet 줄은 업종이 비어 오는 일이 많다 — 이름으로 한 번 더(은행·증권·보험·금융·화학·철강).
    if ev["mkt"] == "jp" and not ev.get("sector") and re.search(
            r"銀行|証券|保険|ファイナンス|クレジット|リース|信託|化学|化成|鉄鋼|製鉄|製鋼|ＲＥＩＴ|投資法人", ev.get("orig", "")):
        return False, "업종 제외(이름)"
    if re.search(r"(?i)\bREIT\b|投資法人|리츠", ev.get("orig", "") + " " + ev.get("name", "")):
        return False, "리츠 제외"
    with H.lock:
        desc = (H.D.get("descKo") or {}).get(ev["key"], "") + " " + (H.D.get("desc") or {}).get(ev["key"], "")
    if BIO_DESC.search(desc):
        return False, "신약 개발 바이오 제외"
    krw = (ev.get("cap") or 0) * 1e9 * float(H.D.get("usdKrw") or 1400)
    if krw < MIN_KRW:
        return False, "시총 1,000억 원 미만" if krw else "시총 모름"
    return True, cap_won(ev)


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
    if not ev.get("replay"):                     # 이미 보낼지 정한 공시 — 다시 그리지도 않는다(켤 때 다시 훑는 몫)
        old = S.get(ev["id"]) or {}
        if old.get("tgWhy") or old.get("t_tg"):
            return
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
    # 🏢 비즈니스 한 줄은 앨범 글 맨 위에 꼭 — 미리 만든 게 없으면 지금 만든다(몇 초).
    if not biz_of(ev) and brief.enabled():
        with H.lock:
            d = (H.D.get("descKo") or {}).get(key) or (H.D.get("desc") or {}).get(key) or ""
        got = brief.biz_lines({key: f"{ev['name']} ({ev.get('orig', '')}) | {(d or text)[:800]}"}, timeout=40)
        if got.get(key):
            BIZ[key] = got[key]
            BIZ_PATH.write_text(json.dumps(BIZ, ensure_ascii=False))
        log("비즈니스 즉석", key, got.get(key) or "못 만듦")
    # 회원님(10/30): "못 채울 거면 바로 쏘지 말고 다 완성해서 쏴 — 그렇다고 못 채웠다고 안 쏘지는 말고."
    # 이번 분기 가이던스나 사업부가 비면 AI 원문 판독(최대 80초)을 기다려 채운 다음 앨범을 보낸다. 못 채우면 그대로 보낸다.
    seg_missing = bool(seg) and point is not None and not ev.get("segPoint")
    guide_missing = ev["mkt"] in ("jp", "us") and not ev.get("guide") and not ev.get("preInfo")
    b_early = None
    if (seg_missing or guide_missing) and brief.enabled():
        th.join(80)
        b_early = box.get("b")
        if b_early:
            g = b_early.get("fyg")
            if guide_missing and g:
                try:
                    gd = (GU.put_us(ev["code"], ev["kstDate"], g) if ev["mkt"] == "us" else
                          GD.add(ev["code"], {"end": g["fy_end"] + "-28", "rev": g.get("rev"), "opi": g.get("op")}, ev["date"]))
                    # AI 가 단위를 틀리면(백만 엔을 엔으로) 급상향으로 오인한다 — 직전 예상의 절반~두 배 밖이면 안 쓴다
                    pv, nw = (gd or {}).get("prev"), (gd or {}).get("now")
                    if gd and not (pv and pv[0] and nw and nw[0] and not 0.5 <= nw[0] / pv[0] <= 2):
                        ev["guide"] = gd
                except Exception:                                # noqa: BLE001
                    log("가이던스 채우기 실패", key, traceback.format_exc(limit=1))
            if seg_missing:
                sp0 = seg_ai_try(ev, b_early, seg)
                if sp0:
                    ev["segPoint"] = sp0
        log("완성 기다림", key, "가이던스", "채움" if ev.get("guide") else "없음", "· 사업부", "채움" if ev.get("segPoint") else "없음")
    with H.lock:
        ann = H.D.get("ann", {}).get(key)
        G = H.D.get("fcst", {}).get(key)
    gh = GD.get(ev["code"], ann) if ev["mkt"] == "jp" else GU.get(ev["code"], ann) if ev["mkt"] == "us" else None
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
        # 이미 본 공시 — 다만 **텔레그램을 보낼지 정하기 전에** 서버가 꺼졌던 것(재배포)이면 다시 한다.
        # 아오이전자(6832, 10/30)가 그렇게 빠졌다: 앞 서버가 받아 적고 그리는 사이 재배포로 꺼졌고,
        # 새 서버는 '본 것'이라 건너뛰었다.
        old = S.get(ev["id"]) or {}
        if old.get("tgWhy") or old.get("t_tg"):
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
    gpng = None
    if ev["mkt"] == "us" and b.get("fyg") and not b_early:   # 이번 보도자료의 연간 전망 — 앨범 전에 못 받았을 때만 답장에
        try:
            ev["guide"] = GU.put_us(ev["code"], ev["kstDate"], b["fyg"])
            gh2 = GU.get(ev["code"], ann)
            if H.html and sum(1 for v in gh2.values() for s in v if s != "실제") >= 3:
                gpng = (shot.render(dict({k: v for k, v in ev.items() if not k.startswith("_")}, onlySeg="guide"), gh2) or [None])[0]
        except Exception:                                        # noqa: BLE001
            log("미국 가이던스 실패", key, traceback.format_exc(limit=2))
    ser_add_q(key, b, ev.get("cur") or "USD")
    had_seg = bool(ev.get("segPoint"))
    sp = None if had_seg else seg_ai_try(ev, b, seg)
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
        if not segpng and sp and not had_seg and H.html:   # 앨범에 이번 분기 사업부가 빠졌었다 — 채운 그림을 답장에(9830, 10/30)
            try:
                segpng = (shot.render(dict({k: v for k, v in ev.items() if not k.startswith("_")}, onlySeg="segchart")) or [None])[0]
            except Exception:                    # noqa: BLE001
                segpng = None
        segpng = segpng or gpng
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


def segfill(n, only=None):
    """이미 이번 분기를 발표했는데 부문이 뒤처진 미국 회사 — 보도자료를 다시 읽어 메운다. **시총 큰 순**(인텔이 뒤로 밀렸다, 10/30).
    only: 이 종목들만(예: {"us:INTC"}) — 이미 있어도 다시 읽는다."""
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
        if only is not None:
            if k not in only:
                continue
        elif p[0] != top or (s and s["pts"][-1][0] == top) or (k in SEGAI and SEGAI[k].get("label") == top):
            continue
        cik = rev_map.get(k[3:].replace(".", "-"))
        if cik:
            todo.append((k, cik, p))
    with H.lock:
        capk = {k: max((r[11] or 0) for r in H.rows_by_key.get(k) or [[0] * 12]) for k, _, _ in todo}
    todo = sorted(todo, key=lambda t: -capk.get(t[0], 0))[:n]
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


def guidefill_us(n, back=8):
    """미국 — 지난 실적 보도자료 여덟 장의 연간 전망을 한 번에 읽어 가이던스 변천을 쌓는다(시총 큰 순)."""
    import scrape_pr_us as pr
    with H.lock:
        rows = dict(H.rows_by_key)
    caps = {}
    for k, rs in rows.items():
        if k.startswith("us:") and rs:
            caps[k] = max((r[11] or 0) for r in rs)
    rev_map = {v: k for k, v in cik_ticker().items()}
    todo = [k for k, c in sorted(caps.items(), key=lambda x: -x[1])
            if c * 1e9 * float(H.D.get("usdKrw") or 1400) >= MIN_KRW and not TG_OUT_IND.search(H.ind.get(k, "") or "")
            and k[3:] not in GU.own][:n]
    log("미국 가이던스 이력 시작", len(todo), "종목")
    done = [0]

    def one(k):
        cik = rev_map.get(k[3:].replace(".", "-"))
        if not cik:
            return
        try:
            rec = json.loads(pr.get(pr.SUBS.format(cik=cik), binary=False) or "{}")["filings"]["recent"]
        except Exception:                                        # noqa: BLE001
            return
        hits = [(a.replace("-", ""), d) for a, fm, it, d in zip(rec.get("accessionNumber", []), rec.get("form", []),
                                                               rec.get("items", []), rec.get("filingDate", []))
                if fm == "8-K" and "2.02" in (it or "")][:back]
        docs = []
        for acc, d in hits:
            try:
                t = pr.press_release(cik, acc)
            except Exception:                                    # noqa: BLE001
                t = None
            if t:
                docs.append((d, t))
        got = brief.us_guide(k[3:], list(reversed(docs))) if docs else []
        for d, g in sorted(got, key=lambda x: x[0]):
            GU.put_us(k[3:], d, g)
        if not got:                                              # 연간 전망을 안 내는 회사 — 다시 안 본다
            with GU.lock:
                GU._put(k[3:], "none", "none", None)
        else:
            done[0] += 1
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(one, todo))
    log("미국 가이던스 이력 끝", done[0], "/", len(todo))


JH_PATH = DATA / "jphist.json"
try:
    JH = json.loads(JH_PATH.read_text())
except (OSError, ValueError):
    JH = {"done": [], "seg": {}, "fin": {}}
JH.setdefault("fin", {})
JH_LOCK = threading.Lock()
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
_yano = {"t": 0.0, "lock": threading.Lock()}


def jp_fin_spans(vals):
    """결산단신 요약 XBRL -> {'시작/종료': {rev, opi, ni}} 누계 실적(예상은 뺀다, 연결 우선). 분기로 되돌리는 건 빌드가 한다."""
    out = {}
    for k, names in (("rev", fj.NAME_REV), ("opi", fj.NAME_OPI), ("ni", fj.NAME_NI)):
        for nm in names:
            hits = [(cid, v, se) for cid, v, se in vals.get(nm, [])
                    if not fj.FORECAST_CTX.search(cid) and fj.DUR_CTX.search(cid) and se and se[0] and se[1]
                    and not fj.NONCONS_CTX.search(cid)]
            if hits:
                for cid, v, (st, en) in hits:
                    out.setdefault(f"{st}/{en}", {})[k] = v
                break
    return out


def jp_old_points(key):
    """옛 결산단신 누계 -> 사이트 차트 점 [이름, 매출, 영업이익] (같은 시작일의 앞 누계를 빼 분기로). build.py 의 jp_old_fin 과 같은 셈."""
    with JH_LOCK:
        spans = dict(JH["fin"].get(key) or {})
    mon = lambda a, b: (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7]) + 1   # noqa: E731
    by = {}
    for span, row in spans.items():
        st, _, en = span.partition("/")
        if st and en and row.get("rev"):
            by.setdefault(st, []).append((en, row))
    q = {}
    for st, lst in by.items():
        lst.sort()
        prev = None
        for en, row in lst:
            m = mon(st, en)
            if m == 3:
                q[en] = row
            elif prev and mon(st, prev[0]) == m - 3:
                q[en] = {k: (row[k] - prev[1][k]) if row.get(k) is not None and prev[1].get(k) is not None else None
                         for k in ("rev", "opi")}
            prev = (en, row)
    return [[f"{(int(en[5:7]) + 2) // 3}Q{en[2:4]}", q[en]["rev"], q[en].get("opi")]
            for en in sorted(q) if q[en].get("rev") and q[en]["rev"] > 0]


def jphist(n, since="2017-01-01"):
    """일본 옛 결산단신을 통째로 — 가이던스 변천(연간 예상)과 부문 누계(2024년부터 비던 3·4분기)를 같이 채운다.
    목록은 yanoshin TDnet API(2009년치부터, 문서번호·XBRL 이름), 파일은 JPX 공시 보관소(약 5~6년,
    www2.jpx.co.jp/disc/{코드}0/{파일}). TDnet 은 한 달만 남기고 InvestingWiki 는 하루 8천 건 한도라 직접 받는다."""
    with H.lock:
        rows = dict(H.rows_by_key)
    caps = {}
    for k, rs in rows.items():
        if k.startswith("jp:") and rs:
            caps[k[3:]] = max((r[11] or 0) for r in rs)
    done = set(JH["done"])
    JH["want"] = max(JH.get("want") or 0, min(n, len(caps)))
    todo = [c for c, _ in sorted(caps.items(), key=lambda x: -x[1]) if c not in done][:n]
    log("일본 옛 결산단신 시작", len(todo), "종목")
    got = [0, 0, "9999"]                                         # 종목 · 문서 · 받은 가장 옛 공시일

    def listing(code):
        with _yano["lock"]:                                      # 한 사람이 돌리는 무료 API — 1초에 한 번
            wait = 0.4 - (time.time() - _yano["t"])
            if wait > 0:
                time.sleep(wait)
            _yano["t"] = time.time()
        j = json.loads(http_get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=300&hasXBRL=1",
                                ua=BROWSER_UA, timeout=30))
        return [it.get("Tdnet", it) for it in (j.get("items") or [])]

    def one(code):
        try:
            items = listing(code)
        except Exception as e:                                   # noqa: BLE001
            log("옛 결산단신 목록 실패", code, repr(e)[:120])
            return
        items = sorted((it for it in items if (it.get("pubdate") or "")[:10] >= since and it.get("url_xbrl")
                        and fj.TANSHIN.search(it.get("title") or "") and not fj.NOT_TANSHIN.search(it.get("title") or "")),
                       key=lambda it: it["pubdate"])
        seg, fin = {}, {}
        for it in items:
            fn = it["url_xbrl"].rsplit("/", 1)[-1]
            try:
                blob = http_get(f"https://www2.jpx.co.jp/disc/{code}0/{fn}", ua=BROWSER_UA, timeout=60, binary=True)
            except Exception:                                    # noqa: BLE001
                continue                                         # 보관소에 없는 옛 파일(404)
            try:
                vals = fj.read_summary(blob)
                if vals:
                    GD.add(code, fj.forecast_from(vals), it["pubdate"][:10])
                for st, en, row in fj.read_segments(blob):
                    seg[f"{st}/{en}"] = row
                for span, row in jp_fin_spans(vals or {}).items():
                    fin.setdefault(span, row)
                got[1] += 1
                got[2] = min(got[2], it["pubdate"][:10])
            except Exception:                                    # noqa: BLE001
                continue
        with JH_LOCK:
            if seg:
                JH["seg"].setdefault("jp:" + code, {}).update(seg)
            if fin:
                JH["fin"].setdefault("jp:" + code, {}).update(fin)
            JH["done"].append(code)
            got[0] += 1
            if got[0] % 10 == 0:
                JH_PATH.write_text(json.dumps(JH, ensure_ascii=False))
                log("옛 결산단신", got[0], "/", len(todo), "종목 · 문서", got[1], "· 가장 옛", got[2])
    with ThreadPoolExecutor(10) as ex:                           # 회원님(10/30): "빠릿빠릿하게" — 파일은 JPX, 목록만 yanoshin
        list(ex.map(one, todo))
    with JH_LOCK:
        JH_PATH.write_text(json.dumps(JH, ensure_ascii=False))
    log("일본 옛 결산단신 끝", got[0], "종목 · 문서", got[1])


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
    """앨범 뒤에 붙는 답장 — 회원님 형식(10/30): 🏢 비즈니스 / 💡 실적 배경 / 🗓 가이던스 / ⚠️ 주의, 사이에 빈 줄."""
    e = html.escape
    b = ev.get("brief") or {}
    if b.get("biz") and not BIZ.get(ev["key"]):
        BIZ[ev["key"]] = b["biz"]
    with H.lock:
        biz = (H.D.get("descKo") or {}).get(ev["key"]) or ""
    biz = "" if ev.get("bizInCap") else (b.get("biz") or biz or "")      # 앨범 글에 이미 실었으면 답장엔 안 쓴다
    out = []

    def sec(title, items):
        items = [x for x in items if x]
        if items:
            out.append("\n".join([title] + [f"• {e(x)}" for x in items]))
    if b.get("key") and not ev.get("point"):
        sec("📊 <b>실적</b>", [b["key"]])
    sec("🏢 <b>비즈니스</b>", [biz.strip().rstrip(".")] if biz else [])
    sec("💡 <b>실적 배경</b>", b.get("why") or [])
    gd = b.get("guide")
    gd = gd if isinstance(gd, list) else [gd] if gd else []
    gl = guide_line(ev)
    if gl or gd:                                    # 첫 줄은 상향·하향·유지 판정 — 숫자는 그 밑에
        out.append("\n".join(["🗓 <b>가이던스</b>"] + ([gl] if gl else []) + [f"• {e(x)}" for x in gd]))
    sec("⚠️ <b>주의</b>", b.get("watch") or [])
    return "\n\n".join(out)


GSTAT = {"상향": "📈", "하향": "📉", "유지": "➡️", "엇갈림": "🔀", "첫 제시": "🆕", "철회": "⛔"}


def guide_status(ev):
    """가이던스가 직전 예상보다 올랐나·내렸나·그대로인가 — 회원님(10/30): "숫자보다 그게 더 중요."
    -> (상태, "매출 +3.0% / 영업이익 +5.1%") 또는 (None, "").
    일본은 결산단신 예상을 직전 예상과 직접 비교하고, 나머지는 AI 가 원문에서 읽은 판정(gstat)을 쓴다."""
    g = ev.get("guide")
    if g and g.get("prev"):
        diff, signs = [], []
        for n, a, b in zip(("매출", "영업이익", "EPS" if ev["mkt"] == "us" else "순이익"), g["prev"], g["now"]):
            if a and b is not None and a > 0:
                c = (b / a - 1) * 100
                signs.append(1 if c > 0.5 else -1 if c < -0.5 else 0)
                if signs[-1]:
                    diff.append(f"{n} {pct(c)}")
        if signs:
            st = ("유지" if not any(signs) else "상향" if all(x >= 0 for x in signs)
                  else "하향" if all(x <= 0 for x in signs) else "엇갈림")
            return st, " / ".join(diff)
    if g and g.get("stage") == "최초":
        return "첫 제시", ""
    st = ((ev.get("brief") or {}).get("gstat") or "").strip()
    return (st, "") if st in GSTAT else (None, "")


def guide_line(ev):
    st, d = guide_status(ev)
    if not st:
        return ""
    return f"{GSTAT[st]} 가이던스 : <b>{st}</b>" + (f" ({html.escape(d)})" if d else "")


def pct(v):
    return f"{v:+.1f}%".replace("+0.0%", "0%")


def cap_won(ev):
    """시총(원화): 1조 밑은 「1,980억 원」, 1조~10조는 「3.5조 원」, 10조부터 「11조 원」."""
    usd_b = ev.get("cap") or 0
    if not usd_b:
        return ""
    with H.lock:
        krw = usd_b * 1e9 * (H.D.get("usdKrw") or 1400)
    if krw < 1e12:
        return f"{round(krw / 1e8):,}억 원"
    if krw < 1e13:
        return f"{krw / 1e12:.1f}조 원".replace(".0조", "조")
    return f"{round(krw / 1e12):,}조 원"


def oku(v):
    """엔 -> '163억 엔' (1억 밑은 '0.5억 엔')."""
    if v is None:
        return "—"
    x = v / 1e8
    return (f"{x:,.0f}억 엔" if abs(x) >= 100 else f"{x:,.1f}억 엔".replace(".0억", "억"))


def mon_line(ev):
    """월차를 내는 회사 — 결산 카드에 최근 월차 한 줄(회원님 10/30)."""
    with H.lock:
        num = (H.D.get("monthlyNum") or {}).get(ev["code"])
    if not num or not num.get("historyVerified") or not num.get("m"):
        return ""
    r = num["m"][-1]
    if r[2] is None:
        return ""
    base = num.get("historyBase") or num.get("base") or ""
    return f"📅 월 매출 : {r[0].replace('-', '.')} {html.escape(base + ' ' if base else '')}매출 YoY {pct(r[2] - 100)}"


BIZ_PATH = DATA / "biz_ko2.json"                 # 2: 가장 구체적인 제품 이름까지(회원님 10/30)
try:
    BIZ = json.loads(BIZ_PATH.read_text())
except (OSError, ValueError):
    BIZ = {}


def biz_of(ev):
    """이 회사가 무엇을 파는지 한 줄 — 미리 만들어 둔 것(biz_loop), 없으면 사이트 설명의 첫 마디."""
    k = ev["key"]
    if BIZ.get(k):
        return BIZ[k]
    b = (ev.get("brief") or {}).get("biz")
    if b:
        return b
    with H.lock:
        d = (H.D.get("descKo") or {}).get(k) or ""
    # 사이트 설명은 「TOTO. 위생도기·…」처럼 회사 이름으로 시작하는 것이 있다 — 이름만 덩그러니 나왔다(10/30).
    # 이름이 아닌 첫 마디를 쓴다. 쓸 게 없으면 비운다(답장에서 AI 가 채운다).
    names = {ev.get("name", ""), ev.get("orig", ""), ev.get("code", "")}
    parts = [x.strip() for x in re.split(r"[.。]", d or "") if x.strip()]
    parts = [x for x in parts if len(x) >= 6 and x not in names and not any(n and x.startswith(n) and len(x) <= len(n) + 4 for n in names)]
    return parts[0][:40] if parts else ""


def biz_loop():
    """알림에 쓸 '무엇을 파는지 한 줄'을 미리 만들어 둔다 — 시총 큰 순, 30분마다 600곳씩(앨범을 늦추지 않게)."""
    time.sleep(180)
    while True:
        with H.lock:
            desc, dko = dict(H.D.get("desc") or {}), dict(H.D.get("descKo") or {})
            caps = {k: max((r[11] or 0) for r in rs) for k, rs in H.rows_by_key.items() if rs}
            names = {k: rs[-1][2] or rs[-1][7] for k, rs in H.rows_by_key.items() if rs}
        keys = [k for k in sorted(set(desc) | set(dko), key=lambda k: -caps.get(k, 0)) if k not in BIZ][:600]
        for i in range(0, len(keys), 30):
            items = {k: f"{names.get(k, k)} | {(dko.get(k) or desc.get(k) or '')[:300]}" for k in keys[i:i + 30]}
            got = brief.biz_lines(items)
            if got:
                BIZ.update(got)
                BIZ_PATH.write_text(json.dumps(BIZ, ensure_ascii=False))
        if keys:
            log("비즈니스 한 줄", len(BIZ), "곳")
        time.sleep(1800)


def caption(ev):
    """회원님이 정한 그대로(10/30):
    🇯🇵 재팬 팃슈엔지니어링 (7774) / 3Q26 실적
    (빈 줄) 💰 시총 : 2,772억 원
    (빈 줄) 🕒실적 발표시간 : 2026.10.30 / 21:30
    (빈 줄) 🔥 숫자 : 매출 +6.0%  / 영업이익 -1.4% / 이익률 13.2%
    회사 이름에 원문 링크를 건다(줄을 늘리지 않으려고)."""
    e = html.escape
    name = f"{e(ev['name'])} ({e(ev['code'])})"
    if ev.get("url"):
        name = f"<a href=\"{e(ev['url'])}\">{name}</a>"
    pre, mon = ev.get("preInfo"), ev.get("mon")
    tail = (f" / {e(pre['what'])}" if pre else f" / {int(mon['period'][5:7])}월 월 매출" if mon
            else f" / {e(ev['point'][0])} 실적" if ev.get("point") else " / 실적 발표")
    head = f"{FLAG[ev['mkt']]} <b>{name}</b>" + tail
    try:                                          # 늦게 잡은 것은 늦었다고 적는다(놓쳤다 메운 것)
        late = (datetime.now(timezone.utc) - datetime.fromisoformat(ev["published"])).total_seconds() / 60
        if late > 30 and not ev.get("replay"):
            head += f" <i>(⏱ {int(late)}분 늦게 잡음)</i>"
    except (KeyError, ValueError):
        pass
    lines = [head]
    bz = biz_of(ev)
    if bz:                                        # 회원님(10/30): 무엇을 파는지 — 시총 위에, 구체적으로
        lines += ["", "🏢 비즈니스", f"• {e(bz)}"]
        ev["bizInCap"] = True
    cw = cap_won(ev)
    if cw:
        lines += ["", f"💰 <b><u>시총 : {cw}</u></b>"]          # 회원님(10/30): 굵게 + 밑줄
    if ev.get("kstTime"):
        lines += ["", f"🕒 {'공시' if pre or mon else '실적발표'} 시간 : {ev['kstDate'].replace('-', '.')} / {ev['kstTime']}"]
    if pre:                                       # 결산단신 전 실적 속보·예상 수정 — 직전 회사 예상 → 이번 숫자
        rs = []
        for r in pre["rows"]:
            t = f"{r['n']} {oku(r['prev'])} → {oku(r['now'])}" if r.get("prev") else f"{r['n']} {oku(r['now'])}"
            if r.get("prev") and r["prev"] > 0:
                t += f" ({pct((r['now'] / r['prev'] - 1) * 100)})"
            rs.append(t)
        lines += ["", f"🔥 {e(pre['period'])}" + (" · 직전 회사 예상 대비" if any(r.get('prev') for r in pre['rows']) else "")]
        lines += [e(x) for x in rs]
        gl = guide_line(ev)
        if gl:
            lines += ["", gl]
        out = "\n".join(lines)
        return out if len(out) <= 1024 else out[:1000] + "…"
    if mon:
        lines += ["", f"🔥 {e(mon['base'] + ' ' if mon['base'] else '')}매출 YoY : <b>{pct(mon['yoy'])}</b>"
                      + (f" ({e(mon['other'])})" if mon.get("other") else "")]
        return "\n".join(lines)
    ml = mon_line(ev) if ev["mkt"] == "jp" else ""
    if ml:
        lines += ["", ml]
    p = ev.get("point")
    key = []
    if p:
        rev, opi = p[1], p[2]
        if ev.get("yoy") is not None:
            key.append(f"매출 {pct(ev['yoy'])}")
        if ev.get("opiYoy") is not None:
            key.append(f"영업이익 {pct(ev['opiYoy'])}")
        elif ev.get("turn"):
            key.append(ev["turn"])
        if opi is not None and rev:
            key.append(f"이익률 {opi / rev * 100:.1f}%")
    if key:
        lines += ["", "🔥 숫자 : " + e(" / ".join(key))]
    gl = guide_line(ev)
    if gl:
        lines += ["", gl + (" 🚀" if guide_boom(ev) else "")]
    if not p and ev.get("title"):
        lines += ["", e(ev["title"][:160])]
    out = "\n".join(lines)
    return out if len(out) <= 1024 else out[:1000] + "…"


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
                # 켤 때는 앞 몇 쪽을 넘겨 본다 — 꺼져 있던 사이 올라온 8-K 가 최신 40건 밖으로 밀렸을 수 있다.
                xml = "".join(http_get(FEED.format(form=form).replace("start=0&count=40", f"start={st}&count=100"), timeout=20)
                              for st in (range(0, 600, 100) if first else [None])) if first else http_get(FEED.format(form=form), timeout=15)
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
    rows = {p[0]: p for p in seg["pts"]}
    names = seg["names"]
    out = {}
    # 짝짓기 ① 같은 분기 값이 사이트 막대와 원 단위까지 같은 것 — 전년 같은 분기만 보던 시절에는 그 분기가
    # 사이트에서 비면(일본은 2024년부터 3·4분기 이력이 빈다) 통째로 못 맞췄다(9830, 10/30). 겹치는 분기 전부를 본다.
    lab = lambda e: f"{(int(e[5:7]) + 2) // 3}Q{e[2:4]}"     # noqa: E731
    for e, row in q.items():
        site = rows.get(lab(e)) if e != end else None
        if not site:
            continue
        for n, v in row.items():
            for i, sn in enumerate(names):
                sv = site[i + 1]
                if sn not in out and n in cur and sv and v is not None and abs(v - sv) <= max(1, abs(sv) * 0.005):
                    out[sn] = cur[n]
    # ② 이름이 글자 그대로 같은 것(공백·전각만 무시)
    norm = lambda x: re.sub(r"\s|　", "", str(x))           # noqa: E731
    for n, v in cur.items():
        for sn in names:
            if sn not in out and norm(n) == norm(sn):
                out[sn] = v
    now = [n for i, n in enumerate(names) if rows[seg["pts"][-1][0]][i + 1] is not None]
    if len(out) < max(2, len(now) - 0):
        log("XBRL 부문 짝 못 맞춤", key, list(cur)[:5], names[:5])
        return None
    log("XBRL 부문 이번 분기", key, label, len(out), "개")
    return {"label": label, "names": names, "vals": [out.get(n) for n in names]}


def guide_from_pdf(code, name, url, day):
    """결산단신 PDF 첫 두 쪽 -> 연간 예상 -> 가이던스 변천에 적는다. 직전 예상의 절반~두 배 밖이면 안 쓴다(단위 오독)."""
    try:
        text = pdftext.extract_text(http_get(url, ua=BROWSER_UA, timeout=30, binary=True), 2)
    except Exception as e:                                       # noqa: BLE001
        log("결산단신 PDF 실패", code, repr(e)[:100])
        return None
    g = brief.fy_from_text(name, text)
    if not g or not g.get("rev"):
        log("연간 예상 판독 없음", code)
        return None
    st = stage_of(g["fy_end"] + "-28", day)
    before = GD.get(code).get(g["fy_end"], {})
    prev = next((before[s] for s in reversed(STAGES[:STAGES.index(st)]) if s in before), None) if st else None
    if prev and prev[0] and not 0.5 <= g["rev"] / prev[0] <= 2:
        log("연간 예상 판독 버림(단위?)", code, g)
        return None
    out = GD.add(code, {"end": g["fy_end"] + "-28", "rev": g.get("rev"), "opi": g.get("op"), "ni": g.get("eps")}, day)
    log("연간 예상 AI 판독", code, g["fy_end"], st, "매출", g.get("rev"))
    return out


def guide_audit(days=45, limit=400):
    """매일 — 최근 실적을 낸 일본 회사 중 그 분기 가이던스가 빈 곳을 원본(JPX 보관소 결산단신)으로 메운다.
    XBRL 이 먼저, 안 되면 PDF 첫 쪽을 AI 가 읽는다. 회원님(10/30): 일본 가이던스는 반드시 차트에."""
    today = datetime.now(JST).date()
    with H.lock:
        rows = [(k, rs) for k, rs in H.rows_by_key.items() if k.startswith("jp:")]
    todo = []
    for k, rs in rows:
        for r in rs:
            try:
                d = date.fromisoformat(r[0])
            except ValueError:
                continue
            m = re.match(r"(\d+)월", r[3] or "")
            if not m or not (today - timedelta(days=days) <= d <= today):
                continue
            fm = int(m.group(1))
            # 그 결산단신이 싣는 연간 예상 = 발표일 다음에 오는 결산월(분기면 이번 회계연도, 본결산이면 다음 회계연도)
            fy = f"{d.year if d.month < fm else d.year + 1}-{fm:02d}"
            st = stage_of(fy + "-28", r[0])
            if st and st not in GD.get(k[3:]).get(fy, {}):
                todo.append((k[3:], r[0], r[2] or r[7], max((x[11] or 0) for x in rs)))
            break
    todo = sorted(set(todo), key=lambda t: -t[3])[:limit]
    log("가이던스 빈칸 점검", len(todo), "곳")
    got = 0
    for code, day, name, _ in todo:
        try:
            with _yano["lock"]:
                time.sleep(0.4)
            j = json.loads(http_get(f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json?limit=30", ua=BROWSER_UA, timeout=30))
            items = [it.get("Tdnet", it) for it in j.get("items") or []]
            hit = next((it for it in items if (it.get("pubdate") or "")[:10] == day and fj.TANSHIN.search(it.get("title") or "")
                        and not fj.NOT_TANSHIN.search(it.get("title") or "")), None)
            if not hit:
                continue
            res = None
            if hit.get("url_xbrl"):
                fn = hit["url_xbrl"].rsplit("/", 1)[-1]
                for base in ("https://www.release.tdnet.info/inbs/", f"https://www2.jpx.co.jp/disc/{code}0/"):
                    try:
                        vals = fj.read_summary(http_get(base + fn, ua=BROWSER_UA, timeout=40, binary=True))
                        res = GD.add(code, fj.forecast_from(vals or {}), day)
                        break
                    except Exception:                            # noqa: BLE001
                        continue
            if not res and hit.get("document_url"):
                pn = hit["document_url"].rsplit("/", 1)[-1]
                for base in ("https://www.release.tdnet.info/inbs/", f"https://www2.jpx.co.jp/disc/{code}0/"):
                    res = guide_from_pdf(code, name, base + pn, day)
                    if res:
                        break
            got += 1 if res else 0
        except Exception as e:                                   # noqa: BLE001
            log("가이던스 점검 실패", code, repr(e)[:100])
    log("가이던스 빈칸 점검 끝", got, "/", len(todo), "곳 메움")


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
            if not ev.get("guide") and row.get("pdf"):          # XBRL 로 못 뽑았다 — 첫 쪽 표를 AI 가 읽는다(후쿠다전자 10/30)
                ev["guide"] = guide_from_pdf(code, ev["name"], "https://www.release.tdnet.info/inbs/" + row["pdf"], day)
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


# 결산단신보다 먼저 실적을 알리는 공시 — 다카라토미 10/29 「上期業績予想と実績値との差異」(결산단신은 11/10).
PRE_RE = re.compile(r"業績予想.{0,12}修正|予想.{0,6}実績.{0,6}差異|実績.{0,8}差異|決算速報|業績速報|業績.{0,4}(概要|概況)に関する")
PRE_SKIP = re.compile(r"決算短信|訂正|取消|中止|延期|配当予想の修正に関するお知らせ$")
MON_SENT = set()


def jp_pre_process(row, day, replay=False):
    code = row["code"]
    if H.skip("jp", code, row["name"]):
        return
    url = "https://www.release.tdnet.info/inbs/" + row["pdf"]
    ev = base_event("jp", code, row["name"], f"{day} {row['time']}", JST, "TDnet 적시공시", url, row["title"])
    ev["replay"] = replay
    try:
        text = pdftext.extract_text(http_get(url, ua=BROWSER_UA, timeout=30, binary=True), 6)
    except Exception as e:                                       # noqa: BLE001
        log("적시공시 PDF 실패", code, repr(e)[:120])
        return
    p = brief.pre_read(ev["name"], row["title"], text)
    if not p:
        log("적시공시 숫자 없음", code, row["title"][:40])
        return
    ev["preInfo"] = p
    if p.get("fy"):                                              # 연간 예상이 바뀌었으면 가이던스 변천에도 적는다
        g = p["fy"]
        ev["guide"] = GD.add(code, {"end": g["fy_end"] + "-28", "rev": g.get("rev"), "opi": g.get("op"), "ni": g.get("eps")}, day)
    ev["_text"] = text
    log("적시공시", code, p["what"], p["period"])
    finish(ev, None)


def jp_mon_process(row, day, replay=False):
    """월차 — 표를 읽어 최근 달의 전년비. 기존점 우선. +10% 이상이면 알림(회원님 10/30)."""
    code = row["code"]
    if H.skip("jp", code, row["name"]):
        return
    url = "https://www.release.tdnet.info/inbs/" + row["pdf"]
    try:
        got = montable.read(http_get(url, ua=BROWSER_UA, timeout=30, binary=True), day, title=row["title"])
    except Exception as e:                                       # noqa: BLE001
        log("월차 PDF 실패", code, repr(e)[:120])
        return
    rows = [r for r in (got or {}).get("rows") or []
            if r.get("yoy") is not None and (not r.get("metric") or re.search(r"売上|営業収益|営業収入|取扱高|販売高|月商|前年|昨対", r["metric"]))]
    if not rows:
        log("월차 표 못 읽음", code, row["title"][:40])
        return
    last = max(r["period"] for r in rows)
    now = [r for r in rows if r["period"] == last]
    lab = lambda r: (r.get("metric") or "") + " " + (got.get("yoy_label") or "")     # noqa: E731
    pick = next((r for r in now if "既存" in lab(r)), None) or next((r for r in now if "全店" in lab(r)), None) or now[0]
    base = "기존점" if "既存" in lab(pick) else "전점" if "全店" in lab(pick) else ""
    other = next((r for r in now if r is not pick and "全店" in lab(r)), None)
    key = (code, last, base)
    if key in MON_SENT and not replay:
        return
    MON_SENT.add(key)
    ev = base_event("jp", code, row["name"], f"{day} {row['time']}", JST, "TDnet 월차", url, row["title"])
    ev["replay"] = replay
    series = [[r["period"], r.get("rev"), r["yoy"], 0] for r in rows if lab(r) == lab(pick)]
    ev["mon"] = {"period": last, "base": base, "yoy": pick["yoy"] - 100, "rows": series,
                 "other": f"전점 {pct(other['yoy'] - 100)}" if other else ""}
    send, why = tg_pick(ev, [])
    if send and ev["mon"]["yoy"] < 10 and not replay:
        send, why = False, f"월차 {pct(ev['mon']['yoy'])} (+10% 미만)"
    log(f"월차 {code} {ev['name']} {last} {base} {pct(ev['mon']['yoy'])} · 텔레그램 {'보냄' if send or replay else '안 보냄'} ({why})")
    if not (send or replay):
        return
    pngs = []
    if H.html:
        try:
            pngs = shot.render(dict({k: v for k, v in ev.items() if not k.startswith("_")}, onlySeg="mon")) or []
        except Exception:                                        # noqa: BLE001
            log("월차 그림 실패", code, traceback.format_exc(limit=2))
    TG.send_album(("🧪 <i>지난 공시 다시 보내기(시험)</i>\n" if replay else "") + caption(ev), pngs)


def jp_watch():
    seen = set()
    first = True
    while True:
        t0 = time.time()
        day = datetime.now(JST).strftime("%Y-%m-%d")
        try:
            # **첫 쪽만 보면 15시에 놓친다.** 결산 공시가 15:00 에 수백 건 한꺼번에 올라와 한 쪽(100건) 밖으로 밀린다 —
            # TDK(6762, 10/30)가 그렇게 빠졌다. 새 줄이 하나도 없는 쪽이 나올 때까지 넘겨 본다(켤 때는 그날 전부).
            rows_all = []
            for page in range(1, 30):
                got = fj.listing(day, page)
                if not got:
                    break
                fresh = [r for r in got if (day, r["code"], r["time"], r["title"]) not in seen]
                rows_all += fresh
                if not fresh and not first:
                    break
            for r in rows_all:
                k = (day, r["code"], r["time"], r["title"])
                if k in seen:
                    continue
                seen.add(k)
                if first and (not r["time"] or stale(datetime.strptime(f"{day} {r['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=JST))):
                    continue
                t = r["title"]
                if fj.TANSHIN.search(t) and not fj.NOT_TANSHIN.search(t) and r["zip"]:
                    POOL.submit(safe, jp_process, r, day)
                elif PRE_RE.search(t) and not PRE_SKIP.search(t) and r.get("pdf"):
                    POOL.submit(safe, jp_pre_process, r, day)         # 결산단신 전 실적 속보·예상 수정(회원님 10/30)
                elif jt.is_monthly(t) and r.get("pdf"):
                    POOL.submit(safe, jp_mon_process, r, day)         # 월차
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


ALERTS = {"n": 0, "lock": threading.Lock()}             # 지금 처리 중인 실적 알림 수 — 끄기 전에 다 보내고 끈다


def safe(fn, *a):
    alert = fn.__name__ in ("us_process", "jp_process", "jp_pre_process", "jp_mon_process", "hk_process")
    if alert:
        with ALERTS["lock"]:
            ALERTS["n"] += 1
    try:
        fn(*a)
    except Exception:                                            # noqa: BLE001
        log("작업 실패", fn.__name__, traceback.format_exc(limit=3))
    finally:
        if alert:
            with ALERTS["lock"]:
                ALERTS["n"] -= 1


def on_term(*_):
    """새 판으로 바꿀 때(SIGTERM) — 처리 중인 알림을 다 보내고, 쌓던 것을 적어 두고 끈다(회원님 10/30: "교란 없게").
    새 서버는 켜지자마자 지난 20분 공시를 다시 보고(보내기 전에 끊긴 것은 다시 보낸다), 하던 큰 일은 이어 간다."""
    log("끄기 요청 — 처리 중 알림", ALERTS["n"], "건을 마치고 끈다")
    t0 = time.time()
    while ALERTS["n"] > 0 and time.time() - t0 < 40:
        time.sleep(0.5)
    try:
        with JH_LOCK:
            JH_PATH.write_text(json.dumps(JH, ensure_ascii=False))
    except Exception:                                            # noqa: BLE001
        pass
    os._exit(0)


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
        f = re.match(r"segfill\s+([A-Z][A-Z.\-,]*)$", ln.strip())    # 종목을 콕 집어 — segfill INTC,AMD
        if f:
            segfill(50, {"us:" + t for t in f.group(1).split(",") if t})
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
        f = re.match(r"jpday$", ln.strip())                    # 오늘 일본 결산단신 중 아직 안 보낸 것 전부 — 놓친 것 메우기
        if f:
            day = datetime.now(JST).strftime("%Y-%m-%d")
            rows = []
            for page in range(1, 40):
                got = fj.listing(day, page)
                if not got:
                    break
                rows += got
            hit = [r for r in rows if fj.TANSHIN.search(r["title"]) and not fj.NOT_TANSHIN.search(r["title"]) and r["zip"]]
            log("오늘 일본 결산단신 다시 훑기", len(hit), "건")
            for r in hit:
                POOL.submit(safe, jp_process, r, day)
            continue
        f = re.match(r"jp(pre|mon)\s+(\w+)", ln.strip())         # 시험 — 최근 닷새 안의 그 종목 적시공시·월차를 다시
        if f:
            for back in range(0, 6 if f.group(1) == "pre" else 32):
                day = (datetime.now(JST) - timedelta(days=back)).strftime("%Y-%m-%d")
                rows = []
                for page in range(1, 12):
                    got = fj.listing(day, page)
                    if not got:
                        break
                    rows += got
                fn = (lambda t: PRE_RE.search(t) and not PRE_SKIP.search(t)) if f.group(1) == "pre" else jt.is_monthly
                hit = [r for r in rows if r["code"] == f.group(2) and fn(r["title"]) and r.get("pdf")]
                if hit:
                    (jp_pre_process if f.group(1) == "pre" else jp_mon_process)(hit[0], day, True)
                    break
            continue
        f = re.match(r"serfill\s+(\d+)", ln.strip())
        if f:
            serfill(int(f.group(1)))
            continue
        f = re.match(r"probe\s+(\S+)", ln.strip())          # 서버에서만 닿는 주소를 떠본다(로그에 상태·앞머리)
        if f:
            try:
                b = http_get(f.group(1), ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", timeout=30, binary=True)
                log("떠보기", f.group(1), len(b or b""), repr((b or b"")[:300]))
            except Exception as e:                               # noqa: BLE001
                log("떠보기 실패", f.group(1), repr(e)[:200])
            continue
        f = re.match(r"jphist\s+(\d+)", ln.strip())
        if f:
            threading.Thread(target=jphist, args=(int(f.group(1)),), daemon=True, name="jphist").start()   # 몇 시간짜리 — 다음 줄을 막지 않게
            continue
        if ln.strip() == "guideaudit":
            guide_audit()
            continue
        f = re.match(r"guideus\s+(\d+)", ln.strip())
        if f:
            guidefill_us(int(f.group(1)))
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
                    threading.Thread(target=safe, args=(replay_run, lines[1:]), daemon=True).start()   # 긴 일이 다음 요청을 막지 않게
                last = rid
        except Exception as e:                                   # noqa: BLE001
            if "404" not in str(e):
                log("다시 보내기:", e)
            if last is None:
                last = ""
        time.sleep(90)


SEGKO_PATH = DATA / "seg_ko_extra.json"
try:
    SEGKO = json.loads(SEGKO_PATH.read_text())
except (OSError, ValueError):
    SEGKO = {}


def segko_loop():
    """사이트 부문 이름 중 아직 일·중 원문으로 남은 것 — AI 로 옮겨 /api/segko 로 내준다(빌드가 받아 쓴다)."""
    cjk = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
    time.sleep(120)
    while True:
        hangul = re.compile(r"[\uac00-\ud7a3]")
        with H.lock:
            # 일·중 원문, 그리고 일본·홍콩 회사의 영문 부문 이름(Real Estate Lease Business)도 한국어로.
            # 미국 회사의 영어 부문 이름도 한국어로(회원님 10/30: 인텔 'Client Computing' 이 영어로 남았다).
            names = {n for k, v in (H.D.get("seg") or {}).items() for n in v.get("names") or []
                     if n not in SEGKO and not hangul.search(n)}
        names = sorted(names, key=lambda n: (not cjk.search(n), n))[:1600]
        for i in range(0, len(names), 40):
            got = brief.translate_names(names[i:i + 40])
            if got:
                SEGKO.update(got)
                SEGKO_PATH.write_text(json.dumps(SEGKO, ensure_ascii=False))
                log("부문 이름 번역", len(got), "개")
        time.sleep(600)


def fill_loop():
    """끊겨도 저절로 이어 간다 — 재배포로 서버가 다시 켜지면 하던 큰 일(옛 결산단신)이 멈췄고, 이번 분기 부문
    메우기도 사람이 다시 걸어야 했다(인텔 3Q26 이 그래서 빠졌다, 10/30). 켜진 뒤 2분, 그다음 두 시간마다."""
    time.sleep(120)
    while True:
        if JH.get("want") and len(JH["done"]) < JH["want"] and not any(t.name == "jphist" for t in threading.enumerate()):
            threading.Thread(target=safe, args=(jphist, JH["want"]), daemon=True, name="jphist").start()
        safe(segfill, 500)
        safe(segfill_jp, 300)
        safe(guide_audit)
        time.sleep(2 * 3600)


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
        elif path == "/api/jpsegraw":                            # 옛 결산단신 부문 누계 — numbers 수집기가 segments_jp.json raw 에 합친다
            self.head()
            with JH_LOCK:
                self.wfile.write(json.dumps(JH["seg"], ensure_ascii=False).encode())
        elif path == "/api/co":                                  # 상세창을 열 때 사이트가 바로 묻는다 — 빌드를 기다리지 않게(회원님 10/30)
            key = (urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("key") or [""])[0]
            mkt, _, code = key.partition(":")
            out = {"guide": {}, "fin": []}
            try:
                if mkt in ("jp", "us"):
                    out["guide"] = {fy: {s: v for s, v in st.items() if s != "실제"}
                                    for fy, st in (GD if mkt == "jp" else GU).get(code).items()}
                if mkt == "jp":
                    out["fin"] = jp_old_points(key)
            except Exception as e:                               # noqa: BLE001
                out["err"] = repr(e)[:100]
            self.head()
            self.wfile.write(json.dumps(out, ensure_ascii=False).encode())
        elif path == "/api/jpfinraw":                            # 옛 결산단신 실적 누계 — 빌드가 1Q18 부터의 분기로 되돌려 잇는다
            self.head()
            with JH_LOCK:
                self.wfile.write(json.dumps(JH["fin"], ensure_ascii=False).encode())
        elif path == "/api/guide":                               # 빌드가 받아 사이트 상세창의 가이던스 변천으로
            self.head()
            self.wfile.write(json.dumps({"jp": GD.own, "us": GU.own}, ensure_ascii=False).encode())
        elif path == "/api/segko":
            self.head()
            self.wfile.write(json.dumps(SEGKO, ensure_ascii=False).encode())
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
    import signal
    signal.signal(signal.SIGTERM, on_term)
    log("시작 — 판", os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:7], "· 텔레그램", "켜짐" if TG_TOKEN else "꺼짐(TG_BOT_TOKEN 없음)", "· 구독", len(TG.subs))
    H.refresh()
    shot.start(lambda: H.html)
    for fn in (TG.poll, us_watch, jp_watch, hk_watch, history_loop, llm_selftest, replay_loop, segko_loop, fill_loop, biz_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
