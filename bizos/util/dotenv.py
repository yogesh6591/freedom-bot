"""
Minimal .env loader
===================

Reads ``.env`` into ``os.environ`` without adding a dependency. Existing
environment variables always win, so a real deployment's injected configuration
is never overridden by a file that happened to be left in the image.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root — two levels up from this file (bizos/util/dotenv.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load ``KEY=value`` lines from ``path`` (default ``<root>/.env``)."""
    target = Path(path) if path else PROJECT_ROOT / ".env"
    loaded: dict[str, str] = {}
    if not target.is_file():
        return loaded
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded
