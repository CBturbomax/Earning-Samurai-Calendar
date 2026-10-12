import {highSignal} from './highlights.mjs';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function normalizeDiscovery(item,latestMonth,companies={}) {
 const mapping=companies[`${item.flow}:${item.code}`]||companies[item.code]||{};
 return {id:`discovery-${item.flow||'export'}-${item.code}`,name:mapping.name||item.name,group:'전체 품목 발굴',hs:[item.code],note:'세계 전체 · '+(item.flow?'End-use 5자리 · 계절조정':`HS ${item.code.length}자리 · 금액 기준`),meta:{flow:item.flow||'export'},latestMonth,companies:mapping.companies||null,series:{WORLD:Object.fromEntries(Object.entries(item.values||{}).map(([m,value])=>[m,{value,quantity:null,unit:null}]))}};
}
export function discoveryHits(data,{flow,companies={}}={}) {
 return (data?.items||[]).filter(i=>!flow||i.flow===flow).map(i=>{const item=normalizeDiscovery(i,data.latestMonth,companies);return {item,signal:highSignal(item.series.WORLD,data.latestMonth)}}).filter(x=>x.signal).sort((a,b)=>Number(b.signal.record)-Number(a.signal.record)||(b.item.series.WORLD[data.latestMonth]?.value??0)-(a.item.series.WORLD[data.latestMonth]?.value??0)||b.signal.ratio-a.signal.ratio||(b.signal.growth??-Infinity)-(a.signal.growth??-Infinity));
}
export function renderDiscovery(data,{flow,companies={},latest,country='WORLD',error=false,status=null,statusError=false,query=''}={}) {
 const title='<div class="high-title"><h2>전체 품목 발굴</h2></div>';
 if(!data)return title+`<p>${error?'전체 품목 자료를 불러오지 못했습니다. 다시 새로고침해 주세요.':'전체 품목 자료를 불러오는 중입니다.'}</p>`;
 const allHits=discoveryHits(data,{flow,companies}),search=query.trim().toLowerCase(),hits=allHits.filter(({item})=>!search||[item.name,...item.hs,item.companies||''].join(' ').toLowerCase().includes(search)),counts=data.counts?.[flow]||data,aggregate=flow&&!data.counts?.[flow]?'수출·수입 합계 검사':'검사';
 const row=({item,signal})=>`<div class="discovery-row"><button data-discovery="${esc(item.id)}"><strong>${esc(item.name)}</strong><small>${esc(item.hs[0])}</small><span class="high-badge">${signal.record?'5년 최고':`최고치 근접 · ${Math.min(100,Math.floor(signal.ratio))}%`}</span></button><small>관련기업 : ${esc(item.companies||'미확인')}</small></div>`;
 const levels=data.levels?`<p class="discovery-count">${Object.entries(data.levels).map(([level,c])=>`HS${esc(level)} 검사 ${esc(c.totalItems)}개 · 비교 가능 ${esc(c.eligibleItems)}개 · 발굴 ${esc(c.matchedItems)}개`).join(' / ')}<br>HS4·HS6 상하위 품목 중복 포함 · 합산하지 마세요.</p>`:'';
 const checked=String(data.checkedAt||'').slice(0,10)||'확인일 미상';
 return title+`<p class="high-caption">${flow==='import'?'미국 수입':flow==='export'?'미국 수출':'일본 수출'} · 세계 전체 · ${esc(data.classification)} · ${esc(data.latestMonth)}</p><p class="discovery-count">${aggregate} ${esc(counts.totalItems)}개 · 비교 가능 ${esc(counts.eligibleItems)}개 · 제외 ${esc(counts.excludedItems)}개 · 발굴 ${allHits.length}개${search?` · 검색 결과 ${hits.length}개`:""} · 확인 ${esc(checked)}</p>${levels}${status?.ok===false?'<p class="warning">최근 갱신 실패 · 마지막 확인 자료 표시</p>':statusError?'<p class="warning">갱신 상태 확인 불가</p>':''}${latest&&data.latestMonth<latest?'<p class="warning">전체 품목 자료가 기본 품목보다 이전 발표월입니다.</p>':''}${country!=='WORLD'?'<p>전체 품목 발굴은 세계 전체 기준입니다. 세계 전체를 선택하면 발굴 품목을 볼 수 있습니다.</p>':hits.length?hits.slice(0,5).map(row).join('')+(hits.length>5?`<details><summary>전체 ${hits.length}개 보기</summary>${hits.slice(5).map(row).join('')}</details>`:''):(search?'<p>검색 결과가 없습니다.</p>':'<p>조건에 맞는 품목이 없습니다.</p>')}<details class="high-rules"><summary>발굴 기준</summary><p>최근 60개월 자료가 모두 있는 품목 중 최신월이 5년 최고(동률 포함), 또는 최근 3개월 평균이 5년 내 최고 3개월 평균의 95% 이상인 품목입니다. 5년 최고 품목을 먼저, 그 안에서는 최신월 금액이 큰 순으로 표시합니다. 발굴 수는 현재 조건에 맞는 품목 수이며, 지난 발표 대비 신규 진입 수는 아닙니다.${!flow?' HS 개정으로 범위가 달라질 수 있어 원문 확인이 필요합니다.':''}</p></details>`;
}
export async function fetchDiscovery(path){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{const r=await fetch(path,{cache:'no-cache',signal:controller.signal});if(!r.ok)throw Error('discovery unavailable');const data=await r.json();if(!Array.isArray(data.items)||!/^\d{4}-\d{2}$/.test(data.latestMonth))throw Error('invalid discovery');return data}finally{clearTimeout(timer)}
}

export async function fetchDiscoveryStatus(path){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{const r=await fetch(path,{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error('status unavailable');const status=await r.json();if(typeof status.ok!=='boolean')throw Error('invalid status');return status}finally{clearTimeout(timer)}
}
