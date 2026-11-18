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


#: The plans sold at signup, as the signup page shows them (ui/src/lib/signup.ts): monthly price in cents
#: and the departments each covers. The server decides the price, never the browser.
SIGNUP_PLANS: dict[str, dict[str, Any]] = {
    "starter": {"name": "Starter", "cents": 24900, "domains": {"general", "operations"}},
    "growth": {"name": "Growth", "cents": 74900,
               "domains": {"general", "operations", "sales", "intake", "planning"}},
    "enterprise": {"name": "Enterprise", "cents": 199900, "domains": None},  # None: every department
}


def _plan(plan_id: str, domains: list[str]) -> dict[str, Any]:
    """The chosen plan, refused when it does not cover every chosen department."""
    plan = SIGNUP_PLANS.get((plan_id or "").strip().casefold())
    if plan is None:
        raise HTTPException(status_code=400, detail="Choose a plan.")
    wanted = {str(d).strip().casefold() for d in domains if str(d).strip()} | {"general"}
    if plan["domains"] is not None and not wanted <= plan["domains"]:
        raise HTTPException(status_code=400,
                            detail=f"{plan['name']} doesn't include every department you picked. Choose a larger plan.")
    return plan


def _stripe_keys() -> Optional[tuple[str, str]]:
    """(secret key, publishable key) when Stripe checkout is switched on; None means the demo checkout."""
    enabled = os.environ.get("STRIPE_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}
    secret = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    publishable = os.environ.get("STRIPE_PUBLISHABLE_KEY", "").strip()
    if not enabled:
        return None
    if not secret or not publishable:
        raise HTTPException(status_code=503, detail="Payments are not set up yet. Please contact FreedomBot.")
    return secret, publishable


def _domains_tag(domains: list[str]) -> str:
    return ",".join(sorted({str(d).strip().casefold() for d in domains if str(d).strip()} | {"general"}))


def _order_tags(*, slug: str, plan_id: str, email: str, domains: list[str]) -> dict[str, str]:
    """What a Checkout Session is for, so a payment can only make the workspace it was taken for."""
    from freedombot.util.ids import slugify

    return {"signup_slug": slugify(slug), "signup_plan": plan_id.strip().casefold(),
            "signup_email": email.strip().casefold(), "signup_domains": _domains_tag(domains)}


class CheckoutBody(BaseModel):
    company_name: str = Field(min_length=1, max_length=new_client.MAX_NAME_LENGTH)
    slug: Optional[str] = Field(default=None, max_length=40)
    purchased_domains: list[str] = Field(default_factory=list)
    plan_id: str = Field(default="", max_length=40)
    admin_email: EmailStr


@router.post("/checkout")
def start_checkout(request: Request, body: CheckoutBody, x_signup_key: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Start paying for a workspace. With Stripe switched on this makes an embedded Checkout Session for the
    plan's monthly subscription; otherwise the page uses the demo checkout."""
    _require_key(x_signup_key)
    _limit(request, "check", CHECKS_PER_HOUR)
    plan = _plan(body.plan_id, body.purchased_domains)
    keys = _stripe_keys()
    if keys is None:
        return {"mode": "demo"}
    from freedombot.util.ids import slugify

    slug = slugify(body.slug or body.company_name)
    if new_client._slug_taken(slug):
        raise HTTPException(status_code=409, detail=f'The workspace ID "{slug}" is already taken. Choose another.')
    import stripe

    tags = _order_tags(slug=slug, plan_id=body.plan_id, email=str(body.admin_email), domains=body.purchased_domains)
    try:
        session = stripe.checkout.Session.create(
            api_key=keys[0],
            ui_mode="embedded",
            mode="subscription",
            redirect_on_completion="never",
            customer_email=str(body.admin_email),
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": os.environ.get("STRIPE_CURRENCY", "usd").strip().casefold() or "usd",
                    "unit_amount": plan["cents"],
                    "recurring": {"interval": "month"},
                    "product_data": {"name": f"FreedomBot {plan['name']}"},
                },
            }],
            metadata=tags,
            subscription_data={"metadata": tags},
        )
    except stripe.StripeError as exc:
        from agno.utils.log import log_warning

        log_warning(f"signup checkout could not start: {exc}")
        raise HTTPException(status_code=502, detail="Payment could not be started. Try again in a minute.") from exc
    return {"mode": "stripe", "client_secret": session.client_secret, "publishable_key": keys[1],
            "session_id": session.id}


def _confirm_payment(body: "SignupBody") -> None:
    """With Stripe on: the Checkout Session must be complete, paid, and for exactly this workspace and plan."""
    keys = _stripe_keys()
    if keys is None:
        return
    reference = (body.payment_reference or "").strip()
    if not reference.startswith("cs_"):
        raise HTTPException(status_code=402, detail="Payment is required before the workspace is created.")
    import stripe

    try:
        session = stripe.checkout.Session.retrieve(reference, api_key=keys[0])
    except stripe.StripeError as exc:
        raise HTTPException(status_code=402, detail="That payment could not be found.") from exc
    if session.status != "complete" or session.payment_status not in {"paid", "no_payment_required"}:
        raise HTTPException(status_code=402, detail="The payment has not gone through yet.")
    wanted = _order_tags(slug=body.slug or body.company_name, plan_id=body.plan_id,
                         email=str(body.admin_email), domains=body.purchased_domains)
    paid_for = dict(session.metadata or {})
    if any(paid_for.get(k) != v for k, v in wanted.items()):
        raise HTTPException(status_code=402, detail="That payment was for a different workspace or plan.")


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
    _plan(body.plan_id, body.purchased_domains)
    _confirm_payment(body)
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
        if (body.payment_reference or "").startswith("cs_"):
            from agno.utils.log import log_warning

            # Paid but not made: support needs the payment to refund it or finish the setup by hand.
            log_warning(f"signup paid ({body.payment_reference}) but the workspace was not made: {exc.message}")
            raise HTTPException(status_code=exc.status, detail=(
                f"{exc.message} Your payment went through: contact FreedomBot support with reference "
                f"{body.payment_reference} and we will finish your setup.")) from exc
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
