"""Public acquisition journey pages (proof → demo → value → calendar)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.services.acquisition_journey_service import (
    AcquisitionJourneyError,
    AcquisitionJourneyService,
)
from acquisition.services.assessment_chat_service import AssessmentChatService
from acquisition.services.assessment_service import AssessmentEventError
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["acquisition-journey"])

BRAND = "JeanneCAIO / FreedomBot"


def _journey_page_ctx(
    *,
    token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "token": token,
        "payload": payload,
        "brand": BRAND,
    }


def _truthy(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_journey(db: Session = Depends(get_db)) -> AcquisitionJourneyService:
    return AcquisitionJourneyService(db)


def get_assessment_chat(db: Session = Depends(get_db)) -> AssessmentChatService:
    return AssessmentChatService(db)


class BudgetReadinessBody(BaseModel):
    budget_readiness: str = Field(min_length=1, max_length=32)


def _gate_error_page(request: Request, exc: AcquisitionJourneyError | AssessmentEventError) -> HTMLResponse:
    message = getattr(exc, "message", str(exc))
    status = getattr(exc, "http_status", 403)
    return templates.TemplateResponse(
        request,
        "assessment/invalid.html",
        {"message": message},
        status_code=status if status in {403, 404, 422, 503} else 403,
    )


def _http_error(exc: AcquisitionJourneyError | AssessmentEventError):
    from fastapi import HTTPException

    return HTTPException(
        status_code=getattr(exc, "http_status", 403),
        detail=getattr(exc, "message", str(exc)),
    )


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


@router.get("/a/{token}/proof", response_class=HTMLResponse)
def proof_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> HTMLResponse:
    try:
        payload = journey.enter_proof(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)
    return templates.TemplateResponse(
        request,
        "assessment/proof.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.post("/a/{token}/proof/complete")
def proof_complete(
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> RedirectResponse:
    try:
        journey.complete_proof(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc
    return RedirectResponse(url=f"/a/{token}/demo", status_code=303)


@router.get("/a/{token}/demo", response_class=HTMLResponse)
def demo_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> HTMLResponse:
    try:
        payload = journey.enter_demo(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)
    return templates.TemplateResponse(
        request,
        "assessment/demo.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.post("/a/{token}/demo/complete")
def demo_complete(
    token: str,
    watched: Annotated[str, Form()] = "",
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> RedirectResponse:
    try:
        journey.complete_demo(token, watched=_truthy(watched))
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc
    return RedirectResponse(url=f"/a/{token}/value", status_code=303)


@router.post("/api/assessment/{token}/demo/complete")
def demo_complete_api(
    token: str,
    watched: Annotated[str, Query()] = "",
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> dict[str, Any]:
    try:
        return journey.complete_demo(token, watched=_truthy(watched))
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc


@router.get("/a/{token}/value", response_class=HTMLResponse)
def value_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> HTMLResponse:
    try:
        payload = journey.enter_value(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)
    return templates.TemplateResponse(
        request,
        "assessment/value.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.post("/a/{token}/value/complete")
def value_complete(
    token: str,
    budget_readiness: str = Form(...),
    journey: AcquisitionJourneyService = Depends(get_journey),
):
    try:
        result = journey.complete_value(token, budget_readiness=budget_readiness)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc
    if result.get("calendar_eligible"):
        return RedirectResponse(url=f"/a/{token}/calendar", status_code=303)
    outcome = str(result.get("budget_readiness") or "not_ready").strip().lower()
    if outcome not in {"unclear", "not_ready"}:
        outcome = "not_ready"
    return RedirectResponse(
        url=f"/a/{token}/value?outcome={outcome}",
        status_code=303,
    )


@router.get("/a/{token}/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> HTMLResponse:
    try:
        payload = journey.enter_calendar(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)
    if payload.get("booking_confirmed"):
        return RedirectResponse(url=f"/a/{token}/booked", status_code=303)
    return templates.TemplateResponse(
        request,
        "assessment/calendar.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.get("/a/{token}/booked", response_class=HTMLResponse)
def booked_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> HTMLResponse:
    try:
        payload = journey.enter_booked(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        # Not confirmed yet — show confirming/poll state when calendar-eligible,
        # otherwise gated error.
        try:
            link, profile = journey.resolve(token)
            if journey.can_access_calendar(link, profile):
                return templates.TemplateResponse(
                    request,
                    "assessment/booked.html",
                    _journey_page_ctx(
                        token=token,
                        payload={
                            "confirming": True,
                            "token": token,
                            "booking_status": getattr(link, "booking_status", "not_booked"),
                        },
                    ),
                )
        except Exception:  # noqa: BLE001
            pass
        return _gate_error_page(request, exc)
    return templates.TemplateResponse(
        request,
        "assessment/booked.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.get("/a/{token}/checkout", response_class=HTMLResponse)
def checkout_page(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
    cancelled: str | None = None,
) -> HTMLResponse:
    """JeanneCAIO post-consult checkout — prospect pays after Jeanne offers."""
    from acquisition.services.jeanne_checkout_service import public_checkout_payload

    try:
        link, _profile = journey.resolve(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)
    if not journey.can_access_booked(link, _profile):
        return _gate_error_page(
            request,
            AcquisitionJourneyError("Checkout is available after your consultation booking.", http_status=403),
        )
    payload = public_checkout_payload(link)
    payload["cancelled"] = str(cancelled or "").strip() in {"1", "true", "yes"}
    return templates.TemplateResponse(
        request,
        "assessment/checkout.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.post("/a/{token}/checkout/pay")
def checkout_pay(
    request: Request,
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.services.jeanne_checkout_service import (
        JeanneCheckoutError,
        create_prospect_checkout_session,
    )

    try:
        link, profile = journey.resolve(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc
    if not journey.can_access_booked(link, profile):
        raise _http_error(
            AcquisitionJourneyError("Checkout is available after your consultation booking.", http_status=403)
        )
    public_base = str(request.base_url).rstrip("/")
    try:
        created = create_prospect_checkout_session(link, public_base=public_base)
    except JeanneCheckoutError as exc:
        return RedirectResponse(
            url=f"/a/{token}/checkout?error={exc.code}",
            status_code=303,
        )
    return RedirectResponse(url=created["url"], status_code=303)


@router.get("/a/{token}/checkout/success", response_class=HTMLResponse)
def checkout_success(
    request: Request,
    token: str,
    session_id: str | None = None,
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    from acquisition.services.jeanne_checkout_service import (
        JeanneCheckoutError,
        complete_paid_checkout,
        public_checkout_payload,
    )
    from acquisition.services.stripe_test_service import StripeTestError

    try:
        link, profile = journey.resolve(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        return _gate_error_page(request, exc)

    error_message = None
    result = None
    if session_id and str(session_id).strip():
        try:
            result = complete_paid_checkout(db, str(session_id).strip())
            db.refresh(link)
        except (StripeTestError, JeanneCheckoutError) as exc:
            error_message = getattr(exc, "message", str(exc))

    payload = public_checkout_payload(link)
    payload["payment_result"] = result
    payload["error_message"] = error_message
    payload["success"] = bool(
        payload.get("already_paid") and not error_message
    )
    return templates.TemplateResponse(
        request,
        "assessment/checkout_success.html",
        _journey_page_ctx(token=token, payload=payload),
    )


@router.get("/api/assessment/{token}/booking")
def booking_status_api(
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> dict[str, Any]:
    try:
        return journey.booking_status_payload(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc


# ---------------------------------------------------------------------------
# Safe JSON (never leaks calendar URL unless eligible + calendar route)
# ---------------------------------------------------------------------------


@router.get("/api/assessment/{token}/journey")
def journey_status(
    token: str,
    journey: AcquisitionJourneyService = Depends(get_journey),
) -> dict[str, Any]:
    try:
        link, profile = journey.resolve(token)
    except (AcquisitionJourneyError, AssessmentEventError) as exc:
        raise _http_error(exc) from exc
    progress = journey.admin_progress(link, profile)
    # Strip any chance of calendar URL; public journey status is gate flags only.
    return {
        "token": token,
        "journey_stage": progress["journey_stage"],
        "qualification_assessment_status": progress["assessment_status"],
        "proof_viewed": bool(progress["proof_viewed_at"]),
        "demo_completed": bool(progress["demo_completed_at"]),
        "value_completed": bool(progress["value_context_completed_at"]),
        "budget_readiness": progress["budget_readiness"],
        "calendar_eligible": progress["calendar_eligible"],
        "booking_status": progress.get("booking_status") or "not_booked",
        "booking_confirmed": progress.get("booking_status") == "confirmed",
        "can_access_proof": journey.can_access_proof(link, profile),
        "can_access_demo": journey.can_access_demo(link, profile),
        "can_access_value": journey.can_access_value(link, profile),
        "can_access_calendar": journey.can_access_calendar(link, profile),
        "can_access_booked": journey.can_access_booked(link, profile),
    }
