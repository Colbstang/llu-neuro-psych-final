"""Small private, resumable study plans. Completion never grades a study item."""
from __future__ import annotations

import json
import math
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

KINDS = {"concept", "repair", "visual", "anki", "lab"}


def make_plan(concepts, repairs, visuals, anki, *, minutes=15, topic_id="", lab=None):
    if isinstance(minutes, bool) or minutes not in {15, 30, 45}:
        raise ValueError("Choose a 15, 30, or 45 minute session")
    tasks = []
    def add(kind, identity, title, **extra):
        tasks.append({"id": secrets.token_urlsafe(12), "kind": kind,
                      "ref": str(identity), "title": str(title)[:240],
                      "status": "pending", "draft": "", **extra})
    # Alternate recall and repair rather than placing every source on screen.
    budget = {15: 3, 30: 5, 45: 7}[minutes]
    for item in list(concepts)[:1]:
        add("concept", item["item_id"], item.get("title", "Concept recall"))
    if repairs:
        add("repair", repairs[0]["id"], "Repair a saved question")
    # Reserve one of a small session's steps for reported due Anki cards.
    # Otherwise the 15-minute bound could silently drop Anki behind visuals/labs.
    if (anki and isinstance(anki.get("due_count"), int)
            and not isinstance(anki["due_count"], bool) and anki["due_count"] > 0):
        add("anki", topic_id, "Review due Anki cards")
    if visuals:
        add("visual", visuals[0]["id"], visuals[0].get("name", "Visual recall"))
    if lab:
        add("lab", lab["item_id"], "Predict and trace: eye movements")
    for item in list(concepts)[1:budget]:
        add("concept", item["item_id"], item.get("title", "Concept recall"))
    return {"id": secrets.token_urlsafe(18), "minutes": minutes,
            "topic_id": topic_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "tasks": tasks[:budget], "position": 0, "status": "active"}


class StudySessions:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text())
                return value if isinstance(value, dict) and isinstance(value.get("tasks"), list) else None
            except (OSError, ValueError):
                return None

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_suffix(".tmp")
        pending.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        pending.chmod(0o600)
        pending.replace(self.path)
        return value

    def start(self, plan):
        with self.lock:
            current = self.snapshot()
            if current and current["status"] == "active":
                raise ValueError("Resume or finish your current session before starting another")
            if not plan["tasks"]:
                raise ValueError("No practice items are available for this subject yet")
            return self._write(plan)

    def complete_anki(self, session_id, task_id, accepted_review):
        """Only the server's bridge confirmation can finish an Anki step."""
        with self.lock:
            current = self.snapshot()
            if not current or current["id"] != session_id:
                raise ValueError("This session changed. Reopen Study session")
            task = next((row for row in current["tasks"] if row["id"] == task_id), None)
            if not task or task["kind"] != "anki":
                raise ValueError("Unknown Anki session step")
            accepted = accepted_review if isinstance(accepted_review, dict) else {}
            if (accepted.get("confirmed") is not True or accepted.get("topic_id") != task["ref"]
                    or not isinstance(accepted.get("request_id"), str) or not accepted["request_id"]
                    or isinstance(accepted.get("review_id"), bool)
                    or not isinstance(accepted.get("review_id"), int) or accepted["review_id"] <= 0):
                raise ValueError("Complete an explicit Anki review before continuing")
            if task.get("anki_request_id") == accepted["request_id"] and task["status"] == "complete":
                return current
            started_at = datetime.fromisoformat(current["created_at"]).timestamp()
            submitted_at = accepted.get("submitted_at")
            if (isinstance(submitted_at, bool) or not isinstance(submitted_at, (int, float))
                    or not math.isfinite(submitted_at) or submitted_at < started_at):
                raise ValueError("Review a card during this study session before continuing")
            if current["status"] != "active":
                raise ValueError("This session has finished")
            task["anki_request_id"] = accepted["request_id"]
            task["anki_review_id"] = accepted["review_id"]
            return self.update(session_id, task_id, action="complete", _accepted_anki=True, _current=current)

    def update(self, session_id, task_id=None, *, action="draft", draft=None,
               _accepted_anki=False, _current=None):
        with self.lock:
            current = _current or self.snapshot()
            if not current or current["id"] != session_id:
                raise ValueError("This session changed. Reopen Study session")
            if action == "finish":
                current["status"] = "finished"
                return self._write(current)
            if current["status"] != "active":
                raise ValueError("This session has finished")
            task = next((row for row in current["tasks"] if row["id"] == task_id), None)
            if not task:
                raise ValueError("Unknown session step")
            if action == "draft":
                if not isinstance(draft, str) or len(draft) > 2000:
                    raise ValueError("Keep your draft under 2,000 characters")
                task["draft"] = draft
            elif action in {"complete", "skip"}:
                if action == "complete" and task["kind"] == "anki" and not _accepted_anki:
                    raise ValueError("Complete an explicit Anki review before continuing")
                task["status"] = "complete" if action == "complete" else "skipped"
                current["position"] = next((i for i, row in enumerate(current["tasks"])
                                             if row["status"] == "pending"), len(current["tasks"]))
                if current["position"] == len(current["tasks"]):
                    current["status"] = "finished"
            else:
                raise ValueError("Unknown session action")
            return self._write(current)
