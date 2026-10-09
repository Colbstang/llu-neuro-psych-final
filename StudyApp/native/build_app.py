#!/usr/bin/env python3
"""Build the dependency-free LLU Study macOS app bundle."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    native_dir = Path(__file__).resolve().parent
    study_app_dir = native_dir.parent
    project_dir = study_app_dir.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, default=project_dir / "study_app_server.py",
                        help="Path to the local study_app_server.py script")
    parser.add_argument("--python", type=Path, default=Path(sys.executable),
                        help="Python interpreter used to run the local server")
    parser.add_argument("--output", type=Path, default=project_dir / "local-private" / "LLU Study.app",
                        help="Output app bundle path (kept outside tracked files)")
    args = parser.parse_args()

    def absolute_path_preserving_symlinks(path: Path) -> Path:
        # Keep a venv `bin/python` symlink intact when the caller supplies one.
        return Path(os.path.abspath(str(path.expanduser())))

    source = native_dir / "Launcher.swift"
    if not source.is_file():
        parser.error(f"Swift source not found: {source}")

    output = args.output.expanduser().resolve()
    contents = output / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    if output.exists():
        if output.is_dir():
            shutil.rmtree(output)
        else:
            output.unlink()
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    executable = macos / "LLU Study"
    subprocess.run([
        "xcrun", "swiftc", "-O", "-framework", "AppKit", "-framework", "WebKit",
        str(source), "-o", str(executable),
    ], check=True)
    executable.chmod(0o755)

    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": "LLU Study",
        "CFBundleIdentifier": "org.llu.studyapp",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "LLU Study",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.1",
        "CFBundleVersion": "2",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "StudyAppServerPath": str(args.server.expanduser().resolve()),
        "StudyAppPythonPath": str(absolute_path_preserving_symlinks(args.python)),
        "StudyAppWorkingDirectory": str(project_dir.resolve()),
        "StudyAppURL": "http://127.0.0.1:8770/",
    }
    with (contents / "Info.plist").open("wb") as plist_file:
        plistlib.dump(info, plist_file, sort_keys=True)

    print(f"Built {output}")
    print(f"Server: {info['StudyAppServerPath']}")
    print(f"Python: {info['StudyAppPythonPath']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
