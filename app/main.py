"""
AgentOS Entrypoint
==================

Boots agno's ``AgentOS`` and mounts the platform API into **the same** FastAPI
application (§26: "Follow the framework already used by the selected Agno
boilerplate rather than introducing unnecessary backend frameworks").

``AgentOS(base_app=...)`` takes the app our routers are already on and adds its
own routes to it, with ``on_route_conflict="preserve_agentos"`` so agno's
surface always wins a collision. One process, one port, one OpenAPI document.

What comes from agno, unchanged: the agent runtime, session and run persistence,
tracing, the scheduler, the knowledge routers, its own approvals surface, and
JWT/service-account verification.

What the platform adds: tenancy, RBAC, the policy engine, execution modes,
organizational memory, business-action approvals, audit, domain packs and connectors.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from agno.utils.log import log_error, log_info, log_warning
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from agno.db.postgres import PostgresDb

from app.api import platform_router
from bizos import settings
from bizos.control.db import dispose_control_engine, migrate_control_plane
from bizos.tenancy.registry import registry
from bizos.tools.registry_check import assert_registry_sound, verify_registry
from bizos.util.dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Startup: migrate the control plane and verify the tool registry."""
    log_info("bizos: starting")
    try:
        migrate_control_plane()
        log_info("bizos: control plane ready")
    except Exception as exc:
        # The API must still start so the failure is visible at /health rather
        # than as a container that will not boot.
        log_error(f"bizos: control plane migration failed: {exc}")

    try:
        assert_registry_sound()
        report = verify_registry()
        log_info(
            f"bizos: tool registry sound "
            f"({len(report['declared_not_implemented'])} declared-not-implemented)"
        )
    except AssertionError as exc:
        log_error(f"bizos: {exc}")

    if not settings.llm_available():
        log_warning(
            "OPENAI_API_KEY is not set — chat is disabled. Policy, approvals, memory, "
            "audit and the connectors all work without it."
        )
    try:
        yield
    finally:
        registry.dispose_all()
        dispose_control_engine()
        log_info("bizos: stopped")


# The platform's own app. AgentOS mounts onto this one.
base_app = FastAPI(
    title="Business AI Agent Platform",
    description=(
        "Multi-tenant business agent platform on Agno AgentOS. Each customer gets an "
        "isolated workspace with its own database, organizational memory, policy engine, "
        "approval queue and audit trail."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

base_app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    # Credentialed requests carry the HttpOnly session cookie, so the origin list
    # must be explicit — never "*", which browsers reject with credentials anyway.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

base_app.include_router(platform_router())


# Mounted at /api/health, not /health: AgentOS publishes its own /health and is
# configured to win route conflicts, so a platform route there would be shadowed.
@base_app.get("/api/health", tags=["health"])
def health() -> dict[str, Any]:
    """Liveness plus the facts an operator needs to diagnose a bad deploy."""
    from bizos.control.db import control_engine
    from sqlalchemy import text

    control_ok = True
    control_error = None
    try:
        with control_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        control_ok = False
        control_error = str(exc)

    report = verify_registry()
    return {
        "status": "ok" if control_ok else "degraded",
        "runtime_env": settings.runtime_env(),
        "control_plane": {"ok": control_ok, "error": control_error},
        "llm_configured": settings.llm_available(),
        "tool_registry": {
            "sound": not report["effects_without_spec"] and not report["implemented_without_effect"],
            "declared_not_implemented": report["declared_not_implemented"],
        },
    }


@base_app.get("/api/version", tags=["health"])
def version() -> dict[str, str]:
    import agno

    return {"platform": "1.0.0", "agno": getattr(agno, "__version__", "3.0.9")}


# AgentOS needs a database for its own OS-level bookkeeping (traces, schedules,
# service accounts). That is *platform* metadata, so it lives in the control
# database under agno's own `agno` schema — never mixed with customer content.
#
# Tenant agent sessions, runs and conversational memory do NOT go here: each
# request builds its agent against `bizos.tenancy.agnodb.workspace_db(ctx)`, so
# they land inside the client's own isolated workspace. Agno's OS-level routers
# therefore show platform bookkeeping; per-tenant history is served by /api/*.
os_db = PostgresDb(id="bizos-os", db_url=settings.control_db_url(), db_schema="agno")

# Agents are constructed per request (see bizos.agents.agent), so none are
# registered here — a long-lived agent object would belong to whichever tenant
# built it last.
agent_os = AgentOS(
    db=os_db,
    id="bizos",
    name="Business AI Agent Platform",
    description="Multi-tenant business agent platform",
    version="1.0.0",
    base_app=base_app,
    on_route_conflict="preserve_agentos",
    tracing=True,
    telemetry=False,
    # Platform routes authenticate themselves against the signed session token
    # (app/api/deps.py). Agno's own routers additionally enforce user isolation
    # when authorization is on.
    authorization=settings.is_prd(),
    authorization_config=AuthorizationConfig(user_isolation=True),
    internal_service_token=settings.internal_service_token(),
    # Auto-provisioning is off: workspaces are created deliberately by
    # bizos.tenancy.provisioning, never as a side effect of a request.
    auto_provision_dbs=False,
)

app = agent_os.get_app()


@app.exception_handler(Exception)
async def unhandled(request: Any, exc: Exception) -> JSONResponse:
    """Never leak internals to a client; log the detail server-side."""
    log_error(f"unhandled error on {getattr(request, 'url', '?')}: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.runtime_env() == "dev",
    )
