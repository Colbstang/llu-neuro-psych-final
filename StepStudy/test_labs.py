import tempfile
import unittest
from pathlib import Path

from StepStudy.labs import LocalLabs, lab_policy


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
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("connect-src http://127.0.0.1:9999/lab/" + "a" * 24 + "/;", policy)
        self.assertIn("form-action 'none'", policy)
        self.assertIn("frame-src 'none'", policy)


if __name__ == "__main__":
    unittest.main()
