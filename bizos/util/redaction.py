"""
Secret Redaction
================

§9 and §14 of the brief: audit records must be useful without ever containing
passwords, OAuth tokens, API keys or payment-card values. This module is the
single chokepoint every audit write and every structured log call passes
through.

Two complementary strategies, because either alone leaks:

1. **Key-name matching** catches structured payloads — a dict key called
   ``refresh_token`` is redacted whatever its value looks like.
2. **Value-pattern matching** catches secrets that arrive inside free text —
   a bearer token pasted into an email body has no helpful key name.

Redaction is deliberately lossy and irreversible. A redacted payload keeps its
*shape* (so an auditor can see that a token was present) but not its content.
"""

from __future__ import annotations

import re
from typing import Any

#: Placeholder written in place of a secret. Distinctive enough to grep for.
REDACTED = "[REDACTED]"

#: Substrings that mark a *key* as secret-bearing. Matched case-insensitively
#: against the whole key, so ``oauth_refresh_token`` and ``clientSecret`` both hit.
_SECRET_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
    "auth_header",
    "cookie",
    "session_key",
    "signing_key",
    "client_secret",
    "cvv",
    "cvc",
    "card_number",
    "cardnumber",
    "pan",
    "ssn",
)

#: Keys that contain one of the parts above but are *not* secrets. Without this,
#: routine audit fields like ``token_usage`` or ``approval_token_count`` would be
#: destroyed, making the audit log less useful for no security gain.
_SECRET_KEY_EXCEPTIONS = frozenset(
    {
        "token_usage",
        "tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "token_count",
        "has_token",
        "token_expires_at",
        "credential_type",
        "secret_configured",
    }
)

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Authorization header values, with or without the header name.
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/=]{12,}")),
    ("basic", re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{12,}")),
    # Provider key formats.
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("google_oauth", re.compile(r"\bya29\.[A-Za-z0-9_\-]{10,}")),
    ("google_refresh", re.compile(r"\b1//[A-Za-z0-9_\-]{20,}")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("aws", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # A JWT: three base64url segments. Catches any token we mint or receive.
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # Postgres/MySQL/Mongo URLs with inline credentials.
    ("db_url", re.compile(r"(?i)\b[a-z0-9+]{2,20}://[^\s:/@]+:[^\s@]+@")),
    # Payment card numbers (13-19 digits, optionally grouped). Never stored.
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
)

#: Maximum length of any single string kept in an audit payload. Long blobs are
#: truncated: an audit record is evidence of *what happened*, not a data store.
_MAX_STRING = 2000


def is_secret_key(key: str) -> bool:
    """Whether a mapping key names a secret and must be redacted wholesale."""
    folded = key.strip().casefold()
    if folded in _SECRET_KEY_EXCEPTIONS:
        return False
    return any(part in folded for part in _SECRET_KEY_PARTS)


def redact_text(value: str) -> str:
    """Replace secret-shaped substrings in free text."""
    if not value:
        return value
    redacted = value
    for _name, pattern in _VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    if len(redacted) > _MAX_STRING:
        redacted = f"{redacted[:_MAX_STRING]}…[truncated {len(redacted) - _MAX_STRING} chars]"
    return redacted


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets from an arbitrary JSON-ish value.

    Containers keep their structure so the audit record still shows which fields
    were present. Depth is bounded so a self-referential or pathological payload
    cannot spin here.
    """
    if _depth > 12:
        return "[REDACTED: max depth]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and is_secret_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact(item, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth=_depth + 1) for item in value]
    # Anything else (datetime, Decimal, dataclass, model object) is stringified
    # and then scrubbed, so an unexpected type can never smuggle a raw secret.
    return redact_text(str(value))
