import assert from 'node:assert/strict';
import fs from 'node:fs';import zlib from 'node:zlib';
import {themes} from '../themes.mjs';
const data=JSON.parse(zlib.gunzipSync(fs.readFileSync(new URL('../data/exports.json.gz',import.meta.url))));
for(const item of data.items){const t=themes[item.id];assert.ok(t,`Missing related company: ${item.id}`);for(const k of ['companies','precision','why','limit','url'])assert.ok(t[k],`${item.id}.${k}`);assert.match(t.url,/^https:\/\//);assert.ok(t.impact?.up&&t.impact?.down,item.id);}
assert.match(themes.memory.companies,/키옥시아/);assert.match(themes.beer.companies,/아사히|기린|삿포로/);
console.log(`All ${data.items.length} Japanese export categories have sourced company mappings`);
