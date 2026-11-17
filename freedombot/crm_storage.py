"""
CRM Storage Choice
==================

Where each person's CRM records (contacts, companies, deals, notes and tasks) are kept:

* ``freedombot``: FreedomBot's own database, the workspace CRM tables (freedombot/connectors/mock_crm.py).
  Always available, and where records go when nothing else is connected.
* ``hubspot``: the person's connected HubSpot (freedombot/connectors/composio.py).
* ``google_drive``: a "FreedomBot CRM" Google Sheet in the person's Drive (freedombot/connectors/drive_crm.py).

The place is picked in this order:

1. The place the request names ("save it in HubSpot"). It must be connected, or nothing is saved.
2. The person's saved choice ("from now on keep my CRM in Google Drive"), while that app stays connected.
   If it was disconnected, records go to FreedomBot's database and the result says so.
3. Automatic (no choice saved): HubSpot when the person connected it, otherwise FreedomBot's database.

This module only picks the place. Permission to write a CRM record is decided by the policy check
(freedombot/tools/base.py guarded_call), which is the same whichever place is picked.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from sqlalchemy import text

from freedombot.connectors.base import Connector, ConnectorNotConfigured
from freedombot.tenancy.context import TenantContext
from freedombot.tenancy.registry import readonly_connection, workspace_connection, workspace_location

AUTO = "auto"
FREEDOMBOT = "freedombot"
HUBSPOT = "hubspot"
GOOGLE_DRIVE = "google_drive"

#: place -> name people see.
NAMES = {FREEDOMBOT: "FreedomBot", HUBSPOT: "HubSpot", GOOGLE_DRIVE: "Google Drive"}
#: Every value a choice may hold.
CHOICES = (AUTO, FREEDOMBOT, HUBSPOT, GOOGLE_DRIVE)
#: The Composio app each outside place needs connected.
APP_FOR = {HUBSPOT: "hubspot", GOOGLE_DRIVE: "googlesheets"}

_ALIASES = {
    "auto": AUTO, "automatic": AUTO, "default": AUTO,
    "freedombot": FREEDOMBOT, "freedom bot": FREEDOMBOT, "database": FREEDOMBOT, "db": FREEDOMBOT,
    "workspace": FREEDOMBOT, "own": FREEDOMBOT, "internal": FREEDOMBOT, "built-in": FREEDOMBOT, "builtin": FREEDOMBOT,
    "hubspot": HUBSPOT, "hub spot": HUBSPOT,
    "google_drive": GOOGLE_DRIVE, "google drive": GOOGLE_DRIVE, "drive": GOOGLE_DRIVE, "gdrive": GOOGLE_DRIVE,
    "google sheets": GOOGLE_DRIVE, "google_sheets": GOOGLE_DRIVE, "sheets": GOOGLE_DRIVE, "sheet": GOOGLE_DRIVE,
}

_lock = threading.Lock()
_ready: set[tuple[str, str]] = set()


class StorageUnavailable(ConnectorNotConfigured):
    """The place asked for is not connected for this person."""


def parse(value: Any) -> Optional[str]:
    """A place from what a person or the model wrote ("Drive", "hubspot", "database"), or None if unknown."""
    key = " ".join(str(value or "").replace("-", " ").split()).casefold()
    return _ALIASES.get(key) or _ALIASES.get(key.replace(" ", "_"))


def label(place: str) -> str:
    return NAMES.get(place, "Automatic")


# --------------------------------------------------------------------------- saved choice


def _ensure_table(ctx: TenantContext) -> None:
    from freedombot.tenancy.schema import client_wall_ddl

    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS crm_storage_choice (
                    owner_user_id TEXT PRIMARY KEY,
                    destination   TEXT NOT NULL DEFAULT 'auto',
                    sheet_id      TEXT,
                    sheet_url     TEXT,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ))
            for statement in client_wall_ddl(ctx.client_id, ("crm_storage_choice",)):
                conn.execute(text(statement))
        _ready.add(key)


def _row(ctx: TenantContext, user_id: str) -> Optional[Any]:
    _ensure_table(ctx)
    with readonly_connection(ctx) as conn:
        return conn.execute(
            text("SELECT destination, sheet_id, sheet_url FROM crm_storage_choice WHERE owner_user_id = :o"),
            {"o": user_id},
        ).first()


def saved(ctx: TenantContext, user_id: str) -> str:
    """This person's saved choice: one of CHOICES, ``auto`` when they never chose."""
    row = _row(ctx, user_id)
    return row.destination if row is not None and row.destination in CHOICES else AUTO


def save(ctx: TenantContext, user_id: str, place: str) -> None:
    if place not in CHOICES:
        raise ValueError(f"Unknown CRM storage {place!r}")
    _ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO crm_storage_choice (owner_user_id, destination) VALUES (:o, :d) "
                "ON CONFLICT (owner_user_id) DO UPDATE SET destination = EXCLUDED.destination, updated_at = NOW()"
            ),
            {"o": user_id, "d": place},
        )


def sheet(ctx: TenantContext, user_id: str) -> tuple[Optional[str], Optional[str]]:
    """The id and link of this person's "FreedomBot CRM" sheet in Drive, once it exists."""
    row = _row(ctx, user_id)
    return (row.sheet_id, row.sheet_url) if row is not None else (None, None)


def remember_sheet(ctx: TenantContext, user_id: str, sheet_id: str, url: Optional[str]) -> None:
    _ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO crm_storage_choice (owner_user_id, sheet_id, sheet_url) VALUES (:o, :s, :u) "
                "ON CONFLICT (owner_user_id) DO UPDATE SET sheet_id = EXCLUDED.sheet_id, "
                "sheet_url = EXCLUDED.sheet_url, updated_at = NOW()"
            ),
            {"o": user_id, "s": sheet_id, "u": url},
        )


# --------------------------------------------------------------------------- picking the place


def connected(ctx: TenantContext, user_id: str) -> dict[str, bool]:
    """Which places this person can use right now."""
    from freedombot.connectors import composio

    apps: dict[str, Optional[str]] = {}
    if composio.configured() and user_id:
        apps = composio._active_accounts(composio.as_user(ctx, user_id))
    return {FREEDOMBOT: True, **{place: bool(apps.get(app)) for place, app in APP_FOR.items()}}


def _missing(place: str) -> str:
    app = "HubSpot" if place == HUBSPOT else "Google Sheets (for Google Drive)"
    return (f"{label(place)} is not connected for you, so nothing was saved there. Connect {app} on the "
            "Integrations page, or ask to save it in FreedomBot instead.")


def resolve(ctx: TenantContext, user_id: str, requested: Any = None) -> tuple[str, str]:
    """(place, note) for this person's next CRM call. ``note`` explains a fallback; it is empty otherwise.

    Raises StorageUnavailable when ``requested`` names a place that is not connected, and ValueError when
    it names no known place.
    """
    available = connected(ctx, user_id)
    if requested not in (None, ""):
        place = parse(requested)
        if place is None:
            raise ValueError(f"save_to must be FreedomBot, HubSpot or Google Drive (got {requested!r}).")
        if place != AUTO:
            if not available.get(place):
                raise StorageUnavailable(_missing(place))
            return place, ""
    choice = saved(ctx, user_id)
    if choice in APP_FOR:
        if available.get(choice):
            return choice, ""
        return FREEDOMBOT, (f"Your saved choice is {label(choice)}, but it is not connected now, so this used "
                            "FreedomBot's own CRM.")
    if choice == FREEDOMBOT:
        return FREEDOMBOT, ""
    return (HUBSPOT if available.get(HUBSPOT) else FREEDOMBOT), ""


def connector(ctx: TenantContext, owner_user_id: Optional[str] = None, *, requested: Any = None) -> Connector:
    """The CRM connector for the place this person's records go, labelled with that place.

    ``owner_user_id`` is the person the call is for when it is not the caller (an approver running
    someone else's request): their choice and their connected apps are used, never the approver's.
    """
    from freedombot.connectors import composio
    from freedombot.connectors.registry import workspace_connector

    owner = owner_user_id or ctx.user_id
    place, note = resolve(ctx, owner, requested)
    if place == HUBSPOT:
        adapter: Connector = composio.CONNECTORS["hubspot"](ctx, {"owner_user_id": owner})
    elif place == GOOGLE_DRIVE:
        from freedombot.connectors.drive_crm import DriveSheetCrmConnector

        adapter = DriveSheetCrmConnector(ctx, {"owner_user_id": owner})
    else:
        adapter = workspace_connector("crm", ctx=ctx, owner_user_id=owner_user_id)
    adapter.storage = place  # type: ignore[attr-defined]
    adapter.storage_note = note  # type: ignore[attr-defined]
    return adapter


def describe(ctx: TenantContext, user_id: str) -> dict[str, Any]:
    """The saved choice, where records go now, and which places are connected (for the UI and the tool)."""
    available = connected(ctx, user_id)
    choice = saved(ctx, user_id)
    place, note = resolve(ctx, user_id)
    sheet_id, sheet_url = sheet(ctx, user_id)
    return {
        "choice": choice,
        "choice_name": label(choice),
        "saving_to": place,
        "saving_to_name": label(place),
        "note": note,
        "available": {p: available.get(p, False) for p in NAMES},
        "sheet_url": sheet_url if sheet_id else None,
    }
