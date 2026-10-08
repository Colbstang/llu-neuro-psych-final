"""Local semantic search and page viewing for cataloged First Aid and Pathoma PDFs."""
from __future__ import annotations

import base64
import html
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

import numpy as np

BOOKS = ("First Aid", "Pathoma")
MIN_RERANK_SCORE = -4.5
_STOP = set("a an and are as at be by for from has have in is it of on or the to with what where when how does do define explain this that these those objective objectives learning lecture slide slides page pages pdf describe discuss list identify compare include including based give given use used using following which who their them its into about after before during between each other also may can should must will would could from all any most more less very such than then there here why".split())
_GENERIC_HEADING_TERMS = {"management", "treatment", "diagnosis", "clinical", "care"}
_LAYER_LOCK = threading.RLock()


class ReferenceIndexNotReady(RuntimeError):
    """Raised when a requested source page cannot be resolved locally."""


def normalize_alias(value: str) -> str:
    from urllib.parse import unquote, urlsplit

    text = unquote(str(value or "")).strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    if parsed.scheme or parsed.netloc:
        text = parsed.path
    text = text.split("#", 1)[0].split("?", 1)[0]
    text = text.replace("\\", "/").rstrip("/")
    return re.sub(r"\s+", " ", text).casefold()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(text).casefold()) if len(t) > 1 and t not in _STOP}


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_bbox_layout(source: str) -> dict[str, Any]:
    import xml.etree.ElementTree as ET

    # Some PDF fonts emit control characters that XML 1.0 cannot represent.
    # Keep word positions and readable text while normalizing those separators.
    source = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", " ", source)
    try:
        page = next(e for e in ET.fromstring(source).iter() if _xml_local(e.tag) == "page")
    except (ET.ParseError, StopIteration) as exc:
        raise RuntimeError("pdftotext did not return a page layout") from exc
    width, height = float(page.attrib["width"]), float(page.attrib["height"])
    spans: list[dict[str, Any]] = []
    for line in (e for e in page.iter() if _xml_local(e.tag) == "line"):
        words = [e for e in line if _xml_local(e.tag) == "word"]
        text = " ".join("".join(word.itertext()) for word in words).strip()
        if not text:
            continue
        try:
            x0, y0 = float(line.attrib["xMin"]), float(line.attrib["yMin"])
            x1, y1 = float(line.attrib["xMax"]), float(line.attrib["yMax"])
        except (KeyError, ValueError):
            continue
        spans.append({
            "x": max(0.0, min(1.0, x0 / width)), "y": max(0.0, min(1.0, y0 / height)),
            "w": max(0.0, min(1.0, (x1 - x0) / width)), "h": max(0.0, min(1.0, (y1 - y0) / height)),
            "size": round(max(1.0, (y1 - y0) * 0.78), 2), "text": text,
        })
    return {"width": width, "height": height, "spans": spans}


class ReferenceSearch:
    """Search the two explicitly cataloged books with shared local models.

    ``encoder`` must expose ``encode([text, ...])`` and ``reranker`` must
    expose ``score(query, [passage, ...])``. The caller supplies these shared
    instances; this class never creates a model or makes a network request.
    """

    def __init__(self, root: str | Path, encoder: Any, reranker: Any):
        self.root = Path(root).resolve()
        self.encoder = encoder
        self.reranker = reranker
        self.index_root = self.root / "reference_index"
        self.cache_root = self.root / "source_page_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.catalog: dict[str, Any] = {}
        self.entries: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, str] = {}
        self.records: list[dict[str, Any]] = []
        self.vectors: np.ndarray | None = None
        self.status = "Reference index is not ready. Run prepare_reference_catalog.py --index."
        self._load()

    def _load(self) -> None:
        catalog_path = self.index_root / "catalog.json"
        metadata_path = self.index_root / "metadata.json"
        vectors_path = self.index_root / "vectors.npz"
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.catalog = catalog
        self.entries = {k: v for k, v in catalog.get("books", {}).items() if k in BOOKS}
        self.documents = {str(d.get("id")): dict(d) for d in catalog.get("documents", []) if d.get("id")}
        for book, entry in self.entries.items():
            doc_id = str(entry.get("id", book))
            self.documents[doc_id] = {**self.documents.get(doc_id, {}), **entry, "book": book}
        for document in self.documents.values():
            document["pdfPath"] = document.get("pdfPath") or document.get("path")
            document["available"] = bool(document["pdfPath"])
            if not document.get("pageCount") and document.get("page_count"):
                document["pageCount"] = document["page_count"]
        self.aliases = {str(k): v for k, v in catalog.get("aliases", {}).items() if v in self.documents}
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            vectors = np.load(vectors_path, allow_pickle=False)["vectors"]
        except (OSError, KeyError, json.JSONDecodeError, ValueError):
            self.status = "Reference catalog is ready, but its semantic index is not. Run prepare_reference_catalog.py --index."
            return
        if metadata.get("schema") != catalog.get("schema") or len(metadata.get("records", [])) != len(vectors):
            self.status = "Reference index metadata does not match its vectors. Rebuild with prepare_reference_catalog.py --index."
            return
        self.records = metadata["records"]
        self.vectors = vectors.astype(np.float32, copy=False)
        self.status = "ready"

    def _canonical(self, document_id: str) -> str | None:
        key = normalize_alias(document_id)
        canonical = self.aliases.get(key)
        if canonical:
            return canonical
        # Only IDs explicitly present in the catalog are accepted.
        return str(document_id) if str(document_id) in self.documents else None

    def search_books(self, query: str, limit: int = 3) -> dict[str, list[dict[str, Any]]]:
        results: dict[str, list[dict[str, Any]]] = {book: [] for book in BOOKS}
        if self.vectors is None or not self.records or not str(query).strip():
            return results
        query = str(query).strip()[:4000]
        limit = max(1, min(10, int(limit)))
        query_vector = np.asarray(self.encoder.encode([query])[0], dtype=np.float32)
        cosine = self.vectors @ query_vector
        for book in BOOKS:
            entry = self.entries[book]
            # The catalog's verified printed-page offset is the physical
            # front-matter boundary (printed page 1 begins immediately after).
            front_matter_pages = int(entry.get("printedPageOffset") or 0)
            indices = [i for i, record in enumerate(self.records)
                       if record.get("documentId") == book and not record.get("indexPage")
                       and int(record.get("physicalPage", 0)) > front_matter_pages]
            if not indices:
                continue
            candidates = sorted(indices, key=lambda i: float(cosine[i]), reverse=True)[:min(48, len(indices))]
            passages = [self.records[i]["excerpt"] for i in candidates]
            rerank = self.reranker.score(query, passages)
            ranked = sorted(zip(candidates, rerank), key=lambda item: float(item[1]) + .15 * float(cosine[item[0]]), reverse=True)
            emitted: set[int] = set()
            for record_index, score in ranked:
                if float(score) < MIN_RERANK_SCORE:
                    continue
                record = self.records[record_index]
                page = int(record["physicalPage"])
                if page in emitted:
                    continue
                emitted.add(page)
                layer = self._get_page_layer(book, page)
                span_indices = self._focus_indices(layer, query)
                results[book].append({
                    "documentId": entry.get("id", book), "book": book,
                    "physicalPage": page, "physical_page": page,
                    "page": page, "printed_page": self._printed_page(book, page, layer),
                    "excerpt": record["excerpt"], "span_indices": span_indices,
                    "title": entry.get("title", book), "url": self._page_url(book, page),
                    "score": round(float(score), 4),
                })
                if len(results[book]) >= limit:
                    break
        return results

    def source_page(self, document_id: str, page: int, query: str = "") -> dict[str, Any]:
        if not self.catalog:
            raise ReferenceIndexNotReady(self.status)
        canonical = self._canonical(document_id)
        if canonical is None:
            raise ValueError("Unknown source document ID")
        if isinstance(page, bool) or not isinstance(page, int):
            raise ValueError("Physical PDF page must be an integer")
        entry = self.documents[canonical]
        page_count = entry.get("pageCount")
        if not entry.get("available") or not entry.get("pdfPath"):
            raise ReferenceIndexNotReady(f"Local PDF unavailable for {canonical}")
        if not isinstance(page_count, int) or page < 1 or page > page_count:
            raise ValueError(f"Physical page must be between 1 and {page_count}")
        layer = self._get_page_layer(canonical, page)
        focus_indices = self._focus_indices(layer, query) if query else []
        quotes = [layer["spans"][i]["text"] for i in focus_indices]
        book = entry.get("book")
        return {
            "documentId": entry.get("id", canonical), "book": book,
            "page": page, "page_count": page_count,
            "title": entry.get("title", canonical),
            "image": self._page_image(canonical, page), "layer": layer,
            "focus": {"spans": focus_indices, "quotes": quotes},
            "url": self._page_url(canonical, page),
            "printed_page": self._printed_page(canonical, page, layer) if book in BOOKS else None,
        }

    def _get_page_layer(self, canonical: str, page: int) -> dict[str, Any]:
        path = self.cache_root / f"{self._slug(canonical)}-{page:04d}.json"
        with _LAYER_LOCK:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            entry = self.documents[canonical]
            result = subprocess.run(
                ["pdftotext", "-bbox-layout", "-f", str(page), "-l", str(page),
                 "-enc", "UTF-8", entry["pdfPath"], "-"],
                check=True, capture_output=True, text=True, timeout=120,
            )
            layer = _parse_bbox_layout(result.stdout)
            path.write_text(json.dumps(layer, ensure_ascii=False), encoding="utf-8")
            return layer

    def _page_image(self, canonical: str, page: int) -> str:
        path = self.cache_root / f"{self._slug(canonical)}-{page:04d}.jpg"
        with _LAYER_LOCK:
            if not path.is_file():
                entry = self.documents[canonical]
                prefix = path.with_suffix("")
                subprocess.run(
                    ["pdftoppm", "-f", str(page), "-l", str(page), "-jpeg", "-singlefile",
                     "-scale-to", "1800", entry["pdfPath"], str(prefix)],
                    check=True, capture_output=True, timeout=180,
                )
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return "data:image/jpeg;base64," + payload

    def _focus_indices(self, layer: dict[str, Any], query: str) -> list[int]:
        all_spans = layer.get("spans", [])
        query_terms = _tokens(query)
        if not query_terms or not all_spans:
            return []

        # If a page has a clearly matching all-caps section heading, constrain
        # candidate windows to that section before ranking. This avoids marking
        # neighboring subsections that happen to repeat words such as
        # "hemorrhage" or "space".
        headings: list[tuple[int, str, set[str]]] = []
        for index, span in enumerate(all_spans):
            text = str(span.get("text", "")).strip()
            words = re.findall(r"[A-Za-z][A-Za-z-]*", text)
            letters = "".join(words)
            terms = _tokens(text)
            if len(words) >= 2 and len(letters) >= 5 and letters.isupper() and terms:
                headings.append((index, text, terms))
        target_heading: int | None = None
        if headings:
            df: dict[str, int] = {}
            for _, _, terms in headings:
                for term in terms:
                    df[term] = df.get(term, 0) + 1
            ranked_headings = []
            for index, _, terms in headings:
                overlap = terms & query_terms
                score = sum(np.log((len(headings) + 1) / (df[term] + 1)) + .35
                            for term in overlap)
                if overlap:
                    ranked_headings.append((float(score), index))
            ranked_headings.sort(reverse=True)
            if ranked_headings:
                best_score, best_index = ranked_headings[0]
                next_score = ranked_headings[1][0] if len(ranked_headings) > 1 else 0.0
                if best_score >= .8 and best_score - next_score >= .3:
                    target_heading = best_index
        original_indices = list(range(len(all_spans)))
        if target_heading is not None:
            next_heading = next((index for index, _, _ in headings if index > target_heading), len(all_spans))
            original_indices = list(range(target_heading, next_heading))
        spans = [all_spans[index] for index in original_indices]

        # Page text often contains repeated titles and metadata. Rank compact
        # neighboring line groups so an LO-plus-answer query marks its actual
        # supporting passage instead of every line sharing a generic term.
        candidates: list[tuple[int, int, str, set[str]]] = []
        for start in range(len(spans)):
            for size in (1, 2, 3):
                end = min(len(spans), start + size)
                if end - start != size:
                    continue
                text = " ".join(str(spans[i].get("text", "")) for i in range(start, end)).strip()
                terms = _tokens(text)
                if not text or not terms:
                    continue
                candidates.append((start, end, text, terms))
        if not candidates:
            return []

        # Page-local IDF downweights common words and repeated headers. Keep a
        # lexical floor so semantic similarity alone cannot fabricate a mark.
        doc_freq: dict[str, int] = {}
        for _, _, _, terms in candidates:
            for term in terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1
        weights = {term: np.log((len(candidates) + 1) / (freq + 1)) + .35
                   for term, freq in doc_freq.items()}
        total_query_weight = sum(weights.get(term, 1.0) for term in query_terms)
        lexical = []
        for _, _, _, terms in candidates:
            overlap = query_terms & terms
            matched_weight = sum(weights.get(term, 1.0) for term in overlap)
            lexical.append(matched_weight / max(total_query_weight, 1e-6))

        query_vector: np.ndarray | None = None
        candidate_vectors: np.ndarray | None = None
        try:
            vectors = np.asarray(self.encoder.encode([query] + [c[2] for c in candidates]), dtype=np.float32)
            if vectors.ndim == 2 and vectors.shape[0] == len(candidates) + 1:
                query_vector, candidate_vectors = vectors[0], vectors[1:]
        except Exception:
            # Lexical ranking remains available if the shared local encoder is
            # not initialized; the viewer still never calls a remote service.
            pass
        semantic = (candidate_vectors @ query_vector if candidate_vectors is not None and query_vector is not None
                    else np.zeros(len(candidates), dtype=np.float32))
        has_lexical_support = any(bool(query_terms & terms) and float(semantic[i]) >= .16
                                  for i, (_, _, _, terms) in enumerate(candidates))
        preliminary = np.asarray(semantic) + np.asarray(lexical) * .28
        top = np.argsort(preliminary)[::-1][:min(36, len(candidates))].tolist()
        rerank_scores: dict[int, float] = {}
        try:
            scores = self.reranker.score(query, [candidates[i][2] for i in top])
            rerank_scores = {index: float(score) for index, score in zip(top, scores)}
        except Exception:
            pass

        ranked: list[tuple[float, int, int, int]] = []
        for i, (start, end, _, terms) in enumerate(candidates):
            if i not in rerank_scores and i not in top:
                continue
            overlap_count = len(query_terms & terms)
            similarity = float(semantic[i])
            # Synonyms are common between a learning objective and its source
            # slide. Permit a strong local embedding match without exact word
            # overlap, while refusing weak semantic-only matches.
            if overlap_count == 0:
                if has_lexical_support or similarity < .39:
                    continue
            if overlap_count and similarity < .16 and lexical[i] < .07:
                continue
            cross = rerank_scores.get(i, -10.0)
            # Exact term coverage and the shared encoder provide stable ranking;
            # use the reranker only as a small tie-breaker across adjacent lines.
            score = .58 * similarity + .40 * lexical[i] + .02 * max(-10.0, min(0.0, cross)) / 10
            ranked.append((score, start, end, i))
        ranked.sort(reverse=True)
        selected: list[int] = []
        groups: list[tuple[int, int]] = []
        for score, start, end, _ in ranked:
            if score < .08:
                break
            if any(start < old_end and end > old_start for old_start, old_end in groups):
                continue
            group = set(range(start, end))
            direct = {
                index for index in group
                if (query_terms & _tokens(str(spans[index].get("text", ""))))
                and not (_tokens(str(spans[index].get("text", ""))) <= _GENERIC_HEADING_TERMS)
            }
            if direct:
                group = direct
            if len(selected) + len(group) > 8:
                continue
            selected.extend(sorted(group))
            groups.append((start, end))
            if len(groups) == 3:
                break
        return sorted(original_indices[index] for index in selected)

    def _printed_page(self, canonical: str, physical_page: int,
                      layer: dict[str, Any]) -> int | None:
        entry = self.documents.get(canonical, {})
        raw_offset = entry.get("printedPageOffset")
        if raw_offset is None:
            return None
        try:
            offset = int(raw_offset)
        except (TypeError, ValueError):
            return None
        expected = physical_page - offset
        # Front matter has no printed Arabic page number. The edition-specific
        # offsets are verified against the mapped physical/printed pairs; use
        # the offset only after its front matter and return None before it.
        return expected if expected >= 1 else None

    def _page_url(self, canonical: str, page: int) -> str:
        entry = self.documents[canonical]
        base = str(entry.get("url") or "")
        if not base:
            base = next((str(alias) for alias in entry.get("aliases", [])
                         if normalize_alias(alias).endswith(".pdf")), "")
        if not base:
            return ""
        return base.split("#", 1)[0] + "#page=" + str(page)

    @staticmethod
    def _slug(canonical: str) -> str:
        if canonical == "First Aid":
            return "first-aid"
        if canonical == "Pathoma":
            return "pathoma"
        safe = re.sub(r"[^a-z0-9-]+", "-", canonical.casefold()).strip("-")
        suffix = __import__("hashlib").sha256(canonical.encode()).hexdigest()[:8]
        return f"{safe[:48] or 'source'}-{suffix}"
