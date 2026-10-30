"""텔레그램 그림을 **사이트 화면 그대로** 찍는다 — 회원님(10/29): "어닝사무라이 형식을 그대로 베껴서."

matplotlib 으로 비슷하게 그리면 늘 어딘가 다르다. 그래서 사이트(index.html)를 머리 없는
크로미움에 한 번 띄워 두고, 사이트의 함수(fcstTable · finChart · segChart)를 그대로 불러
만든 조각을 찍는다. 새 분기는 사이트가 실시간 서버에서 받을 때 쓰는 rtApply 로 얹는다.
사이트에 없는 가이던스 변천 그림만 여기서 같은 class(fb·fx·vn…)로 그린다.

플레이라이트는 한 스레드에서만 다뤄야 해서 일꾼 스레드 하나가 줄을 받아 처리한다.
"""
import os
import queue
import threading
import traceback

JOBS = queue.Queue()
_page = {"html": None, "ver": 0}

GUIDE_JS = r"""
// 사이트(build.py)에 같은 함수가 있다 — 옛 빌드를 띄웠을 때만 이것을 쓴다.
if (!window.tgGuide) window.tgGuide = function (gh, title, mkt) {
  const ST = ['최초', '1Q', '2Q', '3Q'];
  const fys = Object.keys(gh).sort().slice(-5);
  let last = null;
  fys.forEach(f => ST.forEach(s => { if (gh[f][s]) last = f + s; }));
  const one = (k, name) => {
    const cols = [];
    fys.forEach((f, gi) => {
      let st = ST;
      if (gi === fys.length - 1 && !gh[f]['실제']) {
        const got = ST.filter(s => gh[f][s]);
        if (got.length) st = ST.slice(0, ST.indexOf(got[got.length - 1]) + 1);
      }
      st.concat(gh[f]['실제'] ? ['실제'] : []).forEach(s => cols.push({ f, s, v: gh[f][s] ? gh[f][s][k] : null }));
      cols.push(null);
    });
    cols.pop();
    const raw = cols.filter(c => c && c.v != null).map(c => c.v);
    if (cols.filter(c => c && c.v != null && c.s !== '실제').length < 2) return '';   // 실제만 있는 칸은 안 그린다
    // 일본은 억엔 그대로(회원님이 본 모양), 미국은 사이트 차트와 같은 눈금(bil USD…), EPS 는 달러 그대로.
    const U = k === 2 && mkt === 'us' ? { div: 1, ko: 'USD/주' }
            : mkt === 'jp' ? { div: 1e8, ko: '억엔' } : unitFor(Math.max(...raw.map(Math.abs)), 'USD');
    const vals = raw.map(v => v / U.div);
    const mx = niceMax(Math.max(...vals, 1));
    const W = Math.max(880, cols.length * 58), L = 66, R = 20, B = 56, T = 30, H = 300, BASE = H - B;
    const step = (W - L - R) / cols.length;
    const cx = i => L + step * i + step / 2, y = v => BASE - (BASE - T) * v / mx;
    let s = [0, mx / 2, mx].map(v => '<line class="fz" x1="' + L + '" y1="' + y(v).toFixed(1) + '" x2="' + (W - R) +
      '" y2="' + y(v).toFixed(1) + '"/><text class="fx" x="' + (L - 8) + '" y="' + (y(v) + 4).toFixed(1) +
      '" text-anchor="end">' + fmtN(v) + '</text>').join('');
    const groups = {};
    cols.forEach((c, i) => {
      if (!c) return;
      (groups[c.f] = groups[c.f] || []).push(i);
      // 위 줄: 그 예상을 낸 결산단신이 다룬 **실제 분기**(달력, 예 3Q26). 아래 줄: 그 회사 회계연도 기준(예 FY2Q27).
      // 회원님(10/30): "회계연도 말고 실제 분기로, 그 밑에 FY2Q27 처럼."
      const fy = +c.f.slice(0, 4), fm = +c.f.slice(5, 7);
      const back = { '최초': 12, '1Q': 9, '2Q': 6, '3Q': 3, '실제': 0 }[c.s];
      let em = fm - back, ey = fy;
      while (em <= 0) { em += 12; ey -= 1; }
      const cal = Math.ceil(em / 3) + 'Q' + String(ey % 100).padStart(2, '0');
      const fyl = c.s === '최초' ? 'FY' + String(fy % 100).padStart(2, '0') + ' 최초'
                : c.s === '실제' ? 'FY' + String(fy % 100).padStart(2, '0') + ' 실제'
                : 'FY' + c.s + String(fy % 100).padStart(2, '0');
      const lab = '<text class="fx" x="' + cx(i).toFixed(1) + '" y="' + (BASE + 16) + '" text-anchor="middle"' +
        ((c.f + c.s) === last ? ' style="fill:#fff;font-weight:800"' : '') + '>' + cal + '</text>' +
        '<text class="fx" x="' + cx(i).toFixed(1) + '" y="' + (BASE + 31) + '" text-anchor="middle" style="font-size:10px;opacity:.75">' + fyl + '</text>';
      if (c.v == null) { s += lab; return; }
      const v = c.v / U.div, now = (c.f + c.s) === last;
      const fill = c.s === '실제' ? '#d7dde3' : now ? '#FFCB05' : '#5B9BD5';
      s += '<rect x="' + (cx(i) - step * 0.36).toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + (step * 0.72).toFixed(1) +
        '" height="' + Math.max(BASE - y(v), 1).toFixed(1) + '" style="fill:' + fill + '"/>' +
        '<text class="vn rev" x="' + cx(i).toFixed(1) + '" y="' + (y(v) - 6).toFixed(1) + '" text-anchor="middle" style="font-size:11px' +
        (now ? ';fill:#FFCB05;font-weight:800' : '') + '">' + fmtN(v) + '</text>' + lab;
    });
    // 회계연도 묶음 사이는 빈 칸으로만 가른다 — 막대마다 FY 표기가 붙어 있다.
    return '<div class="finhead sub">' + name + ' <span class="dim">(단위: ' + U.ko + ' · 회사 연간 전망)</span></div>' +
      '<div class="finbox"><svg viewBox="0 0 ' + W + ' ' + H + '" class="finsvg">' + s + '</svg></div>';
  };
  const a = one(0, '매출 가이던스'), b = one(1, '영업이익 가이던스');
  const c = mkt === 'us' ? one(2, 'EPS 가이던스') : b ? '' : one(2, '순이익 가이던스');
  if (!a && !b && !c) return '';
  return '<div class="finwrap"><div class="finhead">' + title + ' 가이던스 변천' +
    '<span class="dim"><b style="color:#5B9BD5">■</b> 가이던스 <b style="color:#d7dde3">■</b> 실제 <b style="color:#FFCB05">■</b> 최신</span></div>' +
    a + b + c + '</div>';
};
window.tgBuild = function (ev, gh) {
  const m = ev.mkt, code = ev.code, key = m + ':' + code;
  if (ev.finAll) D.fin[key] = ev.finAll;            // 사이트에 이력이 없던 회사 — 서버가 즉석에서 받은 것
  try { rtApply(ev); } catch (e) {}
  const f = D.fin[key], sg = D.seg[key];
  const title = '<span style="color:#9cc6ee">[' + code + ']</span> ' + (ev.name || code);
  const out = [];
  let t = '';
  try { t = fcstTable(m, code); } catch (e) {}
  if (t) out.push('<div class="finhead">' + title + ' 연간 실적 · 컨센서스</div>' + t);
  if (f && (f.points || []).length >= 2 && (f.freq === 'Q' || f.freq === 'H'))
    out.push(finChart(f).replace(/(<div class="finhead[^"]*">)/, '$1' + title + ' '));
  if (gh) { const g = tgGuide(gh, title, m); if (g) out.push(g); }
  if (sg) {
    const MC = { jp: 'JPY', hk: 'HKD' };
    const s = segChart(sg, (f && f.cur) || MC[m] || 'USD');
    if (s) out.push(s.replace(/(<div class="finhead[^"]*">)/, '$1' + title + ' '));
  }
  // 월차를 내는 일본 회사 — 사이트 월매출 그림(mnChart) 그대로. 방금 읽은 달(ev.mon.rows)을 얹는다.
  const monPart = () => {
    if (m !== 'jp' || typeof mnChart !== 'function') return '';
    const num = (D.monthlyNum || {})[code] || {};
    const rows = ((ev.mon && ev.mon.rows) || []);
    if (!rows.length && !(num.historyVerified && (num.m || []).length)) return '';
    const by = {};
    (num.historyVerified ? (num.m || []) : []).forEach(r => { by[r[0]] = r; });
    rows.forEach(r => { by[r[0]] = r; });
    const mm = Object.keys(by).sort().map(k => by[k]);
    const base = (ev.mon && ev.mon.base) || num.historyBase || num.base || '';
    return '<div class="finhead">' + title + ' 월 매출 <span class="dim">(月次' + (base ? ' · ' + base : '') +
      ' · 막대 위 숫자 = YoY)</span></div><div class="mndetail"><div class="mnmodalchart">' + mnChart(mm) + '</div></div>';
  };
  if (!ev.onlySeg) { const mp = monPart(); if (mp) out.push(mp); }
  if (ev.onlySeg === 'mon') {
    out.length = 0;
    const mp = monPart(); if (mp) out.push(mp);
  } else if (ev.onlySeg === 'segchart') {                  // 답장용 — 이번 분기를 얹은 사업부 그림만
    out.length = 0;
    if (D.seg[key]) {
      const s2 = segChart(D.seg[key], (f && f.cur) || ({ jp: 'JPY', hk: 'HKD' })[m] || 'USD');
      if (s2) out.push(s2.replace(/(<div class="finhead[^"]*">)/, '$1' + title + ' '));
    }
  } else if (ev.onlySeg === 'guide') {                     // 답장용 — 가이던스 변천만(미국: 보도자료를 읽은 뒤라 앨범보다 늦다)
    out.length = 0;
    if (gh) { const g = tgGuide(gh, title, m); if (g) out.push(g); }
  } else if (ev.onlySeg) {                          // 답장용 — 원문 AI 판독 표
    out.length = 0;
    const B = ev.brief || {}, parts = [];
    if (/fin/.test(ev.onlySeg) && B.fin) {
      const F = B.fin, pc = (a, b) => (a == null || !b) ? null : (a / b - 1) * 100;
      const row = (n, v) => !v || v[0] == null ? '' : '<tr><th>' + n + '</th><td>' + (+v[0]).toLocaleString() +
        '</td><td>' + (v[1] == null ? '—' : (+v[1]).toLocaleString()) + '</td><td class="' +
        ((pc(v[0], v[1]) || 0) > 0 ? 'up' : (pc(v[0], v[1]) || 0) < 0 ? 'dn' : '') + '">' +
        (pc(v[0], v[1]) == null ? '—' : (pc(v[0], v[1]) > 0 ? '+' : '') + pc(v[0], v[1]).toFixed(1) + '%') + '</td></tr>';
      parts.push('<div class="finhead">' + title + ' 이번 실적' + (F.period ? ' · ' + esc(F.period) : '') + '</div>' +
        '<div class="rtseg"><table><thead><tr><th style="text-align:left">' + esc(F.unit || '') + '</th><th>이번</th><th>전년 같은 기간</th><th>전년비</th></tr></thead><tbody>' +
        row('매출', F.rev) + row('영업이익', F.op) + row('순이익', F.ni) +
        '</tbody></table><div class="rtnote">회사 공시 원문의 손익 표를 AI 가 읽은 값 · ' + esc(ev.src || '') + '</div></div>');
    }
    if (/seg/.test(ev.onlySeg)) {
      try {
        const r = rtSegBlock(key);
        if (r) parts.push('<div class="finhead" style="margin-top:18px">' + title + ' 이번 발표 사업부별 매출</div>' + r);
      } catch (e) {}
    }
    if (parts.length) out.push(parts.join(''));
  }
  let host = document.getElementById('tgshot');
  if (!host) {
    host = document.createElement('div');
    host.id = 'tgshot';
    host.style.cssText = 'position:absolute;left:0;top:0;width:960px;z-index:99999;background:#0f1419;';
    document.body.appendChild(host);
  }
  host.innerHTML = out.map((h, i) => '<div class="tgpart" style="padding:22px 26px 14px;background:#0f1419' +
    (/class="(fcbox|rtseg)"/.test(h) ? ';width:fit-content' : '') + '">' + h + '</div>').join('');
  return out.length;
};
"""


def _worker(get_html):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        exe = os.environ.get("CHROMIUM_PATH") or None
        br = pw.chromium.launch(args=["--no-sandbox"], executable_path=exe)
        page, ver = None, -1
        while True:
            job = JOBS.get()
            if job is None:
                return
            ev, gh, box, done = job
            try:
                if page is None or ver != _page["ver"]:
                    if page:
                        page.close()
                    page = br.new_page(viewport={"width": 960, "height": 1200}, device_scale_factor=2)
                    # 사이트가 열리며 실시간 서버에 다시 붙지 않게 막는다.
                    page.route("**/*railway.app/**", lambda r: r.abort())
                    page.set_content(get_html(), wait_until="domcontentloaded", timeout=120000)
                    page.add_script_tag(content=GUIDE_JS)
                    page.wait_for_timeout(800)
                    ver = _page["ver"]
                if ev is None:                                   # 미리 띄워 두기만
                    continue
                n = page.evaluate("([ev, gh]) => tgBuild(ev, gh)", [ev, gh])
                page.add_style_tag(content=".finbox{overflow:visible!important}.finsvg{min-width:0!important;width:100%!important}")
                box["pngs"] = [el.screenshot(type="png") for el in page.query_selector_all("#tgshot .tgpart")][:n]
            except Exception:                                    # noqa: BLE001
                traceback.print_exc(limit=3)
                page = None
            finally:
                done.set()


def start(get_html):
    threading.Thread(target=_worker, args=(get_html,), daemon=True, name="shot").start()


def reload():
    """사이트가 새로 빌드됐다 — 새 페이지를 미리 띄워 둔다(첫 알림이 30초를 기다리지 않게)."""
    _page["ver"] += 1
    JOBS.put((None, None, {}, threading.Event()))


def render(ev, gh=None, timeout=60):
    """-> [png, …] 또는 None(실패 — 부른 쪽이 matplotlib 으로 대신 그린다)."""
    box, done = {}, threading.Event()
    JOBS.put((ev, gh, box, done))
    done.wait(timeout)
    return box.get("pngs")
