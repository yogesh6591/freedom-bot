"""Timestamp helpers. Everything the platform stores is timezone-aware UTC; the workspace timezone is
only for reading and showing local times."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


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


#: The workspace timezone when none is set (most users are in India).
DEFAULT_TIMEZONE = "Asia/Kolkata"


def is_timezone(name: object) -> bool:
    """True when ``name`` is an IANA timezone name, e.g. ``Asia/Kolkata``."""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        return False
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        return False
    return True


def workspace_zone(settings: object) -> ZoneInfo:
    """The workspace's timezone; the default when the stored name is missing or invalid."""
    name = getattr(settings, "timezone", None)
    return ZoneInfo(name if is_timezone(name) else DEFAULT_TIMEZONE)


def local_now(settings: object) -> datetime:
    """Current time in the workspace's timezone."""
    return datetime.now(workspace_zone(settings))
