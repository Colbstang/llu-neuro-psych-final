"""Loopback Step workspace. User state and OCR images live outside the checkout."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import signal
import sys
import threading
import time
import urllib.parse
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from StepStudy.anki_signals import AnkiSignals
from StepStudy.course_module import CourseModule
from StepStudy.course_reader import reader_html
from StepStudy.grading import grade_recall, provider_status
from StepStudy.intake import IntakeStore
from StepStudy.labs import LocalLabs, lab_policy
from StepStudy.reference_service import ReferenceService
from StepStudy.module_signals import collect_module_signals
from StepStudy.live_cards import LiveCards
from StepStudy.planning import build_review_queue
from StepStudy.recall import build_recall
from StepStudy.quiz_store import QuizStore, quiz_catalog
from StepStudy.store import StepStudyStore
from StepStudy.concept_learning import build_catalog, build_item_context
from StepStudy.study_session import StudySessions, make_plan
from StepStudy.visual_recall import VisualRecallStore
from StepStudy.learning_sources import course_images
from StepStudy.module_signals import summarize_module
from StudyApp.anki_bridge import AnkiBridge
from StudyApp.app_paths import default_data_dir as llu_study_data_dir
from StudyApp.progress_store import ProgressStore
from StudyApp.tts_service import LocalTTSService, TTSUnavailable
from TermCards.provider import TermProvider

ROOT = Path(__file__).resolve().parent
STATIC = {"/step.js": ROOT / "step.js", "/step.css": ROOT / "step.css",
          **{f"/{name}": ROOT / name for name in ("learning_flow.js", "learning_flow.css", "repair_practice.js", "repair_practice.css", "visual_practice.js", "visual_practice.css", "navigation.js", "anki_review.js", "anki_review.css")},
          "/shortcuts.js": ROOT / "shortcuts.js",
          "/workspace_tools.js": ROOT / "workspace_tools.js", "/workspace_tools.css": ROOT / "workspace_tools.css",
          "/course_embed.js": ROOT / "course_embed.js", "/course_embed.css": ROOT / "course_embed.css",
          "/terms/term_cards.js": PROJECT / "TermCards" / "term_cards.js",
          "/terms/selection_lookup.js": PROJECT / "TermCards" / "selection_lookup.js"}
COURSE_REFERENCE_BASE = "http://127.0.0.1:8768"
COURSE_PROXY_MAX_REQUEST = 16_384
COURSE_PROXY_MAX_RESPONSE = 25 * 1024 * 1024
COURSE_PROGRESS_MAX_BODY = 2 * 1024 * 1024
COURSE_ASSET_MAX_RESPONSE = 20 * 1024 * 1024
COURSE_REFERENCE_TIMEOUT = 40.0
COURSE_SIGNAL_KEYS = ("pages", "topic_groups", "objectives", "questions",
                      "question_auto_links", "question_link_corrections", "question_annotations")


def private_course_store_path(data_dir: Path, key: str, fallback: Path) -> Path:
    """Allow an installed workspace to retain its own course state files."""
    config = data_dir / "course-config.json"
    try:
        raw = json.loads(config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback
    except (OSError, ValueError):
        raise ValueError("Private course configuration is invalid") from None
    if not isinstance(raw, dict):
        raise ValueError("Private course configuration is invalid")
    value = raw.get(key)
    if value is None:
        return fallback
    if not isinstance(value, str) or len(value)>128 or not value.endswith(".sqlite3") or not value[0].isalnum() or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        raise ValueError(f"{key} must be a private SQLite filename")
    return data_dir / value


class _NoRedirect(HTTPRedirectHandler):
    """Keep the reference proxy pinned to its configured loopback service."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CourseProxyError(RuntimeError):
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _is_json_mime(mime: str) -> bool:
    media_type = mime.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _course_signal_data(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only the stable metadata used by course aggregate summaries."""
    pages = []
    for page in data.get("pages", []) if isinstance(data.get("pages"), list) else []:
        if not isinstance(page, dict):
            continue
        pages.append({
            "id": page.get("id", ""), "title": page.get("title", ""),
            "keywords": page.get("keywords", []),
            "blocks": [{"id": block.get("id"), "title": block.get("title", "")}
                       for block in page.get("blocks", [])
                       if isinstance(block, dict) and isinstance(block.get("id"), str)],
        })
    result = {key: data.get(key, []) for key in COURSE_SIGNAL_KEYS}
    result["pages"] = pages
    result["objectives"] = [
        {"id": row.get("id"), "sections": row.get("sections", [])}
        for row in result["objectives"] if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    result["questions"] = [
        {key: row.get(key) for key in ("id", "source_status", "suggested_sections") if key in row}
        for row in result["questions"] if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    return result


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Step Study"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "step-study"


class Services:
    def __init__(self, data_dir: Path, *, terms=None, intake=None, store=None, anki=None,
                 grader=None, ai_status=None, tts=None, module_reader=None, cards=None,
                 course=None, course_progress=None, course_anki=None, anki_bridge=None,
                 reference_transport=None, labs=None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        # A local study app never puts progress in a publishable checkout.
        if self.data_dir == PROJECT or PROJECT in self.data_dir.parents:
            raise ValueError("Choose a private app-data directory outside the repository")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.chmod(0o700)
        self.store = store or StepStudyStore(self.data_dir / "step-study.sqlite3")
        self.catalog = self.store.catalog
        self.terms = terms or TermProvider(PROJECT / "TermCards" / "data" / "terms.json", cache_path=self.data_dir / "term-cache.json")
        self.intake = intake or IntakeStore(self.data_dir / "question-intake.sqlite3", self.data_dir / "question-images")
        self.labs = labs or LocalLabs(self.data_dir / "labs.json")
        self.visual = VisualRecallStore(self.data_dir / "visual-recall.sqlite3")
        self.study_sessions = StudySessions(self.data_dir / "study-session.json")
        self.quizzes = QuizStore(self.data_dir / "comparison-quizzes.sqlite3")
        self._learning_catalog = None
        self.reference_service = ReferenceService(self.data_dir)
        self.anki = anki or AnkiSignals()
        self.cards = cards or LiveCards(self.data_dir)
        self.grader = grader or grade_recall
        self.ai_status = ai_status or provider_status()
        self.tts = tts or LocalTTSService()
        self._module_reader_injected = module_reader is not None
        # Existing server tests supply a module reader and must stay isolated
        # from the user's LLU progress database. Production uses the same
        # ProgressStore as LLU Study, plus a per-Step-app course config file.
        if course is not None:
            self.course = course
            self.course_progress = course_progress or getattr(course, "_store", None)
        elif self._module_reader_injected:
            self.course = None
            self.course_progress = course_progress
        else:
            self.course_progress = course_progress or ProgressStore(private_course_store_path(
                self.data_dir, "progress_file", llu_study_data_dir() / "progress.sqlite3"))
            self.course = CourseModule(
                config_path=self.data_dir / "course-config.json",
                progress_store=self.course_progress,
            )
        self.anki_bridge = course_anki if course_anki is not None else anki_bridge
        self.reference_transport = reference_transport
        self._course_anki_lock = threading.Lock()
        self._course_refresh_event = threading.Event()
        self._course_signal_data: dict[str, Any] | None = None
        self._course_document_pages: dict[str, int | None] | None = None
        self.course_prewarm_thread: threading.Thread | None = None
        self._intake_catalog_ready = False
        self._course_anki_instance = None
        self.pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.completed_concepts = OrderedDict()
        self.lock = threading.RLock()
        self.anki_lock = threading.Lock()
        self.module_lock = threading.Lock()
        self.module_reader = module_reader or collect_module_signals
        self.module_signals = {"available": False, "topics": {}, "reason": "Course module has not been read yet."}
        self.module_checked_at = 0.0
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        try:
            cached = json.loads((self.data_dir / "anki-signals.json").read_text())
            self.anki_signals = cached if isinstance(cached, dict) else {}
            if self.anki_signals:
                self.anki_signals["cached"] = True
        except (OSError, ValueError):
            self.anki_signals = {"available": False, "subjects": {}, "reason": "Sync Anki to read your current review signals."}

    def start_worker(self):
        self.reference_service.start()
        pref = self.store.snapshot()["preferences"]
        if pref.get("watchEnabled"):
            self.intake.start_watch(pref.get("watchFolder") or None)
        def poll():
            while not self.stop_event.wait(3):
                try:
                    self.intake.poll_watch()
                except Exception:
                    # Bad OCR/files must not bring down the studying workspace.
                    continue
        self.worker = threading.Thread(target=poll, daemon=True, name="question-folder-intake")
        self.worker.start()
        if self.course is not None and not self._module_reader_injected:
            self._course_refresh_event.set()
            self.course_prewarm_thread = threading.Thread(
                target=self._course_signal_worker, daemon=True, name="course-signal-reader")
            self.course_prewarm_thread.start()

    def _course_signal_worker(self):
        """Parse the large course bundle off the request path and refresh signals."""
        while not self.stop_event.is_set():
            self._course_refresh_event.wait(timeout=30)
            self._course_refresh_event.clear()
            if self.stop_event.is_set():
                return
            try:
                if self._course_signal_data is None:
                    full_data = self.course.data()
                    self.intake.configure_course(full_data)
                    self._intake_catalog_ready = True
                    self._course_signal_data = _course_signal_data(full_data)
                    self._course_document_pages = self._document_page_catalog(full_data)
                    del full_data
                progress = self.course.load_progress()
                result = summarize_module(
                    self._course_signal_data,
                    progress.get("state", {}) if isinstance(progress, dict) else {},
                    synced_at=progress.get("updated_at") if isinstance(progress, dict) else None,
                )
            except Exception:
                result = {"available": False, "topics": {}, "reason": "The course module is unavailable."}
            with self.lock:
                self.module_signals = result
                self.module_checked_at = time.monotonic()

    def close(self):
        self.reference_service.close()
        self.stop_event.set()
        self._course_refresh_event.set()
        from StepStudy.grading import cancel_active_grading
        cancel_active_grading()
        self.intake.close()
        if self.worker:
            self.worker.join(timeout=2)
        if self.course_prewarm_thread:
            self.course_prewarm_thread.join(timeout=2)
        prewarm_running = bool(self.course_prewarm_thread and self.course_prewarm_thread.is_alive())
        if self.course is not None and not prewarm_running:
            try:
                self.course.close()
            except Exception:
                pass
            if self.course_progress is not None:
                try:
                    self.course_progress.close()
                except Exception:
                    pass
        elif self.course is None and self.course_progress is not None and not prewarm_running:
            try:
                self.course_progress.close()
            except Exception:
                pass
        self.tts.close()
        self.cards.close()

    def intake_catalog(self):
        if self.course is not None and not self._intake_catalog_ready:
            try:
                self.intake.configure_course(self.course.data())
                self._intake_catalog_ready = True
            except (OSError, ValueError, TimeoutError):
                pass
        return [{key: row[key] for key in ("module_id", "topic_id", "topic_title", "chapter_id", "title")}
                for row in self.intake.course_catalog]

    def capture_region(self, body):
        context = body.get("context", {})
        if not isinstance(context, dict) or not set(context).issubset({"subject_id", "chapter_id"}):
            raise ValueError("Invalid capture context")
        self.intake_catalog()
        if context.get("subject_id") and context["subject_id"] not in self.store.topic_ids:
            raise ValueError("Unknown capture subject")
        if context.get("chapter_id") and context["chapter_id"] not in {row["chapter_id"] for row in self.intake.course_catalog}:
            raise ValueError("Unknown capture chapter")
        return self.intake.capture_region(context)

    def question_signals(self):
        raw = self.intake.topic_signals()
        return {"available": True, "topics": {tid: {**row, "wrong_count": row.get("wrong", 0),
                "unresolved_count": row.get("wrong", 0) + row.get("uncertain", 0),
                "total": sum(row.get(key, 0) for key in ("wrong", "uncertain", "correct", "unknown"))}
                for tid, row in raw.items() if any(row.values())}}

    def concept_catalog(self):
        if self.course is None: return []
        with self.lock:
            if self._learning_catalog is None:
                self._learning_catalog = build_catalog(self.course.data(), source_resolver=getattr(self.course, "source_document_url", None))
            return self._learning_catalog

    def concepts(self, topic_id=None, chapter_id=None, limit=100):
        catalog = self.concept_catalog()
        if not topic_id:
            active = self.store.snapshot()["topics"]
            catalog = [{**item, "topic_ids": [tid for tid in item["topic_ids"] if active.get(tid, {}).get("active")]}
                       for item in catalog if any(active.get(tid, {}).get("active") for tid in item["topic_ids"])]
        return {"ok": True, **self.store.concept_review(catalog, topic_id or None, chapter_id or None, limit=limit)}

    def start_concept(self, item_id, topic_id=None):
        if self.course is None: raise ValueError("The course module is unavailable")
        context = build_item_context(self.course.data(), item_id, source_resolver=getattr(self.course, "source_document_url", None))
        if not context: raise ValueError("This objective has no supplied source explanation yet")
        item = context["item"]
        active = self.store.snapshot()["topics"]
        topics = [tid for tid in item["topic_ids"] if active.get(tid, {}).get("active")]
        if topic_id and topic_id not in topics: raise ValueError("This objective is not in the selected active subject")
        if topic_id: topics = [topic_id]
        if not topics: raise ValueError("Activate this subject before practicing recall")
        context.update(item_id=item_id, topic_id=topics[0], chapter_id=item["chapter_id"],
                       session_id=secrets.token_urlsafe(18), revealed=False)
        context["context_hash"] = hashlib.sha256(json.dumps(
            [context["prompt"], context["reference_context"], item["source_ids"]],
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self.lock:
            draft = self.store.concept_draft(item_id, context["context_hash"])
            if draft and draft["revealed"]:
                context.update(revealed=True, independent_answer=draft["answer"])
            self.pending[context["session_id"]] = context
            while len(self.pending) > 100: self.pending.popitem(last=False)
        recall = {key: context[key] for key in ("session_id", "item_id", "topic_id", "chapter_id", "prompt", "sources", "revealed")}
        recall.update(chapter_title=item.get("chapter_title", ""), draft=draft["answer"] if draft else "")
        if context["revealed"]:
            recall["reference_context"] = context["reference_context"]
        return {"ok": True, "recall": recall}

    def save_concept_draft(self, session_id, answer):
        if not isinstance(answer, str) or len(answer) > 2000:
            raise ValueError("Enter a draft of at most 2,000 characters")
        if not isinstance(session_id, str):
            raise ValueError("Reopen this objective")
        with self.lock:
            context = self.pending.get(session_id)
            if not context or "item" not in context: raise ValueError("Reopen this objective")
            if context.get("submitting"): raise ValueError("This answer is being saved")
            if context["revealed"]:
                answer = context["independent_answer"]
            draft = self.store.save_concept_draft(context["item_id"], answer,
                                                 context["context_hash"], context["revealed"])
            if draft["revealed"]:
                context.update(revealed=True, independent_answer=draft["answer"])
            return {"ok": True}

    def reveal_concept(self, session_id, answer):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            raise ValueError("Write your explanation before checking the source")
        with self.lock:
            context = self.pending.get(session_id)
            if not context or "item" not in context: raise ValueError("Reopen this objective")
            if context.get("submitting"): raise ValueError("This answer is being saved")
            context["revealed"] = True
            context.setdefault("independent_answer", answer)
            draft = self.store.save_concept_draft(context["item_id"], context["independent_answer"],
                                                  context["context_hash"], True)
            context["independent_answer"] = draft["answer"]
            return {"ok": True, "reference_context": context["reference_context"], "answer": draft["answer"]}

    def answer_concept(self, body):
        session_id, answer, action = body.get("session_id"), body.get("answer"), body.get("action")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000: raise ValueError("Enter an answer of 1–2,000 characters")
        if action not in {"grade", "save", "self_rating"}: raise ValueError("Choose how to save this answer")
        with self.lock:
            signature = (answer, action, body.get("self_rating"))
            previous = self.completed_concepts.get(session_id)
            if previous:
                if previous[0] != signature: raise ValueError("This answer has already been saved")
                return previous[1]
            prompt = self.pending.get(session_id)
            if not prompt or "item" not in prompt: raise ValueError("This objective session expired. Reopen it.")
            if prompt.get("submitting"): raise ValueError("This answer is already being saved")
            draft = self.store.concept_draft(prompt["item_id"], prompt["context_hash"])
            if draft and draft["revealed"]:
                prompt.update(revealed=True, independent_answer=draft["answer"])
            if action == "grade" and prompt["revealed"]: raise ValueError("The explanation is revealed. Use your own recall rating.")
            if action == "self_rating" and (not prompt["revealed"] or answer != prompt.get("independent_answer")):
                raise ValueError("Check the source against your independent answer first")
            prompt["submitting"] = True
        try:
            grade = self.grader(prompt["prompt"], answer, prompt["expected_points"], prompt["sources"], reference_context=prompt["reference_context"]) if action == "grade" else None
            store_action = "assessed" if grade and grade.get("assessed") is True else ("self_rating" if action == "self_rating" else "save")
            attempt = self.store.record_concept_attempt(prompt["item_id"], prompt["topic_id"], prompt["prompt"], answer,
                action=store_action, grade=grade, self_rating=body.get("self_rating") if action == "self_rating" else None,
                revealed=prompt["revealed"], session_id=session_id, attempt_id=session_id, chapter_id=prompt["chapter_id"])
            result = {"ok": True, "grade": grade, "attempt": attempt}
            if action == "grade" or prompt["revealed"]:
                result["reference_context"] = prompt["reference_context"]
            with self.lock:
                self.pending.pop(session_id, None)
                self.completed_concepts[session_id] = (signature, result)
                while len(self.completed_concepts) > 100: self.completed_concepts.popitem(last=False)
            return result
        finally:
            with self.lock: prompt.pop("submitting", None)

    def visual_sources(self, chapter_id=None):
        rows = course_images(self.course.data(), chapter_id) if self.course else []
        if self.course and hasattr(self.course, "asset_mime"):
            rows = [row for row in rows if self.course.asset_mime(row["source"]["id"]) in {"image/png", "image/jpeg"}]
        topics = {}
        for item in self.concept_catalog(): topics.setdefault(item["chapter_id"], set()).update(item["topic_ids"])
        for row in rows: row["topic_ids"] = sorted(topics.get(row["chapter_id"], set()))
        if not chapter_id:
            for row in self.intake.list_questions(limit=100):
                rows.append({"source": {"kind": "intake_image", "id": str(row["id"])},
                             "title": "Saved screenshot " + str(row["id"]), "topic_ids": row["topic_ids"],
                             "image_url": "/api/visual/image/intake_image/" + str(row["id"])})
        return {"ok": True, "sources": rows}

    def visual_image(self, kind, identity):
        if kind == "upload": return self.visual.get_upload(identity)
        if kind == "course_asset" and self.course: return self.course.asset(identity)
        if kind == "intake_image":
            path = self.intake.image_path(int(identity))
            if path and path.is_file(): return {"body": path.read_bytes(), "mime_type": mimetypes.guess_type(path.name)[0] or "image/png"}
        return None

    def create_visual(self, body):
        source = body.get("source")
        if not isinstance(source, dict): raise ValueError("Choose an image")
        image = self.visual_image(source.get("kind"), source.get("id"))
        if not image: raise ValueError("This source image is unavailable. Choose it again.")
        # Course tokens are intentionally ephemeral. A private copy makes an
        # exercise and its masks survive a course reload or service restart.
        if source["kind"] == "course_asset":
            return {"ok": True, "exercise": self.visual.create_from_image(body.get("name"), image["body"], image["mime_type"], body.get("topic_ids", []), body.get("chapter_id"), body.get("masks", []))}
        return {"ok": True, "exercise": self.visual.create_exercise(body.get("name"), source, body.get("topic_ids", []), body.get("chapter_id"), body.get("masks", []))}

    def update_visual(self, body):
        patch = body.get("patch")
        if not isinstance(patch, dict): raise ValueError("Invalid exercise edit")
        source = patch.get("source")
        if source is not None:
            if not isinstance(source, dict): raise ValueError("Choose an image")
            image = self.visual_image(source.get("kind"), source.get("id"))
            if not image: raise ValueError("This image is unavailable")
            if source.get("kind") == "course_asset":
                return {"ok": True, "exercise": self.visual.update_from_image(body.get("id"), patch, image["body"], image["mime_type"])}
        return {"ok": True, "exercise": self.visual.update_exercise(body.get("id"), patch)}

    def start_study_session(self, minutes, topic_id):
        self.store._validate(topic_id)
        if not self.store.snapshot()["topics"][topic_id]["active"]: raise ValueError("Activate this subject first")
        concepts = self.concepts(topic_id, limit=20)["items"]
        repairs = self.intake.repair_queue(topic_id=topic_id)
        visuals = self.visual.list_exercises(topic_id=topic_id, due_only=True)
        lab = next((item for item in self.concept_catalog() if topic_id in item["topic_ids"] and "eye-movements" in item["chapter_id"]), None)
        plan = make_plan(concepts, repairs, visuals, self.anki_signals.get("subjects", {}).get(topic_id), minutes=minutes, topic_id=topic_id, lab=lab)
        return {"ok": True, "session": self.study_sessions.start(plan)}

    def workspace(self):
        state = self.store.snapshot()
        questions = self.question_signals()
        with self.lock:
            anki = dict(self.anki_signals)
        module = self.read_module_signals()
        concept_summary = self.concepts(limit=100)
        queue = build_review_queue(self.catalog, state, anki, questions, concept_items=concept_summary["items"], concept_counts=concept_summary["topic_counts"])
        if module.get("available") and state["preferences"].get("studyTarget") != "step":
            for tid, row in module.get("topics", {}).items():
                if not state["topics"].get(tid, {}).get("active"):
                    continue
                bad, wrong = row.get("lo_bad", 0), row.get("wrong_count", 0)
                if bad or wrong:
                    label = next(item["label"] for item in self.catalog if item["id"] == tid)
                    queue.append({"id": "course:" + tid, "topic_id": tid, "title": label,
                                  "kind": "course", "count": bad + wrong, "priority": 85,
                                  "reason": f"Course module: {bad} objectives marked bad · {wrong} unresolved wrong questions."})
            queue.sort(key=lambda row: (-row["priority"], row.get("due_at") or "", row["id"]))
        quiz_summaries = {}
        if self.course is not None and module.get("available"):
            course_data = self.course.data()
            for kind in ("drugs", "bugs", "pathology"):
                quiz_summaries[kind] = self.quizzes.snapshot(quiz_catalog(course_data, kind))["summary"]
        return {"ok": True, "catalog": self.catalog, "state": state,
                "queue": queue, "module": module,
                "anki": anki, "questions": questions, "intake": self.intake.status(),
                "ai": self.ai_status, "speech": {k: v for k, v in self.tts.status().items() if not k.startswith("missing_")},
                "learning": {"due_count": concept_summary["due_count"], "unseen_count": concept_summary["unseen_count"], "session": self.study_sessions.snapshot()},
                "quizzes": quiz_summaries,
                "glossary_count": len(self.terms.lexicon()), "module_url": "/#read"}

    def read_module_signals(self):
        # Read-only aggregates; a disconnected course module cannot stall studying.
        if not self._module_reader_injected:
            # The first full DATA parse runs on course-signal-reader. A stale
            # aggregate is fine here; it must not hold up the dashboard.
            return self.module_signals
        if time.monotonic() - self.module_checked_at < 30 or not self.module_lock.acquire(blocking=False):
            return self.module_signals
        try:
            try:
                result = self.module_reader()
            except Exception:
                result = {"available": False, "topics": {}, "reason": "The course module is unavailable."}
            self.module_signals = result
            self.module_checked_at = time.monotonic()
            return result
        finally:
            self.module_lock.release()

    def get_course_anki(self):
        if self.anki_bridge is not None:
            return self.anki_bridge
        if self.course is None:
            raise OSError("Course module is unavailable")
        if self._course_anki_instance is not None:
            return self._course_anki_instance
        if not self._course_anki_lock.acquire(blocking=False):
            raise ValueError("Anki status is still loading")
        try:
            if self._course_anki_instance is None:
                # The course bundle contains the card/note identity allowlist.
                # Reuse LLU Study's review ledger, with no ratings during load.
                ledger = private_course_store_path(self.data_dir, "anki_ledger_file", llu_study_data_dir() / "anki-bridge.sqlite3")
                self._course_anki_instance = AnkiBridge(self.course.anki_data(), sqlite_path=ledger)
            return self._course_anki_instance
        finally:
            self._course_anki_lock.release()

    def course_reference_request(self, name: str, body: dict[str, Any] | None = None):
        endpoints = {
            "health": ("GET", COURSE_REFERENCE_BASE + "/health"),
            "search": ("POST", COURSE_REFERENCE_BASE + "/search"),
            "source-page": ("POST", COURSE_REFERENCE_BASE + "/source-page"),
        }
        if name not in endpoints:
            raise CourseProxyError("Unknown course reference endpoint", 404)
        if name == "search":
            query = body.get("query") if isinstance(body, dict) else None
            scope = body.get("search_scope") if isinstance(body, dict) else None
            kind = body.get("kind") if isinstance(body, dict) else None
            families = body.get("reference_families") if isinstance(body, dict) else None
            limit = body.get("limit") if isinstance(body, dict) else None
            if not isinstance(query, str) or len(query) > 4000:
                raise ValueError("Search query must be text up to 4000 characters")
            if scope not in {"all", "cards", "books", "sources", "background"}:
                raise ValueError("Unknown course search scope")
            if kind not in {"all", "Ty", "AnKing", "NPS"}:
                raise ValueError("Unknown course search kind")
            if (not isinstance(families, list) or len(families) > 4
                    or any(family not in {"first_aid", "pathoma", "mehlman", "in_house"} for family in families)
                    or len(set(families)) != len(families)):
                raise ValueError("Unknown course reference family")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
                raise ValueError("Search limit must be between 1 and 50")
            for key in ("include_books", "include_sources", "include_background"):
                if not isinstance(body.get(key), bool):
                    raise ValueError("Course search source flags must be true or false")
        elif name == "source-page":
            document_id = body.get("documentId") if isinstance(body, dict) else None
            page = body.get("page") if isinstance(body, dict) else None
            query = body.get("query", "") if isinstance(body, dict) else None
            if (not isinstance(document_id, str) or not document_id or len(document_id) > 200
                    or "/" in document_id or "\\" in document_id):
                raise ValueError("A known source document is required")
            if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100_000:
                raise ValueError("Source page must be a positive page number")
            if not isinstance(query, str) or len(query) > 4000:
                raise ValueError("Source query must be text up to 4000 characters")
            if not self.course_document_page_allowed(document_id, page):
                raise ValueError("Unknown source document or page")
        method, endpoint = endpoints[name]
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        if payload is not None and len(payload) > COURSE_PROXY_MAX_REQUEST:
            raise CourseProxyError("Course reference request is too large", 413)
        try:
            if self.reference_transport is not None:
                result = self.reference_transport(endpoint, payload, COURSE_REFERENCE_TIMEOUT)
                if isinstance(result, tuple) and len(result) == 3:
                    status, mime, raw = result
                else:
                    status, mime, raw = 200, "application/json; charset=utf-8", json.dumps(result).encode("utf-8")
            else:
                self.reference_service.start()
                headers = {"Accept": "application/json", "Content-Type": "application/json"}
                request = Request(endpoint, data=payload, headers=headers, method=method)
                with build_opener(_NoRedirect()).open(request, timeout=COURSE_REFERENCE_TIMEOUT) as response:
                    status = response.status
                    mime = response.headers.get("Content-Type", "application/json; charset=utf-8")
                    raw = response.read(COURSE_PROXY_MAX_RESPONSE + 1)
            if not isinstance(raw, bytes):
                raw = bytes(raw)
            if len(raw) > COURSE_PROXY_MAX_RESPONSE:
                raise CourseProxyError("Course reference response is too large", 502)
            if not _is_json_mime(str(mime)):
                raise CourseProxyError("Course reference service returned an invalid response type", 502)
            return int(status), str(mime), raw
        except CourseProxyError:
            raise
        except HTTPError as exc:
            try:
                raw = exc.read(COURSE_PROXY_MAX_RESPONSE + 1)
                mime = exc.headers.get("Content-Type", "application/json; charset=utf-8")
                status = exc.code
            except Exception:
                raise CourseProxyError("Course reference service is unavailable", 503) from None
            if len(raw) > COURSE_PROXY_MAX_RESPONSE:
                raise CourseProxyError("Course reference response is too large", 502)
            if not _is_json_mime(str(mime)):
                raise CourseProxyError("Course reference service returned an invalid response type", 502)
            return status, mime, raw
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise CourseProxyError("Course reference service is unavailable", 503) from exc

    @staticmethod
    def _document_page_catalog(data):
        catalog = data.get("source_catalog") if isinstance(data, dict) else None
        documents = catalog.get("documents") if isinstance(catalog, dict) else None
        result = {}
        for document in documents if isinstance(documents, list) else []:
            if not isinstance(document, dict):
                continue
            identity = document.get("id")
            count = document.get("page_count")
            if not isinstance(identity, str) or not identity or "/" in identity or "\\" in identity:
                continue
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                continue
            result[identity] = count
        return result

    def course_document_page_allowed(self, identity: str, page: int) -> bool:
        if self.course is None:
            return False
        if self._course_document_pages is None:
            self._course_document_pages = self._document_page_catalog(self.course.data())
        if identity not in self._course_document_pages:
            return False
        return page <= self._course_document_pages[identity]

    def topic_records(self, topic_id):
        topic = next((row for row in self.catalog if row["id"] == topic_id), None)
        if topic is None:
            raise ValueError("Unknown subject")
        ids = topic.get("starterTermIds", [])[:8]
        queries = [(identity, True) for identity in ids]
        if not queries:
            queries = [(title, False) for title in topic.get("glossaryTitles", [])[:2]]
        records, misses = [], []
        for query, is_id in queries:
            result = self.terms.lookup(record_id=query) if is_id else self.terms.lookup(query=query)
            if result.get("ok"):
                records.append(result["record"])
            else:
                misses.append({"query": query, "error": result.get("error")})
        return topic, records, misses

    def recall(self, topic_id):
        topic, records, misses = self.topic_records(topic_id)
        state = self.store.snapshot()
        if not state["topics"][topic_id]["active"]:
            raise ValueError("Activate this subject before starting recall")
        previous = [row for row in state["attempts"] if row["topic_id"] == topic_id]
        prompt = build_recall(topic_id, records, previous_attempts=previous, topic_label=topic["label"])
        if prompt:
            prompt["session_id"] = secrets.token_urlsafe(18)
            with self.lock:
                self.pending[prompt["session_id"]] = prompt
                while len(self.pending) > 100:
                    self.pending.popitem(last=False)
        return {"ok": True, "recall": prompt, "misses": misses}

    def answer(self, session_id, answer, should_grade):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            raise ValueError("Enter an answer of 1–2,000 characters")
        with self.lock:
            prompt = self.pending.get(session_id)
            if not prompt:
                raise ValueError("This recall prompt expired. Open another prompt.")
            if prompt.get("submitting"):
                raise ValueError("This answer is already being saved")
            prompt["submitting"] = True
        try:
            if should_grade:
                grade = self.grader(prompt["prompt"], answer, prompt["expected_points"], prompt["sources"], reference_context=prompt["reference_context"])
            else:
                grade = {"assessed": False, "score": None, "confidence": None, "feedback": "Answer saved without an AI assessment.", "missed_concepts": [], "citations": [], "status": "ungraded"}
            assessed = grade.get("assessed") is True
            attempt = self.store.save_attempt(prompt["topic_id"], prompt["prompt"], answer, grade=grade, score=grade.get("score") if assessed else None, item_id=prompt.get("item_id"), source_id=prompt.get("source_id"))
            with self.lock:
                self.pending.pop(session_id, None)
            return {"ok": True, "grade": grade, "attempt": attempt}
        finally:
            with self.lock:
                prompt.pop("submitting", None)

    def sync_anki(self):
        if not self.anki_lock.acquire(blocking=False):
            raise ValueError("Anki sync is already in progress")
        try:
            state = self.store.snapshot()
            identities = [topic["id"] for topic in self.catalog]
            selected = state["preferences"].get("lastTopic")
            prioritized = [tid for tid in identities if state["topics"][tid]["active"]]
            if selected in prioritized:
                prioritized.remove(selected)
                prioritized.insert(0, selected)
            order = prioritized + [tid for tid in identities if tid not in prioritized]
            result = self.anki.sync(topic_ids=order)
            # Only aggregate counts are cached. All data remain per user.
            temp = self.data_dir / ".anki-signals.tmp"
            temp.write_text(json.dumps(result), encoding="utf-8")
            temp.replace(self.data_dir / "anki-signals.json")
            with self.lock:
                self.anki_signals = result
            return {"ok": True, "anki": result}
        finally:
            self.anki_lock.release()

    def card_queue(self, topic_id):
        if not isinstance(topic_id, str) or topic_id not in self.store.topic_ids:
            raise ValueError("Unknown subject")
        if not self.store.snapshot()["topics"][topic_id]["active"]:
            raise ValueError("Activate this subject before reviewing its cards")
        return self.cards.queue(topic_id)

    def rate_card(self, topic_id, token, ease, request_id):
        result = self.cards.rate(topic_id, token, ease, request_id)
        if result.get("confirmed"):
            with self.lock:
                self.anki_signals = {**self.anki_signals, "cached": True}
        return result


class StepServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def __init__(self, address, services):
        super().__init__(address, Handler)
        self.services = services
        self.token = secrets.token_urlsafe(32)
    def server_close(self):
        self.services.close()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: StepServer
    protocol_version = "HTTP/1.1"
    server_version = "StepStudy/0.1"
    sys_version = ""

    def guard(self, mutation=False, *, course=False):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}:
            self.json(403, {"ok": False, "error": "Request must come from this local app"}); return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://localhost:{port}", f"http://127.0.0.1:{port}", f"http://[::1]:{port}"}:
            self.json(403, {"ok": False, "error": "Request must come from this local app"}); return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.json(403, {"ok": False, "error": "Cross-site requests are not allowed"}); return False
        token_header = "X-LLU-App-Token" if course else "X-Step-Token"
        if mutation and not hmac.compare_digest(self.headers.get(token_header, ""), self.server.token):
            self.json(403, {"ok": False, "error": "Refresh the app before saving"}); return False
        return True

    def do_GET(self):
        # Opaque lab frames may load only their registered lab files. They never
        # pass the app API guard; mutations still require the app session token.
        if urlsplit(self.path).path.startswith("/lab/"):
            self.lab_file(); return
        if not self.guard(): return
        parsed = urlsplit(self.path)
        if len(parsed.query) > 1500:
            self.json(400, {"ok": False, "error": "Query too long"}); return
        params = urllib.parse.parse_qs(parsed.query, max_num_fields=8)
        param = lambda key, default="": params.get(key, [default])[0]
        service = self.server.services
        try:
            if parsed.path == "/health": self.json(200, {"app": "step-study", "ready": True})
            elif parsed.path == "/api/workspace": self.json(200, service.workspace())
            elif parsed.path == "/course/reader":
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                view = param("view", "guide")
                subject = param("subject", "neuro")
                target = param("target", "both")
                scope = param("scope") or None
                data = service.course.data()
                anki = service.course.anki_data()
                page, policy = reader_html(data, anki, self.server.token,
                                           view=view, subject=subject, target=target, scope=scope,
                                           source=service.course.status().get("source", "bundle"))
                self.send(200, page, "text/html; charset=utf-8", csp=policy); return
            elif parsed.path == "/api/course/status":
                if service.course is None:
                    self.json(503, {"ok": False, "available": False, "reason": "Course module unavailable"}); return
                self.json(200, {"ok": True, **service.course.status()})
            elif parsed.path == "/api/course/data":
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                self.json(200, {"ok": True, "data": service.course.data()})
            elif parsed.path.startswith("/api/course/document/") and parsed.path.endswith(".pdf"):
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                identity = urllib.parse.unquote(parsed.path[len("/api/course/document/"):-4])
                if not identity or "/" in identity or "\\" in identity:
                    self.json(404, {"ok": False, "error": "Not found"}); return
                document = service.course.document_pdf(identity)
                path = Path(document.get("path", "")) if isinstance(document, dict) else Path()
                if (not isinstance(document, dict) or document.get("kind") != "file"
                        or not path.is_file() or path.stat().st_size > 256 * 1024 * 1024):
                    self.json(404, {"ok": False, "error": "Not found"}); return
                self.send(200, path.read_bytes(), "application/pdf"); return
            elif parsed.path == "/api/course/progress":
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                self.json(200, service.course.load_progress())
            elif parsed.path == "/api/course/quizzes":
                if service.course is None: raise CourseProxyError("The course module is unavailable", 503)
                self.json(200, service.quizzes.snapshot(quiz_catalog(service.course.data(), param("kind"))))
            elif parsed.path == "/api/course/health":
                status, mime, body = service.course_reference_request("health")
                if status >= 400:
                    self.send(status, body, mime); return
                self.send(200, body, mime); return
            elif parsed.path == "/api/course/asset/" or parsed.path.startswith("/api/course/asset/"):
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                identity = urllib.parse.unquote(parsed.path.removeprefix("/api/course/asset/"))
                if not identity or "/" in identity:
                    self.json(404, {"ok": False, "error": "Not found"}); return
                asset = service.course.asset(identity)
                if not asset:
                    self.json(404, {"ok": False, "error": "Not found"}); return
                body = asset.get("body")
                mime_type = asset.get("mime_type")
                size = asset.get("size")
                if (not isinstance(body, bytes)
                        or isinstance(size, bool) or not isinstance(size, int)
                        or size != len(body) or size > COURSE_ASSET_MAX_RESPONSE
                        or mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}):
                    self.json(404, {"ok": False, "error": "Not found"}); return
                self.send(200, body, mime_type); return
            elif parsed.path in {"/api/course/anki/status", "/api/course/anki/coverage"}:
                bridge = service.get_course_anki()
                if parsed.path.endswith("/status"):
                    status = bridge.status()
                    status["coverage"] = bridge.coverage()
                    self.json(200, status)
                else:
                    self.json(200, bridge.coverage())
            elif parsed.path == "/api/course/topic":
                topic_id = param("id")
                if service.course is None:
                    self.json(503, {"ok": False, "error": "The course module is unavailable"}); return
                data = service.course.data()
                self.json(200, {"ok": True, "data": data, "topic_id": topic_id})
            elif parsed.path == "/api/terms": self.json(200, {"ok": True, "records": service.terms.lexicon()})
            elif parsed.path == "/api/term":
                q, identity = param("q"), param("id")
                if not (bool(q) ^ bool(identity)) or len(q) > 160 or len(identity) > 96: raise ValueError("Choose one short medical term")
                self.json(200, service.terms.lookup(query=q) if q else service.terms.lookup(record_id=identity))
            elif parsed.path == "/api/topic":
                topic, records, misses = service.topic_records(param("id"))
                self.json(200, {"ok": True, "topic": topic, "records": records, "misses": misses})
            elif parsed.path == "/api/recall": self.json(200, service.recall(param("topic_id")))
            elif parsed.path == "/api/concepts": self.json(200, service.concepts(param("topic_id"), param("chapter_id"), int(param("limit", "100"))))
            elif parsed.path == "/api/concept": self.json(200, service.start_concept(param("item_id"), param("topic_id") or None))
            elif parsed.path == "/api/gaze": self.json(200, {"ok": True, "items": [row for row in service.concept_catalog() if "eye-movements" in row["chapter_id"]][:30]})
            elif parsed.path == "/api/study-session": self.json(200, {"ok": True, "session": service.study_sessions.snapshot()})
            elif parsed.path == "/api/question-repair": self.json(200, service.intake.get_repair(int(param("id"))))
            elif parsed.path == "/api/repair-queue": self.json(200, {"ok": True, "questions": service.intake.repair_queue()})
            elif parsed.path == "/api/visual/sources": self.json(200, service.visual_sources(param("chapter_id") or None))
            elif parsed.path == "/api/visual/exercises": self.json(200, {"ok": True, "exercises": service.visual.list_exercises(topic_id=param("topic_id") or None, chapter_id=param("chapter_id") or None, due_only=param("due") == "1")})
            elif parsed.path == "/api/visual/exercise": self.json(200, {"ok": True, "exercise": service.visual.get_exercise(param("id"))})
            elif parsed.path.startswith("/api/visual/image/"):
                parts = parsed.path.removeprefix("/api/visual/image/").split("/")
                if len(parts) != 2: raise ValueError("Unknown image")
                result = service.visual_image(parts[0], urllib.parse.unquote(parts[1]))
                if not result: self.json(404, {"ok": False, "error": "Image unavailable"}); return
                self.send(200, result["body"], result["mime_type"])
            elif parsed.path == "/api/card-queue": self.json(200, service.card_queue(param("topic_id")))
            elif parsed.path == "/api/anki-session":
                tid = param("topic_id")
                if tid not in service.store.topic_ids: raise ValueError("Unknown subject")
                self.json(200, service.cards.session_snapshot(tid))
            elif parsed.path == "/api/questions":
                chapter = param("chapter_id")
                rows = (service.intake.search_questions(param("q"), limit=100) if param("q")
                        else service.intake.questions_for_chapter(chapter) if chapter
                        else service.intake.list_questions(status=param("status") or None, limit=100))
                topic = param("topic_id")
                if topic: rows = [row for row in rows if topic in row["topic_ids"]]
                if chapter: rows = [row for row in rows if any(link.get("chapter_id") == chapter for link in row["course_links"])]
                self.json(200, {"ok": True, "questions": rows, "intake": service.intake.status()})
            elif parsed.path == "/api/intake/catalog":
                self.json(200, {"ok": True, "chapters": service.intake_catalog()})
            elif parsed.path == "/api/intake/chapter-links":
                self.json(200, {"ok": True, "counts": service.intake.chapter_link_counts()})
            elif parsed.path == "/api/labs":
                self.json(200, {"ok": True, "labs": service.labs.list()})
            elif parsed.path == "/api/question-image":
                path = service.intake.image_path(int(param("id")))
                self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png")
            elif parsed.path == "/api/export":
                payload = {**service.store.export_state(), "question_repairs": service.intake.export_repairs(), "study_session": service.study_sessions.snapshot(), "comparison_quizzes": service.quizzes.export_state()}
                self.send(200, json.dumps(payload, indent=2).encode(), "application/json", {"Content-Disposition": 'attachment; filename="step-study-progress.json"'})
            elif parsed.path == "/api/visual/export":
                self.send(200, json.dumps(service.visual.export_state()).encode(), "application/json", {"Content-Disposition": 'attachment; filename="step-study-visual-recall.json"'})
            elif parsed.path in ("/", "/index.html"):
                page = (ROOT / "index.html").read_text().replace("__STEP_TOKEN__", self.server.token)
                self.send(200, page.encode(), "text/html; charset=utf-8")
            elif parsed.path in STATIC:
                path = STATIC[parsed.path]
                mime = "text/javascript" if path.suffix == ".js" else "text/css"
                self.send(200, path.read_bytes(), mime + "; charset=utf-8")
            else: self.json(404, {"ok": False, "error": "Not found"})
        except (ValueError, TypeError, KeyError) as error:
            self.json(400, {"ok": False, "error": str(error)[:200]})
        except CourseProxyError as error:
            self.json(error.status, {"ok": False, "error": str(error)[:200]})
        except OSError:
            self.json(503, {"ok": False, "error": "This local resource is unavailable"})

    def do_POST(self):
        path = urlsplit(self.path).path
        is_course_route = path.startswith("/api/course/")
        if not self.guard(mutation=True, course=is_course_route): return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.json(415, {"ok": False, "error": "JSON is required"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 15 * 1024 * 1024 if path == "/api/visual/upload" else COURSE_PROGRESS_MAX_BODY if path in {"/api/course/progress", "/api/course/progress/import"} else (COURSE_PROXY_MAX_REQUEST if path in {"/api/course/search", "/api/course/source-page"} else 100_000)
            if not 0 < length <= limit: raise ValueError("Request too large or empty")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError("Request must be an object")
            service = self.server.services
            if path == "/api/course/progress":
                if service.course is None: raise CourseProxyError("The course module is unavailable", 503)
                patch, revision = body.get("patch"), body.get("base_revision")
                if not isinstance(patch, (dict, list)) or (revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0)):
                    raise ValueError("Expected a progress patch and optional integer base_revision")
                result = service.course.patch_progress(patch, revision)
                service._course_refresh_event.set()
            elif path == "/api/course/progress/import":
                if service.course is None or service.course_progress is None:
                    raise CourseProxyError("The course module is unavailable", 503)
                state = body.get("state")
                if not isinstance(state, dict): raise ValueError("Expected state to be an object")
                # This route is used only by the explicit Import progress action.
                # Page startup never imports or replaces saved progress.
                result = service.course_progress.import_state(state, replace=True)
                service._course_refresh_event.set()
            elif path in {"/api/course/search", "/api/course/source-page"}:
                name = "search" if path.endswith("/search") else "source-page"
                status, mime, raw = service.course_reference_request(name, body)
                self.send(status, raw, mime); return
            elif path == "/api/course/quizzes":
                if service.course is None: raise CourseProxyError("The course module is unavailable", 503)
                result = service.quizzes.action(quiz_catalog(service.course.data(), body.get("kind")), body)
            elif path == "/api/course/tts":
                text, voice = body.get("text"), body.get("voice", "Bella")
                if not isinstance(text, str) or not text.strip() or len(text) > 180 or len(text.split()) > 24:
                    raise ValueError("Choose a medical term or short phrase, up to 180 characters")
                audio = service.tts.synthesize(text.strip(), voice)
                status = service.tts.status()
                self.json(200, {"audio": base64.b64encode(audio).decode("ascii"),
                                "mime": "audio/wav", "model": status.get("model")}); return
            elif path == "/api/course/anki/sync":
                bridge = service.get_course_anki()
                result = bridge.sync_history()
            elif path == "/api/course/anki/begin":
                bridge = service.get_course_anki()
                card_id = body.get("cardId")
                if isinstance(card_id, bool) or not isinstance(card_id, (int, str)):
                    raise ValueError("Card ID must be an integer")
                result = bridge.begin_review(card_id)
            elif path == "/api/course/anki/review":
                bridge = service.get_course_anki()
                token, ease, request_id = body.get("token"), body.get("ease"), body.get("request_id")
                if not isinstance(token, str) or not token or isinstance(ease, bool) or not isinstance(ease, int) or ease not in (1, 2, 3, 4) or not isinstance(request_id, str) or not request_id:
                    raise ValueError("Review requires a token, explicit ease 1–4, and request_id")
                result = bridge.submit_review(token=token, ease=ease, request_id=request_id)
            elif path == "/api/activate":
                if not isinstance(body.get("active"), bool): raise ValueError("Choose active or paused")
                service.store.activate(body.get("topic_id"), body["active"]); result = service.workspace()
            elif path == "/api/notes":
                if not isinstance(body.get("notes"), str) or len(body["notes"]) > 20_000: raise ValueError("Notes too long")
                service.store.save_notes(body.get("topic_id"), body["notes"]); result = {"ok": True}
            elif path == "/api/preferences":
                values = body.get("preferences")
                allowed = {"automaticTerms", "panelWidth", "referenceDetached", "learnedTerms", "collapsedTerms", "studyTarget", "lastTopic"}
                if not isinstance(values, dict) or not set(values).issubset(allowed): raise ValueError("Invalid preference")
                if "panelWidth" in values and (isinstance(values["panelWidth"], bool) or not isinstance(values["panelWidth"], (int, float)) or not 25 <= values["panelWidth"] <= 70): raise ValueError("Panel width must be 25–70%")
                if "automaticTerms" in values and not isinstance(values["automaticTerms"], bool): raise ValueError("Automatic definitions must be on or off")
                for key in ("learnedTerms", "collapsedTerms"):
                    if key in values and (not isinstance(values[key], dict) or len(values[key]) > 2000 or any(not isinstance(v, bool) for v in values[key].values())): raise ValueError("Invalid section preferences")
                if "lastTopic" in values and values["lastTopic"] not in service.store.topic_ids: raise ValueError("Unknown subject")
                if "studyTarget" in values and values["studyTarget"] not in {"both", "step", "in-house"}: raise ValueError("Unknown study target")
                service.store.set_preferences(values); result = {"ok": True}
            elif path == "/api/answer": result = service.answer(body.get("session_id"), body.get("answer"), body.get("grade") is True)
            elif path == "/api/concept-reveal": result = service.reveal_concept(body.get("session_id"), body.get("answer"))
            elif path == "/api/concept-draft": result = service.save_concept_draft(body.get("session_id"), body.get("answer"))
            elif path == "/api/concept-answer": result = service.answer_concept(body)
            elif path == "/api/study-session":
                if body.get("action") == "start": result = service.start_study_session(body.get("minutes", 15), body.get("topic_id"))
                else:
                    current = service.study_sessions.snapshot()
                    task = next((row for row in (current or {}).get("tasks", []) if row["id"] == body.get("task_id")), None)
                    if body.get("action") == "complete" and task and task["kind"] == "anki":
                        accepted = service.cards.confirmed_review(task["ref"], body.get("request_id"))
                        session = service.study_sessions.complete_anki(body.get("session_id"), body.get("task_id"), accepted)
                    else:
                        session = service.study_sessions.update(body.get("session_id"), body.get("task_id"), action=body.get("action"), draft=body.get("draft"))
                    result = {"ok": True, "session": session}
            elif path == "/api/question-repair":
                if body.get("action") == "save": result = service.intake.save_repair(int(body.get("id")), body.get("repair"))
                elif body.get("action") == "retry": result = service.intake.record_retry(int(body.get("id")), body.get("answer"), body.get("self_result"), body.get("request_id"))
                else: raise ValueError("Unknown repair action")
            elif path == "/api/visual/create": result = service.create_visual(body)
            elif path == "/api/visual/update": result = service.update_visual(body)
            elif path == "/api/visual/review": result = service.visual.record_review(body.get("id"), body.get("rating"), body.get("request_id"))
            elif path == "/api/visual/upload":
                value = body.get("data")
                if not isinstance(value, str): raise ValueError("Choose an image file")
                try: image = base64.b64decode(value, validate=True)
                except ValueError: raise ValueError("Invalid image data")
                source = service.visual.store_upload(image, body.get("mime_type"))
                result = {"ok": True, "source": source, "image_url": "/api/visual/image/upload/" + source["id"]}
            elif path == "/api/anki-sync": result = service.sync_anki()
            elif path == "/api/anki-session":
                tid, sid, action = body.get("topic_id"), body.get("session_id"), body.get("action")
                if not isinstance(tid, str) or tid not in service.store.topic_ids or not service.store.snapshot()["topics"][tid]["active"]:
                    raise ValueError("Activate a known subject before reviewing its cards")
                if action == "start": result = service.cards.start_session(tid, body.get("limit", 10))
                elif action == "open": result = service.cards.open_session(tid, sid)
                elif action == "reveal": result = service.cards.reveal_session(tid, sid)
                elif action == "rate":
                    result = service.cards.rate_session(tid, sid, body.get("ease"), body.get("request_id"))
                    if result.get("review", {}).get("confirmed"):
                        with service.lock: service.anki_signals = {**service.anki_signals, "cached": True}
                elif action == "skip": result = service.cards.skip_session(tid, sid)
                elif action == "dismiss": result = service.cards.dismiss_session(tid, sid, body.get("request_id"))
                else: raise ValueError("Unknown Anki session action")
            elif path == "/api/card-start":
                tid = body.get("topic_id")
                if not isinstance(tid, str) or tid not in service.store.topic_ids or not service.store.snapshot()["topics"][tid]["active"]:
                    raise ValueError("Activate a known subject before reviewing its cards")
                result = service.cards.begin(tid, body.get("card_id"))
            elif path == "/api/card-rate":
                result = service.rate_card(body.get("topic_id"), body.get("token"), body.get("ease"), body.get("request_id"))
            elif path == "/api/capture-region":
                result = service.capture_region(body)
            elif path == "/api/labs/register":
                result = {"ok": True, "lab": service.labs.register(body.get("path"), body.get("title", ""))}
            elif path == "/api/labs/open":
                result = service.labs.open_native(body.get("id"))
            elif path == "/api/labs/remove":
                result = service.labs.remove(body.get("id"))
            elif path == "/api/watch":
                enabled = body.get("enabled")
                if not isinstance(enabled, bool): raise ValueError("Choose whether watching is enabled")
                folder = body.get("folder") or None
                if folder is not None and (not isinstance(folder, str) or len(folder) > 2000): raise ValueError("Invalid folder")
                status = service.intake.start_watch(folder) if enabled else service.intake.stop_watch()
                service.store.set_preferences({"watchEnabled": bool(status["running"]), "watchFolder": folder})
                result = {"ok": True, "watch": status}
            elif path == "/api/scan-folder":
                folder = body.get("folder")
                if not isinstance(folder, str) or not folder or len(folder) > 2000: raise ValueError("Choose a folder")
                result = {"ok": True, "scan": service.intake.scan_folder(folder)}
            elif path == "/api/question":
                identity = int(body.get("id"))
                action = body.get("action")
                if action == "confirm": row = service.intake.confirm(identity, True)
                elif action == "exclude": row = service.intake.delete_question(identity)
                elif action == "outcome": row = service.intake.mark_outcome(identity, body.get("outcome"))
                elif action == "topics": row = service.intake.set_topics(identity, body.get("topic_ids"))
                elif action == "retry": row = service.intake.retry_question(identity)
                elif action == "course-links":
                    service.intake_catalog()
                    row = service.intake.set_course_links(identity, body.get("chapter_ids"))
                else: raise ValueError("Unknown question action")
                result = {"ok": True, "question": row}
            elif path == "/api/speech":
                text = body.get("text")
                if not isinstance(text, str) or not text or len(text) > 180: raise ValueError("Choose a short term to pronounce")
                audio = service.tts.synthesize(text, body.get("voice", "Bella"))
                self.send(200, audio, "audio/wav"); return
            else:
                self.json(404, {"ok": False, "error": "Not found"}); return
            self.json(200, result)
        except (ValueError, TypeError, KeyError) as error:
            self.json(400, {"ok": False, "error": str(error)[:200]})
        except CourseProxyError as error:
            self.json(error.status, {"ok": False, "error": str(error)[:200]})
        except RuntimeError as error:
            self.json(503, {"ok": False, "error": str(error)[:200]})
        except (OSError, TTSUnavailable):
            self.json(503, {"ok": False, "error": "The local service is unavailable"})

    def lab_file(self):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        origins = {f"http://localhost:{port}", f"http://127.0.0.1:{port}", f"http://[::1]:{port}"}
        if host not in {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"} or self.headers.get("Origin") not in {None, "null", *origins}:
            self.json(403, {"ok": False, "error": "Lab files are local only"}); return
        pieces = urlsplit(self.path).path.split("/", 3)
        try:
            if len(pieces) != 4:
                raise KeyError("Lab file not found")
            identity, relative = pieces[2], urllib.parse.unquote(pieces[3])
            body, mime = self.server.services.labs.asset(identity, relative)
            self.send(200, body, mime, {"Access-Control-Allow-Origin": "null", "Referrer-Policy": "no-referrer"},
                      csp=lab_policy(f"http://{host}", identity))
        except (ValueError, OSError, KeyError):
            self.json(404, {"ok": False, "error": "Lab file not found"})

    def json(self, status, payload):
        self.send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")
    def send(self, status, body, mime, headers=None, *, csp=None):
        self.send_response(status)
        self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", csp or "default-src 'self'; connect-src 'self'; frame-src 'self'; img-src 'self' data: https://upload.wikimedia.org https://mdwiki.org; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'")
        if headers:
            for key, value in headers.items(): self.send_header(key, value)
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(body); self.close_connection = True
    def log_message(self, *args):
        # Do not put selected terms or private question text into access logs.
        pass


def make_server(port=8773, data_dir=None, services=None):
    service = services or Services(data_dir or default_data_dir())
    return StepServer(("127.0.0.1", port), service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8773)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir)
    server.services.start_worker()
    def request_shutdown(_signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, request_shutdown)
    print(f"Step Study ready at http://localhost:{server.server_address[1]}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
