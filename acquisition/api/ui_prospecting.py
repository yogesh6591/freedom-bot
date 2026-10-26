"""Admin JSON API for Jeanne's prospecting rules (Engine & AI)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.db.session import get_db
from acquisition.services.prospecting_rules_service import (
    ProspectingRulesError,
    ProspectingRulesService,
    ProspectingStaleError,
    STALE_MESSAGE,
)

router = APIRouter(tags=["admin-ui-prospecting"], dependencies=[Depends(require_admin_html_request)])


def _actor(request: Request) -> tuple[str, Any]:
    ctx = None
    try:
        from bizos.tenancy.context import current_context

        ctx = current_context()
    except Exception:  # noqa: BLE001
        ctx = None
    if ctx is None:
        token = (request.cookies.get("bizos_session") or "").strip()
        if token:
            try:
                from bizos.control import store as control
                from bizos.control.tokens import verify_token

                payload = verify_token(token)
                ctx = control.build_context(control.get_user(str(payload.get("sub") or "")))
            except Exception:  # noqa: BLE001
                ctx = None
    if ctx is not None:
        return str(ctx.user_id), ctx
    return str(request.session.get("auth_role") or "admin"), None


def _error(messages: list[str], *, status_code: int = 400) -> JSONResponse:
    text = messages[0] if messages else "Invalid prospecting rules"
    return JSONResponse({"error": text, "errors": messages}, status_code=status_code)


@router.get("/settings/ai/prospecting-rules")
def get_rules(db: Session = Depends(get_db)) -> dict[str, Any]:
    return ProspectingRulesService(db).current().to_dict()


@router.put("/settings/ai/prospecting-rules")
async def put_rules(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        return _error(["The request must be a JSON object."])
    try:
        expected = int(body.get("version"))
    except (TypeError, ValueError):
        return _error(["Reload the page and save again."])
    actor, ctx = _actor(request)
    try:
        saved = ProspectingRulesService(db).save(body, expected_version=expected, actor=actor, ctx=ctx)
    except ProspectingStaleError:
        return _error([STALE_MESSAGE], status_code=409)
    except ProspectingRulesError as exc:
        return _error(exc.messages)
    return JSONResponse(saved.to_dict())


@router.get("/settings/ai/prospecting-rules/history")
def get_history(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"versions": ProspectingRulesService(db).history()}


@router.post("/settings/ai/prospecting-rules/restore")
async def post_restore(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        return _error(["The request must be a JSON object."])
    try:
        version = int(body.get("restore_version"))
        expected = int(body.get("version"))
    except (TypeError, ValueError):
        return _error(["Say which version to restore, and reload if the page is stale."])
    actor, ctx = _actor(request)
    try:
        saved = ProspectingRulesService(db).restore(version, expected_version=expected, actor=actor, ctx=ctx)
    except ProspectingStaleError:
        return _error([STALE_MESSAGE], status_code=409)
    except ProspectingRulesError as exc:
        return _error(exc.messages)
    return JSONResponse(saved.to_dict())


@router.post("/settings/ai/prospecting-rules/reset")
async def post_reset(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    body = await request.json()
    if not isinstance(body, dict):
        return _error(["The request must be a JSON object."])
    try:
        expected = int(body.get("version"))
    except (TypeError, ValueError):
        return _error(["Reload the page and try again."])
    actor, ctx = _actor(request)
    try:
        saved = ProspectingRulesService(db).reset(expected_version=expected, actor=actor, ctx=ctx)
    except ProspectingStaleError:
        return _error([STALE_MESSAGE], status_code=409)
    except ProspectingRulesError as exc:
        return _error(exc.messages)
    return JSONResponse(saved.to_dict())
