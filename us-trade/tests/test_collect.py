import importlib.util, pathlib, unittest, io, zipfile
P=pathlib.Path(__file__).resolve().parents[1]/'scripts/collect.py'
class CollectorTests(unittest.TestCase):
 def load(self):
  self.assertTrue(P.exists(), 'US collector is not implemented')
  s=importlib.util.spec_from_file_location('collect',P); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
 def test_dates(self):
  m=self.load(); self.assertEqual(m.month_key('Aug-26'),'2026-08'); self.assertEqual(m.month_key('Jan-94'),'1994-01')
  with self.assertRaises(ValueError):m.month_key('bad')
 def test_missing_zero_and_duplicate(self):
  m=self.load(); rows=[['DATE','ENDUSE','DESCRIPTION','VALUE'],['Aug-26','21320','SEMICONDUCTORS','0'],['Jul-26','21320','SEMICONDUCTORS','100']]
  self.assertEqual(m.parse_rows(rows)['21320']['2026-08'],0)
  with self.assertRaises(ValueError):m.parse_rows(rows+[rows[-1]])
  with self.assertRaises(ValueError):m.parse_rows([rows[0],['Aug-26','21320','SEMICONDUCTORS','NA']])
 def test_schema_and_revisions(self):
  m=self.load()
  with self.assertRaises(ValueError):m.parse_rows([['DATE','ENDUSE','VALUE','DESCRIPTION']])
  old={'latestMonth':'2026-08'}
  with self.assertRaises(ValueError):m.validate_snapshot({'latestMonth':'2026-07','items':[]},old)

 def test_unselected_negative_adjustment_allowed(self):
  m=self.load(); rows=[['DATE','ENDUSE','DESCRIPTION','VALUE'],['Apr-20','42100','GEM DIAMONDS','-17074253']]
  self.assertEqual(m.parse_rows(rows)['42100']['2020-04'],-17074253)

 def test_sparse_xlsx_keeps_column_positions(self):
  m=self.load();b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Aug-26</t></si><si><t>21320</t></si></sst>')
   z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="D1"><v>100000000</v></c></row></sheetData></worksheet>')
  self.assertEqual(list(m.xlsx_rows(b.getvalue())),[['Aug-26','21320','','100000000']])

 def test_committed_snapshot_catalog_and_continuity(self):
  import json
  m=self.load();root=P.parents[1];d=json.loads((root/'data/trade.json').read_text());catalog=json.loads((root/'catalog.json').read_text())
  m.validate_snapshot(d)
  self.assertEqual({i['id'] for i in d['items']},{i['id'] for i in catalog})
  self.assertEqual(len(d['items']),len({i['id'] for i in d['items']}))
  self.assertEqual(d['unit'],'USD');self.assertEqual(d['adjustment'],'seasonally adjusted')
  for i in catalog:
   self.assertIn(i['flow'],['import','export']);self.assertIsNone(i['tradeShare']);self.assertTrue(i['companies'])
   self.assertTrue(all(c['url'].startswith('https://') for c in i['companies']))
  bad=dict(d);bad['items']=[dict(d['items'][0])];bad['items'][0]['values']=dict(bad['items'][0]['values']);bad['items'][0]['values'].pop(next(iter(bad['items'][0]['values'])))
  with self.assertRaises(ValueError):m.validate_snapshot(bad)

 def test_failure_preserves_cache_and_marks_status(self):
  import tempfile,json
  from unittest.mock import patch
  m=self.load()
  for prior in ['{"latestMonth":"2026-08"}','invalid JSON']:
   with tempfile.TemporaryDirectory() as td:
    root=pathlib.Path(td);(root/'data').mkdir();(root/'data/trade.json').write_text(prior);(root/'data/status.json').write_text('{"ok":true}')
    with patch.object(m,'ROOT',root),patch('sys.argv',['collect.py','--source-dir',str(root/'missing')]):
     with self.assertRaises(Exception):m.main()
    self.assertEqual((root/'data/trade.json').read_text(),prior)
    self.assertFalse(json.loads((root/'data/status.json').read_text())['ok'])
