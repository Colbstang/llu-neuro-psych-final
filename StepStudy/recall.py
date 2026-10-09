"""Source-grounded, open-ended concept recall prompts for StepStudy."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable


MAX_REFERENCE_CHARS = 1_200
MAX_SOURCE_RECORDS = 20
_MECHANISM = re.compile(r"\b(?:underlying mechanism|mechanism involves|mechanism is|pathophysiology)\b", re.I)


def _clean(value: Any, limit: int = MAX_REFERENCE_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _source(record: dict[str, Any]) -> dict[str, str] | None:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    title = _clean(record.get("title"), 160)
    record_id = _clean(record.get("id"), 96)
    if not title or not record_id:
        return None
    row = {"id": record_id, "title": title,
           "name": _clean(source.get("name") or "MDWiki", 80)}
    url = _clean(source.get("url"), 300)
    if url.startswith("https://mdwiki.org/"):
        row["url"] = url
    return row


def _reference_text(record: dict[str, Any]) -> str:
    # Prefer the source lead definition. Longer summary text is used only to
    # supply one directly stated mechanism when the lead contains none.
    definition = _clean(record.get("definition"))
    summary = _clean(record.get("summary"), 5_000)
    if not definition:
        definition = summary[:MAX_REFERENCE_CHARS]
    if not definition:
        return ""
    if _MECHANISM.search(definition) or not summary:
        return definition[:MAX_REFERENCE_CHARS]
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    mechanism = next((sentence for sentence in sentences if _MECHANISM.search(sentence)), "")
    combined = definition + (" " + mechanism if mechanism and mechanism not in definition else "")
    return combined[:MAX_REFERENCE_CHARS]


def _prompt_for(topic_id: str, topic_label: str, title: str, reference: str) -> str:
    if _MECHANISM.search(reference):
        lead = {
            "renal": "Explain the mechanism described for",
            "immunology": "Explain the immune mechanism described for",
            "neuro": "Explain the mechanism described for",
            "psychiatry": "Explain the central process described for",
        }.get(topic_id, "Explain the mechanism or central process described for")
        return f"{lead} {title}. Connect that process to the defining feature or consequence stated in the source."
    lead = {
        "renal": "Explain the renal concept",
        "immunology": "Explain the immune concept",
        "neuro": "Explain the neurologic concept",
        "psychiatry": "Explain the psychiatric concept",
    }.get(topic_id, f"Explain the {topic_label.lower()} concept")
    return f"{lead} {title} in your own words. Include its defining feature and any additional consequence or relationship stated in the source."


def build_recall(topic_id: str, records: Iterable[dict[str, Any]],
                 previous_attempts: Iterable[dict[str, Any]] = (),
                 topic_label: str | None = None) -> dict[str, Any] | None:
    """Build one original open-ended prompt using only supplied source records.

    Past responses are used only to rotate among source records; their contents
    never become grading context. No prompt is fabricated when sources are absent.
    """
    usable: list[tuple[dict[str, Any], dict[str, str], str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        citation = _source(record)
        reference = _reference_text(record)
        if citation and reference:
            usable.append((record, citation, reference))
        if len(usable) >= MAX_SOURCE_RECORDS:
            break
    if not usable:
        return None

    attempts = list(previous_attempts)[-100:]
    last_used: dict[str, int] = {}
    for index, item in enumerate(attempts):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id", ""))
        old_prompt = str(item.get("prompt", "")).casefold()
        if source_id:
            last_used[source_id] = index
        for _, citation, _ in usable:
            title = citation["title"].casefold()
            if title and title in old_prompt:
                last_used[citation["id"]] = index
    record, citation, reference = min(
        usable, key=lambda row: last_used.get(row[1]["id"], -1))
    title = _clean(record.get("title"), 160)
    label = _clean(topic_label or topic_id.replace("-", " ").title(), 100)
    prompt = _prompt_for(topic_id, label, title, reference)
    sentences = re.split(r"(?<=[.!?])\s+", reference)
    expected_points = [sentence.strip() for sentence in sentences if sentence.strip()][:4]
    identity = hashlib.sha256((topic_id + "\0" + citation["id"] + "\0" + prompt).encode()).hexdigest()[:16]
    return {
        "id": f"recall:{topic_id}:{identity}", "topic_id": topic_id,
        "topic": label, "prompt": prompt,
        "expected_points": expected_points,
        "sources": [citation],
        "reference_context": reference,
    }
