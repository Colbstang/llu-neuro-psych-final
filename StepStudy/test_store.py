import tempfile
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


if __name__ == "__main__":
    unittest.main()
