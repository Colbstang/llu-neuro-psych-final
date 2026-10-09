import unittest
from datetime import datetime, timezone

from StepStudy.anki_signals import AnkiSignals, _subject_for_tag


NOW_MS = 1791504000000


class FakeAnki:
    def __init__(self):
        self.queries = []
        self.card_batches = []
        self.review_batches = []

    def __call__(self, action, params):
        if action == "version": return 6
        if action == "getTags":
            return ["#AK_Step1_v12::#FirstAid::02_Neurology", "#AK_Step1_v12::#FirstAid::Renal",
                    "#AK_Step1_v12::#FirstAid::Immunology", "#AK_Step1_v12::#FirstAid::Cardio",
                    "#AK_Step1_v12::#FirstAid::HemeOnc", "#AK_Step1_v12::#FirstAid::MSK",
                    "#AK_Step1_v12::#FirstAid::Psychiatry", "Personal::Renal"]
        if action == "deckNames": return ["AnKing Step 1", "Personal Deck", "Step 2::AnKing"]
        if action == "findCards":
            self.queries.append(params["query"])
            if "is:due" in params["query"]:
                return [101]
            return [101, 102]
        if action == "cardsInfo":
            self.card_batches.append(params["cards"])
            return [{"cardId": card, "queue": 2, "type": 2, "deckName": "AnKing Step 1"}
                    for card in params["cards"]]
        if action == "getReviewsOfCards":
            self.review_batches.append(params["cards"])
            return {"101": [{"id": NOW_MS, "ease": 1, "type": 1},
                             {"id": NOW_MS - 1000, "ease": 2, "type": 2}],
                    "102": [{"id": NOW_MS, "ease": 4, "type": 3}]}
        raise AssertionError(action)


class AnkiSignalTests(unittest.TestCase):
    def test_uses_exact_step_category_aliases_and_real_dict_review_rows(self):
        fake = FakeAnki()
        now = datetime(2026, 10, 9, tzinfo=timezone.utc)
        data = AnkiSignals(fake, clock=lambda: now).sync()
        subject = data["subjects"]["neuro"]
        self.assertTrue(data["available"])
        self.assertEqual(subject["card_count"], 2)
        self.assertEqual(subject["due_count"], 1)
        self.assertEqual(subject["review_count"], 2)
        self.assertEqual(subject["again_count"], 1)
        self.assertEqual(subject["hard_count"], 1)
        self.assertTrue(all("Personal Deck" not in q for q in fake.queries))
        self.assertTrue(all("Personal::Renal" not in q for q in fake.queries))
        self.assertTrue(all("-is:suspended" in q for q in fake.queries))
        self.assertEqual(data["scope"]["query_count"], 7)
        self.assertTrue(data["scope"]["partial"])
        self.assertTrue(subject["scope_known"])
        self.assertTrue(subject["review_history_known"])
        self.assertFalse(any("text" in key for key in subject))

    def test_category_matching_has_no_generic_substring_false_positives(self):
        ids = {"neuro", "psychiatry", "cardiovascular", "hematology-oncology", "renal", "immunology"}
        self.assertEqual(_subject_for_tag("#AK_Step1_v12::#FirstAid::#02_Neurology", ids), "neuro")
        self.assertEqual(_subject_for_tag("#AK_Step1_v12::#FirstAid::Renal", ids), "renal")
        self.assertIsNone(_subject_for_tag("#AK_Step1_v12::Heartburn", ids))
        self.assertIsNone(_subject_for_tag("#AK_Step1_v12::Blood pressure", ids))
        self.assertIsNone(_subject_for_tag("Personal::Renal", ids))

    def test_numbered_full_category_names_map_all_catalog_topics(self):
        tags = {
            "01_General Principles": "general-principles", "02_Biochemistry": "biochemistry",
            "03_Immunology": "immunology", "04_Microbiology": "microbiology",
            "05_Pathology": "pathology", "06_Pharmacology": "pharmacology",
            "07_Public Health": "public-health", "08_Cardiovascular": "cardiovascular",
            "09_Endocrine": "endocrine", "10_Gastrointestinal": "gastrointestinal",
            "11_Hematology & Oncology": "hematology-oncology",
            "12_MSK/Skin/Connective Tissue": "musculoskeletal-skin",
            "13_Neurology": "neuro", "14_Psychiatry": "psychiatry",
            "15_Renal": "renal", "16_Reproductive": "reproductive",
            "17_Respiratory": "respiratory",
        }
        known = set(tags.values())
        for category, expected in tags.items():
            self.assertEqual(_subject_for_tag(f"#AK_Step1_v12::#FirstAid::{category}", known), expected)

    def test_child_tag_volume_does_not_expand_query_and_deck_descendants_are_compressed(self):
        class TaxonomyFake(FakeAnki):
            def __call__(self, action, params):
                if action == "getTags":
                    return ["#AK_Step1_v12::#FirstAid::#02_Neurology"] + [
                        f"#AK_Step1_v12::#FirstAid::#02_Neurology::Child{i}" for i in range(5000)]
                if action == "deckNames":
                    return ["AnKing Step 1", "AnKing Step 1::Neurology", "Personal"]
                if action == "findCards":
                    self.queries.append(params["query"])
                    return []
                return super().__call__(action, params)

        fake = TaxonomyFake()
        data = AnkiSignals(fake).sync()
        self.assertEqual(data["scope"]["query_count"], 1)
        self.assertEqual(data["scope"]["decks"], ["AnKing Step 1"])
        self.assertLess(len(fake.queries[0]), 400)
        self.assertIn('tag:"#AK_Step1_v12::#FirstAid::#02_Neurology::*"', fake.queries[0])

    def test_query_prefix_count_and_query_length_have_hard_bounds(self):
        class PrefixFake(FakeAnki):
            def __call__(self, action, params):
                if action == "getTags":
                    return [f"#AK_Step1_v12::Namespace{i}::Neurology" for i in range(60)]
                if action == "findCards":
                    self.queries.append(params["query"])
                    return []
                return super().__call__(action, params)

        fake = PrefixFake()
        data = AnkiSignals(fake).sync()
        self.assertTrue(data["partial"])
        self.assertEqual(data["scope"]["query_count"], 1)
        self.assertLessEqual(len(fake.queries[0]), 12000)

    def test_missing_cards_info_and_suspended_cards_are_not_counted(self):
        class MissingInfoFake(FakeAnki):
            def __call__(self, action, params):
                if action == "findCards":
                    self.queries.append(params["query"])
                    return [1, 3]
                if action == "cardsInfo":
                    self.card_batches.append(params["cards"])
                    return [{"cardId": 1, "queue": 2}, {"cardId": 2, "queue": -1}]
                if action == "getReviewsOfCards":
                    self.review_batches.append(params["cards"])
                    return {}
                return super().__call__(action, params)

        fake = MissingInfoFake()
        data = AnkiSignals(fake).sync()
        subject = data["subjects"]["neuro"]
        self.assertEqual(subject["card_count"], 2)
        self.assertEqual(subject["due_count"], 2)
        self.assertEqual(subject["sampled_card_count"], 1)
        self.assertEqual(subject["missing_card_info_count"], 1)
        self.assertEqual(fake.review_batches, [[1]])
        self.assertFalse(subject["review_history_known"])

    def test_many_tags_and_large_decks_are_bounded_and_marked_partial(self):
        class HugeFake(FakeAnki):
            def __call__(self, action, params):
                if action == "getTags":
                    return ["#AK_Step1_v12::#FirstAid::Neurology"] + [f"Personal::{i}" for i in range(5000)]
                if action == "findCards":
                    self.queries.append(params["query"])
                    return [] if "is:due" in params["query"] else list(range(1, 6001))
                if action == "cardsInfo":
                    self.card_batches.append(params["cards"])
                    return []
                if action == "getReviewsOfCards":
                    self.review_batches.append(params["cards"])
                    return {}
                return super().__call__(action, params)

        fake = HugeFake()
        data = AnkiSignals(fake, clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc), batch_size=173).sync()
        self.assertTrue(data["scope"]["partial"])
        self.assertEqual(data["scope"]["query_count"], 1)
        self.assertLessEqual(sum(map(len, fake.card_batches)), 5000)
        self.assertLessEqual(sum(map(len, fake.review_batches)), 5000)
        self.assertLessEqual(sum(map(len, fake.card_batches)), 200)
        self.assertLessEqual(sum(map(len, fake.review_batches)), 200)
        self.assertLessEqual(len(fake.queries), 2)

    def test_sync_can_limit_and_order_requested_subjects(self):
        fake = FakeAnki()
        data = AnkiSignals(fake).sync(["renal", "neuro"])
        self.assertEqual(set(data["subjects"]), {"renal", "neuro"})
        self.assertIn("Renal", fake.queries[0])
        self.assertEqual(data["scope"]["query_count"], 2)

    def test_invalid_future_cram_and_manual_rows_are_excluded_and_history_unknown_stays_unknown(self):
        class HistoryFake(FakeAnki):
            def __call__(self, action, params):
                if action == "getReviewsOfCards":
                    return {"101": [
                        {"id": NOW_MS, "ease": 3, "type": 1},
                        {"id": NOW_MS, "ease": 2, "type": 3},
                        {"id": NOW_MS + 1, "ease": 1, "type": 1},
                        {"id": NOW_MS, "ease": 9, "type": 1},
                        {"id": "bad", "ease": 1, "type": 1},
                    ]}
                return super().__call__(action, params)

        data = AnkiSignals(HistoryFake(), clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc)).sync()
        subject = data["subjects"]["neuro"]
        self.assertEqual(subject["review_count"], 1)
        self.assertEqual(subject["hard_count"], 0)
        self.assertEqual(subject["unknown_history_count"], 1)

    def test_missing_tags_and_personal_decks_stays_empty(self):
        responses = {"version": 6, "getTags": ["Personal::Renal"], "deckNames": ["Personal Deck"]}
        data = AnkiSignals(lambda action, params: responses[action]).sync()
        self.assertTrue(data["available"])
        self.assertFalse(data["subjects"]["neuro"]["scope_known"])
        self.assertIsNone(data["subjects"]["neuro"]["due_count"])
        self.assertIn("No clearly Step", data["reason"])

    def test_timeout_after_one_topic_retains_known_counts_and_marks_rest_unknown(self):
        class InterruptedFake(FakeAnki):
            def __call__(self, action, params):
                if action == "findCards":
                    self.queries.append(params["query"])
                    if len(self.queries) > 2:
                        raise TimeoutError("simulated deadline")
                    return [101] if "is:due" in params["query"] else [101, 102]
                return super().__call__(action, params)

        data = AnkiSignals(InterruptedFake(), clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc)).sync()
        self.assertTrue(data["available"])
        self.assertTrue(data["partial"])
        self.assertEqual(data["subjects"]["neuro"]["total_card_count"], 2)
        self.assertIsNone(data["subjects"]["neuro"]["due_count_total"])
        self.assertTrue(data["subjects"]["psychiatry"]["scope_known"])
        self.assertFalse(data["subjects"]["general-principles"]["scope_known"])
        self.assertIsNone(data["subjects"]["general-principles"]["review_count"])


if __name__ == "__main__":
    unittest.main()
