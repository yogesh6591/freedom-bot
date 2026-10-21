"""FreedomBot FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from acquisition.api import acquisition as acquisition_api
from acquisition.api import assessment as assessment_api
from acquisition.api import calcom_webhooks as calcom_webhooks_api
from acquisition.api import campaigns as campaigns_api
from acquisition.api import dev_layout as dev_layout_api
from acquisition.api import integrations as integrations_api
from acquisition.api import outbound as outbound_api
from acquisition.api import prospects as prospects_api
from acquisition.api import stripe_test as stripe_test_api
from acquisition.api import ui as ui_routes
from acquisition.api import ui_acquisition_database as ui_acquisition_database_routes
from acquisition.api import ui_acquisition_live as ui_acquisition_live_routes  # noqa: E402
from acquisition.api import ui_assessments as ui_assessment_routes
from acquisition.api import ui_auth as ui_auth_routes
from acquisition.api import ui_campaigns as ui_campaign_routes
from acquisition.api import ui_integrations as ui_integrations_routes
from acquisition.api import ui_journey as ui_journey_routes
from acquisition.api import ui_outbound as ui_outbound_routes
from acquisition.auth.admin import AdminAuthError, assert_admin_auth_config, install_admin_exception_handlers
from acquisition.config import api_docs_url_kwargs, get_settings, is_non_production_environment
from acquisition.db.session import check_database_connection
from acquisition.prefix_middleware import AcquisitionPrefixMiddleware

settings = get_settings()

try:
    assert_admin_auth_config(settings)
except AdminAuthError as exc:
    raise RuntimeError(str(exc)) from exc

_docs = api_docs_url_kwargs(settings.environment)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    task = None
    if settings.autonomous_scheduler_inprocess:
        from acquisition.services.autonomous_scheduler import autonomous_scheduler_loop

        task = asyncio.create_task(autonomous_scheduler_loop(settings))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title=settings.app_name,
    description="JeanneCAIO / FreedomBot — assessment and outreach",
    version="0.9.0",
    docs_url=_docs["docs_url"],
    redoc_url=_docs["redoc_url"],
    openapi_url=_docs["openapi_url"],
    lifespan=lifespan,
)

install_admin_exception_handlers(app)

_secure_cookie = not is_non_production_environment(settings.environment)
_session_max_age = max(3600, int(settings.admin_session_ttl_hours or 12) * 3600)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.admin_session_cookie_name or "freedombot_admin_session",
    max_age=_session_max_age,
    same_site="lax",
    https_only=_secure_cookie,
)

static_dir = Path(__file__).resolve().parent / "static"


class _NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


app.mount("/static", _NoCacheStaticFiles(directory=str(static_dir)), name="static")

# Public / webhook routers (no Admin session)
app.include_router(ui_auth_routes.router)
app.include_router(assessment_api.router)
app.include_router(acquisition_api.router)
app.include_router(calcom_webhooks_api.router)
app.include_router(outbound_api.webhook_router)
# TEMPORARY Stripe TEST success/cancel (payment verified server-side; not public pricing)
app.include_router(stripe_test_api.router)

# Admin-protected routers
app.include_router(prospects_api.router)
app.include_router(campaigns_api.router)
app.include_router(integrations_api.router)
app.include_router(outbound_api.admin_router)
app.include_router(dev_layout_api.router)
app.include_router(ui_routes.router)
app.include_router(ui_campaign_routes.router)
app.include_router(ui_integrations_routes.router)
app.include_router(ui_outbound_routes.router)
app.include_router(ui_assessment_routes.router)
app.include_router(ui_journey_routes.router)
app.include_router(ui_acquisition_database_routes.router)
app.include_router(ui_acquisition_live_routes.router)


@app.get("/health", tags=["health"])
def health():
    from fastapi.responses import JSONResponse

    try:
        check_database_connection()
    except Exception as exc:  # noqa: BLE001 — surface connectivity in health payload
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": settings.app_name,
                "database": f"disconnected: {exc.__class__.__name__}",
            },
        )

    payload: dict = {
        "status": "healthy",
        "service": settings.app_name,
        "database": "connected",
        "autonomous": {
            "scheduler_inprocess": bool(settings.autonomous_scheduler_inprocess),
            "scheduler_approach": "cli_loop_or_optional_inprocess",
        },
    }
    try:
        from acquisition.db.session import SessionLocal
        from acquisition.services.autonomous_automation_service import AutonomousAutomationService

        with SessionLocal() as db:
            ops = AutonomousAutomationService(db, settings=settings).operational_status()
        # Never include secrets; strip provider message verbosity if needed.
        payload["autonomous"].update(
            {
                "engine_enabled": ops.get("engine_enabled"),
                "discovery_enabled": ops.get("discovery_enabled"),
                "autonomous_outreach_enabled": ops.get("autonomous_outreach_enabled"),
                "capacity_state": (ops.get("capacity") or {}).get("state"),
                "provider_name": (ops.get("provider") or {}).get("provider_name"),
                "provider_configured": (ops.get("provider") or {}).get("configured"),
                "provider_mode": ops.get("provider_mode"),
                "provider_safe_for_production": ops.get("provider_safe_for_production"),
                "last_discovery_status": (ops.get("last_discovery") or {}).get("status"),
                "last_discovery_at": (ops.get("last_discovery") or {}).get("completed_at"),
                "last_successful_live_discovery": ops.get("last_successful_live_discovery"),
                "last_activation_status": (ops.get("last_activation") or {}).get("status"),
                "last_activation_at": (ops.get("last_activation") or {}).get("completed_at"),
                "last_discovery_error": (ops.get("last_discovery") or {}).get("error_summary"),
                "last_activation_error": (ops.get("last_activation") or {}).get("error_summary"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        payload["autonomous"]["status_error"] = exc.__class__.__name__
    return payload


# Outermost: rewrite links only when mounted under FreedomBot core.
app.add_middleware(AcquisitionPrefixMiddleware)
