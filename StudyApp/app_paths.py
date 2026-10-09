"""Per-user storage paths for the portable local app."""
from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    """Return a private per-user directory outside the public checkout."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "LLU Study"
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "LLU Study"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "llu-study"


def secure_data_dir(path: str | os.PathLike[str] | None = None) -> Path:
    """Create private app storage with owner-only access where supported."""
    directory = Path(path).expanduser() if path is not None else default_data_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    return directory.resolve()
