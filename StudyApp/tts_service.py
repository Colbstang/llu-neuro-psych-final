"""Offline, local KittenTTS service for short read-aloud text.

The application server can import this module in its existing environment. TTS
inference runs in one persistent process under an isolated app-data runtime.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import selectors
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any


MODEL_ID = "KittenML/kitten-tts-micro-0.8"
MODEL_REVISION = "1ccf72b2c2048fd17efac7de2fab32d10e225084"
VOICES = ("Bella", "Jasper", "Luna", "Bruno", "Rosie", "Hugo", "Kiki", "Leo")
SAMPLE_RATE = 24_000
MAX_TEXT_CHARS = 180
MAX_OUTPUT_WAV_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_LINE_BYTES = (MAX_OUTPUT_WAV_BYTES + 2) // 3 * 4 + 8192
WORKER_STARTUP_TIMEOUT_SECONDS = 45.0
SYNTHESIS_TIMEOUT_SECONDS = 30.0
MAX_STARTUP_LINE_BYTES = 64 * 1024


class TTSUnavailable(RuntimeError):
    """Raised when the local model or isolated inference environment is absent."""


class LocalTTSService:
    def __init__(
        self,
        model_dir: Path | None = None,
        env_dir: Path | None = None,
        *,
        startup_timeout: float = WORKER_STARTUP_TIMEOUT_SECONDS,
        synthesis_timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
    ) -> None:
        self.project_dir = Path(__file__).resolve().parent.parent
        self.model_dir = Path(model_dir or os.environ.get(
            "LLU_TTS_MODEL_DIR",
            Path.home() / "Library" / "Application Support" / "LLU Study" / "tts-models" / "kitten-tts-micro-0.8",
        )).expanduser()
        support_dir = Path.home() / "Library" / "Application Support" / "LLU Study"
        self.env_dir = Path(env_dir or os.environ.get("LLU_TTS_ENV_DIR", support_dir / ".tts-env")).expanduser()
        self.python = self.env_dir / "bin" / "python"
        self.worker = Path(__file__).resolve().with_name("tts_worker.py")
        self.startup_timeout = startup_timeout
        self.synthesis_timeout = synthesis_timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_buffer = bytearray()
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self.last_generation_seconds: float | None = None
        atexit.register(self.close)

    def status(self) -> dict[str, Any]:
        required = ("config.json", "kitten_tts_micro_v0_8.onnx", "voices.npz")
        missing = [name for name in required if not (self.model_dir / name).is_file()]
        installed = self.python.is_file() and self.worker.is_file() and not missing
        result: dict[str, Any] = {
            "available": bool(installed),
            "model": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "voices": list(VOICES),
            "sample_rate": SAMPLE_RATE,
            "running": self._process is not None and self._process.poll() is None,
            "message": "Ready for local speech." if installed else "Local speech model is not installed.",
        }
        if missing:
            result["missing_model_files"] = missing
        if not self.python.is_file():
            result["missing_environment"] = str(self.python)
        return result

    def synthesize(self, text: str, voice: str = "Bella") -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must not be empty")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"text is limited to {MAX_TEXT_CHARS} characters")
        if voice not in VOICES:
            raise ValueError(f"voice must be one of: {', '.join(VOICES)}")

        cache_key = hashlib.sha256((voice + "\0" + text.strip()).encode("utf-8")).hexdigest()
        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]
            process = self._ensure_worker()
            assert process.stdin is not None and process.stdout is not None
            request = json.dumps({"text": text, "voice": voice}, ensure_ascii=False).encode("utf-8") + b"\n"
            try:
                os.write(process.stdin.fileno(), request)
                line = self._readline(self.synthesis_timeout, MAX_RESPONSE_LINE_BYTES)
            except TimeoutError as exc:
                self._stop_worker()
                raise TTSUnavailable(
                    f"Local speech synthesis timed out after {self.synthesis_timeout:g} seconds"
                ) from exc
            except (OSError, TTSUnavailable):
                self._stop_worker()
                raise
            if not line:
                self._stop_worker()
                raise TTSUnavailable("The local speech worker stopped before returning audio")
            try:
                response = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._stop_worker()
                raise TTSUnavailable("The local speech worker returned an invalid response") from exc
            if not isinstance(response, dict):
                self._stop_worker()
                raise TTSUnavailable("The local speech worker returned an invalid response")
            if not response.get("ok"):
                self._stop_worker()
                raise ValueError(response.get("error", "Speech synthesis failed"))
            try:
                audio = base64.b64decode(response["wav_b64"], validate=True)
            except (KeyError, TypeError, ValueError) as exc:
                self._stop_worker()
                raise TTSUnavailable("The local speech worker returned invalid audio data") from exc
            if len(audio) > MAX_OUTPUT_WAV_BYTES or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
                self._stop_worker()
                raise TTSUnavailable("The local speech worker returned audio outside the allowed format or size")
            self.last_generation_seconds = response.get("generation_seconds")
            self._cache[cache_key] = audio
            if len(self._cache) > 32:
                self._cache.popitem(last=False)
            return audio

    def _ensure_worker(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        status = self.status()
        if not status["available"]:
            raise TTSUnavailable(status["message"] + " Run StudyApp/install_tts.py to install it.")
        env = os.environ.copy()
        env["LLU_TTS_MODEL_DIR"] = str(self.model_dir)
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        self._stdout_buffer.clear()
        self._process = subprocess.Popen(
            [str(self.python), "-u", str(self.worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            bufsize=0,
        )
        assert self._process.stdout is not None
        os.set_blocking(self._process.stdout.fileno(), False)
        try:
            startup = self._readline(self.startup_timeout, MAX_STARTUP_LINE_BYTES)
            response = json.loads(startup)
        except TimeoutError as exc:
            self._stop_worker()
            raise TTSUnavailable(
                f"Local speech worker startup timed out after {self.startup_timeout:g} seconds"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, TTSUnavailable) as exc:
            self._stop_worker()
            raise TTSUnavailable("The local speech worker failed to start") from exc
        if not isinstance(response, dict):
            self._stop_worker()
            raise TTSUnavailable("The local speech worker failed to start")
        if not response.get("ready"):
            message = response.get("error", "The local speech worker failed to load the model")
            self._stop_worker()
            raise TTSUnavailable(message)
        return self._process

    def _readline(self, timeout: float, max_bytes: int) -> str:
        process = self._process
        if process is None or process.stdout is None:
            raise TTSUnavailable("The local speech worker is not running")
        fd = process.stdout.fileno()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                newline = self._stdout_buffer.find(b"\n")
                if newline >= 0:
                    if newline > max_bytes:
                        raise TTSUnavailable("The local speech worker response exceeded the size limit")
                    raw = bytes(self._stdout_buffer[:newline])
                    del self._stdout_buffer[:newline + 1]
                    try:
                        return raw.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise TTSUnavailable("The local speech worker returned invalid UTF-8") from exc
                if len(self._stdout_buffer) > max_bytes:
                    raise TTSUnavailable("The local speech worker response exceeded the size limit")
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Timed out waiting for the local speech worker")
                try:
                    chunk = os.read(fd, min(65_536, max_bytes + 1 - len(self._stdout_buffer)))
                except BlockingIOError:
                    continue
                if not chunk:
                    if self._stdout_buffer:
                        raise TTSUnavailable("The local speech worker returned an incomplete response")
                    return ""
                self._stdout_buffer.extend(chunk)

    def _stop_worker(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            if process is None:
                return
            if process.stdin is not None and process.poll() is None:
                try:
                    process.stdin.close()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
            for stream in (process.stdin, process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()


_default_service: LocalTTSService | None = None
_default_lock = threading.Lock()


def get_tts_service() -> LocalTTSService:
    """Return the shared, lazy local TTS service instance."""
    global _default_service
    with _default_lock:
        if _default_service is None:
            _default_service = LocalTTSService()
        return _default_service
