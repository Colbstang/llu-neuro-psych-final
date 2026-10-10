"""Private screenshot intake for the optional Step study app.

Images and database files are supplied by the caller and must live outside the
repository. OCR is local (Apple Vision) and can be injected in tests.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

CATALOG_PATH = Path(__file__).with_name("data") / "topics.json"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 80_000_000
MAX_SCAN_BATCH = 20
MAX_POLL_ENTRIES = 1000
MAX_SCAN_ENTRIES = 5000
MAX_POLL_OCR = 2
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
QUESTION_HINT = re.compile(r"\?|\b(which|what|most likely|best explains|most appropriate|would be expected|is the cause)\b", re.I)
OPTION_LINE = re.compile(r"^\s*(?:\(?[A-Ea-e]\)|[A-Ea-e][.)]|\d+[.)])\s+\S")
MEDICAL_CUE = re.compile(
    r"\b(patient|diagnos(?:is|tic)|disease|disorder|syndrome|symptom|clinical|"
    r"artery|vein|cerebral|aphasia|seizure|stroke|tumou?r|cancer|infection|"
    r"medication|drug|dose|pharmacology|anatomy|physiology|pathology|"
    r"blood pressure|heart rate|respiratory|neurologic|neurological|psychiatric)\b", re.I)
TITLE_GENERIC = set("""anatomy organization control overview foundations normal clinical features
pathways pathway diseases disease disorder disorders system systems syndrome syndromes brain
cerebral spinal nerve nerves neurology neurologic psychiatry psychiatric approach patterns pattern
review course chapter specific mechanism mechanisms treatment treatments physiology pathology
pharmacology introduction function functional signals guide gross common before after understand
explain compare comparison localize localise identify differentiate presentation development
underlying structure structures major important learning objectives recognize recognition model
section topic process blood vessels infection infections patient patients support response responses
adult pediatric paediatric congenital acute chronic white matter central peripheral motor sensory
anterior posterior medial lateral findings management basics principles""".split())


def _private_path(path: str | Path, name: str) -> Path:
    target = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if target == repo or repo in target.parents:
        raise ValueError(f"{name} must be stored outside the repository")
    return target


def _image_header(path: Path) -> tuple[str, int, int]:
    """Validate PNG/JPEG magic and read bounded dimensions without decoding pixels."""
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ValueError("Image is empty or exceeds the 20 MiB intake limit")
    with path.open("rb") as stream:
        header = stream.read(32)
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            width, height = struct.unpack(">II", header[16:24])
            kind = "png"
        elif header.startswith(b"\xff\xd8"):
            width, height = _jpeg_dimensions(stream)
            kind = "jpeg"
        else:
            raise ValueError("File does not have a supported PNG or JPEG header")
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError("Image dimensions are invalid or exceed the 80 megapixel limit")
    return kind, width, height


def _jpeg_dimensions(stream) -> tuple[int, int]:
    stream.seek(2)
    while True:
        byte = stream.read(1)
        if not byte:
            break
        if byte != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker or marker in (b"\xd8", b"\xd9"):
            continue
        raw = stream.read(2)
        if len(raw) != 2:
            break
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            break
        if marker[0] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            data = stream.read(5)
            if len(data) == 5:
                return struct.unpack(">HH", data[1:5])
            break
        stream.seek(length - 2, 1)
    raise ValueError("JPEG dimensions could not be read")


class VisionOCR:
    """Compile and run the bundled Vision OCR helper in a private temp cache."""
    def __init__(self, swift_path: str | Path | None = None, timeout: float = 20,
                 compile_timeout: float = 120):
        self.swift_path = Path(swift_path) if swift_path else Path(__file__).with_name("VisionOCR.swift")
        self.timeout = timeout
        user_id = getattr(os, "getuid", lambda: "user")()
        cache_dir = Path(tempfile.gettempdir()) / f"stepstudy-{user_id}"
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            cache_dir.chmod(0o700)
        except OSError:
            pass
        self.binary = cache_dir / f"vision-{hashlib.sha256(str(self.swift_path.resolve()).encode()).hexdigest()[:12]}"
        self.compile_timeout = compile_timeout
        self._lock = threading.Lock()

    def _ensure_compiled(self) -> None:
        if self.binary.is_file() and self.binary.stat().st_mtime >= self.swift_path.stat().st_mtime:
            return
        with self._lock:
            if self.binary.is_file() and self.binary.stat().st_mtime >= self.swift_path.stat().st_mtime:
                return
            result = subprocess.run(["xcrun", "swiftc", str(self.swift_path), "-o", str(self.binary)],
                                    capture_output=True, text=True, timeout=self.compile_timeout, check=False)
            if result.returncode:
                raise RuntimeError("Apple Vision OCR could not be compiled: " + (result.stderr.strip()[-1200:] or "unknown compiler error"))

    def __call__(self, image_path: Path) -> str:
        self._ensure_compiled()
        result = subprocess.run([str(self.binary), str(image_path)], capture_output=True, text=True,
                                timeout=self.timeout, check=False)
        if result.returncode:
            raise RuntimeError("Apple Vision OCR failed: " + (result.stderr.strip()[-1200:] or "unknown OCR error"))
        try:
            value = json.loads(result.stdout)
            return str(value.get("text", ""))
        except (json.JSONDecodeError, AttributeError) as error:
            raise RuntimeError("Apple Vision OCR returned invalid output") from error


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


class IntakeStore:
    """Manage screenshot OCR records, review state and a server-polled folder watcher."""
    def __init__(self, db_path: str | Path, image_dir: str | Path,
                 catalog_path: str | Path | None = None,
                 ocr: Callable[[Path], str] | None = None,
                 clock: Callable[[], float] | None = None,
                 capture: Callable[[Path], bool] | None = None):
        self.path = _private_path(db_path, "Intake database")
        self.image_dir = _private_path(image_dir, "Screenshot image directory")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_path = Path(catalog_path) if catalog_path else CATALOG_PATH
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.topics = catalog["topics"]
        self.ocr = ocr or VisionOCR()
        self.clock = clock or time.time
        self.capture = capture or self._capture_selection
        self._capture_lock = threading.Lock()
        self.course_catalog: list[dict[str, Any]] = []
        self._watch_lock = threading.RLock()
        self._watch_folder: Path | None = None
        self._watch_default = False
        self._baseline: set[str] = set()
        self._observed: dict[str, tuple[int, int, int]] = {}
        self._watch_errors: list[str] = []
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, factory=_ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                image_name TEXT NOT NULL,
                full_text TEXT NOT NULL,
                stem TEXT NOT NULL DEFAULT '',
                options_json TEXT NOT NULL DEFAULT '[]',
                explanation TEXT NOT NULL DEFAULT '',
                topic_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL CHECK(status IN ('confirmed','needs_confirmation','excluded','deleted')),
                outcome TEXT NOT NULL DEFAULT 'unknown' CHECK(outcome IN ('unknown','wrong','uncertain','correct')),
                error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
              );
              CREATE INDEX IF NOT EXISTS questions_status_created ON questions(status,created_at DESC);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(questions)")}
            for column in ("course_links_json", "tag_evidence_json"):
                if column not in columns:
                    db.execute(f"ALTER TABLE questions ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'")
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(full_text, stem, options, content='questions', content_rowid='id')")
                db.executescript("""
                  CREATE TRIGGER IF NOT EXISTS questions_fts_insert AFTER INSERT ON questions BEGIN
                    INSERT INTO questions_fts(rowid,full_text,stem,options) VALUES(new.id,new.full_text,new.stem,new.options_json);
                  END;
                  CREATE TRIGGER IF NOT EXISTS questions_fts_update AFTER UPDATE ON questions BEGIN
                    INSERT INTO questions_fts(questions_fts,rowid,full_text,stem,options) VALUES('delete',old.id,old.full_text,old.stem,old.options_json);
                    INSERT INTO questions_fts(rowid,full_text,stem,options) VALUES(new.id,new.full_text,new.stem,new.options_json);
                  END;
                """)
                db.execute("INSERT INTO questions_fts(questions_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                # The regular table search remains available on SQLite builds without FTS5.
                pass

    def _parse(self, text: str) -> tuple[str, list[str], str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        option_indexes = [i for i, line in enumerate(lines) if OPTION_LINE.match(line)]
        options = [lines[i] for i in option_indexes]
        stem = "\n".join(lines[:option_indexes[0]]) if option_indexes else "\n".join(lines)
        explanation = ""
        # Preserve recognizable explanation text, while retaining it in full_text regardless.
        for i, line in enumerate(lines):
            if re.match(r"^(?:explanation|rationale)\s*[:\-]", line, re.I):
                explanation = "\n".join(lines[i:])
                break
        has_question = bool(QUESTION_HINT.search(stem)) and len(stem) >= 8
        # A stem plus options alone also describes ordinary surveys. Require a
        # clinical cue or course-taxonomy signal before automatic confirmation.
        medical_context = bool(self._topic_ids(stem)) or bool(MEDICAL_CUE.search(stem))
        if has_question and len(options) >= 2 and medical_context:
            status = "confirmed"
        elif has_question or options:
            status = "needs_confirmation"
        else:
            status = "excluded"
        return stem, options, explanation, status

    def _topic_ids(self, text: str) -> list[str]:
        folded = text.casefold()
        return [topic["id"] for topic in self.topics
                if any(re.search(r"(?<!\w)" + re.escape(keyword.casefold()) + r"(?!\w)", folded)
                       for keyword in topic.get("keywords", []) if keyword)]

    def configure_course(self, data: dict[str, Any]) -> None:
        """Index stable labels and keywords only; never classify from private prose."""
        grouping = data.get("topic_groups")
        groups = grouping.get("groups", []) if isinstance(grouping, dict) else []
        page_groups: dict[str, list[dict[str, Any]]] = {}
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get("id"), str):
                continue
            for identity in group.get("page_ids", []) if isinstance(group.get("page_ids"), list) else []:
                if isinstance(identity, str):
                    page_groups.setdefault(identity, []).append(group)
        result = []
        for page in data.get("pages", []) if isinstance(data.get("pages"), list) else []:
            if not isinstance(page, dict) or not isinstance(page.get("id"), str):
                continue
            title = str(page.get("title", ""))[:300]
            keywords = [value[:100] for value in (page.get("keywords", []) if isinstance(page.get("keywords"), list) else [])
                        if isinstance(value, str) and value.strip()]
            # Long title phrases are useful when no explicit keywords exist;
            # generic individual words never create a chapter link.
            titles = [title, *[str(block.get("title", ""))[:300] for block in (page.get("blocks", []) if isinstance(page.get("blocks"), list) else [])
                              if isinstance(block, dict)]]
            distinctive, phrases = [], []
            for label in titles:
                words = [word.casefold() for word in re.findall(r"[A-Za-z]+", label)]
                terms = [word for word in words if len(word) >= 6 and word not in TITLE_GENERIC]
                distinctive.extend(terms)
                for part in re.split(r"[,;:/]|\s+and\s+", label, flags=re.I):
                    if len(part.split()) >= 2 and any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", part, re.I) for term in terms):
                        phrases.append(part.strip())
            for group in page_groups.get(page["id"], [{}]):
                result.append({"module_id": "neuro-psych", "topic_id": str(group.get("id", "")),
                               "topic_title": str(group.get("title", ""))[:200],
                               "chapter_id": page["id"], "title": title,
                               "keywords": list(dict.fromkeys(keywords + phrases + distinctive))})
        self.course_catalog = result

    def _automatic_links(self, text: str, context: dict[str, Any] | None = None) -> tuple[list, list]:
        folded = text.casefold()
        links, evidence = [], []
        for topic in self.topics:
            matches = [keyword for keyword in topic.get("keywords", []) if keyword and
                       re.search(r"(?<!\w)" + re.escape(keyword.casefold()) + r"(?!\w)", folded)]
            if matches:
                evidence.append({"subject_id": topic["id"], "confidence": "keyword", "evidence": matches})
            elif context and context.get("subject_id") == topic["id"]:
                evidence.append({"subject_id": topic["id"], "confidence": "context", "evidence": []})
        for chapter in self.course_catalog:
            matches = [keyword for keyword in chapter["keywords"] if
                       re.search(r"(?<!\w)" + re.escape(keyword.casefold()) + r"(?!\w)", folded)]
            contextual = bool(context and context.get("chapter_id") == chapter["chapter_id"])
            if matches or contextual:
                links.append({key: chapter[key] for key in ("module_id", "topic_id", "chapter_id", "title", "topic_title")})
                links[-1].update({"confidence": "keyword" if matches else "context", "evidence": matches})
        return links, evidence

    @staticmethod
    def _capture_selection(target: Path) -> bool:
        """An explicit region selection only. Escape cancels without an intake item."""
        executable = Path("/usr/sbin/screencapture")
        if not executable.is_file():
            raise RuntimeError("Region capture is available in the Mac app. Import a screenshot file instead.")
        try:
            result = subprocess.run([str(executable), "-i", "-s", "-x", str(target)],
                                    capture_output=True, text=True, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Capture timed out. Choose Capture region again when ready.") from None
        if target.is_file() and target.stat().st_size:
            return True
        if result.stderr.strip():
            raise RuntimeError("The Mac could not capture this region. Check Screen Recording permission in System Settings.")
        return False

    def capture_region(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._capture_lock.acquire(blocking=False):
            raise ValueError("A region selection is already open")
        try:
            with tempfile.TemporaryDirectory(prefix="capture-", dir=self.image_dir) as folder:
                target = Path(folder) / "region.png"
                if not self.capture(target):
                    return {"ok": True, "cancelled": True}
                result = self.ingest_file(target, context=context)
                # An explicit capture may be a teaching figure. Retain it for
                # review rather than silently hiding it as a non-question.
                if not result["duplicate"] and result["question"]["status"] == "excluded":
                    result["question"] = self.confirm(result["question"]["id"], False)
                return {"ok": True, "cancelled": False, **result}
        finally:
            self._capture_lock.release()

    def ingest_file(self, source: str | Path, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        source = Path(source).expanduser().resolve()
        try:
            kind, _, _ = _image_header(source)
        except OSError:
            raise ValueError("Image is missing or inaccessible") from None
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM questions WHERE sha256=?", (sha,)).fetchone()
            if existing:
                return {"duplicate": True, "question": self._row(existing)}
        dest = self.image_dir / f"{sha}.{kind}"
        if not dest.exists():
            shutil.copyfile(source, dest)
        try:
            text = self.ocr(dest)
            if not isinstance(text, str):
                raise ValueError("OCR adapter must return text")
            text = text.strip()[:200_000]
            if not text:
                raise RuntimeError("OCR returned no readable text")
            stem, options, explanation, status = self._parse(text)
            tags = self._topic_ids(stem)
            links, evidence = self._automatic_links(stem, context)
            tags = list(dict.fromkeys(tags + [item["subject_id"] for item in evidence]))
            now = float(self.clock())
            with self._connect() as db:
                cursor = db.execute("INSERT OR IGNORE INTO questions(sha256,image_name,full_text,stem,options_json,explanation,topic_ids_json,status,created_at,updated_at,course_links_json,tag_evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (sha, dest.name, text, stem, json.dumps(options, ensure_ascii=False), explanation,
                                     json.dumps(tags), status, now, now, json.dumps(links), json.dumps(evidence)))
                row = db.execute("SELECT * FROM questions WHERE sha256=?", (sha,)).fetchone()
            return {"duplicate": cursor.rowcount == 0, "question": self._row(row)}
        except Exception as error:
            now = float(self.clock())
            safe_error = str(error).replace(str(source), "[source image]")
            safe_error = safe_error.replace(str(dest), "[private image]").replace(str(self.image_dir), "[private storage]")
            # Record processing failures without surfacing source paths.
            with self._connect() as db:
                cursor = db.execute("INSERT OR IGNORE INTO questions(sha256,image_name,full_text,status,error,created_at,updated_at) VALUES(?,?,?,'needs_confirmation',?,?,?)",
                                    (sha, dest.name, "", safe_error[:1000], now, now))
                row = db.execute("SELECT * FROM questions WHERE sha256=?", (sha,)).fetchone()
            return {"duplicate": cursor.rowcount == 0, "error": safe_error[:1000], "question": self._row(row)}

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "full_text": row["full_text"], "stem": row["stem"],
                "options": json.loads(row["options_json"]), "explanation": row["explanation"],
                "topic_ids": json.loads(row["topic_ids_json"]), "status": row["status"],
                "outcome": row["outcome"], "error": row["error"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "course_links": json.loads(row["course_links_json"]),
                "tag_evidence": json.loads(row["tag_evidence_json"])}

    def list_questions(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status is not None and status not in {"confirmed", "needs_confirmation", "excluded", "deleted"}:
            raise ValueError("Unknown question status")
        limit = max(1, min(int(limit), 500))
        with self._connect() as db:
            if status:
                rows = db.execute("SELECT * FROM questions WHERE status=? ORDER BY created_at DESC,id DESC LIMIT ?", (status, limit))
            else:
                rows = db.execute("SELECT * FROM questions WHERE status!='deleted' ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))
            return [self._row(row) for row in rows]

    def get_question(self, question_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM questions WHERE id=? AND status!='deleted'", (int(question_id),)).fetchone()
        if row is None:
            raise KeyError("Question not found")
        return self._row(row)

    def questions_for_chapter(self, chapter_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(chapter_id, str) or not chapter_id or len(chapter_id) > 200:
            raise ValueError("Choose a stable course chapter ID")
        limit = max(1, min(int(limit), 200))
        with self._connect() as db:
            rows = db.execute("""SELECT q.* FROM questions q WHERE q.status IN ('confirmed','needs_confirmation')
                AND EXISTS (SELECT 1 FROM json_each(q.course_links_json) link
                            WHERE json_extract(link.value,'$.chapter_id')=?)
                ORDER BY q.created_at DESC,q.id DESC LIMIT ?""", (chapter_id, limit))
            return [self._row(row) for row in rows]

    def image_path(self, question_id: int) -> Path:
        """Return the private stored image for an existing question ID only."""
        with self._connect() as db:
            row = db.execute("SELECT image_name FROM questions WHERE id=? AND status!='deleted'", (int(question_id),)).fetchone()
        if row is None:
            raise KeyError("Question not found")
        name = Path(row["image_name"]).name
        path = (self.image_dir / name).resolve()
        if path.parent != self.image_dir or not path.is_file():
            raise FileNotFoundError("Stored screenshot is unavailable")
        return path

    def search_questions(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        terms = [term for term in re.findall(r"[\w-]+", str(query)) if term]
        if not terms:
            return []
        limit = max(1, min(int(limit), 200))
        expression = " AND ".join('"' + term.replace('"', '""') + '"*' for term in terms)
        with self._connect() as db:
            try:
                rows = db.execute("SELECT q.* FROM questions_fts f JOIN questions q ON q.id=f.rowid WHERE questions_fts MATCH ? AND q.status NOT IN ('deleted','excluded') ORDER BY rank LIMIT ?", (expression, limit))
            except sqlite3.OperationalError:
                like = "%" + "%".join(terms) + "%"
                rows = db.execute("SELECT * FROM questions WHERE status NOT IN ('deleted','excluded') AND (full_text LIKE ? OR stem LIKE ?) ORDER BY created_at DESC LIMIT ?", (like, like, limit))
            return [self._row(row) for row in rows]

    def confirm(self, question_id: int, confirmed: bool = True) -> dict[str, Any]:
        status = "confirmed" if confirmed else "needs_confirmation"
        with self._connect() as db:
            row = db.execute("SELECT * FROM questions WHERE id=? AND status!='deleted'", (int(question_id),)).fetchone()
            if row is None:
                raise KeyError("Question not found")
            if confirmed and (not row["full_text"].strip() or row["error"]):
                raise ValueError("This item has no usable OCR text; retry OCR before confirming it")
            db.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?", (status, float(self.clock()), int(question_id)))
            row = db.execute("SELECT * FROM questions WHERE id=?", (int(question_id),)).fetchone()
        if row is None:
            raise KeyError("Question not found")
        return self._row(row)

    def retry_question(self, question_id: int) -> dict[str, Any]:
        """Retry local OCR for a saved failure without changing its content hash or ID."""
        with self._connect() as db:
            row = db.execute("SELECT * FROM questions WHERE id=? AND status!='deleted'", (int(question_id),)).fetchone()
        if row is None:
            raise KeyError("Question not found")
        if row["full_text"].strip() and not row["error"]:
            raise ValueError("This question does not need an OCR retry")
        image_name = Path(row["image_name"]).name
        image = (self.image_dir / image_name).resolve()
        if image.parent != self.image_dir or not image.is_file():
            raise FileNotFoundError("Stored screenshot is unavailable")
        try:
            _image_header(image)
            text = self.ocr(image)
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("OCR returned no readable text")
            text = text.strip()[:200_000]
            stem, options, explanation, status = self._parse(text)
            tags = self._topic_ids(stem)
            links, evidence = self._automatic_links(stem)
            error = ""
        except Exception as failure:
            error = str(failure).replace(str(image), "[private image]")
            error = error.replace(str(self.image_dir), "[private storage]")[:1000]
            text, stem, options, explanation, tags, status = "", "", [], "", [], "needs_confirmation"
            links, evidence = [], []
        with self._connect() as db:
            db.execute("UPDATE questions SET full_text=?,stem=?,options_json=?,explanation=?,topic_ids_json=?,status=?,error=?,updated_at=?,course_links_json=?,tag_evidence_json=? WHERE id=? AND status!='deleted'",
                       (text, stem, json.dumps(options, ensure_ascii=False), explanation, json.dumps(tags), status, error,
                        float(self.clock()), json.dumps(links), json.dumps(evidence), int(question_id)))
            updated = db.execute("SELECT * FROM questions WHERE id=? AND status!='deleted'", (int(question_id),)).fetchone()
        if updated is None:
            raise KeyError("Question not found")
        return self._row(updated)

    def set_course_links(self, question_id: int, chapter_ids: list[str]) -> dict[str, Any]:
        if not isinstance(chapter_ids, list) or len(chapter_ids) > 100 or any(not isinstance(value, str) for value in chapter_ids):
            raise ValueError("chapter_ids must be a bounded list of stable course IDs")
        known = {row["chapter_id"] for row in self.course_catalog}
        unique = set(chapter_ids)
        if not unique.issubset(known):
            raise ValueError("Unknown course chapter ID")
        links = [{**{key: row[key] for key in ("module_id", "topic_id", "chapter_id", "title", "topic_title")},
                  "confidence": "reviewed", "evidence": []}
                 for row in self.course_catalog if row["chapter_id"] in unique]
        with self._connect() as db:
            cursor = db.execute("UPDATE questions SET course_links_json=?,updated_at=? WHERE id=? AND status!='deleted'",
                                (json.dumps(links), float(self.clock()), int(question_id)))
            row = db.execute("SELECT * FROM questions WHERE id=?", (int(question_id),)).fetchone()
        if not cursor.rowcount or row is None:
            raise KeyError("Question not found")
        return self._row(row)

    def mark_outcome(self, question_id: int, outcome: str) -> dict[str, Any]:
        if outcome not in {"unknown", "wrong", "uncertain", "correct"}:
            raise ValueError("Outcome must be unknown, wrong, uncertain, or correct")
        with self._connect() as db:
            cursor = db.execute("UPDATE questions SET outcome=?,updated_at=? WHERE id=? AND status='confirmed'", (outcome, float(self.clock()), int(question_id)))
            row = db.execute("SELECT * FROM questions WHERE id=?", (int(question_id),)).fetchone()
        if cursor.rowcount == 0 or row is None:
            raise KeyError("Confirmed question not found")
        return self._row(row)

    def set_topics(self, question_id: int, topic_ids: list[str]) -> dict[str, Any]:
        """Set user-reviewed taxonomy topics; input IDs are checked against catalog."""
        if not isinstance(topic_ids, list) or any(not isinstance(value, str) for value in topic_ids):
            raise ValueError("topic_ids must be a list of catalog IDs")
        known = {topic["id"] for topic in self.topics}
        unique = list(dict.fromkeys(topic_ids))
        if any(topic_id not in known for topic_id in unique):
            raise ValueError("Unknown Step study topic ID")
        evidence = [{"subject_id": identity, "confidence": "reviewed", "evidence": []} for identity in unique]
        with self._connect() as db:
            cursor = db.execute("UPDATE questions SET topic_ids_json=?,tag_evidence_json=?,updated_at=? WHERE id=? AND status!='deleted'",
                                (json.dumps(unique), json.dumps(evidence), float(self.clock()), int(question_id)))
            row = db.execute("SELECT * FROM questions WHERE id=?", (int(question_id),)).fetchone()
        if cursor.rowcount == 0 or row is None:
            raise KeyError("Question not found")
        return self._row(row)

    def delete_question(self, question_id: int) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute("UPDATE questions SET status='deleted',updated_at=? WHERE id=? AND status!='deleted'", (float(self.clock()), int(question_id)))
            row = db.execute("SELECT * FROM questions WHERE id=?", (int(question_id),)).fetchone()
        if cursor.rowcount == 0 or row is None:
            raise KeyError("Question not found")
        return {"deleted": True, "id": int(question_id)}

    def topic_signals(self) -> dict[str, dict[str, int]]:
        result = {topic["id"]: {key: 0 for key in ("wrong", "uncertain", "correct", "unknown")} for topic in self.topics}
        with self._connect() as db:
            rows = db.execute("SELECT topic_ids_json,outcome FROM questions WHERE status='confirmed'")
            for row in rows:
                for topic_id in json.loads(row["topic_ids_json"]):
                    if topic_id in result:
                        result[topic_id][row["outcome"]] += 1
        return result

    def chapter_link_counts(self) -> dict[str, int]:
        """Count many-to-many associations without returning screenshots or prose."""
        counts: dict[str, int] = {}
        with self._connect() as db:
            for row in db.execute("SELECT course_links_json FROM questions WHERE status IN ('confirmed','needs_confirmation')"):
                for identity in {link.get("chapter_id") for link in json.loads(row[0]) if link.get("chapter_id")}:
                    counts[identity] = counts.get(identity, 0) + 1
        return counts

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            counts = {row["status"]: row["n"] for row in db.execute("SELECT status,COUNT(*) n FROM questions GROUP BY status")}
            counts["error"] = db.execute("SELECT COUNT(*) FROM questions WHERE status!='deleted' AND error!=''").fetchone()[0]
        return {"counts": counts, "watch": self.watch_status()}

    @staticmethod
    def _eligible(path: Path, default_mode: bool) -> bool:
        if path.suffix.lower() not in IMAGE_EXTENSIONS or path.is_symlink() or not path.is_file():
            return False
        return not default_mode or bool(re.match(r"^(?:screenshot|screen shot|screenshot)\b", path.name, re.I))

    def start_watch(self, folder: str | Path | None = None) -> dict[str, Any]:
        default_mode = folder is None
        target = Path(folder).expanduser().resolve() if folder is not None else (Path.home() / "Desktop").resolve()
        with self._watch_lock:
            self._watch_errors.clear()
            try:
                if not target.is_dir():
                    raise NotADirectoryError("Watch folder is missing or is not a directory")
                self._watch_folder = target
                self._watch_default = default_mode
                self._baseline = {str(p.resolve()) for p in target.iterdir() if self._eligible(p, default_mode)}
                self._observed.clear()
            except OSError as error:
                self._watch_folder = None
                self._baseline.clear()
                self._observed.clear()
                self._watch_errors.append("Watch folder is missing or inaccessible")
        return self.watch_status()

    def stop_watch(self) -> dict[str, Any]:
        with self._watch_lock:
            self._watch_folder = None
            self._baseline.clear()
            self._observed.clear()
        return self.watch_status()

    def close(self) -> None:
        """Stop the poll-controlled watcher; no worker thread is created."""
        self.stop_watch()

    def watch_status(self) -> dict[str, Any]:
        with self._watch_lock:
            return {"running": self._watch_folder is not None,
                    "folder_name": self._watch_folder.name if self._watch_folder else None,
                    "errors": list(self._watch_errors[-10:])}

    def poll_watch(self) -> dict[str, Any]:
        with self._watch_lock:
            folder = self._watch_folder
            if folder is None:
                return {"running": False, "processed": [], "errors": []}
            try:
                paths = [p for p in itertools.islice(folder.iterdir(), MAX_POLL_ENTRIES)
                         if self._eligible(p, self._watch_default)]
            except Exception as error:
                message = "Folder scan failed because the selected folder is unavailable"
                self._watch_errors.append(message)
                return {"running": True, "processed": [], "errors": [message]}
            processed, errors = [], []
            processed_count = 0
            for path in paths:
                key = str(path.resolve())
                if key in self._baseline:
                    continue
                try:
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
                    previous = self._observed.get(key)
                    self._observed[key] = signature
                    if previous != signature or stat.st_size <= 0:
                        continue
                    if processed_count >= MAX_POLL_OCR:
                        continue
                    result = self.ingest_file(path)
                    processed.append(result)
                    processed_count += 1
                    self._baseline.add(key)
                    self._observed.pop(key, None)
                except Exception as error:
                    message = "An image could not be processed; check its file and retry"
                    errors.append(message)
                    self._watch_errors.append(message)
            return {"running": True, "processed": processed, "errors": errors}

    def scan_folder(self, folder: str | Path) -> dict[str, Any]:
        target = Path(folder).expanduser().resolve()
        counts = {"confirmed": 0, "needs_confirmation": 0, "excluded": 0,
                  "duplicates": 0, "errors": [], "scanned": 0, "truncated": False}
        try:
            if not target.is_dir():
                raise NotADirectoryError("Scan folder is missing or is not a directory")
            paths = []
            entries_seen = 0
            with os.scandir(target) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_SCAN_ENTRIES:
                        counts["truncated"] = True
                        break
                    if (len(paths) >= MAX_SCAN_BATCH or entry.is_symlink() or
                            Path(entry.name).suffix.lower() not in IMAGE_EXTENSIONS or
                            not entry.is_file(follow_symlinks=False)):
                        if len(paths) >= MAX_SCAN_BATCH:
                            counts["truncated"] = True
                            break
                        continue
                    paths.append(Path(entry.path))
        except OSError as error:
            counts["errors"].append("Folder is missing or inaccessible")
            return counts
        for path in paths:
            counts["scanned"] += 1
            try:
                result = self.ingest_file(path)
                if result.get("duplicate"):
                    counts["duplicates"] += 1
                else:
                    counts[result["question"]["status"]] += 1
                    if result.get("error"):
                        counts["errors"].append(result["error"])
            except Exception as error:
                counts["errors"].append("One image could not be processed; check image format and permissions")
        return counts
