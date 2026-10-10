"""Private adapter for the existing LLU Neuro/Psych course module.

The source is either a private local HTML bundle containing ``const DATA =``
JSON or the already-running loopback course app. Imported scripts are never
executed. ``data()`` returns the complete DATA object except for its ``assets``
member; markup strings are reduced to passive HTML. Referenced images are
resolved separately by ``asset()``.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from StudyApp.app_paths import default_data_dir
from StudyApp.progress_store import ProgressStore


MODULE_URL = "http://127.0.0.1:8770/"
PROJECT = Path(__file__).resolve().parent.parent
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SEARCH_BYTES = 256 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_MARKUP_CHARS = 8_000_000
DEFAULT_TIMEOUT = 30.0
_ASSIGNMENT = re.compile(r"const\s+DATA\s*=")
_ANKI_ASSIGNMENT = re.compile(r"(?:const\s+ANKI_LIBRARY|window\.ANKI_CONTEXT)\s*=")
_ASSET_REFERENCE = re.compile(r"@asset:(asset\d+)")
_IMAGE_SOURCE = re.compile(r"<\s*img\b[^>]*?(?<![-\w:])src\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))", re.I)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".php"}
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024
_ASSET_ID = re.compile(r"asset\d+\Z")
_IMAGE_DATA = re.compile(r"data:image/(?:png|jpe?g|gif|webp);base64,", re.I)
_SVG_TAGS = {"svg", "g", "path", "circle", "rect", "line", "polyline", "polygon", "ellipse",
             "text", "tspan", "defs", "linearGradient", "radialGradient", "stop", "clipPath",
             "mask", "pattern", "use", "title", "desc"}
_SVG_ATTRS = {"id", "class", "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2",
              "cx", "cy", "r", "rx", "ry", "d", "points", "transform", "fill", "fill-rule",
              "fill-opacity", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
              "stroke-opacity", "opacity", "offset", "stop-color", "stop-opacity", "clip-path",
              "mask", "patternUnits", "gradientUnits", "gradientTransform", "preserveAspectRatio",
              "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline", "href"}
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math",
                 "form", "input", "button", "select", "option", "textarea",
                 "video", "audio", "canvas", "template", "noscript"}
_ALLOWED_TAGS = {"p", "div", "span", "a", "b", "i", "u", "em", "strong", "br",
                 "img", "table", "tr", "td", "th", "thead", "tbody", "tfoot",
                 "ul", "ol", "li", "sub", "sup", "blockquote", "code", "pre",
                 "mark", "del", "ins", "details", "summary", "h1", "h2", "h3",
                 "h4", "h5", "h6", "hr"}
_VOID_TAGS = {"br", "img", "hr"}


class _DataReader:
    """Read a bounded source, then decode one DATA JSON value in C-backed JSON."""

    def __init__(self, stream: Any, *, max_bytes: int = MAX_SOURCE_BYTES,
                 chunk_size: int = 262_144, timeout: float = DEFAULT_TIMEOUT):
        self.stream = stream
        self.max_bytes = max_bytes
        self.chunk_size = chunk_size
        self.deadline = time.monotonic() + timeout
        self.source_bytes = b""
    def parse(self, *, search_limit: int = MAX_SEARCH_BYTES) -> dict[str, Any]:
        chunks: list[bytes] = []
        total = 0
        while True:
            if time.monotonic() >= self.deadline:
                raise TimeoutError("course module read exceeded its time limit")
            chunk = self.stream.read(self.chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_bytes:
                raise ValueError("course module exceeds the source size limit")
            chunks.append(chunk)
        self.source_bytes = b"".join(chunks)
        text = self.source_bytes.decode("utf-8")
        match = _ASSIGNMENT.search(text[:search_limit + 32])
        if not match or len(text[:match.start()].encode("utf-8")) > search_limit:
            raise ValueError("course DATA assignment was not found within the search limit")
        try:
            value, _end = json.JSONDecoder().raw_decode(text, match.end())
        except json.JSONDecodeError:
            raise ValueError("course DATA contains invalid or incomplete JSON") from None
        if not isinstance(value, dict):
            raise ValueError("course DATA assignment is not a JSON object")
        return value


class _CourseBundleUnavailable(ValueError):
    """Path-free error for a bundle the user explicitly selected."""


def extract_data(source: bytes | str, *, max_bytes: int = MAX_SOURCE_BYTES) -> dict[str, Any]:
    """Extract a complete DATA object from HTML text/bytes without executing it."""
    raw = source.encode("utf-8") if isinstance(source, str) else source
    if len(raw) > max_bytes:
        raise ValueError("course module exceeds the source size limit")
    import io
    return _DataReader(io.BytesIO(raw), max_bytes=max_bytes).parse()


def extract_anki(source: bytes | str, *, max_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Read an optional Anki JSON assignment from HTML/JS without evaluating it."""
    raw = source.encode("utf-8") if isinstance(source, str) else source
    if len(raw) > max_bytes:
        raise ValueError("Anki library exceeds the source size limit")
    text = raw.decode("utf-8")
    match = _ANKI_ASSIGNMENT.search(text)
    if not match:
        raise ValueError("Anki JSON assignment was not found")
    value, _end = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
    if not isinstance(value, dict):
        raise ValueError("Anki assignment is not a JSON object")
    return value


class _PassiveHTML(HTMLParser):
    def __init__(self, asset_tokens: dict[str, str], local_image_tokens: dict[str, str],
                 document_urls: dict[str, str]):
        super().__init__(convert_charrefs=True)
        self.asset_tokens = asset_tokens
        self.local_image_tokens = local_image_tokens
        self.document_urls = document_urls
        self.output: list[str] = []
        self.stack: list[tuple[str, bool, bool]] = []
        self.drop_depth = 0

    def _url(self, value: str, *, image: bool = False) -> str:
        value = html.unescape(value).strip()
        if not value:
            return ""
        if value.startswith("#"):
            return value
        known_document = _known_document_url(value, self.document_urls)
        if known_document:
            return known_document
        if image and re.fullmatch(r"@asset:asset\d+", value):
            return value
        if image and _IMAGE_DATA.match(value) and len(value) <= MAX_ASSET_BYTES:
            return value
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme or value.startswith("//"):
            return ""
        # Relative links are same-origin when resolved by the existing module.
        if value.startswith(("/", "./", "../")):
            return value if not image else ""
        return "" if image else value

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            self.stack.append((tag, False, False))
            return
        if tag in _DROP_CONTENT:
            self.stack.append((tag, False, True))
            self.drop_depth += 1
            return
        if tag not in _ALLOWED_TAGS:
            self.stack.append((tag, False, False))
            return
        values = dict(attrs)
        safe: list[tuple[str, str]] = []
        element_id = values.get("id")
        if element_id and re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", element_id):
            safe.append(("id", element_id))
        cls = values.get("class")
        if cls and re.fullmatch(r"[A-Za-z0-9_ -]{1,256}", cls):
            safe.append(("class", " ".join(cls.split())))
        for key, value in attrs:
            key = key.lower()
            if not key.startswith("data-") or not re.fullmatch(r"data-[a-z0-9_-]{1,64}", key) or value is None or len(value) > 512:
                continue
            if re.search(r"(?:^|[/\\])(?:Users|home|private|var)(?:[/\\])|^file://", value, re.I):
                continue
            if key.endswith(("-url", "-href", "-src")):
                value = self._url(value, image=key.endswith("-src"))
                if not value:
                    continue
            elif value.startswith("@asset:"):
                value = _asset_reference(value, self.asset_tokens)
                if not value:
                    continue
            safe.append((key, value))
        for key in ("alt", "title"):
            if values.get(key) is not None:
                safe.append((key, values[key] or ""))
        if tag == "a" and values.get("href"):
            href = self._url(values["href"] or "")
            if href:
                safe.append(("href", href))
        if tag == "img" and values.get("src"):
            source = html.unescape(values["src"] or "").strip()
            if source.startswith("@asset:"):
                match = _ASSET_REFERENCE.fullmatch(source)
                token = self.asset_tokens.get(match.group(1)) if match else None
            else:
                token = self.local_image_tokens.get(source)
            src = "/api/course/asset/" + token if token else ""
            if src:
                safe.append(("src", src))
        for key in ("width", "height", "colspan", "rowspan"):
            val = values.get(key)
            limit = 4096 if key in {"width", "height"} else 100
            if val and val.isdigit() and 0 < int(val) <= limit:
                safe.append((key, val))
        attrs_text = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in safe)
        self.output.append(f"<{tag}{attrs_text}>")
        emit_end = tag not in _VOID_TAGS
        self.stack.append((tag, emit_end, False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        idx = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if idx is None:
            return
        closing = self.stack[idx:]
        del self.stack[idx:]
        for name, emit, drop in reversed(closing):
            if drop:
                self.drop_depth = max(0, self.drop_depth - 1)
            elif emit:
                self.output.append(f"</{name}>")

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            rendered = _ASSET_REFERENCE.sub(lambda match: _asset_reference(match.group(0), self.asset_tokens), data)
            self.output.append(html.escape(rendered, quote=False))

    def finish(self) -> str:
        for name, emit, _drop in reversed(self.stack):
            if emit:
                self.output.append(f"</{name}>")
        return "".join(self.output)


def _asset_reference(value: str, tokens: dict[str, str]) -> str:
    match = re.fullmatch(r"@asset:(asset\d+)", value)
    return "/api/course/asset/" + tokens[match.group(1)] if match and match.group(1) in tokens else ""


def _sanitize_markup(value: str, asset_tokens: dict[str, str], local_image_tokens: dict[str, str],
                     document_urls: dict[str, str]) -> str:
    if len(value) > MAX_MARKUP_CHARS:
        raise ValueError("course markup field exceeds the size limit")
    parser = _PassiveHTML(asset_tokens, local_image_tokens, document_urls)
    parser.feed(value)
    parser.close()
    return parser.finish()


def _sanitize_tree(value: Any, asset_tokens: dict[str, str], local_image_tokens: dict[str, str],
                   document_urls: dict[str, str], document_route_by_id: dict[str, str], key: str = "") -> Any:
    if isinstance(value, dict):
        result = {child_key: _sanitize_tree(item, asset_tokens, local_image_tokens, document_urls,
                                            document_route_by_id, child_key)
                  for child_key, item in value.items()}
        if isinstance(value.get("id"), str) and isinstance(value.get("aliases"), list):
            route = document_route_by_id.get(value["id"])
            if route:
                aliases = result.get("aliases")
                if isinstance(aliases, list) and route not in aliases:
                    aliases.append(route)
        return result
    if isinstance(value, list):
        if key == "aliases":
            safe_aliases = []
            for item in value:
                if not isinstance(item, str):
                    continue
                if _is_private_path_alias(item):
                    route = _known_document_url(item, document_urls)
                    if route and route not in safe_aliases:
                        safe_aliases.append(route)
                else:
                    safe_aliases.append(item)
            return safe_aliases
        return [_sanitize_tree(item, asset_tokens, local_image_tokens, document_urls,
                               document_route_by_id, key) for item in value]
    if isinstance(value, str) and key.lower() in {"path", "source_path", "absolute_path", "pdf_path", "local_path", "screenshot", "file_path"}:
        return _known_document_url(value, document_urls) or ""
    if isinstance(value, str) and (key.lower().endswith(("url", "href")) or key.lower() in {"href", "src"}):
        # These fields are metadata too, so enforce the same origin boundary
        # as markup attributes. Keep only same-origin relative navigation.
        if value.startswith("@asset:"):
            return _asset_reference(value, asset_tokens)
        document_url = _known_document_url(value, document_urls)
        if document_url:
            return document_url
        if key.lower() == "src":
            token = local_image_tokens.get(value.strip())
            return "/api/course/asset/" + token if token else ""
        safe = _PassiveHTML(asset_tokens, local_image_tokens, document_urls)._url(value, image=key.lower() == "src")
        return safe
    if isinstance(value, str) and re.search(r"<\s*/?\s*[A-Za-z][^>]*>", value):
        return _sanitize_markup(value, asset_tokens, local_image_tokens, document_urls)
    if isinstance(value, str) and _ASSET_REFERENCE.search(value):
        return _ASSET_REFERENCE.sub(lambda match: _asset_reference(match.group(0), asset_tokens), value)
    if isinstance(value, str):
        if _is_private_path_alias(value):
            return _known_document_url(value, document_urls) or ""
        return value
    return value


def _private_roots(bundle: Path | None, extra: list[Path] | None = None) -> list[Path]:
    roots = [bundle.parent.resolve()] if bundle else []
    roots.extend(Path(item).expanduser().resolve() for item in (extra or []))
    # De-duplicate while retaining the configured priority.
    return list(dict.fromkeys(roots))


def _inside(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _image_sources(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _image_sources(item)
    elif isinstance(value, list):
        for item in value:
            yield from _image_sources(item)
    elif isinstance(value, str):
        for match in _IMAGE_SOURCE.finditer(value):
            yield next((group for group in match.groups() if group is not None), "").strip()


def _image_tag_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_image_tag_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_image_tag_count(item) for item in value)
    if isinstance(value, str):
        return len(re.findall(r"<\s*img\b", value, re.I))
    return 0


def _is_private_path_alias(value: str) -> bool:
    parsed = urllib.parse.urlsplit(value)
    return (parsed.scheme.lower() == "file" or Path(urllib.parse.unquote(parsed.path)).is_absolute()
            or bool(re.match(r"^[A-Za-z]:[\\/]", value)))


def _local_document_path(source: str, roots: list[Path]) -> Path | None:
    if not source or source.startswith("//"):
        return None
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme == "file" and not parsed.netloc:
        decoded = urllib.parse.unquote(parsed.path)
    elif parsed.scheme or parsed.netloc:
        return None
    else:
        decoded = urllib.parse.unquote(parsed.path)
    path = Path(decoded).expanduser()
    candidates = [path] if path.is_absolute() else [root / path for root in roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if (resolved.suffix.lower() == ".pdf" and _inside(resolved, roots)
                    and resolved.is_file() and resolved.stat().st_size <= MAX_DOCUMENT_BYTES):
                return resolved
        except OSError:
            continue
    return None


def _known_document_url(value: str, document_urls: dict[str, str]) -> str:
    """Return a route only for an exact registered alias; preserve PDF page fragments."""
    parsed = urllib.parse.urlsplit(value.strip())
    fragment = ("#" + parsed.fragment) if parsed.fragment else ""
    candidates = [value.strip(), urllib.parse.unquote(value.strip()),
                  urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")),
                  urllib.parse.unquote(parsed.path)]
    for candidate in candidates:
        route = document_urls.get(candidate)
        if route:
            return route + fragment
    return ""


def _collect_source_aliases(value: Any, output: dict[str, str]) -> None:
    if isinstance(value, dict):
        identity, aliases = value.get("id"), value.get("aliases")
        if isinstance(identity, str) and isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str):
                    output.setdefault(alias, identity)
        for child in value.values():
            _collect_source_aliases(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_source_aliases(child, output)


def _document_source_strings(record: Any):
    """Yield strings that may identify a source, without evaluating content."""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, str) and (key.lower() in {
                    "url", "href", "src_url", "path", "source_path", "absolute_path",
                    "pdf_path", "local_path", "file_path"}):
                yield value
            elif isinstance(value, (dict, list)):
                yield from _document_source_strings(value)
    elif isinstance(record, list):
        for value in record:
            yield from _document_source_strings(value)


def _register_documents(documents: Any, roots: list[Path]) -> tuple[dict[str, str], dict[str, str], dict[str, Path]]:
    """Map exact known aliases to opaque routes and routes back to safe paths."""
    urls: dict[str, str] = {}
    routes_by_id: dict[str, str] = {}
    paths: dict[str, Path] = {}
    if not isinstance(documents, list):
        return urls, routes_by_id, paths
    for record in documents:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        document_id = record["id"]
        resolved_path = None
        aliases = record.get("aliases") if isinstance(record.get("aliases"), list) else []
        source_strings = list(aliases) + list(_document_source_strings(record))
        for source in source_strings:
            if isinstance(source, str):
                resolved_path = _local_document_path(source, roots)
                if resolved_path:
                    break
        if not resolved_path:
            continue
        token = secrets.token_urlsafe(24)
        route = f"/api/course/document/{token}.pdf"
        routes_by_id[document_id] = route
        paths[token] = resolved_path
        for source in source_strings:
            candidate = _local_document_path(source, roots) if isinstance(source, str) else None
            if candidate == resolved_path:
                urls.setdefault(source, route)
                urls.setdefault(urllib.parse.unquote(source), route)
    return urls, routes_by_id, paths


def _decode_image_data_url(value: str) -> tuple[bytes, str] | None:
    match = _IMAGE_DATA.match(value)
    if not match or len(value) > MAX_ASSET_BYTES * 4 // 3 + 128:
        return None
    mime_type = value[5:match.end() - 8].lower()
    try:
        body = base64.b64decode(value[match.end():], validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    return (body, mime_type) if len(body) <= MAX_ASSET_BYTES else None


def _local_image_path(source: str, roots: list[Path]) -> Path | None:
    if not source or source.startswith("//"):
        return None
    parsed = urllib.parse.urlsplit(source)
    if parsed.scheme == "file" and not parsed.netloc:
        decoded = urllib.parse.unquote(parsed.path)
    elif parsed.scheme or parsed.netloc:
        return None
    else:
        decoded = urllib.parse.unquote(parsed.path)
    path = Path(decoded).expanduser()
    candidates = [path] if path.is_absolute() else [root / path for root in roots]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if (resolved.suffix.lower() in _IMAGE_SUFFIXES and _inside(resolved, roots)
                    and resolved.is_file() and resolved.stat().st_size <= MAX_ASSET_BYTES
                    and (resolved.suffix.lower() != ".php" or _image_mime_for_path(resolved) == "image/jpeg")):
                return resolved
        except OSError:
            continue
    return None


def _image_mime_for_path(path: Path) -> str:
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml"}.get(path.suffix.lower())
    if mime:
        return mime
    # Some legacy image aliases use a .php suffix. Never execute or serve that
    # file as PHP: accept it only when its bytes are a static JPEG image.
    try:
        return "image/jpeg" if path.open("rb").read(3) == b"\xff\xd8\xff" else ""
    except OSError:
        return ""


def _safe_svg(body: bytes) -> bytes | None:
    """Keep inert vector drawing content while removing active/external XML."""
    if len(body) > MAX_ASSET_BYTES:
        return None
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError):
        return None
    def clean(element):
        local_tag = element.tag.rsplit("}", 1)[-1]
        if local_tag not in _SVG_TAGS:
            return False
        element.tag = local_tag
        safe_attributes = {}
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1]
            if name not in _SVG_ATTRS or name.lower().startswith("on"):
                continue
            if name == "href" and not value.startswith("#"):
                continue
            if re.search(r"(?:url\s*\(|javascript:|data:|https?:|file:|expression\s*\()", value, re.I):
                continue
            safe_attributes[name] = value[:8192]
        element.attrib.clear()
        element.attrib.update(safe_attributes)
        for child in list(element):
            if not clean(child):
                element.remove(child)
        return True
    if not clean(root) or root.tag != "svg":
        return None
    return ET.tostring(root, encoding="utf-8")


class CourseModule:
    """Read the existing full course DATA and share its existing user progress.

    Bundle discovery order is constructor argument, ``LLU_NEURO_PSYCH_BUNDLE``,
    then a private ``course-module.json`` config in LLU Study app data. A config
    may contain ``bundle_path`` and optional ``asset_roots``. With no bundle,
    data is read from the fixed loopback module URL. The progress database is
    the exact LLU Study ``ProgressStore`` default unless injected for tests.

    Rendering contract: all HTML-like string fields returned by ``data()`` are
    sanitized passive HTML. Render them only through the app's established safe
    markup path. Do not use ``innerHTML`` with any non-sanitized source string.
    Assets are excluded from ``data()`` and fetched through ``asset(id)``.
    """

    def __init__(self, bundle_path: str | os.PathLike[str] | None = None, *,
                 config_path: str | os.PathLike[str] | None = None,
                 module_url: str = MODULE_URL, progress_store: ProgressStore | None = None,
                 asset_roots: list[str | os.PathLike[str]] | None = None,
                 public_paths: list[str | os.PathLike[str]] | None = None,
                 timeout: float = DEFAULT_TIMEOUT, max_source_bytes: int = MAX_SOURCE_BYTES):
        self.module_url = module_url
        self.timeout = timeout
        self.max_source_bytes = max_source_bytes
        self.config_path = Path(config_path).expanduser() if config_path is not None else self._default_config_path()
        self._bundle_was_configured = bundle_path is not None
        self.bundle_path = Path(bundle_path).expanduser() if bundle_path is not None else None
        self.config_asset_roots = [Path(p).expanduser() for p in (asset_roots or [])]
        self.public_paths = [Path(p).expanduser() for p in public_paths] if public_paths is not None else None
        self._data: dict[str, Any] | None = None
        self._assets: dict[str, Any] = {}
        self._asset_tokens: dict[str, str] = {}
        self._token_assets: dict[str, str] = {}
        self._local_image_tokens: dict[str, str] = {}
        self._extra_assets: dict[str, tuple[bytes | Path, str]] = {}
        self._image_diagnostics = {"figure_count": 0, "figures_missing_or_removed_src": 0,
                                   "anki_figure_count": 0, "anki_figures_missing_or_removed_src": 0}
        self._private_source_aliases: dict[str, str] = {}
        self._safe_source_aliases: dict[str, str] = {}
        self._document_urls: dict[str, str] = {}
        self._document_route_by_id: dict[str, str] = {}
        self._document_paths: dict[str, Path] = {}
        self._anki: dict[str, Any] | None = None
        self._bundle: Path | None = None
        self._roots: list[Path] = []
        self._lock = threading.RLock()
        self._store = progress_store or ProgressStore()
        self._owns_store = progress_store is None

    @staticmethod
    def _default_config_path() -> Path:
        # Same private per-user directory used by LLU Study for its SQLite state.
        return default_data_dir() / "course-config.json"

    def _configured_bundle(self) -> tuple[Path | None, list[Path], bool]:
        if self._bundle_was_configured:
            return self.bundle_path, self.config_asset_roots, True
        if "LLU_NEURO_PSYCH_BUNDLE" in os.environ:
            env_bundle = os.environ["LLU_NEURO_PSYCH_BUNDLE"]
            if not env_bundle.strip():
                raise _CourseBundleUnavailable("configured course bundle is unavailable")
            return Path(env_bundle).expanduser(), self.config_asset_roots, True
        env_config = os.environ.get("LLU_NEURO_PSYCH_CONFIG", "")
        env_configured = bool(env_config.strip())
        config = Path(env_config).expanduser() if env_configured else self.config_path
        try:
            raw = json.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if env_configured:
                raise _CourseBundleUnavailable("course bundle configuration is unavailable") from None
            return None, self.config_asset_roots, False
        except (OSError, ValueError, UnicodeDecodeError):
            raise _CourseBundleUnavailable("course bundle configuration is invalid") from None
        if not isinstance(raw, dict):
            raise _CourseBundleUnavailable("course bundle configuration is invalid")
        path = raw.get("bundle_path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise _CourseBundleUnavailable("course bundle configuration is invalid")
        roots = raw.get("asset_roots", [])
        if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
            raise _CourseBundleUnavailable("course bundle configuration is invalid")
        return (Path(path).expanduser() if path else None,
                self.config_asset_roots + [Path(item).expanduser() for item in roots],
                isinstance(path, str))

    def _load(self) -> None:
        if self._data is not None:
            return
        bundle, extra_roots, bundle_required = self._configured_bundle()
        raw = None
        anki = {}
        source = ""
        if bundle_required:
            try:
                if bundle is None:
                    raise ValueError("missing configured bundle")
                bundle = bundle.resolve()
                if not bundle.is_file() or bundle.stat().st_size > self.max_source_bytes:
                    raise ValueError("configured bundle is missing or too large")
                with bundle.open("rb") as stream:
                    reader = _DataReader(stream, max_bytes=self.max_source_bytes, timeout=self.timeout)
                    raw = reader.parse()
                self._bundle = bundle
                self._roots = _private_roots(bundle, extra_roots)
                source = "bundle"
                try:
                    anki = extract_anki(reader.source_bytes)
                except ValueError:
                    anki = {}
                if not anki:
                    for sibling in (bundle.parent / "anki_context_data.js", bundle.parent / "anki_library.js"):
                        try:
                            if sibling.is_file() and sibling.stat().st_size <= 64 * 1024 * 1024:
                                anki = extract_anki(sibling.read_bytes())
                                break
                        except (OSError, ValueError, UnicodeDecodeError):
                            continue
            except (OSError, ValueError, TimeoutError, RuntimeError):
                raise _CourseBundleUnavailable("configured course bundle is unavailable") from None
        if raw is None:
            try:
                parsed = urllib.parse.urlsplit(self.module_url)
                if (parsed.scheme, parsed.hostname, parsed.port, parsed.path) != ("http", "127.0.0.1", 8770, "/"):
                    raise ValueError("course module URL is outside the loopback allowlist")
                request = Request(self.module_url, headers={"Accept": "text/html"}, method="GET")
                with urlopen(request, timeout=self.timeout) as response:
                    reader = _DataReader(response, max_bytes=self.max_source_bytes, timeout=self.timeout)
                    raw = reader.parse()
                self._roots = _private_roots(None, extra_roots)
                source = "live"
                try:
                    anki = extract_anki(reader.source_bytes)
                except ValueError:
                    anki = {}
            except (OSError, ValueError, TimeoutError):
                raw = None
        if raw is None:
            for public_path in self._public_candidates():
                try:
                    if public_path.is_file() and public_path.stat().st_size <= self.max_source_bytes:
                        candidate = json.loads(public_path.read_text(encoding="utf-8"))
                        if isinstance(candidate, dict) and isinstance(candidate.get("pages"), list):
                            raw = candidate
                            source = "public"
                            self._roots = []
                            break
                except (OSError, ValueError, UnicodeDecodeError):
                    continue
            if raw is None:
                raise FileNotFoundError("course module and public course data are unavailable")
        assets = raw.get("assets")
        self._assets = assets if isinstance(assets, dict) else {}
        self._asset_tokens = {key: secrets.token_urlsafe(18) for key in self._assets
                              if isinstance(key, str) and _ASSET_ID.fullmatch(key)}
        self._token_assets = {token: asset_id for asset_id, token in self._asset_tokens.items()}
        # Register only image files beneath the explicit private bundle roots.
        # This covers legacy relative image references absent from DATA.assets,
        # including images in the optional companion Anki JSON.
        image_sources = list(_image_sources(raw))
        for image_source in image_sources:
            self._register_image_source(image_source)
        for image_source in _image_sources(anki):
            self._register_image_source(image_source)
        figure_count = _image_tag_count(raw)
        routed_count = sum(
            bool(self._asset_tokens.get(source[7:])) if source.startswith("@asset:")
            else source in self._local_image_tokens
            for source in image_sources
        )
        anki_image_sources = list(_image_sources(anki))
        anki_routed = sum(
            bool(self._asset_tokens.get(source[7:])) if source.startswith("@asset:")
            else source in self._local_image_tokens
            for source in anki_image_sources
        )
        self._image_diagnostics = {
            "figure_count": figure_count,
            "figures_missing_or_removed_src": max(0, figure_count - routed_count),
            "anki_figure_count": _image_tag_count(anki),
            "anki_figures_missing_or_removed_src": max(0, _image_tag_count(anki) - anki_routed),
        }
        self._anki = anki if isinstance(anki, dict) else {}
        catalog = raw.get("source_catalog", {})
        _collect_source_aliases(catalog, self._private_source_aliases)
        documents = catalog.get("documents", []) if isinstance(catalog, dict) else []
        self._document_urls, self._document_route_by_id, self._document_paths = _register_documents(
            documents, self._roots)
        self._safe_source_aliases = {}
        for alias, document_id in self._private_source_aliases.items():
            if not _is_private_path_alias(alias):
                self._safe_source_aliases.setdefault(alias, document_id)
            route = _known_document_url(alias, self._document_urls)
            if route:
                self._safe_source_aliases.setdefault(route.split("#", 1)[0], document_id)
        safe_data = {key: value for key, value in raw.items() if key != "assets"}
        safe_data["assets"] = {}
        self._data = _sanitize_tree(safe_data, self._asset_tokens, self._local_image_tokens,
                                    self._document_urls, self._document_route_by_id)
        self._anki = _sanitize_tree(self._anki, self._asset_tokens, self._local_image_tokens,
                                    self._document_urls, self._document_route_by_id)
        self._source = source

    def _public_candidates(self) -> list[Path]:
        if self.public_paths is not None:
            return self.public_paths
        return [PROJECT / "CourseModule" / "data" / "public-study-guide.json",
                PROJECT / "data" / "public-study-guide.json"]

    def source_document_id(self, alias: str) -> str | None:
        """Resolve an exact private alias or safe relative alias to a stable ID."""
        if not isinstance(alias, str):
            return None
        with self._lock:
            self._load()
            return (self._private_source_aliases.get(alias) or self._safe_source_aliases.get(alias)
                    or self._safe_source_aliases.get(alias.split("#", 1)[0]))

    def document_pdf(self, token: str) -> dict[str, Any] | None:
        """Resolve only an opaque token registered from a known source PDF.

        The returned path is an internal server value. Never serialize it into
        an API response or accept a caller-supplied path here.
        """
        if not isinstance(token, str):
            return None
        token = token.removesuffix(".pdf")
        with self._lock:
            self._load()
            path = self._document_paths.get(token)
            if path is None:
                return None
            try:
                resolved = path.resolve()
                if (resolved.suffix.lower() != ".pdf" or not _inside(resolved, self._roots)
                        or not resolved.is_file() or resolved.stat().st_size > MAX_DOCUMENT_BYTES):
                    return None
                return {"kind": "file", "path": resolved}
            except OSError:
                return None

    def _register_image_source(self, source: str) -> None:
        if not source or source.startswith("@asset:") or source in self._local_image_tokens:
            return
        if source.startswith("data:"):
            decoded = _decode_image_data_url(source)
            if decoded is not None:
                token = secrets.token_urlsafe(18)
                self._local_image_tokens[source] = token
                self._extra_assets[token] = decoded
            return
        path = _local_image_path(source, self._roots)
        if path is not None:
            token = secrets.token_urlsafe(18)
            self._local_image_tokens[source] = token
            mime = _image_mime_for_path(path)
            if not mime:
                return
            self._extra_assets[token] = (path, mime)

    def status(self) -> dict[str, Any]:
        with self._lock:
            try:
                self._load()
                data = self._data or {}
                pages = data.get("pages", [])
                return {"available": True, "source": self._source,
                        "chapter_count": len(pages) if isinstance(pages, list) else 0,
                        "objective_count": len(data.get("objectives", [])) if isinstance(data.get("objectives"), list) else 0,
                        "question_count": len(data.get("questions", [])) if isinstance(data.get("questions"), list) else 0,
                        "asset_count": len(self._assets), **self._image_diagnostics}
            except Exception as exc:
                # Never expose a configured private bundle path through status.
                if isinstance(exc, _CourseBundleUnavailable):
                    reason = str(exc)
                else:
                    reason = "course module is unavailable" if isinstance(exc, (OSError, TimeoutError)) else str(exc)[:160]
                return {"available": False, "source": None, "reason": reason}

    def data(self) -> dict[str, Any]:
        """Return a detached copy of every DATA field except embedded assets."""
        with self._lock:
            self._load()
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def asset(self, identity: str) -> dict[str, Any] | None:
        """Resolve an opaque asset token to bounded bytes and MIME, without a path."""
        if not isinstance(identity, str):
            return None
        with self._lock:
            self._load()
            extra = self._extra_assets.get(identity)
            if extra is not None:
                payload, mime_type = extra
                if isinstance(payload, Path):
                    try:
                        if not _inside(payload.resolve(), self._roots) or payload.stat().st_size > MAX_ASSET_BYTES:
                            return None
                        body = payload.read_bytes()
                    except OSError:
                        return None
                else:
                    body = payload
                if mime_type == "image/svg+xml":
                    body = _safe_svg(body)
                    if body is None:
                        return None
                return {"mime_type": mime_type, "body": body, "size": len(body)}
            identity = self._token_assets.get(identity)
            if not isinstance(identity, str) or not _ASSET_ID.fullmatch(identity):
                return None
            if identity not in self._assets:
                return None
            value = self._assets[identity]
            if isinstance(value, str):
                decoded = _decode_image_data_url(value)
                if decoded is not None:
                    body, mime_type = decoded
                    return {"mime_type": mime_type, "body": body, "size": len(body)}
                candidate = Path(value).expanduser()
            elif isinstance(value, dict):
                data_url = value.get("data_url", value.get("data"))
                decoded = _decode_image_data_url(data_url) if isinstance(data_url, str) else None
                if decoded is not None:
                    body, mime_type = decoded
                    return {"mime_type": mime_type, "body": body, "size": len(body)}
                path_value = value.get("path", value.get("src", value.get("url")))
                if not isinstance(path_value, str):
                    return None
                candidate = Path(path_value).expanduser()
            else:
                return None
            path = _local_image_path(str(candidate), self._roots)
            if path is None:
                return None
            if path.stat().st_size > MAX_ASSET_BYTES:
                return None
            mime_type = "image/png"
            suffix = path.suffix.lower()
            mime_type = _image_mime_for_path(path) or mime_type
            if isinstance(value, dict):
                declared = value.get("mime_type", value.get("mime"))
                if isinstance(declared, str) and declared in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                    mime_type = declared
            try:
                body = path.read_bytes()
            except OSError:
                return None
            if mime_type == "image/svg+xml":
                body = _safe_svg(body)
                if body is None:
                    return None
            return {"mime_type": mime_type, "body": body, "size": len(body)}

    def anki_data(self) -> dict[str, Any]:
        """Return optional companion Anki JSON after passive markup filtering."""
        with self._lock:
            if self._anki is not None:
                return json.loads(json.dumps(self._anki, ensure_ascii=False))
            self._load()
            return json.loads(json.dumps(self._anki or {}, ensure_ascii=False))

    def load_progress(self) -> dict[str, Any]:
        """Read the shared LLU Study progress state and revision."""
        return self._store.load()

    def patch_progress(self, patch: list[Any] | dict[str, Any], revision: int | None = None) -> dict[str, Any]:
        """Apply a merge/JSON patch to the same LLU Study SQLite store.

        ``revision`` is accepted for endpoint compatibility. ProgressStore
        applies merge-safe patches to the latest state, so stale revisions do
        not replace unrelated user data.
        """
        if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(patch, (dict, list)):
            raise ValueError("progress patch must be an object or operation list")
        return self._store.apply_patch(patch, base_revision=revision)

    def import_progress(self, state: dict[str, Any], replace: bool = True) -> dict[str, Any]:
        """Explicitly import a user-selected progress backup into shared state."""
        if not isinstance(state, dict):
            raise ValueError("imported progress state must be an object")
        if not isinstance(replace, bool):
            raise ValueError("replace must be a boolean")
        return self._store.import_state(state, replace=replace)

    def close(self) -> None:
        if self._owns_store:
            self._store.close()


__all__ = ["CourseModule", "extract_data", "extract_anki", "MAX_SOURCE_BYTES", "MAX_ASSET_BYTES"]
