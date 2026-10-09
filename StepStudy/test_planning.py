import unittest
from datetime import datetime, timezone

from StepStudy.planning import build_review_queue


class PlanningTests(unittest.TestCase):
    def test_only_active_topics_and_only_real_signals_enter_queue(self):
        catalog = [
            {"id": "neuro", "label": "Neurology", "dependencies": ["biochemistry"]},
            {"id": "biochemistry", "label": "Biochemistry", "dependencies": []},
            {"id": "renal", "label": "Renal", "dependencies": []},
        ]
        state = {"topics": {
            "neuro": {"active": True, "next_recall": "2026-10-08T00:00:00Z"},
            "biochemistry": {"active": False}, "renal": {"active": True},
        }}
        anki = {"available": True, "subjects": {"neuro": {"due_count": 3, "again_count": 2, "hard_count": 1}}}
        questions = {"available": True, "topics": {"renal": {"unresolved_count": 2}}}
        rows = build_review_queue(catalog, state, anki, questions,
                                  now=datetime(2026, 10, 9, tzinfo=timezone.utc))
        self.assertEqual({row["topic_id"] for row in rows}, {"neuro", "renal"})
        self.assertEqual({row["kind"] for row in rows}, {"recall", "anki", "questions"})
        anki_row = next(row for row in rows if row["kind"] == "anki")
        self.assertEqual(anki_row["count"], 3)
        self.assertIn("Anki remains the scheduler", anki_row["reason"])

    def test_unknown_evidence_yields_explicit_learning_item_not_fake_zero(self):
        catalog = [{"id": "renal", "label": "Renal", "dependencies": ["biochemistry"]}]
        rows = build_review_queue(catalog, {"topics": {"renal": {"active": True}}},
                                  {"available": False, "subjects": {}}, None,
                                  now="2026-10-09T00:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "learn")
        self.assertEqual(rows[0]["recommended_dependencies"], ["biochemistry"])

    def test_connected_partial_sync_with_unknown_due_count_keeps_planner_usable(self):
        rows = build_review_queue([{"id": "renal", "label": "Renal"}],
            {"topics": {"renal": {"active": True}}},
            {"available": True, "subjects": {"renal": {"scope_known": False, "due_count": None}}},
            None, now="2026-10-09T00:00:00Z")
        self.assertEqual([row["kind"] for row in rows], ["learn"])

    def test_weak_recall_returns_now_and_related_topic_mapping_ignores_order(self):
        catalog = [{"id": "renal", "label": "Renal", "dependencies": ["biochemistry"]},
                   {"id": "biochemistry", "label": "Biochemistry", "dependencies": []}]
        state = {"topics": {tid: {"active": True, "next_recall": "2026-11-01T00:00:00Z"} for tid in ("renal", "biochemistry")},
                 "attempts": [{"topic_id": "biochemistry", "score": .4, "grade": {"assessed": True}}]}
        for order in (catalog, list(reversed(catalog))):
            rows = build_review_queue(order, state, None, None, now="2026-10-09T00:00:00Z")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "recall:biochemistry")
            self.assertEqual(rows[0]["related_topic_ids"], ["renal"])
            self.assertIn("for Renal", rows[0]["reason"])

    def test_unassessed_answer_never_triggers_weak_score_recommendation(self):
        rows = build_review_queue([{"id": "renal", "label": "Renal"}],
            {"topics": {"renal": {"active": True, "next_recall": "2026-11-01T00:00:00Z"}},
             "attempts": [{"topic_id": "renal", "score": .1, "grade": {"assessed": False}}]},
            None, None, now="2026-10-09T00:00:00Z")
        self.assertEqual(rows, [])

    def test_later_unassessed_answer_does_not_clear_weak_assessment(self):
        rows = build_review_queue([{"id": "renal", "label": "Renal"}],
            {"topics": {"renal": {"active": True, "next_recall": "2026-11-01T00:00:00Z"}},
             "attempts": [{"topic_id": "renal", "score": .4, "grade": {"assessed": True}},
                          {"topic_id": "renal", "score": None, "grade": {"assessed": False}}]},
            None, None, now="2026-10-09T00:00:00Z")
        self.assertEqual([row["id"] for row in rows], ["recall:renal"])


if __name__ == "__main__":
    unittest.main()
