"""TEMPORARY development-only Stripe TEST success/cancel routes.

Payment status is always verified by retrieving the Checkout Session
server-side with STRIPE_SECRET_KEY. Browser query params are never trusted
for amount, payment status, or prospect identity.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.db.session import get_db
from acquisition.services.stripe_test_service import StripeTestError, verify_and_apply_paid_session

router = APIRouter(tags=["stripe-test"])


def _simple_page(*, title: str, body: str, link_href: str | None = None, link_label: str | None = None) -> HTMLResponse:
    link_html = ""
    if link_href and link_label:
        link_html = f'<p><a href="{link_href}">{link_label}</a></p>'
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
  <h1>{title}</h1>
  <p>{body}</p>
  {link_html}
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/stripe-test/success", response_class=HTMLResponse, response_model=None)
def stripe_test_success(
    request: Request,
    session_id: str | None = None,
    db: Session = Depends(get_db),
):
    if not session_id or not str(session_id).strip():
        return _simple_page(
            title="Stripe test — missing session",
            body="No Checkout Session id was provided. Payment was not recorded.",
        )
    try:
        result = verify_and_apply_paid_session(db, str(session_id).strip())
    except StripeTestError as exc:
        return _simple_page(
            title="Stripe test — not applied",
            body=exc.message,
        )
    link_id = result.get("campaign_prospect_id")
    status_label = result.get("status") or "applied"
    flash = "stripe_test_paid" if status_label == "applied" else "stripe_test_already"
    if link_id:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash={flash}#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return _simple_page(
        title="Stripe test — payment verified",
        body="Payment verified and purchase outcome updated.",
    )


@router.get("/stripe-test/cancel", response_class=HTMLResponse, response_model=None)
def stripe_test_cancel(
    request: Request,
    link_id: uuid.UUID | None = None,
):
    """Cancelled Checkout does NOT set purchase_outcome = did_not_purchase."""
    if link_id is not None:
        return RedirectResponse(
            url=f"/assessments/{link_id}?flash=stripe_test_cancelled#booking",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return _simple_page(
        title="Stripe test — cancelled",
        body=(
            "Stripe test checkout was cancelled or not completed. "
            "Purchase outcome was left unchanged."
        ),
        link_href="/assessments",
        link_label="Back to Assessments",
    )
