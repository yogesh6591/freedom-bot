"""Who has a connected (ACTIVE) Composio app: Gmail decides who can send, Cal.com who owns the calendar."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def _gmail_address(item: Any) -> str:
    data = getattr(item, "data", None) or {}
    if isinstance(data, dict):
        return str(data.get("displayName") or data.get("email") or "").strip()
    return ""


def list_connected(toolkit: str = "gmail") -> list[dict[str, str]]:
    """[{user_id, name, email, workspace, gmail}] for every platform user with an ACTIVE ``toolkit`` account.

    One Composio call lists all ACTIVE accounts of the toolkit; each is matched to a platform user through the
    Composio user id ``fb:{client_id}:{user_id}``. Accounts that belong to nobody on this platform are skipped,
    so only a user that can actually use the app is offered. (``gmail`` holds the account's address.)
    """
    from bizos.connectors import composio
    from bizos.control import store as control

    out: list[dict[str, str]] = []
    try:
        listing = composio.get_client().connected_accounts.list(toolkit_slugs=[toolkit], statuses=["ACTIVE"])
        items = getattr(listing, "items", None) or []
    except Exception as exc:  # noqa: BLE001 - the page must still load
        log.warning("outreach: could not list %s accounts: %s", toolkit, exc)
        return out

    seen: set[str] = set()
    for item in items:
        raw = str(getattr(item, "user_id", "") or "")
        parts = raw.split(":")
        if len(parts) != 3 or parts[0] != "fb":
            continue
        _, client_id, user_id = parts
        if user_id in seen:
            continue
        try:
            user = control.get_user(user_id)
        except Exception:  # noqa: BLE001 - not a user of this platform
            continue
        if user is None or getattr(user, "client_id", client_id) != client_id:
            continue
        seen.add(user_id)
        out.append(
            {
                "user_id": user_id,
                "name": str(getattr(user, "display_name", "") or ""),
                "email": str(getattr(user, "email", "") or ""),
                "workspace": client_id,
                "gmail": _gmail_address(item),
            }
        )
    return out
