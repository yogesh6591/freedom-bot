"""JeanneCAIO post-consult checkout + Cepoch handoff.

Jeanne's required commercial process (Stripe TEST credentials OK for now):
1. After consult, Jeanne offers a customized checkout amount.
2. Prospect opens JeanneCAIO-branded checkout and pays via Stripe Checkout.
3. Successful payment records purchase and marks Cepoch handoff READY.
4. Delivery/onboarding proceeds from that handoff.

This is the product path — not an admin-only "pay as yourself" stub.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.models.campaign import CampaignProspect, HandoffStatus, PurchaseOutcome
from acquisition.services.assessment_tokens import build_assessment_url
from acquisition.services.booking_service import BOOKING_STATUS_CONFIRMED
from acquisition.services.stripe_test_service import (
    StripeTestError,
    _configure_stripe,
    apply_successful_purchase,
    build_checkout_metadata,
    stripe_test_ui_enabled,
    verify_and_apply_paid_session,
)

logger = logging.getLogger(__name__)

PRODUCT_NAME = "FreedomBot — JeanneCAIO"
PRODUCT_DESCRIPTION = (
    "Custom FreedomBot engagement scoped during your JeanneCAIO consultation."
)


class JeanneCheckoutError(Exception):
    def __init__(self, message: str, *, code: str = "checkout_error", http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


def build_checkout_url(token: str, *, settings: Settings | None = None) -> str:
    cfg = settings or get_settings()
    base = (cfg.public_base_url or "").rstrip("/") + "/"
    return urljoin(base, f"a/{token}/checkout")


def _money(value: Any) -> Decimal | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise JeanneCheckoutError("Invalid money amount.", code="invalid_amount") from exc
    if amount < 0:
        raise JeanneCheckoutError("Amount cannot be negative.", code="invalid_amount")
    return amount.quantize(Decimal("0.01"))


def _booked(link: CampaignProspect) -> bool:
    return (
        str(link.booking_status or "").strip().lower() == BOOKING_STATUS_CONFIRMED
        and link.booked_at is not None
    )


def _amount_to_cents(amount: Decimal) -> int:
    cents = int((amount * 100).quantize(Decimal("1")))
    if cents <= 0:
        raise JeanneCheckoutError("Checkout amount must be greater than zero.", code="invalid_amount")
    return cents


def checkout_payments_enabled(
    settings: Settings | None = None,
    db: Session | None = None,
) -> bool:
    """Stripe TEST (or future live) path available for Jeanne checkout."""
    return stripe_test_ui_enabled(settings, db)


def offer_checkout(
    db: Session,
    link: CampaignProspect,
    *,
    amount: Any,
    recurring_amount: Any = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """Jeanne offers post-consult checkout to the prospect (creates the public link)."""
    if not _booked(link):
        raise JeanneCheckoutError(
            "Checkout can only be offered after a confirmed booking.",
            code="booking_required",
            http_status=400,
        )
    # Real Stripe-paid purchases always offered checkout first. A manual "Purchased"
    # mark with no prior offer is a premature override — clear it so Jeanne can send the link.
    if link.purchase_outcome == PurchaseOutcome.PURCHASED and link.checkout_offered_at is not None:
        raise JeanneCheckoutError(
            "This prospect already purchased.",
            code="already_purchased",
            http_status=400,
        )

    offer_amount = _money(amount)
    if offer_amount is None:
        raise JeanneCheckoutError("Enter the customized investment amount.", code="amount_required")
    recurring = _money(recurring_amount)
    currency_norm = (currency or "USD").strip().upper()[:3] or "USD"

    link.checkout_offer_amount = offer_amount
    link.checkout_offer_recurring_amount = recurring
    link.checkout_offer_currency = currency_norm
    link.checkout_offered_at = datetime.now(timezone.utc)
    # Keep proposed amount visible in admin revenue fields until Stripe overwrites on pay.
    link.revenue_amount = offer_amount
    link.recurring_revenue_amount = recurring
    link.revenue_currency = currency_norm
    if link.purchase_outcome in {PurchaseOutcome.DID_NOT_PURCHASE, PurchaseOutcome.PURCHASED}:
        link.purchase_outcome = PurchaseOutcome.NOT_RECORDED
        link.purchased_at = None
    db.add(link)
    db.commit()
    db.refresh(link)

    token = link.assessment_token or ""
    checkout_url = build_checkout_url(token) if token else None
    return {
        "status": "offered",
        "campaign_prospect_id": str(link.id),
        "checkout_url": checkout_url,
        "amount": str(offer_amount),
        "recurring_amount": str(recurring) if recurring is not None else None,
        "currency": currency_norm,
        "offered_at": link.checkout_offered_at.isoformat() if link.checkout_offered_at else None,
    }


def mark_handoff(
    db: Session,
    link: CampaignProspect,
    *,
    status: HandoffStatus,
) -> dict[str, Any]:
    if status not in {HandoffStatus.READY, HandoffStatus.HANDED_OFF, HandoffStatus.NOT_STARTED}:
        raise JeanneCheckoutError("Invalid handoff status.", code="invalid_handoff")
    link.handoff_status = status.value
    if status == HandoffStatus.NOT_STARTED:
        link.handoff_at = None
    else:
        link.handoff_at = datetime.now(timezone.utc)
    db.add(link)
    db.commit()
    db.refresh(link)
    return {
        "status": link.handoff_status,
        "handoff_at": link.handoff_at.isoformat() if link.handoff_at else None,
    }


def public_checkout_payload(link: CampaignProspect, *, settings: Settings | None = None) -> dict[str, Any]:
    """Prospect-facing checkout page payload (JeanneCAIO branded)."""
    cfg = settings or get_settings()
    prospect = link.prospect
    company = (getattr(prospect, "company", None) or "").strip() if prospect else ""
    name = (getattr(prospect, "executive_name", None) or "").strip() if prospect else ""
    already_paid = link.purchase_outcome == PurchaseOutcome.PURCHASED
    offered = link.checkout_offered_at is not None and link.checkout_offer_amount is not None

    if already_paid and link.revenue_amount is not None:
        amount = link.revenue_amount
        currency = (link.revenue_currency or link.checkout_offer_currency or "USD").upper()
    else:
        amount = link.checkout_offer_amount
        currency = (link.checkout_offer_currency or "USD").upper()

    if already_paid and link.recurring_revenue_amount is not None:
        recurring = link.recurring_revenue_amount
    else:
        recurring = link.checkout_offer_recurring_amount

    paid_at = link.purchased_at
    session_ref = (link.stripe_checkout_session_id or "").strip()
    receipt_code = session_ref[-10:].upper() if len(session_ref) >= 10 else (session_ref.upper() or None)

    return {
        "token": link.assessment_token,
        "company": company or None,
        "contact_name": name or None,
        "offered": offered,
        "already_paid": already_paid,
        "amount": float(amount) if amount is not None else None,
        "recurring_amount": float(recurring) if recurring is not None else None,
        "currency": currency,
        "amount_label": _format_money(amount, currency) if amount is not None else None,
        "recurring_label": _format_money(recurring, currency) if recurring is not None else None,
        "payments_enabled": checkout_payments_enabled(cfg),
        "handoff_status": link.handoff_status,
        "purchased_at": paid_at,
        "receipt_code": receipt_code,
        "stripe_session_id": session_ref or None,
        "booked_url": build_assessment_url(link.assessment_token, settings=cfg) + "/booked"
        if link.assessment_token
        else None,
    }


def _format_money(amount: Any, currency: str) -> str:
    from acquisition.templating import format_money

    return format_money(amount, currency)


def create_prospect_checkout_session(
    link: CampaignProspect,
    *,
    settings: Settings | None = None,
    public_base: str | None = None,
) -> dict[str, Any]:
    """Create Stripe Checkout for the prospect (JeanneCAIO product copy)."""
    from sqlalchemy.orm import object_session

    cfg = settings or get_settings()
    db_sess = object_session(link)
    if not checkout_payments_enabled(cfg, db_sess):
        raise JeanneCheckoutError(
            "Checkout payments are not configured (need Stripe under Integrations or STRIPE_ENABLED + sk_test_ key).",
            code="stripe_disabled",
            http_status=503,
        )
    if not _booked(link):
        raise JeanneCheckoutError(
            "Booking must be confirmed before checkout.",
            code="booking_required",
            http_status=400,
        )
    if link.purchase_outcome == PurchaseOutcome.PURCHASED:
        raise JeanneCheckoutError("Already purchased.", code="already_purchased", http_status=400)
    if link.checkout_offered_at is None or link.checkout_offer_amount is None:
        raise JeanneCheckoutError(
            "Jeanne has not offered checkout for this consultation yet.",
            code="offer_required",
            http_status=403,
        )

    amount = Decimal(str(link.checkout_offer_amount)).quantize(Decimal("0.01"))
    amount_cents = _amount_to_cents(amount)
    currency = (link.checkout_offer_currency or cfg.stripe_currency or "usd").strip().lower() or "usd"
    token = link.assessment_token or ""
    base = (public_base or cfg.public_base_url or "http://127.0.0.1:8020").strip().rstrip("/")
    success_url = f"{base}/a/{token}/checkout/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{base}/a/{token}/checkout?cancelled=1"
    metadata = build_checkout_metadata(link)
    metadata["flow"] = "jeanne_checkout"

    stripe = _configure_stripe(cfg, db_sess)

    # Prefill US billing so Checkout does not default Country to the payer's IP
    # (e.g. India when Jeanne tests from India). JeanneCAIO engagements are USD/US.
    from acquisition.models.prospect import Prospect

    prospect = getattr(link, "prospect", None)
    if prospect is None and db_sess is not None and link.prospect_id:
        prospect = db_sess.get(Prospect, link.prospect_id)

    customer_params: dict[str, Any] = {
        "address": {"country": "US"},
        "metadata": {
            "campaign_prospect_id": str(link.id),
            "flow": "jeanne_checkout",
        },
    }
    if prospect is not None:
        email = str(getattr(prospect, "email", "") or "").strip()
        name = str(getattr(prospect, "executive_name", "") or "").strip()
        if email:
            customer_params["email"] = email
        if name:
            customer_params["name"] = name
    customer = stripe.Customer.create(**customer_params)
    customer_id = getattr(customer, "id", None) or (
        customer.get("id") if isinstance(customer, dict) else None
    )
    if not customer_id:
        raise JeanneCheckoutError("Stripe did not return a customer id.", code="stripe_no_customer")

    session = stripe.checkout.Session.create(
        mode="payment",
        customer=str(customer_id),
        customer_update={"address": "auto", "name": "auto"},
        locale="en",
        billing_address_collection="auto",
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
        cancel_url=cancel_url,
        client_reference_id=str(link.id),
        metadata=metadata,
    )
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    session_id = getattr(session, "id", None) or (
        session.get("id") if isinstance(session, dict) else None
    )
    if not url or not session_id:
        raise JeanneCheckoutError("Stripe did not return a checkout URL.", code="stripe_no_url")
    return {"id": str(session_id), "url": str(url), "metadata": metadata}


def complete_paid_checkout(
    db: Session,
    session_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Verify Stripe session, record purchase, mark Cepoch handoff ready."""
    result = verify_and_apply_paid_session(db, session_id, settings=settings)
    link_id_raw = result.get("campaign_prospect_id")
    if not link_id_raw:
        return result
    try:
        link_id = uuid.UUID(str(link_id_raw))
    except ValueError:
        return result
    link = db.get(CampaignProspect, link_id)
    if link is None:
        return result

    # Copy offered recurring onto the purchase record when Stripe only charged one-time.
    if (
        link.purchase_outcome == PurchaseOutcome.PURCHASED
        and link.checkout_offer_recurring_amount is not None
        and link.recurring_revenue_amount is None
    ):
        link.recurring_revenue_amount = link.checkout_offer_recurring_amount
        db.add(link)

    if link.purchase_outcome == PurchaseOutcome.PURCHASED and link.handoff_status in {
        HandoffStatus.NOT_STARTED.value,
        None,
        "",
    }:
        link.handoff_status = HandoffStatus.READY.value
        link.handoff_at = datetime.now(timezone.utc)
        db.add(link)
        logger.info(
            "jeanne_checkout_handoff_ready campaign_prospect_id=%s",
            link.id,
        )

    db.commit()
    db.refresh(link)
    result["handoff_status"] = link.handoff_status
    result["token"] = link.assessment_token
    return result


# Re-export for callers that already import Stripe helpers from here.
__all__ = [
    "JeanneCheckoutError",
    "PRODUCT_DESCRIPTION",
    "PRODUCT_NAME",
    "StripeTestError",
    "apply_successful_purchase",
    "build_checkout_url",
    "checkout_payments_enabled",
    "complete_paid_checkout",
    "create_prospect_checkout_session",
    "mark_handoff",
    "offer_checkout",
    "public_checkout_payload",
]
