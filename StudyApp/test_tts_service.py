"""Checks for the packaged, offline speech service."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

try:
    from .tts_service import LocalTTSService, TTSUnavailable, VOICES
except ImportError:
    from tts_service import LocalTTSService, TTSUnavailable, VOICES


class TTSServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = LocalTTSService()

    def tearDown(self) -> None:
        self.service.close()

    def test_status_describes_pinned_model_and_voices(self) -> None:
        status = self.service.status()
        self.assertEqual(status["model"], "KittenML/kitten-tts-micro-0.8")
        self.assertEqual(tuple(status["voices"]), VOICES)
        self.assertEqual(status["sample_rate"], 24_000)

    def test_rejects_invalid_text_and_voice_without_loading_model(self) -> None:
        with self.assertRaises(ValueError):
            self.service.synthesize("   ")
        with self.assertRaises(ValueError):
            self.service.synthesize("Aphasia.", voice="unknown")
        with self.assertRaises(ValueError):
            self.service.synthesize("x" * 181)

    def test_offline_synthesis_returns_a_valid_wav(self) -> None:
        if not self.service.status()["available"]:
            self.skipTest("Run install_tts.py before the local inference check")
        audio = self.service.synthesize("Aphasia.", voice="Bella")
        with wave.open(io.BytesIO(audio), "rb") as wav:
            self.assertEqual(wav.getframerate(), 24_000)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertGreater(wav.getnframes(), 1000)
        self.assertIs(audio, self.service.synthesize("Aphasia.", voice="Bella"))


class WorkerFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="llu-tts-test-")
        root = Path(self.temp_dir.name)
        python = root / "bin" / "python"
        python.parent.mkdir()
        python.symlink_to(sys.executable)
        self.pid_file = root / "worker.pid"
        self.root = root
        self.model_dir = root / "synthetic-model"
        self.model_dir.mkdir()
        for name in ("config.json", "kitten_tts_micro_v0_8.onnx", "voices.npz"):
            (self.model_dir / name).write_bytes(b"synthetic test fixture")

    def tearDown(self) -> None:
        if hasattr(self, "service"):
            self.service.close()
        self.temp_dir.cleanup()

    def _service(self, worker_code: str) -> LocalTTSService:
        worker = self.root / "fake_worker.py"
        worker.write_text(worker_code, encoding="utf-8")
        self.service = LocalTTSService(
            model_dir=self.model_dir,
            env_dir=self.root,
            startup_timeout=0.2,
            synthesis_timeout=0.2,
        )
        self.service.worker = worker
        self.assertTrue(self.service.status()["available"])
        return self.service

    def _assert_child_reaped(self) -> None:
        pid = int(self.pid_file.read_text(encoding="utf-8"))
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_startup_timeout_stops_and_reaps_worker(self) -> None:
        service = self._service(
            "import os, sys, time\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "sys.stdout.write('{\"ready\":')\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(TTSUnavailable, "startup timed out"):
            service.synthesize("Aphasia.")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNone(service._process)
        self._assert_child_reaped()

    def test_synthesis_timeout_stops_and_reaps_worker(self) -> None:
        service = self._service(
            "import json, os, sys, time\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "sys.stdout.write('{\"ok\":')\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()
        with self.assertRaisesRegex(TTSUnavailable, "synthesis timed out"):
            service.synthesize("Aphasia.")
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIsNone(service._process)
        self._assert_child_reaped()

    def test_malformed_response_stops_worker(self) -> None:
        service = self._service(
            "import json, os, sys\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "print('{broken json', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        with self.assertRaisesRegex(TTSUnavailable, "invalid response"):
            service.synthesize("Aphasia.")
        self.assertIsNone(service._process)
        self._assert_child_reaped()

    def test_worker_error_stops_worker(self) -> None:
        service = self._service(
            "import json, os, sys\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "print(json.dumps({'ok': False, 'error': 'inference failed'}), flush=True)\n"
            "sys.stdin.readline()\n"
        )
        with self.assertRaisesRegex(ValueError, "inference failed"):
            service.synthesize("Aphasia.")
        self.assertIsNone(service._process)
        self._assert_child_reaped()

    def test_oversized_audio_is_rejected_and_worker_stopped(self) -> None:
        service = self._service(
            "import base64, json, os, sys\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            "print(json.dumps({'ready': True}), flush=True)\n"
            "sys.stdin.readline()\n"
            "audio = b'RIFF' + b'0' * (2 * 1024 * 1024)\n"
            "print(json.dumps({'ok': True, 'wav_b64': base64.b64encode(audio).decode()}), flush=True)\n"
            "sys.stdin.readline()\n"
        )
        with self.assertRaisesRegex(TTSUnavailable, "outside the allowed"):
            service.synthesize("Aphasia.")
        self.assertIsNone(service._process)
        self._assert_child_reaped()


if __name__ == "__main__":
    unittest.main()
