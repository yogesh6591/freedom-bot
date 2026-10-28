"""
Composio Catalog and Tool Choices
=================================

Every platform Composio offers (about 1,700), searchable, with each platform's tools, and the per-user
choice of which tools of a connected platform the assistant may use ("all" by default).

* The catalog is loaded from Composio once and cached in the process (it changes rarely).
* A platform can be connected in one click when Composio manages its sign-in, or when this Composio
  project already has an auth config for it (an admin set it up with its own API key or OAuth app).
  Others are listed as needing admin setup, never offered as if they worked.
* Tool choices live in the workspace (``integration_tool_choices``, owner-only) and are enforced in
  :func:`bizos.connectors.composio.execute`, so they bind the dedicated tools and app actions alike.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Optional

from sqlalchemy import text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import readonly_connection, workspace_connection, workspace_location

CATALOG_TTL = 6 * 3600.0
CONFIGURED_TTL = 600.0
TOOLS_TTL = 3600.0
SLUG = re.compile(r"^[a-z0-9_]{2,60}$")

_lock = threading.Lock()
_catalog: tuple[float, list[dict[str, Any]]] = (0.0, [])
_configured: tuple[float, frozenset[str]] = (0.0, frozenset())
_tools: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ready: set[tuple[str, str]] = set()


# --------------------------------------------------------------------------- catalog


def _platform(item: Any) -> dict[str, Any]:
    meta = getattr(item, "meta", None)
    categories = [str(getattr(c, "name", "") or getattr(c, "id", "")) for c in (getattr(meta, "categories", None) or [])]
    return {
        "slug": str(item.slug),
        "name": str(getattr(item, "name", "") or item.slug),
        "description": " ".join(str(getattr(meta, "description", "") or "").split())[:240],
        "logo": str(getattr(meta, "logo", "") or ""),
        "categories": [c for c in categories if c],
        "tools_count": int(getattr(meta, "tools_count", 0) or 0),
        "managed": bool(getattr(item, "composio_managed_auth_schemes", None)) or bool(getattr(item, "no_auth", False)),
        "auth_schemes": [str(s) for s in (getattr(item, "auth_schemes", None) or [])],
    }


def catalog() -> list[dict[str, Any]]:
    """Every Composio platform, most used first (cached)."""
    global _catalog
    if time.monotonic() - _catalog[0] < CATALOG_TTL and _catalog[1]:
        return _catalog[1]
    from bizos.connectors.composio import get_client

    with _lock:
        if time.monotonic() - _catalog[0] < CATALOG_TTL and _catalog[1]:
            return _catalog[1]
        client, items, cursor = get_client(), [], None
        for _ in range(10):  # 1,000 per page; the whole catalog is about 1,700
            page = client.toolkits.list(limit=1000, cursor=cursor, sort_by="usage")
            items += [_platform(i) for i in page.items]
            cursor = page.next_cursor
            if not cursor or not page.items:
                break
        _catalog = (time.monotonic(), items)
        return items


def configured() -> frozenset[str]:
    """Platforms this Composio project has an auth config for (cached)."""
    global _configured
    if time.monotonic() - _configured[0] < CONFIGURED_TTL:
        return _configured[1]
    from bizos.connectors.composio import get_client

    found: set[str] = set()
    cursor = None
    for _ in range(20):
        page = get_client().auth_configs.list(limit=500, **({"cursor": cursor} if cursor else {}))
        found |= {str(getattr(getattr(i, "toolkit", None), "slug", "") or "") for i in page.items}
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    _configured = (time.monotonic(), frozenset(found - {""}))
    return _configured[1]


def platform(slug: str) -> Optional[dict[str, Any]]:
    return next((p for p in catalog() if p["slug"] == slug), None)


def connectable(item: dict[str, Any]) -> bool:
    return bool(item["managed"] or item["slug"] in configured())


def search(query: str = "", *, category: str = "", connected: frozenset[str] = frozenset(),
           connected_only: bool = False, offset: int = 0, limit: int = 60) -> dict[str, Any]:
    """Platforms matching ``query`` (name, slug, description) and ``category``, connected ones first."""
    words = [w for w in str(query or "").casefold().split() if w]
    cat = str(category or "").casefold()
    rows = []
    for p in catalog():
        hay = f"{p['name']} {p['slug']} {p['description']}".casefold()
        if words and not all(w in hay for w in words):
            continue
        if cat and cat not in [c.casefold() for c in p["categories"]]:
            continue
        if connected_only and p["slug"] not in connected:
            continue
        rows.append({**p, "connected": p["slug"] in connected, "connectable": connectable(p)})
    rows.sort(key=lambda r: (not r["connected"], not r["connectable"]))  # stable: usage order within groups
    offset, limit = max(0, int(offset)), max(1, min(int(limit), 120))
    counts: dict[str, int] = {}
    for p in catalog():
        for c in p["categories"]:
            counts[c] = counts.get(c, 0) + 1
    return {
        "total": len(rows),
        "items": rows[offset:offset + limit],
        "categories": [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:24]],
    }


def tools(slug: str) -> list[dict[str, Any]]:
    """The platform's tools with their tier (read, write, destructive), cached."""
    from bizos.connectors.composio import get_client
    from bizos.connectors.composio_actions import classify

    cached = _tools.get(slug)
    if cached and time.monotonic() - cached[0] < TOOLS_TTL:
        return cached[1]
    raw = get_client().tools.get_raw_composio_tools(toolkits=[slug], limit=1000)
    rows = sorted(
        ({"slug": str(t.slug), "name": str(getattr(t, "name", "") or t.slug),
          "description": " ".join(str(getattr(t, "description", "") or "").split())[:200],
          "tier": classify(str(t.slug), list(getattr(t, "tags", None) or []))}
         for t in raw if not getattr(t, "is_deprecated", False)),
        key=lambda r: ({"read": 0, "write": 1, "destructive": 2}[r["tier"]], r["name"]),
    )
    _tools[slug] = (time.monotonic(), rows)
    return rows


# --------------------------------------------------------------------------- tool choices


def _ensure_table(ctx: TenantContext) -> None:
    from bizos.tenancy.schema import client_wall_ddl

    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS integration_tool_choices (
                    owner_user_id TEXT NOT NULL,
                    toolkit       TEXT NOT NULL,
                    actions       JSONB NOT NULL,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (owner_user_id, toolkit)
                )
                """
            ))
            for statement in client_wall_ddl(ctx.client_id, ("integration_tool_choices",)):
                conn.execute(text(statement))
        _ready.add(key)


def chosen(ctx: TenantContext, toolkit: str) -> Optional[list[str]]:
    """The tools this user allowed on ``toolkit``, or None for all of them (the default)."""
    _ensure_table(ctx)
    with readonly_connection(ctx) as conn:
        row = conn.execute(
            text("SELECT actions FROM integration_tool_choices WHERE owner_user_id = :o AND toolkit = :t"),
            {"o": ctx.user_id, "t": toolkit},
        ).first()
    if row is None:
        return None
    value = row.actions if isinstance(row.actions, list) else json.loads(row.actions or "[]")
    return [str(a) for a in value]


def choose(ctx: TenantContext, toolkit: str, actions: Optional[list[str]]) -> None:
    """Allow every tool (``None``) or only ``actions`` on ``toolkit`` for this user."""
    _ensure_table(ctx)
    with workspace_connection(ctx) as conn:
        if actions is None:
            conn.execute(text("DELETE FROM integration_tool_choices WHERE owner_user_id = :o AND toolkit = :t"),
                         {"o": ctx.user_id, "t": toolkit})
            return
        conn.execute(
            text(
                "INSERT INTO integration_tool_choices (owner_user_id, toolkit, actions) VALUES (:o, :t, CAST(:a AS JSONB)) "
                "ON CONFLICT (owner_user_id, toolkit) DO UPDATE SET actions = EXCLUDED.actions, updated_at = NOW()"
            ),
            {"o": ctx.user_id, "t": toolkit, "a": json.dumps(sorted(set(actions)))},
        )


def allowed(ctx: TenantContext, toolkit: str, action: str) -> bool:
    """Whether this user lets the assistant run ``action`` on ``toolkit``."""
    picked = chosen(ctx, toolkit)
    return picked is None or str(action).upper() in {a.upper() for a in picked}
