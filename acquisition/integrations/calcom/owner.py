"""Whose Cal.com account the platform uses.

Cal.com is connected through Composio, like Gmail: a platform user connects it on the Integrations page
(no API key is typed into this app). The calendar owner is the user with an ACTIVE ``cal`` connection;
if several have one, the outreach sender is preferred, otherwise the first found.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

TOOLKIT = "cal"
_TTL_SECONDS = 30.0

_lock = threading.Lock()
_cache: tuple[float, str | None] | None = None


def _pick_user_id() -> str | None:
    """The platform user id that owns the calendar, or None when nobody connected Cal.com."""
    from app.outreach import gmail_users

    options = gmail_users.list_connected(TOOLKIT)
    if not options:
        return None
    try:
        from acquisition.db.session import SessionLocal
        from acquisition.services.outreach_settings import effective

        db = SessionLocal()
        try:
            preferred = effective(db).sender_user_id
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - the preference is optional
        preferred = None
    if preferred and any(o["user_id"] == preferred for o in options):
        return preferred
    return options[0]["user_id"]


def owner_user_id() -> str | None:
    """Cached for a few seconds, so one page load does not list Composio accounts many times."""
    global _cache
    with _lock:
        if _cache and time.monotonic() - _cache[0] < _TTL_SECONDS:
            return _cache[1]
    user_id = _pick_user_id()
    with _lock:
        _cache = (time.monotonic(), user_id)
    return user_id


def forget() -> None:
    """Drop the cached owner (after connecting or disconnecting Cal.com)."""
    global _cache
    with _lock:
        _cache = None


def owner_context() -> Any | None:
    """TenantContext of the calendar owner, or None when Cal.com is not connected."""
    user_id = owner_user_id()
    if not user_id:
        return None
    from freedombot.control import store as control

    user = control.get_user(user_id)
    return control.build_context(user) if user is not None else None
