"""Broad Japan export screen: all reported HS4 and HS6 headings, 60 complete months.

Only national WORLD CSVs are used. Never uses collect.PREFIXES. Absent HS4
rows become zero only after validating each complete annual national source.
HS4 headings introduced/removed by HS2022 are excluded from comparisons.
"""
import csv
import datetime as dt
import hashlib
import html
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collect import ROOT, MONTHS, fetch, listing, links_in

NOMENCLATURE = 'https://comtradeapi.un.org/files/v1/app/reference/{}.json'


def months_ending(latest, count=60):
    year, month = map(int, latest.split('-'))
    end = year * 12 + month - 1
    return [f'{n // 12}-{n % 12 + 1:02}' for n in range(end-count+1, end+1)]


def parse_national(text, year, through_month):
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    required = {'HS', 'Year', 'Exp or Imp'} | {'Value-'+m for m in MONTHS[:through_month]}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError('Invalid/incomplete national CSV header')
    if 'Country' in reader.fieldnames:
        raise ValueError('Expected national totals, not country records')
    values, seen, confidential = {}, set(), 0
    for row in reader:
        if row['Exp or Imp'] != '1' or int(row['Year']) != year:
            raise ValueError('Wrong direction or year')
        code = row['HS'].strip("' ")
        if code in seen:
            raise ValueError('Duplicate national HS row')
        seen.add(code)
        amounts = []
        for mon in MONTHS[:through_month]:
            value = row['Value-'+mon].strip()
            if not value.isdigit():
                raise ValueError('Missing or invalid published monthly value')
            amounts.append(int(value))
        if re.fullmatch(r'\d{2}X{6}[A-Z]', code):
            confidential += 1
            continue
        if not re.fullmatch(r'\d{9}', code):
            raise ValueError(f'Invalid HS code {code}')
        for width in (4, 6):
            out = values.setdefault(code[:width], {})
            for month, value in enumerate(amounts, 1):
                ym = f'{year}-{month:02}'
                out[ym] = out.get(ym, 0) + value
    if not values:
        raise ValueError('Empty national source')
    return values, confidential, len(seen)


def collect_national(job):
    year, month = job
    _, page = listing(year, month)
    sources = links_in(page)
    if len(sources) != 1:
        raise ValueError(f'{year}: expected exactly one national source')
    meta = sources[0]
    blob = fetch(meta['url'])
    values, confidential, rows = parse_national(blob.decode('utf-8-sig'), year, month)
    # A truncated CSV can parse cleanly; reject implausibly small national tables.
    if rows < 4000 or len(values) < 1000:
        raise ValueError(f'{year}: national source appears incomplete: {rows} rows')
    return dict(year=year, throughMonth=month, values=values,
                confidentialRows=confidential, sourceRows=rows,
                source={**meta, 'sha256': hashlib.sha256(blob).hexdigest(), 'bytes':len(blob)})


def signal(values):
    if len(values) != 60 or any(not isinstance(v, (float, int)) or v < 0 for v in values):
        raise ValueError('Screen requires 60 nonnegative monthly values')
    peak = max(values)
    if peak <= 0:
        return None
    averages = [sum(values[i:i+3])/3 for i in range(58)]
    ratio = averages[-1]/max(averages)*100
    record = values[-1] >= peak
    prior = sum(values[-15:-12])
    growth = (sum(values[-3:])/prior-1)*100 if prior else None
    return dict(record=record, ratio=ratio, growth=growth) if record or ratio >= 95 else None


def screen(snapshots, latest, names, previous_names):
    months = months_ending(latest)
    by_year = {s['year']: s for s in snapshots}
    if len(by_year) != len(snapshots):
        raise ValueError('Duplicate annual snapshot')
    for ym in months:
        y, m = map(int, ym.split('-'))
        if y not in by_year or by_year[y]['throughMonth'] < m:
            raise ValueError(f'Missing validated source month {ym}')
    codes = sorted({c for s in snapshots for c in s['values']})
    items, excluded = [], []
    eligible = 0
    for code in codes:
        if code not in names or code not in previous_names or ' '.join(names[code].lower().split()) != ' '.join(previous_names[code].lower().split()):
            excluded.append({'code':code, 'reason':'HS code absent or description changed between HS2017 and HS2022 nomenclatures'})
            continue
        values = {}
        for ym in months:
            source = by_year[int(ym[:4])]['values']
            # A present heading must have every published month; only absent rows imply zero.
            if code in source and ym not in source[code]:
                raise ValueError(f'Missing month for present heading {code} {ym}')
            values[ym] = source.get(code, {}).get(ym, 0)
        if max(values.values()) <= 0:
            excluded.append({'code':code, 'reason':'No positive export value in the 60-month window'})
            continue
        eligible += 1
        hit = signal(list(values.values()))
        if hit:
            items.append(dict(id='jp-hs-'+code, code=code, name=names[code], values=values, **hit))
    items.sort(key=lambda x:(-x['record'], -x['ratio'], -(x['growth'] if x['growth'] is not None else -1e99)))
    return dict(totalItems=len(codes), eligibleItems=eligible, excludedItems=len(excluded),
                exclusions=excluded, matchedItems=len(items), items=items, levels={str(n):dict(totalItems=sum(len(c)==n for c in codes), eligibleItems=sum(len(c)==n for c in codes)-sum(len(e['code'])==n for e in excluded), matchedItems=sum(len(i['code'])==n for i in items)) for n in (4,6)})


def nomenclature(version):
    doc = json.loads(fetch(NOMENCLATURE.format(version)))
    names = {r['id']: re.sub(r'^\d{4,6}\s*-\s*', '', r['text'])
             for r in doc['results'] if re.fullmatch(r'(?:\d{4}|\d{6})', r['id'])}
    if len(names) < 1000:
        raise ValueError('Incomplete official HS nomenclature')
    return names


def run_collection():
    _, page = listing()
    ym = re.findall(r'year=(\d{4})0(?:&amp;|&).*?month=(\d{8})', html.unescape(page))
    if not ym:
        raise ValueError('No official publication month discovered')
    year, month = max((int(y), int(m[-2:])) for y, m in ym)
    latest = f'{year}-{month:02}'
    jobs = [(y, month if y == year else 12) for y in range(year-5, year+1)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(collect_national, jobs))
        names, previous = list(pool.map(nomenclature, ['H6', 'H5']))
    doc = dict(latestMonth=latest, checkedAt=dt.datetime.now(dt.timezone.utc).isoformat(),
               classification='HS4·HS6', country='WORLD', flow='exports', valueUnit='thousand JPY',
               displayMonths=60, nameLanguage='en',
               sourceUrls=[s['source']['url'] for s in snapshots]+[NOMENCLATURE.format(v) for v in ['H6','H5']],
               sources=[dict(**s['source'], year=s['year'], throughMonth=s['throughMonth'],
                             sourceRows=s['sourceRows'], confidentialRows=s['confidentialRows']) for s in snapshots],
               methodology='All HS4 and HS6 codes reported in full national export CSVs; no curated-prefix filter. '
               'Latest month at 60-month high (ties included), or latest 3-month average >=95% of highest '
               '3-month average wholly within 60 months. Zeros only for absent headings in validated national '
               'sources. Codes missing from either HS2017 or HS2022, or with changed official descriptions, excluded. Remaining headings can '
               'contain HS revision scope changes; no concordance adjustment. Confidential chapter aggregates excluded.',
               **screen(snapshots, latest, names, previous))
    write_snapshot(doc)
    print(json.dumps({k:doc[k] for k in ['latestMonth','totalItems','eligibleItems','excludedItems','matchedItems']}))
    for item in doc['items'][:25]:
        print(item['code'], item['name'], item['record'], round(item['ratio'],1), item['growth'])

    return doc


def write_snapshot(doc):
    target = ROOT/'data'/'discovery.json'
    if target.exists() and json.loads(target.read_text()).get('latestMonth', '') > doc['latestMonth']:
        raise ValueError('Official discovery publication month regressed; preserving previous snapshot')
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(doc, ensure_ascii=False, separators=(',',':'))+'\n')
    temp.replace(target)


def main():
    target = ROOT/'data'/'discovery-status.json'
    status = {'checkedAt':dt.datetime.now(dt.timezone.utc).isoformat(), 'ok':False}
    try:
        doc = run_collection()
        status.update(ok=True, latestMonth=doc['latestMonth'], matchedItems=doc['matchedItems'])
    except Exception as error:
        status.update(error=str(error), errorType=type(error).__name__)
        raise
    finally:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(status,ensure_ascii=False)+'\n')
        temp.replace(target)


if __name__ == '__main__':
    main()
