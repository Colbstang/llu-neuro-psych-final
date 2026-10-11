"""Exercise comparison quiz routes against a temporary app and synthetic course."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from StepStudy import test_course_server as fixtures
from StepStudy.server import Services, make_server
from StepStudy.store import StepStudyStore
from StepStudy.test_quiz_store import synthetic_data


class QuizServerTests(unittest.TestCase):
    request = fixtures.CourseServerTests.request

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='quiz-api-test-')
        self.root = Path(self.temp.name)
        self.course = fixtures.FakeCourse(fixtures.FakeProgress())
        self.course._data.update(synthetic_data())
        self.bridge = fixtures.FakeAnkiBridge()
        services = Services(self.root, store=StepStudyStore(self.root / 'step.sqlite'),
                            course=self.course, course_anki=self.bridge, tts=fixtures.FakeSpeech(),
                            ai_status={'available': False}, module_reader=None)
        self.server = make_server(port=0, services=services)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, values):
        status, _, raw = self.request('POST', '/api/course/quizzes', values)
        self.assertEqual(status, 200, raw)
        return json.loads(raw)

    def test_complete_round_is_resumable_idempotent_and_never_calls_anki(self):
        status, _, raw = self.request('GET', '/api/course/quizzes?kind=bugs')
        self.assertEqual(status, 200)
        catalog = json.loads(raw)['catalog']
        saved = self.post({'kind': 'bugs', 'action': 'start', 'round_ids': ['bug-a'], 'categories': catalog['categories']})['session']
        saved = self.post({'kind': 'bugs', 'action': 'draft', 'session_id': saved['id'], 'revision': saved['revision'], 'selections': {'Shape': ['Coccus']}, 'category_at': 1})['session']
        status, _, raw = self.request('GET', '/api/course/quizzes?kind=bugs')
        self.assertEqual(json.loads(raw)['session']['selections'], {'Shape': ['Coccus']})
        check = {'kind': 'bugs', 'action': 'check', 'session_id': saved['id'], 'revision': saved['revision'], 'selections': catalog['cases'][0]['traits']}
        result = self.post(check)
        self.assertEqual(result['summary']['attempts'], 1)
        self.assertTrue(result['session']['grade']['perfect'])
        self.assertEqual(self.post(check)['summary']['attempts'], 1)
        self.assertEqual(self.bridge.reviews, [])
        self.assertEqual(self.bridge.begins, [])

    def test_api_rejects_cross_origin_no_token_and_unsourced_identifiers(self):
        values = {'kind': 'bugs', 'action': 'start', 'round_ids': ['bug-a'], 'categories': ['Shape']}
        status, _, _ = self.request('POST', '/api/course/quizzes', values, token=None)
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/course/quizzes', values, origin='https://example.com')
        self.assertEqual(status, 403)
        status, _, _ = self.request('GET', '/api/course/quizzes?kind=bugs', host='example.com')
        self.assertEqual(status, 403)
        status, _, _ = self.request('POST', '/api/course/quizzes', {**values, 'round_ids': ['invented']})
        self.assertEqual(status, 400)
        status, _, raw = self.request('GET', '/api/course/quizzes?kind=bugs')
        self.assertEqual(json.loads(raw)['summary']['attempts'], 0)
        self.assertIsNone(json.loads(raw)['session'])


if __name__ == '__main__':
    unittest.main()
