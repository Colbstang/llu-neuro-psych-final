"""Local server tests use temporary storage and a fake bridge only."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from StudyApp.progress_store import ProgressStore
from study_app_server import AppServices, create_server


class FakeBridge:
    def status(self):
        return {"available": False, "allowed_card_count": 0, "rating_write": False,
                "scope_import_required": True}

    def coverage(self):
        return {"summary": None, "cards": {}}


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
        (self.project / "anki_context_data.js").write_text("window.ANKI_CONTEXT={};", encoding="utf-8")
        (self.project / "secret.json").write_text("should not be served", encoding="utf-8")
        asset = self.project / "assets" / "generated" / "intracranial-compartments.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"public-generated-image")
        self.token = "test-token"
        services = AppServices(self.root / "app-data", ProgressStore(self.root / "progress.sqlite3"), FakeBridge())
        self.server = create_server(0, services=services, token=self.token, project_root=self.project)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, *, method="GET", body=None, token=None, origin=None):
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-LLU-App-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        request = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method=method)
        try:
            with urlopen(request) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            try:
                return error.code, error.headers, error.read()
            finally:
                error.close()

    def test_health_index_injection_and_static_allowlist(self):
        self.assertEqual(self.request("/health")[0], 200)
        status, _headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b'window.STUDY_APP_CONFIG', body)
        self.assertEqual(self.request("/anki_context_data.js")[0], 200)
        self.assertEqual(self.request("/assets/generated/intracranial-compartments.png")[0], 200)
        self.assertEqual(self.request("/assets/generated/private.png")[0], 404)
        self.assertEqual(self.request("/secret.json")[0], 404)

    def test_progress_requires_token_and_persists_private_state(self):
        self.assertEqual(self.request("/api/progress")[0], 200)
        status, _headers, _body = self.request("/api/progress", method="POST",
                                                body={"patch": {"good": {"block": True}}, "base_revision": 0})
        self.assertEqual(status, 403)
        status, _headers, body = self.request("/api/progress", method="POST",
                                              body={"patch": {"good": {"block": True}}, "base_revision": 0},
                                              token=self.token, origin=self.base)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["state"]["good"]["block"], True)

    def test_anki_unavailable_is_a_valid_zero_export_state(self):
        status, _headers, body = self.request("/api/anki/status")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["allowed_card_count"], 0)
        self.assertFalse(payload["rating_write"])


if __name__ == "__main__":
    unittest.main()
