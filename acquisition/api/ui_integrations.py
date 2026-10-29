"""Integrations admin UI routes.

* Gmail and Cal.com are connected through Composio (in the chat app); this page only shows their status.
* OpenAI and Stripe keys live in the server .env; this page shows their status and can test them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.api.ui_sending import sending_context
from acquisition.auth.admin import require_admin_html_request
from acquisition.db.session import get_db
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.calcom_admin import calcom_admin_context
from acquisition.services.calcom_service import CalcomService
from acquisition.services.openai_service import OpenAIService
from acquisition.services.stripe_service import StripeService
from acquisition.templating import create_templates

templates = create_templates()

router = APIRouter(tags=["admin-ui-integrations"], dependencies=[Depends(require_admin_html_request)])


def get_openai_service(db: Session = Depends(get_db)) -> OpenAIService:
    return OpenAIService(db=db)


def get_calcom_service(db: Session = Depends(get_db)) -> CalcomService:
    return CalcomService(db=db)


def get_stripe_service(db: Session = Depends(get_db)) -> StripeService:
    return StripeService(db=db)


def get_journey(db: Session = Depends(get_db)) -> AcquisitionJourneyService:
    return AcquisitionJourneyService(db)


def _openai_context(service: OpenAIService) -> dict:
    summary = service.get_summary()
    return {"configured": summary.configured, "api_key_set": summary.api_key_configured, "model": summary.model}


def _stripe_context(service: StripeService) -> dict:
    summary = service.get_summary()
    return {
        "configured": summary.configured,
        "enabled": summary.enabled,
        "currency": summary.currency,
        "key_hint": summary.key_hint,
        "webhook_secret_set": summary.webhook_secret_configured,
    }


def _integrations_redirect(*, flash: str) -> RedirectResponse:
    return RedirectResponse(url=f"/integrations?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)


def _status_fragment(request: Request, *, configured: bool, result) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "integrations/_connection_status.html",
        {
            "configured": configured,
            "connected": result.connected,
            "message": result.message,
            "category": result.category,
            "checked": True,
            "http_status": result.http_status,
        },
    )


@router.get("/integrations", response_class=HTMLResponse)
def integrations_page(
    request: Request,
    openai: OpenAIService = Depends(get_openai_service),
    stripe: StripeService = Depends(get_stripe_service),
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    context = {
        "errors": [],
        "flash": request.query_params.get("flash"),
        "email": sending_context(db),
        "openai": _openai_context(openai),
        "stripe": _stripe_context(stripe),
        "settings": journey.get_settings(),
        **calcom_admin_context(journey.get_settings(), db=db, include_events=False),
    }
    return templates.TemplateResponse(request, "integrations/index.html", context)


# ---------------------------------------------------------------------------- tests (nothing is saved)
@router.post("/integrations/openai/test", response_class=HTMLResponse)
def openai_test(request: Request, service: OpenAIService = Depends(get_openai_service)) -> HTMLResponse:
    return _status_fragment(request, configured=service.get_summary().api_key_configured, result=service.check_connection())


@router.post("/integrations/calcom/test", response_class=HTMLResponse)
def calcom_test(request: Request, service: CalcomService = Depends(get_calcom_service)) -> HTMLResponse:
    return _status_fragment(request, configured=service.get_summary().configured, result=service.check_connection())


@router.post("/integrations/stripe/test", response_class=HTMLResponse)
def stripe_test(request: Request, service: StripeService = Depends(get_stripe_service)) -> HTMLResponse:
    return _status_fragment(request, configured=service.get_summary().configured, result=service.check_connection())


# ---------------------------------------------------------------------------- Cal.com webhook
@router.post("/integrations/calcom/webhook/register", response_model=None)
def register_calcom_webhook(db: Session = Depends(get_db)):
    from acquisition.services.calcom_webhook_service import CalcomWebhookService, CalcomWebhookServiceError

    try:
        CalcomWebhookService(db).register_webhook()
        return _integrations_redirect(flash="calcom_updated")
    except CalcomWebhookServiceError:
        return _integrations_redirect(flash="calcom_error")


@router.post("/integrations/calcom/webhook/reconcile", response_model=None)
def reconcile_calcom_bookings(db: Session = Depends(get_db)):
    from acquisition.services.calcom_webhook_service import CalcomWebhookService, CalcomWebhookServiceError

    try:
        CalcomWebhookService(db).reconcile_recent_bookings()
        return _integrations_redirect(flash="calcom_updated")
    except CalcomWebhookServiceError:
        return _integrations_redirect(flash="calcom_error")
