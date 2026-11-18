"""Integrations page: the Sending card (which Gmail sends, on/off)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from acquisition.auth.admin import require_admin_html_request
from acquisition.db.session import get_db
from acquisition.services import outreach_settings as store

router = APIRouter(tags=["admin-ui-sending"], dependencies=[Depends(require_admin_html_request)])


def sending_context(db: Session) -> dict:
    """Everything the Sending card shows."""
    from acquisition.config import get_settings

    eff = store.effective(db)
    sender_gmail = ""
    try:
        from app.outreach import sender

        ok, reason = sender.readiness()
        try:
            sender_gmail = sender.resolve_sender().get("gmail", "")
        except Exception:  # noqa: BLE001 - nothing connected yet
            sender_gmail = ""
    except Exception as exc:  # noqa: BLE001 - the page must still render
        ok, reason = False, f"email status could not be checked: {exc}"
    cfg = get_settings()
    try:
        from app.outreach import followups

        followup_summary = followups.describe(db, eff)
    except Exception as exc:  # noqa: BLE001 - the page must still render
        followup_summary = f"follow-up status could not be checked: {exc}"
    return {
        "ready": ok,
        "reason": reason,
        "worker_on": eff.worker_enabled,
        "auto_send_on": eff.auto_send_enabled,
        "first_subject": eff.first_subject,
        "first_body": eff.first_body,
        "stages": [{"key": k, "label": store.STAGE_LABELS[k], "body": eff.stage_bodies[k]} for k in store.STAGE_KEYS],
        "sender_gmail": sender_gmail,
        "followup_on": eff.followup_enabled,
        "followup_days": eff.followup_days,
        "followup_hour": eff.followup_hour,
        "followup_max": eff.followup_max,
        "followup_summary": followup_summary,
        "daily_ceiling": cfg.outreach_daily_ceiling,
        "gap_seconds": cfg.outreach_gap_seconds,
    }


@router.post("/integrations/sending", response_model=None)
def save_sending(
    worker_enabled: Annotated[str | None, Form()] = None,
    auto_send_enabled: Annotated[str | None, Form()] = None,
    first_subject: Annotated[str, Form()] = "",
    first_body: Annotated[str, Form()] = "",
    followup_body: Annotated[str, Form()] = "",
    stage_not_started: Annotated[str, Form()] = "",
    stage_in_progress: Annotated[str, Form()] = "",
    stage_qualified: Annotated[str, Form()] = "",
    stage_unqualified: Annotated[str, Form()] = "",
    stage_proof: Annotated[str, Form()] = "",
    stage_demo: Annotated[str, Form()] = "",
    stage_value: Annotated[str, Form()] = "",
    stage_calendar: Annotated[str, Form()] = "",
    stage_payment_pending: Annotated[str, Form()] = "",
    followup_enabled: Annotated[str | None, Form()] = None,
    followup_days: Annotated[str | None, Form()] = None,
    followup_hour: Annotated[str | None, Form()] = None,
    followup_max: Annotated[str | None, Form()] = None,
    db: Session = Depends(get_db),
):
    want_on = worker_enabled is not None
    want_auto = auto_send_enabled is not None
    current = store.effective(db)
    keep_sender = current.sender_user_id  # an explicit sender, if one was ever saved
    store.save(
        db,
        sender_user_id=keep_sender,
        business_address=current.business_address,
        optout_line=current.optout_line,
        worker_enabled=False,  # switched on below only when everything is ready
        updated_by="admin",
        auto_send_enabled=False,  # switched on below only when the first-email template is filled in
        first_subject=first_subject,
        first_body=first_body,
        stage_bodies={"not_started": stage_not_started, "in_progress": stage_in_progress,
                      "qualified": stage_qualified, "unqualified": stage_unqualified, "proof": stage_proof,
                      "demo": stage_demo, "value": stage_value, "calendar": stage_calendar,
                      "payment_pending": stage_payment_pending},
    )
    flash = "sending_saved"
    if want_auto:
        saved = store.effective(db)
        if saved.template_ready:
            store.save(
                db,
                sender_user_id=keep_sender,
                business_address=current.business_address,
                optout_line=current.optout_line,
                worker_enabled=False,
                updated_by="admin",
                auto_send_enabled=True,
            )
            flash = "template_on"
        else:
            flash = "template_missing"
    if want_on:
        from app.outreach import sender

        ok, _reason = sender.readiness()
        if ok:
            store.save(
                db,
                sender_user_id=keep_sender,
                business_address=current.business_address,
                optout_line=current.optout_line,
                worker_enabled=True,
                updated_by="admin",
            )
            flash = "sending_on" if flash != "template_on" else "template_on"
        else:
            flash = "sending_not_ready"
    if followup_days is not None:  # the form carries the follow-up fields; an old cached page does not
        try:
            store.save_followup(db, enabled=followup_enabled is not None, days=followup_days,
                                hour=followup_hour, max_followups=followup_max, updated_by="admin")
        except ValueError:
            flash = "followup_invalid"
    return RedirectResponse(url=f"/integrations?flash={flash}", status_code=status.HTTP_303_SEE_OTHER)
