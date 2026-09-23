"""
Agno Persistence per Workspace
==============================

Agno's :class:`~agno.db.postgres.PostgresDb` is reused unchanged; the platform's
only contribution is pointing one instance at each client's workspace, so agent
sessions, conversational memory, knowledge rows, traces and agno's run-pause
approvals inherit the same isolation boundary as the platform tables.

Instances are cached per workspace because ``PostgresDb`` owns a connection pool
and a table-resolution cache.
"""

from __future__ import annotations

import threading

from agno.db.postgres import PostgresDb

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_location

_dbs: dict[tuple[str, str], PostgresDb] = {}
_lock = threading.Lock()


def workspace_db(ctx: TenantContext) -> PostgresDb:
    """The agno ``PostgresDb`` bound to this client's workspace."""
    db_name, db_schema = workspace_location(ctx)
    key = (db_name, db_schema)
    db = _dbs.get(key)
    if db is not None:
        return db
    with _lock:
        db = _dbs.get(key)
        if db is None:
            from bizos import settings

            db = PostgresDb(
                id=f"ws-{ctx.client_slug}",
                db_url=settings.database_url(db_name),
                db_schema=db_schema,
            )
            _dbs[key] = db
    return db


def clear_cache() -> None:
    """Drop cached instances (shutdown, and between test modules)."""
    with _lock:
        _dbs.clear()
