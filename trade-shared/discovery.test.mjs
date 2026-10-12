import assert from 'node:assert/strict';
import fs from 'node:fs';
import {discoveryHits,normalizeDiscovery,renderDiscovery,fetchDiscovery,fetchDiscoveryStatus} from './discovery.mjs';
import {windowMonths,metricValue} from '../japan-exports/metrics.mjs';
const latest='2026-08',values=Object.fromEntries(windowMonths(latest,72).map((m,i)=>[m,100+i]));
const item={code:'12345',flow:'import',name:'<제품>',values,record:false};
const data={latestMonth:latest,checkedAt:'2026-10-12T00:00:00Z',classification:'End-use 5자리',totalItems:100,eligibleItems:90,excludedItems:10,items:[item,{...item,code:'54321',flow:'export'}]};
const n=normalizeDiscovery(item,latest,{'12345':{name:'제품',companies:'기업 (1234)'}});
assert.equal(n.series.WORLD[latest].value,171,'USD / thousand-yen source values must not be converted during normalization');
assert.equal(n.series.WORLD[latest].quantity,null);assert.equal(metricValue(n.series.WORLD[latest],'price'),null);assert.equal(metricValue(n.series.WORLD[latest],'value'),171/100000);
assert.equal(normalizeDiscovery(item,latest,{'import:12345':{companies:'수입사'},'12345':{companies:'공통'}}).companies,'수입사');
assert.equal(n.companies,'기업 (1234)');assert.equal(n.name,'제품');
const large={...item,code:'99999',values:Object.fromEntries(Object.entries(values).map(([m,v])=>[m,v*100000]))};
assert.equal(discoveryHits({...data,items:[item,large]},{flow:'import'})[0].item.hs[0],'99999','Economically larger record must rank first');
const leveled=renderDiscovery({...data,levels:{4:{totalItems:1200,eligibleItems:1100,matchedItems:200},6:{totalItems:5200,eligibleItems:4600,matchedItems:600}}});assert.ok(leveled.includes('HS4 검사 1200개'));assert.ok(leveled.includes('HS6 검사 5200개'));assert.ok(leveled.includes('상하위 품목 중복 포함'));
assert.equal(discoveryHits(data,{flow:'import'}).length,1);assert.equal(discoveryHits(data,{flow:'export'})[0].item.hs[0],'54321');
const incomplete={...item,values:{...values}};delete incomplete.values['2025-01'];assert.equal(discoveryHits({...data,items:[incomplete]}).length,0,'Incomplete 60-month history cannot be a hit');
const html=renderDiscovery(data,{flow:'import',latest:'2026-09'});
assert.ok(html.includes('&lt;제품&gt;'));assert.ok(html.includes('이전 발표월'));assert.ok(html.includes('발굴 1개'));assert.ok(!html.includes('54321'));assert.ok(html.includes('관련기업 : 미확인'));
assert.ok(!renderDiscovery(data,{flow:'import',latest}).includes('이전 발표월'));
const failedUpdate=renderDiscovery(data,{latest,status:{ok:false}});assert.ok(failedUpdate.includes('최근 갱신 실패'));assert.ok(failedUpdate.includes('data-discovery='));
assert.ok(renderDiscovery(data,{statusError:true}).includes('갱신 상태 확인 불가'));assert.ok(renderDiscovery(data).includes('HS 개정으로 범위가 달라질 수 있어 원문 확인이 필요합니다.'));

const foreign=renderDiscovery(data,{country:'304'});assert.ok(foreign.includes('세계 전체를 선택'));assert.ok(!foreign.includes('data-discovery='));
assert.ok(renderDiscovery(null,{error:true}).includes('불러오지 못했습니다'));assert.ok(!renderDiscovery(null,{error:true}).includes('불러오는 중'));
const many={...data,items:Array.from({length:8},(_,i)=>({...item,code:String(i)}))};const manyHtml=renderDiscovery(many,{flow:'import'});assert.equal((manyHtml.match(/data-discovery=/g)||[]).length,8);assert.ok(manyHtml.indexOf('<details>')>manyHtml.indexOf('data-discovery="discovery-import-4"'));assert.ok(manyHtml.indexOf('<details>')<manyHtml.indexOf('data-discovery="discovery-import-5"'));
globalThis.fetch=async()=>({ok:true,json:async()=>({items:[]})});await assert.rejects(fetchDiscovery('unused'));
globalThis.fetch=async()=>({ok:false});await assert.rejects(fetchDiscovery('unused'));await assert.rejects(fetchDiscoveryStatus('unused'));
console.log('Discovery: signals, flow isolation, top-five expansion, stale/error states, safe text and units passed');
// Deterministic expanded signals: future months may legitimately have no actual hits.
const actualMappings=JSON.parse(fs.readFileSync(new URL('../us-trade/discovery-companies.json',import.meta.url)));
const fixture={...data,items:Object.entries(actualMappings).map(([key])=>{const [flow,code]=key.split(':');return {...item,flow,code,name:'Source product'}})};
const elements=new Map();
function element(id){if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',hidden:false,style:{},selectedOptions:[{text:'금액 · 억 달러'}],classList:{remove(){},add(){}},addEventListener(event,fn){this[event]=fn},setAttribute(){},removeAttribute(){}});return elements.get(id)}
globalThis.document={querySelector:element,querySelectorAll:()=>[],createElement:()=>element('tooltip'),body:{append(){}},addEventListener(){}};
globalThis.location={hash:'#export',pathname:'/us-trade/'};globalThis.localStorage={getItem:()=>null,setItem(){}};
globalThis.window={addEventListener(){}};globalThis.requestAnimationFrame=fn=>fn();
for(const [key,value] of Object.entries({country:'WORLD',range:'60',metric:'value',mode:'trend',sort:'default'}))element('#'+key).value=value;
globalThis.fetch=async path=>({ok:true,json:async()=>path==='data/discovery.json'?fixture:path.endsWith('status.json')?{ok:true}:JSON.parse(fs.readFileSync(new URL('../us-trade/'+path,import.meta.url),'utf8'))});
await import('../us-trade/app.js');await new Promise(r=>setTimeout(r,0));
const el=s=>document.querySelector(s);
document.getElementById=()=>({scrollIntoView(){}});
const firstId=el('#trade-discovery').innerHTML.match(/data-discovery="([^"]+)"/)?.[1];
assert.ok(firstId,'Expanded dataset must render selectable results');
el('#trade-discovery').click({target:{closest:()=>({dataset:{discovery:firstId}})}});
const selected=el('#discovery-card').innerHTML;
assert.ok(!selected.includes('data-fav='),'Discovery card must not offer unsupported favorites');assert.ok(selected.includes('<svg'));assert.ok(selected.includes('<table'));assert.ok(selected.includes('억 달러'));
assert.equal((el('#cards').innerHTML.match(/<article/g)||[]).length,8,'Selecting discovery must preserve curated cards');
console.log('Discovery selected item renders existing USD chart/table and preserves curated cards');

const mappingEntry=Object.entries(actualMappings).find(([key])=>key.startsWith('export:')&&el('#trade-discovery').innerHTML.includes(`data-discovery="discovery-export-${key.split(':')[1]}"`));
assert.ok(mappingEntry,'There must be a mapped export discovery item');
const [mappingKey,actualMapping]=mappingEntry;
el('#trade-discovery').click({target:{closest:()=>({dataset:{discovery:`discovery-export-${mappingKey.split(':')[1]}`}})}});
assert.ok(el('#discovery-card').innerHTML.includes(actualMapping.name),'Browser must fetch root company mapping and use its product name');
assert.ok(el('#discovery-card').innerHTML.includes(actualMapping.companies),'Browser must use actual independently loaded mapping company label');
console.log('Root discovery company mapping loads through browser fetch and appears in selected card');

const searchedMapping={'import:12345':{name:'국문 품목',companies:'한국기업 ABC'}};
for(const query of ['한국기업','국문','12345','abc']){const result=renderDiscovery(data,{flow:'import',companies:searchedMapping,query});assert.ok(result.includes('검색 결과 1개'));assert.ok(result.includes('data-discovery='));assert.ok(result.includes('검사 100개'));}
assert.ok(renderDiscovery(data,{query:'no matches'}).includes('검색 결과 0개'));
assert.ok(!renderDiscovery(data,{query:''}).includes('검색 결과'));
el('#discovery-search').value='no matches';el('#discovery-search').input();assert.ok(el('#trade-discovery').innerHTML.includes('검색 결과 0개'));
el('#discovery-search').value=mappingKey.split(':')[1];el('#discovery-search').input();assert.ok(el('#trade-discovery').innerHTML.includes('검색 결과 1개'));
el('#discovery-search').value='';el('#discovery-search').input();assert.ok(!el('#trade-discovery').innerHTML.includes('검색 결과'));
console.log('Discovery-only Korean/company/code search and default reset passed');
