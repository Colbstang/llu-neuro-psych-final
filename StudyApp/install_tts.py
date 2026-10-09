#!/usr/bin/env python3
"""Install the pinned, offline Kitten Micro 0.8 model and isolated runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


MODEL_ID = "KittenML/kitten-tts-micro-0.8"
MODEL_REVISION = "1ccf72b2c2048fd17efac7de2fab32d10e225084"
MODEL_LICENSE = "Apache-2.0 (Hugging Face repository metadata)"
MODEL_FILES = {
    "config.json": "1f0bd2208348f9211cb0da64fcd1536eb28228571cc6b09e767eb6e203a0a532",
    "kitten_tts_micro_v0_8.onnx": "95481626fee1ba70ce683e69c534fc7cb38433c46ce42d3abbeafb4b9f1a4123",
    "voices.npz": "112710c1be8ad0e967c190fb0fd95cbe5848ec4791b93209f20b28b7da20dac1",
}
WHEEL_URL = "https://github.com/KittenML/KittenTTS/releases/download/0.8.1/kittentts-0.8.1-py3-none-any.whl"
WHEEL_SHA256 = "482a436c4f1f3192153710376e459ff3689517ebcda7c2b051e2fd4187b41851"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_verified(url: str, target: Path, expected_hash: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LLU-Study-TTS-installer/1"})
        with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual_hash = sha256(temporary)
        if actual_hash != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch for {target.name}: expected {expected_hash}, got {actual_hash}")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def default_base_python() -> Path:
    project = Path(__file__).resolve().parent.parent
    preferred = project / "Prototype" / ".semantic-env" / "bin" / "python"
    return preferred if preferred.is_file() else Path(sys.executable)


def main() -> int:
    support = Path.home() / "Library" / "Application Support" / "LLU Study"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=support / "tts-models" / "kitten-tts-micro-0.8")
    parser.add_argument("--env-dir", type=Path, default=support / ".tts-env")
    parser.add_argument("--base-python", type=Path, default=default_base_python(),
                        help="Python used to create the isolated environment")
    parser.add_argument("--skip-python", action="store_true", help="Only install/verify model files")
    args = parser.parse_args()
    model_dir = args.model_dir.expanduser().resolve()
    env_dir = args.env_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)

    for filename, expected_hash in MODEL_FILES.items():
        destination = model_dir / filename
        if destination.is_file() and sha256(destination) == expected_hash:
            print(f"Verified {filename}")
            continue
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{filename}"
        print(f"Downloading and verifying {filename}...", flush=True)
        download_verified(url, destination, expected_hash)

    manifest = {
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "license": MODEL_LICENSE,
        "license_url": f"https://huggingface.co/{MODEL_ID}",
        "files": MODEL_FILES,
        "python_package": "KittenTTS 0.8.1",
        "python_wheel_url": WHEEL_URL,
        "python_wheel_sha256": WHEEL_SHA256,
    }
    (model_dir / "MODEL_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    if args.skip_python:
        print(f"Model installed at {model_dir}")
        return 0
    base_python = args.base_python.expanduser().resolve()
    if not base_python.is_file():
        parser.error(f"Base Python does not exist: {base_python}")
    if not (env_dir / "bin" / "python").is_file():
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(base_python), "-m", "venv", str(env_dir)], check=True)
    python = env_dir / "bin" / "python"
    with tempfile.TemporaryDirectory(prefix="llu-tts-install-") as scratch:
        wheel = Path(scratch) / "kittentts-0.8.1-py3-none-any.whl"
        print("Downloading and verifying KittenTTS runtime...", flush=True)
        download_verified(WHEEL_URL, wheel, WHEEL_SHA256)
        dependency_lock = Path(__file__).resolve().with_name("tts-requirements.lock")
        subprocess.run([
            str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(dependency_lock)
        ], check=True)
        subprocess.run([
            str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-deps", str(wheel)
        ], check=True)
    print(f"Installed isolated runtime at {env_dir}")
    print(f"Installed local model at {model_dir}")
    print("Synthesis uses these local files; no Hugging Face request is made during inference.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
