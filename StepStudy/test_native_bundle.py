"""Isolated packaging tests for the Step Study native runtime allowlist."""
from __future__ import annotations

import ast
import plistlib
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
            "concept_learning.py", "study_session.py", "learning_sources.py",
            "learning_flow.js", "learning_flow.css", "repair_practice.js", "repair_practice.css",
            "visual_recall.py", "visual_practice.js", "visual_practice.css",
            "navigation.js", "course_navigation.js", "anki_review.js", "anki_review.css", "quiz_store.py",
            "gaze_lab.html", "gaze_lab.js", "gaze_lab.css",
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
    launcher.write_text("// fixture launcher\nNSApp.mainMenu = mainMenu\nprocess.arguments = [serverPath]\n", encoding="utf-8")
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
                self.assertIn('serverArguments += ["--data-dir", dataDirectory]', source_path.read_text())
                Path(command[-1]).write_bytes(b"fake executable")

            with patch.object(build_app, "__file__", str(app_script)), \
                    patch.object(sys, "argv", ["build_app.py", "--output", str(output)]), \
                    patch.object(build_app.subprocess, "run", side_effect=fake_compiler):
                self.assertEqual(build_app.main(), 0)

            self.assertTrue((output / "Contents/Resources/Runtime/CourseModule/guide_template.html").is_file())
            self.assertTrue((output / "Contents/MacOS/Step Study").is_file())
            with (output / "Contents/Info.plist").open("rb") as file:
                info = plistlib.load(file)
            self.assertNotIn("StudyAppDataDirectory", info)
            self.assertEqual(info["StudyAppServerPath"], str((output / "Contents/Resources/Runtime/StepStudy/server.py").resolve()))
            self.assertTrue(source_paths)
            self.assertTrue(all(not path.exists() for path in source_paths))


    def test_private_data_directory_is_only_stored_in_generated_plist(self):
        with tempfile.TemporaryDirectory(prefix="step-native-private-config-test-") as folder:
            root = Path(folder) / "project"
            output = Path(folder) / "Step Study.app"
            private_data = Path(folder) / "Private study state"
            private_data.mkdir()
            (private_data / "personal-progress.sqlite3").write_bytes(b"private data sentinel")
            make_project(root)
            app_script = root / "StepStudy/native/build_app.py"
            app_script.write_text("# synthetic path marker\n")
            def fake_compiler(command, check):
                source = Path(command[-3]).read_text()
                self.assertNotIn(str(private_data), source)
                self.assertIn('configuration["StudyAppDataDirectory"]', source)
                Path(command[-1]).write_bytes(b"fake executable")
            with patch.object(build_app, "__file__", str(app_script)), \
                    patch.object(sys, "argv", ["build_app.py", "--output", str(output), "--data-dir", str(private_data)]), \
                    patch.object(build_app.subprocess, "run", side_effect=fake_compiler):
                self.assertEqual(build_app.main(), 0)
            with (output / "Contents/Info.plist").open("rb") as file:
                info = plistlib.load(file)
            self.assertEqual(info["StudyAppDataDirectory"], str(private_data.resolve()))
            self.assertEqual(info["StudyAppServerPath"], str((output / "Contents/Resources/Runtime/StepStudy/server.py").resolve()))
            for path in (output / "Contents/Resources/Runtime").rglob("*"):
                if path.is_file():
                    self.assertNotIn(str(private_data).encode(), path.read_bytes())
                    self.assertNotIn(b"private data sentinel", path.read_bytes())
            self.assertEqual((private_data / "personal-progress.sqlite3").read_bytes(), b"private data sentinel")

    def test_data_directory_inside_checkout_or_bundle_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory(prefix="step-native-data-boundary-test-") as folder:
            root = Path(folder) / "project"
            output = Path(folder) / "Step Study.app"
            app_script = root / "StepStudy/native/build_app.py"
            app_script.parent.mkdir(parents=True)
            app_script.write_text("# synthetic path marker\n")
            for data_directory in (root / "private-state", output / "Contents/Resources/state"):
                with self.subTest(path=data_directory), \
                        patch.object(build_app, "__file__", str(app_script)), \
                        patch.object(sys, "argv", ["build_app.py", "--output", str(output), "--data-dir", str(data_directory)]), \
                        patch.object(build_app.subprocess, "run") as compiler, \
                        self.assertRaises(SystemExit):
                    build_app.main()
                compiler.assert_not_called()
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
