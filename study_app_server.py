#!/usr/bin/env python3
"""Serve the public guide with private local progress and optional Anki writes."""
from __future__ import annotations

import argparse
import importlib
import json
import mimetypes
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from StudyApp.app_paths import default_data_dir, secure_data_dir
from build_public import GENERATED_ASSETS


PROJECT_ROOT = Path(__file__).resolve().parent
APP_ROOT = PROJECT_ROOT / "StudyApp"
MAX_JSON_BODY = 2 * 1024 * 1024


class ServiceUnavailable(RuntimeError):
    pass


class AppServices:
    """Lazy standard-library services; Anki is optional and never auto-started."""

    def __init__(self, data_dir: Path | None = None, progress_store: Any = None,
                 anki_bridge: Any = None):
        self.data_dir = secure_data_dir(data_dir or default_data_dir())
        self._progress_store = progress_store
        self._anki_bridge = anki_bridge
        self._lock = threading.RLock()

    @property
    def progress(self):
        if self._progress_store is None:
            try:
                module = importlib.import_module("StudyApp.progress_store")
                self._progress_store = module.ProgressStore(self.data_dir / "progress.sqlite3")
            except Exception as exc:
                raise ServiceUnavailable(f"Local progress storage is unavailable: {type(exc).__name__}") from exc
        return self._progress_store

    @property
    def anki(self):
        if self._anki_bridge is None:
            with self._lock:
                if self._anki_bridge is not None:
                    return self._anki_bridge
                try:
                    module = importlib.import_module("StudyApp.anki_bridge")
                    scope_path = self.data_dir / "anki-scope.json"
                    if scope_path.is_file():
                        scope: Any = scope_path
                    else:
                        scope = {}
                    self._anki_bridge = module.AnkiBridge(
                        scope, sqlite_path=self.data_dir / "anki-bridge.sqlite3")
                except Exception as exc:
                    raise ServiceUnavailable(f"Anki integration is unavailable: {type(exc).__name__}") from exc
        return self._anki_bridge


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_handler(services: AppServices, token: str, project_root: Path = PROJECT_ROOT):
    allowed_root = project_root.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "LLUStudy/1.0"
        sys_version = ""

        def log_message(self, _format: str, *_args: Any) -> None:
            # Avoid logging progress, local paths, card IDs, or study topics.
            return

        def _allowed_host(self) -> bool:
            host = self.headers.get("Host", "").lower().strip()
            port = int(self.server.server_port)
            return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _allowed_origin(self, require: bool = False) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return not require
            port = int(self.server.server_port)
            return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

        def _guard(self, mutation: bool = False) -> bool:
            if not self._allowed_host() or not self._allowed_origin(require=mutation):
                self._reply(403, {"error": "This local app request is not permitted."})
                return False
            if mutation and not secrets.compare_digest(self.headers.get("X-LLU-App-Token", ""), token):
                self._reply(403, {"error": "The app session token is missing or invalid."})
                return False
            return True

        def _reply(self, status: int, payload: Any, content_type: str = "application/json; charset=utf-8",
                   body: bytes | None = None) -> None:
            data = _json_bytes(payload) if body is None else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _body_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid request length") from exc
            if length <= 0 or length > MAX_JSON_BODY:
                raise ValueError("Request body is empty or too large")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _call(self, function):
            try:
                self._reply(200, function())
            except ServiceUnavailable as exc:
                self._reply(503, {"error": str(exc)})
            except Exception as exc:
                status = 400 if isinstance(exc, (ValueError,)) else 409 if type(exc).__name__ in {"ScopeError", "StaleReview"} else 500
                self._reply(status, {"error": str(exc) if status < 500 else "The local request could not be completed."})

        def do_GET(self) -> None:
            if not self._guard():
                return
            path = urlsplit(self.path).path
            if path == "/health":
                self._reply(200, {"app": "llu-study-app", "ready": True})
            elif path == "/api/progress":
                self._call(lambda: services.progress.load())
            elif path == "/api/anki/status":
                self._call(lambda: services.anki.status())
            elif path == "/api/anki/coverage":
                self._call(lambda: services.anki.coverage())
            elif path == "/":
                self._serve_index()
            elif path == "/anki_context_data.js":
                self._serve_public_file(path.lstrip("/"), "text/javascript; charset=utf-8")
            elif path.startswith("/assets/generated/"):
                name = path.lstrip("/")
                if name not in GENERATED_ASSETS:
                    self._reply(404, {"error": "Not found"})
                    return
                self._serve_public_file(name, mimetypes.guess_type(name)[0] or "application/octet-stream")
            else:
                self._reply(404, {"error": "Not found"})

        def do_POST(self) -> None:
            if not self._guard(mutation=True):
                return
            try:
                body = self._body_json()
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._reply(400, {"error": str(exc)})
                return
            path = urlsplit(self.path).path
            if path == "/api/progress":
                patch, revision = body.get("patch"), body.get("base_revision")
                if not isinstance(patch, dict) or isinstance(revision, bool) or not isinstance(revision, int):
                    self._reply(400, {"error": "Expected a JSON patch object and integer base_revision."})
                    return
                self._call(lambda: services.progress.apply_patch(patch, revision))
            elif path == "/api/progress/import":
                state = body.get("state")
                if not isinstance(state, dict):
                    self._reply(400, {"error": "Expected state to be an object."})
                    return
                self._call(lambda: services.progress.import_state(state, replace=True))
            elif path == "/api/anki/card":
                self._call(lambda: services.anki.card_info(card_id=body.get("cardId")))
            elif path == "/api/anki/begin":
                self._call(lambda: services.anki.begin_review(card_id=body.get("cardId")))
            elif path == "/api/anki/review":
                card_token, ease, request_id = body.get("token"), body.get("ease"), body.get("request_id")
                if (not isinstance(card_token, str) or not card_token or isinstance(ease, bool)
                        or not isinstance(ease, int) or ease not in (1, 2, 3, 4)
                        or not isinstance(request_id, str) or not request_id):
                    self._reply(400, {"error": "Review requires a token, explicit ease 1–4, and request_id."})
                    return
                self._call(lambda: services.anki.submit_review(token=card_token, ease=ease,
                                                                request_id=request_id))
            elif path == "/api/anki/sync":
                self._call(lambda: services.anki.sync_history())
            else:
                self._reply(404, {"error": "Not found"})

        def _serve_index(self) -> None:
            self._serve_public_file("index.html", "text/html; charset=utf-8", inject=True)

        def _serve_public_file(self, name: str, content_type: str, inject: bool = False) -> None:
            if name not in {"index.html", "anki_context_data.js"} and name not in GENERATED_ASSETS:
                self._reply(404, {"error": "Not found"})
                return
            path = (allowed_root / name).resolve()
            try:
                path.relative_to(allowed_root)
                content = path.read_text(encoding="utf-8") if name in {"index.html", "anki_context_data.js"} else path.read_bytes()
            except (OSError, ValueError):
                self._reply(503, {"error": "Public guide assets are unavailable."})
                return
            if inject:
                config = {"apiBase": "/api", "token": token, "ready": True}
                script = "<script>window.STUDY_APP_CONFIG=" + json.dumps(config, separators=(",", ":")) + ";</script>"
                content = content.replace("</head>", script + "</head>", 1)
            body = content.encode("utf-8") if isinstance(content, str) else content
            self._reply(200, {}, content_type, body)

    return Handler


def create_server(port: int = 8770, data_dir: Path | None = None, token: str | None = None,
                  services: AppServices | None = None, project_root: Path = PROJECT_ROOT) -> ThreadingHTTPServer:
    if not 0 <= int(port) <= 65535:
        raise ValueError("port must be between 0 and 65535")
    app_services = services or AppServices(data_dir)
    session_token = token or secrets.token_urlsafe(32)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, address, handler):
            self.services = app_services
            self.session_token = session_token
            super().__init__(address, handler)

    return Server(("127.0.0.1", int(port)), make_handler(app_services, session_token, project_root))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="Private per-user app data directory")
    parser.add_argument("--port", type=int, default=8770, help="Loopback port (default: 8770)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    server = create_server(args.port, args.data_dir)
    print(f"LLU Study ready at http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
