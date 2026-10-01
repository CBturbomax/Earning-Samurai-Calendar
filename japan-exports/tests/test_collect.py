import unittest, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
try:
 from collect import parse_csv, aggregate
 import collect
except ImportError:
 parse_csv=aggregate=None
HEADER='Exp or Imp,Year,HS,Unit1,Unit2,Quantity1-Year,Quantity2-Year,Value-Year,Quantity1-Jan,Quantity2-Jan,Value-Jan,Quantity1-Feb,Quantity2-Feb,Value-Feb\n'
class CollectorTests(unittest.TestCase):
 def test_monthly_not_annual_and_future_excluded(self):
  self.assertTrue(callable(parse_csv),'CSV parser must exist')
  rows=parse_csv(HEADER+"1,2026,'854232100',  ,NO,0,30,900,0,10,100,0,20,800\n",2026,1)
  self.assertEqual(len(rows),1)
  self.assertEqual(rows[0]['value'],100)
  self.assertEqual(rows[0]['month'],'2026-01')
 def test_sum_subcodes_without_duplication(self):
  self.assertTrue(callable(aggregate),'aggregator must exist')
  records=[dict(hs='854232100',month='2026-01',country='WORLD',value=100,quantity=10,unit='NO'),dict(hs='854232900',month='2026-01',country='WORLD',value=200,quantity=20,unit='NO')]
  a=aggregate(records,['854232','854232100'])
  self.assertEqual(a['WORLD']['2026-01']['value'],300)
  self.assertEqual(a['WORLD']['2026-01']['quantity'],30)
 def test_mixed_units_disable_quantity(self):
  self.assertTrue(callable(aggregate),'aggregator must exist')
  records=[dict(hs='910100000',month='2026-01',country='WORLD',value=100,quantity=10,unit='NO'),dict(hs='910200000',month='2026-01',country='WORLD',value=200,quantity=20,unit='KG')]
  self.assertIsNone(aggregate(records,['91'])['WORLD']['2026-01']['quantity'])
 def test_official_confidential_chapter_rows_are_not_assigned_to_specific_hs(self):
  row="1,2026,'85XXXXXXA',  ,NO,0,10,100,0,10,100,0,0,0\n"
  self.assertEqual(parse_csv(HEADER+row,2026,1),[])
 def test_history_must_include_latest_month_without_gaps(self):
  check=getattr(collect,'validate_history',None)
  self.assertTrue(callable(check))
  old={f'{y}-{m:02}':{} for y in range(2020,2026) for m in range(1,13)}
  with self.assertRaises(ValueError):check(old,'2026-08')
  full={f'{y}-{m:02}':{} for y in range(2020,2027) for m in range(1,13)}
  check(full,'2026-08')
  del full['2023-04']
  with self.assertRaises(ValueError):check(full,'2026-08')
 def test_invalid_csv_rejected(self):
  self.assertTrue(callable(parse_csv),'CSV parser must exist')
  with self.assertRaises(ValueError): parse_csv('<html>blocked</html>',2026,1)
 def test_duplicate_rows_rejected(self):
  self.assertTrue(callable(parse_csv),'CSV parser must exist')
  row="1,2026,'854232100',  ,NO,0,10,100,0,10,100,0,0,0\n"
  with self.assertRaises(ValueError): parse_csv(HEADER+row+row,2026,1)
if __name__=='__main__': unittest.main()
