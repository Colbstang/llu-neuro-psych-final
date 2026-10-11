"""Subject-scoped live Anki card reviews for the Step workspace.

Anki remains the scheduler. This module only exposes explicitly selected live
due/new cards from the exact Step category scope reported by Anki, and routes
ratings through StudyApp's private, idempotent review ledger.
"""
from __future__ import annotations

import html
import json
import re
import secrets
import sys
import threading
import time
from html.parser import HTMLParser
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from StudyApp.anki_bridge import AnkiBridge, BridgeUnavailable, ScopeError, StaleReview, _media_filename
from StepStudy.anki_signals import CATALOG_PATH, _category_prefix, _quote_search, _subject_for_tag, anki_connect

MAX_QUEUE_CARDS = 200
CARD_INFO_BATCH_SIZE = 25
MAX_CATEGORY_PREFIXES = 12
MAX_TRANSPORT_SECONDS = 25.0
MAX_MARKUP_CHARS = 500_000
MAX_TEXT_CHARS = 100_000
MAX_IMAGES = 24
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_CLOZE = re.compile(r"\{\{c\d+::\s*(.*?)\s*(?:::\s*(.*?))?\s*\}\}", re.I | re.S)


class _CardTextParser(HTMLParser):
    """Extract readable text and only already-validated local image data URLs."""

    _HIDDEN = {"script", "style", "template", "iframe", "object", "embed", "noscript"}
    _BLOCK = {"br", "p", "div", "li", "tr", "td", "th", "ul", "ol", "table", "section", "article", "h1", "h2", "h3", "h4"}

    def __init__(self, media: dict[str, str], *, front: bool, css: str = ""):
        super().__init__(convert_charrefs=True)
        self.media = media
        self.front = front
        self.stack: list[tuple[str, bool]] = []
        hidden_selectors = set()
        if front and isinstance(css, str):
            for selector, declarations in re.findall(r"([^{}]{1,500})\{([^{}]{0,1000})\}", css[:MAX_MARKUP_CHARS]):
                if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", declarations, re.I):
                    hidden_selectors.update(re.findall(r"\.([\w-]+)", selector))
                    hidden_selectors.update(re.findall(r"#([\w-]+)", selector))
        self.hidden_selectors = hidden_selectors
        self.parts: list[str] = []
        self.images: list[dict[str, str]] = []

    @property
    def suppressed(self) -> bool:
        return any(suppress for _tag, suppress in self.stack)

    def _hidden_attrs(self, attrs: list[tuple[str, str | None]]) -> bool:
        values = {name.lower(): value for name, value in attrs}
        if "hidden" in values or (values.get("aria-hidden") or "").strip().lower() == "true":
            return True
        style = values.get("style") or ""
        if re.search(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)", style, re.I):
            return True
        if self.hidden_selectors:
            if self.hidden_selectors.intersection((values.get("class") or "").split()):
                return True
            if (values.get("id") or "") in self.hidden_selectors:
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            is_suppressed = self.suppressed or tag in self._HIDDEN or (self.front and self._hidden_attrs(attrs))
            if is_suppressed:
                return
        else:
            is_suppressed = self.suppressed or tag in self._HIDDEN or (self.front and self._hidden_attrs(attrs))
            self.stack.append((tag, is_suppressed))
        if self.suppressed:
            return
        if tag in self._BLOCK:
            self.parts.append(" ")
        if tag == "img" and len(self.images) < MAX_IMAGES:
            values = {name.lower(): value for name, value in attrs if value is not None}
            src = values.get("src", "").strip()
            filename = _media_filename(src)
            if filename and filename in self.media and self.media[filename].startswith("data:image/"):
                alt = _clean_text(values.get("alt", ""), 500)
                self.images.append({"src": self.media[filename], "alt": alt})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        # A mismatched end tag cannot pop unrelated suppression state. When
        # the tag is present, discard only it and descendants from the stack.
        match = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag), None)
        if match is None:
            return
        was_suppressed = self.suppressed
        del self.stack[match:]
        if not was_suppressed and not self.suppressed and tag in self._BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.suppressed and data:
            self.parts.append(data)


def _clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    value = html.unescape(value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _card_side(value: Any, media: Any, *, front: bool, css: str = "") -> dict[str, Any]:
    parser = _CardTextParser(media if isinstance(media, dict) else {}, front=front, css=css)
    try:
        parser.feed(value[:MAX_MARKUP_CHARS] if isinstance(value, str) else "")
        parser.close()
    except Exception:
        # Malformed card markup must never escape as raw HTML.
        pass
    text = _clean_text(" ".join(parser.parts))
    if front:
        text = _CLOZE.sub(lambda match: f"[{_clean_text(match.group(2), 500)}]" if match.group(2) else "[...]", text)
    else:
        text = _CLOZE.sub(lambda match: _clean_text(match.group(1), 500), text)
    images = parser.images
    return {"text": text, "images": images}


def _positive_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else None
    return None


def _identity_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return None
    return None


def _requires_native_rendering(card: dict[str, Any]) -> bool:
    """Do not flatten image occlusion overlays into an unmasked answer image."""
    model = str(card.get("modelName", "")).lower().replace("_", " ").replace("-", " ")
    question = str(card.get("question", ""))[:MAX_MARKUP_CHARS]
    return ("image occlusion" in model or "imageocclusion" in model
            or re.search(r"<\s*canvas\b", question, re.I) is not None
            or (re.search(r"<\s*svg\b", question, re.I) is not None
                and re.search(r"<\s*img\b", question, re.I) is not None))


class LiveCards:
    """Read and explicitly rate subject-specific live Anki cards."""

    def __init__(self, data_dir: str | Path, catalog_path: str | Path | None = None,
                 transport: Callable[[str, dict[str, Any]], Any] | None = None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        self.catalog_path = Path(catalog_path or CATALOG_PATH)
        self.transport = transport
        self.live_transport = transport is None
        import json
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        topics = payload.get("topics") if isinstance(payload, dict) else None
        self.catalog = {row["id"]: row for row in topics if isinstance(row, dict) and isinstance(row.get("id"), str)} if isinstance(topics, list) else {}
        self._bridges: dict[str, AnkiBridge] = {}
        self._identities: dict[str, dict[int, dict[str, int]]] = {}
        self._queued: dict[str, set[int]] = {}
        self._lock = threading.RLock()
        self._timing = threading.local()
        self._closed = False
        self._session_path = self.data_dir / "anki-sessions.json"

    def _call(self, action: str, **params: Any) -> Any:
        if self._closed:
            raise RuntimeError("Live card service is closed")
        deadline = getattr(self._timing, "deadline", None)
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Anki queue time budget exhausted")
        if self.live_transport:
            return anki_connect(action, params, timeout=min(8.0, remaining) if remaining is not None else 8.0)
        assert self.transport is not None
        return self.transport(action, params)

    def _bridge_transport(self, action: str, params: dict[str, Any]) -> Any:
        if self._closed:
            raise RuntimeError("Live card service is closed")
        deadline = getattr(self._timing, "deadline", None)
        remaining = deadline - time.monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Anki request time budget exhausted")
        if self.transport is not None:
            return self.transport(action, params)
        return anki_connect(action, params, timeout=min(8.0, remaining) if remaining is not None else 8.0)

    def _topic(self, topic_id: Any) -> str | None:
        return topic_id if isinstance(topic_id, str) and topic_id in self.catalog else None

    def _bridge(self, topic_id: str, identities: dict[int, dict[str, int]] | None = None) -> AnkiBridge:
        with self._lock:
            bridge = self._bridges.get(topic_id)
            if bridge is None:
                identities = identities or self._identities.get(topic_id, {})
                bridge = AnkiBridge(identities, sqlite_path=self.data_dir / f"anki-live-{topic_id}.sqlite3",
                                    transport=self._bridge_transport)
                self._bridges[topic_id] = bridge
                self._identities[topic_id] = dict(identities)
            elif identities:
                current = self._identities.setdefault(topic_id, {})
                # Preserve identities already bound to review tokens. Newly
                # discovered cards can be added without invalidating a session.
                for card_id, identity in identities.items():
                    current.setdefault(card_id, dict(identity))
                bridge.allowed.update({cid: dict(identity) for cid, identity in current.items()})
            return bridge

    def queue(self, topic_id: Any) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            return {"ok": False, "topic_id": topic_id if isinstance(topic_id, str) else None,
                    "card_ids": [], "count": 0, "partial": False, "reason": "Unknown topic."}
        prefixes: set[str] = set()
        partial = False
        self._timing.deadline = time.monotonic() + MAX_TRANSPORT_SECONDS
        try:
            self._call("version")
            tags = self._call("getTags") or []
            known = set(self.catalog)
            for tag in tags:
                if isinstance(tag, str) and _subject_for_tag(tag, known) == topic_id:
                    prefix = _category_prefix(tag, topic_id)
                    if prefix:
                        prefixes.add(prefix)
            if len(prefixes) > MAX_CATEGORY_PREFIXES:
                prefixes = set(sorted(prefixes)[:MAX_CATEGORY_PREFIXES])
                partial = True
            if not prefixes:
                self._timing.deadline = None
                with self._lock:
                    self._queued[topic_id] = set()
                reason = "No matching Step category tag was reported."
                return {"ok": True, "topic_id": topic_id, "card_ids": [], "count": 0,
                        "partial": partial, "reason": reason}
            # A parent tag search includes all descendants in Anki. Avoid a
            # redundant wildcard term for each of the verified category prefixes.
            tag_parts = [f"tag:{_quote_search(prefix)}" for prefix in sorted(prefixes)]
            # Verified category tags define Step scope even when a user moves
            # AnKing cards into a personally named deck. Never select a personal
            # note merely because its deck or unqualified disease tag matches.
            scope_query = f"({' OR '.join(tag_parts)}) -is:suspended -deck:filtered"
            if len(scope_query) > 11950:
                self._timing.deadline = None
                return {"ok": False, "topic_id": topic_id, "card_ids": [], "count": 0,
                        "partial": False, "reason": "The reported Anki scope exceeded the query limit."}
            # A large new-card population must not crowd reported due learning
            # and review cards out of this bounded batch.
            due_found = self._call("findCards", query=scope_query + " is:due") or []
            new_found = self._call("findCards", query=scope_query + " is:new") or []
            if not isinstance(due_found, list) or not isinstance(new_found, list):
                raise ValueError("invalid card list")
            found = due_found + new_found
            parsed: list[int] = []
            for value in found:
                card_id = _positive_id(value)
                if card_id is not None and card_id not in parsed:
                    parsed.append(card_id)
                elif card_id is None:
                    partial = True
            if len(parsed) > MAX_QUEUE_CARDS:
                partial = True
            selected = parsed[:MAX_QUEUE_CARDS]
            identities: dict[int, dict[str, int]] = {}
            card_info_failed = False
            for offset in range(0, len(selected), CARD_INFO_BATCH_SIZE):
                if time.monotonic() > self._timing.deadline:
                    partial = True
                    card_info_failed = True
                    break
                batch = selected[offset:offset + CARD_INFO_BATCH_SIZE]
                try:
                    rows = self._call("cardsInfo", cards=batch)
                except Exception:
                    partial = True
                    card_info_failed = True
                    break
                if not isinstance(rows, list):
                    partial = True
                    card_info_failed = True
                    continue
                returned: set[int] = set()
                for row in rows:
                    if not isinstance(row, dict):
                        partial = True
                        continue
                    cid = _positive_id(row.get("cardId"))
                    if cid is None or cid not in batch or cid in returned:
                        partial = True
                        continue
                    returned.add(cid)
                    note = _identity_integer(row.get("note"))
                    ordinal = _identity_integer(row.get("ord"))
                    queue_value = _identity_integer(row.get("queue"))
                    if note is None or note <= 0 or ordinal is None or ordinal < 0 or queue_value is None:
                        partial = True
                        continue
                    # A live identity row must refer to the requested ID and a
                    # queue that can be presented by the explicit due/new flow.
                    if queue_value < 0:
                        continue
                    identities[cid] = {"note": note, "ord": ordinal}
                if len(returned) != len(batch):
                    partial = True
            card_ids = [cid for cid in selected if cid in identities]
            if selected and not card_ids:
                self._timing.deadline = None
                with self._lock:
                    cached = sorted(self._queued.get(topic_id, set()))
                return {"ok": False, "topic_id": topic_id, "card_ids": cached,
                        "count": len(cached), "partial": bool(cached),
                        "reason": ("Anki card details are unavailable; no refreshed cards could be confirmed."
                                   if card_info_failed else
                                   "Anki returned card IDs, but no live card identities could be confirmed.")}
            self._bridge(topic_id, identities)
            with self._lock:
                self._queued[topic_id] = set(card_ids)
            reason = "Results are partial because Anki returned an incomplete or bounded card list." if partial else None
            self._timing.deadline = None
            return {"ok": True, "topic_id": topic_id, "card_ids": card_ids, "count": len(card_ids),
                    "partial": partial, "reason": reason}
        except Exception as exc:
            self._timing.deadline = None
            with self._lock:
                # Do not erase the last known queue on a transient offline error.
                cached = sorted(self._queued.get(topic_id, set()))
            return {"ok": False, "topic_id": topic_id, "card_ids": cached,
                    "count": len(cached), "partial": False,
                    "reason": "Anki is unavailable." if isinstance(exc, (OSError, TimeoutError, ConnectionError)) else "Anki could not provide this scoped queue."}

    def begin(self, topic_id: Any, card_id: Any) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            return {"ok": False, "topic_id": topic_id if isinstance(topic_id, str) else None, "reason": "Unknown topic."}
        cid = _positive_id(card_id)
        with self._lock:
            if cid is None or cid not in self._queued.get(topic_id, set()):
                return {"ok": False, "topic_id": topic_id, "reason": "Card is outside the current subject queue."}
            bridge = self._bridges.get(topic_id)
        if bridge is None:
            return {"ok": False, "topic_id": topic_id, "reason": "Refresh the subject queue before opening a card."}
        try:
            result = bridge.begin_review(cid)
            card = result.get("card", {}) if isinstance(result, dict) else {}
            if _requires_native_rendering(card):
                return {"ok": False, "topic_id": topic_id,
                        "reason": "This card uses image occlusion or interactive rendering. Review it in Anki to preserve its question."}
            media = card.get("media", {}) if isinstance(card, dict) else {}
            return {"ok": True, "topic_id": topic_id, "token": result["token"], "card_id": cid,
                    "front": _card_side(card.get("question"), media, front=True, css=card.get("css", "")),
                    "back": _card_side(card.get("answer"), media, front=False, css=card.get("css", "")),
                    "is_new": bool(card.get("isNew")), "is_due": bool(card.get("isDue"))}
        except Exception as exc:
            return {"ok": False, "topic_id": topic_id,
                    "reason": _safe_bridge_reason(exc)}

    def rate(self, topic_id: Any, token: Any, ease: Any, request_id: Any) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            return {"ok": False, "topic_id": topic_id if isinstance(topic_id, str) else None, "reason": "Unknown topic."}
        if not isinstance(token, str) or not token or len(token) > 200:
            return {"ok": False, "topic_id": topic_id, "reason": "Review token is invalid."}
        if isinstance(ease, bool) or not isinstance(ease, int) or ease not in (1, 2, 3, 4):
            return {"ok": False, "topic_id": topic_id, "reason": "Choose an explicit Anki rating from 1 through 4."}
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            return {"ok": False, "topic_id": topic_id, "reason": "Rating request ID is invalid."}
        with self._lock:
            bridge = self._bridges.get(topic_id)
        if bridge is None:
            return {"ok": False, "topic_id": topic_id, "reason": "Review session is stale."}
        try:
            # One card can have category tags for multiple subjects. Serialize
            # every live write across subject bridges so the second token sees
            # the first review's changed baseline rather than answering twice.
            with self._lock:
                result = bridge.submit_review(token, ease, request_id)
            return {"ok": True, "topic_id": topic_id, "card_id": result.get("cardId"),
                    "ease": result.get("ease"), "request_id": result.get("request_id"),
                    "confirmed": result.get("confirmed", False), "status": result.get("status", "unknown"),
                    "review_id": result.get("reviewId"), "detail": result.get("detail")}
        except Exception as exc:
            return {"ok": False, "topic_id": topic_id, "reason": _safe_bridge_reason(exc)}

    def _read_sessions(self) -> dict[str, Any]:
        try:
            value = json.loads(self._session_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_session(self, topic_id: str, session: dict[str, Any]) -> None:
        sessions = self._read_sessions()
        sessions[topic_id] = session
        pending = self._session_path.with_suffix(".tmp")
        pending.write_text(json.dumps(sessions, ensure_ascii=False), encoding="utf-8")
        pending.chmod(0o600)
        pending.replace(self._session_path)

    @staticmethod
    def _public_session(session: dict[str, Any] | None) -> dict[str, Any] | None:
        if session is None:
            return None
        results = session.get("results", [])
        current = session.get("current")
        last_confirmed = next((row for row in reversed(results) if row.get("status") == "confirmed"), {})
        return {key: session.get(key) for key in
                ("id", "topic_id", "status", "created_at", "position", "partial", "reason")} | {
            "total": len(session.get("card_ids", [])),
            "confirmed_count": sum(row.get("status") == "confirmed" for row in results),
            "skipped_count": sum(row.get("status") == "skipped" for row in results),
            "unknown_count": sum(row.get("status") == "unknown" for row in results),
            "last_confirmed_request_id": last_confirmed.get("request_id"),
            "last_confirmed_submitted_at": last_confirmed.get("submitted_at"),
            "current": ({key: current.get(key) for key in
                         ("card_id", "revealed", "request_id", "ease", "status")} if current else None),
        }

    def _session(self, topic_id: Any, session_id: Any = None) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            raise ValueError("Unknown subject")
        session = self._read_sessions().get(topic_id)
        if not isinstance(session, dict) or (session_id is not None and session.get("id") != session_id):
            raise ValueError("This Anki session changed. Reopen its subject")
        return session

    def confirmed_review(self, topic_id: Any, request_id: Any) -> dict[str, Any] | None:
        """Read trusted local confirmation; never send a rating or infer success."""
        if self._topic(topic_id) is None or not isinstance(request_id, str) or not request_id:
            return None
        with self._lock:
            bridge = self._bridge(topic_id)
            result = bridge._request_result(request_id)
            review = result.get("review") if result else None
            if (not result or not result.get("confirmed") or not isinstance(review, dict)
                    or _positive_id(result.get("reviewId")) is None
                    or isinstance(review.get("type"), bool) or review.get("type") not in (0, 1, 2)
                    or not isinstance(review.get("id"), int) or isinstance(review.get("id"), bool)
                    or not isinstance(review.get("ease"), int) or isinstance(review.get("ease"), bool)
                    or review["ease"] != result["ease"]):
                return None
            with closing(bridge._connect()) as conn:
                request = conn.execute("SELECT created_at FROM review_requests WHERE request_id=?", (request_id,)).fetchone()
            return {"confirmed": True, "topic_id": topic_id, "request_id": request_id,
                    "card_id": result["cardId"], "review_id": result["reviewId"], "ease": result["ease"],
                    "submitted_at": request["created_at"]}

    def _advance_session(self, topic_id: str, session: dict[str, Any], result: dict[str, Any]) -> None:
        session["results"].append(result)
        session["position"] += 1
        session["current"] = None
        if session["position"] >= len(session["card_ids"]):
            session["status"] = "finished"
        self._save_session(topic_id, session)

    def session_snapshot(self, topic_id: Any) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            return {"ok": False, "reason": "Unknown subject"}
        with self._lock:
            session = self._read_sessions().get(topic_id)
            if not isinstance(session, dict):
                return {"ok": True, "session": None}
            current = session.get("current")
            # If Anki committed before the service stopped, the bridge's ledger
            # survives. Repair only local session progress, without another write.
            if current and current.get("request_id"):
                accepted = self.confirmed_review(topic_id, current["request_id"])
                if accepted:
                    self._advance_session(topic_id, session, {**accepted, "status": "confirmed"})
            return {"ok": True, "session": self._public_session(session)}

    def start_session(self, topic_id: Any, limit: Any = 20) -> dict[str, Any]:
        if self._topic(topic_id) is None:
            return {"ok": False, "reason": "Unknown subject"}
        if isinstance(limit, bool) or not isinstance(limit, int) or limit not in (5, 10, 20, 50):
            return {"ok": False, "reason": "Choose a batch of 5, 10, 20, or 50 cards"}
        with self._lock:
            previous = self.session_snapshot(topic_id)["session"]
            if previous and previous["status"] == "active":
                return {"ok": True, "session": previous}
            queued = self.queue(topic_id)
            if not queued.get("ok"):
                return queued
            ids = queued["card_ids"][:limit]
            session = {"id": secrets.token_urlsafe(18), "topic_id": topic_id,
                       "created_at": time.time(), "status": "active" if ids else "finished",
                       "card_ids": ids, "position": 0, "results": [], "current": None,
                       "partial": queued["partial"], "reason": queued["reason"]}
            self._save_session(topic_id, session)
            return {"ok": True, "session": self._public_session(session)}

    def _restore_session_bridge(self, topic_id: str, current: dict[str, Any]) -> AnkiBridge:
        bridge = self._bridge(topic_id)
        with closing(bridge._connect()) as conn:
            row = conn.execute("SELECT card_id,expected_note,expected_ord FROM review_tokens WHERE token=?",
                               (current["token"],)).fetchone()
        if row is None or row["card_id"] != current["card_id"]:
            raise StaleReview("Stored review token is invalid")
        return self._bridge(topic_id, {row["card_id"]: {"note": row["expected_note"], "ord": row["expected_ord"]}})

    def open_session(self, topic_id: Any, session_id: Any) -> dict[str, Any]:
        with self._lock:
            try:
                self.session_snapshot(topic_id)
                session = self._session(topic_id, session_id)
                if session["status"] != "active":
                    return {"ok": True, "session": self._public_session(session), "card": None}
                self._timing.deadline = time.monotonic() + MAX_TRANSPORT_SECONDS
                current = session.get("current")
                if current:
                    bridge = self._restore_session_bridge(topic_id, current)
                    card = bridge.card_info(current["card_id"])
                    if _requires_native_rendering(card):
                        return {"ok": False, "session": self._public_session(session),
                                "reason": "This card uses image occlusion or interactive rendering. Review it in Anki to preserve its question."}
                    media = card.get("media", {})
                    front = _card_side(card.get("question"), media, front=True, css=card.get("css", ""))
                    back = _card_side(card.get("answer"), media, front=False, css=card.get("css", ""))
                else:
                    # A restart may have dropped the in-memory allowlist. The
                    # queue is revalidated before any new card token is opened.
                    cid = session["card_ids"][session["position"]]
                    if cid not in self._queued.get(topic_id, set()):
                        queued = self.queue(topic_id)
                        if not queued.get("ok"):
                            return queued
                    self._timing.deadline = time.monotonic() + MAX_TRANSPORT_SECONDS
                    opened = self.begin(topic_id, cid)
                    if not opened.get("ok"):
                        return {**opened, "session": self._public_session(session)}
                    current = {"card_id": cid, "token": opened["token"], "revealed": False,
                               "request_id": None, "ease": None, "status": "ready"}
                    front, back = opened["front"], opened["back"]
                    session["current"] = current
                    self._save_session(topic_id, session)
                return {"ok": True, "session": self._public_session(session),
                        "card": {"card_id": current["card_id"], "front": front,
                                 "back": back if current["revealed"] else None}}
            except Exception as exc:
                return {"ok": False, "reason": _safe_bridge_reason(exc)}
            finally:
                self._timing.deadline = None

    def reveal_session(self, topic_id: Any, session_id: Any) -> dict[str, Any]:
        with self._lock:
            try:
                session = self._session(topic_id, session_id)
                if session["status"] != "active" or not session.get("current"):
                    raise ValueError("Open a card before revealing it")
                session["current"]["revealed"] = True
                self._save_session(topic_id, session)
                return self.open_session(topic_id, session_id)
            except Exception as exc:
                return {"ok": False, "reason": _safe_bridge_reason(exc)}

    def rate_session(self, topic_id: Any, session_id: Any, ease: Any, request_id: Any) -> dict[str, Any]:
        with self._lock:
            try:
                session = self._session(topic_id, session_id)
                if isinstance(ease, bool) or ease not in (1, 2, 3, 4) or not isinstance(ease, int):
                    raise ValueError("Choose an explicit Anki rating")
                if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
                    raise ValueError("Invalid rating request")
                request_id = request_id.strip()
                previous = next((row for row in session["results"] if row.get("request_id") == request_id), None)
                if previous:
                    if previous["ease"] != ease:
                        raise ValueError("This request already has a different rating")
                    return {"ok": True, "session": self._public_session(session), "review": previous}
                current = session.get("current")
                if session["status"] != "active" or not current or not current["revealed"]:
                    raise ValueError("Reveal the answer before choosing a rating")
                if current.get("request_id") and (current["request_id"] != request_id or current["ease"] != ease):
                    raise ValueError("Check the existing rating before continuing")
                # Persist intent before invoking the bridge. An HTTP response
                # lost after a write is retried with this same identity only.
                current.update(request_id=request_id, ease=ease, status="pending")
                self._save_session(topic_id, session)
                bridge = self._restore_session_bridge(topic_id, current)
                self._timing.deadline = time.monotonic() + MAX_TRANSPORT_SECONDS
                result = self.rate(topic_id, current["token"], ease, request_id)
                if result.get("ok") and result.get("status") == "unknown":
                    # Recheck only history. Never repeat an uncertain answerCards.
                    with closing(bridge._connect()) as conn:
                        row = conn.execute("SELECT baseline_review_id FROM review_requests WHERE request_id=?",
                                           (request_id,)).fetchone()
                    if row:
                        try:
                            matched = bridge._reconcile(current["card_id"], ease, row["baseline_review_id"])
                            if matched and not isinstance(matched.get("type"), bool) and matched.get("type") in (0, 1, 2):
                                bridge._finish_request(request_id, "confirmed", card_id=current["card_id"],
                                                       ease=ease, review=matched, detail="Confirmed by a read-only history recheck")
                        except Exception:
                            pass
                accepted = self.confirmed_review(topic_id, request_id)
                if accepted:
                    self._advance_session(topic_id, session, {**accepted, "status": "confirmed"})
                    return {"ok": True, "session": self._public_session(session), "review": accepted}
                current["status"] = "unknown" if result.get("status") == "confirmed" else result.get("status", "unknown")
                self._save_session(topic_id, session)
                return {"ok": True, "session": self._public_session(session),
                        "review": {"confirmed": False, "status": current["status"], "request_id": request_id},
                        "reason": ("Anki did not return a valid normal learning or review record. This outcome remains unconfirmed."
                                   if result.get("status") == "confirmed" else
                                   result.get("reason") or result.get("detail") or "Anki has not confirmed this rating")}
            except Exception as exc:
                return {"ok": False, "reason": _safe_bridge_reason(exc)}
            finally:
                self._timing.deadline = None

    def skip_session(self, topic_id: Any, session_id: Any) -> dict[str, Any]:
        with self._lock:
            try:
                session = self._session(topic_id, session_id)
                if session["status"] != "active":
                    raise ValueError("This Anki session has finished")
                current = session.get("current")
                if current and current.get("request_id"):
                    raise ValueError("Check the existing rating before continuing")
                cid = session["card_ids"][session["position"]]
                self._advance_session(topic_id, session, {"card_id": cid, "status": "skipped"})
                return {"ok": True, "session": self._public_session(session)}
            except Exception as exc:
                return {"ok": False, "reason": _safe_bridge_reason(exc)}

    def dismiss_session(self, topic_id: Any, session_id: Any, request_id: Any) -> dict[str, Any]:
        """Leave an uncertain outcome recorded as unknown, without another write."""
        with self._lock:
            try:
                session = self._session(topic_id, session_id)
                if not isinstance(request_id, str) or not request_id or len(request_id) > 200:
                    raise ValueError("Choose the existing unconfirmed request")
                prior = next((row for row in session["results"] if row.get("request_id") == request_id), None)
                if prior:
                    return {"ok": True, "session": self._public_session(session), "review": prior}
                current = session.get("current")
                if session["status"] != "active" or not current or current.get("request_id") != request_id:
                    raise ValueError("Choose the existing unconfirmed request")
                accepted = self.confirmed_review(topic_id, request_id)
                if accepted:
                    self._advance_session(topic_id, session, {**accepted, "status": "confirmed"})
                    return {"ok": True, "session": self._public_session(session), "review": accepted}
                outcome = {"card_id": current["card_id"], "ease": current["ease"],
                           "request_id": request_id, "confirmed": False, "status": "unknown"}
                # Preserve the bridge's write-ahead record, even if it is still
                # pending. This action never invokes submit_review/answerCards.
                self._advance_session(topic_id, session, outcome)
                return {"ok": True, "session": self._public_session(session), "review": outcome}
            except Exception as exc:
                return {"ok": False, "reason": _safe_bridge_reason(exc)}

    def close(self) -> None:
        # AnkiBridge opens short-lived SQLite connections per operation; there
        # are no persistent file handles to close. Drop references explicitly.
        with self._lock:
            self._bridges.clear()
            self._identities.clear()
            self._queued.clear()
            self._closed = True


def _safe_bridge_reason(exc: Exception) -> str:
    if isinstance(exc, BridgeUnavailable):
        return "Anki is unavailable."
    if isinstance(exc, StaleReview):
        return "This review session is stale or has already been used."
    if isinstance(exc, ScopeError):
        return "This card is no longer eligible for this subject review."
    if isinstance(exc, ValueError):
        return "The Anki review request was invalid."
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return "Anki is unavailable."
    return "Anki could not complete this review request."
