"""All review writes in this suite use a fake AnkiConnect transport."""
from __future__ import annotations

import tempfile
import sqlite3
import json
import os
import stat
import unittest
import base64
from pathlib import Path
from typing import Any

try:
    from .anki_bridge import AnkiBridge, AnkiConnectError, BridgeUnavailable, ScopeError, StaleReview
    from .import_anki_scope import import_scope
except ImportError:
    from anki_bridge import AnkiBridge, AnkiConnectError, BridgeUnavailable, ScopeError, StaleReview
    from import_anki_scope import import_scope


CARD_MAP = {"notes": [
    {"id": 11, "cardIds": [101, 102]},
    {"id": 22, "cardIds": [201]},
]}


def card(card_id: int, note: int, ordinal: int, *, queue: int = 2, reps: int = 1) -> dict[str, Any]:
    return {
        "cardId": card_id, "note": note, "ord": ordinal, "queue": queue,
        "type": 2 if reps else 0, "interval": 7, "reps": reps, "lapses": 0,
        "nextReviews": [{"name": "Again", "interval": "1m"}], "due": 0,
        "mod": 1234, "deckName": "NeuroPsych", "modelName": "Cloze",
        "factor": 2500, "left": 0, "flags": 0,
        "question": "<div>Live question</div>", "answer": "<div>Live answer</div>",
        "css": ".card { color: black; }", "fields": {"private": "must not escape"},
    }


class FakeAnki:
    def __init__(self) -> None:
        self.cards = {101: card(101, 11, 0, reps=0, queue=0),
                      102: card(102, 11, 1), 201: card(201, 22, 0)}
        self.due = {101: True, 102: True, 201: True}
        self.reviews: dict[int, list[dict[str, Any]]] = {101: [], 102: [], 201: []}
        self.answer_calls: list[dict[str, Any]] = []
        self.answer_mode = "success"
        self.max_batch = 0
        self.media_files: dict[str, str] = {}
        self.media_calls: list[str] = []

    def external_review(self, card_id: int, ease: int = 3) -> None:
        next_id = max((row["id"] for rows in self.reviews.values() for row in rows), default=1000) + 1
        self.reviews.setdefault(card_id, []).append({"cid": card_id, "id": next_id, "usn": 1, "ease": ease,
                                                     "ivl": 8, "lastIvl": 7, "factor": 2500, "time": 900,
                                                     "type": 1})

    def __call__(self, action: str, params: dict[str, Any]) -> Any:
        if action == "version":
            return 6
        if action == "deckNames":
            return ["NeuroPsych", "Other"]
        if action == "getLatestReviewID":
            return max((row["id"] for rows in self.reviews.values() for row in rows), default=0)
        if action == "cardsInfo":
            return [dict(self.cards.get(int(cid), {})) for cid in params["cards"]]
        if action == "retrieveMediaFile":
            filename = params["filename"]
            self.media_calls.append(filename)
            return self.media_files.get(filename)
        if action == "areDue":
            return [self.due.get(int(cid), False) for cid in params["cards"]]
        if action == "getReviewsOfCards":
            batch = params["cards"]
            self.max_batch = max(self.max_batch, len(batch))
            return {str(cid): list(self.reviews.get(int(cid), [])) for cid in batch}
        if action == "answerCards":
            self.answer_calls.append(params)
            if self.answer_mode == "api_error":
                raise AnkiConnectError("simulated native scheduling failure")
            if self.answer_mode == "timeout_before_write":
                raise TimeoutError("simulated timeout before processing")
            answer = params["answers"][0]
            self.external_review(int(answer["cardId"]), int(answer["ease"]))
            if self.answer_mode == "timeout_after_write":
                raise TimeoutError("simulated timeout after processing")
            return [True]
        raise AssertionError(f"Unexpected AnkiConnect action: {action}")


class AnkiBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "ledger.sqlite3"
        self.fake = FakeAnki()
        self.bridge = AnkiBridge(CARD_MAP, self.db, transport=self.fake)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def begin(self, card_id: int = 101) -> dict[str, Any]:
        return self.bridge.begin_review(card_id=card_id)

    def test_empty_public_install_is_read_only_until_user_imports_scope(self):
        bridge = AnkiBridge({}, Path(self.temp.name) / "empty.sqlite3", transport=self.fake)
        status = bridge.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["allowed_card_count"], 0)
        self.assertFalse(status["rating_write"])
        self.assertTrue(status["scope_import_required"])
        with self.assertRaises(ScopeError):
            bridge.begin_review(101)
        self.assertEqual(self.fake.answer_calls, [])

    def test_project_directory_loads_the_public_context_export(self):
        project = Path(self.temp.name) / "project"
        project.mkdir()
        (project / "anki_context_data.js").write_text(
            'window.ANKI_CONTEXT=' + json.dumps(CARD_MAP) + ';', encoding="utf-8")
        bridge = AnkiBridge(project, Path(self.temp.name) / "project.sqlite3", transport=self.fake)
        self.assertEqual(bridge.allowed, {101: {"note": 11, "ord": 0}, 102: {"note": 11, "ord": 1},
                                          201: {"note": 22, "ord": 0}})

    def test_private_import_keeps_only_card_identity_allowlist(self):
        source = Path(self.temp.name) / "user-export.js"
        source.write_text('window.ANKI_CONTEXT=' + json.dumps({"notes": [
            {"id": 7, "cardIds": [81, 82], "html": "fixture-only content", "images": ["fixture.png"]}
        ]}) + ';', encoding="utf-8")
        target_dir = Path(self.temp.name) / "private-app-data"
        self.assertEqual(import_scope(source, target_dir), 2)
        saved = (target_dir / "anki-scope.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(saved), {"81": {"note": 7, "ord": 0}, "82": {"note": 7, "ord": 1}})
        self.assertNotIn("fixture-only content", saved)
        self.assertNotIn("fixture.png", saved)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE((target_dir / "anki-scope.json").stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(target_dir.stat().st_mode), 0o700)

    def test_success_uses_native_answercards_shape_and_live_card_content(self) -> None:
        started = self.begin()
        self.assertEqual(started["card"]["question"], "<div>Live question</div>")
        self.assertEqual(started["card"]["answer"], "<div>Live answer</div>")
        self.assertEqual(started["card"]["css"], ".card { color: black; }")
        self.assertTrue(started["card"]["isDue"])
        self.assertTrue(started["card"]["isNew"])
        self.assertNotIn("fields", started["card"])
        result = self.bridge.submit_review(started["token"], 3, "review-1")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["cardId"], 101)
        self.assertEqual(result["ease"], 3)
        self.assertIsNotNone(result["reviewId"])
        self.assertEqual(self.fake.answer_calls, [{"answers": [{"cardId": 101, "ease": 3}]}])

    def test_duplicate_request_after_restart_never_resends(self) -> None:
        started = self.begin(102)
        first = self.bridge.submit_review(started["token"], 4, "durable-request")
        self.assertTrue(first["confirmed"])
        # A new bridge instance and transport simulate process restart.
        after_restart = AnkiBridge(CARD_MAP, self.db, transport=self.fake)
        second = after_restart.submit_review(started["token"], 4, "durable-request")
        self.assertEqual(second["status"], "confirmed")
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_other_card_review_does_not_stale_this_card_session(self) -> None:
        started = self.begin(101)
        self.fake.external_review(201)
        result = self.bridge.submit_review(started["token"], 3, "unrelated-card-review")
        self.assertTrue(result["confirmed"])
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_same_card_review_makes_session_stale(self) -> None:
        started = self.begin(101)
        self.fake.external_review(101)
        with self.assertRaises(StaleReview):
            self.bridge.submit_review(started["token"], 3, "same-card-stale")
        self.assertEqual(self.fake.answer_calls, [])

    def test_out_of_scope_id_is_rejected_before_transport(self) -> None:
        with self.assertRaises(ScopeError):
            self.bridge.card_info(999)
        self.assertEqual(self.fake.answer_calls, [])

    def test_live_note_or_ordinal_mismatch_is_rejected(self) -> None:
        self.fake.cards[101]["ord"] = 1
        with self.assertRaises(ScopeError):
            self.bridge.card_info(101)

    def test_live_media_is_retrieved_only_for_safe_local_image_filenames(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"test-image"
        self.fake.media_files["occlusion.png"] = base64.b64encode(png).decode("ascii")
        self.fake.cards[101]["question"] = (
            '<img src="occlusion.png"><img src="occlusion.png">'
            '<img src="../private.png"><img src="https://example.test/logo.png">'
            '<img src="data:image/png;base64,AAAA">'
        )
        result = self.bridge.card_info(101)
        self.assertEqual(self.fake.media_calls, ["occlusion.png"])
        self.assertEqual(result["media"]["occlusion.png"],
                         "data:image/png;base64," + base64.b64encode(png).decode("ascii"))

    def test_live_svg_image_occlusion_is_returned_as_safe_data_url(self) -> None:
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" onclick="bad()"/><script>alert(1)</script></svg>'
        self.fake.cards[101]["answer"] = '<img src="occlusion.svg">'
        self.fake.media_files["occlusion.svg"] = base64.b64encode(svg).decode("ascii")
        result = self.bridge.begin_review(101)
        data_url = result["card"]["media"]["occlusion.svg"]
        decoded = base64.b64decode(data_url.split(",", 1)[1])
        self.assertTrue(data_url.startswith("data:image/svg+xml;base64,"))
        self.assertNotIn(b"script", decoded)
        self.assertNotIn(b"onclick", decoded)

    def test_invalid_or_oversized_live_media_is_omitted(self) -> None:
        self.fake.cards[101]["question"] = '<img src="bad.png"><img src="large.png">'
        self.fake.media_files["bad.png"] = "not base64!"
        self.fake.media_files["large.png"] = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024)).decode("ascii")
        result = self.bridge.card_info(101)
        self.assertEqual(result["media"], {})

    def test_suspended_card_cannot_begin_review(self) -> None:
        self.fake.cards[102]["queue"] = -1
        self.fake.due[102] = False
        with self.assertRaises(ScopeError):
            self.begin(102)
        self.assertEqual(self.fake.answer_calls, [])

    def test_not_due_card_cannot_begin_review(self) -> None:
        self.fake.due[102] = False
        with self.assertRaises(ScopeError):
            self.begin(102)

    def test_invalid_ease_is_rejected_without_write(self) -> None:
        started = self.begin()
        for ease in (0, 5, True, "3"):
            with self.subTest(ease=ease), self.assertRaises(ValueError):
                self.bridge.submit_review(started["token"], ease, f"invalid-{ease}")
        self.assertEqual(self.fake.answer_calls, [])

    def test_unavailable_anki_is_reported_and_no_write_occurs(self) -> None:
        def unavailable(action: str, _params: dict[str, Any]) -> Any:
            if action == "version":
                raise BridgeUnavailable("offline")
            raise BridgeUnavailable("offline")

        bridge = AnkiBridge(CARD_MAP, self.db, transport=unavailable)
        self.assertFalse(bridge.status()["available"])
        with self.assertRaises(BridgeUnavailable):
            bridge.card_info(101)

    def test_timeout_after_native_write_is_confirmed_from_history(self) -> None:
        self.fake.answer_mode = "timeout_after_write"
        started = self.begin()
        result = self.bridge.submit_review(started["token"], 2, "timeout-after-write")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_uncertain_timeout_without_history_is_never_retried(self) -> None:
        self.fake.answer_mode = "timeout_before_write"
        started = self.begin()
        first = self.bridge.submit_review(started["token"], 2, "uncertain-no-write")
        second = self.bridge.submit_review(started["token"], 2, "uncertain-no-write")
        restarted = AnkiBridge(CARD_MAP, self.db, transport=self.fake)
        third = restarted.submit_review(started["token"], 2, "uncertain-no-write")
        self.assertEqual(first["status"], "unknown")
        self.assertEqual(second["status"], "unknown")
        self.assertEqual(third["status"], "unknown")
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_restart_recovers_write_ahead_pending_from_history(self) -> None:
        started = self.begin(102)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE review_tokens SET status='submitted',request_id=? WHERE token=?",
                         ("crashed-request", started["token"]))
            conn.execute(
                "INSERT INTO review_requests(request_id,token,card_id,ease,baseline_review_id,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',1,1)",
                ("crashed-request", started["token"], 102, 3, started["baseline_review_id"]),
            )
        self.fake.external_review(102, 3)
        restarted = AnkiBridge(CARD_MAP, self.db, transport=self.fake)
        recovered = restarted.submit_review(started["token"], 3, "crashed-request")
        self.assertTrue(recovered["confirmed"])
        self.assertEqual(recovered["status"], "confirmed")
        self.assertEqual(self.fake.answer_calls, [])

    def test_explicit_ankiconnect_error_is_honestly_unsynced_and_idempotent(self) -> None:
        self.fake.answer_mode = "api_error"
        started = self.begin()
        first = self.bridge.submit_review(started["token"], 1, "native-error")
        second = self.bridge.submit_review(started["token"], 1, "native-error")
        self.assertEqual(first["status"], "unsynced")
        self.assertFalse(first["confirmed"])
        self.assertEqual(second["status"], "unsynced")
        self.assertEqual(len(self.fake.answer_calls), 1)

    def test_history_sync_is_scoped_persisted_and_read_only_on_coverage(self) -> None:
        self.fake.external_review(101, 3)
        synced = self.bridge.sync_history()
        self.assertEqual(synced["summary"]["scope_card_count"], 3)
        self.assertEqual(synced["summary"]["review_count"], 1)
        self.assertTrue(synced["cards"]["101"]["reviewed"])
        self.assertEqual(synced["cards"]["101"]["last_review"]["ease"], 3)
        count = len(self.fake.answer_calls)
        cached = AnkiBridge(CARD_MAP, self.db, transport=self.fake).coverage()
        self.assertEqual(cached, synced)
        self.assertEqual(len(self.fake.answer_calls), count)
        self.assertFalse(synced["summary"]["guide_progress_changed"])

    def test_history_sync_splits_requests_at_ankiconnect_batch_limit(self) -> None:
        ids = list(range(20000, 21005))
        mapping = {"notes": [{"id": 77, "cardIds": ids}]}
        fake = FakeAnki()
        fake.reviews = {cid: [] for cid in ids}
        bridge = AnkiBridge(mapping, self.db, transport=fake)
        synced = bridge.sync_history()
        self.assertEqual(synced["summary"]["scope_card_count"], len(ids))
        self.assertEqual(len(synced["cards"]), len(ids))
        self.assertEqual(fake.max_batch, 999)


if __name__ == "__main__":
    unittest.main()
