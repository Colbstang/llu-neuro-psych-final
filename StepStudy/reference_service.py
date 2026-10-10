"""Supervise an explicitly configured local reference service, never a UI command."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import urlopen


class ReferenceService:
    def __init__(self, data_dir: Path, *, probe=None, spawn=None):
        self.data_dir = Path(data_dir)
        self.probe = probe or self._probe
        self.spawn = spawn or subprocess.Popen
        self.process = None
        self.lock = threading.Lock()
        self.attempted_at = -100.0
        self.reason = "Reference service is not configured"

    @staticmethod
    def _probe():
        try:
            with urlopen("http://127.0.0.1:8768/health", timeout=.5) as response:
                record = json.loads(response.read(16_384))
                return record.get("ready") is True and record.get("local_only") is True
        except (OSError, ValueError):
            return False

    def start(self):
        """Warm startup is cheap; an existing independent service is left intact."""
        with self.lock:
            if self.probe():
                self.reason = "Local reference service ready"
                return True
            if self.process is not None and self.process.poll() is None:
                self.reason = "Local reference service is starting"
                return False
            if time.monotonic() - self.attempted_at < 10:
                return False
            self.attempted_at = time.monotonic()
            path = self.data_dir / "reference-service.json"
            try:
                if path.stat().st_size > 8192:
                    raise ValueError("Reference configuration exceeds its limit")
                config = json.loads(path.read_text())
                values = [config.get(key) for key in ("python_path", "script_path", "reference_root")]
                if any(not isinstance(value, str) or not value or len(value) > 4096 for value in values):
                    raise ValueError("Reference configuration needs explicit local paths")
                # Keep the virtualenv entry point; resolving its Python symlink
                # would discard the environment's installed dependencies.
                python = Path(values[0]).expanduser().absolute()
                script, root = [Path(value).expanduser().resolve() for value in values[1:]]
                if not python.is_file() or not os.access(python, os.X_OK) or not script.is_file() or not root.is_dir():
                    raise ValueError("Configured local reference service is unavailable")
                if script.name != "semantic_search.py":
                    raise ValueError("Only the configured semantic reference service can start")
                log_path = self.data_dir / "reference-service.log"
                with log_path.open("ab") as log:
                    self.process = self.spawn([str(python), str(script), "--port", "8768", "--reference-root", str(root)], cwd=str(script.parent), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                self.reason = "Local reference service is starting"
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                self.reason = "Reference startup unavailable: " + str(error)
            return False

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
            self.process = None
