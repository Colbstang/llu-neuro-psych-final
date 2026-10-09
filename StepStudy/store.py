"""Private, per-user SQLite state for the optional Step study planner."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


CATALOG_PATH = Path(__file__).with_name("data") / "topics.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    value = value or _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


class StepStudyStore:
    """Store topic choices, notes, and recall history without deleting paused work."""

    def __init__(self, db_path: str | Path, catalog_path: str | Path | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        self.clock = clock or _utc_now
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.catalog = raw["topics"]
        self.topic_ids = {topic["id"] for topic in self.catalog}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level="IMMEDIATE", factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS topic_state (
                topic_id TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0,
                activated_at TEXT, paused_at TEXT, last_recall TEXT, next_recall TEXT,
                notes TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(topic_id) REFERENCES catalog_ids(topic_id)
              );
              CREATE TABLE IF NOT EXISTS catalog_ids (topic_id TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, topic_id TEXT NOT NULL,
                prompt TEXT NOT NULL, answer TEXT NOT NULL, grade_json TEXT,
                score REAL, created_at TEXT NOT NULL,
                FOREIGN KEY(topic_id) REFERENCES catalog_ids(topic_id)
              );
              CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY, value_json TEXT NOT NULL
              );
            """)
            db.executemany("INSERT OR IGNORE INTO catalog_ids(topic_id) VALUES (?)",
                           [(tid,) for tid in sorted(self.topic_ids)])
            db.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('revision','0')")
            # Neuro and psychiatry already have local course material. Other Step topics
            # remain opt-in placeholders until the user activates them.
            for topic in self.catalog:
                active = topic["id"] in {"neuro", "psychiatry"}
                db.execute("INSERT OR IGNORE INTO topic_state(topic_id,active) VALUES(?,?)",
                           (topic["id"], int(active)))
                if active:
                    db.execute("UPDATE topic_state SET activated_at=COALESCE(activated_at,?) WHERE topic_id=?",
                               (_stamp(self.clock()), topic["id"]))

    def _validate(self, topic_id: str) -> None:
        if topic_id not in self.topic_ids:
            raise ValueError(f"Unknown Step study topic: {topic_id}")

    @staticmethod
    def _bump(db: sqlite3.Connection) -> int:
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        return int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            rows = {row["topic_id"]: dict(row) for row in db.execute("SELECT * FROM topic_state")}
            attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY id")]
            preferences = {row["key"]: json.loads(row["value_json"])
                           for row in db.execute("SELECT * FROM preferences")}
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        topics = {}
        for topic in self.catalog:
            row = rows[topic["id"]]
            topics[topic["id"]] = {
                "active": bool(row["active"]), "activated_at": row["activated_at"],
                "paused_at": row["paused_at"], "last_recall": row["last_recall"],
                "next_recall": row["next_recall"], "notes": row["notes"],
            }
        for attempt in attempts:
            attempt["grade"] = json.loads(attempt.pop("grade_json")) if attempt["grade_json"] else None
        return {"revision": revision, "topics": topics, "attempts": attempts,
                "preferences": preferences}

    def activate(self, topic_id: str, active: bool = True) -> dict[str, Any]:
        self._validate(topic_id)
        now = _stamp(self.clock())
        with self._connect() as db:
            current = db.execute("SELECT active FROM topic_state WHERE topic_id=?", (topic_id,)).fetchone()
            if bool(current["active"]) != bool(active):
                if active:
                    db.execute("UPDATE topic_state SET active=1, activated_at=COALESCE(activated_at,?), paused_at=NULL WHERE topic_id=?",
                               (now, topic_id))
                else:
                    db.execute("UPDATE topic_state SET active=0, paused_at=? WHERE topic_id=?", (now, topic_id))
                self._bump(db)
        return self.snapshot()["topics"][topic_id]

    def save_notes(self, topic_id: str, text: str) -> dict[str, Any]:
        self._validate(topic_id)
        if not isinstance(text, str):
            raise TypeError("Notes must be text")
        with self._connect() as db:
            db.execute("UPDATE topic_state SET notes=? WHERE topic_id=?", (text, topic_id))
            self._bump(db)
        return self.snapshot()["topics"][topic_id]

    def save_attempt(self, topic_id: str, prompt: str, answer: str,
                     grade: Any = None, score: float | None = None) -> dict[str, Any]:
        self._validate(topic_id)
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(answer, str):
            raise ValueError("A non-empty prompt and text answer are required")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
            raise ValueError("score must be null or a number from 0 to 1")
        if score is not None and (not isinstance(grade, dict) or grade.get("assessed") is not True):
            raise ValueError("A score requires an actual assessment")
        now_dt = self.clock()
        now = _stamp(now_dt)
        # This is a suggested concept-recall interval, separate from Anki's scheduler.
        # Missing AI grades remain null; the conservative interval doesn't imply mastery.
        days = 1 if score is None or score < .6 else (3 if score < .85 else 7)
        next_recall = _stamp(now_dt + timedelta(days=days))
        grade_json = json.dumps(grade, ensure_ascii=False, sort_keys=True) if grade is not None else None
        with self._connect() as db:
            cursor = db.execute("INSERT INTO attempts(topic_id,prompt,answer,grade_json,score,created_at) VALUES(?,?,?,?,?,?)",
                                (topic_id, prompt, answer, grade_json, score, now))
            db.execute("UPDATE topic_state SET last_recall=?,next_recall=? WHERE topic_id=?",
                       (now, next_recall, topic_id))
            self._bump(db)
            attempt_id = cursor.lastrowid
        return next(item for item in self.snapshot()["attempts"] if item["id"] == attempt_id)

    def set_preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise TypeError("Preferences must be a dictionary")
        with self._connect() as db:
            for key, value in values.items():
                if not isinstance(key, str) or not key:
                    raise ValueError("Preference keys must be non-empty strings")
                db.execute("INSERT INTO preferences(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
            if values:
                self._bump(db)
        return self.snapshot()["preferences"]

    def export_state(self) -> dict[str, Any]:
        return self.snapshot()

    def summary(self) -> dict[str, Any]:
        state = self.snapshot()
        active = [tid for tid, value in state["topics"].items() if value["active"]]
        return {"revision": state["revision"], "active_topic_ids": active,
                "attempt_count": len(state["attempts"]),
                "due_concept_review_count": sum(1 for tid in active
                    if state["topics"][tid]["next_recall"] and state["topics"][tid]["next_recall"] <= _stamp(self.clock()))}
