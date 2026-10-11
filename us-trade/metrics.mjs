export function monthsEnding(last,n){const [y,m]=last.split('-').map(Number),end=y*12+m-1;return Array.from({length:n},(_,j)=>{const i=end-n+1+j;return `${Math.floor(i/12)}-${String(i%12+1).padStart(2,'0')}`})}
export function yoy(values,month){const previous=`${Number(month.slice(0,4))-1}${month.slice(4)}`,a=values[month],b=values[previous];return Number.isFinite(a)&&Number.isFinite(b)&&b>0?(a/b-1)*100:null}
export function valueAt(item,month,metric){return metric==='yoy'?yoy(item.values,month):item.values[month]??null}
export function sortItems(items,month,metric,descending){return [...items].sort((a,b)=>{const av=valueAt(a,month,metric),bv=valueAt(b,month,metric);if(av==null)return bv==null?0:1;if(bv==null)return -1;return (av-bv)*(descending?-1:1)})}
