"""Explicitly selected local interactive labs, separate from imported guides.

The private registry contains user-chosen paths. HTML labs run in an opaque
sandbox and can only load bounded files inside their own selected directory.
Selected gaze JSON uses a trusted renderer and an allowlisted observation
schema. Native registrations are preserved, but need a browser export for the
in-app viewer; the legacy explicit launch transport never executes a shell.
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
MAX_LESSON_BYTES = 1024 * 1024
MAX_LABS = 40
ID = re.compile(r"[a-f0-9]{24}\Z")
LESSON_ID = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
VIEWER_ROOT = Path(__file__).resolve().parent
GAZE_VIEWER = {"": "gaze_lab.html", "gaze_lab.js": "gaze_lab.js", "gaze_lab.css": "gaze_lab.css"}
MIME = {".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
        ".bin": "application/octet-stream", ".wasm": "application/wasm",
        ".data": "application/octet-stream", ".unityweb": "application/octet-stream",
        ".drc": "application/octet-stream", ".ktx2": "image/ktx2", ".hdr": "application/octet-stream",
        ".woff": "font/woff", ".woff2": "font/woff2"}


def gaze_lesson(path: Path) -> dict:
    """Allow only the authored circuit/observation schema, never executable text."""
    if path.is_symlink() or path.resolve() != path or not path.is_file() or path.stat().st_size > MAX_LESSON_BYTES:
        raise ValueError("The selected gaze lesson is unavailable or exceeds 1 MiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("Choose a valid local gaze lesson JSON") from None
    if not isinstance(data, dict):
        raise ValueError("Choose a valid local gaze lesson JSON")
    def label(value, maximum=1200):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError("The gaze lesson has an invalid label")
        return value
    def identity(value):
        if not isinstance(value, str) or not LESSON_ID.fullmatch(value):
            raise ValueError("The gaze lesson has an invalid identity")
        return value
    entities = data.get("entities", [])
    if not isinstance(entities, list) or not 2 <= len(entities) <= 60:
        raise ValueError("The gaze lesson needs a bounded entity list")
    clean_entities = []
    for item in entities:
        if not isinstance(item, dict): raise ValueError("The gaze lesson has an invalid entity")
        clean_entities.append({"id": identity(item.get("id")), "name": label(item.get("name"), 180),
                               "function": label(item.get("function")), "side": label(item.get("side"), 20)})
    ids = {item["id"] for item in clean_entities}
    if len(ids) != len(clean_entities) or not {"left_eye", "right_eye"} <= ids:
        raise ValueError("The gaze lesson must identify both eyes with unique entities")
    paths = data.get("pathways", [])
    if not isinstance(paths, list) or not 1 <= len(paths) <= 8:
        raise ValueError("The gaze lesson needs a bounded pathway list")
    clean_paths = []
    for item in paths:
        if not isinstance(item, dict): raise ValueError("The gaze lesson has an invalid pathway")
        nodes, edges = item.get("nodes", []), item.get("edges", [])
        if not isinstance(nodes, list) or not 2 <= len(nodes) <= 60 or not all(isinstance(node, str) and node in ids for node in nodes):
            raise ValueError("The gaze lesson pathway has unknown entities")
        if not isinstance(edges, list) or not 1 <= len(edges) <= 80:
            raise ValueError("The gaze lesson needs bounded circuit edges")
        clean_edges = []
        for edge in edges:
            if not isinstance(edge, dict) or not all(isinstance(edge.get(key), str) and edge[key] in ids for key in ("source", "target")):
                raise ValueError("The gaze lesson circuit has unknown entities")
            clean_edges.append({"source": edge["source"], "target": edge["target"],
                                "function": label(edge.get("function"))})
        clean_paths.append({"id": identity(item.get("id")), "name": label(item.get("name"), 180),
                            "nodes": nodes, "edges": clean_edges})
    lesions = data.get("lesions", [])
    if not isinstance(lesions, list) or not 1 <= len(lesions) <= 60:
        raise ValueError("The gaze lesson needs a bounded lesion list")
    clean_lesions = []
    for item in lesions:
        if not isinstance(item, dict) or not isinstance(item.get("site"), str) or item["site"] not in ids:
            raise ValueError("The gaze lesson lesion has an unknown site")
        row = {"id": identity(item.get("id")), "name": label(item.get("name"), 180),
               "site": item["site"], "function": label(item.get("function")),
               "evidence_note": label(item.get("evidence_note")),
               "equivalence_group": identity(item.get("equivalence_group")),
               "quizable": item.get("quizable") is True}
        for direction in ("left", "right"):
            expected = item.get("expected_" + direction)
            if not isinstance(expected, dict): raise ValueError("The gaze lesson is missing its observations")
            result = {"observation": label(expected.get("observation"))}
            for eye in ("left", "right"):
                moves, degrees = expected.get(eye + "EyeMoves"), expected.get(eye + "EyeDegrees")
                if not isinstance(moves, bool) or isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or not -90 <= degrees <= 90 or (not moves and degrees != 0):
                    raise ValueError("The gaze lesson has an invalid eye movement")
                result[eye + "EyeMoves"], result[eye + "EyeDegrees"] = moves, degrees
            row["expected_" + direction] = result
        clean_lesions.append(row)
    if len({row["id"] for row in clean_paths}) != len(clean_paths) or len({row["id"] for row in clean_lesions}) != len(clean_lesions):
        raise ValueError("The gaze lesson has duplicate identities")
    return {"entities": clean_entities, "pathways": clean_paths, "lesions": clean_lesions}


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
                          and row.get("kind") in {"html", "native", "gaze"}][:MAX_LABS]
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
            raise ValueError("Choose a trusted HTML file, gaze lesson JSON, or native .app")
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
        elif path.is_file() and path.suffix.lower() == ".json":
            gaze_lesson(path)
            kind = "gaze"
        else:
            raise ValueError("Choose trusted HTML or a gaze lesson JSON. A 3D model needs a local HTML viewer.")
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
                "embedded": row["kind"] in {"html", "gaze"},
                **({"url": f"/lab/{row['id']}/"} if row["kind"] in {"html", "gaze"} else {})}

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
        if row["kind"] == "gaze":
            if relative == "lesson.json":
                return json.dumps(gaze_lesson(Path(row["path"])), ensure_ascii=False).encode(), "application/json"
            if relative not in GAZE_VIEWER:
                raise KeyError("Lab file not found")
            target = VIEWER_ROOT / GAZE_VIEWER[relative]
            return target.read_bytes(), MIME[target.suffix]
        if row["kind"] != "html":
            raise KeyError("Lab file not found")
        entry = Path(row["path"])
        if entry.resolve() != entry or entry.is_symlink():
            raise KeyError("Lab file not found")
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
            f"script-src 'unsafe-inline' 'wasm-unsafe-eval' {prefix}; style-src 'unsafe-inline' {prefix}; "
            f"img-src data: blob: {prefix}; font-src {prefix}; connect-src {prefix}; "
            "object-src 'none'; frame-src 'none'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'self'")
