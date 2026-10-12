import importlib.util
import pathlib
import sys
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

class DiscoveryTests(unittest.TestCase):
    def load(self):
        path = SCRIPTS / 'discover.py'
        self.assertTrue(path.exists(), 'Broad Census discovery is not implemented')
        spec = importlib.util.spec_from_file_location('us_discover', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def rows(self, series):
        import collect
        months = collect.months_ending('2026-08', 60)
        import datetime
        return [['DATE','ENDUSE','DESCRIPTION','VALUE']] + [
            [datetime.datetime.strptime(month, '%Y-%m').strftime('%b-%y'), code, 'TEST '+code, str(value)]
            for code, values in series.items() for month, value in zip(months, values) if value is not None]

    def test_scans_both_flows_without_catalog_or_aggregates(self):
        m = self.load()
        rows = self.rows({'1':[100]*60, '12345':list(range(1,61)), '54321':[100]*57+[95]*3, '22222':[100]*57+[94]*3})
        result = m.build_from_rows({'import':rows, 'export':rows})
        self.assertEqual(result['totalItems'],6)
        self.assertEqual(result['eligibleItems'],6)
        self.assertEqual(result['excludedItems'],0)
        self.assertEqual({i['id'] for i in result['items']}, {'us-import-12345','us-export-12345','us-import-54321','us-export-54321'})
        record = next(i for i in result['items'] if i['id']=='us-import-12345')
        self.assertTrue(record['record'])
        self.assertEqual(record['values']['2026-08'],60)
        self.assertAlmostEqual(record['growth'], (177/141-1)*100)
        near = next(i for i in result['items'] if i['code']=='54321')
        self.assertFalse(near['record'])
        self.assertAlmostEqual(near['ratio'],95)

    def test_excludes_incomplete_negative_nonfinite_and_zero_peak(self):
        m = self.load()
        rows = self.rows({'11111':[100]*59+[None], '22222':[100]*59+[-1], '33333':[100]*59+[float('nan')], '44444':[0]*60, '55555':[100]*60})
        result=m.build_from_rows({'import':rows,'export':self.rows({'55555':[100]*60})})
        self.assertEqual(result['totalItems'],6)
        self.assertEqual(result['eligibleItems'],3)
        self.assertEqual(result['excludedItems'],3)
        self.assertEqual(len(result['items']),2)
        self.assertEqual(result['coverage']['import']['exclusionReasons'], {'incomplete':1,'invalidValue':2})

    def test_committed_snapshot_has_full_history_and_honest_coverage(self):
        import json
        import math
        import collect
        data = json.loads((SCRIPTS.parent / 'data/discovery.json').read_text())
        self.assertGreater(data['totalItems'], 200)
        self.assertEqual(data['totalItems'], data['eligibleItems'] + data['excludedItems'])
        self.assertEqual(data['totalItems'], sum(c['totalItems'] for c in data['counts'].values()))
        self.assertEqual(len(data['items']), len({i['id'] for i in data['items']}))
        for item in data['items']:
            self.assertEqual(list(item['values']), collect.months_ending(data['latestMonth'], 60))
            self.assertTrue(all(math.isfinite(v) and v >= 0 for v in item['values'].values()))
            self.assertTrue(item['record'] or item['ratio'] >= 95)
            self.assertTrue(item['sourceName'])
            self.assertTrue(item['name'])

    def test_failure_preserves_snapshot_and_marks_discovery_status(self):
        import json
        import tempfile
        from unittest.mock import patch
        m = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'data').mkdir()
            path = root / 'data/discovery.json'
            original = '{"latestMonth":"2026-08"}'
            path.write_text(original)
            with patch.object(m, 'ROOT', root), patch('sys.argv', ['discover.py', '--source-dir', str(root/'missing')]):
                with self.assertRaises(FileNotFoundError):
                    m.main()
            self.assertEqual(path.read_text(), original)
            status_path = root / 'data/discovery-status.json'
            self.assertTrue(status_path.exists(), 'Discovery failures need their own status artifact')
            status = json.loads(status_path.read_text())
            self.assertFalse(status['ok'])
            self.assertEqual(status['error'], 'FileNotFoundError')
            self.assertTrue(status['checkedAt'])

    def test_backward_snapshot_is_rejected_and_status_updated(self):
        import json
        import tempfile
        from unittest.mock import patch
        m = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'data').mkdir()
            for flow in ('import','export'):
                (root / (flow+'s_enduse.xlsx')).write_bytes(b'test')
            path = root / 'data/discovery.json'
            original = '{"latestMonth":"2026-09"}'
            path.write_text(original)
            with patch.object(m, 'ROOT', root), patch.object(m, 'xlsx_rows', return_value=[]), patch.object(m, 'build_from_rows', return_value={'latestMonth':'2026-08'}), patch('sys.argv', ['discover.py', '--source-dir', str(root)]):
                with self.assertRaisesRegex(ValueError, 'backward'):
                    m.main()
            self.assertEqual(path.read_text(), original)
            self.assertEqual(json.loads((root/'data/discovery-status.json').read_text())['error'], 'ValueError')

    def test_duplicate_month_fails_instead_of_double_counting(self):
        m=self.load(); rows=self.rows({'12345':[100]*60})
        with self.assertRaises(ValueError):
            m.build_from_rows({'import':rows+[rows[-1]], 'export':rows})

if __name__=='__main__': unittest.main()
