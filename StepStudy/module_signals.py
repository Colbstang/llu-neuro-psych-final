"""Read-only aggregate signals from the local Neuro/Psych study module.

Only stable IDs and course metadata are retained from the guide HTML. Question
text, objective text, notes, and authentication material never leave this module.
"""

from __future__ import annotations

import json
import re
import threading
import time
import codecs
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODULE_URL = "http://127.0.0.1:8770/"
PROGRESS_URL = "http://127.0.0.1:8770/api/progress"
SOURCE = "Neuro/Psych module"
MAX_HTML_BYTES = 40_000_000
MAX_ASSIGNMENT_SEARCH_BYTES = 256_000
REQUEST_TIMEOUT_SECONDS = 2.0
_DATA_ASSIGNMENT = re.compile(r"const\s+DATA\s*=")
_REQUIRED_DATA_KEYS = {
    "pages", "topic_groups", "objectives", "questions", "question_auto_links",
    "question_link_corrections", "question_annotations",
}
_CACHE_LOCK = threading.Lock()
_METADATA_CACHE: dict[str, Any] | None = None


def _get(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> bytes:
    """GET a fixed loopback URL with a strict response-size bound."""
    if url not in (MODULE_URL, PROGRESS_URL):
        raise ValueError("URL is outside the local module allowlist")
    request = Request(url, headers={"Accept": "text/html,application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_HTML_BYTES if url == MODULE_URL else 2_000_000)
        if url == MODULE_URL and len(body) == MAX_HTML_BYTES:
            raise ValueError("module HTML exceeds the 40 MB response limit")
        if url == PROGRESS_URL and len(body) == 2_000_000:
            raise ValueError("progress response exceeds the size limit")
        return body


def _extract_data(html: bytes | str) -> dict[str, Any]:
    """Decode the DATA JSON assignment without evaluating or rendering HTML."""
    if isinstance(html, bytes):
        if len(html) > MAX_HTML_BYTES:
            raise ValueError("module HTML exceeds the 40 MB response limit")
        html = html.decode("utf-8")
    match = _DATA_ASSIGNMENT.search(html)
    if not match:
        raise ValueError("module DATA assignment was not found within the response limit")
    value, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
    if not isinstance(value, dict):
        raise ValueError("module DATA assignment is not a JSON object")
    return value


class _StreamingJSONReader:
    """Incrementally decode JSON values from a bounded byte stream."""

    def __init__(self, stream: Any, *, max_bytes: int, chunk_size: int = 262_144,
                 deadline: float | None = None):
        self.stream = stream
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size
        self.deadline = deadline
        self.byte_count = 0
        self.buffer_byte_start = 0
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.json_decoder = json.JSONDecoder()

    def _read_more(self) -> None:
        if self.eof:
            return
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TimeoutError("module metadata read exceeded the 6 second budget")
        chunk = self.stream.read(self.chunk_size)
        if not chunk:
            self.buffer += self.decoder.decode(b"", final=True)
            self.eof = True
            return
        self.byte_count += len(chunk)
        if self.byte_count > self.max_bytes:
            raise ValueError("module metadata exceeds the 40 MB response limit")
        self.buffer += self.decoder.decode(chunk, final=False)

    def _available(self) -> bool:
        if self.position < len(self.buffer):
            return True
        if self.eof:
            return False
        self.buffer = ""
        self.position = 0
        self._read_more()
        return self.position < len(self.buffer)

    def skip_space(self) -> None:
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer) or self.eof:
                return
            self._read_more()

    def peek(self) -> str:
        self.skip_space()
        if self.position >= len(self.buffer):
            raise ValueError("incomplete module DATA JSON")
        return self.buffer[self.position]

    def parse_value(self) -> Any:
        while True:
            self.skip_space()
            if self.position >= len(self.buffer):
                raise ValueError("incomplete module DATA JSON")
            try:
                value, end = self.json_decoder.raw_decode(self.buffer, self.position)
                self.position = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("incomplete or invalid module DATA JSON") from None
                self._read_more()

    def parse_data_assignment(self, *, search_limit: int) -> dict[str, Any]:
        match = None
        while match is None:
            match = _DATA_ASSIGNMENT.search(self.buffer)
            if match:
                self.position = match.end()
                match_byte_offset = self.buffer_byte_start + len(self.buffer[:match.start()].encode("utf-8"))
                if match_byte_offset > search_limit:
                    raise ValueError("module DATA assignment starts beyond the 256 KB search limit")
                break
            if self.eof or self.byte_count >= search_limit:
                raise ValueError("module DATA assignment was not found within the 256 KB search limit")
            # Keep enough overlap to detect an assignment split across chunks.
            keep = max(0, len(self.buffer) - len("const DATA = ") - 16)
            if keep:
                self.buffer_byte_start += len(self.buffer[:keep].encode("utf-8"))
                self.buffer = self.buffer[keep:]
                self.position = max(0, self.position - keep)
            self._read_more()

        if self.peek() != "{":
            raise ValueError("module DATA assignment is not a JSON object")
        self.position += 1
        selected: dict[str, Any] = {}
        while len(selected.keys() & _REQUIRED_DATA_KEYS) < len(_REQUIRED_DATA_KEYS):
            delimiter = self.peek()
            if delimiter == "}":
                raise ValueError("module DATA is missing required metadata")
            if delimiter == ",":
                self.position += 1
                self.skip_space()
            key = self.parse_value()
            if not isinstance(key, str):
                raise ValueError("module DATA contains a non-string key")
            self.skip_space()
            if self.peek() != ":":
                raise ValueError("invalid module DATA object")
            self.position += 1
            value = self.parse_value()
            if key in _REQUIRED_DATA_KEYS:
                selected[key] = value
        return selected


def _extract_metadata_stream(stream: Any, *, max_bytes: int = MAX_HTML_BYTES,
                             search_limit: int = MAX_ASSIGNMENT_SEARCH_BYTES,
                             chunk_size: int = 262_144, deadline: float | None = None) -> dict[str, Any]:
    """Read DATA's required JSON values and stop before trailing assets."""
    reader = _StreamingJSONReader(stream, max_bytes=max_bytes, chunk_size=chunk_size, deadline=deadline)
    return reader.parse_data_assignment(search_limit=search_limit)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _section_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only stable IDs and labels needed to classify the course sections."""
    pages: list[dict[str, Any]] = []
    for page in data.get("pages", []) if isinstance(data.get("pages"), list) else []:
        if not isinstance(page, dict):
            continue
        sections = []
        for block in page.get("blocks", []) if isinstance(page.get("blocks"), list) else []:
            if not isinstance(block, dict) or not isinstance(block.get("id"), str):
                continue
            sections.append({"id": block["id"], "title": _text(block.get("title"))})
        pages.append({
            "id": _text(page.get("id")),
            "title": _text(page.get("title")),
            "keywords": [_text(item) for item in page.get("keywords", []) if isinstance(item, str)],
            "sections": sections,
        })
    groups = []
    topic_groups = data.get("topic_groups")
    for group in topic_groups.get("groups", []) if isinstance(topic_groups, dict) else []:
        if not isinstance(group, dict):
            continue
        groups.append({
            "id": _text(group.get("id")), "title": _text(group.get("title")),
            "short_title": _text(group.get("short_title")), "page_ids": _ids(group.get("page_ids")),
        })
    objectives = []
    for lo in data.get("objectives", []) if isinstance(data.get("objectives"), list) else []:
        if isinstance(lo, dict) and isinstance(lo.get("id"), str):
            objectives.append({"id": lo["id"], "sections": _ids(lo.get("sections"))})
    question_links: dict[str, set[str]] = defaultdict(set)
    for question in data.get("questions", []) if isinstance(data.get("questions"), list) else []:
        if isinstance(question, dict) and isinstance(question.get("id"), str):
            question_links[question["id"]].update(_ids(question.get("suggested_sections")))
    for link in data.get("question_auto_links", []) if isinstance(data.get("question_auto_links"), list) else []:
        if isinstance(link, dict) and isinstance(link.get("question_id"), str):
            question_links[link["question_id"]].update(_ids(link.get("sections")))
    for annotation in data.get("question_annotations", []) if isinstance(data.get("question_annotations"), list) else []:
        if isinstance(annotation, dict) and isinstance(annotation.get("question_id"), str):
            block_id = annotation.get("block_id")
            if isinstance(block_id, str) and block_id:
                question_links[annotation["question_id"]].add(block_id)
    # Corrections replace the old stable section link after all source links
    # have been combined, including duplicated suggested/automatic links.
    for correction in data.get("question_link_corrections", []) if isinstance(data.get("question_link_corrections"), list) else []:
        if not isinstance(correction, dict) or not isinstance(correction.get("question_id"), str):
            continue
        sections = question_links[correction["question_id"]]
        old_section, new_section = correction.get("old_section"), correction.get("new_section")
        if isinstance(old_section, str) and old_section:
            sections.discard(old_section)
        if isinstance(new_section, str) and new_section:
            sections.add(new_section)
    questions = []
    for question in data.get("questions", []) if isinstance(data.get("questions"), list) else []:
        if isinstance(question, dict) and isinstance(question.get("id"), str):
            questions.append({
                "id": question["id"], "source_status": _text(question.get("source_status")),
                "sections": sorted(question_links.get(question["id"], set())),
            })
    return {"pages": pages, "groups": groups, "objectives": objectives, "questions": questions}


_PSYCH = re.compile(r"\b(?:psychiatr\w*|psych\w*|mental\s+health|mood|anxiety|substance|personality|somatic|eating\s+disorder|childhood\s+disorder)\b", re.I)
_NEURO = re.compile(r"\b(?:neurolog\w*|neuro\w*|brain|cerebell\w*|spinal\s+cord|seizure|epilep\w*|stroke|vascular|neuroanatom\w*|vision|hearing|movement|cognition|dementia|myelin)\b", re.I)


def _branches(label: str) -> set[str]:
    if re.search(r"neuropsych", label, re.I):
        return {"neuro", "psychiatry"}
    psych = bool(_PSYCH.search(label))
    neuro = bool(_NEURO.search(label))
    if psych and neuro:
        return {"neuro", "psychiatry"}
    if psych:
        return {"psychiatry"}
    if neuro:
        return {"neuro"}
    return set()


def _mapping(metadata: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Map stable section IDs to course branches; leave unclear labels unmapped."""
    pages_by_id = {p["id"]: p for p in metadata.get("pages", []) if p.get("id")}
    group_by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in metadata.get("groups", []):
        for page_id in group.get("page_ids", []):
            group_by_page[page_id].append(group)
    branches_by_section: dict[str, set[str]] = defaultdict(set)
    reasons: dict[str, int] = defaultdict(int)
    for page_id, page in pages_by_id.items():
        groups = group_by_page.get(page_id, [])
        group_label = " ".join(f"{g.get('id', '')} {g.get('title', '')} {g.get('short_title', '')}" for g in groups)
        for section in page.get("sections", []):
            sid = section.get("id")
            if not sid:
                continue
            labels = [page.get("title", ""), section.get("title", ""), *page.get("keywords", [])]
            # Group names provide stable course context, while section/page labels
            # can still mark a mixed review section as overlapping or ambiguous.
            labels.append(group_label)
            branch = set().union(*(_branches(label) for label in labels)) if labels else set()
            if branch:
                branches_by_section[sid].update(branch)
            else:
                reasons["unclassified_sections"] += 1
    return branches_by_section, dict(reasons)


def _review(value: Any) -> str | None:
    if value == "reviewed":
        return "good"
    if value == "later":
        return "bad"
    if value in ("good", "bad"):
        return value
    return None


def summarize_module(data: dict[str, Any], state: dict[str, Any], *, synced_at: Any = None) -> dict[str, Any]:
    """Return aggregate Neuro/Psych counts from guide metadata and saved state."""
    metadata = _section_metadata(data)
    return _summarize_metadata(metadata, state, synced_at=synced_at)


def _summarize_metadata(metadata: dict[str, Any], state: dict[str, Any], *, synced_at: Any = None) -> dict[str, Any]:
    section_branches, mapping_counts = _mapping(metadata)
    mapped_sections = set(section_branches)
    objectives_state = state.get("objectives") if isinstance(state.get("objectives"), dict) else {}
    questions_state = state.get("questions") if isinstance(state.get("questions"), dict) else {}
    learned_state = state.get("good") if isinstance(state.get("good"), dict) else {}

    branch_sections: dict[str, set[str]] = {"neuro": set(), "psychiatry": set()}
    for section_id, branches in section_branches.items():
        for branch in branches:
            branch_sections[branch].add(section_id)

    result: dict[str, Any] = {}
    for branch in ("neuro", "psychiatry"):
        sections = branch_sections[branch]
        los: dict[str, set[str]] = defaultdict(set)
        for lo in metadata.get("objectives", []):
            lo_id = lo.get("id")
            linked = set(lo.get("sections", [])) & sections
            if lo_id and linked:
                los[lo_id].update(linked)
        counts = {"good": 0, "bad": 0, "unrated": 0}
        for lo_id in los:
            rating = _review((objectives_state.get(lo_id) or {}).get("review") if isinstance(objectives_state.get(lo_id), dict) else None)
            counts[rating or "unrated"] += 1
        questions: dict[str, set[str]] = defaultdict(set)
        wrong_ids: set[str] = set()
        for question in metadata.get("questions", []):
            qid = question.get("id")
            linked = set(question.get("sections", [])) & sections
            if not qid or not linked:
                continue
            questions[qid].update(linked)
            qstate = questions_state.get(qid)
            qreview = qstate.get("review") if isinstance(qstate, dict) else None
            if re.search(r"wrong", question.get("source_status", ""), re.I) and qreview != "reviewed":
                wrong_ids.add(qid)
        result[branch] = {
            "lo_good": counts["good"], "lo_bad": counts["bad"], "lo_unrated": counts["unrated"],
            "learned_sections": len(sections & {sid for sid, value in learned_state.items() if value}),
            "total_sections": len(sections), "wrong_count": len(wrong_ids),
            "question_count": len(questions),
        }

    mapping_counts["unmapped_objectives"] = sum(
        not (set(lo.get("sections", [])) & mapped_sections) for lo in metadata.get("objectives", [])
    )
    mapping_counts["unmapped_questions"] = sum(
        not (set(question.get("sections", [])) & mapped_sections)
        for question in metadata.get("questions", [])
    )

    return {
        "available": True,
        "synced_at": _normalise_time(synced_at),
        "source": SOURCE,
        "topics": result,
        "reason": None,
        "module_url": MODULE_URL,
        "unmapped_metadata": mapping_counts,
    }


def _normalise_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        return value
    return None


def _unavailable(reason: str) -> dict[str, Any]:
    zero = {"lo_good": 0, "lo_bad": 0, "lo_unrated": 0, "learned_sections": 0,
            "total_sections": 0, "wrong_count": 0, "question_count": 0}
    return {"available": False, "synced_at": None, "source": SOURCE,
            "topics": {"neuro": dict(zero), "psychiatry": dict(zero)},
            "reason": reason, "module_url": MODULE_URL, "unmapped_metadata": {}}


def _metadata(fetch: Callable[[str], bytes] | None, *, deadline: float) -> dict[str, Any]:
    global _METADATA_CACHE
    with _CACHE_LOCK:
        if _METADATA_CACHE is not None:
            return _METADATA_CACHE
        if fetch is None:
            timeout = max(0.1, min(4.0, deadline - time.monotonic()))
            request = Request(MODULE_URL, headers={"Accept": "text/html"}, method="GET")
            with urlopen(request, timeout=timeout) as response:
                extracted = _extract_metadata_stream(response, deadline=deadline)
        else:
            html = fetch(MODULE_URL)
            if len(html) > MAX_HTML_BYTES:
                raise ValueError("module HTML exceeds the 40 MB response limit")
            extracted = _extract_data(html)
        _METADATA_CACHE = _section_metadata(extracted)
        return _METADATA_CACHE


def collect_module_signals(fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
    """Read local module progress and its embedded DATA JSON; perform no writes.

    ``fetch`` is an injectable GET transport for tests. Production requests are
    restricted to the two fixed loopback URLs and have bounded timeouts.
    """
    transport = fetch or _get
    deadline = time.monotonic() + 6.0
    try:
        progress_body = transport(PROGRESS_URL)
        if len(progress_body) > 2_000_000:
            return _unavailable("progress response exceeds the size limit")
        envelope = json.loads(progress_body)
        if not isinstance(envelope, dict):
            return _unavailable("progress response has an unexpected shape")
        state = envelope.get("state")
        if not isinstance(state, dict):
            return _unavailable("saved progress state is unavailable")
        metadata = _metadata(fetch, deadline=deadline)
        if time.monotonic() > deadline:
            return _unavailable("local module read exceeded the 6 second budget")
        return _summarize_metadata(metadata, state, synced_at=envelope.get("updated_at"))
    except HTTPError as exc:
        return _unavailable(f"local module returned HTTP {exc.code}")
    except (URLError, TimeoutError, OSError):
        return _unavailable("local module is unavailable")
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _unavailable(str(exc))


__all__ = ["collect_module_signals", "summarize_module"]
