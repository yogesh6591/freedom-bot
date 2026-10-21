"""Acquisition funnel KPI aggregates for Admin reporting."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.models.assessment import AssessmentProfile
from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignProspect,
    ConsultationShowStatus,
    PurchaseOutcome,
)
from acquisition.models.journey import QualificationStatus
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def build_acquisition_kpis(db: Session) -> dict[str, Any]:
    """Primary buyer-funnel KPIs from CampaignProspect + AssessmentProfile."""
    links = list(
        db.scalars(
            select(CampaignProspect)
            .options(
                selectinload(CampaignProspect.prospect),
                selectinload(CampaignProspect.campaign),
            )
            .order_by(CampaignProspect.created_at.desc())
        ).all()
    )
    profiles = {
        p.campaign_prospect_id: p
        for p in db.scalars(select(AssessmentProfile)).all()
    }

    totals = {
        "prospects_reached": 0,
        "assessment_started": 0,
        "assessment_completed": 0,
        "qualified": 0,
        "unqualified": 0,
        "demo_started": 0,
        "demo_completed": 0,
        "calendar_unlocked": 0,
        "booked": 0,
        "showed": 0,
        "no_show": 0,
        "purchased": 0,
        "did_not_purchase": 0,
        "revenue": Decimal("0"),
        "recurring_revenue": Decimal("0"),
    }

    by_source: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "completed_assessments": 0,
            "qualified": 0,
            "booked": 0,
            "purchased": 0,
            "revenue": Decimal("0"),
        }
    )
    by_campaign: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": "",
            "completed_assessments": 0,
            "qualified": 0,
            "booked": 0,
            "purchased": 0,
            "revenue": Decimal("0"),
        }
    )

    seen_prospects: set[object] = set()
    for link in links:
        prospect = link.prospect
        if prospect is not None and prospect.id not in seen_prospects:
            # Count people who were synced or otherwise entered outreach.
            if link.instantly_lead_id or link.assessment_token or link.outreach_status.value != "pending":
                seen_prospects.add(prospect.id)
                totals["prospects_reached"] += 1

        source = ((prospect.source if prospect else None) or "unknown").strip() or "unknown"
        campaign_key = str(link.campaign_id)
        campaign_name = (link.campaign.name if link.campaign else None) or campaign_key
        by_campaign[campaign_key]["name"] = campaign_name

        if link.assessment_started_at or link.assessment_status != AssessmentStatus.NOT_STARTED:
            totals["assessment_started"] += 1
        if link.assessment_completed_at or link.assessment_status == AssessmentStatus.COMPLETED:
            totals["assessment_completed"] += 1
            by_source[source]["completed_assessments"] += 1
            by_campaign[campaign_key]["completed_assessments"] += 1

        profile = profiles.get(link.id)
        qual = (
            profile.qualification_status.value
            if profile and profile.qualification_status is not None
            else QualificationStatus.PENDING.value
        )
        if qual == QualificationStatus.QUALIFIED.value:
            totals["qualified"] += 1
            by_source[source]["qualified"] += 1
            by_campaign[campaign_key]["qualified"] += 1
        elif qual == QualificationStatus.UNQUALIFIED.value:
            totals["unqualified"] += 1

        if link.demo_started_at:
            totals["demo_started"] += 1
        if link.demo_completed_at:
            totals["demo_completed"] += 1
        if link.calendar_unlocked_at:
            totals["calendar_unlocked"] += 1

        booked = (
            str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
            and link.booked_at is not None
        )
        if booked:
            totals["booked"] += 1
            by_source[source]["booked"] += 1
            by_campaign[campaign_key]["booked"] += 1

        if link.consultation_show_status == ConsultationShowStatus.SHOWED:
            totals["showed"] += 1
        elif link.consultation_show_status == ConsultationShowStatus.NO_SHOW:
            totals["no_show"] += 1

        if link.purchase_outcome == PurchaseOutcome.PURCHASED:
            totals["purchased"] += 1
            by_source[source]["purchased"] += 1
            by_campaign[campaign_key]["purchased"] += 1
            rev = _money(link.revenue_amount)
            totals["revenue"] += rev
            by_source[source]["revenue"] += rev
            by_campaign[campaign_key]["revenue"] += rev
            totals["recurring_revenue"] += _money(link.recurring_revenue_amount)
        elif link.purchase_outcome == PurchaseOutcome.DID_NOT_PURCHASE:
            totals["did_not_purchase"] += 1

    def _serialize_bucket(data: dict[str, Any]) -> dict[str, Any]:
        out = dict(data)
        out["revenue"] = float(out.get("revenue") or 0)
        return out

    return {
        "totals": {
            **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in totals.items()},
        },
        "by_source": [
            {"source": key, **_serialize_bucket(val)}
            for key, val in sorted(by_source.items(), key=lambda item: item[0].lower())
        ],
        "by_campaign": [
            {"campaign_id": key, **_serialize_bucket(val)}
            for key, val in sorted(
                by_campaign.items(), key=lambda item: (item[1].get("name") or "").lower()
            )
        ],
    }
