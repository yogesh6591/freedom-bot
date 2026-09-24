"""Platform API routers, mounted into the same FastAPI app as AgentOS."""

from fastapi import APIRouter

from app.api import admin, approvals, audit, auth, chat, integrations, memory


def platform_router() -> APIRouter:
    """Every platform route under one router."""
    router = APIRouter()
    for module in (auth, chat, memory, approvals, integrations, audit, admin):
        router.include_router(module.router)
    return router


__all__ = ["platform_router"]
