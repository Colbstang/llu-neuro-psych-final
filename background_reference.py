"""Precise local lookup for imported public NLM health-topic summaries.

This module never downloads or calls a remote service.  Populate the JSON file
with ``prepare_background_reference.py`` and pass that file to
``BackgroundReferenceIndex`` or the small command-line search interface.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def normalize_text(value: str) -> str:
    """Normalize aliases while folding IFN beta and Greek beta spellings."""
    text = str(value or "").casefold().replace("β", " beta ").replace("ϐ", " beta ")
    text = re.sub(r"\bifn\s*[-‐‑–—]?\s*beta\b", "interferon beta", text)
    text = re.sub(r"\binterferon\s*[-‐‑–—]?\s*beta\b", "interferon beta", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> list[str]:
    stopwords = {'what', 'is', 'are', 'the', 'a', 'an', 'does', 'mean', 'meaning', 'of', 'tell', 'me', 'about', 'please', 'explain', 'define'}
    return [token for token in normalize_text(value).split() if len(token) > 1 and token not in stopwords]


class BackgroundReferenceIndex:
    """Search imported records by exact title, ID, or alias before token match."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema") != "medlineplus-health-topics-v1":
            raise ValueError("Unsupported background reference schema")
        self.metadata = dict(payload.get("source") or {})
        self.records = [dict(record) for record in payload.get("records", []) if record.get("title")]
        self._lookup: dict[str, dict[str, Any]] = {}
        for record in self.records:
            values = [record.get("id", ""), record.get("title", ""), *record.get("aliases", [])]
            for value in values:
                key = normalize_text(value)
                if key:
                    self._lookup.setdefault(key, record)

    @staticmethod
    def _result(record: dict[str, Any]) -> dict[str, Any]:
        keys = ("title", "summary", "excerpt", "source", "url", "updated", "fetched", "license")
        result = {key: record.get(key, "") for key in keys}
        result['source_updated'] = record.get('source_updated') or record.get('updated', '')
        result['fetched_at'] = record.get('fetched_at') or record.get('fetched', '')
        return result

    def get(self, title_alias_or_id: str) -> dict[str, Any] | None:
        """Return one exact title/alias/ID match, or ``None``."""
        record = self._lookup.get(normalize_text(title_alias_or_id))
        return self._result(record) if record else None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return precise local matches, with exact title/alias hits first."""
        normalized = normalize_text(query)
        terms = set(_tokens(query))
        if not normalized or not terms:
            return []
        exact = self._lookup.get(normalized)
        if exact:
            return [self._result(exact)]
        ranked: list[tuple[int, dict[str, Any]]] = []
        for record in self.records:
            labels = [record.get("title", ""), *record.get("aliases", [])]
            label_text = normalize_text(" ".join(labels))
            label_terms = set(_tokens(label_text))
            overlap = terms & label_terms
            if not overlap or not terms.issubset(label_terms):
                continue
            score = len(overlap) * 10
            if normalized in label_text:
                score += 100
            if normalize_text(record.get("title", "")) == normalized:
                score += 1000
            ranked.append((score, record))
        ranked.sort(key=lambda item: (-item[0], normalize_text(item[1].get("title", ""))))
        return [self._result(record) for _, record in ranked[: max(1, int(limit))]]


class BackgroundReference(BackgroundReferenceIndex):
    """Backend-compatible loader rooted at the Prototype directory.

    A missing or empty staged index is a valid offline state and returns no
    matches; it never falls back to a network request.
    """

    def __init__(self, root: str | Path):
        root_path = Path(root)
        path = root_path if root_path.is_file() else root_path / "background_index" / "medlineplus_health_topics.json"
        if not path.is_file():
            self.path = path
            self.metadata = {}
            self.records = []
            self._lookup = {}
            return
        super().__init__(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search a local MedlinePlus background index")
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("query", nargs="+")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    index = BackgroundReferenceIndex(args.index)
    print(json.dumps(index.search(" ".join(args.query), args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
