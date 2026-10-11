"""Integration regressions use private temporary state and synthetic sources."""
import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path

from StepStudy.server import Services, make_server
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

    def test_second_reveal_returns_the_first_frozen_answer(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        second = self.service.start_concept(first["item_id"])["recall"]
        self.service.reveal_concept(first["session_id"], "Original independent answer")
        result = self.service.reveal_concept(second["session_id"], "Stale second view answer")
        self.assertEqual(result["answer"], "Original independent answer")
        self.assertEqual(self.service.pending[second["session_id"]]["independent_answer"], "Original independent answer")
        self.service.answer_concept({"session_id": second["session_id"], "answer": result["answer"], "action": "self_rating", "self_rating": "hard"})

    def test_saved_answer_has_no_schedule_and_failed_grade_stays_unassessed(self):
        for graded in (False, True):
            first = self.service.start_concept("lo:lo-stable-1")["recall"]
            self.service.grader = lambda *a, **kw: {"assessed": False, "score": 1, "feedback": "Unavailable"}
            result = self.service.answer_concept({"session_id": first["session_id"], "answer": "Test draft", "action": "grade" if graded else "save"})
            self.assertIsNone(result["attempt"]["schedule"])
            self.assertIsNone(result["attempt"]["score"])
            if not graded:
                self.assertNotIn("reference_context", result)

    def test_private_drafts_resume_per_item_without_recording_recall(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        self.service.save_concept_draft(first["session_id"], "Continue this prediction")
        other = self.service.start_concept("block:n-block-b")["recall"]
        self.service.save_concept_draft(other["session_id"], "A separate explanation")
        # Reopening after navigation creates a new session but preserves each answer.
        resumed = self.service.start_concept(first["item_id"])["recall"]
        self.assertNotEqual(first["session_id"], resumed["session_id"])
        self.assertEqual(resumed["draft"], "Continue this prediction")
        self.assertFalse(resumed["revealed"])
        self.assertNotIn("reference_context", resumed)
        self.assertEqual(self.service.start_concept(other["item_id"])["recall"]["draft"], "A separate explanation")
        self.assertEqual(self.service.store.export_state()["concept_attempts"], [])
        self.assertEqual(self.service.store.export_state()["concept_schedule"], [])
        self.assertEqual(self.calls, [])
        self.assertNotIn("concept_drafts", self.service.store.snapshot())
        for answer in ("x" * 2001, None, []):
            with self.assertRaises(ValueError): self.service.save_concept_draft(resumed["session_id"], answer)
        with self.assertRaises(ValueError): self.service.save_concept_draft("unknown", "Invalid session")

    def test_revealed_draft_resumes_frozen_and_cannot_be_graded_after_navigation(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        stale = self.service.start_concept(first["item_id"])["recall"]
        self.service.reveal_concept(first["session_id"], "My original answer")
        # A second view cannot erase the source-reveal marker or original answer.
        self.service.save_concept_draft(stale["session_id"], "Edited after seeing the source")
        resumed = self.service.start_concept(first["item_id"])["recall"]
        self.assertTrue(resumed["revealed"])
        self.assertEqual(resumed["draft"], "My original answer")
        self.assertIn("First stated mechanism", resumed["reference_context"])
        for identity in (stale["session_id"], resumed["session_id"]):
            with self.assertRaisesRegex(ValueError, "revealed"):
                self.service.answer_concept({"session_id": identity, "answer": "My original answer", "action": "grade"})
        self.service.answer_concept({"session_id": resumed["session_id"], "answer": "My original answer", "action": "self_rating", "self_rating": "hard"})
        self.assertEqual(self.service.store.export_state()["concept_drafts"], [])
        self.assertFalse(self.service.start_concept(first["item_id"])["recall"]["revealed"])
        self.assertEqual(self.calls, [])

    def test_draft_is_not_applied_to_changed_source_explanation(self):
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        self.service.reveal_concept(first["session_id"], "Earlier lesson answer")
        changed = course_data()
        changed["objective_answers"]["lo-stable-1"]["html"] = "<p>Updated synthetic source explanation.</p>"
        self.course.data = lambda: changed
        resumed = self.service.start_concept(first["item_id"])["recall"]
        self.assertEqual(resumed["draft"], "")
        self.assertFalse(resumed["revealed"])
        self.assertNotIn("reference_context", resumed)
        # The old answer is retained in the private export until replaced.
        self.assertEqual(self.service.store.export_state()["concept_drafts"][0]["answer"], "Earlier lesson answer")

    def test_draft_route_requires_token_and_valid_objective_session(self):
        server = make_server(port=0, services=self.service)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        first = self.service.start_concept("lo:lo-stable-1")["recall"]
        def post(body, token=None):
            port = server.server_address[1]
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = {"Content-Type": "application/json"}
            if token: headers["X-Step-Token"] = token
            connection.request("POST", "/api/concept-draft", json.dumps(body), headers)
            response = connection.getresponse();status = response.status;response.read();connection.close()
            return status
        body = {"session_id": first["session_id"], "answer": "Private draft"}
        self.assertEqual(post(body), 403)
        self.assertEqual(post(body, server.token), 200)
        self.assertEqual(post({**body, "session_id": "missing"}, server.token), 400)
        self.assertEqual(post({**body, "answer": "x" * 2001}, server.token), 400)
        self.assertEqual(self.service.start_concept(first["item_id"])["recall"]["draft"], "Private draft")

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
