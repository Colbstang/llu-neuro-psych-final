#!/usr/bin/env python3
"""Import only Anki note/card IDs from a local export into private app storage.

The input JavaScript is parsed as a JSON assignment and is never executed. Card
text, media, tags, and scheduling data are not copied into the app scope file.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

try:
    from .anki_bridge import _allowed_cards, _parse_export
    from .app_paths import default_data_dir, secure_data_dir
except ImportError:  # Direct invocation as ``python3 StudyApp/import_anki_scope.py``.
    from anki_bridge import _allowed_cards, _parse_export
    from app_paths import default_data_dir, secure_data_dir


def import_scope(source: Path, data_dir: Path) -> int:
    exported = _parse_export(source)
    allowed = _allowed_cards(exported)
    payload = {str(card_id): identity for card_id, identity in sorted(allowed.items())}
    data_dir = secure_data_dir(data_dir)
    target = data_dir / "anki-scope.json"
    fd, temporary_name = tempfile.mkstemp(prefix=".anki-scope-", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.write("\n")
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return len(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Anki context JavaScript export")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(),
                        help="Private app data folder (default: per-user app storage)")
    args = parser.parse_args()
    try:
        count = import_scope(args.export.expanduser().resolve(), args.data_dir.expanduser().resolve())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"Imported a local Anki allowlist for {count} cards; card content was not copied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
