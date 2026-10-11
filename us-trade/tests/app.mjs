import assert from 'node:assert/strict';
import fs from 'node:fs';
const elements=new Map(),listeners={};
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,style:{},selectedOptions:[{text:'금액 · 억 달러'}],classList:{remove(){},add(){}},addEventListener(event,fn){this[event]=fn},setAttribute(){},removeAttribute(){}});return elements.get(id)}
globalThis.document={querySelector:element,querySelectorAll:()=>[],createElement:()=>element('tooltip'),body:{append(){}}};
globalThis.location={hash:'#import',pathname:'/us-trade/'};globalThis.localStorage={getItem:()=>null,setItem(){}};
globalThis.window={addEventListener:(e,fn)=>listeners[e]=fn};globalThis.requestAnimationFrame=fn=>fn();
element('#country').value='WORLD';element('#range').value='60';element('#metric').value='value';element('#mode').value='trend';element('#sort').value='default';
globalThis.document.addEventListener=()=>{};
globalThis.fetch=async path=>({ok:true,json:async()=>JSON.parse(fs.readFileSync(new URL('../'+path,import.meta.url),'utf8'))});
await import('../app.js'); await new Promise(r=>setTimeout(r,0));
let cards=element('#cards').innerHTML;
assert.equal((cards.match(/<article/g)||[]).length,8);assert.equal((cards.match(/<svg/g)||[]).length,8);assert.equal((cards.match(/<table/g)||[]).length,8);
assert.ok(cards.includes('세면용품·화장품'));assert.ok(!cards.includes('민간 항공기'));
assert.ok(cards.includes('억 달러'));assert.ok(!cards.includes('억 엔'));assert.ok(!cards.includes('/ HS '));
location.hash='#export';listeners.hashchange();cards=element('#cards').innerHTML;
assert.equal((cards.match(/<article/g)||[]).length,8);assert.ok(cards.includes('민간 항공기'));assert.ok(cards.includes('셰니어'));assert.equal(element('#page-title').textContent,'미국 수출 트래커');
element('#mode').value='season';element('.controls').change();assert.ok(element('#cards').innerHTML.includes('class="legend"'));
element('#search').value='LNG';element('#search').input();assert.equal((element('#cards').innerHTML.match(/<article/g)||[]).length,1);
element('#search').value='not-matched';element('#search').input();assert.ok(element('#cards').innerHTML.includes('해당하는 품목이 없습니다'));
console.log('US tracker runtime: import/export, 8 charts/tables, USD, season view and company search passed');

element('#search').value='';element('.tabs').click({target:{closest:()=>({dataset:{group:'항공·에너지'},classList:{add(){}}})}});location.hash='#import';listeners.hashchange();assert.equal((element('#cards').innerHTML.match(/<article/g)||[]).length,8,'Switching flow resets incompatible category');
