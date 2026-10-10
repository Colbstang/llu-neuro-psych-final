"""Isolated packaging tests for the Step Study native runtime allowlist."""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from StepStudy.native import build_app


PROJECT_FILES = {
    "StepStudy": [
        "__init__.py", "server.py", "store.py", "planning.py",
        "anki_signals.py", "module_signals.py", "live_cards.py",
        "grading.py", "recall.py", "intake.py", "VisionOCR.swift",
        "index.html", "step.js", "step.css", "data/topics.json",
        "course_module.py", "course_reader.py", "course_embed.js",
        "course_embed.css",
        "labs.py", "workspace_tools.js", "workspace_tools.css", "shortcuts.js",
        "course_practice.js", "course_practice.css", "reference_service.py",
    ],
    "TermCards": [
        "__init__.py", "provider.py", "term_cards.js",
        "selection_lookup.js", "data/terms.json", "CONTENT-LICENSE.md",
    ],
    "StudyApp": [
        "tts_service.py", "tts_worker.py", "anki_bridge.py",
        "progress_store.py", "app_paths.py",
    ],
}


def make_project(root: Path) -> None:
    for folder, names in PROJECT_FILES.items():
        for name in names:
            path = root / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture\n", encoding="utf-8")
    for name in build_app.COURSE_RENDERER_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "window.ANKI_CONTEXT={notes:[],cards:[],scope:{},graph:{}};\n" if name == "anki_context_data.js" else "fixture\n"
        path.write_text(body, encoding="utf-8")
    launcher = root / "StudyApp" / "native" / "Launcher.swift"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("// fixture launcher\nNSApp.mainMenu = mainMenu\n", encoding="utf-8")
    menu = root / "StepStudy" / "native" / "WorkspaceMenu.swift"
    menu.parent.mkdir(parents=True, exist_ok=True)
    menu.write_text("// Step-only capture menu fixture\n", encoding="utf-8")


class NativeBundleTests(unittest.TestCase):
    def test_renderer_allowlist_matches_public_builder(self):
        source = Path(__file__).resolve().parents[1] / "build_public.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"CSS", "JS", "GENERATED_ASSETS"}
        }
        self.assertEqual(tuple(assignments["CSS"]), build_app.COURSE_RENDERER_CSS)
        self.assertEqual(tuple(assignments["JS"]), build_app.COURSE_RENDERER_JS)
        self.assertEqual(set(assignments["GENERATED_ASSETS"]), set(build_app.COURSE_RENDERER_ASSETS))

    def test_allowlist_copies_complete_public_renderer_and_runtime_support(self):
        with tempfile.TemporaryDirectory(prefix="step-native-allowlist-test-") as folder:
            root = Path(folder) / "project"
            runtime = Path(folder) / "Runtime"
            make_project(root)
            # These files represent private material that may coexist in a
            # checkout, but must never be included by the native builder.
            private = root / "local-private" / "runtime" / "index.html"
            private.parent.mkdir(parents=True)
            private.write_text("private guide sentinel", encoding="utf-8")
            (root / "Prototype").mkdir()
            (root / "Prototype" / "secret.txt").write_text("private sentinel", encoding="utf-8")

            copied = build_app.copy_allowlisted_files(root, runtime)

            expected_step = {
                f"{folder}/{name}" for folder, names in PROJECT_FILES.items()
                for name in names
            }
            expected_course = {f"CourseModule/{name}" for name in build_app.COURSE_RENDERER_FILES}
            expected = expected_step | expected_course
            self.assertEqual(set(copied), expected)
            actual = {path.relative_to(runtime).as_posix() for path in runtime.rglob("*") if path.is_file()}
            self.assertEqual(actual, expected)
            self.assertTrue((runtime / "CourseModule/data/public-study-guide.json").is_file())
            self.assertTrue((runtime / "CourseModule/StudyApp/study_app.js").is_file())
            self.assertTrue((runtime / "StudyApp/progress_store.py").is_file())
            self.assertFalse((runtime / "local-private").exists())
            self.assertFalse((runtime / "Prototype").exists())
            self.assertFalse((runtime / "index.html").exists())

    def test_bundle_build_uses_fake_compiler_and_cleans_temporary_source(self):
        with tempfile.TemporaryDirectory(prefix="step-native-build-test-") as folder:
            root = Path(folder) / "project"
            output = Path(folder) / "Step Study.app"
            make_project(root)
            app_script = root / "StepStudy" / "native" / "build_app.py"
            app_script.parent.mkdir(parents=True, exist_ok=True)
            app_script.write_text("# synthetic path marker\n", encoding="utf-8")
            source_paths: list[Path] = []

            def fake_compiler(command, check):
                self.assertTrue(check)
                source_path = Path(command[-3])
                source_paths.append(source_path)
                self.assertIn("installWorkspaceMenu(mainMenu)", source_path.read_text())
                self.assertIn("Step-only capture menu fixture", source_path.read_text())
                Path(command[-1]).write_bytes(b"fake executable")

            with patch.object(build_app, "__file__", str(app_script)), \
                    patch.object(sys, "argv", ["build_app.py", "--output", str(output)]), \
                    patch.object(build_app.subprocess, "run", side_effect=fake_compiler):
                self.assertEqual(build_app.main(), 0)

            self.assertTrue((output / "Contents/Resources/Runtime/CourseModule/guide_template.html").is_file())
            self.assertTrue((output / "Contents/MacOS/Step Study").is_file())
            self.assertTrue(source_paths)
            self.assertTrue(all(not path.exists() for path in source_paths))


if __name__ == "__main__":
    unittest.main()
