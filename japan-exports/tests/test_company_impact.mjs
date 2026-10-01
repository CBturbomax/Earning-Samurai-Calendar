import test from 'node:test';
import assert from 'node:assert/strict';
import {themes} from '../themes.mjs';
let render;try{({renderCompanyImpact:render}=await import('../company-impact.mjs'))}catch{}
test('missing company export shares are not replaced by business sales proportions',()=>{
 assert.equal(typeof render,'function');
 const html=render(themes.camera_lenses,10);
 assert.match(html,/일본 수출 내 비중/);assert.match(html,/산출 불가/);
 assert.match(html,/71\.3%/);assert.match(html,/2025/);assert.match(html,/회사 매출 내/);
});
test('falling, missing and flat export growth do not imply positive earnings',()=>{
 assert.equal(typeof render,'function');
 assert.match(render(themes.fishing_reels,-5),/수출 감소/);
 assert.match(render(themes.fishing_reels,null),/판단 보류/);
 assert.match(render(themes.fishing_reels,0),/수출 보합/);
});
test('all covered themes preserve source, caveat and both directions',()=>{
 for(const [id,t] of Object.entries(themes)){
  assert.ok(t.impact,id);assert.ok(t.impact.up,id);assert.ok(t.impact.down,id);
  assert.ok(t.impact.caution,id);assert.equal(t.impact.exportShare,null,id);
  for(const s of t.impact.sources)assert.match(s.url,/^https:\/\//);
 }
});
test('notes escape editorial strings and make no unsupported ranking',()=>{
 assert.equal(typeof render,'function');const t={impact:{...themes.camera_lenses.impact,up:'<script>bad</script>'}};
 const html=render(t,1);assert.ok(!html.includes('<script>'));assert.match(html,/&lt;script&gt;/);
 assert.match(html,/최대 수출기업 미확인/);
});
