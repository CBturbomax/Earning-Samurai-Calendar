/* 맨 위 「신고가 알림판」 — 이번 달 일본 수출에서 5년 신고가를 찍은 품목과
   신고가에 바짝 붙은 품목을 한눈에. 회원님 요청(10/29): "뭐가 제일 수출 신고가인지,
   신고가에 근접했는지 가독성 좋게, 알림처럼. 뭐가 잘되나 한눈에."
   자료는 discover.py 가 만든 data/discovery.json(세계 전체 · HS4·HS6) 그대로다.
   - 큰 품목(월 10억 엔 이상)만 앞에 세운다. 작은 품목은 접어 둔다 — 몇천만 엔짜리가
     신고가라고 맨 위에 서면 정작 볼 것을 가린다.
   - HS6 와 그 상위 HS4 가 둘 다 걸리면 HS4 카드 안에 「세부」로 붙인다(같은 수출을
     두 번 세우지 않는다).
   - 이름은 hs-ko.json(한글) → discovery-companies.json → 영문 원문 순. */
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const BIG = 1e6;                          // 천 엔 단위 → 10억 엔
const eok = v => v == null ? '—' : (v / 1e5 >= 100 ? Math.round(v / 1e5).toLocaleString('ko-KR') : (v / 1e5).toFixed(1));
const pct = v => v == null || !isFinite(v) ? '—' : (v > 0 ? '+' : '') + Math.round(v) + '%';
const cls = v => v > 0 ? 'positive' : v < 0 ? 'negative' : '';

function shortEn(name) {
  const s = String(name || '').split(/[;(]/)[0].trim();
  return s.length > 38 ? s.slice(0, 36) + '…' : s;
}

function months(values) { return Object.keys(values || {}).sort(); }

function stats(item, latest) {
  const vs = item.values || {}, ms = months(vs).filter(m => m <= latest);
  const v = vs[latest];
  const [y, m] = latest.split('-');
  const py = `${+y - 1}-${m}`;
  const prior = ms.filter(x => x < latest).map(x => vs[x]).filter(x => x != null);
  const prevHigh = prior.length ? Math.max(...prior) : null;
  return {
    v,
    yoy: vs[py] > 0 ? (v / vs[py] - 1) * 100 : null,
    overHigh: prevHigh > 0 ? (v / prevHigh - 1) * 100 : null,
    ofHigh: prevHigh > 0 ? v / prevHigh * 100 : null,       // 이번 달이 5년 최고의 몇 %
    spark: ms.slice(-24).map(x => vs[x] ?? 0),
  };
}

function spark(arr, record) {
  if (!arr.length) return '';
  const W = 150, H = 38, max = Math.max(...arr, 1);
  const x = i => (i / Math.max(1, arr.length - 1)) * (W - 6) + 3, y = v => H - 4 - (v / max) * (H - 10);
  const d = arr.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1)).join('');
  const lx = x(arr.length - 1), ly = y(arr[arr.length - 1]);
  return `<svg class="al-spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}" fill="none" stroke="#5B9BD5" stroke-width="1.6" opacity=".75"/>` +
    `<circle cx="${lx}" cy="${ly}" r="3.6" fill="${record ? '#FFCB05' : '#9cc6ee'}"/></svg>`;
}

export function renderAlerts(data, { names = {}, companies = {}, query = '', error = false } = {}) {
  if (!data) return `<div class="al-head"><h2>🔔 수출 신고가 알림</h2></div><p class="al-empty">${error ? '자료를 불러오지 못했습니다. 새로고침해 주세요.' : '불러오는 중…'}</p>`;
  const latest = data.latestMonth, [yy, mm] = latest.split('-');
  const q = query.trim().toLowerCase();
  const name = it => names[it.code] || companies[it.code]?.name || shortEn(it.name);
  const all = (data.items || []).map(it => ({ it, s: stats(it, latest), nm: name(it), co: companies[it.code]?.companies || '' }))
    .filter(x => x.s.v != null)
    .filter(x => !q || [x.nm, x.it.code, x.it.name, x.co].join(' ').toLowerCase().includes(q));
  // HS6 가 상위 HS4 와 같이 걸리면 HS4 카드에 붙인다.
  const four = new Map(all.filter(x => x.it.code.length === 4).map(x => [x.it.code, x]));
  const kids = new Map();
  const tops = all.filter(x => {
    if (x.it.code.length === 6 && four.has(x.it.code.slice(0, 4))) {
      const k = x.it.code.slice(0, 4);
      kids.set(k, [...(kids.get(k) || []), x]);
      return false;
    }
    return true;
  });
  const byV = (a, b) => b.s.v - a.s.v;
  const rec = tops.filter(x => x.it.record).sort(byV);
  const near = tops.filter(x => !x.it.record).sort((a, b) => (b.s.ofHigh ?? 0) - (a.s.ofHigh ?? 0) || byV(a, b));
  const recBig = rec.filter(x => x.s.v >= BIG), nearBig = near.filter(x => x.s.v >= BIG);
  const recSmall = rec.filter(x => x.s.v < BIG), nearSmall = near.filter(x => x.s.v < BIG);

  const card = x => {
    const sub = (kids.get(x.it.code) || []).sort(byV).slice(0, 3);
    return `<button class="al-card ${x.it.record ? 'rec' : 'near'}" data-discovery="discovery-export-${esc(x.it.code)}">` +
      `<div class="al-top"><span class="al-badge">${x.it.record ? '5년 신고가' : '최고치의 ' + Math.floor(x.s.ofHigh ?? 0) + '%'}</span><span class="al-code">HS ${esc(x.it.code)}</span></div>` +
      `<div class="al-name">${esc(x.nm)}</div>` +
      `<div class="al-mid"><div><b class="al-val">${eok(x.s.v)}</b><span class="al-unit">억 엔</span></div>${spark(x.s.spark, x.it.record)}</div>` +
      `<div class="al-meta"><span>전년비 <b class="${cls(x.s.yoy)}">${pct(x.s.yoy)}</b></span>` +
      (x.it.record && x.s.overHigh != null ? `<span>직전 최고 대비 <b class="positive">${pct(x.s.overHigh)}</b></span>` : '') + `</div>` +
      (sub.length ? `<div class="al-sub">세부 · ${sub.map(k => esc(k.nm)).join(' · ')}</div>` : '') +
      (x.co ? `<div class="al-co">${esc(x.co)}</div>` : '') + `</button>`;
  };
  const row = x => `<button class="al-row" data-discovery="discovery-export-${esc(x.it.code)}">` +
    `<span class="al-rn">${esc(x.nm)} <small>${esc(x.it.code)}</small></span>` +
    `<span class="al-rv">${eok(x.s.v)}<small>억 엔</small></span>` +
    `<span class="al-ry ${cls(x.s.yoy)}">${pct(x.s.yoy)}</span>` +
    (x.it.record ? `<span class="al-bar rec"><i style="width:100%"></i><em>신고가</em></span></button>`
      : `<span class="al-bar"><i style="width:${Math.min(100, x.s.ofHigh ?? 0)}%"></i><em>최고치의 ${Math.floor(x.s.ofHigh ?? 0)}%</em></span></button>`);

  const SHOW = 12;
  return `<div class="al-head"><h2>🔔 ${+mm}월 수출 신고가 알림</h2>` +
    `<span class="al-cap">${yy}년 ${+mm}월 · 일본 → 세계 · 확인 ${esc(String(data.checkedAt || '').slice(0, 10))}</span>` +
    `<span class="al-chips"><span class="al-chip rec">5년 신고가 <b>${recBig.length}</b></span><span class="al-chip near">신고가 근접 <b>${nearBig.length}</b></span><span class="al-note">월 10억 엔 이상 품목</span></span></div>` +
    (recBig.length ? `<div class="al-grid">${recBig.slice(0, SHOW).map(card).join('')}</div>` : `<p class="al-empty">${q ? '검색 결과가 없습니다.' : '이번 달 10억 엔 이상 품목 중 5년 신고가는 없습니다.'}</p>`) +
    (recBig.length > SHOW ? `<details class="al-more"><summary>신고가 ${recBig.length - SHOW}개 더 보기</summary><div class="al-grid">${recBig.slice(SHOW).map(card).join('')}</div></details>` : '') +
    (nearBig.length ? `<h3 class="al-h3">신고가 근접 <small>최근 3개월 흐름이 5년 최고 구간에 붙은 품목 · 막대는 이번 달이 5년 최고치의 몇 %인지</small></h3>` +
      `<div class="al-list">${nearBig.slice(0, 10).map(row).join('')}</div>` +
      (nearBig.length > 10 ? `<details class="al-more"><summary>근접 ${nearBig.length - 10}개 더 보기</summary><div class="al-list">${nearBig.slice(10).map(row).join('')}</div></details>` : '') : '') +
    (recSmall.length + nearSmall.length ? `<details class="al-more"><summary>월 10억 엔 미만 작은 품목 ${recSmall.length + nearSmall.length}개</summary><div class="al-list">${[...recSmall, ...nearSmall].sort(byV).map(row).join('')}</div></details>` : '');
}
