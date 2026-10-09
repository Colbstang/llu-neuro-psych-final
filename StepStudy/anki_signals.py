"""Read-only, bounded AnkiConnect signals for the Step study planner."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

CATALOG_PATH = Path(__file__).with_name("data") / "topics.json"
MAX_SUBJECTS = 18
MAX_CATEGORY_PREFIXES = 12
MAX_BACKGROUND_CATEGORY_PREFIXES = 2
MAX_SEARCH_QUERY_CHARS = 12000
MAX_TOTAL_CARDS = 5000
MAX_HISTORY_SAMPLE = 200
HISTORY_BATCH_SIZE = 50


def anki_connect(action: str, params: dict[str, Any] | None = None,
                 url: str = "http://127.0.0.1:8765", timeout: float = 4.0) -> Any:
    """Call AnkiConnect over loopback. No write actions are exposed by this module."""
    body = json.dumps({"action": action, "version": 6, "params": params or {}}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload.get("result")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _parts(tag: str) -> list[str]:
    parts = []
    for raw in re.split(r"::|[|]", tag):
        # First Aid category leaves often carry an ordering prefix (02_Neurology).
        raw = re.sub(r"^\s*#?\s*\d+[_ .-]*", "", raw)
        raw = raw.lstrip("#").strip()
        parts.append(_norm(raw))
    return parts


def _scoped_tag(tag: str) -> bool:
    """Require an explicit known Step/AnKing/First Aid namespace in the tag path."""
    parts = _parts(tag)
    return any(part.startswith(("ak step1", "ak step2", "ak step 1", "ak step 2", "anking", "firstaid", "first aid", "step1", "step2", "step 1", "step 2"))
               for part in parts)


def _scoped_deck(name: str) -> bool:
    value = _norm(name)
    return "anking" in value or re.search(r"\bstep ?[12]\b", value) is not None


# Only known category leaves map to the topic catalog. Matching a generic disease
# word such as "heartburn" against "heart" would create misleading coverage.
_CATEGORY_ALIASES = {
    "general principles": "general-principles", "public health": "public-health",
    "biochemistry": "biochemistry", "biochem": "biochemistry",
    "immunology": "immunology", "microbiology": "microbiology", "micro": "microbiology",
    "pathology": "pathology", "pharmacology": "pharmacology", "pharm": "pharmacology",
    "cardio": "cardiovascular", "cardiovascular": "cardiovascular",
    "endocrine": "endocrine", "endocrinology": "endocrine",
    "gastrointestinal": "gastrointestinal", "gi": "gastrointestinal", "gastroenterology": "gastrointestinal",
    "hemeonc": "hematology-oncology", "heme onc": "hematology-oncology", "hematology oncology": "hematology-oncology",
    "hematology and oncology": "hematology-oncology",
    "msk": "musculoskeletal-skin", "msk skin": "musculoskeletal-skin", "musculoskeletal": "musculoskeletal-skin",
    "msk skin connective tissue": "musculoskeletal-skin", "musculoskeletal skin connective tissue": "musculoskeletal-skin",
    "skin": "musculoskeletal-skin", "connective tissue": "musculoskeletal-skin",
    "neuro": "neuro", "neurology": "neuro",
    "psych": "psychiatry", "psychiatry": "psychiatry",
    "renal": "renal", "nephrology": "renal",
    "reproductive": "reproductive", "reproductive endocrinology": "reproductive",
    "respiratory": "respiratory", "pulmonary": "respiratory",
}


def _subject_for_tag(tag: str, known_topic_ids: set[str]) -> str | None:
    if not _scoped_tag(tag):
        return None
    for part in reversed(_parts(tag)):
        topic_id = _CATEGORY_ALIASES.get(part)
        if topic_id in known_topic_ids:
            return topic_id
    return None


def _category_prefix(tag: str, topic_id: str) -> str | None:
    raw_parts = re.split(r"::|[|]", tag)
    for index, raw in enumerate(raw_parts):
        candidate = re.sub(r"^\s*#?\s*\d+[_ .-]*", "", raw).lstrip("#").strip()
        if _CATEGORY_ALIASES.get(_norm(candidate)) == topic_id:
            return "::".join(raw_parts[:index + 1])
    return None


def _quote_search(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _review_fields(row: Any) -> tuple[int, int, int] | None:
    """Return (timestamp_ms, ease, type) from current Anki dict rows or legacy tuples."""
    try:
        if isinstance(row, dict):
            values = row["id"], row["ease"], row["type"]
        elif isinstance(row, (list, tuple)) and len(row) >= 7:
            values = row[0], row[1], row[6]
        else:
            return None
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            return None
        timestamp, ease, kind = values
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return timestamp, ease, kind


class AnkiSignals:
    """Collect aggregate counts and actual rating history; never returns card text/media."""

    def __init__(self, transport: Callable[[str, dict[str, Any]], Any] | None = None,
                 catalog_path: str | Path | None = None, max_cards: int = MAX_TOTAL_CARDS,
                 batch_size: int = 200, recent_days: int = 30,
                 clock: Callable[[], datetime] | None = None):
        self.transport = transport or (lambda action, params: anki_connect(action, params))
        self.live_transport = transport is None
        self._deadline = 0.0
        data = json.loads(Path(catalog_path or CATALOG_PATH).read_text(encoding="utf-8"))
        self.catalog = data["topics"]
        self.max_cards = max(1, min(int(max_cards), MAX_TOTAL_CARDS))
        self.batch_size = max(1, min(int(batch_size), 200))
        self.recent_days = 30
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _call(self, action: str, **params: Any) -> Any:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Anki signal time budget exhausted")
        if self.live_transport:
            return anki_connect(action, params, timeout=max(0.1, min(3.0, remaining)))
        return self.transport(action, params)

    def sync(self, topic_ids: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        synced_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        self._deadline = time.monotonic() + 40.0
        version_ok = False
        partial = False
        reason = None
        subjects: dict[str, dict[str, Any]] = {}
        scoped_decks: list[str] = []
        completed_topics: set[str] = set()
        topic_card_ids: dict[str, list[int]] = {}
        topic_due_ids: dict[str, set[int]] = {}
        topic_queries: dict[str, str] = {}
        known_ids = {topic["id"] for topic in self.catalog}
        requested = [topic_id for topic_id in (topic_ids or []) if topic_id in known_ids]
        if not requested:
            requested = [topic["id"] for topic in self.catalog]
            requested = [topic for topic in ("neuro", "psychiatry") if topic in known_ids] + [
                topic for topic in requested if topic not in {"neuro", "psychiatry"}]
        for topic_id in requested:
            subjects[topic_id] = {
                "card_count": None, "due_count": None, "total_card_count": None,
                "due_count_total": None, "sampled_card_count": 0,
                "review_count": None, "again_count": None, "hard_count": None,
                "review_history_known": False, "scope_known": False,
            }
        try:
            self._call("version")
            version_ok = True
            tags = self._call("getTags") or []
            decks = self._call("deckNames") or []
            scoped_decks_all = sorted({deck for deck in decks if isinstance(deck, str) and _scoped_deck(deck)})
            scoped_decks = [deck for deck in scoped_decks_all
                            if not any(deck.startswith(parent + "::") for parent in scoped_decks_all if parent != deck)]
            if not scoped_decks:
                partial = True
                reason = "No clearly Step or AnKing deck is available to map."
                return {"available": True, "synced_at": synced_at, "subjects": subjects,
                        "partial": partial, "scope": {"query_count": 0, "completed_subject_count": 0,
                        "partial": partial}, "reason": reason}

            topic_prefixes: dict[str, set[str]] = {}
            prefix_frequency: dict[tuple[str, str], int] = {}
            for tag in tags:
                if not isinstance(tag, str):
                    continue
                topic_id = _subject_for_tag(tag, known_ids)
                if topic_id:
                    prefix = _category_prefix(tag, topic_id)
                    if prefix:
                        topic_prefixes.setdefault(topic_id, set()).add(prefix)
                        key = (topic_id, prefix)
                        prefix_frequency[key] = prefix_frequency.get(key, 0) + 1
            partial = False
            prefix_truncated: set[str] = set()
            topic_prefixes = {topic: topic_prefixes[topic] for topic in requested if topic in topic_prefixes}
            if len(topic_prefixes) > MAX_SUBJECTS:
                partial = True
            topic_prefixes = dict(list(topic_prefixes.items())[:MAX_SUBJECTS])
            for topic_id, prefixes in list(topic_prefixes.items()):
                prefix_limit = MAX_CATEGORY_PREFIXES if topic_id in requested[:2] else MAX_BACKGROUND_CATEGORY_PREFIXES
                if len(prefixes) > prefix_limit:
                    partial = True
                    prefix_truncated.add(topic_id)
                    ranked = sorted(prefixes, key=lambda prefix: (-prefix_frequency.get((topic_id, prefix), 0), prefix))
                    topic_prefixes[topic_id] = set(ranked[:prefix_limit])
            deck_query = "(" + " OR ".join(f"deck:{_quote_search(deck)}" for deck in scoped_decks) + ")"
            for topic_id, prefixes in topic_prefixes.items():
                # A category prefix query includes its own tag and descendants without
                # enumerating every child tag returned by getTags.
                alternatives = []
                for prefix in sorted(prefixes):
                    quoted = _quote_search(prefix)
                    alternatives.extend((f"tag:{quoted}", f"tag:{_quote_search(prefix + '::*')}"))
                tag_query = "(" + " OR ".join(alternatives) + ")"
                base_query = f"{deck_query} {tag_query} -is:suspended"
                if len(base_query) > MAX_SEARCH_QUERY_CHARS:
                    raise ValueError("bounded Anki search query exceeded configured size")
                try:
                    raw_ids = self._call("findCards", query=base_query) or []
                    ids = sorted({value for value in raw_ids if isinstance(value, int) and not isinstance(value, bool) and value > 0})
                    if len(ids) > self.max_cards:
                        partial = True
                    sampled_ids = ids[:self.max_cards]
                    topic_card_ids[topic_id] = sampled_ids
                    topic_queries[topic_id] = tag_query
                    subjects[topic_id].update({"card_count": len(ids), "total_card_count": len(ids),
                                               "scope_known": True,
                                               "scope_partial": topic_id in prefix_truncated or len(ids) > self.max_cards,
                                               "scope_prefix_count": len(prefixes)})
                    completed_topics.add(topic_id)
                except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
                    partial = True
                    reason = f"Anki sync stopped during topic scope queries: {type(exc).__name__}"
                    break

            if completed_topics:
                due_query = f"{deck_query} (" + " OR ".join(
                    f"{topic_queries[topic]}" for topic in topic_queries) + ") -is:suspended is:due"
                if len(due_query) <= 48000:
                    try:
                        due_raw = self._call("findCards", query=due_query) or []
                        due_union = {value for value in due_raw if isinstance(value, int) and not isinstance(value, bool) and value > 0}
                        for topic_id in completed_topics:
                            ids = topic_card_ids.get(topic_id, [])
                            total = subjects[topic_id]["total_card_count"]
                            if isinstance(total, int) and total <= len(ids):
                                due = due_union & set(ids)
                                topic_due_ids[topic_id] = due
                                subjects[topic_id].update({"due_count": len(due), "due_count_total": len(due)})
                    except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
                        partial = True
                        reason = reason or f"Anki sync stopped during due-count query: {type(exc).__name__}"
                else:
                    partial = True
                    reason = reason or "Due-count query exceeded the configured size bound."

            # Round-robin samples avoid allowing a large first topic to consume the budget.
            sample_ids: dict[str, list[int]] = {topic: [] for topic in completed_topics}
            assigned: set[int] = set()
            offsets = {topic: 0 for topic in completed_topics}
            priority_topics = [topic for topic in requested[:2] if topic in completed_topics]
            quota = max(1, MAX_HISTORY_SAMPLE // max(1, len(priority_topics)))
            for topic_id in priority_topics:
                for cid in topic_card_ids.get(topic_id, [])[:quota]:
                    sample_ids.setdefault(topic_id, []).append(cid)
                    assigned.add(cid)
            rest_topics = [topic for topic in topic_prefixes if topic not in priority_topics]
            while len(assigned) < MAX_HISTORY_SAMPLE:
                added = False
                for topic_id in rest_topics:
                    cards = topic_card_ids.get(topic_id, [])
                    index = offsets.get(topic_id, 0)
                    if index < len(cards):
                        cid = cards[index]
                        offsets[topic_id] = index + 1
                        sample_ids.setdefault(topic_id, []).append(cid)
                        if cid not in assigned:
                            assigned.add(cid)
                        added = True
                        if len(assigned) >= MAX_HISTORY_SAMPLE:
                            break
                if not added:
                    break
            # Use leftover sample capacity on the prioritized subjects first.
            for topic_id in priority_topics:
                if len(assigned) >= MAX_HISTORY_SAMPLE:
                    break
                start = len(sample_ids.get(topic_id, []))
                for cid in topic_card_ids.get(topic_id, [])[start:]:
                    sample_ids.setdefault(topic_id, []).append(cid)
                    assigned.add(cid)
                    if len(assigned) >= MAX_HISTORY_SAMPLE:
                        break
            if any(len(topic_card_ids.get(tid, [])) > len(sample_ids.get(tid, [])) for tid in topic_card_ids):
                partial = True

            card_info: dict[int, dict[str, Any]] = {}
            excluded_ids: set[int] = set()
            for offset in range(0, len(assigned), HISTORY_BATCH_SIZE):
                try:
                    rows = self._call("cardsInfo", cards=sorted(assigned)[offset:offset + HISTORY_BATCH_SIZE]) or []
                except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
                    partial = True
                    reason = reason or f"Anki sync stopped while sampling cards: {type(exc).__name__}"
                    break
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("cardId"), int) or isinstance(row.get("cardId"), bool):
                        continue
                    cid = row["cardId"]
                    try:
                        queue = int(row["queue"]) if row.get("queue") is not None else None
                    except (TypeError, ValueError):
                        continue
                    if queue is not None and queue < 0:
                        excluded_ids.add(cid)
                    else:
                        card_info[cid] = row

            review_ids = sorted(set(assigned) & set(card_info))
            reviews: dict[int, list[Any]] = {}
            history_known: set[int] = set()
            threshold_ms = int((now - timedelta(days=30)).timestamp() * 1000)
            end_ms = int(now.timestamp() * 1000)
            for offset in range(0, len(review_ids), HISTORY_BATCH_SIZE):
                batch = review_ids[offset:offset + HISTORY_BATCH_SIZE]
                try:
                    rows = self._call("getReviewsOfCards", cards=batch) or {}
                except (TimeoutError, urllib.error.URLError, OSError, RuntimeError) as exc:
                    partial = True
                    reason = reason or f"Anki sync stopped while reading sampled history: {type(exc).__name__}"
                    break
                if isinstance(rows, dict):
                    for cid in batch:
                        key = str(cid) if str(cid) in rows else cid
                        if key in rows and isinstance(rows[key], list):
                            history_known.add(cid)
                            reviews[cid] = rows[key]

            for topic_id in completed_topics:
                ids = topic_card_ids.get(topic_id, [])
                samples = sample_ids.get(topic_id, [])
                verified_sample_ids = set(samples) & set(card_info)
                history_sample_ids = verified_sample_ids & history_known
                review_count = again_count = hard_count = 0
                last_review: int | None = None
                for cid in history_sample_ids:
                    for raw_review in reviews.get(cid, []):
                        fields = _review_fields(raw_review)
                        if fields is None:
                            continue
                        timestamp, ease, kind = fields
                        if timestamp < threshold_ms or timestamp > end_ms or kind not in (0, 1, 2) or ease not in (1, 2, 3, 4):
                            continue
                        review_count += 1
                        last_review = max(last_review or timestamp, timestamp)
                        again_count += int(ease == 1)
                        hard_count += int(ease == 2)
                all_scoped_ids = topic_card_ids.get(topic_id, [])
                population_sampled = subjects[topic_id].get("total_card_count") == len(samples)
                all_sample_history_known = (history_sample_ids >= verified_sample_ids and
                                            len(verified_sample_ids) == len(all_scoped_ids))
                review_sample_observed = bool(history_sample_ids) or subjects[topic_id].get("total_card_count") == 0
                subjects[topic_id].update({
                    "sampled_card_count": len(verified_sample_ids),
                    "sampled_due_count": (len(set(samples) & topic_due_ids[topic_id])
                                          if topic_id in topic_due_ids else None),
                    "missing_card_info_count": len(set(samples) - set(card_info) - excluded_ids),
                    "review_sample_size": len(history_sample_ids),
                    "review_scope": "sample",
                    "review_count": review_count if review_sample_observed else None,
                    "again_count": again_count if review_sample_observed else None,
                    "hard_count": hard_count if review_sample_observed else None,
                    "review_count_sampled": review_count if review_sample_observed else None,
                    "again_count_sampled": again_count if review_sample_observed else None,
                    "hard_count_sampled": hard_count if review_sample_observed else None,
                    "reviewed_card_count": None,
                    "unknown_history_count": len(verified_sample_ids - history_known),
                    "review_history_known": bool(population_sampled and all_sample_history_known),
                    "last_review_at": datetime.fromtimestamp(last_review / 1000, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if last_review else None,
                })
            if len(completed_topics) < len(topic_prefixes) or len(completed_topics) < len(requested):
                partial = True
            return {"available": True, "synced_at": synced_at, "subjects": subjects,
                    "partial": partial,
                    "scope": {"decks": scoped_decks, "query_count": len(topic_prefixes),
                              "completed_subject_count": len(completed_topics),
                              "partial": partial, "history_sample_limit": MAX_HISTORY_SAMPLE},
                    "reason": reason or ("Results are partial because a topic, category-prefix, card, or history-sample bound was reached." if partial else None)}
        except (OSError, TimeoutError, urllib.error.URLError, RuntimeError, ValueError, TypeError) as exc:
            if version_ok:
                partial = True
                reason = reason or f"Anki sync stopped after connecting: {type(exc).__name__}"
                return {"available": True, "synced_at": synced_at, "subjects": subjects,
                        "partial": True, "scope": {"decks": scoped_decks,
                        "query_count": len(topic_card_ids), "completed_subject_count": len(completed_topics),
                        "partial": True, "history_sample_limit": MAX_HISTORY_SAMPLE}, "reason": reason}
            return {"available": False, "synced_at": synced_at, "subjects": {},
                    "partial": False,
                    "scope": {"query_count": 0, "partial": False},
                    "reason": f"Anki signal sync unavailable: {type(exc).__name__}"}
