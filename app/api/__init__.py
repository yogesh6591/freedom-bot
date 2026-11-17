"""Platform API routers, mounted into the same FastAPI app as AgentOS."""

from fastapi import APIRouter

from app.api import access, admin, approvals, audit, auth, chat, integrations, memory, owner, signup, support


def platform_router() -> APIRouter:
    """Every platform route under one router."""
    router = APIRouter()
    for module in (
        auth,
        chat,
        memory,
        approvals,
        integrations,
        audit,
        admin,
        access,
        support,
        owner,
        signup,
    ):
        router.include_router(module.router)
    return router


__all__ = ["platform_router"]
