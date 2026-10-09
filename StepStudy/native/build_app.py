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
    files = {
        "StepStudy": ["__init__.py", "server.py", "store.py", "planning.py", "anki_signals.py", "module_signals.py", "live_cards.py", "grading.py", "recall.py", "intake.py", "VisionOCR.swift", "index.html", "step.js", "step.css", "data/topics.json"],
        "TermCards": ["__init__.py", "provider.py", "term_cards.js", "selection_lookup.js", "data/terms.json", "CONTENT-LICENSE.md"],
        "StudyApp": ["tts_service.py", "tts_worker.py", "anki_bridge.py"],
    }
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
    base = project / "StudyApp" / "native" / "Launcher.swift"
    source = base.read_text().replace("LLU Study", "Step Study").replace("llu-study-app", "step-study").replace("8770", "8773")
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
