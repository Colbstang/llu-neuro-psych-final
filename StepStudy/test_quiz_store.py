import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from StepStudy.quiz_store import QuizStore, grade, quiz_catalog


def synthetic_data():
    return {
        'review_games': {'bugs': {'categories': ['Gram / stain', 'Shape', 'Virulence'], 'cases': [
            {'id': 'bug-a', 'name': 'Organism A', 'row_index': 0, 'traits': {'Gram / stain': ['Positive'], 'Shape': ['Coccus'], 'Virulence': ['Capsule', 'Toxin']}},
            {'id': 'bug-b', 'name': 'Organism B', 'row_index': 1, 'traits': {'Gram / stain': ['Negative'], 'Shape': ['Rod'], 'Virulence': ['Capsule']}},
        ]}},
        'comparison_sheets': [
            {'id': 'bugs', 'columns': ['Organism', 'Clinical pattern', 'Microbiology / CSF', 'Transmission'], 'rows': [
                {'cells': ['Organism A', '<p>Pattern A</p>', '<p>Test A &amp; culture</p>', '<p>Not specified</p>']},
                {'cells': ['Organism B', '<p>Pattern B</p>', '<p>Test B</p>', '<p>Contact</p>']},
            ]},
            {'id': 'pathology', 'columns': ['Condition', 'Clinical pattern', 'Mechanism', 'Discriminator'], 'rows': [
                {'cells': ['Condition A', '<p>Finding A</p>', '<p>Mechanism A</p>', '']},
            ]},
        ],
    }


class QuizStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.sqlite'
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.store = QuizStore(self.path, clock=lambda: self.now)
        self.catalog = quiz_catalog(synthetic_data(), 'bugs')

    def tearDown(self):
        self.temp.cleanup()

    def start(self, ids=None, categories=None):
        return self.store.action(self.catalog, {'action': 'start', 'round_ids': ids or ['bug-a'], 'categories': categories or self.catalog['categories']})['session']

    def action(self, session, action, **extra):
        return self.store.action(self.catalog, {'action': action, 'session_id': session['id'], 'revision': session['revision'], **extra})

    def answers(self):
        return self.catalog['cases'][0]['traits']

    def test_every_populated_source_column_including_microbiology_is_quizzed(self):
        record = self.catalog['cases'][0]
        self.assertEqual(record['traits']['Source detail · Microbiology / CSF'], ['Test A & culture'])
        self.assertIn('Source detail · Transmission', record['omitted'])
        self.assertIn('Virulence', self.catalog['categories'])
        visual = synthetic_data()
        visual['review_games']['bugs']['cases'][0]['figures'] = [{'src': '/api/course/asset/opaque', 'source_book': 'fixture-book'}]
        self.assertEqual(quiz_catalog(visual, 'bugs')['cases'][0]['figures'][0]['src'], '/api/course/asset/opaque')
        path = quiz_catalog(synthetic_data(), 'pathology')
        self.assertEqual(path['cases'][0]['id'], 'pathology-0')
        self.assertEqual(path['cases'][0]['traits']['Source detail · Mechanism'], ['Mechanism A'])

    def test_all_traits_are_required_and_shared_trait_is_still_valid(self):
        result = grade(self.catalog['cases'][0], ['Virulence'], {'Virulence': ['Capsule']})
        self.assertEqual(result['correct'], 0)
        self.assertEqual(result['details'][0]['missing'], ['Toxin'])
        complete = grade(self.catalog['cases'][0], ['Virulence'], {'Virulence': ['Capsule', 'Toxin']})
        self.assertTrue(complete['perfect'])

    def test_draft_pause_and_restart_retain_answers_without_attempt_or_due_date(self):
        session = self.start()
        session = self.action(session, 'draft', selections={'Shape': ['Coccus']}, category_at=2)['session']
        session = self.action(session, 'pause')['session']
        reopened = QuizStore(self.path, clock=lambda: self.now).snapshot(self.catalog)
        self.assertEqual(reopened['session']['selections'], {'Shape': ['Coccus']})
        self.assertEqual(reopened['session']['category_at'], 2)
        self.assertTrue(reopened['session']['paused'])
        self.assertEqual(reopened['summary']['attempts'], 0)
        self.assertEqual(reopened['summary']['due'], 0)
        self.assertEqual(self.action(session, 'resume')['session']['paused'], False)

    def test_explicit_check_is_idempotent_and_dates_each_populated_category(self):
        session = self.start()
        checked = self.action(session, 'check', selections=self.answers(), category_at=0)
        self.assertEqual(checked['summary']['attempts'], 1)
        self.assertEqual(checked['summary']['complete'], 1)
        self.assertEqual(checked['items'][0]['reviewed_categories'], 5)
        self.assertNotIn('Source detail · Transmission', checked['items'][0]['unseen_categories'])
        repeated = self.action(session, 'check', selections={})
        self.assertEqual(repeated['summary']['attempts'], 1)
        self.assertEqual(repeated['session']['grade'], checked['session']['grade'])
        self.assertEqual(repeated['anki_ratings'], 0)
        self.now += timedelta(days=3)
        self.assertEqual(self.store.snapshot(self.catalog)['summary']['due'], 1)

    def test_wrong_and_skipped_entries_remain_incomplete(self):
        session = self.start(ids=['bug-a', 'bug-b'], categories=['Shape', 'Virulence'])
        checked = self.action(session, 'check', selections={'Shape': ['Rod'], 'Virulence': ['Capsule']})
        self.assertEqual(checked['session']['grade']['correct'], 0)
        self.assertEqual(checked['summary']['complete'], 0)
        advanced = self.action(checked['session'], 'next')
        finished = self.action(advanced['session'], 'next')
        self.assertTrue(finished['session']['complete'])
        self.assertEqual(finished['summary']['attempts'], 1)
        self.assertEqual(finished['items'][1]['reviewed_categories'], 0)
        self.now += timedelta(days=1)
        self.assertEqual(self.store.snapshot(self.catalog)['summary']['due'], 1)

    def test_stale_window_and_invented_choices_are_rejected(self):
        session = self.start()
        newer = self.action(session, 'draft', selections={'Shape': ['Coccus']})['session']
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.action(session, 'next')
        with self.assertRaisesRegex(ValueError, 'source catalog'):
            self.action(newer, 'check', selections={'Shape': ['Invented']})
        with self.assertRaisesRegex(ValueError, 'known'):
            self.start(ids=['not-in-course'])
        self.assertEqual(self.store.snapshot(self.catalog)['summary']['attempts'], 0)

    def test_source_changes_do_not_claim_old_correctness_for_new_facts(self):
        session = self.start()
        self.action(session, 'check', selections=self.answers())
        changed = synthetic_data()
        changed['review_games']['bugs']['cases'][0]['traits']['Virulence'].append('New sourced trait')
        updated = quiz_catalog(changed, 'bugs')
        snapshot = self.store.snapshot(updated)
        self.assertTrue(snapshot['session']['source_changed'])
        self.assertEqual(snapshot['summary']['complete'], 0)
        self.assertEqual(snapshot['items'][0]['reviewed_categories'], 0)
        with self.assertRaisesRegex(ValueError, 'source entry changed'):
            self.store.action(updated, {'action': 'next', 'session_id': session['id'], 'revision': snapshot['session']['revision']})

    def test_subset_checks_do_not_count_unasked_categories_as_complete(self):
        session = self.start(categories=['Shape'])
        result = self.action(session, 'check', selections={'Shape': ['Coccus']})
        self.assertTrue(result['session']['grade']['perfect'])
        self.assertEqual(result['summary']['complete'], 0)
        self.assertIn('Virulence', result['items'][0]['unseen_categories'])

    def test_two_windows_cannot_silently_overwrite_the_same_draft_revision(self):
        session = self.start()
        def write(picked):
            try:
                return self.action(session, 'draft', selections={'Shape': [picked]})['session']['revision']
            except ValueError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ['Coccus', 'Rod']))
        self.assertEqual(results.count('conflict'), 1)
        self.assertEqual(results.count(2), 1)
        self.assertEqual(self.store.snapshot(self.catalog)['summary']['attempts'], 0)

    def test_private_export_keeps_drafts_attempts_and_category_schedule(self):
        session = self.start()
        checked = self.action(session, 'check', selections=self.answers())
        export = self.store.export_state()
        self.assertEqual(export['format'], 'step-study-comparison-quizzes')
        self.assertEqual(export['version'], 1)
        self.assertEqual(export['sessions'][0]['session']['id'], session['id'])
        self.assertEqual(export['attempts'][0]['grade'], checked['session']['grade'])
        self.assertEqual(len(export['schedule']), 5)
        self.assertNotIn('catalog', checked)
        self.assertNotIn('session_json', export['sessions'][0])
        self.assertNotIn('grade_json', export['attempts'][0])

    def test_review_position_is_saved_after_check_without_another_attempt(self):
        session = self.start()
        checked = self.action(session, 'check', selections=self.answers())
        changed = self.action(checked['session'], 'position', category_at=3)
        self.assertEqual(changed['session']['category_at'], 3)
        self.assertEqual(changed['summary']['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
