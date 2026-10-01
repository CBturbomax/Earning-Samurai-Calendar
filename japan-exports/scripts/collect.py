"""Japan MOF official e-Stat monthly exports; no API key or third-party estimates."""
import calendar,csv,datetime as dt,gzip,hashlib,html,io,json,re,sys,time,urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
BASE='https://www.e-stat.go.jp/stat-search/files?page=1&layout=datalist&toukei=00350300&tstat=000001013141&cycle=1&metadata=1&data=1&tclass3val=0'
ITEMS=[
 ('memory','메모리 반도체','반도체·전자',['854232'],'메모리 IC 전체. NAND·DRAM 등을 포함하며 특정 기업 매출과 다릅니다.'),
 ('cards','놀이용 카드','소비재',['950440'],'트레이딩 카드와 트럼프 등 포함. 포켓몬 카드만의 수출액은 아닙니다.'),
 ('semiequip','반도체 제조장비','반도체·전자',['848620'],'반도체 디바이스·집적회로 제조용 장비.'),
 ('wafer','반도체용 도핑 소재','반도체·전자',['381800'],'도핑한 원소·화합물. 실리콘 웨이퍼만의 통계는 아닙니다.'),
 ('mlcc','적층 세라믹 커패시터','반도체·전자',['853224'],'다층 세라믹 유전체의 고정식 커패시터.'),
 ('pcb','인쇄회로 기판','반도체·전자',['853400'],'인쇄회로 전체. ABF 기판만을 분리한 수치가 아닙니다.'),
 ('logic','프로세서·컨트롤러','반도체·전자',['854231'],'프로세서·컨트롤러 IC.'),
 ('watches','손목시계 등','소비재',['9101','9102'],'손목시계·회중시계 등. 일본에서 수출된 금액으로 해외 현지생산은 제외.'),
 ('beauty','화장품','소비재',['3304'],'메이크업·스킨케어·선케어·매니큐어 등의 제품.'),
 ('games','게임기·관련 제품','소비재',['950450'],'게임기와 해당 분류 부속품. 특정 플랫폼만의 수치는 아닙니다.'),
 ('robots','산업용 로봇','산업재',['847950'],'별도로 분류되지 않은 산업용 로봇.'),
 ('bearings','베어링','산업재',['8482'],'볼·롤러 베어링과 해당 부품. 혼합 수량 단위는 단가를 표시하지 않습니다.'),
 ('optics','광학 렌즈 등','산업재',['9002'],'장착된 렌즈·프리즘·거울 등 광학 요소.'),
 ('auto','승용차','산업재',['8703'],'주로 사람 수송용 승용차 등. 엔진·전기차를 포함합니다.'),
 ('auto_parts','자동차 부품','산업재',['8708'],'자동차용 부분품·부속품.'),
 ('sake','사케','소비재',['220600200'],'청주(Sake), 일본 수출통계 9자리 코드 기준.'),
]
MONTHS=list(calendar.month_abbr)[1:]
PREFIXES=tuple(p for item in ITEMS for p in item[3])
SECTIONS=('28-38','84-85','86-89','90-92','94-96','16-24')
COUNTRIES={'103':'한국','105':'중국','106':'대만','108':'홍콩','110':'베트남','111':'태국','112':'싱가포르','113':'말레이시아','117':'필리핀','118':'인도네시아','123':'인도','147':'아랍에미리트','205':'영국','207':'네덜란드','210':'프랑스','213':'독일','215':'스위스','218':'스페인','220':'이탈리아','304':'미국','302':'캐나다','305':'멕시코','410':'브라질','601':'호주','606':'뉴질랜드'}
def fetch(url):
 for attempt in range(3):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'JapanExportTracker/1.0 (official statistics research)'})
   with urllib.request.urlopen(req,timeout=75) as r: return r.read()
  except Exception:
   if attempt==2: raise
   time.sleep(2+attempt*3)
def listing(year=None,month=None,country=False):
 a,b=('000001013180','000001013181') if country else ('000001013183','000001013184')
 url=BASE+f'&tclass1={a}&tclass2={b}'
 if year: url+=f'&year={year}0'
 if month: url+='&month='+['110103','120406','230709','241012'][(month-1)//3]+f'{month:02}'
 return url,fetch(url).decode('utf-8')
def links_in(text):
 out=[]
 for block in re.findall(r'<article\b.*?</article>',text,re.S):
  ids=re.findall(r'file-download\?statInfId=(\d+)&(?:amp;)?fileKind=1',block)
  if not ids: continue
  label=re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',html.unescape(block))).strip()
  dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',label)
  out.append({'id':ids[0],'label':label,'published':dates[-1] if dates else None,'url':f'https://www.e-stat.go.jp/stat-search/file-download?statInfId={ids[0]}&fileKind=1'})
 return out
def parse_csv(text,year,through_month):
 reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
 if not reader.fieldnames or not {'HS','Year','Exp or Imp','Value-Jan'}.issubset(reader.fieldnames): raise ValueError('Invalid official CSV header')
 rows=[]; seen=set()
 for row in reader:
  if row['Exp or Imp']!='1' or int(row['Year'])!=year: raise ValueError('Wrong trade direction or year')
  hs=row['HS'].strip("' ")
  if re.fullmatch(r'\d{2}X{6}[A-Z]',hs): continue  # Official chapter-level confidential aggregate, not a specific HS
  if not re.fullmatch(r'\d{9}',hs): raise ValueError(f'Invalid HS: {hs}')
  country=row.get('Country','WORLD').strip()
  key=(hs,country)
  if key in seen: raise ValueError('Duplicate source row')
  seen.add(key)
  if not hs.startswith(PREFIXES): continue
  unit1=row.get('Unit1','').strip(); unit2=row.get('Unit2','').strip()
  unit=unit1 or unit2
  qcol='Quantity1-' if unit1 else 'Quantity2-'
  # Published months only: official future month columns contain zeros.
  for m in range(1,through_month+1):
   field='Value-'+MONTHS[m-1]; value=row[field].strip()
   if not value or not value.isdigit(): raise ValueError('Missing/invalid published value')
   q=row[qcol+MONTHS[m-1]].strip()
   rows.append(dict(hs=hs,country=country,month=f'{year}-{m:02}',value=int(value),quantity=int(q) if q.isdigit() and unit else None,unit=unit or None))
 return rows

def aggregate(records,prefixes):
 out={}
 for r in records:
  if not any(r['hs'].startswith(p) for p in prefixes):continue
  a=out.setdefault(r['country'],{}).setdefault(r['month'],dict(value=0,quantity=0,unit=r['unit']))
  a['value']+=r['value']
  if a['unit']!=r['unit'] or r['quantity'] is None or a['quantity'] is None:
   a['quantity']=None; a['unit']=None
  else:a['quantity']+=r['quantity']
 return out

def read_source(meta,year,month):
 path=ROOT/'raw'/f"{meta['id']}.csv";path.parent.mkdir(exist_ok=True)
 blob=fetch(meta['url'])  # Revalidate even when the publisher revises the same statInfId
 text=blob.decode('utf-8-sig')
 records=parse_csv(text,year,month)
 path.write_bytes(blob)
 return records,{**meta,'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob)}

def collect_year(args):
 year,month=args
 print(f'Collect {year}-{month:02}',flush=True)
 _,page=listing(year,month); meta=links_in(page)
 if len(meta)!=1: raise ValueError(f'{year}: expected one national CSV, got {len(meta)}')
 national,nsource=read_source(meta[0],year,month)
 _,page=listing(year,month,True)
 cmeta=[x for x in links_in(page) if any(s+'類' in x['label'] for s in SECTIONS)]
 if len(cmeta)!=len(SECTIONS): raise ValueError(f'{year}: incomplete country source sections ({len(cmeta)})')
 source=[nsource]; countries=[]
 for records,s in ThreadPoolExecutor(max_workers=3).map(lambda m:read_source(m,year,month),cmeta):
  countries.extend(records);source.append(s)
 result={}
 for item_id,name,group,prefixes,note in ITEMS:
  a=aggregate(national,prefixes);a.update(aggregate(countries,prefixes))
  # Reconcile independently sourced country totals against national total.
  for ym,n in a.get('WORLD',{}).items():
   total=sum(v.get(ym,{}).get('value',0) for c,v in a.items() if c!='WORLD')
   if total!=n['value']:raise ValueError(f'National/country mismatch {year} {item_id} {ym}: {n["value"]}!={total}')
  result[item_id]=a
 snapshot=dict(year=year,throughMonth=month,sources=source,series=result)
 print(f'Validated {year}: {len(national)} national + {len(countries)} country observations',flush=True)
 return snapshot

def validate_history(world,latest):
 y,m=map(int,latest.split('-'));end=y*12+m-1
 required={f'{i//12}-{i%12+1:02}' for i in range(end-71,end+1)}
 missing=required-set(world)
 if missing:raise ValueError(f'Missing published history: {sorted(missing)}')

def main():
 data=ROOT/'data';data.mkdir(exist_ok=True)
 now=dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
 status={'checkedAt':now.isoformat(),'ok':False}
 try:
  _,page=listing()
  ym=re.findall(r'year=(\d{4})0(?:&amp;|&).*?month=(\d{8})',html.unescape(page))
  if not ym:raise ValueError('No publication period discovered')
  latest=max((int(y),int(m[-2:])) for y,m in ym)
  # If local clock trails data publication, follow official publication metadata.
  year,month=latest
  snapshots=[]; jobs=[]
  for y in range(year-6,year+1):
   p=data/f'year-{y}.json.gz'
   # Re-read current/prior year on every run, older revisions weekly.
   cached=json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None
   if cached and y<year-1 and now.weekday()!=0: snapshots.append(cached)
   else:jobs.append((y,month if y==year else 12))
  for snap in ThreadPoolExecutor(max_workers=2).map(collect_year,jobs):snapshots.append(snap)
  snapshots.sort(key=lambda x:x['year'])
  items=[]
  for item_id,name,group,prefixes,note in ITEMS:
   series={}
   for snap in snapshots:
    for c,months in snap['series'][item_id].items():series.setdefault(c,{}).update(months)
   world=series.get('WORLD',{})
   validate_history(world,f'{year}-{month:02}')
   months=sorted(world)
   # Missing country rows imply no declared trade in a validated complete national source section.
   for c,values in series.items():
    if c=='WORLD':continue
    for m in months:
     if m not in values:values[m]={'value':0,'quantity':None,'unit':None}
   items.append(dict(id=item_id,name=name,group=group,hs=prefixes,note=note,series=series))
  doc=dict(title='일본 수출 트래커',latestMonth=f'{year}-{month:02}',checkedAt=now.isoformat(),valueUnit='천 엔',displayMonths=60,countries=COUNTRIES,items=items,sources=[s for snap in snapshots for s in snap['sources']],sourceHome='https://www.customs.go.jp/toukei/info/tsdl.htm',calendar='https://www.customs.go.jp/toukei/calendar/calend.htm')
  for snap in snapshots:(data/f'year-{snap["year"]}.json.gz').write_bytes(gzip.compress(json.dumps(snap,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
  target=data/'exports.json.gz';temp=data/'exports.tmp';temp.write_bytes(gzip.compress(json.dumps(doc,ensure_ascii=False,separators=(',',':')).encode(),mtime=0));temp.replace(target)
  status.update(ok=True,latestMonth=doc['latestMonth'],items=len(items))
  print(json.dumps(status,ensure_ascii=False))
 except Exception as e:
  status['error']=str(e);print(json.dumps(status,ensure_ascii=False),file=sys.stderr)
  (data/'status.json').write_text(json.dumps(status,ensure_ascii=False));raise
 (data/'status.json').write_text(json.dumps(status,ensure_ascii=False))
if __name__=='__main__':main()
