"""Build a separate Step Study macOS launcher without changing LLU Study."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# The course renderer is rebuilt from reviewed repository code and the public
# metadata-only dataset. Keep this list explicit: local bundles, private source
# packs, and generated user state must never be swept into the native app.
COURSE_RENDERER_CSS = (
    "guide_style.css", "objective_style.css", "reading_tools.css",
    "continuous_style.css", "workspace_style.css", "book_highlights.css",
    "freehand.css", "study_practice.css", "anki_context.css",
    "objective_compact.css", "week8_style.css", "image_navigation.css",
    "semantic_search.css", "source_viewer.css", "reference_workspace.css",
    "StudyApp/study_app.css", "study_dashboard.css",
)
COURSE_RENDERER_JS = (
    "guide_app.js", "study_scope.js", "reference_panel.js",
    "image_navigation.js", "book_highlights.js", "objective_ui.js",
    "workspace_views.js", "reading_tools.js", "freehand.js",
    "study_practice.js", "guide_skim.js", "anki_context.js",
    "semantic_search.js", "source_viewer.js", "reference_workspace.js",
    "StudyApp/study_app.js", "study_dashboard.js",
)
COURSE_RENDERER_ASSETS = (
    "assets/generated/intracranial-compartments.png",
    "assets/generated/white-matter-cell-targets.png",
    "assets/generated/antiseizure-targets.png",
    "assets/generated/path-completion/imnm.png",
    "assets/generated/path-completion/cpt2-periodic-v2.png",
    "assets/generated/path-completion/feeding-patterns.png",
    "assets/generated/path-completion/apnea-mechanics.png",
)
COURSE_RENDERER_FILES = (
    "guide_template.html", "build_public.py", "anki_context_data.js",
    "data/public-study-guide.json", *COURSE_RENDERER_CSS,
    *COURSE_RENDERER_JS, *COURSE_RENDERER_ASSETS,
)


def copy_allowlisted_files(project: Path, runtime: Path) -> list[str]:
    """Copy the Step app and trusted public course renderer into a runtime."""
    files = {
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
    copied: list[str] = []
    for directory, names in files.items():
        for name in names:
            source_file = project / directory / name
            if not source_file.is_file():
                if name == "__init__.py":
                    continue
                raise FileNotFoundError(source_file)
            destination = runtime / directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
            copied.append(destination.relative_to(runtime).as_posix())

    for name in COURSE_RENDERER_FILES:
        source_file = project / name
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        destination = runtime / "CourseModule" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, destination)
        copied.append(destination.relative_to(runtime).as_posix())
    return copied


def main():
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, default=Path.home() / "Applications" / "Step Study.app")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.name != "Step Study.app":
        parser.error("Use the separate Step Study.app bundle name")
    contents = output / "Contents"
    if output.exists():
        shutil.rmtree(output)
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    # Package the reviewed public runtime so opening the app never depends on
    # permission to read a repository on Desktop. Private source packs stay out.
    runtime = contents / "Resources" / "Runtime"
    copy_allowlisted_files(project, runtime)
    base = project / "StudyApp" / "native" / "Launcher.swift"
    source = base.read_text().replace("LLU Study", "Step Study").replace("llu-study-app", "step-study").replace("8770", "8773")
    source = source.replace("NSApp.mainMenu = mainMenu", "installWorkspaceMenu(mainMenu)\n        NSApp.mainMenu = mainMenu")
    source += "\n" + (project / "StepStudy" / "native" / "WorkspaceMenu.swift").read_text()
    source = source.replace("process.arguments = [serverPath]", 'process.arguments = [serverPath]\n        var environment = ProcessInfo.processInfo.environment\n        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (environment["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin")\n        process.environment = environment')
    with tempfile.TemporaryDirectory(prefix="step-study-launcher-") as folder:
        swift = Path(folder) / "Launcher.swift"
        swift.write_text(source)
        executable = contents / "MacOS" / "Step Study"
        subprocess.run(["xcrun", "swiftc", "-O", "-framework", "AppKit", "-framework", "WebKit", str(swift), "-o", str(executable)], check=True)
        executable.chmod(0o755)
    info = {"CFBundleDevelopmentRegion": "en", "CFBundleExecutable": "Step Study",
            "CFBundleIdentifier": "org.llu.stepstudy", "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": "Step Study", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.1", "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "StudyAppServerPath": str(runtime / "StepStudy" / "server.py"),
            "StudyAppPythonPath": os.path.abspath(str(args.python.expanduser())),
            "StudyAppWorkingDirectory": str(runtime), "StudyAppURL": "http://127.0.0.1:8773/"}
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump(info, file)
    print(f"Built {output}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
