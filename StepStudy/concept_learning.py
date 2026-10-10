"""Stable, source-grounded recall items for the private Neuro/Psych guide.

The catalog returned here is metadata only. Answer text is resolved on demand
for one item by :func:`build_item_context` and is never copied into state.
"""
from __future__ import annotations

import re
import hashlib
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Callable

from StepStudy.module_signals import _mapping, _section_metadata


MAX_ITEMS = 5_000
MAX_PROMPT_CHARS = 1_200
MAX_CONTEXT_CHARS = 3_000
MAX_SOURCE_INPUT_CHARS = 40_000
MAX_POINTS = 8
MAX_SOURCES = 4
_DOCUMENT_ROUTE = re.compile(r"^/api/course/document/[A-Za-z0-9_-]{16,64}\.pdf(?:#page=\d+)?$")
_VETTED_HTTPS_DOMAINS = {
    "mdwiki.org", "ncbi.nlm.nih.gov", "nih.gov", "medlineplus.gov",
    "msdmanuals.com", "merckmanuals.com",
}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "iframe", "object", "svg"}:
            self.skip += 1
        if not self.skip and tag.lower() in {"p", "li", "div", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "iframe", "object", "svg"} and self.skip:
            self.skip -= 1
        if not self.skip and tag.lower() in {"p", "li", "div", "br", "h1", "h2", "h3", "tr"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def _plain(value: Any, limit: int = MAX_CONTEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value[:MAX_SOURCE_INPUT_CHARS]
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        value = " ".join(parser.parts)
    except Exception:
        # Malformed passive HTML should not make the source unavailable if the
        # remaining text can still be stripped safely.
        value = re.sub(r"<[^>]{0,500}>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return value
    # Keep a readable final word and make truncation visible to the learner.
    clipped = value[:max(1, limit - 1)]
    boundary = clipped.rfind(" ")
    if boundary >= max(1, limit // 2):
        clipped = clipped[:boundary]
    return clipped.rstrip() + "…"


def _id(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(ch) < 32 for ch in value):
        return ""
    return value


def _list_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(x for raw in value if (x := _id(raw))))


def _answer_for(answer: Any) -> str:
    if isinstance(answer, str):
        return _plain(answer)
    if not isinstance(answer, dict):
        return ""
    for key in ("text", "answer", "html", "content", "explanation", "rationale"):
        text = _plain(answer.get(key))
        if text:
            return text
    return ""


def _source_rows(rows: Any, fallback_id: str, fallback_title: str,
                 source_resolver: Callable[[str], str | None] | None = None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    def citation(label: str, page: Any = None, url: Any = None,
                 path: Any = None) -> dict[str, str] | None:
        label = _plain(label, 160)
        if not label or re.match(r"^(?:/|[A-Za-z]:[\\/]|file:|https?://)", label, re.I):
            label = "Course source"
        page_number = page if isinstance(page, int) and not isinstance(page, bool) and 1 <= page <= 100_000 else None
        title = f"{label} (p. {page_number})" if page_number else label
        material = "\0".join((label.casefold(), str(page_number or "")))
        row = {"id": "course-source:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20],
               "title": title[:160]}
        if source_resolver:
            for alias in (url, path):
                if isinstance(alias, str) and alias:
                    try:
                        resolved = source_resolver(alias)
                    except Exception:
                        resolved = None
                    if isinstance(resolved, str) and resolved:
                        url = resolved
                        break
        if isinstance(url, str):
            safe_url = ""
            if _DOCUMENT_ROUTE.fullmatch(url):
                safe_url = url.split("#", 1)[0]
                if page_number:
                    safe_url += f"#page={page_number}"
                elif "#page=" in url:
                    safe_url += "#" + url.rsplit("#", 1)[-1]
            else:
                parsed = urllib.parse.urlsplit(url)
                host = (parsed.hostname or "").lower().rstrip(".")
                vetted = any(host == domain or host.endswith("." + domain) for domain in _VETTED_HTTPS_DOMAINS)
                if (parsed.scheme == "https" and vetted and not parsed.username and not parsed.password
                        and not parsed.query and parsed.path.startswith("/")
                        and len(url) <= 1_000):
                    safe_url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
                    if page_number and parsed.path.lower().endswith(".pdf"):
                        safe_url += f"#page={page_number}"
            if safe_url:
                row["url"] = safe_url
        return row

    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                value = _plain(row, 160)
                citation_row = citation(value or "Course source")
                if citation_row:
                    result.append(citation_row)
            elif isinstance(row, dict):
                title = _plain(row.get("title") or row.get("name") or row.get("label"), 160)
                citation_row = citation(title or fallback_title, row.get("page"), row.get("url"), row.get("path"))
                if citation_row:
                    result.append(citation_row)
    if not result:
        result = [{"id": fallback_id, "title": fallback_title}]
    unique = []
    seen = set()
    for row in result:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)
        if len(unique) >= MAX_SOURCES:
            break
    return unique


def _prompt(label: str, text: str, kind: str) -> str:
    if kind == "objective":
        prompt = f"Recall the key points that satisfy this learning objective: {text}"
    else:
        prompt = f"Explain the key idea in {label} using the source material."
    return _plain(prompt, MAX_PROMPT_CHARS)


def _chapter_maps(data: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, str], dict[str, set[str]]]:
    pages = {pid: page for page in data.get("pages", []) if isinstance(page, dict)
             and (pid := _id(page.get("id")))}
    block_page: dict[str, str] = {}
    blocks: dict[str, dict[str, Any]] = {}
    for pid, page in pages.items():
        for block in page.get("blocks", []) if isinstance(page.get("blocks"), list) else []:
            if not isinstance(block, dict) or not (bid := _id(block.get("id"))):
                continue
            blocks[bid] = block
            block_page[bid] = pid
    metadata = _section_metadata(data)
    section_topics, _ = _mapping(metadata)
    # A few authored pages have stable chapter IDs that carry the course
    # branch while their blocks/objectives have no branch-specific wording.
    # This is explicit source metadata, so apply it only to those pages and
    # their own blocks; never use it as a fallback for arbitrary unclassified
    # content.
    explicit_group_topics: dict[str, set[str]] = {}
    groups = data.get("topic_groups", {}).get("groups", []) if isinstance(data.get("topic_groups"), dict) else []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        group_topics = _stable_topic_tokens(group.get("id"))
        for page_id in _list_ids(group.get("page_ids")):
            explicit_group_topics.setdefault(page_id, set()).update(group_topics)
    for page_id, page in pages.items():
        page_topics = _stable_topic_tokens(page_id) | explicit_group_topics.get(page_id, set())
        if not page_topics:
            continue
        for block in page.get("blocks", []) if isinstance(page.get("blocks"), list) else []:
            if isinstance(block, dict) and isinstance(block.get("id"), str):
                section_topics.setdefault(block["id"], set()).update(page_topics)
    return pages, block_page, {bid: bid for bid in blocks}, section_topics


def _stable_topic_tokens(value: Any) -> set[str]:
    """Map only explicit branch tokens in author-assigned stable IDs."""
    if not isinstance(value, str):
        return set()
    tokens = {token for token in re.split(r"[-_./]+", value.casefold()) if token}
    result: set[str] = set()
    if tokens & {"neuro", "neurology"}:
        result.add("neuro")
    if tokens & {"psychiatry", "psychiatric"}:
        result.add("psychiatry")
    if "neuropsych" in tokens:
        result.update({"neuro", "psychiatry"})
    return result


def _topics_for(sections: list[str], section_topics: dict[str, set[str]], page: dict[str, Any] | None) -> list[str]:
    result: set[str] = set()
    for section in sections:
        result.update(section_topics.get(section, set()))
    # Page group membership is represented in the section classifier. For an
    # objective without section links, leave it unmapped rather than guessing.
    return sorted(result)


def build_catalog(course_data: dict[str, Any], topic_ids: list[str] | set[str] | None = None,
                  source_resolver: Callable[[str], str | None] | None = None) -> list[dict[str, Any]]:
    """Build compact item descriptors from stable course IDs and source text.

    Learning objectives with supplied answers are preferred. Linked blocks are
    used only when no answered objective covers them. Items with no usable
    source answer are omitted; this function never supplies medical facts.
    """
    if not isinstance(course_data, dict):
        return []
    allowed_topics = set(topic_ids) if topic_ids is not None else None
    pages, block_page, _, section_topics = _chapter_maps(course_data)
    blocks = {block_id: block for page in pages.values()
              for block in page.get("blocks", []) if isinstance(block, dict)
              and (block_id := _id(block.get("id")))}
    answers = course_data.get("objective_answers", {})
    answers = answers if isinstance(answers, dict) else {}
    question_answers = course_data.get("question_answers", {})
    question_answers = question_answers if isinstance(question_answers, dict) else {}
    objectives = course_data.get("objectives", [])
    objectives = objectives if isinstance(objectives, list) else []
    catalog: list[dict[str, Any]] = []
    covered_blocks: set[str] = set()

    for lo in objectives:
        if not isinstance(lo, dict):
            continue
        stable_id = _id(lo.get("id"))
        text = _plain(lo.get("text") or lo.get("objective") or lo.get("title"), MAX_PROMPT_CHARS)
        answer_text = _answer_for(answers.get(stable_id)) if stable_id else ""
        if not stable_id or not text or not answer_text:
            continue
        sections = _list_ids(lo.get("sections") or lo.get("section_ids"))
        topics = _topics_for(sections, section_topics, None)
        if allowed_topics is not None:
            topics = [topic for topic in topics if topic in allowed_topics]
        if not topics:
            continue
        locations = [s for s in sections if s in blocks]
        if not locations:
            continue
        chapter_id = block_page[locations[0]]
        page = pages[chapter_id]
        title = _plain(lo.get("title") or text, 200)
        item_id = f"lo:{stable_id}"
        source_rows = _source_rows(answers.get(stable_id, {}).get("sources") if isinstance(answers.get(stable_id), dict) else None,
                                   item_id, title, source_resolver)
        catalog.append({"item_id": item_id, "topic_ids": topics, "chapter_id": chapter_id,
                        "chapter_title": _plain(page.get("title") or chapter_id, 180),
                        "title": title, "prompt": _prompt(title, text, "objective"),
                        "source_ids": [source["id"] for source in source_rows],
                        "kind": "objective"})
        # A cross-chapter objective is scheduled under its first linked
        # chapter only. Its links into other chapters must not suppress their
        # own source-grounded block fallback when they have no local item.
        covered_blocks.update(section_id for section_id in locations
                              if block_page.get(section_id) == chapter_id)
        if len(catalog) >= MAX_ITEMS:
            return catalog

    # Use a question only when its own record explicitly carries a supplied
    # answer/explanation or a matching answer record. Unanswered question
    # prompts are not turned into a fabricated rubric.
    question_links: dict[str, list[str]] = {}
    for question in course_data.get("questions", []) if isinstance(course_data.get("questions"), list) else []:
        if isinstance(question, dict) and (qid := _id(question.get("id"))):
            question_links[qid] = _list_ids(question.get("suggested_sections"))
    for link in course_data.get("question_auto_links", []) if isinstance(course_data.get("question_auto_links"), list) else []:
        if isinstance(link, dict) and _id(link.get("question_id")):
            question_links.setdefault(link["question_id"], []).extend(_list_ids(link.get("sections")))
    for annotation in course_data.get("question_annotations", []) if isinstance(course_data.get("question_annotations"), list) else []:
        if isinstance(annotation, dict) and _id(annotation.get("question_id")) and _id(annotation.get("block_id")):
            question_links.setdefault(annotation["question_id"], []).append(annotation["block_id"])
    for correction in course_data.get("question_link_corrections", []) if isinstance(course_data.get("question_link_corrections"), list) else []:
        if not isinstance(correction, dict) or not _id(correction.get("question_id")):
            continue
        linked = question_links.setdefault(correction["question_id"], [])
        old, new = _id(correction.get("old_section")), _id(correction.get("new_section"))
        linked[:] = [sid for sid in linked if sid != old]
        if new and new not in linked:
            linked.append(new)
    for question in course_data.get("questions", []) if isinstance(course_data.get("questions"), list) else []:
        if not isinstance(question, dict) or not (qid := _id(question.get("id"))):
            continue
        supplied = question_answers.get(qid)
        if not supplied:
            # Accept only explicit answer-like fields on the question itself.
            supplied = {key: question[key] for key in
                        ("answer", "correct_answer", "explanation", "rationale", "answer_html", "key_fact")
                        if key in question}
        answer_text = _answer_for(supplied)
        trigger = _plain(question.get("trigger") or question.get("stem") or question.get("question"), 420)
        trap = _plain(question.get("trap"), 700)
        if question.get("key_fact"):
            key_fact = _plain(question.get("key_fact"), 1_200)
            if not answer_text or (isinstance(supplied, dict) and set(supplied).issubset({"key_fact"})):
                answer_text = "Key fact: " + key_fact + (" Source-identified trap: " + trap if trap else "")
        sections = list(dict.fromkeys(question_links.get(qid, [])))
        locations = [sid for sid in sections if sid in blocks]
        topics = _topics_for(locations, section_topics, None)
        if allowed_topics is not None:
            topics = [topic for topic in topics if topic in allowed_topics]
        if not answer_text or not trigger or not locations or not topics:
            continue
        chapter_id = block_page[locations[0]]
        page = pages[chapter_id]
        item_id = f"question:{qid}"
        prompt_prefix = "Explain the distinction tested by this supplied course trigger: "
        prompt = prompt_prefix + trigger
        catalog.append({"item_id": item_id, "topic_ids": topics, "chapter_id": chapter_id,
                        "chapter_title": _plain(page.get("title") or chapter_id, 180),
                        "title": "Supplied course question",
                        "prompt": _plain(prompt, MAX_PROMPT_CHARS),
                        "source_ids": [source["id"] for source in _source_rows(
                            supplied.get("sources") if isinstance(supplied, dict) and supplied.get("sources") else
                            ([{"label": question.get("source"), "page": question.get("source_page"),
                              "url": question.get("source_url")}] if question.get("source") else None),
                            item_id, "Course source", source_resolver)], "kind": "question"})
        if len(catalog) >= MAX_ITEMS:
            return catalog

    for block_id, block in blocks.items():
        if block_id in covered_blocks:
            continue
        page_id = block_page[block_id]
        page = pages[page_id]
        sections = [block_id]
        topics = _topics_for(sections, section_topics, page)
        if allowed_topics is not None:
            topics = [topic for topic in topics if topic in allowed_topics]
        source_text = _plain(block.get("summary") or block.get("text") or block.get("html"))
        if not topics or not source_text:
            continue
        title = _plain(block.get("title") or page.get("title") or block_id, 200)
        item_id = f"block:{block_id}"
        sources = _source_rows(block.get("sources") or block.get("lecture_sources"), item_id, title, source_resolver)
        catalog.append({"item_id": item_id, "topic_ids": topics, "chapter_id": page_id,
                        "chapter_title": _plain(page.get("title") or page_id, 180),
                        "title": title, "prompt": _prompt(title, title, "block"),
                        "source_ids": [source["id"] for source in sources], "kind": "block"})
        if len(catalog) >= MAX_ITEMS:
            break
    return catalog


def build_item_context(course_data: dict[str, Any], item_id: str,
                       source_resolver: Callable[[str], str | None] | None = None) -> dict[str, Any] | None:
    """Resolve one catalog item to a bounded source context for explicit grading."""
    if not isinstance(item_id, str) or not item_id or len(item_id) > 200:
        return None
    catalog = build_catalog(course_data, source_resolver=source_resolver)
    item = next((row for row in catalog if row["item_id"] == item_id), None)
    if item is None:
        return None
    reference = ""
    sources: list[dict[str, str]] = []
    expected_points: list[str] = []
    if item_id.startswith("lo:"):
        lo_id = item_id[3:]
        answers = course_data.get("objective_answers", {})
        answer = answers.get(lo_id) if isinstance(answers, dict) else None
        reference = _answer_for(answer)
        sources = _source_rows(answer.get("sources") if isinstance(answer, dict) else None,
                               item_id, item["title"], source_resolver)
        expected_points = [part.strip(" .;:") for part in re.split(r"(?<=[.!?])\s+|\s*[;•]\s*", reference)
                           if len(part.strip()) >= 8][:MAX_POINTS]
    elif item_id.startswith("block:"):
        target = item_id[6:]
        for page in course_data.get("pages", []) if isinstance(course_data.get("pages"), list) else []:
            if not isinstance(page, dict):
                continue
            for block in page.get("blocks", []) if isinstance(page.get("blocks"), list) else []:
                if isinstance(block, dict) and block.get("id") == target:
                    reference = _plain(block.get("text") or block.get("html") or block.get("summary"))
                    sources = _source_rows(block.get("sources") or block.get("lecture_sources"), item_id, item["title"], source_resolver)
                    break
    elif item_id.startswith("question:"):
        qid = item_id[9:]
        questions = course_data.get("questions", [])
        question = next((q for q in questions if isinstance(q, dict) and q.get("id") == qid), None) if isinstance(questions, list) else None
        answer_map = course_data.get("question_answers", {})
        answer = answer_map.get(qid) if isinstance(answer_map, dict) else None
        if question and not answer:
            answer = {key: question[key] for key in
                      ("answer", "correct_answer", "explanation", "rationale", "answer_html", "key_fact")
                      if key in question}
        key_fact = _plain(question.get("key_fact"), 1_200) if question else ""
        trap = _plain(question.get("trap"), 700) if question else ""
        if key_fact and (not answer or (isinstance(answer, dict) and set(answer).issubset({"key_fact"}))):
            reference = "Key fact: " + key_fact + (" Source-identified trap: " + trap if trap else "")
            expected_points = ["Key fact: " + key_fact]
            if trap:
                expected_points.append("Source-identified trap: " + trap)
        else:
            reference = _answer_for(answer)
            expected_points = [part.strip(" .;:") for part in re.split(r"(?<=[.!?])\s+|\s*[;•]\s*", reference)
                               if len(part.strip()) >= 8][:MAX_POINTS]
        source_rows = answer.get("sources") if isinstance(answer, dict) and answer.get("sources") else None
        if not source_rows and question and question.get("source"):
            source_rows = [{"label": question.get("source"), "page": question.get("source_page"),
                            "url": question.get("source_url")}]
        sources = _source_rows(source_rows, item_id, "Course source", source_resolver)
    reference = reference[:MAX_CONTEXT_CHARS]
    if not reference or not sources:
        return None
    if not expected_points:
        expected_points = [reference[:240]]
    return {"item": item, "prompt": item["prompt"], "expected_points": expected_points,
            "sources": sources, "reference_context": reference}
