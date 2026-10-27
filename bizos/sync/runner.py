"""Sync one user, or every connected user in every workspace (one run at a time across processes)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from agno.utils.log import log_info, log_warning
from sqlalchemy import text

from bizos.sync import store
from bizos.sync.sources import FETCHERS
from bizos.tenancy.context import TenantContext, tenant_scope

#: Postgres advisory-lock key for the sync run (any constant; one run at a time across API processes).
_LOCK_KEY = 4_241_771_001


def _connector(ctx: TenantContext, category: str) -> Optional[Any]:
    """The user's own Composio connector for a category, or None when they did not connect one."""
    from bizos.connectors import composio

    toolkit = composio.connected_toolkit(ctx, category)
    if not toolkit:
        return None
    return composio.CONNECTORS[toolkit](ctx, {"owner_user_id": ctx.user_id})


def sync_user(ctx: TenantContext, *, sources: Optional[list[str]] = None,
              connector_for: Callable[[TenantContext, str], Optional[Any]] = _connector) -> dict[str, Any]:
    """Pull each connected source for this user. A failing source is recorded and the others still run."""
    report: dict[str, Any] = {}
    with tenant_scope(ctx):
        store.ensure_tables(ctx)
        for source, (category, fetch) in FETCHERS.items():
            if sources and source not in sources:
                continue
            connector = connector_for(ctx, category)
            if connector is None:
                continue
            checkpoint = store.get_checkpoint(ctx, ctx.user_id, source)
            try:
                items, new_checkpoint = fetch(connector, checkpoint)
                new, updated = store.upsert(ctx, ctx.user_id, items)
                store.record_run(ctx, ctx.user_id, source, checkpoint=new_checkpoint, new=new, updated=updated)
                report[source] = {"fetched": len(items), "new": new, "updated": updated}
            except Exception as exc:  # noqa: BLE001 - one source failing must not stop the rest
                store.record_run(ctx, ctx.user_id, source, checkpoint=None, error=f"{type(exc).__name__}: {exc}")
                report[source] = {"error": str(exc)[:200]}
    return report


def _users_to_sync() -> list[TenantContext]:
    from bizos.connectors import composio
    from bizos.control import store as control

    if not composio.configured():
        return []
    out = []
    for client in control.list_clients():
        for user in control.list_users(client.id):
            if str(getattr(user, "status", "ACTIVE")) != "ACTIVE":
                continue
            try:
                ctx = control.build_context(user)
            except Exception:  # noqa: BLE001 - a broken account must not stop the run
                continue
            if composio.connected_categories(ctx):
                out.append(ctx)
    return out


def sync_all() -> dict[str, Any]:
    """One background tick: every connected user, unless another process is already running a tick."""
    from bizos.control.db import control_engine

    with control_engine().connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            return {"skipped": "another sync is running"}
        try:
            users = _users_to_sync()
            summary = {"users": len(users), "new": 0, "updated": 0, "errors": 0}
            for ctx in users:
                report = sync_user(ctx)
                for result in report.values():
                    summary["new"] += result.get("new", 0)
                    summary["updated"] += result.get("updated", 0)
                    summary["errors"] += 1 if "error" in result else 0
            if users:
                log_info(f"integration sync: {summary}")
            return summary
        except Exception as exc:  # noqa: BLE001 - the loop keeps going next tick
            log_warning(f"integration sync failed: {type(exc).__name__}: {exc}")
            return {"error": str(exc)[:200]}
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
