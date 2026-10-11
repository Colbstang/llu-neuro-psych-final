import base64
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
        self.assertNotIn('deck:"', self.fake.queries[0])
        self.assertIn('tag:"#AK_Step1_v12::#FirstAid::02_Neurology"', self.fake.queries[0])
        self.assertEqual(self.fake.queries[0].count('tag:'), 1)
        self.assertNotIn('::*', self.fake.queries[0])
        self.assertNotIn("Personal", self.fake.queries[0])
        self.assertIn("-is:suspended", self.fake.queries[0])
        self.assertIn("-deck:filtered", self.fake.queries[0])

    def test_step_tags_work_in_personal_deck_but_unqualified_personal_notes_are_excluded(self):
        original = self.fake
        def personal_deck(action, params):
            if action == "deckNames":
                return ["Med", "Personal::Neurology"]
            if action == "findCards":
                self.assertIn('tag:"#AK_Step1_v12::#FirstAid::02_Neurology"', params["query"])
                self.assertNotIn("Personal", params["query"])
                self.assertNotIn('deck:"', params["query"])
                return [1001]
            result = original(action, params)
            if action == "cardsInfo":
                for row in result:
                    row["deckName"] = "Med"
            return result
        self.live.transport = personal_deck
        self.assertEqual(self.live.queue("neuro")["card_ids"], [1001])
        self.assertTrue(self.live.begin("neuro", 1001)["ok"])
        self.assertFalse(self.live.begin("neuro", 9999)["ok"])
        self.assertEqual(self.fake.answer_calls, [])

    def test_personal_subject_tag_without_step_namespace_never_opens_queue(self):
        original = self.fake
        def personal_tags(action, params):
            if action == "getTags": return ["Personal::Neurology"]
            if action == "findCards": raise AssertionError("An unqualified personal tag must never be queried")
            return original(action, params)
        self.live.transport = personal_tags
        result = self.live.queue("neuro")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertFalse(self.live.begin("neuro", 1001)["ok"])

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

    def test_due_cards_are_not_crowded_out_by_many_new_cards(self):
        original = self.fake
        def due_priority(action, params):
            if action == "findCards":
                return [9001] if params["query"].endswith("is:due") else list(range(1001, 1301))
            return original(action, params)
        self.live.transport = due_priority
        queued = self.live.queue("neuro")
        self.assertTrue(queued["ok"])
        self.assertEqual(queued["card_ids"][0], 9001)
        self.assertEqual(queued["count"], 200)
        self.assertTrue(queued["partial"])

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

    def open_session(self):
        session = self.live.start_session("neuro", 5)["session"]
        opened = self.live.open_session("neuro", session["id"])
        self.assertTrue(opened["ok"])
        return session, opened

    def test_session_requires_reveal_and_counts_only_confirmed_explicit_reviews(self):
        session, opened = self.open_session()
        self.assertIsNone(opened["card"]["back"])
        self.assertIsNone(self.live.confirmed_review("neuro", "unseen"))
        refused = self.live.rate_session("neuro", session["id"], 3, "premature")
        self.assertFalse(refused["ok"])
        self.assertEqual(self.fake.answer_calls, [])
        revealed = self.live.reveal_session("neuro", session["id"])
        self.assertIn("Back revealed answer", revealed["card"]["back"]["text"])
        rated = self.live.rate_session("neuro", session["id"], 3, "session-review")
        self.assertTrue(rated["review"]["confirmed"])
        self.assertEqual(rated["session"]["confirmed_count"], 1)
        self.assertEqual(rated["session"]["position"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)
        replay = self.live.rate_session("neuro", session["id"], 3, "session-review")
        self.assertTrue(replay["review"]["confirmed"])
        self.assertEqual(replay["session"]["confirmed_count"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_session_skip_has_no_rating_and_persists_after_restart(self):
        session, opened = self.open_session()
        skipped = self.live.skip_session("neuro", session["id"])
        self.assertEqual(skipped["session"]["skipped_count"], 1)
        self.assertEqual(skipped["session"]["confirmed_count"], 0)
        restarted = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.session_snapshot("neuro")["session"]["position"], 1)
        reopened = restarted.open_session("neuro", session["id"])
        self.assertTrue(reopened["ok"])
        self.assertEqual(reopened["card"]["card_id"], 1002)
        self.assertIsNone(reopened["card"]["back"])
        self.assertEqual(self.fake.answer_calls, [])

    def test_reveal_token_and_confirmation_survive_restart_without_double_rating(self):
        session, _opened = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        restarted = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(restarted.close)
        reopened = restarted.open_session("neuro", session["id"])
        self.assertTrue(reopened["session"]["current"]["revealed"])
        self.assertIsNotNone(reopened["card"]["back"])
        accepted = restarted.rate_session("neuro", session["id"], 2, "restart-review")
        self.assertTrue(accepted["review"]["confirmed"])
        again = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(again.close)
        replay = again.rate_session("neuro", session["id"], 2, "restart-review")
        self.assertTrue(replay["review"]["confirmed"])
        self.assertEqual(replay["session"]["confirmed_count"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_lost_write_response_is_reconciled_without_resending(self):
        original = self.fake
        def lost_response(action, params):
            result = original(action, params)
            if action == "answerCards":
                raise TimeoutError("Synthetic response lost after commit")
            return result
        self.live.transport = lost_response
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        result = self.live.rate_session("neuro", session["id"], 4, "lost-review")
        self.assertTrue(result["review"]["confirmed"])
        self.assertEqual(result["session"]["confirmed_count"], 1)
        self.live.rate_session("neuro", session["id"], 4, "lost-review")
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_unknown_write_is_retained_and_history_recheck_can_later_confirm(self):
        original = self.fake
        visible = False
        def delayed_history(action, params):
            if action == "getReviewsOfCards" and not visible:
                return {str(cid): [] for cid in params["cards"]}
            result = original(action, params)
            if action == "answerCards":
                raise TimeoutError("Synthetic uncertain outcome")
            return result
        self.live.transport = delayed_history
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        unknown = self.live.rate_session("neuro", session["id"], 1, "delayed-review")
        self.assertFalse(unknown["review"]["confirmed"])
        self.assertEqual(unknown["review"]["status"], "unknown")
        self.assertEqual(unknown["session"]["confirmed_count"], 0)
        self.assertFalse(self.live.skip_session("neuro", session["id"])["ok"])
        self.assertFalse(self.live.rate_session("neuro", session["id"], 4, "different-rating")["ok"])
        visible = True
        resolved = self.live.rate_session("neuro", session["id"], 1, "delayed-review")
        self.assertTrue(resolved["review"]["confirmed"])
        self.assertEqual(resolved["session"]["confirmed_count"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_explicit_dismissal_keeps_unknown_ledger_and_does_not_repeat_a_write(self):
        original = self.fake
        visible = False
        def lost_history(action, params):
            if action == "getReviewsOfCards" and not visible:
                return {str(cid): [] for cid in params["cards"]}
            return original(action, params)
        self.live.transport = lost_history
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        result = self.live.rate_session("neuro", session["id"], 3, "unknown-dismiss")
        self.assertFalse(result["review"]["confirmed"])
        self.assertEqual(len(self.fake.answer_calls), 1)
        self.assertFalse(self.live.skip_session("neuro", session["id"])["ok"])
        self.assertFalse(self.live.dismiss_session("neuro", session["id"], "wrong-request")["ok"])
        dismissed = self.live.dismiss_session("neuro", session["id"], "unknown-dismiss")
        self.assertTrue(dismissed["ok"])
        self.assertEqual(dismissed["session"]["confirmed_count"], 0)
        self.assertEqual(dismissed["session"]["unknown_count"], 1)
        self.assertEqual(dismissed["session"]["position"], 1)
        self.assertEqual(self.live._bridge("neuro")._request_result("unknown-dismiss")["status"], "unknown")
        replay = self.live.dismiss_session("neuro", session["id"], "unknown-dismiss")
        self.assertEqual(replay["session"]["unknown_count"], 1)
        visible = True
        replay_rating = self.live.rate_session("neuro", session["id"], 3, "unknown-dismiss")
        self.assertFalse(replay_rating["review"]["confirmed"])
        self.assertEqual(len(self.fake.answer_calls), 1)
        restarted = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.session_snapshot("neuro")["session"]["unknown_count"], 1)
        self.assertTrue(restarted.open_session("neuro", session["id"])["ok"])

    def test_dismissal_of_already_confirmed_ledger_recovers_actual_review(self):
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        original = self.live._advance_session
        self.live._advance_session = lambda *args: (_ for _ in ()).throw(OSError("Synthetic session interruption"))
        self.assertFalse(self.live.rate_session("neuro", session["id"], 4, "confirmed-dismiss")["ok"])
        self.live._advance_session = original
        result = self.live.dismiss_session("neuro", session["id"], "confirmed-dismiss")
        self.assertTrue(result["review"]["confirmed"])
        self.assertEqual(result["session"]["confirmed_count"], 1)
        self.assertEqual(result["session"]["unknown_count"], 0)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_invalid_or_cram_review_record_never_counts_as_normal_review(self):
        original = self.fake
        def invalid_history(action, params):
            result = original(action, params)
            if action == "getReviewsOfCards":
                for rows in result.values():
                    for row in rows:
                        row["type"] = True
            return result
        self.live.transport = invalid_history
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        result = self.live.rate_session("neuro", session["id"], 1, "invalid-normal-record")
        self.assertFalse(result["review"]["confirmed"])
        self.assertEqual(result["review"]["status"], "unknown")
        self.assertEqual(result["session"]["confirmed_count"], 0)
        self.assertIsNone(self.live.confirmed_review("neuro", "invalid-normal-record"))
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_racing_duplicate_ratings_have_one_confirmed_result_and_one_write(self):
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.live.rate_session("neuro", session["id"], 3, "racing-review"), range(2)))
        self.assertTrue(all(result["review"]["confirmed"] for result in results))
        self.assertEqual(self.live.session_snapshot("neuro")["session"]["confirmed_count"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_overlapping_subject_tokens_cannot_race_into_two_reviews(self):
        original = self.fake
        def overlap(action, params):
            if action == "getTags":
                return ["#AK_Step1_v12::#FirstAid::Neurology", "#AK_Step1_v12::#FirstAid::Psychiatry"]
            return original(action, params)
        self.live.transport = overlap
        self.live.queue("neuro")
        self.live.queue("psychiatry")
        first = self.live.begin("neuro", 1001)
        second = self.live.begin("psychiatry", 1001)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(self.live.rate, "neuro", first["token"], 3, "neuro-overlap"),
                       pool.submit(self.live.rate, "psychiatry", second["token"], 3, "psych-overlap")]
            results = [future.result() for future in pending]
        self.assertEqual(sum(result.get("confirmed", False) for result in results), 1)
        self.assertEqual(sum(not result["ok"] for result in results), 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_crash_between_bridge_confirmation_and_session_advance_is_repaired_locally(self):
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        original = self.live._advance_session
        def interrupted(*args):
            raise OSError("Synthetic crash after bridge confirmation")
        self.live._advance_session = interrupted
        result = self.live.rate_session("neuro", session["id"], 3, "interrupted-session")
        self.assertFalse(result["ok"])
        self.live._advance_session = original
        restarted = LiveCards(Path(self.temp.name), transport=self.fake)
        self.addCleanup(restarted.close)
        snapshot = restarted.session_snapshot("neuro")["session"]
        self.assertEqual(snapshot["confirmed_count"], 1)
        self.assertEqual(snapshot["position"], 1)
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_private_session_state_contains_identities_but_no_card_content(self):
        session, _ = self.open_session()
        self.live.reveal_session("neuro", session["id"])
        saved = (Path(self.temp.name) / "anki-sessions.json").read_text()
        self.assertNotIn("Front cloze", saved)
        self.assertNotIn("Back revealed answer", saved)
        self.assertNotIn("data:image", saved)
        self.assertEqual((Path(self.temp.name) / "anki-sessions.json").stat().st_mode & 0o777, 0o600)

    def test_image_occlusion_overlay_is_not_flattened_into_unmasked_front(self):
        original = self.fake
        def occlusion(action, params):
            result = original(action, params)
            if action == "cardsInfo":
                for row in result:
                    row["modelName"] = "Image Occlusion Enhanced"
                    row["question"] = '<img src="safe.png"><svg><rect width="20" height="20"/></svg>'
            return result
        self.live.transport = occlusion
        self.live.queue("neuro")
        result = self.live.begin("neuro", 1001)
        self.assertFalse(result["ok"])
        self.assertIn("image occlusion", result["reason"])
        self.assertNotIn("front", result)
        self.assertEqual(self.fake.answer_calls, [])


if __name__ == "__main__":
    unittest.main()
