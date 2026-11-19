"""
Support Ticket Emails
=====================

When a new support ticket comes in, the person who owns the team it was routed to gets an email with a
link that opens the ticket in FreedomBot (``/chat?ticket=<id>``):

* Cepoch (technical and triage) → ``TEAM_EMAILS["CEPOCH"]``.
* Jeanne (business) → ``TEAM_EMAILS["JEANNE"]``, only while ``SUPPORT_EMAIL_JEANNE`` is on.

``SUPPORT_TICKET_EMAILS=off`` stops every ticket email. Sending happens on a background thread and never
fails or slows down the ticket. Without SMTP settings nothing is sent.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from html import escape
from typing import Any, Optional
from urllib.parse import quote

from freedombot import settings as app_settings

log = logging.getLogger(__name__)


def ticket_link(ticket_id: str) -> str:
    return f"{app_settings.app_public_url()}/chat?ticket={quote(ticket_id, safe='')}"


def recipient(team: str) -> Optional[str]:
    """Who gets the new-ticket email for ``team``, or ``None`` when that team's email is off."""
    from freedombot.support import ASSIGNEE_JEANNE, TEAM_EMAILS

    if not app_settings.support_ticket_emails():
        return None
    if team == ASSIGNEE_JEANNE and not app_settings.support_email_jeanne():
        return None
    return TEAM_EMAILS.get(team)


def compose(ticket: dict[str, Any], to: str) -> EmailMessage:
    from freedombot.support import TEAM_LABELS

    link = ticket_link(ticket["id"])
    team = TEAM_LABELS.get(ticket.get("assignee", ""), ticket.get("assignee", ""))
    kind = "triage ticket" if ticket.get("triage") else "ticket"
    category = str(ticket.get("category") or "").title()
    body = (ticket.get("body") or "").strip()
    rows = [
        ("Ticket", ticket["id"]),
        ("Team", team + (" (triage)" if ticket.get("triage") else "")),
        ("Category", category),
        ("Severity", str(ticket.get("severity") or "")),
        ("Raised by", str(ticket.get("created_by") or "")),
    ]

    msg = EmailMessage()
    msg["Subject"] = f"[FreedomBot] New {kind}: {ticket.get('subject', '')}"[:250]
    msg["To"] = to
    text_rows = "\n".join(f"{k}: {v}" for k, v in rows if v)
    msg.set_content(
        f"A new support {kind} needs your attention.\n\n{ticket.get('subject', '')}\n\n{text_rows}\n\n"
        + (f"{body[:2000]}\n\n" if body else "")
        + f"Open the ticket: {link}\n"
    )
    html_rows = "".join(
        f"<tr><td style='padding:2px 12px 2px 0;color:#666'>{escape(k)}</td><td>{escape(v)}</td></tr>"
        for k, v in rows if v
    )
    html_body = f"<p style='white-space:pre-wrap'>{escape(body[:2000])}</p>" if body else ""
    msg.add_alternative(
        f"""<div style="font-family:Arial,sans-serif;font-size:14px;color:#222">
<p>A new support {escape(kind)} needs your attention.</p>
<h3 style="margin:12px 0">{escape(ticket.get('subject', ''))}</h3>
<table style="font-size:13px">{html_rows}</table>
{html_body}
<p><a href="{escape(link)}" style="display:inline-block;background:#5b21b6;color:#fff;padding:10px 16px;
border-radius:8px;text-decoration:none">Open ticket in FreedomBot</a></p>
<p style="font-size:12px;color:#888">{escape(link)}</p>
</div>""",
        subtype="html",
    )
    return msg


def send(msg: EmailMessage) -> None:
    """Send through the configured SMTP server. Replaceable in tests, which never send real email."""
    config = app_settings.smtp_config()
    if config is None:
        log.info("support email skipped: SMTP_USER / SMTP_PASSWORD not set")
        return
    msg["From"] = str(config["sender"])
    host, port = str(config["host"]), int(config["port"])  # type: ignore[arg-type]
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as server:
            server.login(str(config["user"]), str(config["password"]))
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=context)
            server.login(str(config["user"]), str(config["password"]))
            server.send_message(msg)


def _deliver(msg: EmailMessage, ticket_id: str) -> None:
    try:
        send(msg)
        log.info("support email sent for %s to %s", ticket_id, msg["To"])
    except Exception:  # noqa: BLE001 - an email failure never affects the ticket
        log.exception("support email failed for %s", ticket_id)


def notify_new_ticket(ticket: dict[str, Any], team: str, *, background: bool = True) -> Optional[str]:
    """Email the owner of ``team`` about a new ticket. Returns the address it went to, or ``None``."""
    to = recipient(team)
    if not to:
        return None
    try:
        msg = compose(ticket, to)
    except Exception:  # noqa: BLE001
        log.exception("support email could not be built for %s", ticket.get("id"))
        return None
    if background:
        threading.Thread(target=_deliver, args=(msg, ticket["id"]), daemon=True).start()
    else:
        _deliver(msg, ticket["id"])
    return to
