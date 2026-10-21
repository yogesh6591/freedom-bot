"""Map FreedomBot Campaign + messages → Instantly API V2 payload.

Instantly delay semantics (official V2):
  step.delay / delay_unit = wait before sending the NEXT email.

FreedomBot stores delay_after_previous_hours on each message (wait before THIS message).

Mapping:
  - Active messages only, ordered by sequence_order
  - Instantly step[i] content ← message[i]
  - Instantly step[i].delay ← message[i+1].delay_after_previous_hours (hours)
  - Last step delay = 0
  - First message must have delay_after_previous_hours == 0 (immediate start);
    Instantly regular campaigns ignore pre_delay, so a non-zero initial delay cannot
    be represented exactly and sync is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from acquisition.models.campaign import Campaign, CampaignMessage, MessageChannel, MessageType
from acquisition.time_format import format_12h
from acquisition.integrations.instantly.timezones import (
    INSTANTLY_TIMEZONE_SET,
    normalize_instantly_timezone,
)
from acquisition.services.email_body import plain_text_to_email_html

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class InstantlyMappingError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InstantlySequencePreviewStep:
    order: int
    label: str
    subject: str
    delay_label: str
    delay_hours: int


@dataclass(frozen=True, slots=True)
class InstantlyCampaignPreview:
    name: str
    schedule_label: str
    timezone: str
    window_start: str
    window_end: str
    days_label: str
    steps: list[InstantlySequencePreviewStep]


def build_instantly_campaign_payload(campaign: Campaign) -> dict[str, Any]:
    """Build Instantly V2 create/patch body from a FreedomBot campaign."""
    _validate_schedule(campaign)
    active = _active_email_messages(campaign)
    if not active:
        raise InstantlyMappingError("At least one active email message is required to sync")

    first = active[0]
    if first.delay_after_previous_hours != 0:
        raise InstantlyMappingError(
            "Initial email delay must be 0 hours for Instantly sync "
            "(Instantly regular campaigns cannot represent a pre-start delay exactly)"
        )

    steps: list[dict[str, Any]] = []
    for index, message in enumerate(active):
        subject = (message.subject or "").strip()
        body = (message.body or "").strip()
        if not subject or not body:
            raise InstantlyMappingError(
                f"Active message sequence_order={message.sequence_order} is missing subject or body"
            )

        if index + 1 < len(active):
            next_delay = active[index + 1].delay_after_previous_hours
            delay_value, delay_unit = _hours_to_instantly_delay(next_delay)
        else:
            delay_value, delay_unit = 0, "hours"

        steps.append(
            {
                "type": "email",
                "delay": delay_value,
                "delay_unit": delay_unit,
                "variants": [
                    {
                        "subject": subject,
                        "body": plain_text_to_email_html(body),
                    }
                ],
            }
        )

    return {
        "name": campaign.name.strip(),
        "campaign_schedule": {
            "schedules": [
                {
                    "name": "Default Schedule",
                    "timing": {
                        "from": campaign.send_window_start,
                        # Instantly API accepts 23:59, but its Settings UI dropdown
                        # has no 11:59 PM option — To renders blank. Use 23:00.
                        "to": _timing_to_for_instantly(campaign.send_window_end),
                    },
                    "days": _days_payload(campaign),
                    "timezone": normalize_instantly_timezone(campaign.send_timezone),
                }
            ]
        },
        "sequences": [{"steps": steps}],
    }


def build_instantly_campaign_preview(campaign: Campaign) -> InstantlyCampaignPreview:
    """Human-readable preview derived from the same mapping rules as the API payload."""
    payload = build_instantly_campaign_payload(campaign)
    active = _active_email_messages(campaign)
    schedule = payload["campaign_schedule"]["schedules"][0]
    days_label = _days_label(campaign)
    steps: list[InstantlySequencePreviewStep] = []
    for index, message in enumerate(active):
        if index == 0:
            delay_hours = 0
            delay_label = "Immediate"
        else:
            delay_hours = message.delay_after_previous_hours
            delay_label = _delay_preview_label(delay_hours)
        label = "Initial Email" if message.message_type == MessageType.INITIAL else "Follow-up"
        steps.append(
            InstantlySequencePreviewStep(
                order=index + 1,
                label=label,
                subject=(message.subject or "").strip(),
                delay_label=delay_label,
                delay_hours=delay_hours,
            )
        )
    return InstantlyCampaignPreview(
        name=payload["name"],
        schedule_label=f"{days_label}, {format_12h(schedule['timing']['from'])}–{format_12h(schedule['timing']['to'])}, {schedule['timezone']}",
        timezone=schedule["timezone"],
        window_start=schedule["timing"]["from"],
        window_end=schedule["timing"]["to"],
        days_label=days_label,
        steps=steps,
    )


def _active_email_messages(campaign: Campaign) -> list[CampaignMessage]:
    messages = [
        m
        for m in sorted(campaign.messages, key=lambda item: item.sequence_order)
        if m.is_active and m.channel == MessageChannel.EMAIL
    ]
    return messages


def _hours_to_instantly_delay(hours: int) -> tuple[int, str]:
    """Convert FreedomBot hour delays to Instantly delay + unit without rounding."""
    if not isinstance(hours, int) or hours < 0:
        raise InstantlyMappingError(
            f"Delay of {hours!r} hours cannot be represented exactly for Instantly"
        )
    # Instantly supports minutes/hours/days. We store whole hours → always exact as hours.
    return hours, "hours"


def _validate_schedule(campaign: Campaign) -> None:
    if not (campaign.send_timezone or "").strip():
        raise InstantlyMappingError("Send timezone is required")
    timezone = normalize_instantly_timezone(campaign.send_timezone)
    if timezone not in INSTANTLY_TIMEZONE_SET:
        raise InstantlyMappingError(
            f"Timezone {campaign.send_timezone!r} is not supported by Instantly. "
            "Pick a value from the Schedule timezone list (e.g. America/Chicago)."
        )
    if not _HHMM.match(campaign.send_window_start or ""):
        raise InstantlyMappingError("Send window start must be HH:MM (24-hour)")
    if not _HHMM.match(campaign.send_window_end or ""):
        raise InstantlyMappingError("Send window end must be HH:MM (24-hour)")
    if campaign.send_window_start >= campaign.send_window_end:
        raise InstantlyMappingError("Send window start must be before send window end")
    days = [
        campaign.send_sunday,
        campaign.send_monday,
        campaign.send_tuesday,
        campaign.send_wednesday,
        campaign.send_thursday,
        campaign.send_friday,
        campaign.send_saturday,
    ]
    if not any(days):
        raise InstantlyMappingError("At least one send day must be enabled")


def _timing_to_for_instantly(hhmm: str) -> str:
    """Normalize send-window end for Instantly Settings UI compatibility.

    Instantly accepts any HH:MM via API, but its schedule dropdowns typically
    omit 23:59 — the To field then shows empty even though the value is stored.
    """
    cleaned = (hhmm or "").strip()
    if cleaned == "23:59":
        return "23:00"
    return cleaned


def _days_payload(campaign: Campaign) -> dict[str, bool]:
    # Instantly day keys: 0=Sunday … 6=Saturday
    return {
        "0": bool(campaign.send_sunday),
        "1": bool(campaign.send_monday),
        "2": bool(campaign.send_tuesday),
        "3": bool(campaign.send_wednesday),
        "4": bool(campaign.send_thursday),
        "5": bool(campaign.send_friday),
        "6": bool(campaign.send_saturday),
    }


def _days_label(campaign: Campaign) -> str:
    enabled = []
    mapping = [
        (campaign.send_monday, "Mon"),
        (campaign.send_tuesday, "Tue"),
        (campaign.send_wednesday, "Wed"),
        (campaign.send_thursday, "Thu"),
        (campaign.send_friday, "Fri"),
        (campaign.send_saturday, "Sat"),
        (campaign.send_sunday, "Sun"),
    ]
    for enabled_day, label in mapping:
        if enabled_day:
            enabled.append(label)
    if enabled == ["Mon", "Tue", "Wed", "Thu", "Fri"]:
        return "Mon-Fri"
    if len(enabled) == 7:
        return "Every day"
    return ", ".join(enabled) if enabled else "No days"


def _delay_preview_label(hours: int) -> str:
    if hours == 0:
        return "Immediate"
    if hours % 24 == 0 and hours >= 24:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''} ({hours} hours)"
    return f"{hours} hours"
