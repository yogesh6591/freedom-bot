"""Assessment token helpers and public personalized URL builder."""

from __future__ import annotations

import secrets
from urllib.parse import urljoin

from acquisition.config import Settings, get_settings


def generate_assessment_token() -> str:
    return secrets.token_urlsafe(24)


def build_assessment_url(token: str, *, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    base = (cfg.public_base_url or "").rstrip("/") + "/"
    return urljoin(base, f"a/{token}")


def split_executive_name(full_name: str) -> tuple[str, str]:
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])
