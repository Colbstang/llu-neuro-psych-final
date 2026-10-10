"""HTTP integration tests for the trusted in-app course reader endpoints."""
from __future__ import annotations

import base64
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from StepStudy.server import COURSE_PROXY_MAX_RESPONSE, Services, make_server
from StepStudy.store import StepStudyStore


class FakeProgress:
    def __init__(self):
        self.state = {"objectives": {"lo1": {"review": "later"}}}
        self.revision = 1
        self.imports = 0

    def load(self):
        return {"state": json.loads(json.dumps(self.state)), "revision": self.revision,
                "updated_at": "2026-10-09T00:00:00Z", "exists": True}

    def apply_patch(self, patch, base_revision=None):
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(self.state.get(key), dict):
                self.state[key].update(value)
            else:
                self.state[key] = value
        self.revision += 1
        return self.load()

    def import_state(self, state, replace=True):
        self.imports += 1
        self.state = json.loads(json.dumps(state)) if replace else {**self.state, **state}
        self.revision += 1
        return self.load()

    def close(self):
        pass


class FakeCourse:
    def __init__(self, progress):
        self._store = progress
        self.data_calls = 0
        self.reader_calls = 0
        self._data = {
            "pages": [{"id": "neuro-page", "title": "Neurology", "keywords": ["brain"],
                       "blocks": [{"id": "sec1", "title": "Neuroanatomy"}]}],
            "topic_groups": {"groups": [{"id": "neuro", "title": "Neurology", "page_ids": ["neuro-page"]}]},
            "objectives": [{"id": "lo1", "sections": ["sec1"]}],
            "questions": [], "question_auto_links": [],
            "question_link_corrections": [], "question_annotations": [], "assets": {},
            "source_catalog": {"documents": [{"id": "FA", "page_count": 3, "title": "First Aid"}]},
        }
        self._asset_records = {
            "opaque-token": {
                "mime_type": "image/png",
                "body": base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jzZcAAAAASUVORK5CYII="),
            },
        }
        for record in self._asset_records.values():
            record["size"] = len(record["body"])

    def status(self):
        return {"available": True, "source": "fixture", "chapter_count": 1,
                "objective_count": 1, "question_count": 0, "asset_count": 1}

    def data(self):
        self.data_calls += 1
        return json.loads(json.dumps(self._data))

    def anki_data(self):
        return {"notes": [], "cards": [], "scope": {}, "graph": {}}

    def load_progress(self):
        return self._store.load()

    def patch_progress(self, patch, revision=None):
        return self._store.apply_patch(patch, revision)

    def asset(self, identity):
        return self._asset_records.get(identity)

    def document_pdf(self, identity):
        if identity != "known-document":
            return None
        path = Path(self._store_pdf)
        return {"kind": "file", "path": str(path)}

    def close(self):
        pass


class FakeSpeech:
    def status(self): return {"available": True, "model": "fake-model"}
    def synthesize(self, text, voice="Bella"): return b"RIFF-fake-audio"
    def close(self): pass


class FakeAnkiBridge:
    def __init__(self):
        self.reviews = []
        self.begins = []

    def status(self): return {"available": True, "allowed_card_count": 1}
    def coverage(self): return {"summary": {}, "cards": {}}
    def sync_history(self): return {"summary": {"review_count": 0}, "cards": {}}
    def begin_review(self, card_id):
        self.begins.append(card_id)
        return {"token": "synthetic-live-token", "card": {"cardId": card_id}}
    def submit_review(self, **values):
        self.reviews.append(values)
        return {"confirmed": True, "request_id": values["request_id"]}


class CourseServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="step-course-server-test-")
        self.root = Path(self.temp.name)
        self.progress = FakeProgress()
        self.course = FakeCourse(self.progress)
        self.course._store_pdf = self.root / "source.pdf"
        self.course._store_pdf.write_bytes(b"%PDF-fixture")
        self.bridge = FakeAnkiBridge()
        self.reference_requests = []

        def reference_transport(endpoint, payload, timeout):
            self.reference_requests.append((endpoint, payload, timeout))
            if endpoint.endswith("/health"):
                return {"ready": True}
            return 200, "application/json; charset=utf-8", b'{"matches":[]}'

        self.services = Services(
            self.root, store=StepStudyStore(self.root / "step-study.sqlite3"),
            course=self.course, course_progress=self.progress, course_anki=self.bridge,
            reference_transport=reference_transport, tts=FakeSpeech(),
            ai_status={"available": False},
            module_reader=None,
        )
        self.server = make_server(port=0, services=self.services)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, *, token="course", origin=None, host=None):
        port = self.server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{port}"}
        if token == "course": headers["X-LLU-App-Token"] = self.server.token
        elif token == "step": headers["X-Step-Token"] = self.server.token
        if origin: headers["Origin"] = origin
        if body is not None: headers["Content-Type"] = "application/json"
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, response, raw)
        connection.close()
        return result

    def test_course_reader_whitelists_route_options_and_has_one_nonce_policy(self):
        rendered = []

        def compose(data, anki, token, **options):
            rendered.append((data, anki, options))
            return b"<main>trusted reader fixture</main>", "default-src 'self'; script-src 'nonce-unit'; frame-ancestors 'self'"

        with patch("StepStudy.server.reader_html", side_effect=compose):
            status, response, body = self.request("GET", "/course/reader?view=objectives&subject=psychiatry&target=step&scope=week8")
        self.assertEqual(status, 200)
        self.assertIn(b"trusted reader fixture", body)
        self.assertEqual(len(response.headers.get_all("Content-Security-Policy")), 1)
        self.assertEqual(response.getheader("Content-Security-Policy"), "default-src 'self'; script-src 'nonce-unit'; frame-ancestors 'self'")
        self.assertEqual(rendered[0][2], {"view": "objectives", "subject": "psychiatry", "target": "step", "scope": "week8", "source": "fixture"})
        self.assertEqual(self.request("GET", "/course/reader?view=not-a-view")[0], 400)
        self.assertEqual(self.request("GET", "/course/reader?subject=all")[0], 400)
        self.assertIn("frame-src 'self'", self.request("GET", "/")[1].getheader("Content-Security-Policy"))

    def test_shared_progress_routes_require_course_token_and_import_only_on_explicit_action(self):
        status, _, before = self.request("GET", "/api/course/progress")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(before)["state"]["objectives"]["lo1"]["review"], "later")
        self.assertEqual(self.request("POST", "/api/course/progress", {"patch": {"objectives": {"lo1": {"review": "good"}}}, "base_revision": 1}, token="step")[0], 403)
        status, _, body = self.request("POST", "/api/course/progress", {"patch": {"objectives": {"lo1": {"review": "good"}}}, "base_revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"]["objectives"]["lo1"]["review"], "good")
        self.assertEqual(self.progress.imports, 0)
        status, _, body = self.request("POST", "/api/course/progress/import", {"state": {"good": {"sec1": True}}})
        self.assertEqual(status, 200)
        self.assertEqual(self.progress.imports, 1)
        self.assertEqual(json.loads(body)["state"], {"good": {"sec1": True}})

    def test_status_data_and_assets_use_course_module_allowlist(self):
        status, _, payload = self.request("GET", "/api/course/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["chapter_count"], 1)
        status, _, payload = self.request("GET", "/api/course/data")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["data"]["objectives"][0]["id"], "lo1")
        status, response, payload = self.request("GET", "/api/course/asset/opaque-token")
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("Content-Type"), "image/png")
        self.assertEqual(payload, self.course._asset_records["opaque-token"]["body"])
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        status, response, payload = self.request("GET", "/api/course/document/known-document.pdf")
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/pdf")
        self.assertEqual(payload, b"%PDF-fixture")
        self.assertEqual(self.request("GET", "/api/course/document/../../secret.pdf")[0], 404)
        self.assertEqual(self.request("GET", "/api/course/asset/../../secret")[0], 404)

    def test_course_asset_rejects_over_limit_mismatched_and_non_image_payloads(self):
        image = self.course._asset_records["opaque-token"]
        with patch("StepStudy.server.COURSE_ASSET_MAX_RESPONSE", len(image["body"]) - 1):
            self.assertEqual(self.request("GET", "/api/course/asset/opaque-token")[0], 404)

        image["size"] += 1
        self.assertEqual(self.request("GET", "/api/course/asset/opaque-token")[0], 404)
        image["size"] -= 1
        image["mime_type"] = "text/html"
        self.assertEqual(self.request("GET", "/api/course/asset/opaque-token")[0], 404)
        image["mime_type"] = "image/png"
        image["body"] = "not bytes"
        self.assertEqual(self.request("GET", "/api/course/asset/opaque-token")[0], 404)

    def test_course_asset_obeys_host_and_origin_guards(self):
        port = self.server.server_address[1]
        self.assertEqual(self.request("GET", "/api/course/asset/opaque-token", host=f"example.com:{port}")[0], 403)
        self.assertEqual(self.request("GET", "/api/course/asset/opaque-token", origin="https://example.com")[0], 403)

    def test_reference_proxy_is_fixed_bounded_and_health_is_local(self):
        status, _, payload = self.request("GET", "/api/course/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ready"])
        search = {"query": "neurology", "kind": "all", "search_scope": "all",
                  "reference_families": ["first_aid"], "limit": 8,
                  "include_books": True, "include_sources": True, "include_background": True}
        status, _, payload = self.request("POST", "/api/course/search", search)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["matches"], [])
        self.assertTrue(self.reference_requests[0][0].startswith("http://127.0.0.1:8768/"))
        self.assertEqual(self.reference_requests[0][2], 40.0)
        self.assertEqual(self.request("POST", "/api/course/search", {**search, "search_scope": "file:///etc/passwd"})[0], 400)
        self.assertEqual(self.request("POST", "/api/course/source-page", {"documentId": "../../secret", "page": 1, "query": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/course/source-page", {"documentId": "FA", "page": 4, "query": "x"})[0], 400)
        status, _, payload = self.request("POST", "/api/course/source-page", {"documentId": "FA", "page": 2, "query": "neuro"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["matches"], [])
        self.assertEqual(self.request("POST", "/api/course/search", {"query": "x" * 20000})[0], 400)

        self.services.reference_transport = lambda *_: (200, "application/json", b"x" * (COURSE_PROXY_MAX_RESPONSE + 1))
        self.assertEqual(self.request("POST", "/api/course/source-page", {"documentId": "FA", "page": 1})[0], 502)
        self.services.reference_transport = lambda *_: (200, "text/html", b"<script>alert(1)</script>")
        self.assertEqual(self.request("POST", "/api/course/search", search)[0], 502)

    def test_tts_and_anki_calls_are_only_through_course_routes(self):
        status, _, payload = self.request("POST", "/api/course/tts", {"text": "cerebellum", "voice": "Bella"})
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertEqual(result["model"], "fake-model")
        self.assertEqual(base64.b64decode(result["audio"]), b"RIFF-fake-audio")
        self.assertEqual(self.request("GET", "/api/course/anki/status")[0], 200)
        self.assertEqual(self.request("POST", "/api/course/anki/sync", {})[0], 200)
        status, _, body = self.request("POST", "/api/course/anki/begin", {"cardId": 42})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], "synthetic-live-token")
        self.assertEqual(self.bridge.reviews, [])
        self.assertEqual(self.request("POST", "/api/activate", {"topic_id": "neuro", "active": True}, token="course")[0], 403)

    def test_dashboard_aggregates_same_course_store_without_blocking_workspace(self):
        self.services.start_worker()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.services.module_signals.get("available"):
            time.sleep(.01)
        self.assertTrue(self.services.module_signals["available"])
        self.assertEqual(self.services.module_signals["topics"]["neuro"]["lo_bad"], 1)
        self.request("POST", "/api/course/progress", {"patch": {"objectives": {"lo1": {"review": "good"}}}})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.services.module_signals["topics"]["neuro"]["lo_good"] != 1:
            time.sleep(.01)
        self.assertEqual(self.services.module_signals["topics"]["neuro"]["lo_good"], 1)
        self.assertLessEqual(self.course.data_calls, 1)


if __name__ == "__main__":
    unittest.main()
