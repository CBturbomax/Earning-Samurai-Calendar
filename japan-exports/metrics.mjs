export function change(value,base){return value==null||base==null||base===0?null:(value-base)/base*100}
export function windowMonths(latest,n=60){let [y,m]=latest.split('-').map(Number),out=[];for(let i=n-1;i>=0;i--){let d=new Date(Date.UTC(y,m-1-i,1));out.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`)}return out}
export function metricValue(r,metric){if(!r)return null;if(metric==='quantity')return r.quantity??null;if(metric==='price')return r.quantity>0?r.value*1000/r.quantity:null;return r.value==null?null:r.value/100000}
export function priorYear(m){return `${Number(m.slice(0,4))-1}${m.slice(4)}`}
export function compatibleSeries(rows,months){const units=new Set(months.map(m=>rows[m]).filter(r=>r?.quantity!=null).map(r=>r.unit));if(units.size<=1)return rows;return Object.fromEntries(Object.entries(rows).map(([m,r])=>[m,{...r,quantity:null,unit:null}]))}

export function rollingGrowth(rows,latest){const months=windowMonths(latest,3),a=months.map(m=>rows[m]?.value),b=months.map(m=>rows[priorYear(m)]?.value);if([...a,...b].some(v=>v==null||!Number.isFinite(v)))return null;return change(a.reduce((x,y)=>x+y,0),b.reduce((x,y)=>x+y,0))}
