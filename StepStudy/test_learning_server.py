"""Integration regressions use private temporary state and synthetic sources."""
import json
import tempfile
import unittest
from pathlib import Path

from StepStudy.server import Services
from StepStudy.test_server import FakeSpeech, FakeAnki
from StepStudy.test_visual_recall import png
from StepStudy.test_concept_learning import course_data


class Course:
    def __init__(self): self.token = "opaque-course-image"
    def data(self): return course_data()
    def asset(self, identity):
        return {"mime_type": "image/png", "body": png()} if identity == self.token else None
    def close(self): pass


class LearningServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.course = Course()
        def grader(*args, **kwargs):
            self.calls.append((args, kwargs))
            return {"assessed": True, "score": .8, "feedback": "Synthetic source assessment"}
        self.service = Services(Path(self.temp.name), course=self.course, tts=FakeSpeech(), anki=FakeAnki(),
            module_reader=lambda: {"available": False}, grader=grader, ai_status={"available": True})
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.service.close)

    def test_source_is_hidden_and_only_explicit_grade_calls_provider(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        self.assertNotIn("reference_context", first)
        self.assertNotIn("expected_points", first)
        self.assertEqual(self.calls, [])
        result = self.service.answer_concept({"session_id": first["session_id"], "answer": "Independent test answer", "action": "grade"})
        self.assertEqual(result["attempt"]["score"], .8)
        self.assertEqual(len(self.calls), 1)
        repeated = self.service.answer_concept({"session_id": first["session_id"], "answer": "Independent test answer", "action": "grade"})
        self.assertEqual(repeated["attempt"]["attempt_id"], result["attempt"]["attempt_id"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("First stated mechanism", self.calls[0][1]["reference_context"])
        with self.assertRaises(ValueError): self.service.answer_concept({"session_id": first["session_id"], "answer": "repeat", "action": "grade"})

    def test_reveal_disables_assessment_and_rating_keeps_item_separate(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        self.service.reveal_concept(first["session_id"], "My original answer")
        with self.assertRaises(ValueError): self.service.answer_concept({"session_id": first["session_id"], "answer": "My original answer", "action": "grade"})
        with self.assertRaises(ValueError): self.service.answer_concept({"session_id": first["session_id"], "answer": "Edited after reveal", "action": "self_rating", "self_rating": "remembered"})
        self.service.answer_concept({"session_id": first["session_id"], "answer": "My original answer", "action": "self_rating", "self_rating": "remembered"})
        ids = {item["item_id"] for item in self.service.concepts("neuro")["items"]}
        self.assertNotIn("lo:lo-stable-1", ids)
        self.assertIn("block:n-block-b", ids)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.service.store.snapshot()["topics"]["neuro"]["next_recall"])

    def test_saved_answer_has_no_schedule_and_failed_grade_stays_unassessed(self):
        for graded in (False, True):
            first = self.service.start_concept("lo:lo-stable-1")["recall"]
            self.service.grader = lambda *a, **kw: {"assessed": False, "score": 1, "feedback": "Unavailable"}
            result = self.service.answer_concept({"session_id": first["session_id"], "answer": "Test draft", "action": "grade" if graded else "save"})
            self.assertIsNone(result["attempt"]["schedule"])
            self.assertIsNone(result["attempt"]["score"])
            if not graded:
                self.assertNotIn("reference_context", result)

    def test_course_occlusion_copy_survives_asset_token_change(self):
        payload = {"name": "Synthetic image exercise", "source": {"kind": "course_asset", "id": self.course.token}, "topic_ids": ["neuro"], "chapter_id": "neuro-chapter", "masks": [{"x": 0, "y": 0, "width": .2, "height": .2}]}
        record = self.service.create_visual(payload)["exercise"]
        self.assertEqual(record["source"]["kind"], "upload")
        self.course.token = "new-token-after-restart"
        image = self.service.visual_image(record["source"]["kind"], record["source"]["id"])
        self.assertEqual(image["body"], png())
        with self.assertRaises(ValueError): self.service.create_visual({**payload, "source": {"kind": "course_asset", "id": self.course.token}, "masks": [{"x": 2}]})
        self.assertEqual(len(self.service.visual.export_state()["uploads"]), 1)

    def test_session_references_are_server_generated_and_resume(self):
        result = self.service.start_study_session(15, "neuro")["session"]
        self.assertTrue(result["tasks"])
        first = result["tasks"][0]
        self.service.study_sessions.update(result["id"], first["id"], draft="Resume this draft")
        self.assertEqual(self.service.study_sessions.snapshot()["tasks"][0]["draft"], "Resume this draft")
        self.assertFalse(any(row["kind"] == "anki" for row in result["tasks"]))
