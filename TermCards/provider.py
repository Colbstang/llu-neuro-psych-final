"""Local-first term lookup backed by a small MDWiki API adapter.

Only an explicitly requested term is sent to MDWiki. Responses are read as
plain text and are never rendered as remote HTML.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


API_URL = "https://mdwiki.org/w/api.php"
WIKI_ROOT = "https://mdwiki.org"
COPYRIGHT_URL = "https://mdwiki.org/wiki/WikiProjectMed:Copyright"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
PROJECT_URL = "https://github.com/Colbstang/llu-neuro-psych-final"
USER_AGENT = f"LLU-TermCards/0.1 ({PROJECT_URL}; local educational prototype) Python-urllib"
MAX_RESPONSE_BYTES = 1_000_000
MAX_QUERY_LENGTH = 160
REQUEST_TIMEOUT = 10
CACHE_MAX_ENTRIES = 1000
CACHE_MAX_BYTES = 5_000_000


class _LeadParagraphParser(HTMLParser):
    """Collect plain lead paragraphs while skipping non-prose page regions."""

    SKIP_TAGS = {"table", "ul", "ol", "dl", "figure", "script", "style", "sup", "nav", "aside"}
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    SKIP_MARKERS = {"infobox", "reference", "references", "citation", "shortdescription", "mw-editsection", "navbox", "metadata"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._current: list[str] | None = None
        self._ignored: list[str] = []
        self._stopped = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._stopped:
            return
        if self._ignored:
            if tag not in self.VOID_TAGS:
                self._ignored.append(tag)
            return
        attr_map = {key.casefold(): (value or "").casefold() for key, value in attrs}
        markers = set(re.split(r"[^a-z0-9_-]+", attr_map.get("class", "") + " " + attr_map.get("id", "")))
        if tag in self.SKIP_TAGS or markers.intersection(self.SKIP_MARKERS):
            if tag not in self.VOID_TAGS:
                self._ignored.append(tag)
            return
        if tag == "h2":
            self._stopped = True
            self._finish_paragraph()
            return
        if tag == "p":
            self._finish_paragraph()
            self._current = []

    def handle_endtag(self, tag: str) -> None:
        if self._ignored:
            for index in range(len(self._ignored) - 1, -1, -1):
                if self._ignored[index] == tag:
                    del self._ignored[index:]
                    break
            return
        if tag == "p":
            self._finish_paragraph()

    def handle_data(self, data: str) -> None:
        if not self._stopped and not self._ignored and self._current is not None:
            self._current.append(data)

    def _finish_paragraph(self) -> None:
        if self._current is not None:
            text = re.sub(r"\s+", " ", "".join(self._current)).strip()
            if text:
                self.paragraphs.append(text)
            self._current = None

    def result(self) -> str:
        self._finish_paragraph()
        return "\n\n".join(self.paragraphs)


def _lead_plain_text(html: str) -> str:
    parser = _LeadParagraphParser()
    parser.feed(html)
    parser.close()
    return parser.result()


def normalize_term(value: str) -> str:
    """Normalize punctuation, whitespace, and case for exact alias matching."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.translate(str.maketrans({"α": " alpha ", "β": " beta ", "γ": " gamma ", "δ": " delta "}))
    value = re.sub(r"[_\s]+", " ", value.strip())
    value = re.sub(r"[^\w ]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip().casefold()


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return slug or hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]


def _sentence_excerpt(text: str, max_words: int, max_sentences: int | None = None) -> str:
    """Take a concise excerpt at sentence boundaries where possible."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    protected = re.sub(r"\b(e\.g|i\.e|etc|vs|Dr|Mr|Mrs|Prof|Fig|No)\.",
                       lambda match: match.group(0).replace(".", "\u2024"), text, flags=re.IGNORECASE)
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", protected)
    sentences = [sentence.replace("\u2024", ".").strip() for sentence in sentences if sentence.strip()]
    selected: list[str] = []
    word_count = 0
    for sentence in sentences:
        count = len(re.findall(r"\b[\w'-]+\b", sentence))
        if selected and word_count + count > max_words:
            break
        if not selected and count > max_words:
            words = sentence.split()
            return " ".join(words[:max_words]).rstrip(" ,;:") + "…"
        selected.append(sentence)
        word_count += count
        if max_sentences is not None and len(selected) >= max_sentences:
            break
    excerpt = " ".join(selected)
    if not excerpt:
        words = text.split()
        return " ".join(words[:max_words]).rstrip(" ,;:") + ("…" if len(words) > max_words else "")
    if len(text.split()) > len(excerpt.split()) and not excerpt.endswith((".", "!", "?")):
        excerpt += "…"
    return excerpt


def default_cache_path() -> Path:
    """Return per-user app data outside the repository."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif __import__("sys").platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "LLU" / "TermCards" / "cache.json"


class TermProvider:
    """Resolve exact local terms first, then consult MDWiki for new terms."""

    def __init__(self, seed_path: Path | str, cache_path: Path | str | None = None,
                 timeout: float = REQUEST_TIMEOUT, opener: Any = None):
        self.seed_path = Path(seed_path)
        self.cache_path = Path(cache_path) if cache_path else default_cache_path()
        self.timeout = min(max(float(timeout), 0.1), REQUEST_TIMEOUT)
        self.opener = opener or urllib.request.urlopen
        self._records = self._load_seed()
        self._cache = self._load_cache()
        self._cache_lock = threading.RLock()

    def _load_seed(self) -> list[dict[str, Any]]:
        if not self.seed_path.exists():
            return []
        try:
            payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid term seed file: {self.seed_path}") from exc
        records = payload.get("records", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise ValueError("Term seed records must be a list")
        return [r for r in records if isinstance(r, dict) and isinstance(r.get("title"), str)]

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.cache_path.read_bytes()
            if len(raw) > CACHE_MAX_BYTES:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def lexicon(self) -> list[dict[str, Any]]:
        """Return compact vocabulary, including explicitly requested cached terms."""
        records = list(self._records)
        seen = {record.get("id") for record in records}
        with self._cache_lock:
            for record in self._cache.values():
                record_id, title = record.get("id"), record.get("title")
                if not isinstance(record_id, str) or not isinstance(title, str) or not title:
                    continue
                if record_id not in seen:
                    records.append(record)
                    seen.add(record_id)
        return [
            {"id": record.get("id", _slug(record["title"])),
             "title": record["title"],
             "aliases": list(record.get("aliases", [])) if isinstance(record.get("aliases", []), list) else [],
             "source": {"name": (record.get("source") or {}).get("name", "MDWiki")}}
            for record in records
        ]

    def lookup(self, *, query: str | None = None, record_id: str | None = None) -> dict[str, Any]:
        if (query is None) == (record_id is None):
            return {"ok": False, "error": "invalid_query", "candidates": []}
        if record_id is not None:
            if not record_id or len(record_id) > 96:
                return {"ok": False, "error": "invalid_query", "candidates": []}
            hits = [r for r in self._records if str(r.get("id", "")) == record_id]
            if len(hits) == 1:
                return {"ok": True, "record": hits[0]}
            if len(hits) > 1:
                return {"ok": False, "error": "ambiguous_term", "candidates": self._candidate_rows(hits)}
            with self._cache_lock:
                cached = self._cache.get("id:" + record_id)
            return {"ok": True, "record": cached} if cached else {"ok": False, "error": "unknown_term", "candidates": []}

        assert query is not None
        if len(query) > MAX_QUERY_LENGTH or any(ord(ch) < 32 for ch in query):
            return {"ok": False, "error": "invalid_query", "candidates": []}
        query = query.strip()
        if not query:
            return {"ok": False, "error": "invalid_query", "candidates": []}
        norm = normalize_term(query)
        hits = [r for r in self._records if any(
            normalize_term(candidate) == norm
            for candidate in [r.get("title", ""), *(r.get("aliases", []) if isinstance(r.get("aliases", []), list) else [])]
            if isinstance(candidate, str)
        )]
        if len(hits) == 1:
            return {"ok": True, "record": hits[0]}
        if len(hits) > 1:
            return {"ok": False, "error": "ambiguous_term", "candidates": self._candidate_rows(hits)}
        cache_key = "query:" + norm
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached:
            return {"ok": True, "record": cached}
        try:
            result = self._lookup_remote(query)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            return {"ok": False, "error": "source_unavailable", "candidates": []}
        if result.get("ok") and result.get("record"):
            with self._cache_lock:
                self._cache[cache_key] = result["record"]
                self._cache["id:" + result["record"]["id"]] = result["record"]
                self._trim_cache()
                self._save_cache()
        return result

    @staticmethod
    def _candidate_rows(records: list[dict[str, Any]]) -> list[dict[str, str]]:
        rows = []
        for record in records[:5]:
            title = str(record.get("title", ""))
            rows.append({"title": title, "url": str((record.get("source") or {}).get("url", ""))})
        return rows

    def _api(self, params: dict[str, str]) -> dict[str, Any]:
        params = {**params, "format": "json"}
        url = API_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        response = self.opener(request, timeout=self.timeout)
        try:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("MDWiki response exceeded size limit")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or "error" in payload:
            raise ValueError("Invalid MDWiki response")
        return payload

    def _lookup_remote(self, query: str) -> dict[str, Any]:
        # Resolve an exact title or an explicit wiki redirect first. A page is
        # accepted only when the requested text itself resolves to that page.
        direct = self._api({
            "action": "query", "titles": query,
            "prop": "extracts|info|revisions|pageprops", "exintro": "1",
            "explaintext": "1", "inprop": "url", "rvprop": "ids|timestamp",
            "formatversion": "2", "redirects": "1", "ppprop": "disambiguation",
        })
        direct_pages = direct.get("query", {}).get("pages", [])
        direct_redirects = direct.get("query", {}).get("redirects", [])
        if isinstance(direct_pages, list) and direct_pages and isinstance(direct_pages[0], dict):
            page = direct_pages[0]
            redirect_match = any(isinstance(item, dict) and
                                 normalize_term(str(item.get("from", ""))) == normalize_term(query)
                                 for item in direct_redirects)
            exact_title = normalize_term(str(page.get("title", ""))) == normalize_term(query)
            if exact_title or redirect_match:
                if redirect_match:
                    page["_termcards_exact_redirect"] = True
                if page.get("missing"):
                    if self._is_mirrored_revision(page):
                        return self._parse_fallback(page, query)
                else:
                    return self._record_result(page, query)

        # A bounded title search provides choices for imprecise queries. The
        # caller only gets a full record when a title matches exactly.
        search = self._api({
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": "0", "srlimit": "5", "format": "json",
        })
        results = search.get("query", {}).get("search", [])
        candidates = [x for x in results if isinstance(x, dict) and isinstance(x.get("title"), str)]
        exact = next((x for x in candidates if normalize_term(x["title"]) == normalize_term(query)), None)
        if not exact:
            return {"ok": False, "error": "unknown_term", "candidates": [
                {"title": x["title"], "url": WIKI_ROOT + "/wiki/" + urllib.parse.quote(x["title"].replace(" ", "_"))}
                for x in candidates[:5]
            ]}

        title = exact["title"]
        page_data = self._api({
            "action": "query", "titles": title, "prop": "extracts|info|revisions|pageprops",
            "exintro": "1", "explaintext": "1", "inprop": "url",
            "rvprop": "ids|timestamp", "formatversion": "2", "redirects": "1",
            "ppprop": "disambiguation",
        })
        pages = page_data.get("query", {}).get("pages", [])
        if not isinstance(pages, list) or not pages or not isinstance(pages[0], dict):
            return {"ok": False, "error": "unknown_term", "candidates": []}
        page = pages[0]
        return self._record_result(page, query)

    def _record_result(self, page: dict[str, Any], query: str) -> dict[str, Any]:
        page_title = page.get("title", query)
        if page.get("missing") or page.get("pageprops", {}).get("disambiguation") is not None:
            return {"ok": False, "error": "ambiguous_term", "candidates": [{
                "title": page_title, "url": page.get("fullurl", "")
            }]}
        # Redirects are accepted only when the canonical target remains an exact
        # term match. This prevents a short query such as "beta" selecting a
        # broader article that merely ranked first in search.
        if normalize_term(page_title) != normalize_term(query):
            # The page can differ from the requested alias only when the
            # direct lookup provided a matching explicit redirect.
            # `_record_result` is also used for search results, so callers pass
            # the authorized redirect through a private marker.
            if not page.get("_termcards_exact_redirect"):
                return {"ok": False, "error": "unknown_term", "candidates": [{
                    "title": page_title, "url": page.get("fullurl", "")
                }]}
        extract = page.get("extract", "")
        if not isinstance(extract, str):
            extract = ""
        extract = extract.strip()
        if not extract:
            return self._parse_fallback(page, query)
        definition = _sentence_excerpt(extract, 80, max_sentences=2)
        summary = _sentence_excerpt(extract, 350)
        revs = page.get("revisions", [])
        revision = revs[0] if isinstance(revs, list) and revs and isinstance(revs[0], dict) else {}
        revision_id = revision.get("revid")
        fetched = datetime.now(timezone.utc).isoformat()
        canonical_url = page.get("fullurl") or WIKI_ROOT + "/wiki/" + urllib.parse.quote(page_title.replace(" ", "_"))
        history_url = canonical_url + "?action=history"
        source = {
            "name": "MDWiki", "url": canonical_url,
            "history_url": history_url, "license": "CC BY-SA 4.0",
            "license_url": LICENSE_URL, "copyright_url": COPYRIGHT_URL,
            "changes": "Lead excerpt; shortened for display.", "fetched_at": fetched,
        }
        if self._is_mirrored_revision(page) and revision_id is not None:
            original_title = str(page_title)
            original_encoded = urllib.parse.quote(original_title.replace(" ", "_"), safe="_")
            original_query = urllib.parse.urlencode({"title": original_title})
            source.update({
                "original_attribution": "Wikipedia contributors",
                "original_revision_id": revision_id,
                "original_url": "https://en.wikipedia.org/wiki/" + original_encoded,
                "original_revision_url": "https://en.wikipedia.org/w/index.php?oldid=" + str(revision_id),
                "original_history_url": "https://en.wikipedia.org/w/index.php?" + original_query + "&action=history",
                "mirror_revision": revision_id,
            })
            if revision.get("timestamp"):
                source["mirror_timestamp"] = revision["timestamp"]
        elif revision_id is not None:
            source["revision_id"] = revision_id
            source["revision_url"] = canonical_url + "?oldid=" + str(revision_id)
        record = {
            "id": "mdwiki-" + _slug(page_title), "title": page_title, "aliases": [],
            "definition": definition, "summary": summary, "source": source,
        }
        return {"ok": True, "record": record}

    @staticmethod
    def _is_mirrored_revision(page: dict[str, Any]) -> bool:
        revisions = page.get("revisions", [])
        return bool(isinstance(revisions, list) and revisions and isinstance(revisions[0], dict)
                    and revisions[0].get("mirrored") is True)

    def _parse_fallback(self, page: dict[str, Any], query: str) -> dict[str, Any]:
        """Parse an exact MDWiki title and retain only safe lead paragraph text."""
        page_title = str(page.get("title", query))
        allowed_redirect = bool(page.get("_termcards_exact_redirect"))
        if normalize_term(page_title) != normalize_term(query) and not allowed_redirect:
            return {"ok": False, "error": "unknown_term", "candidates": [{
                "title": page_title, "url": page.get("fullurl", "")
            }]}
        parsed = self._api({
            "action": "parse", "page": page_title, "prop": "text|revid",
            "redirects": "1", "formatversion": "2",
        }).get("parse", {})
        if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
            return {"ok": False, "error": "content_unavailable", "candidates": [{
                "title": page_title, "url": page.get("fullurl", "")
            }]}
        parsed_title = str(parsed.get("title", page_title))
        if normalize_term(parsed_title) != normalize_term(query) and not allowed_redirect:
            return {"ok": False, "error": "unknown_term", "candidates": [{
                "title": parsed_title, "url": page.get("fullurl", "")
            }]}
        lead = _lead_plain_text(parsed["text"])
        if not lead:
            return {"ok": False, "error": "content_unavailable", "candidates": [{
                "title": parsed_title, "url": page.get("fullurl", "")
            }]}
        enriched = dict(page)
        enriched["title"] = parsed_title
        enriched["extract"] = lead
        enriched.pop("missing", None)
        if not enriched.get("revisions") and parsed.get("revid") is not None:
            enriched["revisions"] = [{"revid": parsed["revid"]}]
        return self._record_result(enriched, query)

    def _trim_cache(self) -> None:
        """Keep a FIFO cache under entry and serialized-size caps."""
        kept: list[tuple[str, dict[str, Any]]] = []
        size = 2  # Opening and closing braces.
        for key, value in reversed(list(self._cache.items())):
            entry_size = len(json.dumps({key: value}, ensure_ascii=False).encode("utf-8")) - 2 + (1 if kept else 0)
            if len(kept) >= CACHE_MAX_ENTRIES or size + entry_size > CACHE_MAX_BYTES:
                continue
            kept.append((key, value))
            size += entry_size
        self._cache = dict(reversed(kept))

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_lock:
                body = json.dumps(self._cache, ensure_ascii=False).encode("utf-8")
                if len(body) > CACHE_MAX_BYTES:
                    return
                with tempfile.NamedTemporaryFile(dir=self.cache_path.parent, prefix=".termcards-", suffix=".tmp",
                                                 delete=False) as temporary:
                    temporary.write(body)
                    temp_path = Path(temporary.name)
                temp_path.replace(self.cache_path)
        except OSError:
            # The lookup remains usable if per-user caching is unavailable.
            pass
