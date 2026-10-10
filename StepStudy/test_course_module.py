from __future__ import annotations

import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from StudyApp.progress_store import ProgressStore
from StepStudy.course_module import CourseModule, extract_anki, extract_data


class CourseModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / "course.html"
        self.asset = self.root / "brain.png"
        self.asset.write_bytes(b"PNG fixture")
        self.pdf = self.root / "lecture.pdf"
        self.pdf.write_bytes(b"PDF fixture")
        self.db = self.root / "progress.sqlite3"
        self.store = ProgressStore(self.db)
        self.store.apply_patch({"objectives": {"lo-existing": {"review": "good", "note": "keep"}},
                                "notes": {"block-existing": "retain"}})
        self.data = {
            "layout": {"chapter_order": ["chapter-a"]},
            "pages": [{"id": "chapter-a", "title": "Synthetic chapter", "blocks": [
                {"id": "block-a", "title": "Example", "content": (
                    '<div id="course-block-a" class="lesson-block" data-section-id="block-a">'
                    '<p onclick="bad()">Safe <strong>content</strong></p></div>'
                    '<script>window.steal()</script><img src="https://bad.example/x.png">'
                    '<a href="https://bad.example/">external</a>'
                    f'<a href="{self.pdf.as_uri()}#page=7">lecture source</a>'
                    '<img src="@asset:asset1"><img src="brain.png">'
                    f'<img src="{self.asset.as_uri()}">')}
            ]}],
            "objectives": [{"id": "lo-a", "text": "Synthetic objective"}],
            "questions": [{"id": "question-a", "text": "Synthetic question"}],
            "comparison_sheets": [{"id": "comparison-a", "rows": []}],
            "assets": {"asset1": str(self.asset), "asset2": "data:image/png;base64,ZmFrZQ=="},
            "source_catalog": {"documents": [{"id": "slide-a", "title": "Lecture",
                "url": self.pdf.as_uri(), "aliases": ["Week 1 Lecture.pdf", self.pdf.as_uri()]}]},
        }
        self.bundle.write_text("<!doctype html><script>const DATA=" + json.dumps(self.data) + ";</script>\n"
                               "<script>throw new Error('must not run')</script>", encoding="utf-8")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def adapter(self, **kwargs):
        return CourseModule(self.bundle, progress_store=self.store, **kwargs)

    def test_extracts_json_assignment_without_executing_trailing_script(self):
        data = extract_data(self.bundle.read_bytes())
        self.assertEqual(data["pages"][0]["id"], "chapter-a")
        self.assertEqual(extract_anki('window.ANKI_CONTEXT={"cards":[]};throw new Error()'), {"cards": []})
        with self.assertRaises(ValueError):
            extract_data(b"<script>const DATA={bad: true};</script>")

    def test_data_is_complete_except_separate_assets_and_markup_is_passive(self):
        module = self.adapter()
        data = module.data()
        self.assertEqual(data["layout"]["chapter_order"], ["chapter-a"])
        self.assertEqual(data["objectives"][0]["id"], "lo-a")
        self.assertEqual(data["questions"][0]["id"], "question-a")
        self.assertEqual(data["comparison_sheets"][0]["id"], "comparison-a")
        aliases = data["source_catalog"]["documents"][0]["aliases"]
        self.assertEqual(aliases[0], "Week 1 Lecture.pdf")
        self.assertRegex(aliases[1], r"^/api/course/document/[A-Za-z0-9_-]+\.pdf$")
        self.assertEqual(module.source_document_id(self.pdf.as_uri()), "slide-a")
        self.assertEqual(module.source_document_id("Week 1 Lecture.pdf"), "slide-a")
        token = aliases[1].rsplit("/", 1)[1].removesuffix(".pdf")
        self.assertEqual(module.document_pdf(token), {"kind": "file", "path": self.pdf.resolve()})
        self.assertEqual(data["assets"], {})
        markup = data["pages"][0]["blocks"][0]["content"]
        self.assertIn("<strong>content</strong>", markup)
        self.assertIn('id="course-block-a"', markup)
        self.assertIn('class="lesson-block"', markup)
        self.assertIn('data-section-id="block-a"', markup)
        self.assertNotIn("onclick", markup)
        self.assertNotIn("steal", markup)
        self.assertNotIn("bad.example", markup)
        self.assertIn(aliases[1] + "#page=7", markup)
        self.assertRegex(markup, r"/api/course/asset/[A-Za-z0-9_-]+")
        self.assertEqual(markup.count("/api/course/asset/"), 3)
        self.assertNotIn("@asset:", markup)
        self.assertNotIn(str(self.root), json.dumps(data))
        self.assertNotIn(str(self.root), json.dumps(data["source_catalog"]))

    def test_asset_resolver_checks_ids_paths_and_size(self):
        module = self.adapter()
        module.data()
        resolved = module.asset(module._asset_tokens["asset1"])
        self.assertEqual(resolved["mime_type"], "image/png")
        self.assertEqual(resolved["body"], b"PNG fixture")
        self.assertNotIn("path", resolved)
        self.assertEqual(module.asset(module._asset_tokens["asset2"])["body"], b"fake")
        local_token = next(token for source, token in module._local_image_tokens.items() if source == "brain.png")
        self.assertEqual(module.asset(local_token)["body"], b"PNG fixture")
        self.assertIsNone(module.asset("../brain.png"))
        self.assertIsNone(module.asset("asset404"))
        outside = self.root.parent / "outside.png"
        try:
            outside.write_bytes(b"outside")
            module._assets["asset3"] = str(outside)
            token = "test-token"
            module._asset_tokens["asset3"] = token
            module._token_assets[token] = "asset3"
            self.assertIsNone(module.asset(token))
        finally:
            outside.unlink(missing_ok=True)

    def test_asset_data_url_size_limit(self):
        module = self.adapter()
        module.data()
        module._assets["asset3"] = "data:image/png;base64,QUFBQUFBQUFBQUFB"
        token = "test-token"
        module._asset_tokens["asset3"] = token
        module._token_assets[token] = "asset3"
        with patch("StepStudy.course_module.MAX_ASSET_BYTES", 8):
            self.assertIsNone(module.asset(token))

    def test_document_pdf_is_opaque_allowlisted_and_size_bounded(self):
        module = self.adapter()
        data = module.data()
        aliases = data["source_catalog"]["documents"][0]["aliases"]
        route = next(value for value in aliases if value.startswith("/api/course/document/"))
        token = route.rsplit("/", 1)[1].removesuffix(".pdf")
        self.assertEqual(module.document_pdf(token), {"kind": "file", "path": self.pdf.resolve()})
        self.assertIsNone(module.document_pdf(str(self.pdf)))
        self.assertIsNone(module.document_pdf("unknown-token"))
        with patch("StepStudy.course_module.MAX_DOCUMENT_BYTES", 2):
            self.assertIsNone(module.document_pdf(token))
        outside = self.root.parent / "outside-course-fixture.pdf"
        try:
            outside.write_bytes(b"outside")
            module._document_paths[token] = outside
            self.assertIsNone(module.document_pdf(token))
        finally:
            outside.unlink(missing_ok=True)

    def test_progress_patch_merges_into_existing_shared_state(self):
        module = self.adapter()
        before = module.load_progress()
        after = module.patch_progress({"objectives": {"lo-a": {"review": "bad"}}}, before["revision"])
        self.assertEqual(after["state"]["objectives"]["lo-existing"], {"review": "good", "note": "keep"})
        self.assertEqual(after["state"]["objectives"]["lo-a"]["review"], "bad")
        self.assertEqual(after["state"]["notes"]["block-existing"], "retain")
        self.assertEqual(module.load_progress()["revision"], after["revision"])

    def test_config_bundle_path_is_supported_outside_repository(self):
        config = self.root / "course-config.json"
        config.write_text(json.dumps({"bundle_path": str(self.bundle)}), encoding="utf-8")
        module = CourseModule(config_path=config, progress_store=self.store)
        self.assertTrue(module.status()["available"])
        self.assertEqual(module.status()["source"], "bundle")

    def test_explicit_bundle_failures_do_not_fall_back_to_live_or_public_data(self):
        public = self.root / "public-study-guide.json"
        public.write_text(json.dumps({"pages": [{"id": "public-chapter"}], "assets": {}}), encoding="utf-8")
        missing = self.root / "missing-private-course.html"
        invalid = self.root / "invalid-private-course.html"
        invalid.write_text("<script>const DATA={invalid};</script>", encoding="utf-8")
        oversized = self.root / "oversized-private-course.html"
        oversized.write_text("<script>const DATA={};</script>", encoding="utf-8")
        config_missing = self.root / "missing-config-course.json"
        config_missing.write_text(json.dumps({"bundle_path": str(missing)}), encoding="utf-8")

        cases = [
            ("constructor missing", lambda: CourseModule(missing, progress_store=self.store,
                                                           public_paths=[public])),
            ("environment missing", lambda: CourseModule(progress_store=self.store,
                                                           public_paths=[public])),
            ("config missing", lambda: CourseModule(config_path=config_missing,
                                                      progress_store=self.store, public_paths=[public])),
            ("invalid content", lambda: CourseModule(invalid, progress_store=self.store,
                                                       public_paths=[public])),
            ("oversized content", lambda: CourseModule(oversized, progress_store=self.store,
                                                          max_source_bytes=8, public_paths=[public])),
        ]
        for name, make_module in cases:
            with self.subTest(name=name):
                environment = {"LLU_NEURO_PSYCH_BUNDLE": str(missing)} if name == "environment missing" else {}
                with patch.dict("os.environ", environment, clear=True), patch("StepStudy.course_module.urlopen") as fetch:
                    module = make_module()
                    status = module.status()
                self.assertFalse(status["available"])
                self.assertIsNone(status["source"])
                self.assertEqual(status["reason"], "configured course bundle is unavailable")
                self.assertNotIn(str(self.root), status["reason"])
                fetch.assert_not_called()

    def test_invalid_bundle_config_is_path_free_and_does_not_fall_back(self):
        public = self.root / "public-study-guide.json"
        public.write_text(json.dumps({"pages": [{"id": "public-chapter"}], "assets": {}}), encoding="utf-8")
        config = self.root / "invalid-course-config.json"
        config.write_text("not json", encoding="utf-8")
        with patch("StepStudy.course_module.urlopen") as fetch:
            module = CourseModule(config_path=config, progress_store=self.store, public_paths=[public])
            status = module.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "course bundle configuration is invalid")
        self.assertNotIn(str(self.root), status["reason"])
        fetch.assert_not_called()

    def test_missing_config_env_is_fatal_but_constructor_discovery_path_can_fallback(self):
        public = self.root / "public-study-guide.json"
        public.write_text(json.dumps({"pages": [{"id": "public-chapter"}], "assets": {}}), encoding="utf-8")
        missing = self.root / "missing-course-config.json"
        with patch.dict("os.environ", {"LLU_NEURO_PSYCH_CONFIG": str(missing)}, clear=True), \
                patch("StepStudy.course_module.urlopen") as fetch:
            env_module = CourseModule(progress_store=self.store, public_paths=[public])
            env_status = env_module.status()
        self.assertFalse(env_status["available"])
        self.assertEqual(env_status["reason"], "course bundle configuration is unavailable")
        self.assertNotIn(str(self.root), env_status["reason"])
        fetch.assert_not_called()

        with patch.dict("os.environ", {}, clear=True), patch("StepStudy.course_module.urlopen") as fetch:
            module = CourseModule(config_path=missing, module_url="https://bad.example/",
                                  progress_store=self.store, public_paths=[public])
            status = module.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["source"], "public")
        self.assertEqual(module.data()["pages"][0]["id"], "public-chapter")
        fetch.assert_not_called()

    def test_optional_anki_assignment_is_loaded_as_json_only(self):
        companion = self.root / "anki_context_data.js"
        companion.write_text('window.ANKI_CONTEXT={"cards":[],"notes":[],"markup":"<img src=\'brain.png\'>"};throw new Error("ignored");',
                             encoding="utf-8")
        module = self.adapter()
        anki = module.anki_data()
        self.assertEqual(anki["cards"], [])
        self.assertEqual(anki["notes"], [])
        self.assertRegex(anki["markup"], r"/api/course/asset/[A-Za-z0-9_-]+")

    def test_svg_local_assets_are_passive_and_preserved(self):
        svg = self.root / "diagram.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" onload="bad()">'
                       '<script>bad()</script><path d="M0 0" fill="red" />'
                       '<image href="https://bad.example/x.png" />'
                       '</svg>', encoding="utf-8")
        markup = self.data["pages"][0]["blocks"][0]["content"] + f'<img src="{svg.name}">'
        self.data["pages"][0]["blocks"][0]["content"] = markup
        self.bundle.write_text("<script>const DATA=" + json.dumps(self.data) + ";</script>", encoding="utf-8")
        module = self.adapter()
        rendered = module.data()["pages"][0]["blocks"][0]["content"]
        token = module._local_image_tokens["diagram.svg"]
        self.assertIn("/api/course/asset/" + token, rendered)
        asset = module.asset(token)
        self.assertEqual(asset["mime_type"], "image/svg+xml")
        self.assertIn(b"<path", asset["body"])
        self.assertNotIn(b"<script", asset["body"])
        self.assertNotIn(b"bad()", asset["body"])

    def test_public_fallback_is_explicitly_marked_and_custom_url_is_not_fetched(self):
        public = self.root / "CourseModule" / "data" / "public-study-guide.json"
        public.parent.mkdir(parents=True)
        public.write_text(json.dumps({"pages": [{"id": "public-chapter"}],
                                     "objectives": [], "questions": [], "assets": {}}), encoding="utf-8")
        with patch("StepStudy.course_module.PROJECT", self.root):
            module = CourseModule(module_url="https://bad.example/", progress_store=self.store)
            status = module.status()
            self.assertTrue(status["available"])
            self.assertEqual(status["source"], "public")
            self.assertEqual(module.data()["pages"][0]["id"], "public-chapter")
            self.assertEqual(module.data()["assets"], {})
        denied = CourseModule(module_url="http://localhost:8770/", public_paths=[], progress_store=self.store)
        self.assertFalse(denied.status()["available"])

    def test_no_configured_bundle_keeps_live_loopback_fallback(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("StepStudy.course_module.urlopen", return_value=io.BytesIO(self.bundle.read_bytes())) as fetch:
            module = CourseModule(progress_store=self.store, public_paths=[])
            status = module.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["source"], "live")
        self.assertEqual(module.data()["pages"][0]["id"], "chapter-a")
        fetch.assert_called_once()

    def test_import_progress_is_explicit_and_uses_existing_store(self):
        module = self.adapter()
        before = module.load_progress()
        self.assertIn("lo-existing", before["state"]["objectives"])
        imported = module.import_progress({"notes": {"imported": "fixture"}}, replace=False)
        self.assertIn("lo-existing", imported["state"]["objectives"])
        self.assertEqual(imported["state"]["notes"]["imported"], "fixture")


if __name__ == "__main__":
    unittest.main()
