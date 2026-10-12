import {windowMonths,rollingGrowth} from '../japan-exports/metrics.mjs';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// Fixed 60-month monetary benchmark, independent of chart metric/range.
// Incomplete histories cannot substantiate a five-year high.
export function highSignal(rows,latest){
 const values=windowMonths(latest,60).map(m=>rows[m]?.value);
 if(values.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0))return null;
 const peak=Math.max(...values);if(peak<=0)return null;
 const averages=values.slice(2).map((v,i)=>(values[i]+values[i+1]+v)/3);
 const ratio=averages.at(-1)/Math.max(...averages)*100,record=values.at(-1)>=peak;
 return record||ratio>=95?{record,ratio,growth:rollingGrowth(rows,latest)}:null;
}
export function renderHighlights(items,{latest,country,context,company}){
 const hits=items.map(item=>({item,signal:highSignal(item.series[country]||{},latest)})).filter(x=>x.signal).sort((a,b)=>Number(b.signal.record)-Number(a.signal.record)||b.signal.ratio-a.signal.ratio||(b.signal.growth??-Infinity)-(a.signal.growth??-Infinity));
 const row=({item,signal})=>{const c=company(item)||{};return `<div class="high-row"><button data-highlight="${esc(item.id)}"><strong>${esc(item.name)}</strong><span class="high-badge">${signal.record?'5년 최고':`최고치 근접 · ${Math.min(100,Math.floor(signal.ratio))}%`}</span></button><div><b>${esc(c.names||'연결 기업 미등록')}</b><p>${esc(c.reason||'이 품목에는 검증된 기업 연결이 등록되지 않았습니다.')}</p></div><span class="high-growth">3개월 전년비<br><b>${signal.growth==null?'—':`${signal.growth>0?'+':''}${Math.round(signal.growth)}%`}</b></span></div>`};
 return `<div class="high-title"><h2>지금 볼 품목·기업</h2><span>${esc(context)} · ${esc(latest)}</span></div><p class="high-caption">수출입 금액 기준 · 먼저 살펴볼 기업</p>${hits.length?hits.slice(0,5).map(row).join(''):'<p>현재 조건에 맞는 품목이 없습니다.</p>'}${hits.length>5?`<details><summary>나머지 ${hits.length-5}개 보기</summary>${hits.slice(5).map(row).join('')}</details>`:''}<details class="high-rules"><summary>선별 기준</summary><p>최신월이 최근 5년 최고(동률 포함), 또는 최근 3개월 평균이 5년 내 최고 3개월 평균의 95% 이상. 60개월 자료가 모두 있는 품목만 비교합니다. 검색·분류·차트 기간과 무관하게 현재 국가의 전체 품목에서 고릅니다. 기업은 매수 확정 신호가 아닌 검토 후보입니다. 가격·환율·계절성도 금액에 영향을 줍니다.</p></details>`;
}
