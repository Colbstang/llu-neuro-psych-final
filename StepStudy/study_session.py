"""Small private, resumable study plans. Completion never grades a study item."""
from __future__ import annotations

import json
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
    if visuals:
        add("visual", visuals[0]["id"], visuals[0].get("name", "Visual recall"))
    if lab:
        add("lab", lab["item_id"], "Predict and trace: eye movements")
    # Anki appears only when its independently queried scope reports due cards.
    if anki and isinstance(anki.get("due_count"), int) and anki["due_count"] > 0:
        add("anki", topic_id, "Review due Anki cards")
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

    def update(self, session_id, task_id=None, *, action="draft", draft=None):
        with self.lock:
            current = self.snapshot()
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
                task["status"] = "complete" if action == "complete" else "skipped"
                current["position"] = next((i for i, row in enumerate(current["tasks"])
                                             if row["status"] == "pending"), len(current["tasks"]))
                if current["position"] == len(current["tasks"]):
                    current["status"] = "finished"
            else:
                raise ValueError("Unknown session action")
            return self._write(current)
