import unittest
import monir
from datetime import date
import tempfile
import json
from pathlib import Path
from unittest.mock import patch
import build


class ReviewedSeriesTests(unittest.TestCase):
    def run_board(self, raw):
        company = {'code': '9999', 'name': 'Test', 'originalName': 'Test',
                   'scope': 'domestic', 'defaultMetric': 'all',
                   'months': {p: {'all': {'ratio': v, 'source': 'https://example.jp/monthly'}}
                              for p, v in [('2026-04', 101), ('2026-05', 102), ('2026-06', 103)]}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/'data').mkdir()
            (root/'data/monthly_reviewed_jp.json').write_text(json.dumps({'companies':[company], 'endMonth':'2026-06'}))
            (root/'data/monthly_ir_jp.json').write_text(json.dumps({'codes': {'9999': raw}}))
            with patch.object(build, 'HERE', root):
                return build.apply_reviewed_monthly([], {})[1]['9999']

    def test_all_store_series_updates_even_when_default_auto_series_is_same_store(self):
        raw = {'page':'https://example.jp/monthly', 'months': {
            '2026-04': {'all':101, 'same':90}, '2026-05': {'all':102, 'same':91},
            '2026-06': {'all':103, 'same':92},
            '2026-07': {'all':104, 'same':93, 'doc':'https://example.jp/monthly/2026'}}}
        got = self.run_board(raw)
        self.assertEqual(got['latest']['period'], '2026-07')
        self.assertEqual(got['latest']['yoy'], 104)
        self.assertEqual(got['evidence']['2026-07']['source'], 'https://example.jp/monthly/2026')

    def test_conflicting_or_different_metric_is_not_joined(self):
        raw = {'page':'https://example.jp/monthly', 'months': {
            '2026-04': {'all':101}, '2026-05': {'all':999}, '2026-06': {'all':103},
            '2026-07': {'all':104}}}
        self.assertEqual(self.run_board(raw)['latest']['period'], '2026-06')
        raw['months'] = {p: {'same':v} for p,v in [('2026-04',101),('2026-05',102),('2026-06',103),('2026-07',104)]}
        self.assertEqual(self.run_board(raw)['latest']['period'], '2026-06')


class ArchiveDiscoveryTests(unittest.TestCase):
    def test_public_ir_feed_uses_fiscal_group_and_ignores_non_monthly_items(self):
        def table(value):
            return '<table><tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr><tr><td>全店売上高</td>'+''.join('<td>'+str(value)+'</td>' for _ in range(3))+'</tr></table>'
        payload = {'item':[
            {'title':'月次情報', 'group_name':'2027年3月期', 'text':table(105)},
            {'title':'月次情報', 'group_name':'2026年3月期', 'text':table(101)},
            {'title':'決算説明会', 'group_name':'2025年3月期', 'text':table(999)}]}
        source = 'eolparts_announcement_0('+json.dumps(payload)+');'
        got = monir.read(source, date(2026,10,1))
        self.assertEqual(got.get('2026-04'), {'all':105})
        self.assertEqual(got.get('2025-04'), {'all':101})
        self.assertNotIn('2024-04',got)

    def test_total_sales_rows_win_over_directly_operated_subtotal(self):
        page = '''<h2>2027年2月期 月次売上前年比</h2><table>
        <tr><th></th><th></th><th>2026年3月</th><th>2026年4月</th><th>2026年5月</th></tr>
        <tr><td>直営計</td><td>全店</td><td>101.4</td><td>101.6</td><td>105.3</td></tr>
        <tr><td>合計</td><td>全店</td><td>101.7</td><td>102.4</td><td>106.9</td></tr></table>'''
        self.assertEqual(monir.read(page, date(2026, 10, 1)), {
            '2026-03': {'all': 101.7}, '2026-04': {'all': 102.4}, '2026-05': {'all': 106.9}})

    def test_bilingual_month_headers_with_growth_rates(self):
        page = '''<h3>2026年度 売上高前年同月比較表</h3><table>
        <tr><th>グループ合計</th><th>4月April</th><th>5月May</th><th>6月June</th></tr>
        <tr><td>全店 単月</td><td>5.9</td><td>7.3</td><td>-1.9</td></tr></table>'''
        self.assertEqual(monir.read(page, date(2026, 10, 1)), {
            '2026-04': {'all': 105.9}, '2026-05': {'all': 107.3}, '2026-06': {'all': 98.1}})

    def test_two_year_rolling_table_keeps_both_septembers(self):
        page = '''<table><tr><th>2025-2026年度/月度</th>
        <th>9月</th><th>10月</th><th>11月</th><th>12月</th><th>1月</th><th>2月</th>
        <th>3月</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th><th>9月</th></tr>
        <tr><td>全店</td><td>104.1</td><td>101.6</td><td>103.5</td><td>104.3</td>
        <td>102.2</td><td>102.7</td><td>104.0</td><td>103.1</td><td>105.2</td>
        <td>104.4</td><td>102.0</td><td>103.2</td><td>107.1</td></tr></table>'''
        got = monir.read(page, date(2026, 10, 1))
        self.assertEqual(len(got), 13)
        self.assertEqual(got['2025-09']['all'], 104.1)
        self.assertEqual(got['2026-09']['all'], 107.1)

    def test_year_select_urls_are_archives_but_numeric_widget_ids_are_not(self):
        page = '''<select><option value="/ir/performance/sales.html">2027年3月期</option>
        <option value="/ir/performance/sales_2026.html">2026年3月期</option>
        <option value="/ir/performance/sales_2025.html">2025年3月期</option>
        <option value="1">2024年3月期</option>
        <option value="https://other.example/archive/">2023年度</option></select>'''
        self.assertEqual(monir.archive_links(page, 'https://www.nitorihd.co.jp/ir/performance/sales.html'), [
            'https://www.nitorihd.co.jp/ir/performance/sales_2026.html',
            'https://www.nitorihd.co.jp/ir/performance/sales_2025.html'])

    def test_query_based_years_are_distinct_archives(self):
        page = '''<a href="?y=2026">2026年度</a><a href="?y=2025">2025年度</a>
        <a href="?y=2024">2024年度</a>'''
        self.assertEqual(monir.archive_links(page, 'https://example.jp/monthly/?y=2026'), [
            'https://example.jp/monthly/?y=2025', 'https://example.jp/monthly/?y=2024'])


if __name__ == '__main__':
    unittest.main()
