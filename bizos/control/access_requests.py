"""
Access Requests
===============

A person asks an admin for extra data tags. This store only records the request and its outcome: the
tags themselves are granted by :func:`bizos.admin_ops.decide_access_request`, so a request can never
widen anyone's access on its own. Every read and write filters on ``client_id``.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import text

from bizos.control.db import control_connection
from bizos.util.ids import new_id

PENDING = "PENDING"
GRANTED = "GRANTED"
DECLINED = "DECLINED"

_SELECT = (
    "SELECT r.id, r.user_id, r.areas, r.reason, r.status, r.decided_by, r.decided_at, r.created_at, "
    "u.email, u.display_name FROM access_requests r JOIN users u ON u.id = r.user_id "
)


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "email": row.email,
        "display_name": row.display_name,
        "areas": list(row.areas),
        "reason": row.reason,
        "status": row.status,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get(client_id: str, request_id: str) -> Optional[dict[str, Any]]:
    with control_connection() as conn:
        row = conn.execute(
            text(_SELECT + "WHERE r.client_id = :c AND r.id = :i"), {"c": client_id, "i": request_id}
        ).first()
    return _row(row) if row else None


def create(client_id: str, user_id: str, areas: Sequence[str], reason: str = "") -> dict[str, Any]:
    request_id = new_id("acr")
    with control_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO access_requests (id, client_id, user_id, areas, reason) "
                "VALUES (:i, :c, :u, :a, :r)"
            ),
            {"i": request_id, "c": client_id, "u": user_id, "a": list(areas), "r": reason},
        )
    return get(client_id, request_id)  # type: ignore[return-value]


def pending(client_id: str, *, user_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Requests waiting for an admin, newest first, for one person or the whole client."""
    clause, params = "", {"c": client_id, "s": PENDING}
    if user_id:
        clause, params = " AND r.user_id = :u", {**params, "u": user_id}
    with control_connection() as conn:
        rows = conn.execute(
            text(_SELECT + f"WHERE r.client_id = :c AND r.status = :s{clause} ORDER BY r.created_at DESC"),
            params,
        ).fetchall()
    return [_row(r) for r in rows]


def decide(client_id: str, request_id: str, *, granted: bool, decided_by: str) -> Optional[dict[str, Any]]:
    """Record the outcome, or return ``None`` if the request is no longer pending."""
    with control_connection() as conn:
        updated = conn.execute(
            text(
                "UPDATE access_requests SET status = :s, decided_by = :b, decided_at = NOW() "
                "WHERE client_id = :c AND id = :i AND status = :p"
            ),
            {"s": GRANTED if granted else DECLINED, "b": decided_by, "c": client_id, "i": request_id, "p": PENDING},
        )
    return get(client_id, request_id) if updated.rowcount else None
