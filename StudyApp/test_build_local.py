"""The local builder stages generic search and TTS code, not indexed data."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_local


class BuildLocalRuntimeTests(unittest.TestCase):
    def test_temporary_build_stages_search_background_and_tts_modules_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            runtime = private / "runtime"
            bundle = root / "minimal-guide.html"
            bundle.write_text('<script>const DATA={"pages":[],"assets":{}};</script>', encoding="utf-8")

            with patch.object(build_local, "PRIVATE", private), patch.object(build_local, "RUNTIME", runtime), \
                    patch.object(sys, "argv", ["build_local.py", "--bundle", str(bundle),
                                               "--anki-context", str(root / "missing-anki.js")]), \
                    contextlib.redirect_stdout(io.StringIO()):
                build_local.main()

            self.assertTrue((runtime / "index.html").is_file())
            for name in ("semantic_search.py", "reference_search.py", "background_reference.py",
                         "prepare_reference_catalog.py", "requirements.txt"):
                self.assertTrue((runtime / name).is_file(), name)
            for name in ("tts_service.py", "tts_worker.py", "install_tts.py", "tts-requirements.lock"):
                self.assertTrue((runtime / "StudyApp" / name).is_file(), name)

            # A built local runtime without a source pack stays empty and offline.
            spec = importlib.util.spec_from_file_location("runtime_background_reference",
                                                          runtime / "background_reference.py")
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            background = module.BackgroundReference(runtime)
            self.assertEqual(background.records, [])
            self.assertEqual(background.search("multiple sclerosis"), [])

            # Speech code resolves its worker relative to the staged StudyApp directory.
            tts_spec = importlib.util.spec_from_file_location("runtime_tts_service",
                                                              runtime / "StudyApp" / "tts_service.py")
            tts_module = importlib.util.module_from_spec(tts_spec)
            assert tts_spec and tts_spec.loader
            tts_spec.loader.exec_module(tts_module)
            service = tts_module.LocalTTSService(model_dir=root / "absent-model", env_dir=root / "absent-env")
            self.assertEqual(service.project_dir, runtime.resolve())
            self.assertEqual(service.worker, runtime.resolve() / "StudyApp" / "tts_worker.py")
            self.assertFalse(service.status()["available"])
            service.close()

            self.assertFalse((runtime / "reference_index").exists())
            self.assertFalse((runtime / "semantic_index").exists())


if __name__ == "__main__":
    unittest.main()
