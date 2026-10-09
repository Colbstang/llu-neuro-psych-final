"""Loopback Step workspace. User state and OCR images live outside the checkout."""
from __future__ import annotations

import argparse
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

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
from StepStudy.anki_signals import AnkiSignals
from StepStudy.grading import grade_recall, provider_status
from StepStudy.intake import IntakeStore
from StepStudy.module_signals import collect_module_signals
from StepStudy.live_cards import LiveCards
from StepStudy.planning import build_review_queue
from StepStudy.recall import build_recall
from StepStudy.store import StepStudyStore
from StudyApp.tts_service import LocalTTSService, TTSUnavailable
from TermCards.provider import TermProvider

ROOT = Path(__file__).resolve().parent
STATIC = {"/step.js": ROOT / "step.js", "/step.css": ROOT / "step.css",
          "/terms/term_cards.js": PROJECT / "TermCards" / "term_cards.js",
          "/terms/selection_lookup.js": PROJECT / "TermCards" / "selection_lookup.js"}


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Step Study"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "step-study"


class Services:
    def __init__(self, data_dir: Path, *, terms=None, intake=None, store=None, anki=None, grader=None, ai_status=None, tts=None, module_reader=None, cards=None):
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
        self.anki = anki or AnkiSignals()
        self.cards = cards or LiveCards(self.data_dir)
        self.grader = grader or grade_recall
        self.ai_status = ai_status or provider_status()
        self.tts = tts or LocalTTSService()
        self.pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
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

    def close(self):
        self.stop_event.set()
        from StepStudy.grading import cancel_active_grading
        cancel_active_grading()
        self.intake.close()
        if self.worker:
            self.worker.join(timeout=2)
        self.tts.close()
        self.cards.close()

    def question_signals(self):
        raw = self.intake.topic_signals()
        return {"available": True, "topics": {tid: {**row, "wrong_count": row.get("wrong", 0),
                "unresolved_count": row.get("wrong", 0) + row.get("uncertain", 0),
                "total": sum(row.get(key, 0) for key in ("wrong", "uncertain", "correct", "unknown"))}
                for tid, row in raw.items() if any(row.values())}}

    def workspace(self):
        state = self.store.snapshot()
        questions = self.question_signals()
        with self.lock:
            anki = dict(self.anki_signals)
        module = self.read_module_signals()
        queue = build_review_queue(self.catalog, state, anki, questions)
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
        return {"ok": True, "catalog": self.catalog, "state": state,
                "queue": queue, "module": module,
                "anki": anki, "questions": questions, "intake": self.intake.status(),
                "ai": self.ai_status, "speech": {k: v for k, v in self.tts.status().items() if not k.startswith("missing_")},
                "glossary_count": len(self.terms.lexicon()), "module_url": "http://localhost:8770/"}

    def read_module_signals(self):
        # Read-only aggregates; a disconnected course module cannot stall studying.
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
            attempt = self.store.save_attempt(prompt["topic_id"], prompt["prompt"], answer, grade=grade, score=grade.get("score") if assessed else None)
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

    def guard(self, mutation=False):
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}:
            self.json(403, {"ok": False, "error": "Request must come from this local app"}); return False
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://localhost:{port}", f"http://127.0.0.1:{port}", f"http://[::1]:{port}"}:
            self.json(403, {"ok": False, "error": "Request must come from this local app"}); return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.json(403, {"ok": False, "error": "Cross-site requests are not allowed"}); return False
        if mutation and not hmac.compare_digest(self.headers.get("X-Step-Token", ""), self.server.token):
            self.json(403, {"ok": False, "error": "Refresh the app before saving"}); return False
        return True

    def do_GET(self):
        if not self.guard(): return
        parsed = urllib.parse.urlsplit(self.path)
        if len(parsed.query) > 1500:
            self.json(400, {"ok": False, "error": "Query too long"}); return
        params = urllib.parse.parse_qs(parsed.query, max_num_fields=8)
        param = lambda key, default="": params.get(key, [default])[0]
        service = self.server.services
        try:
            if parsed.path == "/health": self.json(200, {"app": "step-study", "ready": True})
            elif parsed.path == "/api/workspace": self.json(200, service.workspace())
            elif parsed.path == "/api/terms": self.json(200, {"ok": True, "records": service.terms.lexicon()})
            elif parsed.path == "/api/term":
                q, identity = param("q"), param("id")
                if not (bool(q) ^ bool(identity)) or len(q) > 160 or len(identity) > 96: raise ValueError("Choose one short medical term")
                self.json(200, service.terms.lookup(query=q) if q else service.terms.lookup(record_id=identity))
            elif parsed.path == "/api/topic":
                topic, records, misses = service.topic_records(param("id"))
                self.json(200, {"ok": True, "topic": topic, "records": records, "misses": misses})
            elif parsed.path == "/api/recall": self.json(200, service.recall(param("topic_id")))
            elif parsed.path == "/api/card-queue": self.json(200, service.card_queue(param("topic_id")))
            elif parsed.path == "/api/questions":
                rows = service.intake.search_questions(param("q"), limit=100) if param("q") else service.intake.list_questions(status=param("status") or None, limit=100)
                topic = param("topic_id")
                if topic: rows = [row for row in rows if topic in row["topic_ids"]]
                self.json(200, {"ok": True, "questions": rows, "intake": service.intake.status()})
            elif parsed.path == "/api/question-image":
                path = service.intake.image_path(int(param("id")))
                self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png")
            elif parsed.path == "/api/export":
                self.send(200, json.dumps(service.store.export_state(), indent=2).encode(), "application/json", {"Content-Disposition": 'attachment; filename="step-study-progress.json"'})
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
        except OSError:
            self.json(503, {"ok": False, "error": "This local resource is unavailable"})

    def do_POST(self):
        if not self.guard(mutation=True): return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.json(415, {"ok": False, "error": "JSON is required"}); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100_000: raise ValueError("Request too large or empty")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError("Request must be an object")
            path = urllib.parse.urlsplit(self.path).path
            service = self.server.services
            if path == "/api/activate":
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
            elif path == "/api/anki-sync": result = service.sync_anki()
            elif path == "/api/card-start":
                tid = body.get("topic_id")
                if not isinstance(tid, str) or tid not in service.store.topic_ids or not service.store.snapshot()["topics"][tid]["active"]:
                    raise ValueError("Activate a known subject before reviewing its cards")
                result = service.cards.begin(tid, body.get("card_id"))
            elif path == "/api/card-rate":
                result = service.rate_card(body.get("topic_id"), body.get("token"), body.get("ease"), body.get("request_id"))
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
        except (OSError, TTSUnavailable):
            self.json(503, {"ok": False, "error": "The local service is unavailable"})

    def json(self, status, payload):
        self.send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")
    def send(self, status, body, mime, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data: https://upload.wikimedia.org https://mdwiki.org; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
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
