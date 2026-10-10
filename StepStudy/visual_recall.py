"""Private image-occlusion practice records for Step Study.

Exercise records contain source references and normalized masks. Imported image
bytes are kept as private SQLite blobs; course and intake images are resolved by
the caller from their existing opaque/numeric identifiers.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


CATALOG_PATH = Path(__file__).with_name("data") / "topics.json"
SCHEMA_VERSION = 1
MAX_EXERCISES = 5_000
MAX_MASKS = 64
MAX_NAME_CHARS = 120
MAX_LABEL_CHARS = 100
MAX_ANSWER_CHARS = 500
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
MAX_EXPORT_IMAGE_BYTES = 50 * 1024 * 1024
RATINGS = {"again", "hard", "good", "easy"}
_COURSE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")
_CHAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def _private_path(path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if target == repo or repo in target.parents:
        raise ValueError("Visual recall database must be stored outside the repository")
    return target


def _stamp(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _plain_text(value: Any, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise ValueError("Expected text")
        return ""
    text = " ".join(value.split()).strip()
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        raise ValueError("Text contains control characters")
    text = text[:limit]
    if required and not text:
        raise ValueError("Text cannot be blank")
    return text


def validate_source(source: Any) -> dict[str, str]:
    """Validate a data-only image reference; paths and arbitrary URLs are rejected."""
    if not isinstance(source, dict) or set(source) != {"kind", "id"}:
        raise ValueError("Source must contain only kind and id")
    kind, identity = source.get("kind"), source.get("id")
    if kind == "course_asset" and isinstance(identity, str) and _COURSE_ID.fullmatch(identity):
        return {"kind": kind, "id": identity}
    if kind == "intake_image" and not isinstance(identity, bool):
        value = str(identity)
        if value.isascii() and value.isdigit() and 0 < int(value) <= 9_999_999_999:
            return {"kind": kind, "id": str(int(value))}
    if kind == "upload" and isinstance(identity, str) and _UPLOAD_ID.fullmatch(identity):
        return {"kind": kind, "id": identity}
    raise ValueError("Unsupported image source reference")


def _dimensions(body: bytes, mime_type: str) -> tuple[int, int]:
    if not isinstance(body, bytes) or not body or len(body) > MAX_IMAGE_BYTES:
        raise ValueError("Image bytes must be non-empty and at most 10 MiB")
    if mime_type == "image/png":
        if len(body) < 24 or not body.startswith(b"\x89PNG\r\n\x1a\n") or body[12:16] != b"IHDR":
            raise ValueError("Image bytes do not match PNG format")
        width, height = struct.unpack(">II", body[16:24])
    elif mime_type == "image/jpeg":
        if len(body) < 4 or not body.startswith(b"\xff\xd8"):
            raise ValueError("Image bytes do not match JPEG format")
        width, height = _jpeg_dimensions(body)
    else:
        raise ValueError("Only PNG and JPEG uploads are supported")
    if not width or not height or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Image dimensions are invalid or exceed 80 megapixels")
    return width, height


def _jpeg_dimensions(body: bytes) -> tuple[int, int]:
    index = 2
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index + 4 <= len(body):
        if body[index] != 0xFF:
            index += 1
            continue
        while index < len(body) and body[index] == 0xFF:
            index += 1
        if index >= len(body):
            break
        marker = body[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(body):
            break
        length = struct.unpack(">H", body[index:index + 2])[0]
        if length < 2 or index + length > len(body):
            break
        if marker in sof:
            if length < 7:
                break
            height, width = struct.unpack(">HH", body[index + 3:index + 7])
            return width, height
        index += length
    raise ValueError("JPEG dimensions could not be read from its header")


class VisualRecallStore:
    """Store named image exercises and self-ratings separately from Anki."""

    def __init__(self, db_path: str | Path, catalog_path: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.path = _private_path(db_path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.topic_ids = {row["id"] for row in catalog.get("topics", []) if isinstance(row, dict) and isinstance(row.get("id"), str)}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level="IMMEDIATE", factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise ValueError("Visual recall database was created by a newer version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS visual_exercises (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, source_json TEXT NOT NULL,
                topic_ids_json TEXT NOT NULL, chapter_id TEXT, masks_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                review_count INTEGER NOT NULL DEFAULT 0, last_reviewed_at TEXT,
                due_at TEXT, interval_days REAL NOT NULL DEFAULT 0, last_rating TEXT
              );
              CREATE TABLE IF NOT EXISTS visual_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
                exercise_id TEXT NOT NULL, rating TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                due_at TEXT NOT NULL, interval_days REAL NOT NULL,
                FOREIGN KEY(exercise_id) REFERENCES visual_exercises(id) ON DELETE CASCADE
              );
              CREATE INDEX IF NOT EXISTS visual_reviews_exercise_idx
                ON visual_reviews(exercise_id, id DESC);
              CREATE TABLE IF NOT EXISTS visual_images (
                id TEXT PRIMARY KEY, mime_type TEXT NOT NULL, size INTEGER NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, sha256 TEXT NOT NULL,
                body BLOB NOT NULL, created_at TEXT NOT NULL
              );
            """)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        # Exercise answers and uploaded figures are private study material.
        os.chmod(self.path, 0o600)

    def _topics(self, topic_ids: Any) -> list[str]:
        if topic_ids is None:
            return []
        if not isinstance(topic_ids, list) or len(topic_ids) > 32:
            raise ValueError("Topic IDs must be a list of at most 32 IDs")
        rows: list[str] = []
        for topic_id in topic_ids:
            if not isinstance(topic_id, str) or topic_id not in self.topic_ids:
                raise ValueError("Unknown topic ID")
            if topic_id not in rows:
                rows.append(topic_id)
        return rows

    @staticmethod
    def _chapter_id(value: Any) -> str | None:
        if value in (None, ""):
            return None
        if not isinstance(value, str) or not _CHAPTER_ID.fullmatch(value):
            raise ValueError("Invalid chapter ID")
        return value

    @staticmethod
    def _masks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > MAX_MASKS:
            raise ValueError(f"Masks must be a list of at most {MAX_MASKS}")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in value:
            if not isinstance(row, dict):
                raise ValueError("Each mask must be an object")
            identity = row.get("id") or uuid.uuid4().hex[:16]
            if not isinstance(identity, str) or not _MASK_ID.fullmatch(identity) or identity in seen:
                raise ValueError("Mask IDs must be unique short identifiers")
            seen.add(identity)
            coords: dict[str, float] = {}
            for key in ("x", "y", "width", "height"):
                raw = row.get(key)
                if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
                    raise ValueError("Mask coordinates must be finite normalized numbers")
                if raw < 0 or raw > 1:
                    raise ValueError("Mask coordinates must be between 0 and 1")
                coords[key] = round(float(raw), 5)
            if coords["width"] < 0.005 or coords["height"] < 0.005:
                raise ValueError("Masks must cover at least 0.5 percent of each image dimension")
            if coords["x"] + coords["width"] > 1.000001 or coords["y"] + coords["height"] > 1.000001:
                raise ValueError("Mask rectangle must remain inside the image")
            label = _plain_text(row.get("label", ""), MAX_LABEL_CHARS)
            answer = _plain_text(row.get("answer", ""), MAX_ANSWER_CHARS)
            result.append({"id": identity, **coords, "label": label, "answer": answer})
        return result

    def create_exercise(self, name: str, source: dict[str, Any], topic_ids: list[str] | None = None,
                        chapter_id: str | None = None, masks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        clean_name, clean_source, topics, chapter, clean_masks = self._prepare_exercise(
            name, source, topic_ids, chapter_id, masks)
        now = _stamp(self.clock())
        identity = uuid.uuid4().hex
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM visual_exercises").fetchone()[0]
            if count >= MAX_EXERCISES:
                raise ValueError("Visual recall exercise limit reached")
            self._insert_exercise(db, identity, clean_name, clean_source, topics, chapter, clean_masks, now)
        return self.get_exercise(identity) or {}

    def _prepare_exercise(self, name, source, topic_ids, chapter_id, masks):
        clean_name = _plain_text(name, MAX_NAME_CHARS, required=True)
        clean_source = validate_source(source)
        topics, chapter, clean_masks = self._prepare_exercise_fields(topic_ids, chapter_id, masks)
        return clean_name, clean_source, topics, chapter, clean_masks

    def _prepare_exercise_fields(self, topic_ids, chapter_id, masks):
        topics = self._topics(topic_ids or [])
        chapter = self._chapter_id(chapter_id)
        clean_masks = self._masks(masks or [])
        return topics, chapter, clean_masks

    @staticmethod
    def _insert_exercise(db, identity, name, source, topics, chapter, masks, now):
        db.execute("""INSERT INTO visual_exercises
          (id,name,source_json,topic_ids_json,chapter_id,masks_json,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?)""",
                   (identity, name, json.dumps(source), json.dumps(topics), chapter,
                    json.dumps(masks, ensure_ascii=False), now, now))

    def create_from_image(self, name: str, body: bytes, mime_type: str,
                          topic_ids: list[str] | None = None, chapter_id: str | None = None,
                          masks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Atomically copy a source image and create its exercise.

        This is used for ephemeral course asset tokens: successful exercises
        receive a stable private upload ID, while invalid exercise input never
        leaves an unreferenced image in the private store.
        """
        clean_name = _plain_text(name, MAX_NAME_CHARS, required=True)
        topics, chapter, clean_masks = self._prepare_exercise_fields(topic_ids, chapter_id, masks)
        width, height = _dimensions(body, mime_type)
        now = _stamp(self.clock())
        image_id, exercise_id = uuid.uuid4().hex, uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM visual_exercises").fetchone()[0]
            if count >= MAX_EXERCISES:
                raise ValueError("Visual recall exercise limit reached")
            db.execute("INSERT INTO visual_images(id,mime_type,size,width,height,sha256,body,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (image_id, mime_type, len(body), width, height, digest, body, now))
            source = {"kind": "upload", "id": image_id}
            self._insert_exercise(db, exercise_id, clean_name, source, topics, chapter, clean_masks, now)
        return self.get_exercise(exercise_id) or {}

    def update_exercise(self, exercise_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_exercise(exercise_id)
        if not current:
            return None
        if not isinstance(patch, dict) or not patch or not set(patch).issubset({"name", "source", "topic_ids", "chapter_id", "masks"}):
            raise ValueError("Invalid exercise update")
        name = _plain_text(patch["name"], MAX_NAME_CHARS, required=True) if "name" in patch else current["name"]
        source = validate_source(patch["source"]) if "source" in patch else current["source"]
        topics = self._topics(patch["topic_ids"]) if "topic_ids" in patch else current["topic_ids"]
        chapter = self._chapter_id(patch["chapter_id"]) if "chapter_id" in patch else current["chapter_id"]
        masks = self._masks(patch["masks"]) if "masks" in patch else current["masks"]
        with self._connect() as db:
            db.execute("""UPDATE visual_exercises SET name=?,source_json=?,topic_ids_json=?,chapter_id=?,
              masks_json=?,updated_at=? WHERE id=?""",
                       (name, json.dumps(source), json.dumps(topics), chapter,
                        json.dumps(masks, ensure_ascii=False), _stamp(self.clock()), exercise_id))
        return self.get_exercise(exercise_id)

    def update_from_image(self, exercise_id: str, patch: dict[str, Any],
                          body: bytes, mime_type: str) -> dict[str, Any] | None:
        """Atomically replace an exercise's course image with a private copy."""
        if not isinstance(exercise_id, str) or not _UPLOAD_ID.fullmatch(exercise_id):
            return None
        if (not isinstance(patch, dict) or not patch or "source" not in patch or
                not set(patch).issubset({"name", "source", "topic_ids", "chapter_id", "masks"})):
            raise ValueError("Invalid exercise update")
        requested_source = validate_source(patch["source"])
        if requested_source["kind"] != "course_asset":
            raise ValueError("Image replacement must reference a course asset")
        width, height = _dimensions(body, mime_type)
        image_id = uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        now = _stamp(self.clock())
        with self._connect() as db:
            row = db.execute("SELECT * FROM visual_exercises WHERE id=?", (exercise_id,)).fetchone()
            if not row:
                return None
            current = self._decode(row, include_history=True)
            name = _plain_text(patch["name"], MAX_NAME_CHARS, required=True) if "name" in patch else current["name"]
            topics = self._topics(patch["topic_ids"]) if "topic_ids" in patch else current["topic_ids"]
            chapter = self._chapter_id(patch["chapter_id"]) if "chapter_id" in patch else current["chapter_id"]
            masks = self._masks(patch["masks"]) if "masks" in patch else current["masks"]
            # Validation happens before either INSERT/UPDATE; both mutations
            # then commit together under the same SQLite transaction.
            db.execute("INSERT INTO visual_images(id,mime_type,size,width,height,sha256,body,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (image_id, mime_type, len(body), width, height, digest, body, now))
            source = {"kind": "upload", "id": image_id}
            db.execute("""UPDATE visual_exercises SET name=?,source_json=?,topic_ids_json=?,chapter_id=?,
              masks_json=?,updated_at=? WHERE id=?""",
                       (name, json.dumps(source), json.dumps(topics), chapter,
                        json.dumps(masks, ensure_ascii=False), now, exercise_id))
        return self.get_exercise(exercise_id)

    @staticmethod
    def _decode(row: sqlite3.Row, include_history: bool = False) -> dict[str, Any]:
        record = {
            "id": row["id"], "name": row["name"], "source": json.loads(row["source_json"]),
            "topic_ids": json.loads(row["topic_ids_json"]), "chapter_id": row["chapter_id"],
            "mask_count": len(json.loads(row["masks_json"])),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "review_count": row["review_count"], "last_reviewed_at": row["last_reviewed_at"],
            "due_at": row["due_at"], "interval_days": row["interval_days"], "last_rating": row["last_rating"],
        }
        if include_history:
            record["masks"] = json.loads(row["masks_json"])
        return record

    def get_exercise(self, exercise_id: str, history_limit: int = 50) -> dict[str, Any] | None:
        if not isinstance(exercise_id, str) or not _UPLOAD_ID.fullmatch(exercise_id):
            return None
        with self._connect() as db:
            row = db.execute("SELECT * FROM visual_exercises WHERE id=?", (exercise_id,)).fetchone()
            if not row:
                return None
            record = self._decode(row, include_history=True)
            record["history"] = [dict(item) for item in db.execute(
                "SELECT rating,reviewed_at,due_at,interval_days FROM visual_reviews WHERE exercise_id=? ORDER BY id DESC LIMIT ?",
                (exercise_id, max(0, min(100, int(history_limit)))))]
        return record

    def list_exercises(self, topic_id: str | None = None, chapter_id: str | None = None,
                       due_only: bool = False) -> list[dict[str, Any]]:
        if topic_id is not None and topic_id not in self.topic_ids:
            raise ValueError("Unknown topic ID")
        if chapter_id is not None:
            chapter_id = self._chapter_id(chapter_id)
        now = _stamp(self.clock())
        with self._connect() as db:
            rows = [self._decode(row) for row in db.execute("SELECT * FROM visual_exercises ORDER BY updated_at DESC")]
        if topic_id:
            rows = [row for row in rows if topic_id in row["topic_ids"]]
        if chapter_id:
            rows = [row for row in rows if row["chapter_id"] == chapter_id]
        if due_only:
            rows = [row for row in rows if not row["due_at"] or row["due_at"] <= now]
        return rows

    def record_review(self, exercise_id: str, rating: str, request_id: str | None = None) -> dict[str, Any] | None:
        if not isinstance(exercise_id, str) or not _UPLOAD_ID.fullmatch(exercise_id):
            return None
        if not isinstance(rating, str) or rating not in RATINGS:
            raise ValueError("Rating must be again, hard, good, or easy")
        request_id = request_id or uuid.uuid4().hex
        if not isinstance(request_id, str) or not _MASK_ID.fullmatch(request_id):
            raise ValueError("Invalid review request ID")
        reviewed_at = _stamp(self.clock())
        with self._connect() as db:
            previous = db.execute("SELECT * FROM visual_reviews WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous["exercise_id"] != exercise_id:
                    raise ValueError("Review request ID was already used for another exercise")
                if previous["rating"] != rating:
                    raise ValueError("Review request ID was already used for another rating")
                return {"ok": True, "duplicate": True, "exercise": self._decode(
                    db.execute("SELECT * FROM visual_exercises WHERE id=?", (exercise_id,)).fetchone()),
                    "review": dict(previous)}
            row = db.execute("SELECT * FROM visual_exercises WHERE id=?", (exercise_id,)).fetchone()
            if not row:
                return None
            old_interval = float(row["interval_days"] or 0)
            if rating == "again":
                interval = 1.0
            elif rating == "hard":
                interval = max(1.0, round(old_interval * 1.2, 2))
            elif rating == "good":
                interval = max(2.0, old_interval * 2) if old_interval else 2.0
            else:
                interval = max(4.0, old_interval * 3) if old_interval else 4.0
            now = self.clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            due_at = _stamp(now + timedelta(days=interval))
            db.execute("""INSERT INTO visual_reviews(request_id,exercise_id,rating,reviewed_at,due_at,interval_days)
                          VALUES(?,?,?,?,?,?)""", (request_id, exercise_id, rating, reviewed_at, due_at, interval))
            db.execute("""UPDATE visual_exercises SET review_count=review_count+1,last_reviewed_at=?,
                          due_at=?,interval_days=?,last_rating=?,updated_at=? WHERE id=?""",
                       (reviewed_at, due_at, interval, rating, reviewed_at, exercise_id))
            review = dict(db.execute("SELECT * FROM visual_reviews WHERE request_id=?", (request_id,)).fetchone())
            exercise = self._decode(db.execute("SELECT * FROM visual_exercises WHERE id=?", (exercise_id,)).fetchone())
        return {"ok": True, "duplicate": False, "exercise": exercise, "review": review}

    def store_upload(self, body: bytes, mime_type: str) -> dict[str, str]:
        if not isinstance(body, bytes) or not body or len(body) > MAX_IMAGE_BYTES:
            raise ValueError("Image upload must be non-empty and at most 10 MiB")
        width, height = _dimensions(body, mime_type)
        identity = uuid.uuid4().hex
        digest = hashlib.sha256(body).hexdigest()
        with self._connect() as db:
            db.execute("INSERT INTO visual_images(id,mime_type,size,width,height,sha256,body,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (identity, mime_type, len(body), width, height, digest, body, _stamp(self.clock())))
        return {"kind": "upload", "id": identity}

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        if not isinstance(upload_id, str) or not _UPLOAD_ID.fullmatch(upload_id):
            return None
        with self._connect() as db:
            row = db.execute("SELECT mime_type,size,width,height,sha256,body FROM visual_images WHERE id=?", (upload_id,)).fetchone()
        if not row:
            return None
        return {"mime_type": row["mime_type"], "size": row["size"], "width": row["width"],
                "height": row["height"], "sha256": row["sha256"], "body": bytes(row["body"])}

    def export_state(self) -> dict[str, Any]:
        with self._connect() as db:
            exercises = [dict(row) for row in db.execute("SELECT * FROM visual_exercises ORDER BY id")]
            reviews = [dict(row) for row in db.execute("SELECT * FROM visual_reviews ORDER BY id")]
            images = [dict(row) for row in db.execute("SELECT * FROM visual_images ORDER BY id")]
        if len(images) > 200:
            raise ValueError("Visual recall image backup exceeds its 200 upload limit")
        total = sum(len(row["body"]) for row in images)
        if total > MAX_EXPORT_IMAGE_BYTES:
            raise ValueError("Visual recall image backup exceeds its 50 MiB limit")
        return {
            "schema": "step-visual-recall-v1", "exported_at": _stamp(self.clock()),
            "exercises": [{"id": row["id"], "name": row["name"], "source": json.loads(row["source_json"]),
                           "topic_ids": json.loads(row["topic_ids_json"]), "chapter_id": row["chapter_id"],
                           "masks": json.loads(row["masks_json"]), "created_at": row["created_at"],
                           "updated_at": row["updated_at"], "review_count": row["review_count"],
                           "last_reviewed_at": row["last_reviewed_at"], "due_at": row["due_at"],
                           "interval_days": row["interval_days"], "last_rating": row["last_rating"]}
                          for row in exercises],
            "reviews": reviews,
            "uploads": [{**{key: row[key] for key in ("id", "mime_type", "size", "width", "height", "sha256", "created_at")},
                         "body_base64": base64.b64encode(row["body"]).decode("ascii")} for row in images],
        }

    def import_state(self, payload: dict[str, Any], replace: bool = False) -> dict[str, int]:
        if not isinstance(payload, dict) or payload.get("schema") != "step-visual-recall-v1":
            raise ValueError("Unsupported visual recall backup")
        exercises = payload.get("exercises", [])
        reviews = payload.get("reviews", [])
        uploads = payload.get("uploads", [])
        if not all(isinstance(rows, list) for rows in (exercises, reviews, uploads)):
            raise ValueError("Invalid visual recall backup collections")
        if len(exercises) > MAX_EXERCISES or len(uploads) > 200 or len(reviews) > MAX_EXERCISES * 100:
            raise ValueError("Visual recall backup exceeds its record limits")
        parsed_uploads: list[tuple[Any, ...]] = []
        total_bytes = 0
        upload_ids: set[str] = set()
        for item in uploads:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _UPLOAD_ID.fullmatch(item["id"]):
                raise ValueError("Invalid upload in visual recall backup")
            try:
                body = base64.b64decode(item.get("body_base64", ""), validate=True)
            except (ValueError, TypeError):
                raise ValueError("Invalid image data in visual recall backup") from None
            mime_type = item.get("mime_type")
            width, height = _dimensions(body, mime_type)
            if int(item.get("size", -1)) != len(body) or item.get("width") != width or item.get("height") != height:
                raise ValueError("Image metadata does not match its bytes")
            digest = hashlib.sha256(body).hexdigest()
            if item.get("sha256") != digest:
                raise ValueError("Image hash does not match its bytes")
            total_bytes += len(body)
            if total_bytes > MAX_EXPORT_IMAGE_BYTES:
                raise ValueError("Visual recall backup images exceed the 50 MiB limit")
            upload_ids.add(item["id"])
            parsed_uploads.append((item["id"], mime_type, len(body), width, height, digest, body,
                                   _plain_text(item.get("created_at") or _stamp(self.clock()), 40)))
        parsed_exercises: list[tuple[Any, ...]] = []
        exercise_ids: set[str] = set()
        for item in exercises:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _UPLOAD_ID.fullmatch(item["id"]):
                raise ValueError("Invalid exercise in visual recall backup")
            source = validate_source(item.get("source"))
            if source["kind"] == "upload" and source["id"] not in upload_ids:
                with self._connect() as db:
                    exists = db.execute("SELECT 1 FROM visual_images WHERE id=?", (source["id"],)).fetchone()
                if not exists:
                    raise ValueError("Exercise references a missing uploaded image")
            name = _plain_text(item.get("name"), MAX_NAME_CHARS, required=True)
            topics = self._topics(item.get("topic_ids", []))
            chapter = self._chapter_id(item.get("chapter_id"))
            masks = self._masks(item.get("masks", []))
            if item["id"] in exercise_ids:
                raise ValueError("Duplicate exercise ID in visual recall backup")
            exercise_ids.add(item["id"])
            parsed_exercises.append((item["id"], name, json.dumps(source), json.dumps(topics), chapter,
                json.dumps(masks, ensure_ascii=False), _plain_text(item.get("created_at") or _stamp(self.clock()), 40),
                _plain_text(item.get("updated_at") or _stamp(self.clock()), 40),
                max(0, int(item.get("review_count", 0))), item.get("last_reviewed_at"), item.get("due_at"),
                max(0.0, float(item.get("interval_days", 0))), item.get("last_rating")))
        parsed_reviews: list[tuple[Any, ...]] = []
        review_ids: set[str] = set()
        for item in reviews:
            if not isinstance(item, dict) or item.get("exercise_id") not in exercise_ids:
                raise ValueError("Review references an unknown imported exercise")
            request_id = item.get("request_id")
            if not isinstance(request_id, str) or not _MASK_ID.fullmatch(request_id) or item.get("rating") not in RATINGS:
                raise ValueError("Invalid review record in visual recall backup")
            if request_id in review_ids:
                raise ValueError("Duplicate review request ID in visual recall backup")
            review_ids.add(request_id)
            parsed_reviews.append((request_id, item["exercise_id"], item["rating"],
                                   _plain_text(item.get("reviewed_at"), 40),
                                   _plain_text(item.get("due_at"), 40), float(item.get("interval_days", 0))))
        with self._connect() as db:
            if replace:
                db.execute("DELETE FROM visual_reviews")
                db.execute("DELETE FROM visual_exercises")
                db.execute("DELETE FROM visual_images")
            for row in parsed_uploads:
                db.execute("INSERT OR IGNORE INTO visual_images(id,mime_type,size,width,height,sha256,body,created_at) VALUES(?,?,?,?,?,?,?,?)", row)
            for row in parsed_exercises:
                db.execute("""INSERT OR IGNORE INTO visual_exercises
                  (id,name,source_json,topic_ids_json,chapter_id,masks_json,created_at,updated_at,
                   review_count,last_reviewed_at,due_at,interval_days,last_rating)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", row)
            for row in parsed_reviews:
                db.execute("""INSERT OR IGNORE INTO visual_reviews
                  (request_id,exercise_id,rating,reviewed_at,due_at,interval_days) VALUES(?,?,?,?,?,?)""", row)
        return {"exercises": len(parsed_exercises), "reviews": len(parsed_reviews), "uploads": len(parsed_uploads)}
