"""Background sender: delivers queued outreach emails through the Gmail (Composio) of the user who asked for them.

* Whose Gmail: each email goes out from the Gmail of the person who requested it, so one account never sends for
  another. A user who has not connected Gmail cannot queue email (the chat says so first), and an email that is
  queued for such a user stays queued and is never sent from someone else's mailbox. The admin Sending card, which
  has no requesting user, still checks the old way (an explicit sender saved in ``outreach_settings`` or
  OUTREACH_SENDER_USER_ID, otherwise the first connected Gmail).
* The on/off switch comes from the Sending card on the Integrations page
  (table ``outreach_settings``). The OUTREACH_* env values are only a fallback.
* One email at a time, spaced by OUTREACH_GAP_SECONDS, never more than OUTREACH_DAILY_CEILING a day
  (rules live in acquisition/services/outreach_service.py).
* Safety: mail only goes out through a real connected Gmail. The workspace's in-app mailbox is refused
  unless OUTREACH_ALLOW_WORKSPACE_MAILBOX=true (development only).
* The loop always runs but sends nothing until Sending is switched on, so the switch needs no restart.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from acquisition.config import get_settings
from acquisition.services import outreach_service as svc

log = logging.getLogger(__name__)


def _effective():
    from acquisition.db.session import SessionLocal
    from acquisition.services.outreach_settings import effective

    db = SessionLocal()
    try:
        return effective(db)
    finally:
        db.close()


def resolve_sender() -> dict:
    """The user whose Gmail sends: the explicit choice if there is one, else the connected Gmail."""
    explicit = _effective().sender_user_id
    from app.outreach import gmail_users

    options = gmail_users.list_connected()
    if explicit:
        match = next((o for o in options if o["user_id"] == explicit), None)
        return match or {"user_id": explicit, "gmail": "", "name": "", "email": "", "workspace": ""}
    if not options:
        raise RuntimeError("no Gmail is connected. Connect Gmail on the Integrations page first")
    return options[0]


def _sender_context() -> Any:
    """TenantContext of the user whose Gmail sends outreach."""
    user_id = resolve_sender()["user_id"]
    from freedombot.control import store as control

    return control.build_context(control.get_user(user_id))


def _bind(ctx: Any):
    from freedombot.tenancy.context import tenant_scope

    return tenant_scope(ctx)


def _gmail_connector(ctx: Any):
    from freedombot.connectors.composio import ComposioGmailConnector
    from freedombot.connectors.registry import get_connector

    connector = get_connector("email", ctx=ctx, owner_user_id=ctx.user_id)
    if isinstance(connector, ComposioGmailConnector) or get_settings().outreach_allow_workspace_mailbox:
        return connector
    raise RuntimeError("the sender has no connected Gmail (Composio). Connect Gmail first")


def _connected_ids() -> set[str]:
    from app.outreach import gmail_users

    return {o["user_id"] for o in gmail_users.list_connected()}


def readiness(user_id: Optional[str] = None) -> tuple[bool, str]:
    """(ok, reason). Checked before anything is queued, so the user hears it immediately.

    With ``user_id`` it is that user's own Gmail that must be connected. Without one (the admin Sending card) the
    old check applies: the explicit sender, or the first connected Gmail."""
    try:
        if user_id:
            if user_id not in _connected_ids():
                raise RuntimeError("your Gmail is not connected. Connect Gmail on the Integrations page first")
            from freedombot.control import store as control

            ctx = control.build_context(control.get_user(user_id))
        else:
            ctx = _sender_context()
        with _bind(ctx):
            _gmail_connector(ctx)
    except Exception as exc:  # noqa: BLE001
        return False, f"email cannot be sent yet: {exc}"
    return True, ""


def _send_fn(ctx: Any) -> svc.SendFn:
    connector = _gmail_connector(ctx)

    def send(to: str, subject: str, body: str, thread_id: Optional[str]) -> svc.SendResult:
        res = connector.send_message(recipients=[to], subject=subject, body=body, thread_id=thread_id)
        if not res.ok:
            return svc.SendResult(ok=False, error=res.error or res.summary)
        data = res.data or {}
        return svc.SendResult(ok=True, message_id=data.get("message_id"), thread_id=data.get("thread_id"))

    return send


def _send_for(user_id: str) -> bool:
    """Send at most one email queued by ``user_id``, through that user's own Gmail."""
    from acquisition.db.session import SessionLocal
    from freedombot.control import store as control

    ctx = control.build_context(control.get_user(user_id))
    with _bind(ctx):
        send = _send_fn(ctx)
        db = SessionLocal()
        try:
            row = svc.send_next(db, send, requested_by=user_id)
            db.commit()
            return row is not None
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def tick() -> bool:
    """Send at most one queued email. True when one was handled (so the loop can continue promptly)."""
    from acquisition.db.session import SessionLocal

    if not _effective().worker_enabled:
        return False
    db = SessionLocal()
    try:
        waiting = svc.queued_requesters(db)
    finally:
        db.close()
    if not waiting:
        return False  # nothing queued: no need to ask Composio who is connected
    for user_id in sorted(waiting & _connected_ids()):
        try:
            if _send_for(user_id):
                return True
        except Exception as exc:  # noqa: BLE001 - one user's failure must not block the others
            log.warning("outreach: send for %s failed: %s", user_id, exc)
    return False


def start() -> Optional[Callable[[], None]]:
    """Start the sender loop in a daemon thread; returns a stop function. Call from the app lifespan."""
    cfg = get_settings()
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                from app.outreach import followups

                followups.run_if_due()  # queue the automatic follow-ups that are due; this loop then sends them
            except Exception as exc:  # noqa: BLE001 - never stop the sender loop
                log.warning("outreach: automatic follow-up check failed: %s", exc)
            try:
                busy = tick()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.warning("outreach: send tick failed: %s", exc)
                busy = False
            stop.wait(1.0 if busy else float(cfg.outreach_poll_seconds))

    thread = threading.Thread(target=loop, name="outreach-sender", daemon=True)
    thread.start()
    log.info("outreach: sender loop started (sends only while Sending is switched on)")
    return stop.set
