"""Dates and times as the business sees them: America/Chicago.

Everything typed by the user or shown to them in chat uses Chicago time, and replies say so.
A date with no time ("2026-10-29") means that whole day.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
TZ_NAME = "America/Chicago"
TZ_LABEL = "Chicago time"
_UTC = ZoneInfo("UTC")
_WORDS = ("today", "yesterday", "tomorrow")
_AMPM = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[ t]+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)$", re.I)


def now() -> datetime:
    return datetime.now(TZ)


def day_range(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=TZ)
    return start, start + timedelta(days=1)


def parse_range(text: str) -> tuple[datetime, datetime]:
    """A date, date-time, or today/yesterday/tomorrow as a [start, end) range. A bare date covers the whole day."""
    raw = (text or "").strip().lower()
    today = now().date()
    offsets = dict(zip(_WORDS, (0, -1, 1)))
    if raw in offsets:
        return day_range(today + timedelta(days=offsets[raw]))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return day_range(date.fromisoformat(raw))
        except ValueError:
            raise ValueError(f"'{text}' is not a real date.") from None
    moment = parse_moment(text)
    return moment, moment + timedelta(minutes=1)


def parse_moment(text: str) -> datetime:
    """An exact moment. Without a time zone it is Chicago time. Needs a time of day, not just a date."""
    raw = (text or "").strip()
    match = _AMPM.match(raw)
    try:
        if match:
            hour = int(match["h"]) % 12 + (12 if match["ap"].lower() == "pm" else 0)
            moment = datetime.combine(date.fromisoformat(match["date"]), time(hour, int(match["m"] or 0)))
        else:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raise ValueError
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"'{text}' is not a date and time. Use YYYY-MM-DD HH:MM (Chicago time), for example 2026-11-05 14:30."
        ) from None
    return moment if moment.tzinfo else moment.replace(tzinfo=TZ)


def fmt_time(value: Optional[datetime]) -> str:
    """A moment in Chicago time, e.g. ``Oct 30 2026 10:00 AM`` (``-`` when empty)."""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value.astimezone(TZ).strftime("%b %d %Y %I:%M %p").replace(" 0", " ")


def fmt_day(value: Optional[datetime]) -> str:
    """A date in Chicago time, e.g. ``Oct 30`` (``?`` when empty)."""
    if value is None:
        return "?"
    if value.tzinfo is None:
        value = value.replace(tzinfo=_UTC)
    return value.astimezone(TZ).strftime("%b %d").replace(" 0", " ")


def to_utc_iso(value: datetime) -> str:
    """``2026-11-05T20:30:00Z`` — the form the Cal.com API takes."""
    return value.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
