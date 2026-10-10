"""Private, per-user SQLite state for the optional Step study planner."""
from __future__ import annotations

import json
import sqlite3
import math
import uuid
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
              CREATE TABLE IF NOT EXISTS concept_schedule (
                item_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                due_at TEXT NOT NULL, interval_days INTEGER NOT NULL,
                repetitions INTEGER NOT NULL DEFAULT 0, last_outcome TEXT NOT NULL,
                last_score REAL, updated_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS concept_schedule_due ON concept_schedule(due_at);
              CREATE TABLE IF NOT EXISTS concept_attempts (
                attempt_id TEXT PRIMARY KEY, session_id TEXT, item_id TEXT NOT NULL,
                topic_id TEXT NOT NULL, action TEXT NOT NULL, prompt TEXT NOT NULL,
                answer TEXT NOT NULL, grade_json TEXT, score REAL, self_rating TEXT,
                revealed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS concept_attempt_item ON concept_attempts(item_id, created_at);
            """)
            # Additive identity fields let legacy topic recall remember stable
            # source items without changing how old attempts are scheduled.
            attempt_columns = {row["name"] for row in db.execute("PRAGMA table_info(attempts)")}
            for name in ("item_id", "source_id"):
                if name not in attempt_columns:
                    db.execute(f"ALTER TABLE attempts ADD COLUMN {name} TEXT")
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

    def snapshot(self, *, include_concepts: bool = False) -> dict[str, Any]:
        with self._connect() as db:
            rows = {row["topic_id"]: dict(row) for row in db.execute("SELECT * FROM topic_state")}
            attempts = [dict(row) for row in db.execute("SELECT * FROM attempts ORDER BY id")]
            preferences = {row["key"]: json.loads(row["value_json"])
                           for row in db.execute("SELECT * FROM preferences")}
            concept_attempts = ([dict(row) for row in db.execute(
                "SELECT * FROM concept_attempts ORDER BY created_at,attempt_id")]
                if include_concepts else None)
            concept_schedule = ([dict(row) for row in db.execute(
                "SELECT * FROM concept_schedule ORDER BY item_id")]
                if include_concepts else None)
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
        result = {"revision": revision, "topics": topics, "attempts": attempts,
                  "preferences": preferences}
        if include_concepts:
            for attempt in concept_attempts or []:
                attempt["grade"] = json.loads(attempt.pop("grade_json")) if attempt["grade_json"] else None
                attempt["revealed"] = bool(attempt["revealed"])
            result["concept_attempts"] = concept_attempts or []
            result["concept_schedule"] = concept_schedule or []
        return result

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
                     grade: Any = None, score: float | None = None, *,
                     item_id: str | None = None, source_id: str | None = None) -> dict[str, Any]:
        self._validate(topic_id)
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(answer, str):
            raise ValueError("A non-empty prompt and text answer are required")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
            raise ValueError("score must be null or a number from 0 to 1")
        if score is not None and (not isinstance(grade, dict) or grade.get("assessed") is not True):
            raise ValueError("A score requires an actual assessment")
        for name, value in (("item_id", item_id), ("source_id", source_id)):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                raise ValueError(f"{name} must be a non-empty string of at most 200 characters")
        now_dt = self.clock()
        now = _stamp(now_dt)
        # This is a suggested concept-recall interval, separate from Anki's scheduler.
        # Missing AI grades remain null; the conservative interval doesn't imply mastery.
        days = 1 if score is None or score < .6 else (3 if score < .85 else 7)
        next_recall = _stamp(now_dt + timedelta(days=days))
        grade_json = json.dumps(grade, ensure_ascii=False, sort_keys=True) if grade is not None else None
        with self._connect() as db:
            cursor = db.execute("INSERT INTO attempts(topic_id,prompt,answer,grade_json,score,created_at,item_id,source_id) VALUES(?,?,?,?,?,?,?,?)",
                                (topic_id, prompt, answer, grade_json, score, now, item_id, source_id))
            db.execute("UPDATE topic_state SET last_recall=?,next_recall=? WHERE topic_id=?",
                       (now, next_recall, topic_id))
            self._bump(db)
            attempt_id = cursor.lastrowid
        return next(item for item in self.snapshot()["attempts"] if item["id"] == attempt_id)

    @staticmethod
    def _concept_schedule_days(action: str, score: float | None, rating: str | None) -> int:
        if action == "self_rating":
            return {"again": 1, "hard": 3, "remembered": 7}[rating or "again"]
        assert score is not None
        return 1 if score < .6 else (3 if score < .85 else 7)

    def concept_review(self, catalog: list[dict[str, Any]], topic_id: str | None = None,
                       chapter_id: str | None = None, now: datetime | None = None,
                       limit: int = 3) -> dict[str, Any]:
        """Return compact due/unseen descriptors; answer text is never included."""
        current = now or self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        now_s = _stamp(current)
        limit = max(1, min(100, int(limit)))
        if topic_id is not None:
            self._validate(topic_id)
        allowed = []
        for item in catalog if isinstance(catalog, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
                continue
            topics = item.get("topic_ids", [])
            if topic_id and topic_id not in topics:
                continue
            if chapter_id and item.get("chapter_id") != chapter_id:
                continue
            allowed.append(item)
        with self._connect() as db:
            schedules = {row["item_id"]: dict(row) for row in db.execute("SELECT * FROM concept_schedule")}
        candidates = []
        for item in allowed:
            schedule = schedules.get(item["item_id"])
            if schedule and schedule["due_at"] > now_s:
                continue
            status = "due" if schedule else "unseen"
            candidates.append({"item_id": item["item_id"],
                               "topic_ids": list(item.get("topic_ids", [])),
                               "chapter_id": item.get("chapter_id"),
                               "chapter_title": item.get("chapter_title", ""),
                               "title": item.get("title", ""), "prompt": item.get("prompt", ""),
                               "source_ids": list(item.get("source_ids", [])), "kind": item.get("kind"),
                               "status": status,
                               "due_at": schedule["due_at"] if schedule else None,
                               "interval_days": schedule["interval_days"] if schedule else None,
                               "repetitions": schedule["repetitions"] if schedule else 0,
                               "last_outcome": schedule["last_outcome"] if schedule else None,
                               "last_score": schedule["last_score"] if schedule else None})
        candidates.sort(key=lambda item: (0 if item["status"] == "due" else 1,
                                          item["due_at"] or "", item["chapter_id"] or "", item["item_id"]))
        topic_counts: dict[str, dict[str, int]] = {}
        for candidate in candidates:
            for candidate_topic in candidate["topic_ids"]:
                counts = topic_counts.setdefault(candidate_topic, {"due_count": 0, "unseen_count": 0})
                counts["due_count" if candidate["status"] == "due" else "unseen_count"] += 1
        # Build short chapter sessions: honor all due items first, then add
        # nearby unseen items to the same chapter group when room permits.
        # Every item keeps its own stable ID and schedule.
        by_chapter: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            by_chapter.setdefault(candidate.get("chapter_id") or "unfiled", []).append(candidate)
        selected: list[dict[str, Any]] = []
        chapter_order = sorted(by_chapter, key=lambda chapter: (
            0 if any(item["status"] == "due" for item in by_chapter[chapter]) else 1,
            next((item["due_at"] or "" for item in by_chapter[chapter] if item["status"] == "due"), ""),
            -len(by_chapter[chapter]) if not any(item["status"] == "due" for item in by_chapter[chapter]) else 0,
            chapter))
        selected_by_chapter: dict[str, int] = {}
        selected_ids: set[str] = set()
        # First pass guarantees due work is never displaced by unseen prompts.
        for chapter in chapter_order:
            for item in by_chapter[chapter]:
                if item["status"] != "due" or selected_by_chapter.get(chapter, 0) >= 3:
                    continue
                selected.append(item)
                selected_ids.add(item["item_id"])
                selected_by_chapter[chapter] = selected_by_chapter.get(chapter, 0) + 1
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        # Fill partially represented chapters first, then add new chapter
        # groups in deterministic order, capped at three prompts per chapter.
        fill_order = list(dict.fromkeys([item.get("chapter_id") or "unfiled" for item in selected] + chapter_order))
        for chapter in fill_order:
            for item in by_chapter[chapter]:
                if item["item_id"] in selected_ids or selected_by_chapter.get(chapter, 0) >= 3:
                    continue
                selected.append(item)
                selected_ids.add(item["item_id"])
                selected_by_chapter[chapter] = selected_by_chapter.get(chapter, 0) + 1
                if len(selected) >= limit:
                    break
            if len(selected) >= limit:
                break
        groups: list[dict[str, Any]] = []
        group_by_chapter: dict[str, dict[str, Any]] = {}
        for item in selected:
            chapter = item.get("chapter_id") or "unfiled"
            group = group_by_chapter.get(chapter)
            if group is None:
                group = {"chapter_id": chapter, "chapter_title": item.get("chapter_title", ""), "items": []}
                group_by_chapter[chapter] = group
                groups.append(group)
            # Each prompt retains an independent item ID and schedule.
            group["items"].append(item)
        return {"items": selected, "groups": groups,
                "due_count": sum(1 for item in candidates if item["status"] == "due"),
                "unseen_count": sum(1 for item in candidates if item["status"] == "unseen"),
                "selected_count": len(selected), "topic_counts": topic_counts, "now": now_s}

    def record_concept_attempt(self, item_id: str, topic_id: str, prompt: str, answer: str,
                               action: str = "save", grade: Any = None,
                               self_rating: str | None = None, revealed: bool = False,
                               session_id: str | None = None,
                               attempt_id: str | None = None,
                               chapter_id: str = "") -> dict[str, Any]:
        """Record an item response; only assessment or revealed self-rating schedules it."""
        self._validate(topic_id)
        if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
            raise ValueError("A stable concept item ID is required")
        if not isinstance(chapter_id, str) or len(chapter_id) > 200:
            raise ValueError("chapter_id must be a bounded stable ID")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1_200:
            raise ValueError("A bounded prompt is required")
        if not isinstance(answer, str) or len(answer) > 2_000:
            raise ValueError("A bounded text answer is required")
        if session_id is not None and (not isinstance(session_id, str) or not session_id or len(session_id) > 100):
            raise ValueError("session_id must be a non-empty string of at most 100 characters")
        if attempt_id is None:
            attempt_id = str(uuid.uuid4())
        if not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 100:
            raise ValueError("attempt_id must be a non-empty string of at most 100 characters")
        score = None
        if action == "assessed":
            score = grade.get("score") if isinstance(grade, dict) else None
            if (not isinstance(grade, dict) or grade.get("assessed") is not True or
                    isinstance(score, bool) or not isinstance(score, (int, float)) or
                    not math.isfinite(score) or not 0 <= score <= 1):
                raise ValueError("An explicit assessed grade with a score from 0 to 1 is required")
            if self_rating is not None:
                raise ValueError("An assessed answer cannot also have a self-rating")
        elif action == "self_rating":
            if not revealed:
                raise ValueError("Self-rating is available only after the source is revealed")
            if self_rating not in {"again", "hard", "remembered"}:
                raise ValueError("Choose again, hard, or remembered after revealing the source")
            if grade is not None:
                raise ValueError("A self-rating is not an assessed grade")
        elif action == "save":
            if grade is not None and (not isinstance(grade, dict) or grade.get("assessed") is True):
                raise ValueError("Save cannot contain an assessment")
            if self_rating is not None:
                raise ValueError("Save cannot contain a self-rating")
        else:
            raise ValueError("Action must be save, assessed, or self_rating")
        if not isinstance(revealed, bool):
            raise ValueError("revealed must be a boolean")

        now_dt = self.clock()
        now_s = _stamp(now_dt)
        grade_json = json.dumps(grade, ensure_ascii=False, sort_keys=True) if grade is not None else None
        days = self._concept_schedule_days(action, float(score) if score is not None else None, self_rating) if action != "save" else None
        due_at = _stamp(now_dt + timedelta(days=days)) if days is not None else None
        with self._connect() as db:
            existing = db.execute("SELECT * FROM concept_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if existing:
                row = dict(existing)
                same_request = (row["session_id"] == session_id and row["item_id"] == item_id and
                                row["topic_id"] == topic_id and row["action"] == action and
                                row["prompt"] == prompt and row["answer"] == answer and
                                row["grade_json"] == grade_json and row["score"] == score and
                                row["self_rating"] == self_rating and bool(row["revealed"]) == revealed)
                if not same_request:
                    raise ValueError("attempt_id was already used for a different response")
                schedule = db.execute("SELECT * FROM concept_schedule WHERE item_id=?", (item_id,)).fetchone()
                return {"attempt_id": row["attempt_id"], "session_id": row["session_id"],
                        "item_id": row["item_id"], "topic_id": row["topic_id"], "action": row["action"],
                        "score": row["score"], "self_rating": row["self_rating"], "created_at": row["created_at"],
                        "schedule": dict(schedule) if schedule else None}
            db.execute("INSERT INTO concept_attempts(attempt_id,session_id,item_id,topic_id,action,prompt,answer,grade_json,score,self_rating,revealed,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (attempt_id, session_id, item_id, topic_id, action, prompt, answer, grade_json,
                        score, self_rating, int(bool(revealed)), now_s))
            if days is not None:
                db.execute("INSERT INTO concept_schedule(item_id,topic_id,chapter_id,due_at,interval_days,repetitions,last_outcome,last_score,updated_at) "
                            "VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
                            "topic_id=excluded.topic_id,chapter_id=excluded.chapter_id,due_at=excluded.due_at,"
                            "interval_days=excluded.interval_days,repetitions=concept_schedule.repetitions+1,"
                            "last_outcome=excluded.last_outcome,last_score=excluded.last_score,updated_at=excluded.updated_at",
                            (item_id, topic_id, chapter_id, due_at, days, action if action == "assessed" else self_rating,
                             score, now_s))
            self._bump(db)
            schedule_row = db.execute("SELECT * FROM concept_schedule WHERE item_id=?", (item_id,)).fetchone()
            return {"attempt_id": attempt_id, "session_id": session_id, "item_id": item_id,
                    "topic_id": topic_id, "action": action, "score": score,
                    "self_rating": self_rating, "created_at": now_s,
                    "schedule": dict(schedule_row) if schedule_row else None}

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
        # Keep the dashboard snapshot compact and avoid returning historical
        # answers on every workspace refresh. Explicit progress export includes
        # the additive per-item history and schedules.
        return self.snapshot(include_concepts=True)

    def summary(self) -> dict[str, Any]:
        state = self.snapshot()
        active = [tid for tid, value in state["topics"].items() if value["active"]]
        return {"revision": state["revision"], "active_topic_ids": active,
                "attempt_count": len(state["attempts"]),
                "due_concept_review_count": sum(1 for tid in active
                    if state["topics"][tid]["next_recall"] and state["topics"][tid]["next_recall"] <= _stamp(self.clock()))}
