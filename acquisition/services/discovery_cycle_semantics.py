"""Discovery cycle semantics: usable results vs provisional candidates.

Admin "Prospects per run" (discovery_cycle_limit) means how many FINAL usable
researched/scored prospects a cycle should produce — not how many raw search
hits to accept before research.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from acquisition.config import Settings
from acquisition.models.prospect import AcquisitionStatus, Prospect

# Completed the intended discovery → research → scoring pipeline in a usable state.
USABLE_ACQUISITION_STATUSES: frozenset[str] = frozenset(
    {
        AcquisitionStatus.READY_FOR_OUTREACH.value,
        AcquisitionStatus.SCORED.value,
    }
)

# Final states that must not consume the Admin usable quota.
NON_USABLE_ACQUISITION_STATUSES: frozenset[str] = frozenset(
    {
        AcquisitionStatus.EXCLUDED.value,
        AcquisitionStatus.UNREACHABLE.value,
        AcquisitionStatus.NEEDS_MORE_RESEARCH.value,
        AcquisitionStatus.DISCOVERED.value,
        AcquisitionStatus.RESEARCHED.value,
        AcquisitionStatus.READY_FOR_SCORING.value,
    }
)


def status_value(status: AcquisitionStatus | str | None) -> str | None:
    if status is None:
        return None
    if isinstance(status, AcquisitionStatus):
        return status.value
    text = str(status).strip()
    return text or None


def is_usable_discovery_result(status: AcquisitionStatus | str | None) -> bool:
    """True when a prospect successfully finished research+scoring in a usable state."""
    value = status_value(status)
    return bool(value and value in USABLE_ACQUISITION_STATUSES)


def effective_usable_limit(settings: Settings, limit: int | None, icp_limit: int | None) -> int:
    configured = int(icp_limit or 0) or int(settings.discovery_cycle_limit or 5)
    hard_max = int(settings.discovery_cycle_hard_max or 25)
    requested = configured if limit is None else int(limit)
    return max(1, min(requested, hard_max))


def effective_scan_cap(settings: Settings, *, usable_limit: int) -> int:
    """Max provisional candidates to inspect while hunting for usable prospects."""
    scan = int(getattr(settings, "discovery_scan_hard_max", 20) or 20)
    hard = int(settings.discovery_cycle_hard_max or 25)
    return max(int(usable_limit), min(scan, hard))


def excluded_refresh_days(settings: Settings) -> int:
    return max(1, int(getattr(settings, "discovery_excluded_refresh_days", 30) or 30))


def should_skip_confidently_excluded(
    prospect: Prospect,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    """Skip re-research when an existing prospect is already confidently excluded.

    Allows refresh after discovery_excluded_refresh_days so old exclusions can
    be revisited without burning capacity every cycle.
    """
    if prospect.acquisition_status != AcquisitionStatus.EXCLUDED:
        return False
    anchor = prospect.last_researched_at or prospect.scored_at
    if anchor is None:
        # Excluded without timestamps is still treated as confident enough to skip.
        return True
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - anchor) < timedelta(days=excluded_refresh_days(settings))


def progress_reason_for_status(status: AcquisitionStatus | str | None) -> str:
    value = status_value(status) or "unknown"
    if value == AcquisitionStatus.EXCLUDED.value:
        return "Candidate excluded after research"
    if value == AcquisitionStatus.UNREACHABLE.value:
        return "Candidate unreachable after research"
    if value == AcquisitionStatus.NEEDS_MORE_RESEARCH.value:
        return "Candidate needs more research"
    if is_usable_discovery_result(value):
        return "Usable prospect persisted"
    return f"Candidate not usable ({value})"


def summarize_stop_reason(
    *,
    usable_found: int,
    usable_limit: int,
    candidates_inspected: int,
    scan_cap: int,
    candidate_pool: int,
) -> str:
    if usable_found >= usable_limit:
        return "Requested usable prospect count reached"
    if candidates_inspected >= scan_cap:
        return "Scan hard cap reached"
    if candidate_pool <= 0:
        return "No discovery candidates available"
    if candidates_inspected >= candidate_pool:
        return "Candidate search exhausted"
    return "Discovery cycle ended"


def outcome_acquisition_status(outcome: dict[str, Any] | None) -> str | None:
    if not outcome:
        return None
    direct = outcome.get("acquisition_status")
    if direct:
        return status_value(direct)
    scoring = outcome.get("scoring") or {}
    return status_value(scoring.get("acquisition_status"))
