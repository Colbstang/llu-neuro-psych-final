"""Build an evidence-labeled study queue from private user state and live signals."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("data") / "topics.json"


def _instant(value: Any, default: datetime) -> datetime:
    if not value:
        return default
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _topic_map(catalog: Any) -> list[dict[str, Any]]:
    if catalog is None:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["topics"]
    if isinstance(catalog, (str, Path)):
        data = json.loads(Path(catalog).read_text(encoding="utf-8"))
        return data.get("topics", data)
    if isinstance(catalog, dict):
        return catalog.get("topics", [])
    return list(catalog)


def _signal_map(signals: Any) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Accept either {subjects:...} or {topics:...}; absent means unknown."""
    if not isinstance(signals, dict):
        return False, {}
    values = signals.get("subjects", signals.get("topics", {}))
    return bool(signals.get("available", True)) and isinstance(values, dict), values if isinstance(values, dict) else {}


def build_review_queue(catalog: Any, state: dict[str, Any], anki_signals: dict[str, Any] | None,
                       question_signals: dict[str, Any] | None, now: datetime | str | None = None) -> list[dict[str, Any]]:
    """Return actionable records; absent signals do not turn into zeroes/mastery."""
    current = _instant(now, datetime.now(timezone.utc))
    current_s = current.isoformat(timespec="seconds").replace("+00:00", "Z")
    topics = _topic_map(catalog)
    labels = {topic["id"]: topic.get("label", topic["id"]) for topic in topics}
    states = state.get("topics", {}) if isinstance(state, dict) else {}
    anki_available, anki_topics = _signal_map(anki_signals)
    question_available, question_topics = _signal_map(question_signals)
    items: list[dict[str, Any]] = []

    for topic in topics:
        tid = topic["id"]
        topic_state = states.get(tid, {})
        if not topic_state.get("active", False):
            continue
        title = topic.get("label", tid)
        due_count = anki_topics.get(tid, {}).get("due_count") if anki_available else None
        anki_due = isinstance(due_count, int) and not isinstance(due_count, bool) and due_count > 0

        next_recall = topic_state.get("next_recall")
        if next_recall and _instant(next_recall, current) <= current:
            items.append({"id": f"recall:{tid}", "topic_id": tid, "title": title,
                          "kind": "recall", "reason": "Your concept recall is due.",
                          "due_at": next_recall, "priority": 100, "count": 1})

        if anki_available and tid in anki_topics:
            signal = anki_topics[tid]
            due_count = signal.get("due_count")
            if anki_due:
                prefix = "at least " if signal.get("scope_partial") else ""
                items.append({"id": f"anki:{tid}", "topic_id": tid, "title": title,
                              "kind": "anki", "reason": f"Anki reports {prefix}{due_count} due cards in the mapped scope; Anki remains the scheduler.",
                              "due_at": signal.get("next_due_at"), "priority": 90,
                              "count": due_count, "again_count": signal.get("again_count"),
                              "hard_count": signal.get("hard_count"),
                              "last_review_at": signal.get("last_review_at")})

        if question_available and tid in question_topics:
            signal = question_topics[tid]
            count = signal.get("unresolved_count", signal.get("wrong_count", signal.get("count")))
            if isinstance(count, int) and count > 0:
                items.append({"id": f"questions:{tid}", "topic_id": tid, "title": title,
                              "kind": "questions", "reason": f"You have {count} unresolved saved question(s).",
                              "due_at": signal.get("oldest_at"), "priority": 80,
                              "count": count})

        # A selected empty topic gets a learning prompt. Related inactive dependencies
        # are surfaced as explicit recommendations, without activating them.
        if not next_recall and not anki_due and not (question_available and question_topics.get(tid, {}).get("unresolved_count", 0) > 0):
            deps = [dep for dep in topic.get("dependencies", []) if not states.get(dep, {}).get("active", False)]
            extra = f" Related prerequisites to consider: {', '.join(labels.get(dep, dep) for dep in deps)}." if deps else ""
            items.append({"id": f"learn:{tid}", "topic_id": tid, "title": title,
                          "kind": "learn", "reason": f"Start a focused pass through {title}.{extra}",
                          "due_at": current_s, "priority": 20, "count": None,
                          "recommended_dependencies": deps})

    latest = {row["topic_id"]: row for row in state.get("attempts", []) if isinstance(row, dict) and row.get("topic_id")
              and isinstance(row.get("grade"), dict) and row["grade"].get("assessed") is True}
    for topic in topics:
        tid = topic["id"]
        if not states.get(tid, {}).get("active"):
            continue
        last = latest.get(tid, {})
        grade = last.get("grade") or {}
        score = last.get("score")
        if grade.get("assessed") is True and isinstance(score, (int, float)) and score < .6:
            current_item = next((row for row in items if row["id"] == f"recall:{tid}"), None)
            reason = "Your last explanation missed key concepts. Practice another recall."
            if current_item:
                current_item.update(reason=reason, priority=110)
            else:
                items.append({"id": f"recall:{tid}", "topic_id": tid, "title": topic.get("label", tid),
                              "kind": "recall", "reason": reason, "due_at": current_s, "priority": 110, "count": 1})
    # Link related topics only after all weak-recall items exist. Catalog order
    # must not change whether a prerequisite is recommended for review.
    for topic in topics:
        tid = topic["id"]
        if not states.get(tid, {}).get("active"):
            continue
        for dep in topic.get("dependencies", []):
            prior = latest.get(dep, {})
            prior_grade = prior.get("grade") or {}
            if states.get(dep, {}).get("active") and prior_grade.get("assessed") is True and isinstance(prior.get("score"), (int, float)) and prior["score"] < .6:
                item = next((row for row in items if row["id"] == f"recall:{dep}"), None)
                if item:
                    item["related_topic_ids"] = sorted(set(item.get("related_topic_ids", []) + [tid]))

    for item in items:
        if item.get("related_topic_ids"):
            related = ", ".join(labels.get(tid, tid) for tid in item["related_topic_ids"])
            item["reason"] += f" Revisit this foundation for {related}."

    return sorted(items, key=lambda item: (-item["priority"], item.get("due_at") or "", item["id"]))
