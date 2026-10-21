"""Priority queue for autonomous outreach activation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.config import Settings, get_settings
from acquisition.models.campaign import CampaignProspect, OutreachStatus, PurchaseOutcome
from acquisition.models.prospect import AcquisitionStatus, Prospect
from acquisition.models.replies import ReplyConversation
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.outreach_conflict_service import (
    find_active_outreach_conflict,
    normalize_outreach_email,
)
from acquisition.services.prospect_identity import is_placeholder_email


@dataclass(slots=True)
class QueueEligibility:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    exclusion_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class QueueEntry:
    prospect_id: uuid.UUID
    company: str
    email: str
    priority_score: float
    fit_score: float | None
    timing_score: float | None
    reachability_score: float | None
    acquisition_status: str
    inclusion_reasons: list[str]
    exclusion_reasons: list[str] = field(default_factory=list)


class ProspectPriorityQueueService:
    """Select and rank prospects ready for autonomous Instantly activation."""

    def __init__(self, db: Session, *, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)

    def evaluate_eligibility(self, prospect: Prospect) -> QueueEligibility:
        reasons: list[str] = []
        exclusions: list[str] = []

        status = prospect.acquisition_status
        if status != AcquisitionStatus.READY_FOR_OUTREACH:
            exclusions.append(f"acquisition_status={status.value if status else None}")
        else:
            reasons.append("acquisition_status=ready_for_outreach")

        if status == AcquisitionStatus.EXCLUDED:
            exclusions.append("excluded_by_icp_or_scoring")
        if status == AcquisitionStatus.UNREACHABLE:
            exclusions.append("unreachable")

        email = (prospect.email or "").strip()
        conf = (prospect.email_confidence or "").strip().lower()
        if not email or is_placeholder_email(email) or conf in {"placeholder", "none", "missing"}:
            exclusions.append("no_legitimate_business_contact_path")
        else:
            reasons.append("legitimate_business_email_present")

        if self._is_customer(prospect):
            exclusions.append("existing_customer")
        if self._is_opted_out_or_bounced(prospect):
            exclusions.append("opted_out_or_bounced")
        if self._has_active_conversation(prospect):
            exclusions.append("active_conversation_exists")

        conflict = find_active_outreach_conflict(
            self.db,
            prospect_id=prospect.id,
            email=prospect.email,
        )
        if conflict is not None:
            exclusions.append(f"active_outreach_conflict:{conflict.blocking_campaign_name}")
        else:
            reasons.append("no_active_outreach_conflict")

        if prospect.priority_score is None:
            exclusions.append("missing_priority_score")
        else:
            reasons.append(f"priority_score={prospect.priority_score:.1f}")

        eligible = not exclusions
        return QueueEligibility(eligible=eligible, reasons=reasons, exclusion_reasons=exclusions)

    def build_activation_queue(self, *, limit: int | None = None) -> dict[str, Any]:
        """Return ranked eligible prospects + skipped with reasons."""
        cap = self._effective_limit(limit)
        learning_on = bool(self.icp.as_dict().get("learning_enabled"))
        learning_svc = None
        if learning_on:
            from acquisition.services.acquisition_learning_service import AcquisitionLearningService

            learning_svc = AcquisitionLearningService(self.db, settings=self.settings)
        rows = list(
            self.db.scalars(
                select(Prospect)
                .where(Prospect.acquisition_status == AcquisitionStatus.READY_FOR_OUTREACH)
                .order_by(Prospect.priority_score.desc().nulls_last(), Prospect.created_at.asc())
            ).all()
        )
        included: list[QueueEntry] = []
        skipped: list[QueueEntry] = []
        for prospect in rows:
            verdict = self.evaluate_eligibility(prospect)
            base_priority = float(prospect.priority_score or 0.0)
            use_effective = bool(
                learning_svc and learning_svc.has_valid_ai_adjustment(prospect)
            )
            effective = (
                float(prospect.effective_priority_score)
                if use_effective and prospect.effective_priority_score is not None
                else base_priority
            )
            entry = QueueEntry(
                prospect_id=prospect.id,
                company=prospect.company,
                email=prospect.email,
                priority_score=effective,
                fit_score=prospect.fit_score,
                timing_score=prospect.timing_score,
                reachability_score=prospect.reachability_score,
                acquisition_status=(
                    prospect.acquisition_status.value if prospect.acquisition_status else ""
                ),
                inclusion_reasons=list(verdict.reasons)
                + (
                    [
                        f"base_priority={base_priority:.1f}",
                        f"effective_priority={effective:.1f}",
                        f"learning_adjustment={float(prospect.learning_adjustment or 0):+.1f}",
                    ]
                    if use_effective
                    else []
                ),
                exclusion_reasons=list(verdict.exclusion_reasons),
            )
            if verdict.eligible:
                included.append(entry)
            else:
                skipped.append(entry)

        # Rank by effective priority when valid AI learning exists; else base priority.
        included.sort(
            key=lambda e: (
                e.priority_score,
                float(e.fit_score or 0.0),
                float(e.timing_score or 0.0),
                float(e.reachability_score or 0.0),
            ),
            reverse=True,
        )
        selected = included[:cap]
        overflow = included[cap:]
        for entry in overflow:
            entry.exclusion_reasons.append(f"over_activation_limit:{cap}")
            skipped.append(entry)

        return {
            "limit": cap,
            "selected": [self._entry_dict(e) for e in selected],
            "skipped": [self._entry_dict(e) for e in skipped],
            "selected_count": len(selected),
            "skipped_count": len(skipped),
        }

    def _effective_limit(self, limit: int | None) -> int:
        icp = self.icp.as_dict()
        configured = int(icp.get("activation_batch_limit") or 5)
        hard = int(self.settings.activation_batch_hard_max or 25)
        requested = configured if limit is None else int(limit)
        return max(1, min(requested, hard))

    def _is_customer(self, prospect: Prospect) -> bool:
        email = normalize_outreach_email(prospect.email)
        stmt = (
            select(CampaignProspect)
            .options(selectinload(CampaignProspect.prospect))
            .where(CampaignProspect.purchase_outcome == PurchaseOutcome.PURCHASED)
        )
        for link in self.db.scalars(stmt).all():
            if link.prospect_id == prospect.id:
                return True
            other = link.prospect
            if other and email and normalize_outreach_email(other.email) == email:
                return True
        return False

    def _is_opted_out_or_bounced(self, prospect: Prospect) -> bool:
        email = normalize_outreach_email(prospect.email)
        terminal = {OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED}
        stmt = select(CampaignProspect).options(selectinload(CampaignProspect.prospect))
        for link in self.db.scalars(stmt).all():
            if link.outreach_status not in terminal:
                continue
            if link.prospect_id == prospect.id:
                return True
            other = link.prospect
            if other and email and normalize_outreach_email(other.email) == email:
                return True
        return False

    def _has_active_conversation(self, prospect: Prospect) -> bool:
        """True when a reply conversation exists on a non-terminal outreach hub."""
        links = list(
            self.db.scalars(
                select(CampaignProspect).where(CampaignProspect.prospect_id == prospect.id)
            ).all()
        )
        if not links:
            return False
        link_ids = [link.id for link in links]
        conv = self.db.scalar(
            select(ReplyConversation)
            .where(ReplyConversation.campaign_prospect_id.in_(link_ids))
            .limit(1)
        )
        if conv is None:
            return False
        # Conversation alone is enough when outreach not terminal — conflict may also catch this.
        for link in links:
            if link.id == conv.campaign_prospect_id and link.outreach_status not in {
                OutreachStatus.STOPPED,
                OutreachStatus.UNSUBSCRIBED,
                OutreachStatus.BOUNCED,
                OutreachStatus.COMPLETED,
                OutreachStatus.COLD,
            }:
                return True
        return False

    @staticmethod
    def _entry_dict(entry: QueueEntry) -> dict[str, Any]:
        return {
            "prospect_id": str(entry.prospect_id),
            "company": entry.company,
            "email": entry.email,
            "priority_score": entry.priority_score,
            "fit_score": entry.fit_score,
            "timing_score": entry.timing_score,
            "reachability_score": entry.reachability_score,
            "acquisition_status": entry.acquisition_status,
            "inclusion_reasons": entry.inclusion_reasons,
            "exclusion_reasons": entry.exclusion_reasons,
        }
