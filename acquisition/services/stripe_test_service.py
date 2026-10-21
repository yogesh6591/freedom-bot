"""TEMPORARY development-only Stripe TEST checkout helpers.

Proves that a successful Stripe TEST payment can update existing
CampaignProspect purchase fields. Not production payment architecture.

Future checkout.session.completed webhooks should call
apply_successful_purchase — do not duplicate purchase-update logic.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.campaign import CampaignProspect, PurchaseOutcome
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED

logger = logging.getLogger(__name__)

STRIPE_META_PROJECT = "freedombot"
STRIPE_META_ENVIRONMENT = "test"
PRODUCT_NAME = "FreedomBot Integration Test"
PRODUCT_DESCRIPTION = "Development test payment - not production pricing"


class StripeTestError(Exception):
    def __init__(self, message: str, *, code: str = "stripe_test_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


def _resolve_stripe_secret(settings: Settings | None = None, db: Session | None = None) -> str:
    """DB integration credentials first, then STRIPE_SECRET_KEY env."""
    from acquisition.services.stripe_service import StripeService

    cfg = settings or get_settings()
    return (StripeService(db=db, settings=cfg).resolve_secret_key() or "").strip()


def _resolve_stripe_enabled(settings: Settings | None = None, db: Session | None = None) -> bool:
    from acquisition.services.stripe_service import StripeService

    cfg = settings or get_settings()
    return bool(StripeService(db=db, settings=cfg).resolve_enabled())


def stripe_test_secret_is_dummy(
    settings: Settings | None = None,
    db: Session | None = None,
) -> bool:
    """Dummy checkout only accepts Stripe TEST keys (sk_test_…)."""
    secret = _resolve_stripe_secret(settings, db)
    return secret.startswith("sk_test_")


def stripe_test_ui_enabled(
    settings: Settings | None = None,
    db: Session | None = None,
) -> bool:
    """True when Admin may expose the temporary Test Stripe Purchase action.

    Gated on Stripe enabled + a real sk_test_ key — not on ENVIRONMENT — so
    dummy checkout works on local and on a shared demo host. Live keys are never
    allowed for this temporary path. Env keys keep working when no DB creds exist.
    """
    if not _resolve_stripe_enabled(settings, db):
        return False
    return stripe_test_secret_is_dummy(settings, db)


def stripe_test_ui_block_reason(
    settings: Settings | None = None,
    db: Session | None = None,
) -> str | None:
    """Human reason the Test Stripe button is hidden/disabled, or None if ready."""
    if stripe_test_ui_enabled(settings, db):
        return None
    if not _resolve_stripe_enabled(settings, db):
        return "Enable Stripe under Integrations (or set STRIPE_ENABLED=true)."
    secret = _resolve_stripe_secret(settings, db)
    if not secret:
        return "Add a Stripe secret key under Integrations (or STRIPE_SECRET_KEY)."
    if not secret.startswith("sk_test_"):
        return "Dummy Stripe only accepts TEST keys starting with sk_test_."
    return "Dummy Stripe checkout is not configured."


def resolve_stripe_test_redirect_urls(
    settings: Settings | None = None,
    *,
    public_base: str | None = None,
) -> tuple[str, str]:
    """Build success/cancel URLs from the URL the admin is actually using.

    Hardcoded localhost success URLs break when Admin is opened via tunnel or
    a remote host — Stripe would redirect somewhere the browser cannot use.
    """
    cfg = settings or get_settings()
    base = (public_base or cfg.public_base_url or "http://127.0.0.1:8020").strip().rstrip("/")
    if not base:
        base = "http://127.0.0.1:8020"
    success = f"{base}/stripe-test/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel = f"{base}/stripe-test/cancel"
    return success, cancel


def _configure_stripe(settings: Settings, db: Session | None = None) -> Any:
    secret = _resolve_stripe_secret(settings, db)
    if not secret:
        raise StripeTestError("Stripe secret key is not configured.", code="stripe_not_configured")
    if not secret.startswith("sk_test_"):
        raise StripeTestError(
            "Dummy Stripe only accepts Stripe TEST keys (sk_test_…).",
            code="live_key_blocked",
        )
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover - dependency should be installed
        raise StripeTestError("stripe package is not installed.", code="stripe_missing") from exc
    stripe.api_key = secret
    return stripe


def assert_link_eligible_for_stripe_test(link: CampaignProspect) -> None:
    booked = (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )
    if not booked:
        raise StripeTestError(
            "Stripe test checkout requires a confirmed booking.",
            code="booking_required",
        )


def build_checkout_metadata(link: CampaignProspect) -> dict[str, str]:
    return {
        "project": STRIPE_META_PROJECT,
        "environment": STRIPE_META_ENVIRONMENT,
        "campaign_prospect_id": str(link.id),
        "prospect_id": str(link.prospect_id),
        "assessment_token": (link.assessment_token or ""),
    }


def create_test_checkout_session(
    link: CampaignProspect,
    *,
    settings: Settings | None = None,
    public_base: str | None = None,
) -> dict[str, Any]:
    """Create a Stripe Checkout Session (mode=payment) for a booked CampaignProspect."""
    from sqlalchemy.orm import object_session

    from acquisition.services.stripe_service import StripeService

    cfg = settings or get_settings()
    db_sess = object_session(link)
    if not stripe_test_ui_enabled(cfg, db_sess):
        raise StripeTestError("Stripe test checkout is disabled.", code="stripe_disabled")
    assert_link_eligible_for_stripe_test(link)

    amount_cents = int(cfg.stripe_test_amount_cents or 0)
    if amount_cents <= 0:
        raise StripeTestError("Invalid Stripe test amount.", code="invalid_amount")
    currency = StripeService(db=db_sess, settings=cfg).resolve_currency()
    metadata = build_checkout_metadata(link)
    success_url, cancel_base = resolve_stripe_test_redirect_urls(cfg, public_base=public_base)

    stripe = _configure_stripe(cfg, db_sess)
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[
            {
                "quantity": 1,
                "price_data": {
                    "currency": currency,
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": PRODUCT_NAME,
                        "description": PRODUCT_DESCRIPTION,
                    },
                },
            }
        ],
        success_url=success_url,
        cancel_url=f"{cancel_base}?link_id={link.id}",
        client_reference_id=str(link.id),
        metadata=metadata,
    )
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    session_id = getattr(session, "id", None) or (
        session.get("id") if isinstance(session, dict) else None
    )
    if not url or not session_id:
        raise StripeTestError("Stripe did not return a checkout URL.", code="stripe_no_url")
    return {"id": str(session_id), "url": str(url), "metadata": metadata}


def retrieve_checkout_session(session_id: str, *, settings: Settings | None = None) -> Any:
    cfg = settings or get_settings()
    cleaned = (session_id or "").strip()
    if not cleaned:
        raise StripeTestError("Missing Checkout Session id.", code="missing_session_id")
    stripe = _configure_stripe(cfg)
    return stripe.checkout.Session.retrieve(cleaned)


def _session_get(session: Any, key: str, default: Any = None) -> Any:
    if session is None:
        return default
    if isinstance(session, dict):
        return session.get(key, default)
    return getattr(session, key, default)


def amount_from_stripe_session(session: Any) -> Decimal:
    raw = _session_get(session, "amount_total")
    if raw is None:
        raise StripeTestError("Checkout Session has no amount_total.", code="missing_amount")
    return (Decimal(int(raw)) / Decimal(100)).quantize(Decimal("0.01"))


def currency_from_stripe_session(session: Any) -> str:
    raw = str(_session_get(session, "currency") or "").strip().upper()
    if not raw:
        raise StripeTestError("Checkout Session has no currency.", code="missing_currency")
    return raw[:3]


def parse_campaign_prospect_id(metadata: dict[str, Any] | None) -> uuid.UUID:
    if not isinstance(metadata, dict):
        raise StripeTestError("Checkout Session metadata is missing.", code="missing_metadata")
    if str(metadata.get("project") or "") != STRIPE_META_PROJECT:
        raise StripeTestError("Checkout Session project metadata mismatch.", code="bad_project")
    if str(metadata.get("environment") or "") != STRIPE_META_ENVIRONMENT:
        raise StripeTestError(
            "Checkout Session environment metadata mismatch.", code="bad_environment"
        )
    raw = str(metadata.get("campaign_prospect_id") or "").strip()
    if not raw:
        raise StripeTestError(
            "Checkout Session missing campaign_prospect_id.", code="missing_campaign_prospect"
        )
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise StripeTestError(
            "Checkout Session campaign_prospect_id is invalid.", code="invalid_campaign_prospect"
        ) from exc


def apply_successful_purchase(
    db: Session,
    link: CampaignProspect,
    *,
    amount: Decimal,
    currency: str,
    stripe_session_id: str,
) -> dict[str, Any]:
    """Domain purchase update shared by success-URL verify and future webhooks.

    Idempotent for the same stripe_session_id. Does not set recurring_revenue_amount.
    """
    cleaned_session = (stripe_session_id or "").strip()
    if not cleaned_session:
        raise StripeTestError("Missing stripe session id.", code="missing_session_id")

    if (
        link.stripe_checkout_session_id == cleaned_session
        and link.purchase_outcome == PurchaseOutcome.PURCHASED
    ):
        return {
            "status": "already_applied",
            "campaign_prospect_id": str(link.id),
            "stripe_checkout_session_id": cleaned_session,
        }

    currency_norm = (currency or "USD").strip().upper()[:3] or "USD"
    link.purchase_outcome = PurchaseOutcome.PURCHASED
    if link.purchased_at is None:
        link.purchased_at = datetime.now(timezone.utc)
    link.revenue_amount = amount
    link.revenue_currency = currency_norm
    link.stripe_checkout_session_id = cleaned_session
    db.add(link)
    db.commit()
    db.refresh(link)
    logger.info(
        "stripe_test_purchase_applied campaign_prospect_id=%s session_id=%s amount=%s currency=%s",
        link.id,
        cleaned_session,
        amount,
        currency_norm,
    )
    return {
        "status": "applied",
        "campaign_prospect_id": str(link.id),
        "stripe_checkout_session_id": cleaned_session,
        "revenue_amount": str(amount),
        "revenue_currency": currency_norm,
    }


def verify_and_apply_paid_session(
    db: Session,
    session_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Retrieve Checkout Session from Stripe and apply purchase when paid."""
    session = retrieve_checkout_session(session_id, settings=settings)
    payment_status = str(_session_get(session, "payment_status") or "").strip().lower()
    if payment_status != "paid":
        raise StripeTestError(
            f"Checkout Session is not paid (payment_status={payment_status!r}).",
            code="not_paid",
        )
    metadata = _session_get(session, "metadata") or {}
    if hasattr(metadata, "to_dict"):
        metadata = metadata.to_dict()
    link_id = parse_campaign_prospect_id(dict(metadata) if metadata else None)
    link = db.get(CampaignProspect, link_id)
    if link is None:
        raise StripeTestError(
            "CampaignProspect from metadata was not found.", code="campaign_prospect_not_found"
        )
    amount = amount_from_stripe_session(session)
    currency = currency_from_stripe_session(session)
    resolved_session_id = str(_session_get(session, "id") or session_id).strip()
    result = apply_successful_purchase(
        db,
        link,
        amount=amount,
        currency=currency,
        stripe_session_id=resolved_session_id,
    )
    result["payment_status"] = payment_status
    result["link"] = link
    return result
