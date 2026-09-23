"""
Email Tool Effects
==================

Drafting and sending are two different tools with two different permission rows
and two different risk levels (§10: "Sending should be separately permissioned
from drafting"). ``email_create_draft`` writes to ``email_drafts``;
``email_send_message`` writes to ``email_messages``. A draft that was never
approved leaves no trace in the sent table, which is exactly what the DRAFT-mode
test asserts.
"""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect, risk_inputs


def _email(scope: RunScope):
    return get_connector("email", ctx=scope.ctx)


def _recipients(args: dict[str, Any]) -> list[str]:
    raw = args.get("recipients") or args.get("to") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


@effect("email_search_threads")
def email_search_threads(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).search_threads(args.get("query", ""), limit=int(args.get("limit", 20)))


@effect("email_read_thread")
def email_read_thread(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).read_thread(str(args.get("thread_id", "")))


@effect("email_create_draft")
def email_create_draft(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).create_draft(
        recipients=_recipients(args),
        subject=str(args.get("subject", "")),
        body=str(args.get("body", "")),
        cc=list(args.get("cc") or []),
        thread_id=args.get("thread_id"),
        action_id=args.get("action_id"),
    )


@risk_inputs("email_create_draft")
def _draft_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"recipients": tuple(_recipients(args))}


@effect("email_send_message")
def email_send_message(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).send_message(
        recipients=_recipients(args),
        subject=str(args.get("subject", "")),
        body=str(args.get("body", "")),
        cc=list(args.get("cc") or []),
        thread_id=args.get("thread_id"),
        draft_id=args.get("draft_id"),
    )


@risk_inputs("email_send_message")
def _send_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Recipients drive the domain allowlist; the count drives the record limit."""
    recipients = _recipients(args)
    return {"recipients": tuple(recipients), "record_count": max(1, len(recipients))}
