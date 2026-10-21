"""Admin rewind: clear assessment + journey progress for one campaign prospect."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import object_session

from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import AssessmentStatus, JourneyStage, OutreachStatus
from acquisition.models.campaign import ConsultationShowStatus, PurchaseOutcome
from acquisition.models.journey import QualificationStatus, empty_qualification, empty_structured_state
from acquisition.models.replies import ReplyConversation
from acquisition.services.assessment_extraction import (
    merge_structured_state,
    prospect_seed_value,
)


def seed_structured_from_prospect(prospect: Any | None) -> dict[str, Any]:
    state = empty_structured_state()
    seed: dict[str, Any] = {}
    if prospect is None:
        return state
    if getattr(prospect, "company", None):
        seed["company"] = prospect_seed_value(prospect.company)
    if getattr(prospect, "revenue_band", None):
        seed["revenue_range"] = prospect_seed_value(prospect.revenue_band)
    if getattr(prospect, "headcount_band", None):
        seed["headcount"] = prospect_seed_value(prospect.headcount_band)
    if seed:
        return merge_structured_state(state, seed)
    return state


def _cancel_cal_bookings_for_link(link: Any) -> list[str]:
    """Best-effort cancel of Cal.com bookings tied to this assessment."""
    cancelled: list[str] = []
    try:
        from acquisition.integrations.calcom.client import CalcomClient, CalcomError
    except Exception:  # noqa: BLE001
        return cancelled

    token = (getattr(link, "assessment_token", None) or "").strip()
    uid = (getattr(link, "calcom_booking_uid", None) or "").strip()
    prospect = getattr(link, "prospect", None)
    email = (getattr(prospect, "email", None) or "").strip().lower()

    try:
        session = object_session(link)
        cal = CalcomClient(db=session) if session is not None else CalcomClient()
    except Exception:  # noqa: BLE001
        return cancelled
    if not cal.configured:
        return cancelled

    targets: set[str] = set()
    if uid:
        targets.add(uid)

    try:
        bookings: list[dict] = []
        for status in (None, "upcoming", "accepted", "pending"):
            try:
                bookings.extend(cal.list_bookings(take=100, status=status))
            except Exception:  # noqa: BLE001
                continue
        seen: set[str] = set()
        for b in bookings:
            buid = str(b.get("uid") or "").strip()
            if not buid or buid in seen:
                continue
            seen.add(buid)
            st = str(b.get("status") or "").lower()
            if st in {"cancelled", "canceled", "rejected"}:
                continue
            meta = b.get("metadata") if isinstance(b.get("metadata"), dict) else {}
            fb = str(meta.get("fb_token") or meta.get("assessment_token") or "").strip()
            atts = b.get("attendees") if isinstance(b.get("attendees"), list) else []
            emails = {
                str(a.get("email") or "").strip().lower()
                for a in atts
                if isinstance(a, dict)
            }
            if (token and fb == token) or (email and email in emails) or (uid and buid == uid):
                targets.add(buid)
    except Exception:  # noqa: BLE001
        pass

    for buid in targets:
        try:
            cal.cancel_booking(buid, reason="FreedomBot journey reset")
            cancelled.append(buid)
        except CalcomError:
            # Still clear local state even if remote cancel fails.
            pass
        except Exception:  # noqa: BLE001
            pass
    return cancelled


def reset_assessment_journey(db: Any, link: Any) -> Any:
    """Rewind assessment + journey for one prospect link.

    Keeps prospect identity, campaign attachment, Instantly lead ids,
    assessment token, and Instantly/Cal.com provider-event idempotency rows
    (so email poll/webhooks do not re-fire auto-replies for old mail). Clears
    transcript, snapshot, qualification, post-assessment progress, booking
    fields, and reply threads. Cancels matching Cal.com bookings when still
    cancellable.
    """
    cancelled = _cancel_cal_bookings_for_link(link)
    if cancelled:
        import logging

        logging.getLogger(__name__).info(
            "Journey reset cancelled Cal.com booking(s): %s", ", ".join(cancelled)
        )

    link_id = link.id

    conv = db.scalar(
        select(AssessmentConversation).where(
            AssessmentConversation.campaign_prospect_id == link_id
        )
    )
    if conv is not None:
        db.delete(conv)
        db.flush()

    profile = db.scalar(
        select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link_id)
    )
    if profile is not None:
        db.delete(profile)
        db.flush()

    for reply in db.scalars(
        select(ReplyConversation).where(ReplyConversation.campaign_prospect_id == link_id)
    ).all():
        db.delete(reply)
    db.flush()

    link.assessment_status = AssessmentStatus.NOT_STARTED
    link.assessment_started_at = None
    link.assessment_completed_at = None
    link.proof_viewed_at = None
    link.demo_started_at = None
    link.demo_completed_at = None
    link.demo_watch_completed_at = None
    link.value_context_completed_at = None
    link.calendar_unlocked_at = None
    link.booked_at = None
    link.booking_status = "not_booked"
    link.calcom_booking_uid = None
    link.calcom_booking_id = None
    link.meeting_start = None
    link.meeting_end = None
    link.meeting_timezone = None
    link.meeting_location = None
    link.meeting_join_url = None
    link.consultation_show_status = ConsultationShowStatus.NOT_RECORDED
    link.consultation_show_recorded_at = None
    link.purchase_outcome = PurchaseOutcome.NOT_RECORDED
    link.purchased_at = None
    link.revenue_amount = None
    link.recurring_revenue_amount = None
    link.revenue_currency = "USD"
    link.checkout_offered_at = None
    link.checkout_offer_amount = None
    link.checkout_offer_recurring_amount = None
    link.checkout_offer_currency = "USD"
    link.handoff_status = "not_started"
    link.handoff_at = None
    link.stripe_checkout_session_id = None
    link.last_activity_at = None
    link.journey_stage = JourneyStage.OUTREACH

    outreach = getattr(link, "outreach_status", None)
    if outreach in {
        OutreachStatus.ASSESSMENT_STARTED,
        OutreachStatus.COMPLETED,
    }:
        link.outreach_status = (
            OutreachStatus.ACTIVE
            if getattr(link, "instantly_lead_id", None)
            else OutreachStatus.PENDING
        )

    conversation = AssessmentConversation(campaign_prospect_id=link.id)
    db.add(conversation)
    db.flush()
    prospect = getattr(link, "prospect", None)
    fresh = AssessmentProfile(
        campaign_prospect_id=link.id,
        conversation_id=conversation.id,
        structured_state=seed_structured_from_prospect(prospect),
        result_payload=None,
        qualification=empty_qualification(),
        qualification_status=QualificationStatus.PENDING,
        personalized_value_text=None,
        personalized_value_meta=None,
        pre_call_brief_data=None,
        pre_call_brief_generated_at=None,
        pre_call_brief_status=None,
        pre_call_brief_error=None,
        pre_call_brief_model=None,
    )
    db.add(fresh)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
