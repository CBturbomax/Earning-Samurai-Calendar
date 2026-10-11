"""Census monthly seasonally adjusted End-use goods data. Standard library only."""
import argparse, calendar, datetime as dt, io, json, math, re, urllib.request, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
SOURCE='https://www.census.gov/foreign-trade/statistics/historical/{}s_enduse.xlsx'
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
def month_key(value):
    match=re.fullmatch(r'([A-Z][a-z]{2})-(\d{2})',value)
    if not match or match[1] not in calendar.month_abbr: raise ValueError('Invalid monthly date')
    year=int(match[2]); year+=1900 if year>=94 else 2000
    return f'{year:04d}-{list(calendar.month_abbr).index(match[1]):02d}'
def months_ending(last,n=72):
    y,m=map(int,last.split('-')); k=y*12+m-1
    return [f'{i//12:04d}-{i%12+1:02d}' for i in range(k-n+1,k+1)]
def xlsx_rows(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        ss=[''.join(e.itertext()) for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        for row in root.findall('.//m:row',NS):
            result=['']*4
            for c in row.findall('m:c',NS):
                col=re.sub(r'\d','',c.attrib['r'])
                if col not in 'ABCD' or len(col)!=1:continue
                v=c.find('m:v',NS)
                value=v.text if v is not None else ''
                if c.get('t')=='s' and value:value=ss[int(value)]
                elif c.get('t')=='inlineStr':value=''.join(c.find('m:is',NS).itertext())
                result[ord(col)-65]=value
            yield result

def parse_rows(rows):
    rows=iter(rows)
    if next(rows,None)!=['DATE','ENDUSE','DESCRIPTION','VALUE']:raise ValueError('Unexpected Census schema')
    series={}
    for date,code,description,value in rows:
        if not any((date,code,description,value)):continue
        month=month_key(date)
        if not re.fullmatch(r'\d{1,5}',code):raise ValueError('Invalid End-use code')
        amount=float(value)
        if not math.isfinite(amount):raise ValueError('Invalid amount')
        target=series.setdefault(code,{})
        if month in target:raise ValueError('Duplicate category-month')
        target[month]=int(amount) if amount.is_integer() else amount
    return series

def validate_snapshot(data,old=None):
    if old and data['latestMonth']<old['latestMonth']:raise ValueError('Source month moved backward')
    if not data['items']:raise ValueError('Empty source')
    required=set(months_ending(data['latestMonth']))
    for item in data['items']:
        if set(item['values'])!=required:raise ValueError('Incomplete monthly history: '+item['id'])
        if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in item['values'].values()):raise ValueError('Invalid series')

def build_snapshot(catalog,contents,old=None):
    all_series={flow:parse_rows(xlsx_rows(contents[flow])) for flow in ('import','export')}
    latest=min(max(ms for values in series.values() for ms in values) for series in all_series.values())
    items=[]; months=months_ending(latest)
    for item in catalog:
        series=all_series[item['flow']]
        values={m:sum(series[c][m] for c in item['codes']) for m in months if all(m in series.get(c,{}) for c in item['codes'])}
        items.append({'id':item['id'],'values':values})
    result={'latestMonth':latest,'checkedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'unit':'USD','adjustment':'seasonally adjusted','classification':'Census End-use','items':items,'sources':{f:SOURCE.format(f) for f in all_series}}
    validate_snapshot(result,old);return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-dir',type=Path);args=parser.parse_args()
    path=ROOT/'data/trade.json'
    try:
        old=json.loads(path.read_text()) if path.exists() else None
        contents={}
        for flow in ('import','export'):
            if args.source_dir:content=(args.source_dir/f'{flow}s_enduse.xlsx').read_bytes()
            else:
                request=urllib.request.Request(SOURCE.format(flow),headers={'User-Agent':'Mozilla/5.0 EarningSamurai public statistics tracker'})
                with urllib.request.urlopen(request,timeout=90) as r:content=r.read()
            contents[flow]=content
        catalog=json.loads((ROOT/'catalog.json').read_text())
        data=build_snapshot(catalog,contents,old)
        temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n');temp.replace(path)
        status={'ok':True,'checkedAt':data['checkedAt'],'latestMonth':data['latestMonth']}
        print(f"{len(data['items'])} series × 72 months; latest {data['latestMonth']}")
    except Exception as e:
        status={'ok':False,'checkedAt':dt.datetime.now(dt.timezone.utc).isoformat(),'error':type(e).__name__}
        (ROOT/'data/status.json').write_text(json.dumps(status)+'\n');raise
    (ROOT/'data/status.json').write_text(json.dumps(status)+'\n')
if __name__=='__main__':main()
