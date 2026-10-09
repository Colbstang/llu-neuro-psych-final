import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from TermCards.server import serve
from TermCards.provider import TermProvider


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir()
        (self.root / "index.html").write_text("<!doctype html><title>cards</title>", encoding="utf-8")
        (self.root / "term_cards.js").write_text("const ok = true;", encoding="utf-8")
        (self.root / "term_cards.css").write_text("body {}", encoding="utf-8")
        (self.root / "data" / "terms.json").write_text(json.dumps({"schema": "medical-term-cards-v1", "records": [
            {"id": "alpha", "title": "Alpha", "aliases": ["A term"], "definition": "secret long text",
             "summary": "private summary", "source": {"name": "MDWiki", "url": "https://mdwiki.org/wiki/Alpha"}}
        ]}), encoding="utf-8")
        (self.root / "private.txt").write_text("must stay hidden", encoding="utf-8")
        provider = TermProvider(self.root / "data" / "terms.json", self.root / "cache.json")
        self.server = serve(directory=self.root, provider=provider, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def fetch(self, path, headers=None):
        request = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            headers = exc.headers
            exc.close()
            return exc.code, headers, body

    def test_lexicon_omits_long_text_and_term_endpoint_returns_exact_record(self):
        status, headers, body = self.fetch("/api/terms")
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["records"][0]["title"], "Alpha")
        self.assertNotIn(b"secret long text", body)
        status, _, body = self.fetch("/api/term?q=A%20term")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["record"]["id"], "alpha")
        self.assertIn("application/json", headers.get_content_type())

    def test_static_allowlist_serves_prototype_and_denies_repository_files(self):
        status, _, body = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn(b"cards", body)
        for path in ("/private.txt", "/StudyApp/app_paths.py", "/%2e%2e/README.md", "/data/../private.txt"):
            status, _, _ = self.fetch(path)
            self.assertEqual(status, 404, path)

    def test_query_limits_and_shape_are_rejected_without_source_access(self):
        status, _, body = self.fetch("/api/term?q=" + "a" * 161)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_query")
        status, _, body = self.fetch("/api/term?q=Alpha&id=alpha")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_query")
        status, _, body = self.fetch("/api/term?q=Alpha&q=Beta")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_query")

    def test_non_loopback_host_and_origin_are_refused(self):
        status, _, body = self.fetch("/api/terms", {"Host": "example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")
        status, _, body = self.fetch("/api/terms", {"Origin": "https://example.com"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")
        status, _, body = self.fetch("/api/terms", {"Host": "["})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")
        status, _, body = self.fetch("/api/terms", {"Origin": "http://["})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")

    def test_server_refuses_non_loopback_bind(self):
        with self.assertRaises(ValueError):
            serve(directory=self.root, provider=TermProvider(self.root / "data" / "terms.json"), host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
