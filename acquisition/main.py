"""FreedomBot FastAPI application."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from acquisition.api import acquisition as acquisition_api
from acquisition.api import assessment as assessment_api
from acquisition.api import calcom_webhooks as calcom_webhooks_api
from acquisition.api import dev_layout as dev_layout_api
from acquisition.api import outbound as outbound_api
from acquisition.api import prospects as prospects_api
from acquisition.api import stripe_test as stripe_test_api
from acquisition.api import ui as ui_routes
from acquisition.api import ui_acquisition_database as ui_acquisition_database_routes
from acquisition.api import ui_acquisition_live as ui_acquisition_live_routes  # noqa: E402
from acquisition.api import ui_assessments as ui_assessment_routes
from acquisition.api import ui_auth as ui_auth_routes
from acquisition.api import ui_integrations as ui_integrations_routes
from acquisition.api import ui_journey as ui_journey_routes
from acquisition.api import ui_outbound as ui_outbound_routes
from acquisition.api import ui_outreach as ui_outreach_routes
from acquisition.api import ui_sending as ui_sending_routes
from acquisition.api import ui_prospecting as ui_prospecting_routes
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
    yield


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
# TEMPORARY Stripe TEST success/cancel (payment verified server-side; not public pricing)
app.include_router(stripe_test_api.router)

# Admin-protected routers
app.include_router(prospects_api.router)
app.include_router(outbound_api.admin_router)
app.include_router(dev_layout_api.router)
app.include_router(ui_routes.router)
app.include_router(ui_integrations_routes.router)
app.include_router(ui_outbound_routes.router)
app.include_router(ui_outreach_routes.router)
app.include_router(ui_sending_routes.router)
app.include_router(ui_prospecting_routes.router)
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

    return {
        "status": "healthy",
        "service": settings.app_name,
        "database": "connected",
        "autonomous": {
            "retired": True,
            "scheduler_inprocess": False,
        },
    }


# Outermost: rewrite links only when mounted under FreedomBot core.
app.add_middleware(AcquisitionPrefixMiddleware)
