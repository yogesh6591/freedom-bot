"""Timezone policy for FreedomBot.

The whole platform is America/Chicago (US Central) based:
- Admin UI timestamps
- Public journey / booking / After Booking labels
- Cal.com embed + host account timezone
- Instantly send schedule default

Database timestamps are stored as timezone-aware UTC and converted on display.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Platform display + booking + Instantly schedule clocks (US Central / Chicago).
APP_DISPLAY_TIMEZONE = "America/Chicago"
INSTANTLY_DEFAULT_TIMEZONE = "America/Chicago"


def resolve_zone(name: str | None, *, fallback: str = APP_DISPLAY_TIMEZONE) -> ZoneInfo:
    candidate = (name or fallback or APP_DISPLAY_TIMEZONE).strip()
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        try:
            return ZoneInfo(fallback)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(APP_DISPLAY_TIMEZONE)


def platform_timezone_name() -> str:
    """Resolved FreedomBot display/booking timezone (Chicago by default)."""
    try:
        from acquisition.config import get_settings

        raw = (get_settings().app_display_timezone or APP_DISPLAY_TIMEZONE).strip()
        if raw:
            resolve_zone(raw, fallback=APP_DISPLAY_TIMEZONE)
            return raw
    except Exception:  # noqa: BLE001
        pass
    return APP_DISPLAY_TIMEZONE


def utc_now() -> datetime:
    """Timezone-aware UTC instant for persistence (convert to Chicago when showing)."""
    return datetime.now(timezone.utc)
