"""V1 capacity throttling for NEW autonomous outreach activation only.

Discovery/research are never stopped by capacity state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.campaign import CampaignProspect, JourneyStage
from acquisition.services.icp_settings_service import IcpSettingsService


class CapacityState(str, Enum):
    NORMAL = "normal"
    REDUCED = "reduced"
    PAUSED = "paused"


@dataclass(slots=True)
class CapacityAssessment:
    state: CapacityState
    upcoming_meetings: int
    mode: str  # auto|normal|reduced|paused
    normal_limit: int
    effective_activation_limit: int
    reasons: list[str]
    automatic_calendar_available: bool


class AcquisitionCapacityService:
    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)

    def count_upcoming_confirmed_meetings(self) -> int:
        now = datetime.now(timezone.utc)
        stmt = select(func.count()).select_from(CampaignProspect).where(
            CampaignProspect.meeting_start.is_not(None),
            CampaignProspect.meeting_start > now,
            or_(
                func.lower(CampaignProspect.booking_status) == "confirmed",
                CampaignProspect.journey_stage == JourneyStage.BOOKED,
                CampaignProspect.booked_at.is_not(None),
            ),
        )
        return int(self.db.scalar(stmt) or 0)

    def assess(self) -> CapacityAssessment:
        cfg = self.icp.as_dict()
        mode = str(cfg.get("capacity_mode") or "auto").lower()
        normal_limit = int(cfg.get("activation_batch_limit") or 5)
        hard = int(self.settings.activation_batch_hard_max or 25)
        normal_limit = max(0, min(normal_limit, hard))
        reduced_limit = min(
            normal_limit,
            max(0, int(cfg.get("capacity_reduced_activation_limit") or 2)),
        )
        reduced_at = int(cfg.get("capacity_upcoming_meetings_reduced_at") or 5)
        paused_at = int(cfg.get("capacity_upcoming_meetings_paused_at") or 10)
        upcoming = self.count_upcoming_confirmed_meetings()
        reasons: list[str] = []
        automatic = True

        if mode in {"normal", "reduced", "paused"}:
            state = CapacityState(mode)
            reasons.append(f"manual_capacity_mode={mode}")
            automatic = False
        else:
            # auto
            if upcoming >= paused_at:
                state = CapacityState.PAUSED
                reasons.append(
                    f"upcoming_confirmed_meetings={upcoming} >= paused_at={paused_at}"
                )
            elif upcoming >= reduced_at:
                state = CapacityState.REDUCED
                reasons.append(
                    f"upcoming_confirmed_meetings={upcoming} >= reduced_at={reduced_at}"
                )
            else:
                state = CapacityState.NORMAL
                reasons.append(
                    f"upcoming_confirmed_meetings={upcoming} below reduced_at={reduced_at}"
                )

        if state == CapacityState.PAUSED:
            effective = 0
        elif state == CapacityState.REDUCED:
            effective = reduced_limit
        else:
            effective = normal_limit

        return CapacityAssessment(
            state=state,
            upcoming_meetings=upcoming,
            mode=mode,
            normal_limit=normal_limit,
            effective_activation_limit=effective,
            reasons=reasons,
            automatic_calendar_available=True,
        )

    def as_dict(self) -> dict[str, Any]:
        a = self.assess()
        return {
            "state": a.state.value,
            "upcoming_meetings": a.upcoming_meetings,
            "mode": a.mode,
            "normal_limit": a.normal_limit,
            "effective_activation_limit": a.effective_activation_limit,
            "reasons": a.reasons,
            "automatic_calendar_available": a.automatic_calendar_available,
            "affects": "new_outreach_activation_only",
        }
