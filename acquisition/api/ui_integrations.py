"""Integrations admin UI routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from acquisition.config import is_non_production_environment
from acquisition.db.session import get_db
from acquisition.schemas.integration import InstantlyConfigCreate, InstantlyConfigUpdate
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.calcom_admin import calcom_admin_context
from acquisition.services.calcom_service import CalcomConfigError, CalcomService
from acquisition.services.dev_webhook_service import DevWebhookError, DevWebhookService
from acquisition.services.instantly_service import InstantlyConfigErrorApp, InstantlyService
from acquisition.services.openai_service import OpenAIConfigError, OpenAIService
from acquisition.services.stripe_service import StripeConfigError, StripeService
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request

templates = create_templates()

router = APIRouter(tags=["admin-ui-integrations"], dependencies=[Depends(require_admin_html_request)])


def get_instantly_service(db: Session = Depends(get_db)) -> InstantlyService:
    return InstantlyService(db=db)


def get_openai_service(db: Session = Depends(get_db)) -> OpenAIService:
    return OpenAIService(db=db)


def get_calcom_service(db: Session = Depends(get_db)) -> CalcomService:
    return CalcomService(db=db)


def get_stripe_service(db: Session = Depends(get_db)) -> StripeService:
    return StripeService(db=db)


def get_dev_webhook_service(db: Session = Depends(get_db)) -> DevWebhookService:
    return DevWebhookService(db=db)


def get_journey(db: Session = Depends(get_db)) -> AcquisitionJourneyService:
    return AcquisitionJourneyService(db)


def _openai_context(service: OpenAIService) -> dict:
    summary = service.get_summary()
    api_key, model = service.resolve_credentials()
    if not api_key:
        api_key = (service.settings.openai_api_key or "").strip() or None
    # Never put the live secret in HTML outside local/dev/test.
    reveal = is_non_production_environment(service.settings.environment)
    key_set = bool(summary.api_key_configured or api_key)
    return {
        "api_key_set": key_set,
        "configured": summary.configured or key_set,
        "model": model or summary.model,
        "source": summary.source,
        "api_key": (api_key or "") if reveal else "",
        "reveal_secrets": reveal,
    }


def _stripe_context(service: StripeService) -> dict:
    summary = service.get_summary()
    reveal = is_non_production_environment(service.settings.environment)
    secret = service.resolve_secret_key() if reveal else ""
    webhook = service.resolve_webhook_secret() if reveal else ""
    return {
        "configured": summary.configured,
        "enabled": summary.enabled,
        "currency": summary.currency,
        "source": summary.source,
        "key_hint": summary.key_hint,
        "secret_key_set": summary.secret_key_configured,
        "webhook_secret_set": summary.webhook_secret_configured,
        "secret_key": secret or "",
        "webhook_secret": webhook or "",
        "reveal_secrets": reveal,
    }


def _integrations_redirect(*, flash: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/integrations?flash={flash}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _save_instantly(
    service: InstantlyService,
    *,
    display_name: str,
    api_key: str,
) -> None:
    existing = service.get_summary()
    cleaned = (api_key or "").strip()
    if existing is None:
        payload = InstantlyConfigCreate.model_validate(
            {"display_name": display_name or "Cepoch Development", "api_key": cleaned}
        )
        service.create_connection(payload)
        return

    update_data: dict = {"display_name": display_name or existing.display_name}
    if cleaned:
        update_data["replace_api_key"] = True
        update_data["api_key"] = cleaned
    payload = InstantlyConfigUpdate.model_validate(update_data)
    service.update_connection(payload)


def _clear_calcom_journey_selection(journey: AcquisitionJourneyService) -> None:
    row = journey.get_settings()
    row.calcom_event_type_id = None
    row.calcom_event_slug = None
    row.calcom_event_title = None
    row.calcom_event_duration_minutes = None
    row.calcom_booking_url = None
    row.calcom_username = None
    journey.db.add(row)
    journey.db.commit()


def _page_context(
    instantly: InstantlyService,
    openai: OpenAIService,
    calcom: CalcomService,
    stripe: StripeService,
    journey: AcquisitionJourneyService,
    db: Session,
    *,
    errors: list[str] | None = None,
    flash: str | None = None,
) -> dict:
    summary = instantly.get_summary()
    settings = journey.get_settings()
    return {
        "config": summary,
        "instantly_api_key": instantly.resolve_api_key() or "",
        "errors": errors or [],
        "flash": flash,
        "checked": False,
        "connected": summary.connection_status.value == "connected" if summary else False,
        "message": None,
        "category": summary.connection_status.value if summary else "unknown",
        "http_status": 200,
        "openai": _openai_context(openai),
        "stripe": _stripe_context(stripe),
        "settings": settings,
        **calcom_admin_context(settings, db=db, include_events=False),
        "calcom_api_key": calcom.resolve_api_key() or "",
    }


@router.get("/integrations", response_class=HTMLResponse)
def integrations_page(
    request: Request,
    instantly: InstantlyService = Depends(get_instantly_service),
    openai: OpenAIService = Depends(get_openai_service),
    calcom: CalcomService = Depends(get_calcom_service),
    stripe: StripeService = Depends(get_stripe_service),
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "integrations/index.html",
        _page_context(
            instantly,
            openai,
            calcom,
            stripe,
            journey,
            db,
            flash=request.query_params.get("flash"),
        ),
    )


@router.post("/integrations/instantly", response_model=None)
def integrations_instantly_save(
    display_name: Annotated[str, Form()] = "Cepoch Development",
    api_key: Annotated[str, Form()] = "",
    service: InstantlyService = Depends(get_instantly_service),
):
    existing = service.get_summary()
    try:
        _save_instantly(service, display_name=display_name, api_key=api_key)
        return _integrations_redirect(flash="updated" if existing else "connected")
    except (ValidationError, InstantlyConfigErrorApp):
        return _integrations_redirect(flash="save_error")


@router.post("/integrations/openai", response_model=None)
def integrations_openai_save(
    api_key: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "gpt-4o-mini",
    service: OpenAIService = Depends(get_openai_service),
):
    try:
        summary = service.get_summary()
        service.save_api_key(api_key, model=model)
        flash = "openai_updated" if summary.configured else "openai_connected"
        return _integrations_redirect(flash=flash)
    except OpenAIConfigError:
        return _integrations_redirect(flash="openai_save_error")


def _instantly_status_response(request: Request, service: InstantlyService) -> HTMLResponse:
    result = service.check_connection()
    summary = service.get_summary()
    return templates.TemplateResponse(
        request,
        "integrations/_instantly_status.html",
        {
            "config": summary,
            "connected": result.connected,
            "message": result.message,
            "category": result.category,
            "checked": True,
            "http_status": result.http_status,
        },
    )


def _openai_status_response(request: Request, service: OpenAIService) -> HTMLResponse:
    result = service.check_connection()
    summary = service.get_summary()
    return templates.TemplateResponse(
        request,
        "integrations/_connection_status.html",
        {
            "configured": summary.api_key_configured or summary.configured,
            "connected": result.connected,
            "message": result.message,
            "category": result.category,
            "checked": True,
            "http_status": result.http_status,
        },
    )


def _calcom_status_response(request: Request, service: CalcomService) -> HTMLResponse:
    result = service.check_connection()
    summary = service.get_summary()
    return templates.TemplateResponse(
        request,
        "integrations/_connection_status.html",
        {
            "configured": summary.configured,
            "connected": result.connected,
            "message": result.message,
            "category": result.category,
            "checked": True,
            "http_status": result.http_status,
        },
    )


@router.get("/integrations/instantly/status", response_class=HTMLResponse)
def instantly_status_fragment(
    request: Request,
    service: InstantlyService = Depends(get_instantly_service),
) -> HTMLResponse:
    return _instantly_status_response(request, service)


@router.post("/integrations/instantly/test", response_class=HTMLResponse)
def instantly_save_and_test(
    request: Request,
    display_name: Annotated[str, Form()] = "Cepoch Development",
    api_key: Annotated[str, Form()] = "",
    service: InstantlyService = Depends(get_instantly_service),
) -> HTMLResponse:
    """Autosave form credentials, then return the connection status fragment."""
    try:
        existing = service.get_summary()
        cleaned = (api_key or "").strip()
        if existing is None and not cleaned:
            return templates.TemplateResponse(
                request,
                "integrations/_instantly_status.html",
                {
                    "config": None,
                    "connected": False,
                    "message": "Enter an API key to save and test.",
                    "category": "config_error",
                    "checked": True,
                    "http_status": 200,
                },
            )
        _save_instantly(service, display_name=display_name, api_key=api_key)
    except (ValidationError, InstantlyConfigErrorApp) as exc:
        message = getattr(exc, "message", None) or "Could not save Instantly settings."
        return templates.TemplateResponse(
            request,
            "integrations/_instantly_status.html",
            {
                "config": service.get_summary(),
                "connected": False,
                "message": str(message),
                "category": "config_error",
                "checked": True,
                "http_status": 200,
            },
        )
    return _instantly_status_response(request, service)


@router.post("/integrations/instantly/delete", response_model=None)
def integrations_instantly_delete(
    service: InstantlyService = Depends(get_instantly_service),
):
    service.delete_connection()
    return _integrations_redirect(flash="instantly_deleted")


@router.get("/integrations/openai/status", response_class=HTMLResponse)
def openai_status_fragment(
    request: Request,
    service: OpenAIService = Depends(get_openai_service),
) -> HTMLResponse:
    return _openai_status_response(request, service)


@router.post("/integrations/openai/test", response_class=HTMLResponse)
def openai_save_and_test(
    request: Request,
    api_key: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "gpt-4o-mini",
    service: OpenAIService = Depends(get_openai_service),
) -> HTMLResponse:
    """Autosave form credentials, then return the connection status fragment."""
    try:
        summary = service.get_summary()
        cleaned = (api_key or "").strip()
        if not cleaned and not summary.api_key_configured:
            return templates.TemplateResponse(
                request,
                "integrations/_connection_status.html",
                {
                    "configured": False,
                    "connected": False,
                    "message": "Enter an API key to save and test.",
                    "category": "config_error",
                    "checked": True,
                    "http_status": 200,
                },
            )
        service.save_api_key(api_key, model=model)
    except OpenAIConfigError as exc:
        return templates.TemplateResponse(
            request,
            "integrations/_connection_status.html",
            {
                "configured": False,
                "connected": False,
                "message": exc.message,
                "category": "config_error",
                "checked": True,
                "http_status": 200,
            },
        )
    return _openai_status_response(request, service)


@router.post("/integrations/openai/delete", response_model=None)
def integrations_openai_delete(
    service: OpenAIService = Depends(get_openai_service),
):
    service.delete_connection()
    return _integrations_redirect(flash="openai_deleted")


@router.get("/integrations/calcom/status", response_class=HTMLResponse)
def calcom_status_fragment(
    request: Request,
    service: CalcomService = Depends(get_calcom_service),
) -> HTMLResponse:
    return _calcom_status_response(request, service)


@router.post("/integrations/calcom/test", response_class=HTMLResponse)
def calcom_save_and_test(
    request: Request,
    api_key: Annotated[str, Form()] = "",
    service: CalcomService = Depends(get_calcom_service),
) -> HTMLResponse:
    """Autosave form credentials, then return the connection status fragment."""
    cleaned = (api_key or "").strip()
    try:
        summary = service.get_summary()
        if cleaned:
            service.save_api_key(cleaned)
        elif not summary.configured:
            return templates.TemplateResponse(
                request,
                "integrations/_connection_status.html",
                {
                    "configured": False,
                    "connected": False,
                    "message": "Enter an API key to save and test.",
                    "category": "config_error",
                    "checked": True,
                    "http_status": 200,
                },
            )
    except CalcomConfigError as exc:
        return templates.TemplateResponse(
            request,
            "integrations/_connection_status.html",
            {
                "configured": False,
                "connected": False,
                "message": exc.message,
                "category": "config_error",
                "checked": True,
                "http_status": 200,
            },
        )
    return _calcom_status_response(request, service)


@router.post("/integrations/calcom/delete", response_model=None)
def integrations_calcom_delete(
    service: CalcomService = Depends(get_calcom_service),
    journey: AcquisitionJourneyService = Depends(get_journey),
):
    service.delete_connection()
    _clear_calcom_journey_selection(journey)
    return _integrations_redirect(flash="calcom_deleted")


@router.post("/integrations/instantly/webhook/register", response_model=None)
def register_instantly_webhook(
    webhook_service: DevWebhookService = Depends(get_dev_webhook_service),
):
    try:
        webhook_service.register_webhook()
        return RedirectResponse(
            url="/integrations?flash=connected",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except DevWebhookError:
        return RedirectResponse(
            url="/integrations?flash=save_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/integrations/instantly/webhook/test", response_model=None)
def test_instantly_webhook_path(
    webhook_service: DevWebhookService = Depends(get_dev_webhook_service),
):
    try:
        result = webhook_service.test_webhook()
        flash = "connected" if result.get("health_ok") else "save_error"
        return RedirectResponse(
            url=f"/integrations?flash={flash}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    except DevWebhookError:
        return RedirectResponse(
            url="/integrations?flash=save_error",
            status_code=status.HTTP_303_SEE_OTHER,
        )


@router.post("/integrations/calcom", response_model=None)
def integrations_calcom_save(
    api_key: Annotated[str, Form()] = "",
    service: CalcomService = Depends(get_calcom_service),
):
    try:
        summary = service.get_summary()
        service.save_api_key(api_key)
        flash = "calcom_updated" if summary.configured else "calcom_connected"
        return _integrations_redirect(flash=flash)
    except CalcomConfigError:
        return _integrations_redirect(flash="calcom_save_error")


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


def _stripe_status_response(request: Request, service: StripeService) -> HTMLResponse:
    result = service.check_connection()
    summary = service.get_summary()
    return templates.TemplateResponse(
        request,
        "integrations/_connection_status.html",
        {
            "configured": summary.configured,
            "connected": result.connected,
            "message": result.message,
            "category": result.category,
            "checked": True,
            "http_status": result.http_status,
        },
    )


@router.post("/integrations/stripe", response_model=None)
def integrations_stripe_save(
    secret_key: Annotated[str, Form()] = "",
    webhook_secret: Annotated[str, Form()] = "",
    currency: Annotated[str, Form()] = "usd",
    enabled: Annotated[str | None, Form()] = None,
    service: StripeService = Depends(get_stripe_service),
):
    try:
        summary = service.get_summary()
        service.save_credentials(
            secret_key=secret_key,
            webhook_secret=webhook_secret,
            currency=currency,
            enabled=enabled == "true",
        )
        flash = "stripe_updated" if summary.configured else "stripe_connected"
        return _integrations_redirect(flash=flash)
    except StripeConfigError:
        return _integrations_redirect(flash="stripe_save_error")


@router.get("/integrations/stripe/status", response_class=HTMLResponse)
def stripe_status_fragment(
    request: Request,
    service: StripeService = Depends(get_stripe_service),
) -> HTMLResponse:
    return _stripe_status_response(request, service)


@router.post("/integrations/stripe/test", response_class=HTMLResponse)
def stripe_save_and_test(
    request: Request,
    secret_key: Annotated[str, Form()] = "",
    webhook_secret: Annotated[str, Form()] = "",
    currency: Annotated[str, Form()] = "usd",
    enabled: Annotated[str | None, Form()] = None,
    service: StripeService = Depends(get_stripe_service),
) -> HTMLResponse:
    cleaned = (secret_key or "").strip()
    try:
        summary = service.get_summary()
        if cleaned or summary.source == "database":
            service.save_credentials(
                secret_key=secret_key,
                webhook_secret=webhook_secret,
                currency=currency,
                enabled=(enabled == "true") if enabled is not None else summary.enabled,
            )
        elif not summary.configured:
            return templates.TemplateResponse(
                request,
                "integrations/_connection_status.html",
                {
                    "configured": False,
                    "connected": False,
                    "message": "Enter a Stripe secret key to save and test.",
                    "category": "config_error",
                    "checked": True,
                    "http_status": 200,
                },
            )
    except StripeConfigError as exc:
        return templates.TemplateResponse(
            request,
            "integrations/_connection_status.html",
            {
                "configured": False,
                "connected": False,
                "message": exc.message,
                "category": "config_error",
                "checked": True,
                "http_status": 200,
            },
        )
    return _stripe_status_response(request, service)


@router.post("/integrations/stripe/delete", response_model=None)
def integrations_stripe_delete(
    service: StripeService = Depends(get_stripe_service),
):
    service.delete_connection()
    return _integrations_redirect(flash="stripe_deleted")

