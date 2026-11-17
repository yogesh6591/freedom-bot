"""Self-serve signup from the FreedomBot website.

After a customer pays on the website, its server calls this with the company, the departments they
bought and their admin login. It makes the client exactly as a platform owner does from the New client
form (:func:`freedombot.tenancy.new_client.create_client`): its own workspace, the departments enabled,
and the first admin.

Only the website's server may call it: every request carries the shared ``SELF_SERVE_SIGNUP_KEY``, which
never reaches a browser. With no key set, signup is switched off. Each address gets a few signups an hour.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from freedombot import admin_ops
from freedombot.tenancy import new_client

router = APIRouter(prefix="/api/public/signup", tags=["signup"])

#: Signups (and identifier checks) one address may make per hour.
SIGNUPS_PER_HOUR = 5
CHECKS_PER_HOUR = 120
_seen: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def _key() -> str:
    return os.environ.get("SELF_SERVE_SIGNUP_KEY", "").strip()


def _require_key(given: Optional[str]) -> None:
    expected = _key()
    if not expected:
        raise HTTPException(status_code=404, detail="Self-serve signup is not enabled on this server")
    if not given or not hmac.compare_digest(given.strip(), expected):
        raise HTTPException(status_code=401, detail="Signup key missing or wrong")


def _limit(request: Request, bucket: str, per_hour: int) -> None:
    # The website forwards the customer's address; fall back to whoever is calling.
    who = (request.headers.get("x-customer-ip") or (request.client.host if request.client else "")).strip()
    key, now = f"{bucket}:{who}", time.monotonic()
    with _lock:
        seen = _seen[key]
        while seen and now - seen[0] > 3600:
            seen.popleft()
        if len(seen) >= per_hour:
            raise HTTPException(status_code=429, detail="Too many attempts. Try again in an hour.")
        seen.append(now)


class SignupBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=new_client.MAX_NAME_LENGTH)
    #: Leave empty to make it from the company name.
    slug: Optional[str] = Field(default=None, max_length=40)
    #: Departments bought. General is always included.
    purchased_domains: list[str] = Field(default_factory=list)
    plan_id: str = Field(default="", max_length=40)
    admin_name: str = Field(default="", max_length=120)
    admin_email: EmailStr
    admin_password: str = Field(min_length=admin_ops.MIN_PASSWORD_LENGTH, max_length=512)
    #: The payment reference from checkout (demo checkout sends "demo").
    payment_reference: str = Field(default="", max_length=200)


def _actor():
    """Who made the client, in the control-plane audit: the website, inside Cepoch's internal workspace."""
    from freedombot.bootstrap import ensure_internal_workspace
    from freedombot.control import store as control

    return control.system_context(ensure_internal_workspace().id, user_id="__website_signup__")


@router.get("/slug")
def check_slug(
    request: Request, slug: str, x_signup_key: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    """Whether a workspace identifier is free, as the server would write it."""
    _require_key(x_signup_key)
    _limit(request, "check", CHECKS_PER_HOUR)
    from freedombot.util.ids import slugify

    wanted = slugify(slug)
    return {"slug": wanted, "available": not new_client._slug_taken(wanted)}


@router.post("", status_code=201)
def signup(request: Request, body: SignupBody, x_signup_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Make the paid client's workspace and first admin. The password is never returned or recorded."""
    _require_key(x_signup_key)
    _limit(request, "signup", SIGNUPS_PER_HOUR)
    try:
        made = new_client.create_client(
            company_name=body.company_name,
            slug=body.slug or None,
            purchased_domains=body.purchased_domains,
            admin_name=body.admin_name,
            admin_email=str(body.admin_email),
            admin_password=body.admin_password,
            actor=_actor(),
        )
    except admin_ops.AdminError as exc:  # bad input, a taken identifier, or a setup that was undone
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    _remember_purchase(made["client"]["id"], body)
    return {
        "workspace": made["client"]["slug"],
        "company_name": made["client"]["company_name"],
        "purchased_domains": made["client"]["purchased_domains"],
        "admin_email": made["admin"]["email"],
        "admin_name": made["admin"]["display_name"],
    }


def _remember_purchase(client_id: str, body: SignupBody) -> None:
    """Keep the plan and payment reference with the client, for Jeanne's owner view and billing."""
    from freedombot.control import store as control
    from freedombot.control.models import ClientSettings
    from freedombot.util.timeutil import utcnow

    try:
        client = control.get_client(client_id)
        payload = client.settings.to_dict()
        onboarding = dict(payload.get("onboarding") or {})
        onboarding["signup"] = {"source": "website", "plan": body.plan_id, "payment_reference": body.payment_reference,
                                "at": utcnow().isoformat()}
        payload["onboarding"] = onboarding
        control.update_client_settings(client_id, ClientSettings.from_dict(payload), updated_by="__website_signup__")
    except Exception as exc:  # the client is made; a missing note must not report it as failed
        from agno.utils.log import log_warning

        log_warning(f"could not record the website purchase for {client_id}: {exc}")
