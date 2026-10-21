"""Helpers that keep Instantly secrets out of logs and responses."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_HEADER_NAMES = {"authorization", "proxy-authorization", "x-api-key"}


def redact_secrets(text: str, *, api_key: str | None = None) -> str:
    """Remove API keys and Authorization header values from free-form text."""
    if not text:
        return text

    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED]")

    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)",
        r"\1[REDACTED]",
        redacted,
    )
    return redacted


def redact_headers(headers: dict[str, Any] | None) -> dict[str, Any]:
    if not headers:
        return {}
    safe: dict[str, Any] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADER_NAMES:
            safe[key] = "[REDACTED]"
        else:
            safe[key] = value
    return safe
