import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from StepStudy.server import Services, make_server
from StepStudy.store import StepStudyStore
from StepStudy.live_cards import LiveCards
from StepStudy.test_live_cards import FakeAnki as FakeCardsAnki


class FakeSpeech:
    def status(self): return {"available": False}
    def close(self): pass


class FakeAnki:
    def sync(self, topic_ids=None):
        self.topic_order = topic_ids
        return {"available": True, "subjects": {"renal": {"due_count": 3, "again_count": 2, "hard_count": 0}}, "synced_at": "2026-10-09T00:00:00Z", "partial": True}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.services = Services(Path(self.temp.name), anki=FakeAnki(), tts=FakeSpeech(), ai_status={"available": True, "model": "fake"},
                                 module_reader=lambda: {"available": False, "topics": {}, "reason": "Isolated test"},
                                 grader=lambda *args, **kw: {"assessed": True, "score": .8, "confidence": .9, "feedback": "Synthetic test assessment", "missed_concepts": [], "citations": [], "status": "graded"})
        self.server = make_server(port=0, services=self.services)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, *, token=True, origin=None, host=None):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if token: headers["X-Step-Token"] = self.server.token
        if origin: headers["Origin"] = origin
        if body is not None: headers["Content-Type"] = "application/json"
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        result = connection.getresponse(); raw = result.read(); connection.close()
        return result.status, json.loads(raw) if result.getheader("Content-Type", "").startswith("application/json") else raw

    def test_activation_notes_and_pause_survive_reopening_database(self):
        status, result = self.request("POST", "/api/activate", {"topic_id": "renal", "active": True})
        self.assertEqual(status, 200)
        self.assertTrue(result["state"]["topics"]["renal"]["active"])
        self.assertIn("renal", {row["topic_id"] for row in result["queue"]})
        self.request("POST", "/api/notes", {"topic_id": "renal", "notes": "Private test note"})
        self.request("POST", "/api/activate", {"topic_id": "renal", "active": False})
        state = StepStudyStore(Path(self.temp.name) / "step-study.sqlite3").snapshot()
        self.assertFalse(state["topics"]["renal"]["active"])
        self.assertEqual(state["topics"]["renal"]["notes"], "Private test note")

    def test_origin_host_token_and_private_static_boundaries(self):
        self.assertEqual(self.request("POST", "/api/activate", {"topic_id":"renal","active":True}, token=False)[0],403)
        self.assertEqual(self.request("GET", "/api/workspace", origin="https://example.com")[0],403)
        self.assertEqual(self.request("GET", "/api/workspace", host="malicious.test")[0],403)
        for path in ["/store.py", "/../AGENTS.md", "/question-intake.sqlite3", "/data/topics.json"]:
            self.assertEqual(self.request("GET", path)[0],404)
        self.assertEqual(self.request("GET", "/health")[1]["app"],"step-study")

    def test_recall_uses_server_rubric_and_saves_only_once(self):
        self.request("POST", "/api/activate", {"topic_id":"renal","active":True})
        status, result = self.request("GET", "/api/recall?topic_id=renal")
        self.assertEqual(status,200)
        prompt = result["recall"]
        self.assertTrue(prompt["sources"])
        answer = {"session_id":prompt["session_id"],"answer":"A synthetic test answer", "grade":True, "expected_points":["Forged rubric"]}
        status, graded = self.request("POST", "/api/answer",answer)
        self.assertEqual(status,200)
        self.assertEqual(graded["attempt"]["score"],.8)
        self.assertEqual(self.request("POST", "/api/answer",answer)[0],400)
        self.assertEqual(len(self.services.store.snapshot()["attempts"]),1)

    def test_unavailable_assessment_does_not_create_a_score_or_anki_write(self):
        self.services.grader=lambda *args, **kw: {"assessed":False,"score":1,"feedback":"Unavailable", "status":"unavailable"}
        prompt=self.request("GET", "/api/recall?topic_id=neuro")[1]["recall"]
        status, result=self.request("POST", "/api/answer", {"session_id":prompt["session_id"],"answer":"Test", "grade":True})
        self.assertEqual(status,200);self.assertIsNone(result["attempt"]["score"])
        self.assertIsNotNone(self.services.store.snapshot()["topics"]["neuro"]["next_recall"])
        self.assertFalse((Path(self.temp.name) / "anki-signals.json").exists())

    def test_preferences_validate_and_anki_sync_keeps_aggregates_private(self):
        self.assertEqual(self.request("POST","/api/preferences",{"preferences":{"panelWidth":900}})[0],400)
        self.assertEqual(self.request("POST","/api/preferences",{"preferences":{"panelWidth":55,"collapsedTerms":{"a":True}}})[0],200)
        result=self.request("POST","/api/anki-sync",{})[1]
        self.assertTrue(result["anki"]["partial"])
        self.assertTrue((Path(self.temp.name)/"anki-signals.json").exists())
        self.assertEqual(self.request("GET","/api/export")[1]["preferences"]["panelWidth"],55)

    def test_anki_history_prioritizes_selected_active_subject_without_losing_catalog(self):
        self.request("POST", "/api/activate", {"topic_id": "renal", "active": True})
        self.request("POST", "/api/preferences", {"preferences": {"lastTopic": "renal"}})
        self.request("POST", "/api/anki-sync", {})
        order = self.services.anki.topic_order
        self.assertEqual(order[0], "renal")
        self.assertEqual(set(order), self.services.store.topic_ids)
        self.assertLess(order.index("neuro"), order.index("biochemistry"))

    def test_failed_watch_is_not_saved_as_running(self):
        result=self.request("POST","/api/watch",{"enabled":True,"folder":str(Path(self.temp.name)/"missing")})[1]
        self.assertFalse(result["watch"]["running"])
        self.assertFalse(self.services.store.snapshot()["preferences"]["watchEnabled"])

    def test_course_signals_are_separate_and_only_queue_active_subjects(self):
        self.services.module_reader = lambda: {"available": True, "topics": {
            "neuro": {"lo_bad": 3, "wrong_count": 2}, "renal": {"lo_bad": 4, "wrong_count": 1}}}
        self.services.module_checked_at = 0
        result = self.request("GET", "/api/workspace")[1]
        courses = [item for item in result["queue"] if item["kind"] == "course"]
        self.assertEqual([item["topic_id"] for item in courses], ["neuro"])
        self.assertEqual(courses[0]["count"], 5)
        self.assertNotIn("score", result["module"]["topics"]["neuro"])
        self.request("POST", "/api/preferences", {"preferences": {"studyTarget": "step"}})
        self.assertFalse(any(item["kind"] == "course" for item in self.request("GET", "/api/workspace")[1]["queue"]))

    def test_live_card_rating_is_explicit_scoped_and_idempotent_over_http(self):
        fake = FakeCardsAnki()
        self.services.cards.close()
        self.services.cards = LiveCards(Path(self.temp.name), transport=fake)
        self.assertEqual(self.request("GET", "/api/card-queue?topic_id=renal")[0], 400)
        queue = self.request("GET", "/api/card-queue?topic_id=neuro")[1]
        self.assertTrue(queue["ok"])
        self.assertEqual(self.request("POST", "/api/card-start", {"topic_id":"neuro", "card_id":queue["card_ids"][0]},token=False)[0],403)
        opened = self.request("POST", "/api/card-start", {"topic_id":"neuro", "card_id":queue["card_ids"][0]})[1]
        self.assertTrue(opened["ok"], opened)
        self.assertEqual(fake.answer_calls, [])
        rating = {"topic_id":"neuro", "token":opened["token"], "ease":3, "request_id":"http-test-request"}
        self.assertTrue(self.request("POST", "/api/card-rate", rating)[1]["confirmed"])
        self.assertTrue(self.request("POST", "/api/card-rate", rating)[1]["confirmed"])
        self.assertEqual(len(fake.answer_calls), 1)
        self.assertEqual(self.services.store.snapshot()["attempts"], [])


if __name__ == "__main__": unittest.main()
