"""Collector regressions: bounded runs must make progress without losing evidence."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scrape_mon_ir as collector


class CollectorRecoveryTests(unittest.TestCase):
    def test_oldest_attempt_precedes_repeated_large_company(self):
        rec = {'attempts': {'1111': '2026-10-13T00:00:00+00:00'}, 'codes': {}}
        with patch.object(collector.monir, 'IR_SITES', {
                '1111': ('Large', 'https://large.jp', ''),
                '2222': ('Small', 'https://small.jp', '')}), \
                patch.object(collector, 'universe_codes', return_value=['1111','2222']), \
                patch.object(collector, 'known_names', return_value={}), \
                patch.object(collector, 'codes_with_numbers', return_value=set()), \
                patch.object(collector, 'cap_map', return_value={'1111':100,'2222':1}):
            self.assertEqual([x[0] for x in collector.iter_targets(rec, date(2026,10,13))],
                             ['2222','1111'])

    def test_pdf_budget_opportunity_rotates_even_when_all_html_was_attempted(self):
        rec = {'attempts': {'1111':'2026-10-13','2222':'2026-10-13'},
               'pdf_turns': {'1111':'2026-10-13'}, 'codes': {}}
        with patch.object(collector.monir, 'IR_SITES', {
                '1111': ('Large', 'https://large.jp', ''),
                '2222': ('Small', 'https://small.jp', '')}), \
                patch.object(collector, 'universe_codes', return_value=['1111','2222']), \
                patch.object(collector, 'known_names', return_value={}), \
                patch.object(collector, 'codes_with_numbers', return_value=set()), \
                patch.object(collector, 'cap_map', return_value={'1111':100,'2222':1}):
            self.assertEqual([x[0] for x in collector.iter_targets(rec, date(2026,10,13))],
                             ['2222','1111'])

    def pdf_run(self, rec, rows, today=date(2026,10,13), pdf=None):
        with patch.object(collector.monir, 'pdf_links', return_value=[
                ('https://example.jp/monthly.pdf','月次','2026-09-01')]), \
                patch.object(collector, 'get_bytes', return_value=b'pdf'), \
                patch.object(collector.montable, 'read', return_value={'rows':rows}), \
                patch.object(collector.time, 'sleep'):
            return collector.read_pdfs('', 'https://example.jp', pdf or {}, rec, today, 2)

    def test_successful_rolling_pdf_refreshes_but_not_twice_the_same_day(self):
        url = 'https://example.jp/monthly.pdf'
        rec = {'done':[url],'skip':{}}
        previous = {'2026-08':{'yoy':100,'doc':url}}
        pdf, _, _, looked = self.pdf_run(rec,[{'period':'2026-09','yoy':101}],pdf=previous)
        self.assertTrue(looked)
        self.assertEqual(pdf['2026-09']['yoy'],101)
        self.assertFalse(self.pdf_run(rec,[{'period':'2026-09','yoy':102}],pdf=pdf)[3])
        self.assertEqual(self.pdf_run(rec,[{'period':'2026-09','yoy':102}],
                            date(2026,10,14),pdf=pdf)[0]['2026-09']['yoy'],102)

    def test_successful_old_archival_pdf_remains_cached(self):
        url = 'https://example.jp/monthly.pdf'
        rec = {'done':[url],'skip':{}}
        old = {'2020-01':{'yoy':100,'doc':url}}
        pdf, _, _, looked = self.pdf_run(rec,[{'period':'2020-01','yoy':102}],pdf=old)
        self.assertFalse(looked)
        self.assertEqual(pdf['2020-01']['yoy'],100)

    def test_legacy_failed_pdf_is_retried_without_rule_bump(self):
        rec = {'skip': {'https://example.jp/monthly.pdf':'表なし'}, 'done': []}
        pdf, _, _, looked = self.pdf_run(rec, [{'period':'2026-09','yoy':101}])
        self.assertEqual(pdf.get('2026-09', {}).get('yoy'),101)
        self.assertTrue(looked)
        self.assertNotIn('https://example.jp/monthly.pdf', rec['skip'])

    def test_pdf_with_only_future_rows_not_permanently_done(self):
        rec = {'done': [], 'skip': {}}
        pdf, _, _, _ = self.pdf_run(rec, [{'period':'2026-10','yoy':101}])
        self.assertEqual(pdf,{})
        self.assertNotIn('https://example.jp/monthly.pdf',rec['done'])
        self.assertIn('https://example.jp/monthly.pdf',rec['skip'])

    def test_failed_pdf_retries_after_cooldown(self):
        url = 'https://example.jp/monthly.pdf'
        rec = {'done': [], 'skip': {}}
        self.pdf_run(rec, [])
        self.assertIsInstance(rec['skip'][url], dict)
        self.assertFalse(self.pdf_run(rec, [{'period':'2026-09','yoy':101}])[3])
        self.assertEqual(self.pdf_run(rec, [{'period':'2026-09','yoy':101}],
                                     date(2026,10,20))[0]['2026-09']['yoy'],101)

    def run_main(self, rec, fixed='https://example.jp/new'):
        with ExitStack() as stack:
            for name, value in [('load',rec), ('iter_targets',[('1111','Test','https://example.jp',fixed)]),
                                ('get','<p>月次情報</p>')]:
                stack.enter_context(patch.object(collector,name,return_value=value))
            stack.enter_context(patch.object(collector,'save'))
            stack.enter_context(patch.object(collector.time,'sleep'))
            stack.enter_context(patch.object(collector.monir,'read',return_value={}))
            stack.enter_context(patch.object(collector.monir,'archive_links',return_value=[]))
            stack.enter_context(patch.object(collector.monir,'pdf_links',return_value=[]))
            collector.main()
        return rec

    def test_new_fixed_source_bypasses_old_failure_and_is_recorded(self):
        rec = {'sites':{'1111':{'page':'https://example.jp/old',
                'parse_miss':'2099-01-01T00:00:00+00:00'}},'codes':{}}
        self.run_main(rec)
        self.assertEqual(rec['sites']['1111']['page'],'https://example.jp/new')
        self.assertIn('1111',rec.get('attempts',{}))

    def test_successful_fetch_clears_previous_fetch_failure_even_without_numbers(self):
        rec = {'sites':{'1111':{'page':'https://example.jp/new',
                              'fetch_failed':'2026-10-01T00:00:00+00:00'}},'codes':{}}
        self.run_main(rec)
        self.assertNotIn('fetch_failed',rec['sites']['1111'])
        self.assertIn('parse_miss',rec['sites']['1111'])

    def test_empty_current_parse_reports_failure_without_erasing_history(self):
        rec = {'sites':{},'codes':{'1111':{'months':{'2020-01':{'all':101}},
                'ts':'2020-02-01T00:00:00+00:00'}}}
        self.run_main(rec)
        self.assertIn('2020-01',rec['codes']['1111']['months'])
        self.assertIn('parse_miss',rec['sites']['1111'])

    def test_feed_list_recovers_html_and_pdf_when_landing_page_is_down(self):
        rec = {'sites':{},'codes':{}}
        feed1, feed2 = 'https://example.jp/feed1', 'https://example.jp/feed2'
        with ExitStack() as stack:
            for name, value in [('load',rec), ('iter_targets',[
                    ('1111','Test','https://example.jp','https://example.jp/monthly')])]:
                stack.enter_context(patch.object(collector,name,return_value=value))
            stack.enter_context(patch.object(collector,'save'))
            stack.enter_context(patch.object(collector.time,'sleep'))
            stack.enter_context(patch.object(collector,'get',side_effect=lambda url:
                {feed1:'table-feed',feed2:'pdf-feed'}.get(url,'')))
            stack.enter_context(patch.object(collector.monir,'IR_FEEDS',{'1111':[feed1,feed2]}))
            stack.enter_context(patch.object(collector.monir,'materialize_feed',
                                             side_effect=lambda text:text,create=True))
            stack.enter_context(patch.object(collector.monir,'read',side_effect=lambda page,today:
                {'2026-08':{'all':102}} if page=='table-feed' else {}))
            stack.enter_context(patch.object(collector.monir,'archive_links',return_value=[]))
            stack.enter_context(patch.object(collector.monir,'pdf_links',side_effect=lambda page,url:
                [('https://example.jp/report.pdf','月次','2026-10-01')] if page=='pdf-feed' else []))
            stack.enter_context(patch.object(collector,'get_bytes',return_value=b'pdf'))
            stack.enter_context(patch.object(collector.montable,'read',return_value={
                'rows':[{'period':'2026-09','yoy':103}]}))
            collector.main()
        self.assertEqual(rec['codes'].get('1111',{}).get('months',{}).get('2026-08',{}),
                         {'all':102,'doc':feed1})
        self.assertEqual(rec['codes']['1111']['pdf']['2026-09']['yoy'],103)

    def test_recent_fetch_does_not_make_old_history_fresh(self):
        stamp = datetime.now(timezone.utc).isoformat()
        rec = {'sites':{'1111':{'page':'https://example.jp/new'}},
               'codes':{'1111':{'months':{f'{year}-{month:02d}':{'all':101}
                   for year in (2019,2020) for month in range(1,13)},
                   'ts':stamp,'pdf_ts':stamp}}}
        self.run_main(rec)
        self.assertIn('1111',rec.get('attempts',{}))

    def test_coverage_reports_fetch_error_separately_from_parse_error(self):
        rec = {'sites':{'1111':{'page':'https://example.jp/monthly',
                              'fetch_failed':'2026-10-13T00:00:00+00:00'}},'codes':{}}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(collector,'COVERAGE',Path(temp)/'coverage.json'), \
                patch.object(collector,'universe_codes',return_value=['1111']), \
                patch.object(collector,'_load_codes_file',return_value=set()):
            collector.save_coverage(rec)
            got = json.loads(collector.COVERAGE.read_text())
        self.assertEqual(got['codes']['1111']['status'],'fetch_failed')

    def test_non_revenue_source_is_explicit_and_preserves_saved_history(self):
        rec = {'sites':{},'codes':{'1111':{'months':{'2020-01':{'all':101}}}}}
        with patch.object(collector.monir,'IR_NON_REVENUE',
                          {'1111':'Reports occupancy, not revenue'},create=True):
            self.run_main(rec)
            with tempfile.TemporaryDirectory() as temp, \
                    patch.object(collector,'COVERAGE',Path(temp)/'coverage.json'), \
                    patch.object(collector,'universe_codes',return_value=['1111']), \
                    patch.object(collector,'_load_codes_file',return_value=set()):
                collector.save_coverage(rec)
                got = json.loads(collector.COVERAGE.read_text())
        self.assertNotIn('1111',rec.get('attempts',{}))
        self.assertIn('2020-01',rec['codes']['1111']['months'])
        self.assertEqual(got['codes']['1111']['status'],'non_revenue_kpi')
        self.assertIn('occupancy',got['codes']['1111']['reason'])

    def test_coverage_distinguishes_stale_and_empty_records(self):
        rec = {'sites':{}, 'codes':{'1111':{'months':{'2020-01':{'all':101}}},'2222':{}}}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(collector,'COVERAGE',Path(temp)/'coverage.json'), \
                patch.object(collector,'universe_codes',return_value=['1111','2222']), \
                patch.object(collector,'_load_codes_file',return_value=set()):
            collector.save_coverage(rec)
            got = json.loads(collector.COVERAGE.read_text())
        self.assertEqual(got['codes']['1111']['status'],'stale')
        self.assertFalse(got['codes']['2222']['ir'])
        self.assertEqual(got['covered_any'],1)


if __name__ == '__main__':
    unittest.main()

class RuleUpgradeTests(unittest.TestCase):
    def test_parser_upgrade_preserves_confirmed_discontinuation(self):
        import tempfile
        from pathlib import Path
        import json
        from unittest.mock import patch
        import scrape_mon_ir as collector
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'record.json'
            path.write_text(json.dumps({'rv':0,'codes':{},'sites':{'3927':{'discontinued':'2023-05-02'}}}))
            with patch.object(collector,'OUT',path):
                self.assertEqual(collector.load()['sites']['3927']['discontinued'],'2023-05-02')
