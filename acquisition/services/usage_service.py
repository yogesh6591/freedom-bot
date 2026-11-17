"""Numbers for the Finder usage box on the admin Integrations page. Nothing here spends credits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.models.prospect import Prospect

EXA_CREDITS_URL = "https://admin-api.exa.ai/team-management/credits"
PROSPEO_KEYS = ("current_plan", "remaining_credits", "used_credits", "next_quota_renewal_date", "next_quota_renewal_days")


def _find(data: Any, key: str) -> Any:
    """First value stored under ``key`` anywhere inside nested dicts and lists."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            hit = _find(value, key)
            if hit is not None:
                return hit
    elif isinstance(data, list):
        for value in data:
            hit = _find(value, key)
            if hit is not None:
                return hit
    return None


def _flat(data: Any, prefix: str = "", out: Optional[list] = None) -> list[tuple[str, str]]:
    out = [] if out is None else out
    if isinstance(data, dict):
        for key, value in data.items():
            _flat(value, f"{prefix}{key}." if isinstance(value, (dict, list)) else f"{prefix}{key}", out)
    elif isinstance(data, list):
        for number, value in enumerate(data[:10]):
            _flat(value, f"{prefix}{number}.", out)
    elif prefix:
        out.append((prefix.rstrip("."), str(data)[:80]))
    return out[:20]


def user_id_from_request(request: Any) -> Optional[str]:
    """The signed-in workspace user, read from the same signed cookie the admin pages already trust."""
    token = (request.cookies.get("freedombot_session") or "").strip()
    if not token:
        return None
    try:
        from freedombot.control.tokens import verify_token

        return str(verify_token(token).get("sub") or "") or None
    except Exception:  # noqa: BLE001
        return None


def prospeo_usage(user_id: Optional[str]) -> dict:
    if not user_id:
        return {"error": "Sign in through the chat app to see the Prospeo balance."}
    try:
        from freedombot.connectors import composio
        from freedombot.control import store as control

        ctx = control.build_context(control.get_user(user_id))
        if not composio._active_accounts(ctx).get("prospeo"):
            return {"error": "Prospeo is not connected for your account. Connect it in the chat app, Integrations."}
        data = composio.execute(ctx, "prospeo", "PROSPEO_GET_ACCOUNT_INFORMATION", {})
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not read Prospeo ({type(exc).__name__})."}
    rows = [(key.replace("_", " "), str(_find(data, key))) for key in PROSPEO_KEYS if _find(data, key) is not None]
    return {"rows": rows} if rows else {"error": "Prospeo answered, but without the account numbers."}


def exa_usage() -> dict:
    key = get_settings().exa_admin_api_key
    if not key:
        return {"note": "Add EXA_ADMIN_API_KEY (an Exa Team Management service key) to the server .env to see the balance here."}
    try:
        reply = httpx.get(EXA_CREDITS_URL, headers={"x-api-key": key}, timeout=15)
        reply.raise_for_status()
        rows = _flat(reply.json())
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not read Exa ({type(exc).__name__})."}
    return {"rows": rows} if rows else {"error": "Exa answered, but without balance numbers."}


def finder_counts(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    out = {}
    for label, days in (("Last 24 hours", 1), ("Last 30 days", 30)):
        out[label] = db.scalar(select(func.count()).select_from(Prospect).where(
            Prospect.source == "Exa + Prospeo", Prospect.created_at >= now - timedelta(days=days))) or 0
    return out
