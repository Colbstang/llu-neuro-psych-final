"""Explicitly selected local interactive labs, separate from imported guides.

The private registry contains user-chosen paths. HTML labs run in an opaque
sandbox and can only load bounded files inside their own selected directory.
Native apps are launched by a fixed `open -a` transport, never a shell command.
"""
from __future__ import annotations

import json
import re
import secrets
import subprocess
import threading
from pathlib import Path
from typing import Callable

from StepStudy.intake import _private_path

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_LABS = 40
ID = re.compile(r"[a-f0-9]{24}\Z")
MIME = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
        ".bin": "application/octet-stream", ".wasm": "application/wasm",
        ".woff": "font/woff", ".woff2": "font/woff2"}


class LocalLabs:
    def __init__(self, registry_path: Path, *, launch: Callable[[Path], None] | None = None):
        self.path = _private_path(registry_path, "Lab registry")
        self._lock = threading.RLock()
        self.launch = launch or self._launch
        self._rows: list[dict] = []
        try:
            data = json.loads(self.path.read_text())
            self._rows = [row for row in data.get("labs", []) if isinstance(row, dict)
                          and ID.fullmatch(str(row.get("id", "")))
                          and row.get("kind") in {"html", "native"}][:MAX_LABS]
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    @staticmethod
    def _launch(path: Path) -> None:
        result = subprocess.run(["/usr/bin/open", "-a", str(path)],
                                capture_output=True, timeout=15, check=False)
        if result.returncode:
            raise OSError("The selected native lab could not open")

    def _save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema_version": 1, "labs": self._rows}, ensure_ascii=False))
        temp.chmod(0o600)
        temp.replace(self.path)

    def register(self, selected_path: str, title: str = "") -> dict:
        if not isinstance(selected_path, str) or not selected_path.strip() or len(selected_path) > 2000:
            raise ValueError("Choose a local HTML file or native .app")
        original = Path(selected_path).expanduser()
        if original.is_symlink():
            raise ValueError("Choose the original lab file rather than a shortcut")
        path = _private_path(original, "Local lab")
        if path.is_dir() and path.suffix.lower() == ".app":
            if not (path / "Contents" / "Info.plist").is_file():
                raise ValueError("This is not an installed native app bundle")
            kind = "native"
        elif path.is_file() and path.suffix.lower() in {".html", ".htm"}:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("The lab HTML exceeds the 32 MiB limit")
            kind = "html"
        else:
            raise ValueError("Choose a local HTML file or native .app. A model needs an HTML viewer.")
        if not isinstance(title, str) or len(title) > 120:
            raise ValueError("Choose a short lab name")
        with self._lock:
            existing = next((row for row in self._rows if row.get("path") == str(path)), None)
            if existing:
                return self._public(existing)
            if len(self._rows) >= MAX_LABS:
                raise ValueError("Remove a lab before adding another")
            row = {"id": secrets.token_hex(12), "title": title.strip() or path.stem,
                   "kind": kind, "path": str(path)}
            self._rows.append(row)
            self._save()
            return self._public(row)

    @staticmethod
    def _public(row: dict) -> dict:
        return {"id": row["id"], "title": row.get("title", "Local lab"), "kind": row["kind"],
                "available": Path(row.get("path", "")).exists(),
                **({"url": f"/lab/{row['id']}/"} if row["kind"] == "html" else {})}

    def list(self) -> list[dict]:
        with self._lock:
            return [self._public(row) for row in self._rows]

    def _get(self, identity: str) -> dict:
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise KeyError("Lab not found")
        with self._lock:
            row = next((row for row in self._rows if row["id"] == identity), None)
        if row is None:
            raise KeyError("Lab not found")
        return row

    def open_native(self, identity: str) -> dict:
        row = self._get(identity)
        path = Path(row["path"])
        if row["kind"] != "native" or not path.is_dir() or path.suffix.lower() != ".app":
            raise ValueError("This native lab is unavailable")
        self.launch(path)
        return {"ok": True, "opened": True}

    def remove(self, identity: str) -> dict:
        self._get(identity)
        with self._lock:
            self._rows = [row for row in self._rows if row["id"] != identity]
            self._save()
        return {"ok": True}

    def asset(self, identity: str, relative: str = "") -> tuple[bytes, str]:
        row = self._get(identity)
        if row["kind"] != "html":
            raise KeyError("Lab file not found")
        entry = Path(row["path"])
        root = entry.parent.resolve()
        components = relative.split("/") if relative else [entry.name]
        if any(part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in components):
            raise KeyError("Lab file not found")
        target = root.joinpath(*components)
        # Reject symbolic links anywhere in the requested path, including an
        # entry replaced after registration. Files cannot escape the lab root.
        for count in range(1, len(components) + 1):
            if root.joinpath(*components[:count]).is_symlink():
                raise KeyError("Lab file not found")
        target = target.resolve()
        if root not in target.parents or not target.is_file() or target.suffix.lower() not in MIME:
            raise KeyError("Lab file not found")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Lab asset exceeds the 32 MiB limit")
        return target.read_bytes(), MIME[target.suffix.lower()]


def lab_policy(origin: str, identity: str) -> str:
    """No parent origin, navigation, forms, network or app-data privileges."""
    prefix = f"{origin}/lab/{identity}/"
    return ("sandbox allow-scripts; default-src 'none'; "
            f"script-src 'unsafe-inline' {prefix}; style-src 'unsafe-inline' {prefix}; "
            f"img-src data: blob: {prefix}; font-src {prefix}; connect-src {prefix}; "
            "object-src 'none'; frame-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'self'")
