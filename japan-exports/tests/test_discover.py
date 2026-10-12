import sys
import unittest
import tempfile
import json
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import discover
from discover import parse_national, signal, screen, months_ending

HEADER='Exp or Imp,Year,HS,Value-Jan,Value-Feb\n'

class DiscoveryTests(unittest.TestCase):
    def test_full_universe_and_both_levels(self):
        values, confidential, count = parse_national(HEADER+"1,2026,'010121100',5,7\n1,2026,'010121900',3,4\n1,2026,'85XXXXXXA',9,0\n", 2026, 2)
        self.assertEqual(values['0101']['2026-01'],8)
        self.assertEqual(values['010121']['2026-02'],11)
        self.assertEqual((confidential,count),(1,3))
    def test_future_columns_not_required(self):
        self.assertEqual(parse_national(HEADER+"1,2026,'010121100',5,\n",2026,1)[0]['0101'],{'2026-01':5})
    def test_invalid_or_duplicate_source_rejected(self):
        row="1,2026,'010121100',5,7\n"
        for text in (HEADER+row+row, HEADER+row.replace(',5,7',',,7'), '<html>error</html>'):
            with self.assertRaises(ValueError):parse_national(text,2026,2)
    def test_peak_and_near_peak(self):
        self.assertTrue(signal([10]*59+[20])['record'])
        v=[100]*60;v[-1]=90
        self.assertFalse(signal(v)['record'])
        self.assertAlmostEqual(signal(v)['ratio'],96.6666667)
        self.assertIsNone(signal([0]*60))
        self.assertIsNone(signal([100]*57+[10]*3))
    def test_zero_only_after_validated_source_and_revision_exclusion(self):
        snaps=[dict(year=y,throughMonth=12,values={'0101':{f'{y}-{m:02}':1 for m in range(1,13)},'0302':{f'{y}-{m:02}':1 for m in range(1,13)}}) for y in range(2022,2027)]
        del snaps[0]['values']['0101']
        result=screen(snaps,'2026-12',{'0101':'Horses','0302':'Fish'},{'0101':'Horses'})
        self.assertEqual(result['eligibleItems'],1)
        self.assertEqual(result['excludedItems'],1)
        self.assertEqual(result['items'][0]['values']['2022-01'],0)
        with self.assertRaises(ValueError):screen(snaps[1:],'2026-12',{'0101':'Horses'},{'0101':'Horses'})
        del snaps[1]['values']['0101']['2023-01']
        with self.assertRaises(ValueError):screen(snaps,'2026-12',{'0101':'Horses'},{'0101':'Horses'})
    def test_changed_nomenclature_excluded(self):
        snaps=[dict(year=y,throughMonth=12,values={'0101':{f'{y}-{m:02}':1 for m in range(1,13)}}) for y in range(2022,2027)]
        result=screen(snaps,'2026-12',{'0101':'Horses and asses'},{'0101':'Horses'})
        self.assertEqual(result['eligibleItems'],0)
        self.assertEqual(result['excludedItems'],1)

    def test_failed_fetch_preserves_snapshot_and_records_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            artifact=root/'data'/'discovery.json';artifact.write_text('{"latestMonth":"2026-08"}')
            before=artifact.read_bytes()
            with patch.object(discover,'ROOT',root), patch.object(discover,'listing',side_effect=ConnectionError('fetch unavailable')):
                with self.assertRaises(ConnectionError): discover.main()
            self.assertEqual(artifact.read_bytes(),before)
            status=json.loads((root/'data'/'discovery-status.json').read_text())
            self.assertFalse(status['ok'])
            self.assertEqual(status['errorType'],'ConnectionError')

    def test_backward_publication_month_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'data').mkdir()
            artifact=root/'data'/'discovery.json';artifact.write_text('{"latestMonth":"2026-08"}')
            before=artifact.read_bytes()
            with patch.object(discover,'ROOT',root):
                with self.assertRaises(ValueError): discover.write_snapshot({'latestMonth':'2026-07'})
            self.assertEqual(artifact.read_bytes(),before)

    def test_exact_sixty_month_window(self):
        months=months_ending('2026-08')
        self.assertEqual((len(months),months[0],months[-1]),(60,'2021-09','2026-08'))

if __name__=='__main__':unittest.main()
