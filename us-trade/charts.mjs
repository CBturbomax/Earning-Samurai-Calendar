import {change,priorYear} from '../japan-exports/metrics.mjs';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,sign=false)=>v==null||!Number.isFinite(v)?'—':`${sign&&Math.round(v)>0?'+':''}${(Math.round(v)||0).toLocaleString('ko-KR')}`;
const fullDate=m=>`${m.slice(0,4)}년 ${Number(m.slice(5))}월`;
const colors=['#657384','#91a5b7','#5B9BD5','#ED7D31','#FFCB05'];
function val(rows,m,metric){return metric==='yoy'?change(rows[m]?.value,rows[priorYear(m)]?.value):rows[m]?.value==null?null:rows[m].value/1e8}
function unit(rows,metric){return metric==='yoy'?'%':'억 달러'}
export function plot(rows,months,metric,mode){
 const W=1280,H=320,L=75,R=20,T=30,B=42,pw=W-L-R,ph=H-T-B;
 let lines=[],values=[];
 if(mode==='season'){
  const years=[...new Set(months.map(m=>m.slice(0,4)))].slice(-Math.min(5,Math.ceil(months.length/12)));
  lines=years.map((y,i)=>({label:y+'년',color:colors[colors.length-years.length+i],points:Array.from({length:12},(_,j)=>{let m=`${y}-${String(j+1).padStart(2,'0')}`;return {m,v:months.includes(m)?val(rows,m,metric):null}})}));values=lines.flatMap(l=>l.points.map(p=>p.v));
 }else values=months.map(m=>val(rows,m,metric));
 const valid=values.filter(v=>v!=null&&Number.isFinite(v));if(!valid.length)return '<div class="plot-empty">이 지표에 비교 가능한 수치가 없습니다.</div>';
 let max=Math.max(0,...valid),min=Math.min(0,...valid);if(max===min)max=min+1;let span=max-min;max+=span*.09;if(min<0)min-=span*.09;
 const y=v=>T+(max-v)/(max-min)*ph,x=(i,n)=>L+(i+.5)/n*pw;
 let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc((metric==='yoy'?'전년 대비 (%)':'금액 (억 달러)'))} ${mode==='season'?'연도별 비교':'월별 추이'}"><text x="${L}" y="16">${esc(unit(rows,metric))}</text>`;
 for(let i=0;i<=4;i++){let v=min+(max-min)*i/4,yy=y(v);svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#2a3542"/><text x="${L-12}" y="${yy+4}" text-anchor="end">${fmt(v)}</text>`}
 svg+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="#59616c"/>`;
 if(mode==='season'){
  lines.forEach(l=>{let path='',started=false;l.points.forEach((p,i)=>{if(p.v==null){started=false;return}path+=`${started?'L':'M'}${x(i,12)},${y(p.v)} `;started=true});svg+=`<path d="${path}" fill="none" stroke="${l.color}" stroke-width="3"/>`;l.points.forEach((p,i)=>{if(p.v==null)return;let tip=`${fullDate(p.m)} · ${fmt(p.v,metric==='yoy')}${metric==='yoy'?'%':' '+unit(rows,metric)}`;svg+=`<circle cx="${x(i,12)}" cy="${y(p.v)}" r="5" fill="${l.color}" data-tip="${esc(tip)}"><title>${esc(tip)}</title></circle>`})});
  for(let i=0;i<12;i++)svg+=`<text x="${x(i,12)}" y="${H-12}" text-anchor="middle">${i+1}월</text>`;
 }else{
  const bw=pw/months.length*.70;
  months.forEach((m,i)=>{let v=values[i];if(v==null)return;let xx=x(i,months.length),yy=y(v),tip=`${fullDate(m)} · ${fmt(v,metric==='yoy')}${metric==='yoy'?'%':' '+unit(rows,metric)}`;svg+=`<rect class="bar" x="${xx-bw/2}" y="${Math.min(yy,y(0))}" width="${bw}" height="${Math.max(1,Math.abs(y(0)-yy))}" fill="${i===months.length-1?'#FFCB05':v<0?'#5B9BD5':'#5B9BD5'}" opacity="${i===months.length-1?1:.88}" data-tip="${esc(tip)}"><title>${esc(tip)}</title></rect>`;if(i===0||i===months.length-1||i%Math.max(1,Math.floor(months.length/8))===0)svg+=`<text x="${xx}" y="${H-12}" text-anchor="middle">${m.slice(2,4)}.${m.slice(5)}</text>`});
 }
 return svg+'</svg>'+(mode==='season'?`<div class="legend">${lines.map(l=>`<span><i style="background:${l.color}"></i>${l.label}</span>`).join('')}</div>`:'');
}
