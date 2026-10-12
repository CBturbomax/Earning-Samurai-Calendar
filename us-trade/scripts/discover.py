"""Discover five-year highs across every Census five-digit End-use goods series."""
import argparse
import datetime as dt
import json
import math
import re
import urllib.request
from pathlib import Path
from collect import SOURCE, months_ending, month_key, xlsx_rows

ROOT = Path(__file__).resolve().parents[1]
KOREAN_NAMES = {
    '21400':'통신장비', '21320':'반도체', '20005':'전기기기',
    '21180':'기타 산업기계', '21100':'산업용 엔진', '21160':'측정·시험·제어 기기',
    '00100':'육류 제품', '22010':'민간 항공기 부품', '12070':'담배 원료·왁스 등',
    '21120':'금속가공 공작기계', '00340':'견과류', '12290':'기타 비철금속',
    '12200':'알루미늄·알루미나', '16040':'황·비금속 광물', '22220':'선박 엔진·부품',
    '40130':'담배 제품', '22020':'민간 항공기 엔진', '41310':'보석류 등',
    '42000':'묘목 등', '11110':'연료유', '21301':'컴퓨터 부속품',
    '12770':'기타 산업용 원부자재', '14220':'구리', '20000':'발전기·부속품',
    '12210':'구리', '12110':'기타 철강 제품', '14290':'기타 비철금속',
    '01010':'주류(와인 제외)', '22210':'기타 상선', '21610':'의료장비',
    '00310':'유제품·달걀', '41050':'휴대전화·기타 가정용품', '12750':'산업용 고무 제품',
    '10150':'기타 농업 가공품', '00320':'과일·냉동 주스', '12760':'가공 광물 원자재',
    '00360':'기타 식품', '21150':'펄프·제지 기계', '00220':'기타 동물 사료',
    '12700':'일차 형태 합성고무', '40120':'세면용품·화장품', '21300':'컴퓨터',
    '00150':'식용유·유지종자', '16120':'기타 산업용 원부자재',
    '21190':'사진·서비스업용 기계', '30200':'차량 엔진·엔진 부품',
    '22000':'민간 항공기', '12550':'기타 화학제품', '40140':'기타 비내구 소비재',
}

def parse_discovery_rows(rows):
    rows = iter(rows)
    if next(rows, None) != ['DATE', 'ENDUSE', 'DESCRIPTION', 'VALUE']:
        raise ValueError('Unexpected Census schema')
    series = {}
    for date, code, description, value in rows:
        if not any((date, code, description, value)):
            continue
        if not re.fullmatch(r'\d{5}', code):
            continue
        month = month_key(date)
        item = series.setdefault(code, {'name': description, 'values': {}})
        if month in item['values']:
            raise ValueError('Duplicate category-month: '+code+' '+month)
        try:
            amount = float(value)
        except (ValueError, TypeError):
            amount = float('nan')
        item['values'][month] = amount
        item['name'] = description
    if not series:
        raise ValueError('No five-digit End-use series')
    return series

def build_from_rows(rows_by_flow):
    sources = {flow: parse_discovery_rows(rows_by_flow[flow]) for flow in ('import', 'export')}
    latest_by_flow = {flow: max(month for item in series.values() for month in item['values']) for flow, series in sources.items()}
    latest = min(latest_by_flow.values())
    months = months_ending(latest, 60)
    items, coverage = [], {}
    for flow, series in sources.items():
        reasons = {'incomplete': 0, 'invalidValue': 0}
        eligible = 0
        for code, source in series.items():
            values = source['values']
            if any(month not in values for month in months):
                reasons['incomplete'] += 1
                continue
            ordered = [values[month] for month in months]
            if any(not math.isfinite(value) or value < 0 for value in ordered):
                reasons['invalidValue'] += 1
                continue
            eligible += 1
            averages = [sum(ordered[index:index+3])/3 for index in range(58)]
            peak = max(ordered)
            avg_peak = max(averages)
            if peak <= 0 or avg_peak <= 0:
                continue
            record = ordered[-1] >= peak
            ratio = averages[-1]/avg_peak
            if not record and ratio < .95:
                continue
            prior = sum(ordered[-15:-12])/3
            items.append({'id':f'us-{flow}-{code}', 'flow':flow, 'code':code,
                          'name':KOREAN_NAMES.get((flow,code), KOREAN_NAMES.get(code,source['name'])),
                          'originalName':source['name'], 'sourceName':source['name'],
                          'values':{month:int(value) if value.is_integer() else value for month,value in zip(months,ordered)},
                          'record':record,'ratio':ratio*100,
                          'growth':(averages[-1]/prior-1)*100 if prior > 0 else None})
        coverage[flow] = {'totalItems':len(series),'eligibleItems':eligible,
                          'excludedItems':len(series)-eligible,'latestSourceMonth':latest_by_flow[flow],
                          'exclusionReasons':reasons}
    items.sort(key=lambda item:(not item['record'],-item['ratio'],-item['values'][latest],item['id']))
    return {'latestMonth':latest,'checkedAt':dt.datetime.now(dt.timezone.utc).isoformat(),
            'classification':'Census 5-digit End-use','unit':'USD','adjustment':'seasonally adjusted',
            'windowMonths':60,'sourceUrls':[SOURCE.format(flow) for flow in sources],
            'totalItems':sum(c['totalItems'] for c in coverage.values()),
            'eligibleItems':sum(c['eligibleItems'] for c in coverage.values()),
            'excludedItems':sum(c['excludedItems'] for c in coverage.values()),
            'counts':{flow:{key:value for key,value in c.items() if key.endswith('Items')} for flow,c in coverage.items()},
            'coverage':coverage,'items':items}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-dir',type=Path)
    args=parser.parse_args()
    status_path=ROOT/'data/discovery-status.json'
    try:
        rows={}
        for flow in ('import','export'):
            if args.source_dir:
                content=(args.source_dir/f'{flow}s_enduse.xlsx').read_bytes()
            else:
                request=urllib.request.Request(SOURCE.format(flow),headers={'User-Agent':'Mozilla/5.0 EarningSamurai public statistics tracker'})
                with urllib.request.urlopen(request,timeout=90) as response:
                    content=response.read()
            rows[flow]=xlsx_rows(content)
        data=build_from_rows(rows)
        path=ROOT/'data/discovery.json'
        if path.exists() and json.loads(path.read_text())['latestMonth'] > data['latestMonth']:
            raise ValueError('Source month moved backward')
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
        temp.replace(path)
        status={'ok':True,'checkedAt':data['checkedAt'],'latestMonth':data['latestMonth']}
    except Exception as error:
        status={'ok':False,'checkedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'error':type(error).__name__}
        status_path.write_text(json.dumps(status)+'\n')
        raise
    status_path.write_text(json.dumps(status)+'\n')
    print(f"{len(data['items'])} hits / {data['eligibleItems']} eligible / {data['totalItems']} five-digit series; latest {data['latestMonth']}")

if __name__=='__main__': main()
