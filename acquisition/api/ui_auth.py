"""Admin login / logout / first-time account creation UI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.auth.admin import (
    clear_admin_session,
    create_acquisition_owner_session,
    create_admin_session,
    is_admin_authenticated,
    is_authenticated,
    safe_next_url,
)
from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.services.admin_credentials_service import (
    create_admin_account,
    credentials_configured,
    resolve_login_role,
)
from acquisition.templating import create_templates

templates = create_templates()
router = APIRouter(tags=["admin-auth"])


def _login_context(
    *,
    request: Request,
    db: Session,
    error: str | None = None,
    next_url: str = "",
    mode: str | None = None,
    username: str = "",
) -> dict:
    configured = credentials_configured(db)
    active_mode = mode or ("setup" if not configured else "login")
    if configured:
        active_mode = "login"
    return {
        "error": error,
        "next": next_url or request.query_params.get("next") or "",
        "mode": active_mode,
        "username": (username or "").strip(),
    }


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if is_authenticated(request):
        from acquisition.auth.admin import is_acquisition_owner

        default = (
            "/acquisition/database" if is_acquisition_owner(request) else "/prospects"
        )
        target = safe_next_url(request.query_params.get("next")) or default
        return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        _login_context(request=request, db=db),
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse:
    settings = get_settings()
    role = resolve_login_role(
        db, username=username, password=password, settings=settings
    )
    if role is None:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            _login_context(
                request=request,
                db=db,
                error="Invalid username or password",
                next_url=next,
                mode="login",
                username=username,
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if role == "acquisition_owner":
        create_acquisition_owner_session(request, settings=settings)
        default_home = "/acquisition/database"
    else:
        create_admin_session(request, settings=settings)
        default_home = "/prospects"
    target = safe_next_url(next) or default_home
    # Owners may not land on Admin-only next URLs.
    if role == "acquisition_owner" and not str(target).startswith("/acquisition/"):
        target = default_home
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/login/setup", response_class=HTMLResponse)
async def login_setup(
    request: Request,
    db: Session = Depends(get_db),
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse:
    if credentials_configured(db):
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            _login_context(
                request=request,
                db=db,
                error="Passwords do not match",
                next_url=next,
                mode="setup",
                username=username,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        create_admin_account(db, username=username, password=password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            _login_context(
                request=request,
                db=db,
                error=str(exc),
                next_url=next,
                mode="setup",
                username=username,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    create_admin_session(request, settings=get_settings())
    target = safe_next_url(next) or "/prospects"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    if is_authenticated(request):
        from acquisition.auth.admin import validate_csrf

        await validate_csrf(request)
    clear_admin_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
