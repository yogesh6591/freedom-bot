"""Timestamp helpers. Everything the platform stores is timezone-aware UTC."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    """Current time as an ISO-8601 string with offset."""
    return utcnow().isoformat()


def in_seconds(seconds: int) -> datetime:
    """A UTC datetime ``seconds`` in the future."""
    return utcnow() + timedelta(seconds=seconds)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime; pass through aware ones and ``None``."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
