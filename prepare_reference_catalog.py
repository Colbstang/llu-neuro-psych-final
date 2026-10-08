"""Build a local catalog and semantic page index for the two authorized books.

The generated files stay under reference_index/. Only PDF paths explicitly
listed by the compiled guide, book_pdf_map.json, or an optional imported
source_viewer_catalog.json are eligible for reading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent
INDEX_DIR = ROOT / "reference_index"
CATALOG_PATH = INDEX_DIR / "catalog.json"
INDEX_META_PATH = INDEX_DIR / "metadata.json"
INDEX_VECTOR_PATH = INDEX_DIR / "vectors.npz"
INDEX_SCHEMA = 2
BOOKS = ("First Aid", "Pathoma")
PRINTED_PAGE_OFFSETS = {"First Aid": 19, "Pathoma": 8}


def _compiled_data(root: Path) -> dict[str, Any]:
    source = (root / "index.html").read_text(encoding="utf-8")
    match = re.search(r"\bconst\s+DATA\s*=\s*", source)
    if not match:
        raise RuntimeError("Could not find compiled DATA in index.html")
    data, _ = json.JSONDecoder().raw_decode(source, match.end())
    return data


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _walk_values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    elif isinstance(value, str):
        yield value


def _path_from_candidate(root: Path, candidate: Any) -> Path | None:
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    raw = candidate.strip()
    if raw.lower().startswith(("http://", "https://", "data:")):
        return None
    if raw.lower().startswith("file:"):
        raw = urllib.parse.unquote(urllib.parse.urlsplit(raw).path)
    else:
        raw = urllib.parse.unquote(raw.split("#", 1)[0].split("?", 1)[0])
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if resolved.is_file() and resolved.suffix.casefold() == ".pdf":
        return resolved
    return None


def _source_records(root: Path, data: dict[str, Any]):
    source_files = (
        "study_guide_content.json", "learning_objectives.json", "objective_answers.json",
        "objective_answers_complete.json", "final_bundle.json", "week8_bundle.json",
        "enrichment.json", "comparison_sheets.json", "review_games.json",
        "rapid_pathology.json", "pathology_images.json", "generated_visual_additions.json",
        "source_catalog.json",
    )
    trees = [data]
    for filename in source_files:
        path = root / filename
        if path.is_file():
            try:
                trees.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue

    def walk(node: Any):
        if isinstance(node, dict):
            record = {k: v for k, v in node.items() if isinstance(v, str)}
            candidates = [record.get(k) for k in ("path", "source_path", "absolute_path", "pdf_path", "local_path", "url", "source_url")]
            if any(isinstance(v, str) and ".pdf" in v.casefold() for v in candidates):
                yield record
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    for tree in trees:
        yield from walk(tree)


def build_source_documents(root: Path, data: dict[str, Any],
                           compiled_books: dict[str, Any],
                           local_books: dict[str, Any]) -> list[dict[str, Any]]:
    """Create the path whitelist consumed by the existing exact-source viewer."""
    docs: dict[str, dict[str, Any]] = {}

    for source in _source_records(root, data):
        raw_candidates = [source.get(k) for k in (
            "path", "source_path", "absolute_path", "pdf_path", "local_path", "url", "source_url")]
        resolved = next((p for raw in raw_candidates if (p := _path_from_candidate(root, raw))), None)
        if not resolved:
            continue
        path_key = str(resolved)
        doc_id = hashlib.sha256(path_key.encode()).hexdigest()[:12] + resolved.suffix
        existing = docs.get(doc_id)
        if existing is None:
            label = next((source.get(k) for k in ("title", "label", "source_label", "lecture") if source.get(k)), "")
            if re.search(r"\b(?:pdf\s*)?p(?:age)?\.?\s*\d+\b|\bslide\s*\d+\b", str(label), re.IGNORECASE):
                label = ""
            docs[doc_id] = {
                "id": doc_id, "title": str(label or resolved.stem), "path": path_key,
                "page_count": _pdf_page_count(resolved), "aliases": [],
            }
        entry = docs[doc_id]
        for raw in raw_candidates:
            if isinstance(raw, str) and ".pdf" in raw.casefold():
                # Preserve cited web aliases as identifiers, but only retain a
                # filesystem alias when it resolves to this same approved PDF.
                # This prevents malformed/stale absolute paths from becoming
                # accepted document IDs in the page-view endpoint.
                if raw.casefold().startswith(("http://", "https://")):
                    entry["aliases"].append(raw)
                elif _path_from_candidate(root, raw) == resolved:
                    entry["aliases"].append(raw)
        entry["aliases"].extend([str(resolved), resolved.as_uri()])
        if not entry.get("url"):
            entry["url"] = source.get("source_url") or source.get("url")

    # The two semantic-search books use stable human-readable document IDs.
    for book in BOOKS:
        candidates = []
        for registry in (compiled_books, local_books):
            meta = registry.get(book, {}) if isinstance(registry, dict) else {}
            if isinstance(meta, dict):
                candidates.extend(meta.get(k) for k in ("absolute_path", "pdf_path", "path", "local_path") if meta.get(k))
        path = next((p for raw in candidates if (p := _path_from_candidate(root, raw))), None)
        if not path:
            continue
        key = str(path)
        doc_id = book
        alias_values = [str(path), path.as_uri()]
        for registry in (compiled_books, local_books):
            meta = registry.get(book, {}) if isinstance(registry, dict) else {}
            if not isinstance(meta, dict):
                continue
            for field in ("url", "source_url", "canonical_url"):
                if meta.get(field):
                    alias_values.append(str(meta[field]))
            if isinstance(meta.get("aliases"), list):
                alias_values.extend(str(a) for a in meta["aliases"])
        local_alias_name = "First Aid 2024.pdf" if book == "First Aid" else "Pathoma 2021.pdf"
        alias_values.append("../Core%20Sources/Books/" + urllib.parse.quote(local_alias_name))
        # Do not duplicate a book under the generic path-hash ID.
        hashed_id = hashlib.sha256(key.encode()).hexdigest()[:12] + path.suffix
        # Preserve any aliases discovered while walking compiled citations for
        # this same canonical file (including source URLs that resolve through
        # a local symlink into the book). The generic entry is removed below,
        # so capture it before popping it and merge its verified aliases into
        # the stable First Aid / Pathoma document.
        hashed_existing = docs.get(hashed_id, {})
        existing = docs.get(doc_id, {})
        docs.pop(hashed_id, None)
        docs[doc_id] = {
            "id": doc_id, "title": compiled_books.get(book, {}).get("label", book),
            "path": key, "page_count": _pdf_page_count(path), "book": book,
            "url": compiled_books.get(book, {}).get("url"),
            "aliases": alias_values + existing.get("aliases", []) + hashed_existing.get("aliases", []),
        }

    # Add the stable SHA-path source URLs generated by build_guide.py. This
    # keeps duplicate lecture citations resolvable by ID and by clicked URL.
    for document in docs.values():
        suffix = document["id"]
        if suffix not in BOOKS:
            document["aliases"].append("../Learning%20Objective%20Sources/" + urllib.parse.quote(suffix))
        document["aliases"] = sorted(set(a for a in document["aliases"] if a))

    # The compiled guide links generated hashed aliases. Add a bare basename
    # only when that name identifies exactly one cataloged PDF; collisions
    # remain path-qualified so backend alias lookup cannot choose the wrong
    # document.
    basename_owners: dict[str, set[str]] = {}
    for document in docs.values():
        for alias in document.get("aliases", []):
            parsed = urllib.parse.urlsplit(str(alias))
            raw_path = parsed.path if parsed.scheme or parsed.netloc else str(alias).split("#", 1)[0].split("?", 1)[0]
            basename = Path(urllib.parse.unquote(raw_path).replace("\\", "/")).name
            if basename.casefold().endswith(".pdf"):
                basename_owners.setdefault(basename.casefold(), set()).add(document["id"])
    for document in docs.values():
        for alias in document.get("aliases", []):
            parsed = urllib.parse.urlsplit(str(alias))
            raw_path = parsed.path if parsed.scheme or parsed.netloc else str(alias).split("#", 1)[0].split("?", 1)[0]
            path = urllib.parse.unquote(raw_path).replace("\\", "/")
            if not path.casefold().startswith("../learning objective sources/"):
                continue
            basename = Path(path).name
            if basename_owners.get(basename.casefold()) == {document["id"]}:
                document["aliases"].append(basename)
        document["aliases"] = sorted(set(document.get("aliases", [])))
    return sorted(docs.values(), key=lambda d: (d.get("book", ""), d["title"].casefold(), d["id"]))


def _pdf_page_count(path: Path) -> int | None:
    try:
        if path.read_bytes()[:5] != b"%PDF-":
            return None
        result = subprocess.run(
            ["pdfinfo", str(path)], check=True, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else None


def build_catalog(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    data = _compiled_data(root)
    compiled_books = data.get("book_pages", {}).get("books", {})
    local_map = _read_json(root / "book_pdf_map.json").get("books", {})
    portable = _read_json(root / "source_catalog.json")
    if not portable:
        portable = data.get("source_catalog", {}) if isinstance(data.get("source_catalog", {}), dict) else {}
    legacy = _read_json(root / "source_viewer_catalog.json")
    all_documents = build_source_documents(root, data, compiled_books, local_map)
    aliases_by_book: dict[str, set[str]] = {name: {name} for name in BOOKS}
    urls_by_book: dict[str, set[str]] = {name: set() for name in BOOKS}
    paths_by_book: dict[str, list[str]] = {name: [] for name in BOOKS}
    ids_by_book: dict[str, str] = {name: name for name in BOOKS}
    portable_page_count: dict[str, int | None] = {name: None for name in BOOKS}

    documents = portable.get("documents", []) if isinstance(portable, dict) else []
    if not documents and isinstance(data.get("source_catalog"), dict):
        documents = data["source_catalog"].get("documents", [])
    if isinstance(documents, list):
        for document in documents:
            if not isinstance(document, dict):
                continue
            book = document.get("book")
            if book not in BOOKS:
                marker = " ".join([str(document.get("id", "")), str(document.get("title", ""))]).casefold()
                book = "First Aid" if "first aid" in marker else "Pathoma" if "pathoma" in marker else None
            if book not in BOOKS:
                continue
            if document.get("id"):
                ids_by_book[book] = str(document["id"])
                aliases_by_book[book].add(str(document["id"]))
            if document.get("title"):
                aliases_by_book[book].add(str(document["title"]))
            if document.get("path"):
                paths_by_book[book].append(str(document["path"]))
            if isinstance(document.get("aliases"), list):
                aliases_by_book[book].update(str(a) for a in document["aliases"])
            try:
                portable_page_count[book] = int(document.get("page_count"))
            except (ValueError, TypeError):
                pass

    for book in BOOKS:
        legacy_books = legacy.get("books", {}) if isinstance(legacy, dict) else {}
        for registry in (compiled_books, local_map, legacy_books):
            entry = registry.get(book, {}) if isinstance(registry, dict) else {}
            if not isinstance(entry, dict):
                continue
            for field in ("absolute_path", "pdf_path", "path", "local_path"):
                if entry.get(field):
                    paths_by_book[book].append(str(entry[field]))
            for field in ("url", "source_url", "canonical_url"):
                if entry.get(field):
                    urls_by_book[book].add(str(entry[field]))
            for value in entry.get("aliases", []) if isinstance(entry.get("aliases", []), list) else []:
                aliases_by_book[book].add(str(value))
            for field in ("label", "title", "documentId", "document_id"):
                if entry.get(field):
                    aliases_by_book[book].add(str(entry[field]))

    # Associate only URL aliases that explicitly name one of these book files.
    for value in _walk_values(data):
        if ".pdf" not in value.casefold():
            continue
        folded = value.casefold().replace("%20", " ")
        for book, markers in {
            "First Aid": ("first aid 2024", "firstaid2024", "first aid usmle step 1 (2024)"),
            "Pathoma": ("pathoma 2021", "pathoma - fundamentals of pathology (2021)")
        }.items():
            if any(marker in folded for marker in markers):
                urls_by_book[book].add(value)

    entries: dict[str, Any] = {}
    alias_map: dict[str, str] = {}
    for book in BOOKS:
        resolved_path = next((candidate for raw in paths_by_book[book]
                              if (candidate := _path_from_candidate(root, raw)) is not None), None)
        page_images = compiled_books.get(book, {}).get("images", {}) if isinstance(compiled_books, dict) else {}
        source_url = compiled_books.get(book, {}).get("url", "") if isinstance(compiled_books, dict) else ""
        if not source_url and urls_by_book[book]:
            source_url = sorted(urls_by_book[book])[0]
        aliases = set(aliases_by_book[book]) | urls_by_book[book]
        if resolved_path:
            aliases.update({resolved_path.name, str(resolved_path), resolved_path.as_uri()})
        if source_url:
            aliases.add(source_url)
        page_count = _pdf_page_count(resolved_path) if resolved_path else portable_page_count[book]
        entry = {
            "id": ids_by_book[book],
            "documentId": book,
            "book": book,
            "title": compiled_books.get(book, {}).get("label", book),
            "pdfPath": str(resolved_path) if resolved_path else None,
            "url": source_url,
            "aliases": sorted(a for a in aliases if a),
            "pageCount": page_count,
            "printedPageOffset": PRINTED_PAGE_OFFSETS[book],
            "pageImages": page_images,
            "available": bool(resolved_path),
        }
        entries[book] = entry
        for alias in aliases:
            key = normalize_alias(alias)
            if key:
                alias_map[key] = entry["id"]

    # The portable catalog is the same whitelist consumed by the existing
    # source viewer. Its IDs are also the only valid source_page document IDs.
    by_book = {doc.get("book"): doc for doc in all_documents if doc.get("book") in BOOKS}
    for book in BOOKS:
        if book in by_book:
            entries[book].update({
                "id": by_book[book]["id"], "pdfPath": by_book[book]["path"],
                "pageCount": by_book[book]["page_count"], "url": by_book[book].get("url") or entries[book]["url"],
                "aliases": by_book[book]["aliases"], "available": True,
            })
    for document in all_documents:
        for alias in [document["id"], document["title"], *document.get("aliases", [])]:
            key = normalize_alias(alias)
            if key:
                alias_map[key] = document["id"]

    return {
        "schema": INDEX_SCHEMA,
        "catalogedAt": int(time.time()),
        "books": entries,
        "documents": all_documents,
        "aliases": alias_map,
    }


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


def _page_texts(pdf_path: str, expected_count: int) -> list[str]:
    result = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"],
        check=True, capture_output=True, text=True, timeout=900,
    )
    pages = result.stdout.split("\f")
    while pages and not pages[-1].strip():
        pages.pop()
    if len(pages) < expected_count:
        pages.extend([""] * (expected_count - len(pages)))
    if len(pages) > expected_count:
        pages = pages[:expected_count]
    return pages


def _chunks(page_text: str, max_chars: int = 1300) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in page_text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in lines:
        if buffer and size + len(line) + 1 > max_chars:
            chunks.append(" ".join(buffer))
            # Carry one line as overlap so a definition split at the boundary
            # remains searchable in either chunk.
            carry = buffer[-1]
            buffer, size = [carry], len(carry)
        buffer.append(line)
        size += len(line) + 1
    if buffer:
        chunks.append(" ".join(buffer))
    return chunks


def _looks_like_index_page(text: str) -> bool:
    if re.search(r"\bINDEX\b", text[:1000], re.IGNORECASE):
        return True
    # Pathoma's back-of-book indexes are not consistently labeled. They have
    # dense alphabetic entries followed by printed-page numbers, unlike the
    # prose and tables that carry the clinical source facts.
    page_refs = re.findall(r"[^\n,]{2,50},\s*\d{1,3}\b", text)
    return len(page_refs) >= 15


def prepare_index(root: Path, catalog: dict[str, Any], encoder: Any,
                  batch_size: int = 48) -> dict[str, Any]:
    index_dir = root / "reference_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = index_dir / "metadata.json"
    vectors_path = index_dir / "vectors.npz"
    available = [entry for entry in catalog["books"].values() if entry.get("available")]
    if len(available) != 2:
        raise RuntimeError("Both First Aid and Pathoma PDFs must be cataloged before indexing")
    fingerprints = {entry["documentId"]: {
        "path": entry["pdfPath"], "size": Path(entry["pdfPath"]).stat().st_size,
        "mtime_ns": Path(entry["pdfPath"]).stat().st_mtime_ns, "pageCount": entry["pageCount"]
    } for entry in available}
    fingerprint = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    if metadata_path.exists() and vectors_path.exists():
        existing = _read_json(metadata_path)
        if existing.get("schema") == INDEX_SCHEMA and existing.get("fingerprint") == fingerprint:
            return existing

    records: list[dict[str, Any]] = []
    for entry in available:
        pages = _page_texts(entry["pdfPath"], entry["pageCount"])
        if len(pages) != entry["pageCount"]:
            raise RuntimeError(f"Page count mismatch for {entry['documentId']}: PDF has {entry['pageCount']}, extracted {len(pages)}")
        for physical_page, text in enumerate(pages, start=1):
            for chunk_index, excerpt in enumerate(_chunks(text)):
                records.append({
                    "documentId": entry["documentId"], "physicalPage": physical_page,
                    "chunkIndex": chunk_index, "excerpt": excerpt,
                    "indexPage": _looks_like_index_page(text),
                })

    vectors = []
    started = time.monotonic()
    texts = [record["excerpt"] for record in records]
    for offset in range(0, len(texts), batch_size):
        vectors.append(encoder.encode(texts[offset:offset + batch_size]))
        if offset == 0 or offset % 480 == 0:
            print(f"Encoding reference pages: {min(offset + batch_size, len(texts))}/{len(texts)}", flush=True)
    matrix = np.concatenate(vectors).astype(np.float32) if vectors else np.empty((0, 384), dtype=np.float32)
    metadata = {
        "schema": INDEX_SCHEMA, "fingerprint": fingerprint, "records": records,
        "recordCount": len(records), "books": fingerprints,
        "builtSeconds": round(time.monotonic() - started, 2),
        "encoder": "shared semantic_search.Encoder (all-MiniLM-L6-v2)",
    }
    np.savez_compressed(vectors_path, vectors=matrix)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--index", action="store_true", help="also extract and encode all book pages")
    parser.add_argument("--batch-size", type=int, default=48)
    args = parser.parse_args()
    root = args.root.resolve()
    index_dir = root / "reference_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog(root)
    (index_dir / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    portable = {"documents": [
        {k: v for k, v in doc.items() if k in {"id", "title", "aliases", "page_count", "book", "path", "url"}}
        for doc in catalog.get("documents", [])
    ]}
    (root / "source_viewer_catalog.json").write_text(json.dumps(portable, ensure_ascii=False, indent=2), encoding="utf-8")
    for book, entry in catalog["books"].items():
        availability = f"{entry['pageCount']} pages" if entry.get("available") else "local PDF unavailable"
        print(f"{book}: {availability}; source alias: {entry.get('url') or '(unavailable)'}")
    if args.index:
        sys.path.insert(0, str(root))
        import semantic_search  # reuses the already-configured local encoder
        encoder = semantic_search.Encoder()
        metadata = prepare_index(root, catalog, encoder, max(1, args.batch_size))
        print(f"Ready: {metadata['recordCount']} page chunks across both books")


if __name__ == "__main__":
    main()
