"""Durable, merge-safe private progress storage for the LLU Study app."""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import shutil
import sqlite3
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "LLU Study" / "progress.sqlite3"
_ARRAY_DELTA_KEY = "__llu_array_delta__"

def _synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_copy(value: Any) -> Any:
    """Validate JSON state and return a detached copy."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("progress state must contain JSON-compatible values") from exc


def _array_delta(value: Any) -> dict[str, list[Any]] | None:
    if not isinstance(value, dict) or _ARRAY_DELTA_KEY not in value:
        return None
    if set(value) != {_ARRAY_DELTA_KEY} or not isinstance(value[_ARRAY_DELTA_KEY], dict):
        raise ValueError("array delta must contain only __llu_array_delta__")
    delta = value[_ARRAY_DELTA_KEY]
    if set(delta) != {"remove", "upsert"} or not isinstance(delta["remove"], list) or not isinstance(delta["upsert"], list):
        raise ValueError("array delta requires remove and upsert lists")
    removes = delta["remove"]
    upserts = delta["upsert"]
    if any(not isinstance(item, str) or not item for item in removes):
        raise ValueError("array delta remove IDs must be non-empty strings")
    if len(set(removes)) != len(removes):
        raise ValueError("array delta remove IDs must be unique")
    ids = []
    for item in upserts:
        if not isinstance(item, dict) or set(item) == set() or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("array delta upserts must be objects with a non-empty string id")
        ids.append(item["id"])
    if len(set(ids)) != len(ids):
        raise ValueError("array delta upsert IDs must be unique")
    if set(removes) & set(ids):
        raise ValueError("array delta cannot remove and upsert the same ID")
    return {"remove": removes, "upsert": [_json_copy(item) for item in upserts]}


def _apply_array_delta(current: Any, value: dict[str, list[Any]]) -> list[dict[str, Any]]:
    items = [] if current is None else _json_copy(current)
    if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in items):
        raise ValueError("array delta target must be a list of objects with string IDs")
    seen = set()
    for item in items:
        if item["id"] in seen:
            raise ValueError("array delta target contains duplicate IDs")
        seen.add(item["id"])
    remove = set(value["remove"])
    items = [item for item in items if item["id"] not in remove]
    positions = {item["id"]: index for index, item in enumerate(items)}
    for item in value["upsert"]:
        if item["id"] in positions:
            items[positions[item["id"]]] = item
        else:
            positions[item["id"]] = len(items)
            items.append(item)
    return items


def _merge(current: Any, patch: Any) -> Any:
    delta = _array_delta(patch)
    if delta is not None:
        return _apply_array_delta(current, delta)
    if isinstance(patch, dict):
        base = {} if current is None else _json_copy(current)
        if not isinstance(base, dict):
            raise ValueError("object patch target must be an object")
        for key, value in patch.items():
            if value is None:
                base.pop(key, None)
            elif isinstance(value, dict):
                base[key] = _merge(base.get(key), value)
            else:
                base[key] = _json_copy(value)
        return base
    return _json_copy(patch)


def _pointer_parts(path: str) -> list[str]:
    if path == "":
        return []
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("JSON patch paths must be JSON pointers")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _apply_operations(state: dict[str, Any], operations: list[Any]) -> dict[str, Any]:
    result = _json_copy(state)
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in {"add", "replace", "remove"}:
            raise ValueError("patch list items require add, replace, or remove operations")
        parts = _pointer_parts(operation.get("path", ""))
        if not parts:
            if operation["op"] == "remove":
                raise ValueError("cannot remove the root state")
            result = _json_copy(operation.get("value"))
            if not isinstance(result, dict):
                raise ValueError("root progress state must be an object")
            continue
        parent = result
        for part in parts[:-1]:
            if not isinstance(parent, dict) or part not in parent:
                raise ValueError("JSON patch path does not exist")
            parent = parent[part]
        key = parts[-1]
        if not isinstance(parent, dict):
            raise ValueError("JSON patch currently supports object paths")
        if operation["op"] == "remove":
            if key not in parent:
                raise ValueError("JSON patch remove path does not exist")
            del parent[key]
        else:
            if operation["op"] == "replace" and key not in parent:
                raise ValueError("JSON patch replace path does not exist")
            value = operation.get("value")
            delta = _array_delta(value)
            parent[key] = _apply_array_delta(parent.get(key), delta) if delta is not None else _json_copy(value)
    if not isinstance(result, dict):
        raise ValueError("root progress state must be an object")
    return result


class ProgressStore:
    """SQLite-backed state store with recursive merge patches and revisions."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, backup_retention: int = 14):
        self.path = Path(path).expanduser() if path is not None else DEFAULT_PATH
        self.path = self.path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        self.backup_retention = max(1, int(backup_retention))
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("CREATE TABLE IF NOT EXISTS progress_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if os.name != "nt":
            self.path.chmod(0o600)

    @_synchronized
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProgressStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @_synchronized
    def load(self) -> dict[str, Any]:
        return self._load_unlocked()

    def _load_unlocked(self) -> dict[str, Any]:
        rows = dict(self._conn.execute("SELECT key, value FROM progress_meta WHERE key IN ('state', 'revision', 'updated_at')"))
        exists = "state" in rows
        return {
            "state": json.loads(rows["state"]) if exists else {},
            "revision": int(rows.get("revision", "0")),
            "updated_at": rows.get("updated_at", ""),
            "exists": exists,
        }

    def _backup_previous(self, *, force: bool = False) -> None:
        if not self.path.exists() or self.load()["exists"] is False:
            return
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True, mode=0o700)
        if os.name != "nt":
            folder.chmod(0o700)
        today = _dt.datetime.now().date().isoformat()
        destination = folder / f"progress-{today}.sqlite3"
        if destination.exists() and not force:
            return
        fd, temp_name = tempfile.mkstemp(prefix=".progress-backup-", suffix=".sqlite3", dir=folder)
        os.close(fd)
        try:
            backup = sqlite3.connect(temp_name)
            with backup:
                self._conn.backup(backup)
            backup.close()
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        backups = sorted(folder.glob("progress-*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in backups[self.backup_retention:]:
            stale.unlink(missing_ok=True)

    def _write_state(self, state: dict[str, Any], revision: int) -> None:
        updated = _now()
        encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._write_rows(encoded, revision, updated)
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _write_rows(self, encoded: str, revision: int, updated: str) -> None:
        self._conn.executemany("INSERT INTO progress_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [("state", encoded), ("revision", str(revision)), ("updated_at", updated)])

    @_synchronized
    def apply_patch(self, patch: list[Any] | dict[str, Any], base_revision: int | None = None) -> dict[str, Any]:
        """Merge a stale-safe patch onto the latest state and return load()."""
        self._backup_previous()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._load_unlocked()
            candidate = _apply_operations(current["state"], patch) if isinstance(patch, list) else _merge(current["state"], patch)
            if not isinstance(candidate, dict):
                raise ValueError("root progress state must be an object")
            candidate = _json_copy(candidate)
            if candidate == current["state"]:
                self._conn.execute("COMMIT")
                return current
            encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, allow_nan=False)
            self._write_rows(encoded, current["revision"] + 1, _now())
            self._conn.execute("COMMIT")
            return self._load_unlocked()
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_synchronized
    def import_state(self, state: dict[str, Any], replace: bool = True) -> dict[str, Any]:
        """Import a complete state, backing up an existing state before replacement."""
        incoming = _json_copy(state)
        if not isinstance(incoming, dict):
            raise ValueError("imported progress state must be an object")
        self._backup_previous(force=True)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._load_unlocked()
            candidate = incoming if replace else _merge(current["state"], incoming)
            if candidate == current["state"]:
                self._conn.execute("COMMIT")
                return current
            encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, allow_nan=False)
            self._write_rows(encoded, current["revision"] + 1, _now())
            self._conn.execute("COMMIT")
            return self._load_unlocked()
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_synchronized
    def export_state(self) -> dict[str, Any]:
        return copy.deepcopy(self.load()["state"])

    @_synchronized
    def export(self, destination: str | os.PathLike[str] | None = None) -> dict[str, Any]:
        state = self.export_state()
        if destination is not None:
            Path(destination).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return state
