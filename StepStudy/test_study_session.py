import tempfile
import unittest
import time
from pathlib import Path
from StepStudy.study_session import StudySessions, make_plan


class SessionTests(unittest.TestCase):
    def test_resume_draft_and_skipped_step_do_not_fake_completion(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            store = StudySessions(path)
            plan = make_plan([{"item_id": "lo:one", "title": "One"}], [{"id": 7}], [], None)
            store.start(plan)
            store.update(plan["id"], plan["tasks"][0]["id"], draft="My independent answer")
            resumed = StudySessions(path).snapshot()
            self.assertEqual(resumed["tasks"][0]["draft"], "My independent answer")
            store.update(plan["id"], plan["tasks"][0]["id"], action="skip")
            self.assertEqual(store.snapshot()["position"], 1)
            self.assertEqual(store.snapshot()["tasks"][0]["status"], "skipped")
            store.update(plan["id"], plan["tasks"][1]["id"], action="complete")
            self.assertEqual(store.snapshot()["status"], "finished")

    def test_no_due_anki_is_invented_and_session_is_bounded(self):
        concepts = [{"item_id": f"lo:{i}"} for i in range(100)]
        plan = make_plan(concepts, [], [], {"due_count": None}, minutes=15)
        self.assertEqual(len(plan["tasks"]), 3)
        self.assertFalse(any(row["kind"] == "anki" for row in plan["tasks"]))
        self.assertFalse(any(row["kind"] == "anki" for row in make_plan(concepts, [], [], {"due_count": True})["tasks"]))
        with self.assertRaises(ValueError):
            make_plan([], [], [], None, minutes=True)

    def test_stale_session_and_invalid_draft_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudySessions(Path(root) / "session.json")
            plan = make_plan([{"item_id": "lo:one"}], [], [], None)
            store.start(plan)
            with self.assertRaises(ValueError): store.start(plan)
            with self.assertRaises(ValueError): store.update("stale", plan["tasks"][0]["id"], draft="x")
            with self.assertRaises(ValueError): store.update(plan["id"], plan["tasks"][0]["id"], draft="x" * 2001)

    def test_anki_requires_trusted_confirmation_during_this_session(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudySessions(Path(root) / "session.json")
            plan = make_plan([], [], [], {"due_count": 3}, topic_id="neuro")
            store.start(plan)
            task = plan["tasks"][0]
            with self.assertRaises(ValueError):
                store.update(plan["id"], task["id"], action="complete")
            for accepted in ({"confirmed": False}, {"confirmed": True, "topic_id": "renal"},
                             {"confirmed": True, "topic_id": "neuro", "request_id": "old", "review_id": 8, "submitted_at": 1}):
                with self.assertRaises(ValueError): store.complete_anki(plan["id"], task["id"], accepted)
            with self.assertRaises(ValueError):
                store.complete_anki(plan["id"], task["id"], {"confirmed": True, "topic_id": "neuro", "request_id": "invalid", "review_id": 8, "submitted_at": float("nan")})
            accepted = {"confirmed": True, "topic_id": "neuro", "request_id": "actual-review", "review_id": 8, "submitted_at": time.time()}
            result = store.complete_anki(plan["id"], task["id"], accepted)
            self.assertEqual(result["status"], "finished")
            self.assertEqual(result["tasks"][0]["anki_review_id"], 8)
            self.assertEqual(store.complete_anki(plan["id"], task["id"], accepted), result)

    def test_small_session_reserves_space_for_anki_when_due(self):
        plan = make_plan([{"item_id": "one"}], [{"id": 1}], [{"id": "image"}],
                         {"due_count": 2}, topic_id="neuro", lab={"item_id": "gaze"})
        self.assertEqual([task["kind"] for task in plan["tasks"]], ["concept", "repair", "anki"])
