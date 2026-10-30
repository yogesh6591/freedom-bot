"""One-time payments: set the amount a person is offered, and email them the payment link.

Payment is Stripe Checkout, one time only (no recurring). The offer needs a qualified person with a confirmed booking. Paying is
done by the prospect on the checkout page; nothing here takes money or records a payment by hand.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from acquisition.models.assessment import AssessmentProfile
from acquisition.models.campaign import CampaignProspect, PurchaseOutcome
from acquisition.models.journey import QualificationStatus
from acquisition.models.prospect import Prospect
from acquisition.services.jeanne_checkout_service import (
    JeanneCheckoutError,
    build_checkout_url,
    checkout_payments_enabled,
    offer_checkout,
)
from app.outreach import datasets, lookup, records
from app.outreach.toolkit import Call, ToolError, tool

CURRENCY = "USD"
MAX_AMOUNT = Decimal("1000000")


def _amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        raise ToolError(f"'{value}' is not an amount. Give a number such as 5000.") from None
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ToolError(f"the amount must be more than 0 and at most {MAX_AMOUNT:,.0f}.")
    return amount.quantize(Decimal("0.01"))


def _person_link(call: Call, who: str) -> tuple[Prospect, CampaignProspect]:
    p = lookup.person(call, who)
    link = records.link_row(call.db, p.id)
    if link is None or not link.assessment_token:
        raise ToolError(f"{p.executive_name} has no assessment link yet, so no booking or payment either.")
    profile = call.db.scalar(select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id))
    if profile is None or profile.qualification_status != QualificationStatus.QUALIFIED:
        raise ToolError(f"{p.executive_name} is not qualified yet, so no payment can be offered. "
                        "Their assessment has to be finished and qualified first.")
    return p, link


@tool("create_payment_offer", "who", "amount")
def create_payment_offer(call: Call, who: str = "", amount: object = None) -> str:
    """Set the one-time amount a booked person can pay, and return their payment link."""
    p, link = _person_link(call, who)
    price = _amount(amount)
    before = link.checkout_offer_amount
    try:
        offer_checkout(call.db, link, amount=str(price), currency=CURRENCY)
    except JeanneCheckoutError as exc:
        if exc.code == "booking_required":
            raise ToolError(f"{p.executive_name} has no confirmed booking yet. A payment offer needs a booked call.") from exc
        raise ToolError(exc.message) from exc
    url = build_checkout_url(link.assessment_token)
    was = f" (was {datasets.fmt_money(before)})" if before is not None else ""
    note = "" if checkout_payments_enabled(db=call.db) else \
        " Stripe is not connected yet, so they cannot pay until an admin adds the Stripe key on the Integrations page."
    return f"Offered {p.executive_name} one payment of {datasets.fmt_money(price)} {CURRENCY}{was}. Link: {url}{note}"


def _signer_name(call: Call) -> str:
    """The signed-in user's display name, or the local part of their email."""
    from bizos.control import store as control
    from bizos.control.store import UserNotFound

    if not call.user_id:
        return ""
    try:
        user = control.get_user(call.user_id)
    except UserNotFound:
        return ""
    name = (user.display_name or "").strip()
    if name:
        return name
    email = (user.email or "").strip()
    return email.split("@", 1)[0] if email else ""


def _payment_email(p: Prospect, link: CampaignProspect, signer_name: str) -> tuple[str, str]:
    """The payment email as a template: ``{{firstName}}`` is filled in per person, the link is typed in."""
    price = datasets.fmt_money(link.checkout_offer_amount)
    sign = f"Best,\n{signer_name}".rstrip()
    body = (f"Hi {{{{firstName}}}},\n\nThank you for your time. Here is the secure link for the one-time payment of "
            f"{price} {CURRENCY} we discussed:\n\n{build_checkout_url(link.assessment_token)}\n\n"
            f"If anything looks off, just reply to this email.\n\n{sign}")
    return "Your payment link", body.rstrip()


def payment_problem(db, email: str, body: str) -> str:
    """Why a payment email on the card cannot go out now ("" when it can). Checked again at send time."""
    p = db.scalar(select(Prospect).where(Prospect.email == email))
    link = records.link_row(db, p.id) if p is not None else None
    if link is None or not link.assessment_token:
        return "this person has no assessment link"
    if link.purchase_outcome == PurchaseOutcome.PURCHASED:
        return f"{p.executive_name} has already paid"
    if link.checkout_offered_at is None or link.checkout_offer_amount is None:
        return f"{p.executive_name} has no payment offer"
    if build_checkout_url(link.assessment_token) not in (body or ""):
        return "the payment link is missing from the email; it has to stay in the text"
    return ""


@tool("send_payment_link", "who", "approval_evidence")
def send_payment_link(call: Call, who: str = "", approval_evidence: str = "") -> str:
    """Put the payment-link email for the amount a person was offered on the email card. It is sent like any
    other email: the user approves it on the card (send_approved)."""
    p, link = _person_link(call, who)
    if link.purchase_outcome == PurchaseOutcome.PURCHASED:
        raise ToolError(f"{p.executive_name} has already paid.")
    if link.checkout_offered_at is None or link.checkout_offer_amount is None:
        raise ToolError(f"{p.executive_name} has no payment offer yet. Create the offer first (needs an amount).")
    current = call.state.get("draft")
    if current and not current.get("payment"):
        raise ToolError("another email draft is already on the card. Send or cancel it first, then ask for the payment link.")
    subject, body = _payment_email(p, link, _signer_name(call))
    call.state["recipients"] = [{"email": p.email, "name": p.executive_name}]
    call.state["draft"] = {
        "id": uuid.uuid4().hex[:10], "email_subject": subject, "email_body": body, "email_template": True,
        "email_draft": "", "same_for_all": False, "new_thread": True, "payment": True,
    }
    from app.outreach import card

    card.refresh_preview(records.people_records(call.db, [p.email]), call.state)
    call.state["_show_card"] = True  # the chat turn shows the card (app/outreach/effects.py)
    return ("ok: the payment email is shown to the user as a card. Ask one short question: send it? "
            "On their yes, call send_approved.")
