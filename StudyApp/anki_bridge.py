"""Safe, narrowly scoped AnkiConnect bridge for explicit study ratings.

This module never schedules a card from a guide state. A rating is written only
after ``submit_review`` receives an explicit ease (1-4) and a live token from
``begin_review``. Tests must use an injected fake transport; do not rate real
cards while developing or validating this module.
"""
from __future__ import annotations

import json
import base64
import binascii
import html
import os
import re
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import unquote
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


DEFAULT_ENDPOINT = "http://127.0.0.1:8765"
MAX_ANKICONNECT_BATCH = 999
MAX_LIVE_MEDIA_FILES = 24
MAX_LIVE_MEDIA_FILE_BYTES = 8 * 1024 * 1024
MAX_LIVE_MEDIA_TOTAL_BYTES = 16 * 1024 * 1024
SAFE_CARD_FIELDS = (
    "cardId", "note", "ord", "queue", "type", "interval", "reps", "lapses",
    "nextReviews", "due", "mod", "deckName", "modelName", "factor", "left", "flags",
    "question", "answer", "css",
)


class AnkiBridgeError(Exception):
    """Base class for bridge errors safe to return to the local app."""


class BridgeUnavailable(AnkiBridgeError):
    pass


class ScopeError(AnkiBridgeError):
    pass


class StaleReview(AnkiBridgeError):
    pass


class AnkiConnectError(AnkiBridgeError):
    pass


Transport = Callable[[str, dict[str, Any]], Any]


class _ImageSourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        for name, value in attrs:
            if name.lower() == "src" and value:
                self.sources.add(html.unescape(value.strip()))


def _media_filename(value: str) -> str | None:
    """Accept only literal Anki collection.media filenames, never paths or URLs."""
    value = unquote(value.strip())
    if (not value or value.startswith(("data:", "blob:", "//")) or
            ":" in value or "/" in value or "\\" in value or
            value in {".", ".."} or any(ord(char) < 32 for char in value)):
        return None
    return value


def _media_data_url(filename: str, encoded: Any) -> tuple[str, int] | None:
    if not isinstance(encoded, str) or len(encoded) > ((MAX_LIVE_MEDIA_FILE_BYTES + 2) // 3) * 4 + 8:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw or len(raw) > MAX_LIVE_MEDIA_FILE_BYTES:
        return None
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
        (b"RIFF", "image/webp"), (b"BM", "image/bmp"),
    )
    mime = next((kind for signature, kind in signatures if raw.startswith(signature)), None)
    if mime == "image/webp" and raw[8:12] != b"WEBP":
        mime = None
    if mime is None and filename.lower().endswith(".svg"):
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return None
        allowed_tags = {"svg", "g", "defs", "clipPath", "mask", "rect", "circle", "ellipse",
                        "line", "polyline", "polygon", "path", "text", "tspan", "title", "desc"}
        allowed_attrs = {"xmlns", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2",
                         "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "fill-rule",
                         "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin",
                         "opacity", "transform", "font-size", "font-family", "font-weight", "text-anchor",
                         "dominant-baseline", "class", "id", "clip-path", "mask"}
        def scrub(parent: ET.Element) -> None:
            for node in list(parent):
                tag = node.tag.rsplit("}", 1)[-1]
                if tag not in allowed_tags:
                    parent.remove(node)
                    continue
                scrub(node)
        if root.tag.rsplit("}", 1)[-1] != "svg":
            return None
        scrub(root)
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            for attr in list(node.attrib):
                key = attr.rsplit("}", 1)[-1]
                val = node.attrib[attr]
                safe_fragment_url = re.fullmatch(r"url\(\s*#[\w.-]+\s*\)", val, re.IGNORECASE)
                if key not in allowed_attrs or key.lower().startswith("on") or ("url(" in val.lower() and not safe_fragment_url):
                    del node.attrib[attr]
        raw = ET.tostring(root, encoding="utf-8", xml_declaration=False)
        mime = "image/svg+xml"
    if mime is None:
        return None
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", len(raw)


def _default_transport(endpoint: str = DEFAULT_ENDPOINT, timeout: float = 4.0) -> Transport:
    def call(action: str, params: dict[str, Any]) -> Any:
        body = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise BridgeUnavailable(f"AnkiConnect is unavailable: {type(exc).__name__}") from exc
        if not isinstance(envelope, dict) or "error" not in envelope:
            raise AnkiConnectError("AnkiConnect returned an invalid response")
        if envelope.get("error") is not None:
            raise AnkiConnectError(str(envelope["error"]))
        return envelope.get("result")

    return call


def _unwrap_transport(transport: Transport, action: str, params: dict[str, Any]) -> Any:
    """Accept either a result-returning transport or raw AnkiConnect envelope."""
    value = transport(action, params)
    if isinstance(value, dict) and "error" in value and "result" in value:
        if value.get("error") is not None:
            raise AnkiConnectError(str(value["error"]))
        return value.get("result")
    return value


def _parse_export(path: Path) -> dict[str, Any]:
    """Read the JSON assignment without executing the exported JavaScript."""
    source = path.read_text(encoding="utf-8")
    marker = "window.ANKI_CONTEXT="
    offset = source.find(marker)
    if offset < 0:
        raise ValueError(f"Missing {marker} in Anki context export")
    payload = source[offset + len(marker):].lstrip()
    value, _end = json.JSONDecoder().raw_decode(payload)
    if not isinstance(value, dict):
        raise ValueError("Anki context export must contain a JSON object")
    return value


def _allowed_cards(root_or_mapping: str | os.PathLike[str] | Mapping[str, Any] | Iterable[int]) -> dict[int, dict[str, int | None]]:
    value: Any = root_or_mapping
    if isinstance(root_or_mapping, (str, os.PathLike)):
        path = Path(root_or_mapping)
        if path.is_dir():
            path = path / "anki_context_data.js"
        if path.name.endswith(".js"):
            value = _parse_export(path)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))

    cards: dict[int, dict[str, int | None]] = {}
    if isinstance(value, Mapping):
        notes = value.get("notes")
        if isinstance(notes, list):
            for note in notes:
                if not isinstance(note, Mapping):
                    continue
                try:
                    note_id = int(note["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                ids = note.get("cardIds", [])
                if not isinstance(ids, list):
                    continue
                for ordinal, card_id in enumerate(ids):
                    cid = int(card_id)
                    cards[cid] = {"note": note_id, "ord": ordinal}
        else:
            # An explicit cardId -> {note, ord} map is useful for tests and
            # small controlled deployments. Missing identity fields are not
            # accepted, because they prevent safe live identity validation.
            for card_id, metadata in value.items():
                if not isinstance(metadata, Mapping) or metadata.get("note") is None or metadata.get("ord") is None:
                    continue
                cards[int(card_id)] = {"note": int(metadata["note"]), "ord": int(metadata["ord"])}
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        raise ValueError("Bare card ID lists lack note/ordinal identity; pass the exported note mapping")
    else:
        raise ValueError("Allowed-card input must be an export object or card identity mapping")
    # An empty public install is valid: it can report AnkiConnect availability
    # without making any card eligible for reads or writes.
    return cards


class AnkiBridge:
    """Private local AnkiConnect adapter with explicit-review idempotency."""

    def __init__(
        self,
        root_or_mapping: str | os.PathLike[str] | Mapping[str, Any] | Iterable[int],
        sqlite_path: str | os.PathLike[str] | None = None,
        transport: Transport | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = 4.0,
    ) -> None:
        self.allowed = _allowed_cards(root_or_mapping)
        self.transport = transport or _default_transport(endpoint, timeout)
        self._lock = threading.RLock()
        if sqlite_path is None:
            data_root = Path.home() / "Library" / "Application Support" / "LLU Study"
            data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                data_root.chmod(0o700)
            sqlite_path = data_root / "anki-bridge.sqlite3"
        self.sqlite_path = Path(sqlite_path).expanduser().resolve()
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.sqlite_path.parent.chmod(0o700)
        self._initialize_ledger()
        if os.name != "nt":
            self.sqlite_path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.sqlite_path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _initialize_ledger(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_tokens (
                    token TEXT PRIMARY KEY,
                    card_id INTEGER NOT NULL,
                    expected_note INTEGER NOT NULL,
                    expected_ord INTEGER NOT NULL,
                    card_mod INTEGER NOT NULL,
                    baseline_review_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready',
                    request_id TEXT
                );
                CREATE TABLE IF NOT EXISTS review_requests (
                    request_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL,
                    card_id INTEGER NOT NULL,
                    ease INTEGER NOT NULL,
                    baseline_review_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    review_id INTEGER,
                    detail TEXT,
                    result_json TEXT,
                    UNIQUE(token)
                );
                CREATE INDEX IF NOT EXISTS review_requests_card_id ON review_requests(card_id);
                CREATE TABLE IF NOT EXISTS history_sync (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    summary_json TEXT NOT NULL,
                    synced_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history_cards (
                    card_id INTEGER PRIMARY KEY,
                    review_count INTEGER NOT NULL,
                    last_review_json TEXT
                );
                """
            )

    def _call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        return _unwrap_transport(self.transport, action, params or {})

    def status(self) -> dict[str, Any]:
        try:
            version = self._call("version")
            if not isinstance(version, int):
                raise AnkiConnectError("AnkiConnect returned an invalid version")
            return {"available": True, "anki_connect_version": version,
                    "allowed_card_count": len(self.allowed), "rating_write": bool(self.allowed),
                    "scope_import_required": not bool(self.allowed)}
        except Exception as exc:
            return {"available": False, "anki_connect_version": None,
                    "allowed_card_count": len(self.allowed), "rating_write": False,
                    "scope_import_required": not bool(self.allowed),
                    "error": f"{type(exc).__name__}: {exc}"}

    def _require_card(self, card_id: int | str) -> tuple[int, dict[str, int | None]]:
        try:
            cid = int(card_id)
        except (TypeError, ValueError) as exc:
            raise ScopeError("Card ID must be an integer") from exc
        identity = self.allowed.get(cid)
        if identity is None:
            raise ScopeError("No Anki cards are imported into this app" if not self.allowed else
                             "Card is outside the imported Anki scope")
        return cid, identity

    @staticmethod
    def _safe_card(info: Mapping[str, Any]) -> dict[str, Any]:
        safe = {key: info[key] for key in SAFE_CARD_FIELDS if key in info}
        if "note" in safe:
            safe["noteId"] = safe["note"]
        return safe

    def _card_info_raw(self, card_id: int) -> dict[str, Any]:
        response = self._call("cardsInfo", {"cards": [card_id]})
        if not isinstance(response, list) or len(response) != 1 or not isinstance(response[0], dict) or not response[0]:
            raise ScopeError("Anki did not return this card")
        info = response[0]
        identity = self.allowed.get(card_id)
        if identity is None or int(info.get("cardId", -1)) != card_id:
            raise ScopeError("Live card ID does not match the exported scope")
        if int(info.get("note", -1)) != identity["note"] or int(info.get("ord", -1)) != identity["ord"]:
            raise ScopeError("Live note or card ordinal does not match the exported scope")
        return info

    def _live_media(self, info: Mapping[str, Any]) -> dict[str, str]:
        """Fetch only bounded, literal local image filenames referenced by this card."""
        parser = _ImageSourceParser()
        for field in ("question", "answer"):
            value = info.get(field)
            if isinstance(value, str):
                parser.feed(value)
        filenames = sorted({name for src in parser.sources if (name := _media_filename(src))})
        result: dict[str, str] = {}
        total = 0
        for filename in filenames[:MAX_LIVE_MEDIA_FILES]:
            try:
                value = self._call("retrieveMediaFile", {"filename": filename})
            except Exception:
                continue
            media = _media_data_url(filename, value)
            if media is None:
                continue
            data_url, size = media
            if total + size > MAX_LIVE_MEDIA_TOTAL_BYTES:
                break
            result[filename] = data_url
            total += size
        return result

    def card_info(self, card_id: int | str) -> dict[str, Any]:
        cid, _identity = self._require_card(card_id)
        info = self._card_info_raw(cid)
        result = self._safe_card(info)
        result["media"] = self._live_media(info)
        result["isDue"] = int(info.get("queue", 0)) >= 0 and self._are_due(cid)
        result["isNew"] = int(info.get("queue", 0)) == 0 and int(info.get("reps", 0)) == 0
        return result

    def _are_due(self, card_id: int) -> bool:
        value = self._call("areDue", {"cards": [card_id]})
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], bool):
            raise AnkiConnectError("Anki returned invalid due status")
        return value[0]

    def _review_rows(self, card_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        result: dict[int, list[dict[str, Any]]] = {cid: [] for cid in card_ids}
        for start in range(0, len(card_ids), MAX_ANKICONNECT_BATCH):
            batch = card_ids[start:start + MAX_ANKICONNECT_BATCH]
            value = self._call("getReviewsOfCards", {"cards": batch})
            if not isinstance(value, dict):
                raise AnkiConnectError("Anki returned invalid review history")
            for cid in batch:
                rows = value.get(str(cid), value.get(cid, []))
                if not isinstance(rows, list):
                    continue
                result[cid] = [row for row in rows if isinstance(row, dict)]
        return result

    def _latest_review_id(self, card_id: int) -> int:
        # The displayed card's latest review is enough to detect a stale token;
        # reviews of unrelated cards do not invalidate this card's session.
        rows = self._review_rows([card_id])
        return max((int(review.get("id", 0)) for reviews in rows.values() for review in reviews), default=0)

    def begin_review(self, card_id: int | str) -> dict[str, Any]:
        cid, identity = self._require_card(card_id)
        with self._lock:
            info = self._card_info_raw(cid)
            if int(info.get("queue", 0)) < 0:
                raise ScopeError("Suspended or buried cards cannot be rated from the guide")
            if not self._are_due(cid):
                raise ScopeError("This card is not due; only new or due cards can be reviewed from the guide")
            baseline = self._latest_review_id(cid)
            token = str(uuid.uuid4())
            created = time.time()
            with closing(self._connect()) as conn:
                conn.execute(
                    "INSERT INTO review_tokens(token,card_id,expected_note,expected_ord,card_mod,baseline_review_id,created_at,status) VALUES(?,?,?,?,?,?,?,'ready')",
                    (token, cid, identity["note"], identity["ord"], int(info.get("mod", 0)), baseline, created),
                )
            safe = self._safe_card(info)
            safe["media"] = self._live_media(info)
            safe["isDue"] = True
            safe["isNew"] = int(info.get("queue", 0)) == 0 and int(info.get("reps", 0)) == 0
            return {"token": token, "card": safe,
                    "baseline_review_id": baseline, "created_at": created}

    @staticmethod
    def _review_for_request(rows: list[dict[str, Any]], card_id: int, ease: int, baseline: int) -> dict[str, Any] | None:
        candidates = [row for row in rows if int(row.get("id", 0)) > baseline and int(row.get("ease", 0)) == ease]
        if not candidates:
            return None
        return max(candidates, key=lambda row: int(row.get("id", 0)))

    def _reconcile(self, card_id: int, ease: int, baseline: int) -> dict[str, Any] | None:
        rows = self._review_rows([card_id]).get(card_id, [])
        return self._review_for_request(rows, card_id, ease, baseline)

    def _request_result(self, request_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM review_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"]) if row["result_json"] else {}
        return {"confirmed": row["status"] == "confirmed", "status": row["status"],
                "request_id": request_id, "cardId": row["card_id"], "ease": row["ease"],
                "reviewId": row["review_id"], "detail": row["detail"], **result}

    def _recover_pending_request(self, request_id: str, row: sqlite3.Row) -> dict[str, Any]:
        """Resolve a write-ahead record left pending by a process interruption."""
        card_id = int(row["card_id"])
        ease = int(row["ease"])
        baseline = int(row["baseline_review_id"])
        try:
            review = self._reconcile(card_id, ease, baseline)
        except Exception as exc:
            review = None
            detail = f"Could not reconcile interrupted request ({type(exc).__name__}); it will not be resubmitted"
        else:
            detail = ("Confirmed from Anki review history after process restart" if review else
                      "Interrupted request has no matching review; it will not be resubmitted")
        return self._finish_request(request_id, "confirmed" if review else "unknown",
                                    card_id=card_id, ease=ease, review=review, detail=detail)

    def submit_review(self, token: str, ease: int, request_id: str) -> dict[str, Any]:
        if isinstance(ease, bool) or not isinstance(ease, int) or ease not in (1, 2, 3, 4):
            raise ValueError("An explicit Anki rating from 1 through 4 is required")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError("A nonempty request_id of at most 200 characters is required")
        request_id = request_id.strip()
        with self._lock:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute("SELECT * FROM review_requests WHERE request_id=?", (request_id,)).fetchone()
                if existing is not None:
                    if existing["token"] != token or int(existing["ease"]) != ease:
                        conn.rollback()
                        raise ScopeError("request_id was already used for a different review")
                    conn.commit()
                    if existing["status"] == "pending":
                        return self._recover_pending_request(request_id, existing)
                    return self._request_result(request_id) or {"confirmed": False, "status": "unknown", "request_id": request_id}
                token_row = conn.execute("SELECT * FROM review_tokens WHERE token=?", (token,)).fetchone()
                if token_row is None:
                    conn.rollback()
                    raise StaleReview("Review token is missing or expired")
                if token_row["status"] != "ready":
                    conn.rollback()
                    raise StaleReview("Review token has already been used or invalidated")
                cid = int(token_row["card_id"])
                baseline = int(token_row["baseline_review_id"])
                conn.commit()

            info = self._card_info_raw(cid)
            if int(info.get("queue", 0)) < 0:
                with closing(self._connect()) as conn:
                    conn.execute("UPDATE review_tokens SET status='rejected' WHERE token=?", (token,))
                raise ScopeError("Suspended or buried cards cannot be rated from the guide")
            if not self._are_due(cid):
                with closing(self._connect()) as conn:
                    conn.execute("UPDATE review_tokens SET status='stale' WHERE token=?", (token,))
                raise StaleReview("Card is no longer due")
            if int(info.get("mod", 0)) != int(token_row["card_mod"]):
                with closing(self._connect()) as conn:
                    conn.execute("UPDATE review_tokens SET status='stale' WHERE token=?", (token,))
                raise StaleReview("Card changed after this review was opened")
            if self._latest_review_id(cid) != baseline:
                with closing(self._connect()) as conn:
                    conn.execute("UPDATE review_tokens SET status='stale' WHERE token=?", (token,))
                raise StaleReview("This card was reviewed again after its Anki review was opened")

            now = time.time()
            # Write-ahead idempotency record: after this commit, this request
            # will never call answerCards again, even after process restart.
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                latest = conn.execute("SELECT status,request_id FROM review_tokens WHERE token=?", (token,)).fetchone()
                if latest is None or latest["status"] != "ready":
                    conn.rollback()
                    raise StaleReview("Review token has already been used")
                conn.execute(
                    "INSERT INTO review_requests(request_id,token,card_id,ease,baseline_review_id,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
                    (request_id, token, cid, ease, baseline, now, now),
                )
                conn.execute("UPDATE review_tokens SET status='submitted',request_id=? WHERE token=?", (request_id, token))
                conn.commit()

            try:
                native_result = self._call("answerCards", {"answers": [{"cardId": cid, "ease": ease}]})
            except AnkiConnectError as exc:
                return self._finish_request(request_id, "unsynced", card_id=cid, ease=ease,
                                            detail=f"AnkiConnect error: {exc}")
            except Exception as exc:
                # A timeout can happen after Anki has already committed. Check
                # history once and never retry the write.
                try:
                    review = self._reconcile(cid, ease, baseline)
                except Exception:
                    review = None
                if review:
                    return self._finish_request(request_id, "confirmed", card_id=cid, ease=ease, review=review,
                                                detail="Confirmed from Anki review history after an uncertain transport outcome")
                return self._finish_request(request_id, "unknown", card_id=cid, ease=ease,
                                            detail=f"Uncertain AnkiConnect outcome ({type(exc).__name__}); request will not be resubmitted")

            if not isinstance(native_result, list) or len(native_result) != 1 or native_result[0] is not True:
                return self._finish_request(request_id, "unsynced", card_id=cid, ease=ease,
                                            detail="Anki did not confirm the rating request")
            try:
                review = self._reconcile(cid, ease, baseline)
            except Exception as exc:
                return self._finish_request(request_id, "unknown", card_id=cid, ease=ease,
                                            detail=f"Rating call succeeded but history confirmation failed ({type(exc).__name__})")
            if review:
                return self._finish_request(request_id, "confirmed", card_id=cid, ease=ease, review=review,
                                            detail="Confirmed by Anki review history")
            return self._finish_request(request_id, "unknown", card_id=cid, ease=ease,
                                        detail="Anki accepted the request but no matching review is visible yet; request will not be resubmitted")

    def _finish_request(self, request_id: str, status: str, *, card_id: int, ease: int,
                        review: dict[str, Any] | None = None,
                        detail: str | None = None) -> dict[str, Any]:
        now = time.time()
        review_id = int(review["id"]) if review and review.get("id") is not None else None
        result = {"review": review} if review else {}
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE review_requests SET status=?,updated_at=?,review_id=?,detail=?,result_json=? WHERE request_id=?",
                (status, now, review_id, detail, json.dumps(result, separators=(",", ":")) if result else None, request_id),
            )
        return {"confirmed": status == "confirmed", "status": status, "request_id": request_id,
                "cardId": card_id, "ease": ease, "reviewId": review_id,
                "detail": detail, **result}

    def sync_history(self, include_reviews: bool = False) -> dict[str, Any]:
        """Read review history for the exported scope only; never edits guide state."""
        card_ids = list(self.allowed)
        reviews_by_card = self._review_rows(card_ids)
        review_count = sum(len(rows) for rows in reviews_by_card.values())
        reviewed = {cid: rows for cid, rows in reviews_by_card.items() if rows}
        latest_id = max((int(row.get("id", 0)) for rows in reviewed.values() for row in rows), default=0)
        synced_at = time.time()
        cards: dict[str, Any] = {}
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM history_cards")
            for cid in card_ids:
                rows = reviews_by_card.get(cid, [])
                last = max(rows, key=lambda row: int(row.get("id", 0))) if rows else None
                last_safe = ({key: last.get(key) for key in ("id", "ease", "ivl", "lastIvl", "time", "type")}
                             if last else None)
                cards[str(cid)] = {"review_count": len(rows), "reviewed": bool(rows), "last_review": last_safe}
                conn.execute(
                    "INSERT INTO history_cards(card_id,review_count,last_review_json) VALUES(?,?,?)",
                    (cid, len(rows), json.dumps(last_safe, separators=(",", ":")) if last_safe else None),
                )
            summary = {"scope_card_count": len(card_ids), "reviewed_card_count": len(reviewed),
                       "review_count": review_count, "latest_review_id": latest_id,
                       "synced_at": synced_at, "guide_progress_changed": False}
            conn.execute("INSERT OR REPLACE INTO history_sync(singleton,summary_json,synced_at) VALUES(1,?,?)",
                         (json.dumps(summary, separators=(",", ":")), synced_at))
            conn.commit()
        result: dict[str, Any] = {"summary": summary, "cards": cards}
        if include_reviews:
            result["reviews_by_card"] = {
                str(cid): [{key: row.get(key) for key in ("id", "ease", "ivl", "lastIvl", "time", "type")}
                           for row in rows]
                for cid, rows in reviewed.items()
            }
        return result

    def coverage(self) -> dict[str, Any]:
        """Return the saved scoped history snapshot without contacting Anki."""
        with closing(self._connect()) as conn:
            summary_row = conn.execute("SELECT summary_json FROM history_sync WHERE singleton=1").fetchone()
            rows = conn.execute("SELECT card_id,review_count,last_review_json FROM history_cards").fetchall()
        if summary_row is None:
            return {"summary": None, "cards": {}}
        cards = {
            str(row["card_id"]): {
                "review_count": int(row["review_count"]),
                "reviewed": int(row["review_count"]) > 0,
                "last_review": json.loads(row["last_review_json"]) if row["last_review_json"] else None,
            }
            for row in rows
        }
        return {"summary": json.loads(summary_row["summary_json"]), "cards": cards}
