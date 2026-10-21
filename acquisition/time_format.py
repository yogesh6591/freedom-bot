"""12-hour / 24-hour time helpers for sending schedule UI and Admin clocks."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from acquisition.timezones import APP_DISPLAY_TIMEZONE, resolve_zone

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: str) -> tuple[int, int]:
    cleaned = (value or "").strip()
    match = _HHMM.match(cleaned)
    if not match:
        raise ValueError("must be HH:MM in 24-hour format")
    return int(match.group(1)), int(match.group(2))


def to_12h_parts(hhmm: str) -> dict[str, str]:
    """Split stored HH:MM into UI select values."""
    hour24, minute = parse_hhmm(hhmm)
    period = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12
    if hour12 == 0:
        hour12 = 12
    return {
        "hour": str(hour12),
        "minute": f"{minute:02d}",
        "period": period,
    }


def from_12h_parts(hour: str | int, minute: str | int, period: str) -> str:
    """Build stored HH:MM from 12-hour UI parts."""
    try:
        hour12 = int(hour)
        minute_i = int(minute)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid time") from exc
    if hour12 < 1 or hour12 > 12 or minute_i < 0 or minute_i > 59:
        raise ValueError("Invalid time")
    period_norm = (period or "").strip().upper()
    if period_norm not in {"AM", "PM"}:
        raise ValueError("Period must be AM or PM")

    if period_norm == "AM":
        hour24 = 0 if hour12 == 12 else hour12
    else:
        hour24 = 12 if hour12 == 12 else hour12 + 12
    return f"{hour24:02d}:{minute_i:02d}"


def format_12h(hhmm: str) -> str:
    """Format stored HH:MM as '9:00 AM'."""
    parts = to_12h_parts(hhmm)
    minute = parts["minute"]
    hour = parts["hour"]
    return f"{hour}:{minute} {parts['period']}"


def format_datetime_12h(
    value: datetime | None,
    *,
    timezone_name: str | None = None,
) -> str:
    """Format a datetime for UI in Chicago by default, e.g. 'Sep 22, 2026 at 6:33 AM'."""
    if value is None:
        return "—"

    dt = value
    if dt.tzinfo is None:
        # Naive values are treated as UTC storage instants.
        dt = dt.replace(tzinfo=timezone.utc)

    try:
        from acquisition.config import get_settings

        default_tz = get_settings().app_display_timezone or APP_DISPLAY_TIMEZONE
    except Exception:  # noqa: BLE001 — settings may be unavailable in pure unit tests
        default_tz = APP_DISPLAY_TIMEZONE

    tz = resolve_zone(timezone_name or default_tz, fallback=APP_DISPLAY_TIMEZONE)
    local = dt.astimezone(tz)
    hour12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return (
        f"{local.strftime('%b')} {local.day}, {local.year} "
        f"at {hour12}:{local.strftime('%M')} {period}"
    )


def _format_clock(local: datetime) -> str:
    hour12 = local.hour % 12 or 12
    period = "AM" if local.hour < 12 else "PM"
    return f"{hour12}:{local.strftime('%M')} {period}"


def format_meeting_card(
    *,
    start: datetime | None,
    end: datetime | None,
    timezone_name: str | None,
) -> dict[str, str | None]:
    """Human-readable meeting labels (platform timezone — Chicago by default)."""
    if start is None:
        return {
            "date_label": None,
            "time_label": None,
            "timezone_label": None,
        }

    dt = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    try:
        from acquisition.config import get_settings

        default_tz = get_settings().app_display_timezone or APP_DISPLAY_TIMEZONE
    except Exception:  # noqa: BLE001
        default_tz = APP_DISPLAY_TIMEZONE
    tz = resolve_zone(timezone_name or default_tz, fallback=APP_DISPLAY_TIMEZONE)
    local_start = dt.astimezone(tz)
    date_label = local_start.strftime("%A, %B ") + str(local_start.day)

    if end is not None:
        end_dt = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        local_end = end_dt.astimezone(tz)
        time_label = f"{_format_clock(local_start)} – {_format_clock(local_end)}"
    else:
        time_label = _format_clock(local_start)

    return {
        "date_label": date_label,
        "time_label": time_label,
        "timezone_label": None,
    }


def split_prep_blocks(text: str | None) -> list[dict[str, object]]:
    """Split Admin prep body into paragraph / bullet blocks for polished rendering."""
    raw = (text or "").strip()
    if not raw:
        return []
    blocks: list[dict[str, object]] = []
    for chunk in re.split(r"\n\s*\n", raw):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        bulletish = all(
            ln.startswith(("- ", "• ", "* ")) or ln[:1] in {"-", "•", "*"} for ln in lines
        )
        if bulletish and len(lines) > 1:
            items = []
            for ln in lines:
                cleaned = re.sub(r"^[-•*]\s*", "", ln).strip()
                if cleaned:
                    items.append(cleaned)
            if items:
                blocks.append({"type": "bullets", "items": items[:4]})
            continue

        paragraph = " ".join(lines)
        # Long prose → short readable bullets (display only; Admin text unchanged).
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", paragraph)
            if s.strip()
        ]
        if len(sentences) >= 2 and len(paragraph) > 140:
            blocks.append({"type": "bullets", "items": sentences[:4]})
        else:
            blocks.append({"type": "paragraph", "text": paragraph})
    return blocks
