import tempfile
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path

from StepStudy.store import StepStudyStore


class StoreTests(unittest.TestCase):
    def test_pause_keeps_history_and_restart_restores_state(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 9, tzinfo=timezone.utc)]
            path = Path(folder) / "step-study.sqlite3"
            store = StepStudyStore(path, clock=lambda: now[0])
            store.activate("renal")
            store.save_notes("renal", "Review acid-base concepts")
            attempt = store.save_attempt("renal", "Prompt", "Answer", grade=None, score=None)
            self.assertIsNone(attempt["grade"])
            self.assertIsNone(attempt["score"])
            store.activate("renal", active=False)
            restarted = StepStudyStore(path, clock=lambda: now[0])
            state = restarted.snapshot()
            self.assertFalse(state["topics"]["renal"]["active"])
            self.assertEqual(state["topics"]["renal"]["notes"], "Review acid-base concepts")
            self.assertEqual(len(state["attempts"]), 1)
            self.assertEqual(state["attempts"][0]["id"], attempt["id"])
            self.assertIsNotNone(state["topics"]["renal"]["paused_at"])

    def test_legacy_attempts_can_keep_stable_source_identity_additively(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StepStudyStore(Path(folder) / "state.sqlite3")
            legacy = store.save_attempt("neuro", "Legacy prompt", "Legacy answer")
            linked = store.save_attempt("neuro", "Source prompt", "Answer",
                                        item_id="mdwiki:neuro:abc", source_id="abc")
            snapshot = store.snapshot()
            self.assertIsNone(snapshot["attempts"][0]["item_id"])
            self.assertEqual(snapshot["attempts"][0]["id"], legacy["id"])
            self.assertEqual(linked["item_id"], "mdwiki:neuro:abc")
            self.assertEqual(linked["source_id"], "abc")
            self.assertIsNone(linked["score"])

    def test_preexisting_legacy_attempt_table_migrates_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL, prompt TEXT NOT NULL, answer TEXT NOT NULL, grade_json TEXT, score REAL, created_at TEXT NOT NULL)")
                db.execute("INSERT INTO attempts(topic_id,prompt,answer,created_at) VALUES('neuro','Old prompt','Old answer','2026-10-01T00:00:00Z')")
            store = StepStudyStore(path)
            attempt = store.snapshot()["attempts"][0]
            self.assertEqual((attempt["prompt"], attempt["answer"], attempt["item_id"], attempt["source_id"]),
                             ("Old prompt", "Old answer", None, None))

    def test_concept_review_topic_counts_cover_candidates_beyond_session_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StepStudyStore(Path(folder) / "state.sqlite3")
            catalog = [{"item_id": f"lo:{i}", "topic_ids": ["neuro", "psychiatry"],
                        "chapter_id": "same-chapter"} for i in range(8)]
            review = store.concept_review(catalog, limit=3)
            self.assertEqual(len(review["items"]), 3)
            self.assertEqual(review["topic_counts"], {
                "neuro": {"due_count": 0, "unseen_count": 8},
                "psychiatry": {"due_count": 0, "unseen_count": 8},
            })

    def test_score_drives_concept_interval_and_unknown_topic_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime(2026, 10, 9, tzinfo=timezone.utc)
            store = StepStudyStore(Path(folder) / "state.sqlite3", clock=lambda: now)
            attempt = store.save_attempt("neuro", "Prompt", "Answer", {"assessed": True, "score": .9}, .9)
            self.assertEqual(attempt["grade"], {"assessed": True, "score": .9})
            self.assertEqual(store.snapshot()["topics"]["neuro"]["next_recall"], "2026-10-16T00:00:00Z")
            with self.assertRaises(ValueError):
                store.activate("unknown")
            with self.assertRaises(ValueError):
                store.save_attempt("neuro", "Prompt", "Answer", {"assessed": False}, .9)

    def test_concept_items_are_independent_and_legacy_progress_survives_schema_addition(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 9, tzinfo=timezone.utc)]
            path = Path(folder) / "state.sqlite3"
            store = StepStudyStore(path, clock=lambda: now[0])
            store.save_notes("neuro", "legacy note")
            legacy = store.save_attempt("neuro", "Legacy prompt", "Legacy answer")
            saved = store.record_concept_attempt("lo:a", "neuro", "Prompt A", "Draft A",
                                                  action="save", session_id="session-a", chapter_id="chapter")
            self.assertIsNone(saved["schedule"])
            graded = store.record_concept_attempt("lo:a", "neuro", "Prompt A", "Answer A",
                    action="assessed", grade={"assessed": True, "score": .4},
                    attempt_id="attempt-a", session_id="session-a", chapter_id="chapter")
            self.assertEqual(graded["schedule"]["due_at"], "2026-10-10T00:00:00Z")
            self.assertEqual(graded["schedule"]["repetitions"], 1)
            review_now = datetime(2026, 10, 10, tzinfo=timezone.utc)
            review = store.concept_review([
                {"item_id": "lo:a", "topic_ids": ["neuro"], "chapter_id": "chapter", "title": "A", "prompt": "Prompt A"},
                {"item_id": "lo:b", "topic_ids": ["neuro"], "chapter_id": "chapter", "title": "B", "prompt": "Prompt B"},
            ], topic_id="neuro", now=review_now)
            self.assertEqual([item["item_id"] for item in review["items"]], ["lo:a", "lo:b"])
            self.assertEqual([item["status"] for item in review["items"]], ["due", "unseen"])
            self.assertEqual(len(review["groups"][0]["items"]), 2)
            restarted = StepStudyStore(path, clock=lambda: now[0])
            state = restarted.snapshot()
            self.assertEqual(state["topics"]["neuro"]["notes"], "legacy note")
            self.assertEqual(state["attempts"][0]["id"], legacy["id"])
            self.assertEqual(len(state["attempts"]), 1)
            self.assertNotIn("concept_attempts", state)
            exported = restarted.export_state()
            self.assertEqual(len(exported["concept_attempts"]), 2)
            self.assertEqual(exported["concept_attempts"][-1]["item_id"], "lo:a")
            self.assertEqual(exported["concept_schedule"][0]["item_id"], "lo:a")

    def test_save_and_failed_grade_do_not_schedule_and_self_rating_requires_reveal(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 9, tzinfo=timezone.utc)]
            store = StepStudyStore(Path(folder) / "state.sqlite3", clock=lambda: now[0])
            store.record_concept_attempt("lo:x", "neuro", "Prompt", "Draft", action="save")
            store.record_concept_attempt("lo:x", "neuro", "Prompt", "Draft",
                action="save", grade={"assessed": False, "score": None})
            with self.assertRaisesRegex(ValueError, "revealed"):
                store.record_concept_attempt("lo:x", "neuro", "Prompt", "Draft",
                    action="self_rating", self_rating="remembered")
            with self.assertRaisesRegex(ValueError, "assessed grade"):
                store.record_concept_attempt("lo:x", "neuro", "Prompt", "Draft",
                    action="assessed", grade={"assessed": False, "score": None})
            review = store.concept_review([{"item_id": "lo:x", "topic_ids": ["neuro"], "chapter_id": "ch"}],
                                          now=now[0])
            self.assertEqual(review["items"][0]["status"], "unseen")
            rated = store.record_concept_attempt("lo:x", "neuro", "Prompt", "Draft",
                action="self_rating", self_rating="hard", revealed=True, chapter_id="ch")
            self.assertIsNone(rated["score"])
            self.assertEqual(rated["schedule"]["interval_days"], 3)
            self.assertEqual(rated["schedule"]["last_outcome"], "hard")

    def test_concept_attempt_id_is_idempotent_and_not_double_scheduled(self):
        with tempfile.TemporaryDirectory() as folder:
            now = [datetime(2026, 10, 9, tzinfo=timezone.utc)]
            store = StepStudyStore(Path(folder) / "state.sqlite3", clock=lambda: now[0])
            kwargs = dict(item_id="lo:a", topic_id="neuro", prompt="Prompt", answer="Answer",
                          action="assessed", grade={"assessed": True, "score": .9}, attempt_id="same-id")
            first = store.record_concept_attempt(**kwargs)
            now[0] = datetime(2026, 10, 12, tzinfo=timezone.utc)
            retry = store.record_concept_attempt(**kwargs)
            self.assertEqual(first["created_at"], retry["created_at"])
            self.assertEqual(first["schedule"]["due_at"], retry["schedule"]["due_at"])
            with self.assertRaisesRegex(ValueError, "different response"):
                store.record_concept_attempt(**{**kwargs, "answer": "Changed answer"})

    def test_chapter_session_groups_at_most_three_individual_items(self):
        with tempfile.TemporaryDirectory() as folder:
            store = StepStudyStore(Path(folder) / "state.sqlite3")
            catalog = [{"item_id": f"lo:{i}", "topic_ids": ["neuro"], "chapter_id": "ch1",
                        "chapter_title": "Chapter 1"} for i in range(5)]
            result = store.concept_review(catalog, topic_id="neuro", limit=100)
            self.assertEqual(len(result["items"]), 3)
            self.assertEqual([len(group["items"]) for group in result["groups"]], [3])
            self.assertEqual(len({item["item_id"] for item in result["items"]}), 3)
            self.assertEqual(result["unseen_count"], 5)


if __name__ == "__main__":
    unittest.main()
