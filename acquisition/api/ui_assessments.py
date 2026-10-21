"""Admin UI: Assessments list and detail (internal qualification included)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from acquisition.db.session import get_db
from acquisition.models.assessment import AssessmentConversation, AssessmentMessage, AssessmentProfile
from acquisition.models.campaign import (
    CampaignProspect,
    ConsultationShowStatus,
    HandoffStatus,
    PurchaseOutcome,
)
from acquisition.models.journey import QualificationStatus, empty_qualification
from acquisition.models.replies import ReplyConversation
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.acquisition_kpi_service import build_acquisition_kpis
from acquisition.services.assessment_chat_service import ASSESSMENT_MODEL, AssessmentChatService
from acquisition.services.assessment_completion_service import build_qualification_transparency
from acquisition.services.assessment_extraction import (
    concrete_value,
    field_display,
    normalize_structured_state,
)
from acquisition.services.assessment_journey_reset import reset_assessment_journey
from acquisition.services.assessment_tokens import build_assessment_url
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED
from acquisition.services.email_delivery_status import resolve_email_delivery
from acquisition.services.jeanne_checkout_service import (
    JeanneCheckoutError,
    build_checkout_url,
    mark_handoff,
    offer_checkout,
)
from acquisition.services.webhook_ingestion_service import WebhookIngestionService
from acquisition.services.pre_call_brief_service import BRIEF_STATUS_READY, PreCallBriefService
from acquisition.services.stripe_test_service import (
    StripeTestError,
    create_test_checkout_session,
    stripe_test_ui_block_reason,
    stripe_test_ui_enabled,
)
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request
from acquisition.config import get_settings
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

templates = create_templates()
router = APIRouter(tags=["admin-ui-assessments"], dependencies=[Depends(require_admin_html_request)])

FIELD_LABELS = {
    "company": "Company",
    "goals": "Goals",
    "revenue_range": "Revenue range",
    "headcount": "Headcount",
    "functions": "Functions",
    "tools": "Tools",
    "workflows": "Workflows",
    "knowledge_locations": "Where knowledge lives",
    "pain_points": "Pain points",
    "time_cost_impact": "Time / cost impact",
    "bottlenecks": "Bottlenecks",
    "change_readiness": "Change readiness",
    "ai_readiness": "AI readiness",
    "authority": "Authority",
}


@router.get("/assessments", response_class=HTMLResponse)
def assessments_list(
    request: Request,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = (
        select(CampaignProspect)
        .where(CampaignProspect.assessment_token.is_not(None))
        .options(
            selectinload(CampaignProspect.prospect),
            selectinload(CampaignProspect.campaign),
        )
        .order_by(CampaignProspect.last_activity_at.desc().nullslast(), CampaignProspect.created_at.desc())
    )
    links = list(db.scalars(stmt).all())
    profiles = {
        p.campaign_prospect_id: p
        for p in db.scalars(select(AssessmentProfile)).all()
    }
    reply_by_link = {
        c.campaign_prospect_id: c
        for c in db.scalars(select(ReplyConversation)).all()
    }

    rows = []
    for link in links:
        profile = profiles.get(link.id)
        qual = profile.qualification_status.value if profile else QualificationStatus.PENDING.value
        qual_payload = (profile.qualification if profile else {}) or {}
        explanation = str(qual_payload.get("explanation") or "").strip()
        reply = reply_by_link.get(link.id)
        rows.append(
            {
                "link": link,
                "prospect": link.prospect,
                "campaign": link.campaign,
                "qualification_status": qual,
                "qualification_explanation": explanation,
                "public_url": build_assessment_url(link.assessment_token) if link.assessment_token else None,
                "has_reply": reply is not None,
                "reply_status": reply.status.value if reply else None,
                "reply_snippet": (reply.latest_incoming_snippet or "").strip() if reply else None,
            }
        )

    return templates.TemplateResponse(
        request,
        "assessments/list.html",
        {
            "rows": rows,
            "total": len(rows),
            "kpis": build_acquisition_kpis(db),
            "flash": request.query_params.get("flash"),
        },
    )


@router.get("/assessments/{link_id}", response_class=HTMLResponse)
def assessment_detail(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    link = db.scalar(
        select(CampaignProspect)
        .where(CampaignProspect.id == link_id)
        .options(
            selectinload(CampaignProspect.prospect),
            selectinload(CampaignProspect.campaign),
        )
    )
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    conversation = db.scalar(
        select(AssessmentConversation).where(AssessmentConversation.campaign_prospect_id == link.id)
    )
    messages: list[AssessmentMessage] = []
    if conversation:
        messages = list(
            db.scalars(
                select(AssessmentMessage)
                .where(AssessmentMessage.conversation_id == conversation.id)
                .order_by(AssessmentMessage.created_at.asc())
            ).all()
        )
    profile = db.scalar(
        select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id)
    )
    reply_conversation = db.scalar(
        select(ReplyConversation)
        .where(ReplyConversation.campaign_prospect_id == link.id)
        .options(selectinload(ReplyConversation.messages))
    )
    state = normalize_structured_state(profile.structured_state if profile else None)
    fields = []
    for key in state:
        display = field_display(state[key])
        fields.append(
            {
                "key": key,
                "label": FIELD_LABELS.get(key, key),
                "status": display["status"],
                "status_label": display["status_label"],
                "value": display["value"],
                "source": display.get("source"),
            }
        )
    qual_status = (
        profile.qualification_status.value if profile else QualificationStatus.PENDING.value
    )
    # Admin-facing: "qualified" = assessment-qualified to continue (not calendar-ready).
    qual_status_label = {
        QualificationStatus.QUALIFIED.value: "Assessment Qualified",
        QualificationStatus.UNQUALIFIED.value: "Unqualified",
        QualificationStatus.PENDING.value: "Pending / unclear",
    }.get(qual_status, qual_status)
    qualification = (profile.qualification if profile else {}) or {}
    qual_transparency = build_qualification_transparency(qualification, status=qual_status)
    acquisition = AcquisitionJourneyService(db).admin_progress(link, profile)
    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    brief_ready = bool(
        profile
        and (profile.pre_call_brief_status or "") == BRIEF_STATUS_READY
        and isinstance(profile.pre_call_brief_data, dict)
        and profile.pre_call_brief_data
    )
    fields_by_key = {item["key"]: item for item in fields}
    initial_tab = (request.query_params.get("tab") or "").strip().lower()
    if initial_tab not in {"overview", "assessment", "booking", "payment", "qualification", "replies"}:
        initial_tab = "overview"

    email_delivery = resolve_email_delivery(db, link)
    # If still queued, refresh from Instantly so Sent flips once mail actually left.
    if email_delivery["status"] == "queued":
        try:
            WebhookIngestionService(db).poll_recent_emails()
            db.refresh(link)
            email_delivery = resolve_email_delivery(db, link)
        except Exception:  # noqa: BLE001 — UI must still render if Instantly is down
            pass

    return templates.TemplateResponse(
        request,
        "assessments/detail.html",
        {
            "link": link,
            "prospect": link.prospect,
            "campaign": link.campaign,
            "messages": messages,
            "profile": profile,
            "fields": fields,
            "fields_by_key": fields_by_key,
            "public_url": build_assessment_url(link.assessment_token) if link.assessment_token else None,
            "qualification": qualification,
            "qualification_status": qual_status,
            "qualification_status_label": qual_status_label,
            "qualification_transparency": qual_transparency,
            "result": profile.result_payload if profile else None,
            "acquisition": acquisition,
            "booked": booked,
            "brief_ready": brief_ready,
            "brief_status": (profile.pre_call_brief_status if profile else None),
            "brief_error": (profile.pre_call_brief_error if profile else None),
            "assessment_model": ASSESSMENT_MODEL,
            "reply_conversation": reply_conversation,
            "initial_tab": initial_tab,
            "email_delivery": email_delivery,
            "flash": request.query_params.get("flash"),
            "stripe_test_enabled": stripe_test_ui_enabled(get_settings()),
            "stripe_test_block_reason": stripe_test_ui_block_reason(get_settings()),
            "stripe_test_amount_label": (
                f"${(int(get_settings().stripe_test_amount_cents or 0) / 100):.2f}"
            ),
            "checkout_url": (
                build_checkout_url(link.assessment_token)
                if link.assessment_token
                else None
            ),
        },
    )


def _load_assessment_link(db: Session, link_id: uuid.UUID) -> CampaignProspect | None:
    return db.scalar(
        select(CampaignProspect)
        .where(CampaignProspect.id == link_id)
        .options(
            selectinload(CampaignProspect.prospect),
            selectinload(CampaignProspect.campaign),
        )
    )


def _brief_header_facts(link: CampaignProspect, profile: AssessmentProfile | None) -> dict:
    prospect = link.prospect
    state = normalize_structured_state(profile.structured_state if profile else None)
    revenue = concrete_value(state.get("revenue_range")) or (
        (prospect.revenue_band or "").strip() or None if prospect else None
    )
    headcount = concrete_value(state.get("headcount")) or (
        (prospect.headcount_band or "").strip() or None if prospect else None
    )
    title = concrete_value(state.get("authority")) or (
        (prospect.executive_title or "").strip() or None if prospect else None
    )
    if isinstance(title, (dict, list)):
        title = str(title)
    return {
        "name": prospect.executive_name if prospect else None,
        "company": prospect.company if prospect else None,
        "title": title,
        "revenue": revenue,
        "headcount": headcount,
        "meeting_start": link.meeting_start,
        "meeting_timezone": link.meeting_timezone,
        "meeting_location": link.meeting_location,
    }


@router.get("/assessments/{link_id}/pre-call-brief", response_class=HTMLResponse)
def assessment_pre_call_brief(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if not booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=brief_not_booked",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    service = PreCallBriefService(db)
    result = service.ensure_for_admin(link)
    profile = service.get_profile(link)
    if profile is None or not service.has_ready_brief(profile):
        return templates.TemplateResponse(
            request,
            "assessments/pre_call_brief.html",
            {
                "link": link,
                "prospect": link.prospect,
                "header": _brief_header_facts(link, profile),
                "brief": None,
                "brief_status": (profile.pre_call_brief_status if profile else None),
                "brief_error": (profile.pre_call_brief_error if profile else result.get("error")),
                "generated_at": (profile.pre_call_brief_generated_at if profile else None),
                "flash": request.query_params.get("flash") or "brief_failed",
            },
            status_code=200,
        )

    return templates.TemplateResponse(
        request,
        "assessments/pre_call_brief.html",
        {
            "link": link,
            "prospect": link.prospect,
            "header": _brief_header_facts(link, profile),
            "brief": profile.pre_call_brief_data,
            "brief_status": profile.pre_call_brief_status,
            "brief_error": None,
            "generated_at": profile.pre_call_brief_generated_at,
            "flash": request.query_params.get("flash"),
        },
    )


@router.post("/assessments/{link_id}/pre-call-brief/generate")
def assessment_pre_call_brief_generate(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if not booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=brief_not_booked",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    result = PreCallBriefService(db).generate_for_link(link, force=False)
    flash = "brief_ready" if result.get("status") == "ready" else "brief_failed"
    return RedirectResponse(
        url=f"/assessments/{link_id}/pre-call-brief?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/pre-call-brief/regenerate")
def assessment_pre_call_brief_regenerate(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if not booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=brief_not_booked",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    result = PreCallBriefService(db).regenerate(link)
    flash = "brief_regenerated" if result.get("status") == "ready" else "brief_failed"
    return RedirectResponse(
        url=f"/assessments/{link_id}/pre-call-brief?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/reset-journey")
async def assessment_reset_journey(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Admin time-machine: rewind assessment + booking for one prospect."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    reset_assessment_journey(db, link)
    return RedirectResponse(
        url=f"/assessments/{link_id}?flash=journey_reset#overview",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/qualification")
async def assessment_set_qualification(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Admin override of assessment qualification after the AI decision."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_locked#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    form = await request.form()
    raw = str(form.get("qualification_status") or "").strip().lower()
    allowed = {
        QualificationStatus.QUALIFIED.value,
        QualificationStatus.UNQUALIFIED.value,
        QualificationStatus.PENDING.value,
    }
    if raw not in allowed:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_invalid#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    profile = db.scalar(
        select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id)
    )
    if profile is None:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_no_profile#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    new_status = QualificationStatus(raw)
    previous = (
        profile.qualification_status.value
        if profile.qualification_status is not None
        else QualificationStatus.PENDING.value
    )
    qual = dict(profile.qualification or empty_qualification())
    qual["status"] = new_status.value
    qual["assessment_status"] = new_status.value
    note = f"Admin override: set to {new_status.value} (was {previous})."
    existing_notes = str(qual.get("notes") or "").strip()
    qual["notes"] = f"{existing_notes}\n{note}".strip() if existing_notes else note
    profile.qualification = qual
    profile.qualification_status = new_status
    flag_modified(profile, "qualification")
    db.add(profile)
    db.commit()
    db.refresh(profile)
    db.refresh(link)

    AcquisitionJourneyService(db).apply_post_assessment_branch(link, profile)

    return RedirectResponse(
        url=f"/assessments/{link_id}?flash=qual_updated#qualification",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/qualification/rerun")
def assessment_rerun_qualification(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Re-run AI qualification with the current prompt and assessment model."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)

    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_locked#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    profile = db.scalar(
        select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id)
    )
    conversation = db.scalar(
        select(AssessmentConversation)
        .where(AssessmentConversation.campaign_prospect_id == link.id)
        .options(selectinload(AssessmentConversation.messages))
    )
    if profile is None or conversation is None:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_no_profile#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        chat = AssessmentChatService(db)
        chat.completion.requalify(link, conversation, profile, preserve_journey=False)
        AcquisitionJourneyService(db).apply_post_assessment_branch(link, profile)
    except Exception:  # noqa: BLE001
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=qual_rerun_error#qualification",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        url=f"/assessments/{link_id}?flash=qual_rerun#qualification",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _parse_money(raw: str | None) -> Decimal | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_money") from None
    if value < 0:
        raise ValueError("invalid_money")
    return value.quantize(Decimal("0.01"))


@router.post("/assessments/{link_id}/consultation-show")
async def assessment_set_consultation_show(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if not booked:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=show_requires_booking#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    form = await request.form()
    raw = str(form.get("consultation_show_status") or "").strip().lower()
    allowed = {s.value for s in ConsultationShowStatus}
    if raw not in allowed:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=show_invalid#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    status_value = ConsultationShowStatus(raw)
    link.consultation_show_status = status_value
    link.consultation_show_recorded_at = (
        datetime.now(timezone.utc)
        if status_value != ConsultationShowStatus.NOT_RECORDED
        else None
    )
    db.add(link)
    db.commit()
    return RedirectResponse(
        url=f"/assessments/{link_id}?flash=show_saved#booking",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/purchase-outcome")
async def assessment_set_purchase_outcome(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Manual purchase edits disabled — Stripe checkout is the source of truth."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/assessments/{link_id}?tab=payment&flash=purchase_manual_disabled#payment",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/checkout-offer")
async def assessment_checkout_offer(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Jeanne offers post-consult checkout (prospect pays on /a/{token}/checkout)."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    form = await request.form()
    try:
        offer_checkout(
            db,
            link,
            amount=form.get("checkout_offer_amount"),
            recurring_amount=form.get("checkout_offer_recurring_amount"),
            currency=str(form.get("checkout_offer_currency") or "USD"),
        )
        flash = "checkout_offered"
    except JeanneCheckoutError as exc:
        if exc.code in {"invalid_amount", "amount_required"}:
            flash = "checkout_offer_invalid"
        elif exc.code == "already_purchased":
            flash = "checkout_offer_already_purchased"
        elif exc.code == "booking_required":
            flash = "checkout_offer_booking_required"
        else:
            flash = "checkout_offer_error"
    return RedirectResponse(
        url=f"/assessments/{link_id}?tab=payment&flash={flash}#payment",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/handoff-status")
async def assessment_handoff_status(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    form = await request.form()
    raw = str(form.get("handoff_status") or "").strip().lower()
    try:
        status_enum = HandoffStatus(raw)
    except ValueError:
        return RedirectResponse(
            url=f"/assessments/{link_id}?tab=payment&flash=handoff_invalid#payment",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        mark_handoff(db, link, status=status_enum)
    except JeanneCheckoutError:
        return RedirectResponse(
            url=f"/assessments/{link_id}?tab=payment&flash=handoff_invalid#payment",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/assessments/{link_id}?tab=payment&flash=handoff_saved#payment",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/assessments/{link_id}/stripe-test/checkout")
async def assessment_stripe_test_checkout(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Legacy admin self-pay stub — prefer Offer checkout → prospect link."""
    link = _load_assessment_link(db, link_id)
    if link is None:
        return RedirectResponse(url="/assessments?flash=not_found", status_code=status.HTTP_303_SEE_OTHER)
    if not stripe_test_ui_enabled(get_settings()):
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=stripe_test_disabled#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    public_base = str(request.base_url).rstrip("/")
    try:
        created = create_test_checkout_session(
            link,
            settings=get_settings(),
            public_base=public_base,
        )
    except StripeTestError as exc:
        flash = "stripe_test_booking_required" if exc.code == "booking_required" else "stripe_test_error"
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash={flash}#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url=created["url"], status_code=status.HTTP_303_SEE_OTHER)


# Alias matching the internal Admin URL shape from the product requirement.
@router.get("/admin/assessments/{link_id}/pre-call-brief", response_class=HTMLResponse)
def admin_assessment_pre_call_brief(
    request: Request,
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return assessment_pre_call_brief(request, link_id, db)
