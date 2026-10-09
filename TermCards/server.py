"""Loopback-only static and API server for TermCards."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from .provider import MAX_QUERY_LENGTH, TermProvider
except ImportError:  # Supports ``python TermCards/server.py``.
    from provider import MAX_QUERY_LENGTH, TermProvider


STATIC_ALLOWLIST = frozenset({"index.html", "term_cards.js", "term_cards.css", "data/terms.json"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class TermCardsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], directory: Path, provider: TermProvider):
        super().__init__(address, TermCardsHandler)
        self.directory = Path(directory).resolve()
        self.provider = provider


class TermCardsHandler(BaseHTTPRequestHandler):
    server: TermCardsServer
    protocol_version = "HTTP/1.1"
    server_version = "TermCards/0.1"
    sys_version = ""

    def do_GET(self) -> None:
        if not self._valid_host() or not self._valid_origin():
            self._json(403, {"ok": False, "error": "forbidden", "candidates": []})
            return
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/terms":
            if parsed.query:
                self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            else:
                self._json(200, {"ok": True, "records": self.server.provider.lexicon()})
            return
        if parsed.path == "/api/term":
            self._term(parsed.query)
            return
        if parsed.path.startswith("/api/"):
            self._json(404, {"ok": False, "error": "not_found", "candidates": []})
            return
        self._static(parsed.path)

    def _valid_host(self) -> bool:
        host = self.headers.get("Host", "")
        if not host or len(host) > 255:
            return False
        # urlsplit handles IPv6 brackets. Any hostname other than loopback is refused.
        try:
            parsed = urllib.parse.urlsplit("http://" + host)
            # Accessing port also validates malformed numeric port syntax.
            _ = parsed.port
        except ValueError:
            return False
        return parsed.hostname in LOOPBACK_HOSTS

    def _valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin == "null":
            return True
        if len(origin) > 512:
            return False
        try:
            parsed = urllib.parse.urlsplit(origin)
            _ = parsed.port
        except ValueError:
            return False
        return parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS

    def _term(self, raw_query: str) -> None:
        if len(raw_query) > MAX_QUERY_LENGTH * 6 + 256:
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        try:
            params = urllib.parse.parse_qs(raw_query, keep_blank_values=True, strict_parsing=True,
                                           max_num_fields=4, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        if set(params) not in ({"q"}, {"id"}):
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        key = next(iter(params))
        if len(params[key]) != 1:
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        value = params[key][0]
        if key == "q" and len(value) > MAX_QUERY_LENGTH:
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        if key == "id" and len(value) > 96:
            self._json(400, {"ok": False, "error": "invalid_query", "candidates": []})
            return
        result = self.server.provider.lookup(query=value) if key == "q" else self.server.provider.lookup(record_id=value)
        status = 200 if result.get("ok") else (503 if result.get("error") == "source_unavailable" else 200)
        self._json(status, result)

    def _static(self, raw_path: str) -> None:
        try:
            decoded = urllib.parse.unquote(raw_path, errors="strict")
        except (UnicodeDecodeError, ValueError):
            self._json(404, {"ok": False, "error": "not_found", "candidates": []})
            return
        if decoded in ("", "/"):
            relative = "index.html"
        else:
            relative = decoded.lstrip("/")
        # Reject encoded separators, traversal, query/fragment-like path tricks,
        # and every path that is not named explicitly in the small allowlist.
        if (relative not in STATIC_ALLOWLIST or "\\" in relative or
                any(part in (".", "..") for part in relative.split("/"))):
            self._json(404, {"ok": False, "error": "not_found", "candidates": []})
            return
        target = (self.server.directory / relative).resolve()
        if self.server.directory not in target.parents or not target.is_file():
            self._json(404, {"ok": False, "error": "not_found", "candidates": []})
            return
        try:
            content = target.read_bytes()
        except OSError:
            self._json(404, {"ok": False, "error": "not_found", "candidates": []})
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type in ("text/javascript", "application/javascript"):
            content_type = "text/javascript"
        self._send(200, content, content_type + "; charset=utf-8")

    def _json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' https://upload.wikimedia.org https://mdwiki.org; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, format: str, *args: Any) -> None:
        # Keep medical query strings out of the default request log.
        return


def serve(directory: Path | str | None = None, seed_path: Path | str | None = None,
          cache_path: Path | str | None = None, host: str = "127.0.0.1", port: int = 8772,
          provider: TermProvider | None = None) -> TermCardsServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("TermCards may bind only to loopback")
    root = Path(directory) if directory else Path(__file__).resolve().parent
    seed = Path(seed_path) if seed_path else root / "data" / "terms.json"
    term_provider = provider or TermProvider(seed, cache_path=cache_path)
    server = TermCardsServer((host, port), root, term_provider)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the local TermCards prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--cache-dir", type=Path, help="Use this private directory for ephemeral lookup cache")
    args = parser.parse_args()
    cache_path = args.cache_dir / "cache.json" if args.cache_dir else None
    server = serve(host=args.host, port=args.port, cache_path=cache_path)
    try:
        print(f"TermCards available at http://{args.host}:{args.port}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
