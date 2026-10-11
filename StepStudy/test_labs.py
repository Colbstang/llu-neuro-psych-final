import tempfile
import unittest
import json
import copy
import http.client
import threading
from types import SimpleNamespace
from pathlib import Path

from StepStudy.labs import LocalLabs, lab_policy, MAX_LESSON_BYTES
from StepStudy.server import make_server


class LabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launched = []
        self.labs = LocalLabs(self.root / "app" / "labs.json", launch=self.launched.append)

    def test_html_assets_are_bounded_to_explicit_root_and_hide_paths(self):
        folder = self.root / "lab"
        folder.mkdir()
        html = folder / "index.html"
        html.write_text('<script src="lab.js"></script>')
        (folder / "lab.js").write_text("window.lab=true")
        secret = self.root / "private.json"
        secret.write_text('{"secret":true}')
        (folder / "escape.json").symlink_to(secret)
        row = self.labs.register(str(html), "Synthetic lab")
        self.assertNotIn("path", row)
        self.assertNotIn(str(self.root), str(self.labs.list()))
        self.assertEqual(self.labs.asset(row["id"])[1], "text/html; charset=utf-8")
        self.assertEqual(self.labs.asset(row["id"], "lab.js")[0], b"window.lab=true")
        for relative in ("../private.json", "escape.json", "/private.json", "nested/../../private.json", "file.txt"):
            with self.assertRaises(KeyError):
                self.labs.asset(row["id"], relative)
        self.assertEqual(LocalLabs(self.labs.path).list(), self.labs.list())

    def test_native_launch_uses_registered_identity_only_and_remove_keeps_files(self):
        app = self.root / "Fixture.app"
        (app / "Contents").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_text("fixture")
        row = self.labs.register(str(app))
        self.assertEqual(self.labs.register(str(app))["id"], row["id"])
        self.labs.open_native(row["id"])
        self.assertEqual(self.launched, [app.resolve()])
        with self.assertRaises(KeyError):
            self.labs.open_native("--args malicious")
        self.labs.remove(row["id"])
        self.assertEqual(self.labs.list(), [])
        self.assertTrue(app.exists())

    def test_html_policy_is_opaque_and_cannot_access_other_labs_or_app_api(self):
        policy = lab_policy("http://127.0.0.1:9999", "a" * 24)
        self.assertIn("sandbox allow-scripts;", policy)
        self.assertNotIn("allow-same-origin", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertIn("'wasm-unsafe-eval'", policy)
        self.assertIn("connect-src http://127.0.0.1:9999/lab/" + "a" * 24 + "/;", policy)
        self.assertIn("form-action 'none'", policy)
        self.assertIn("frame-src 'none'", policy)

    def lesson(self):
        # Synthetic data tests the adapter, without copying a private lesson.
        expected = {"leftEyeMoves": True, "rightEyeMoves": False,
                    "leftEyeDegrees": -30, "rightEyeDegrees": 0, "observation": "Fixture observation"}
        return {"entities": [{"id": name, "name": name, "function": "Fixture entity", "side": "LEFT"}
                             for name in ("left_eye", "right_eye")],
                "pathways": [{"id": "fixture_path", "name": "Fixture path", "nodes": ["left_eye", "right_eye"],
                              "edges": [{"source": "left_eye", "target": "right_eye", "function": "Fixture edge"}]}],
                "lesions": [{"id": "fixture_lesion", "name": "Fixture lesion", "site": "left_eye",
                             "function": "Fixture effect", "evidence_note": "Fixture limitation",
                             "equivalence_group": "fixture_group", "quizable": True,
                             "expected_left": expected, "expected_right": expected}],
                "private_extra": "This must not be served"}

    def write_lesson(self, payload=None):
        path = self.root / "lesson.json"
        path.write_text(json.dumps(payload or self.lesson()))
        return path

    def test_gaze_embeds_trusted_renderer_and_only_bounded_lesson_fields(self):
        path = self.write_lesson()
        row = self.labs.register(str(path), "Fixture circuit")
        self.assertEqual(row["kind"], "gaze")
        self.assertTrue(row["embedded"])
        self.assertEqual(row["url"], "/lab/" + row["id"] + "/")
        self.assertNotIn(str(self.root), str(row))
        self.assertIn(b"gaze_lab.js", self.labs.asset(row["id"])[0])
        self.assertEqual(self.labs.asset(row["id"], "gaze_lab.js")[1], "text/javascript; charset=utf-8")
        lesson = json.loads(self.labs.asset(row["id"], "lesson.json")[0])
        self.assertNotIn("private_extra", lesson)
        self.assertEqual(lesson["lesions"][0]["expected_left"]["rightEyeDegrees"], 0)
        for relative in ("../lesson.json", "other.json", "gaze_lab.html", "gaze_lab.js/.."):
            with self.assertRaises(KeyError): self.labs.asset(row["id"], relative)
        self.assertEqual(LocalLabs(self.labs.path).list(), self.labs.list())

    def test_gaze_rejects_arbitrary_json_and_invalid_observations(self):
        invalids = [{"html": "<script>arbitrary()</script>"}]
        item = copy.deepcopy(self.lesson());item["lesions"][0]["expected_left"]["leftEyeDegrees"] = 100
        invalids.append(item)
        item = copy.deepcopy(self.lesson());item["lesions"][0]["expected_left"]["rightEyeDegrees"] = 30
        invalids.append(item)
        item = copy.deepcopy(self.lesson());item["pathways"][0]["nodes"] = ["left_eye", {"invalid": True}]
        invalids.append(item)
        item = copy.deepcopy(self.lesson());item["pathways"][0]["edges"][0]["source"] = ["left_eye"]
        invalids.append(item)
        for payload in invalids:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.labs.register(str(self.write_lesson(payload)))

    def test_gaze_does_not_follow_replaced_lesson_symlink_or_large_lesson(self):
        path = self.write_lesson()
        row = self.labs.register(str(path))
        hidden = self.root / "private.json";hidden.write_text('{"secret": true}')
        path.unlink();path.symlink_to(hidden)
        with self.assertRaises(ValueError): self.labs.asset(row["id"], "lesson.json")
        path.unlink();path.write_bytes(b" " * (MAX_LESSON_BYTES + 1))
        with self.assertRaises(ValueError): self.labs.asset(row["id"], "lesson.json")

    def test_html_parent_replaced_with_symlink_cannot_change_the_selected_root(self):
        folder = self.root / "lab";folder.mkdir()
        html = folder / "index.html";html.write_text("selected")
        row = self.labs.register(str(html))
        other = self.root / "other";other.mkdir();(other / "index.html").write_text("not selected")
        html.unlink();folder.rmdir();folder.symlink_to(other)
        with self.assertRaises(KeyError): self.labs.asset(row["id"])

    def test_gltf_and_webassembly_assets_remain_in_the_registered_directory(self):
        folder = self.root / "model";folder.mkdir()
        html = folder / "index.html";html.write_text("fixture")
        row = self.labs.register(str(html))
        for filename, mime in (("model.glb", "model/gltf-binary"), ("model.gltf", "model/gltf+json"),
                               ("runtime.wasm", "application/wasm"), ("runtime.data", "application/octet-stream")):
            (folder / filename).write_bytes(b"fixture")
            self.assertEqual(self.labs.asset(row["id"], filename), (b"fixture", mime))

    def test_embedded_lesson_http_route_retains_opaque_origin_and_no_app_access(self):
        row = self.labs.register(str(self.write_lesson()), "Fixture circuit")
        server = make_server(port=0, services=SimpleNamespace(labs=self.labs, close=lambda: None))
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port = server.server_address[1]
        def get(path, origin="null", host=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}", "Origin": origin})
            response = connection.getresponse();body=response.read();headers=dict(response.getheaders());connection.close()
            return response.status, body, headers
        for relative in ("", "gaze_lab.js", "gaze_lab.css", "lesson.json"):
            status, body, headers = get(row["url"] + relative)
            self.assertEqual(status, 200);self.assertTrue(body)
            self.assertEqual(headers["Access-Control-Allow-Origin"], "null")
            self.assertIn("sandbox allow-scripts;", headers["Content-Security-Policy"])
            self.assertNotIn("allow-same-origin", headers["Content-Security-Policy"])
        for relative in ("%2e%2e/private.json", "gaze_lab.html", "missing.js"):
            self.assertEqual(get(row["url"] + relative)[0], 404)
        self.assertEqual(get(row["url"], origin="https://example.com")[0], 403)
        self.assertEqual(get(row["url"], host="example.com")[0], 403)
        self.assertEqual(get("/api/workspace")[0], 403)


if __name__ == "__main__":
    unittest.main()
