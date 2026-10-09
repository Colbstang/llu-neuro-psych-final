import base64
import tempfile
import unittest
from pathlib import Path

from StepStudy.live_cards import LiveCards


class FakeAnki:
    def __init__(self):
        self.answer_calls = []
        self.review_rows = {}
        self.card_ids = [1001, 1002]
        self.mismatch = False
        self.offline = False
        self.queries = []
        self.empty_card_info = False

    def __call__(self, action, params):
        if self.offline:
            raise OSError("private transport detail")
        if action == "version":
            return 6
        if action == "getTags":
            return ["#AK_Step1_v12::#FirstAid::02_Neurology",
                    "#AK_Step1_v12::#FirstAid::Renal",
                    "Personal::Neurology"]
        if action == "deckNames":
            return ["AnKing Step 1", "AnKing Step 1::Neurology", "Personal::Neurology"]
        if action == "findCards":
            self.queries.append(params["query"])
            return list(self.card_ids)
        if action == "cardsInfo":
            if self.empty_card_info:
                return []
            result = []
            for cid in params["cards"]:
                reported_id = cid + 1 if self.mismatch and cid == 1001 else cid
                result.append({"cardId": reported_id, "note": cid + 5000, "ord": 0,
                               "queue": 2, "type": 2, "mod": 44, "reps": 3,
                               "question": '<div>Front <b>cloze</b> {{c1::answer}} '
                                           '{{c2::<span>hinted answer</span>::artery}} '
                                           '<img src="safe%2Epng" alt="diagram">'
                                           '<div hidden>hidden <span>nested</span></div>visible '
                                           '<span aria-hidden="true">aria hidden</span>after '
                                           '<i style="visibility:hidden">inline hidden</i>end '
                                           '<i class="answer-extra">css hidden</i>tail'
                                           '<script>alert("no")<span>nested script</div>still hidden</script>end script',
                               "answer": '<p>Back {{c2::revealed answer::artery}} '
                                         '<div hidden>readable hidden answer</div>'
                                         '<style>.x{}</style><img src="https://evil.test/x.png" alt="bad"></p>',
                               "css": '.answer-extra { display: none; }'})
            return result
        if action == "areDue":
            return [True for _ in params["cards"]]
        if action == "getReviewsOfCards":
            return {str(cid): list(self.review_rows.get(cid, [])) for cid in params["cards"]}
        if action == "retrieveMediaFile":
            if params["filename"] == "safe.png":
                return base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()
            return None
        if action == "answerCards":
            self.answer_calls.append(params["answers"])
            answer = params["answers"][0]
            self.review_rows.setdefault(answer["cardId"], []).append(
                {"id": 1800000000000, "ease": answer["ease"], "type": 1})
            return [True]
        raise AssertionError(action)


class LiveCardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeAnki()
        self.live = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(self.live.close)

    def test_queue_is_isolated_to_exact_reported_subject_scope(self):
        result = self.live.queue("neuro")
        self.assertTrue(result["ok"])
        self.assertEqual(result["card_ids"], [1001, 1002])
        self.assertIn('deck:"AnKing Step 1"', self.fake.queries[0])
        self.assertIn('tag:"#AK_Step1_v12::#FirstAid::02_Neurology"', self.fake.queries[0])
        self.assertNotIn("Personal", self.fake.queries[0])
        self.assertIn("-is:suspended", self.fake.queries[0])

    def test_unknown_topic_does_not_query_anki(self):
        result = self.live.queue("not-a-topic")
        self.assertFalse(result["ok"])
        self.assertEqual(result["card_ids"], [])
        self.assertEqual(self.fake.queries, [])

    def test_cards_info_mismatch_is_excluded_and_marks_partial(self):
        self.fake.mismatch = True
        result = self.live.queue("neuro")
        self.assertTrue(result["partial"])
        self.assertEqual(result["card_ids"], [1002])

    def test_queue_has_a_hard_200_card_bound(self):
        self.fake.card_ids = list(range(1000, 1301))
        result = self.live.queue("neuro")
        self.assertEqual(result["count"], 200)
        self.assertLessEqual(len(result["card_ids"]), 200)
        self.assertTrue(result["partial"])

    def test_card_markup_is_returned_as_text_and_validated_media_only(self):
        self.live.queue("neuro")
        result = self.live.begin("neuro", 1001)
        self.assertTrue(result["ok"])
        self.assertIn("Front cloze [...] [artery]", result["front"]["text"])
        self.assertIn("visible after end tail end script", result["front"]["text"])
        for hidden in ("hinted answer", "answer", "hidden", "aria hidden", "inline hidden", "css hidden", "alert", "nested script", "still hidden"):
            self.assertNotIn(hidden, result["front"]["text"])
        self.assertNotIn("<", result["front"]["text"])
        self.assertEqual(len(result["front"]["images"]), 1)
        self.assertTrue(result["front"]["images"][0]["src"].startswith("data:image/png;base64,"))
        self.assertEqual(result["front"]["images"][0]["alt"], "diagram")
        self.assertEqual(result["back"]["images"], [])
        self.assertIn("Back revealed answer readable hidden answer", result["back"]["text"])
        self.assertNotIn("{{c2::", result["back"]["text"])

    def test_boolean_card_ids_are_rejected(self):
        self.fake.card_ids = [True, 1002]
        result = self.live.queue("neuro")
        self.assertTrue(result["partial"])
        self.assertEqual(result["card_ids"], [1002])

    def test_card_info_failure_returns_confirmed_partial_queue(self):
        self.fake.card_ids = list(range(1000, 1060))
        original = self.fake
        batches = 0

        def flaky(action, params):
            nonlocal batches
            if action == "cardsInfo":
                batches += 1
                if batches == 2:
                    raise OSError("private transport detail")
            return original(action, params)

        partial_live = LiveCards(Path(self.temp.name) / "partial", transport=flaky)
        try:
            result = partial_live.queue("neuro")
        finally:
            partial_live.close()
        self.assertTrue(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["count"], 25)

    def test_offline_queue_is_not_reported_as_healthy_empty(self):
        self.fake.offline = True
        result = self.live.queue("neuro")
        self.assertFalse(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("unavailable", result["reason"].lower())

    def test_unconfirmed_ids_are_not_reported_as_healthy_empty(self):
        self.fake.empty_card_info = True
        result = self.live.queue("neuro")
        self.assertFalse(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("identities", result["reason"].lower())

    def test_explicit_rating_uses_bridge_and_duplicate_request_is_idempotent(self):
        self.live.queue("neuro")
        opened = self.live.begin("neuro", 1001)
        rated = self.live.rate("neuro", opened["token"], 3, "request-1")
        self.assertTrue(rated["ok"])
        self.assertTrue(rated["confirmed"])
        self.assertEqual(rated["ease"], 3)
        again = self.live.rate("neuro", opened["token"], 3, "request-1")
        self.assertEqual(again, rated)
        self.assertEqual(len(self.fake.answer_calls), 1)
        self.assertEqual(self.fake.answer_calls[0], [{"cardId": 1001, "ease": 3}])

    def test_invalid_ratings_and_offline_response_are_safe(self):
        self.live.queue("neuro")
        opened = self.live.begin("neuro", 1001)
        for ease in (True, 0, 5, "3"):
            self.assertFalse(self.live.rate("neuro", opened["token"], ease, "bad")["ok"])
        self.fake.offline = True
        result = self.live.begin("neuro", 1001)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "Anki is unavailable.")
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()
