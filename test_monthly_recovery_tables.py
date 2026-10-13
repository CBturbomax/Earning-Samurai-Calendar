import unittest
from datetime import date
import monir

class ExplicitMonthTests(unittest.TestCase):
    def test_explicit_years_survive_old_and_descending_tables(self):
        page = '<table><tr><th></th><th>2025/12</th><th>2025/11</th><th>2025/10</th></tr><tr><th>全店売上高</th><td>104.7</td><td>107.0</td><td>107.1</td></tr></table>'
        self.assertEqual(monir.read(page,date(2026,10,13)),{'2025-12':{'all':104.7},'2025-11':{'all':107.0},'2025-10':{'all':107.1}})

    def test_record_rows_select_sales_ratio_not_amount_or_customer_count(self):
        page='''<table><tr><td colspan="2"></td><td>売上高</td><td>売上高前年同月比</td><td>客数前年同月比</td></tr>
<tr><td rowspan="2">2026/09</td><td>全店</td><td>19,703 百万円</td><td>107.9%</td><td>105.6%</td></tr>
<tr><td>既存店</td><td>18,676 百万円</td><td>102.6%</td><td>100.4%</td></tr></table>'''
        self.assertEqual(monir.read(page,date(2026,10,13)),{'2026-09':{'all':107.9,'same':102.6}})
        self.assertEqual(monir.read(page.replace('売上高前年同月比','客数前年同月比'),date(2026,10,13)),{})

    def test_record_rows_do_not_take_cumulative_or_conflicting_subtotals(self):
        page='''<table><tr><th>年月</th><th>区分</th><th>売上高前年比</th></tr>
<tr><td>2026/09</td><td>全店累計</td><td>105%</td></tr>
<tr><td>2026/10</td><td>全店</td><td>109%</td></tr></table>'''
        self.assertEqual(monir.read(page,date(2026,10,13)),{})

class FeedTests(unittest.TestCase):
    def test_xj_feed_retains_publication_day_and_only_monthly_pdf(self):
        import json
        raw=json.dumps({'items':[{'title':'月次売上速報（9月）','publishDate':'2026/10/05 16:10:00','files':[{'url':'https://example.jp/opaque.pdf'}]}, {'title':'決算短信','publishDate':'2026/10/01','files':[{'url':'https://example.jp/result.pdf'}]}]})
        page=monir.materialize_feed(raw)
        self.assertEqual(monir.pdf_links(page,'https://example.jp/'),[('https://example.jp/opaque.pdf','月次売上速報（9月）','2026-10-05')])

    def test_eir_link_and_embedded_table_materialized_without_running_javascript(self):
        import json
        raw='eolparts_announcement_0('+json.dumps({'item':[{'title':'月次情報','group_name':'2027年3月期','text':'<table><tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr><tr><th>全店売上高</th><td>101</td><td>102</td><td>103</td></tr></table>'}]})+');'
        self.assertEqual(monir.read(monir.materialize_feed(raw),date(2026,10,13))['2026-04']['all'],101)

    def test_document_write_literal_supported_without_execution(self):
        page="document.write('<h2>2026年度</h2><table><tr><th></th><th>4月</th><th>5月</th><th>6月</th></tr><tr><th>全店売上高</th><td>101</td><td>102</td><td>103</td></tr></table>');"
        self.assertEqual(monir.read(monir.materialize_feed(page),date(2026,10,13))['2026-04']['all'],101)

class SplitHalfYearTests(unittest.TestCase):
    def test_scope_total_and_second_half_single_published_month(self):
        page='''<h2>2027年2月期 月次売上高前年比</h2><table>
<tr><th></th><th></th><th>3月</th><th>4月</th><th>5月</th></tr>
<tr><th rowspan="2">全<br>店</th><th>小売店</th><td>119</td><td>116</td><td>124</td></tr>
<tr><th>計</th><td>136.2</td><td>134.6</td><td>141</td></tr>
<tr><th></th><th></th><th>9月</th><th>10月</th><th>11月</th></tr>
<tr><th rowspan="2">全<br>店</th><th>小売店</th><td>108.2</td><td></td><td></td></tr>
<tr><th>計</th><td>109</td><td></td><td></td></tr></table>'''
        self.assertEqual(monir.read(page,date(2026,10,13)),{'2026-03':{'all':136.2},'2026-04':{'all':134.6},'2026-05':{'all':141},'2026-09':{'all':109}})

class VerticalDateTests(unittest.TestCase):
    def test_explicit_old_vertical_year_is_never_rebased_to_today(self):
        page='''<table><tr><th>年月</th><th>全店売上高前年比</th><th>既存店売上高前年比</th></tr>
<tr><td>2020年7月</td><td>101</td><td>102</td></tr>
<tr><td>2020年8月</td><td>103</td><td>104</td></tr>
<tr><td>2020年9月</td><td>105</td><td>106</td></tr></table>'''
        got=monir.read(page,date(2026,10,13))
        self.assertEqual(sorted(got),['2020-07','2020-08','2020-09'])
        self.assertEqual(got['2020-09'],{'all':105,'same':106})

class SplitFiscalYearTests(unittest.TestCase):
    def test_second_month_block_carries_calendar_rollover(self):
        page='<h2>2025年度 月次売上高</h2><table><tr><th></th><th>7月</th><th>8月</th><th>12月</th></tr><tr><th>全店売上高</th><td>101</td><td>101</td><td>101</td></tr><tr><th></th><th>1月</th><th>2月</th><th>6月</th></tr><tr><th>全店売上高</th><td>102</td><td>102</td><td>102</td></tr></table>'
        got=monir.read(page,date(2026,10,13))
        self.assertEqual(got['2026-01'],{'all':102})
        self.assertNotIn('2025-01',got)

class PartialYearTests(unittest.TestCase):
    def test_mixed_explicit_year_cannot_be_rebased_to_today(self):
        page='<table><tr><th></th><th>2020年7月</th><th>8月</th><th>9月</th></tr><tr><th>全店売上高</th><td>101</td><td>102</td><td>103</td></tr></table>'
        self.assertEqual(monir.read(page,date(2026,10,13)),{})

class OctoberFiscalYearTests(unittest.TestCase):
    def test_rollover_inside_first_block_carries_to_next_block(self):
        page='<h2>2025年度 月次売上高</h2><table>'
        for months,v in [([10,11,12,1,2,3],101),([4,5,6,7,8,9],102)]:
            page+='<tr><th></th>'+''.join(f'<th>{m}月</th>' for m in months)+'</tr><tr><th>全店売上高</th>'+''.join(f'<td>{v}</td>' for _ in months)+'</tr>'
        got=monir.read(page+'</table>',date(2026,10,13))
        self.assertEqual(got.get('2026-04'),{'all':102})
        self.assertNotIn('2025-04',got)

class IrPocketTests(unittest.TestCase):
    def test_protocol_relative_links_and_published_date(self):
        import json
        feed='// public feed\nwindow.ir20handler('+json.dumps({'item':[{'title':'9月売上速報','published':'2026-10-01 16:00:00','link':'//pdf.irpocket.com/C3099/report.pdf'}]})+');'
        self.assertEqual(monir.pdf_links(monir.materialize_feed(feed),'https://example.jp/'),[('https://pdf.irpocket.com/C3099/report.pdf','9月売上速報','2026-10-01')])

class PdfDownloadRouteTests(unittest.TestCase):
    def test_query_parameters_identify_distinct_documents(self):
        page='''<a href="https://ircms.irstreet.com/data_file.php?data=1&amp;filename=pdf_file.pdf">2026年8月度 売上高</a><a href="https://ircms.irstreet.com/data_file.php?data=2&amp;filename=pdf_file.pdf">2026年9月度 売上高</a>'''
        urls=[u for u,_,_ in monir.pdf_links(page,'https://example.jp/')]
        self.assertEqual(urls,['https://ircms.irstreet.com/data_file.php?data=1&filename=pdf_file.pdf','https://ircms.irstreet.com/data_file.php?data=2&filename=pdf_file.pdf'])

    def test_official_extensionless_pdf_routes(self):
        page='<a href="/ir/media-download/3318/hash/PDF/">2026年9月度 月次売上高</a><a href="/ir/library/prospectus?file_id=1&amp;id=20&amp;mode=download">2026年8月度 月次売上高</a>'
        self.assertEqual(len(monir.pdf_links(page,'https://example.jp/')),2)
