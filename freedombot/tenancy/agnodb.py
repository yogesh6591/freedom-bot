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
from typing import Optional

from agno.db.postgres import PostgresDb

from freedombot.tenancy.context import TenantContext
from freedombot.tenancy.registry import workspace_location

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
            from freedombot import settings

            db = PostgresDb(
                id=f"ws-{ctx.client_slug}",
                db_url=settings.database_url(db_name),
                db_schema=db_schema,
            )
            _dbs[key] = db
    return db


def _close(db: PostgresDb) -> None:
    engine = getattr(db, "db_engine", None)
    if engine is not None:
        engine.dispose()


def release(db_name: str, db_schema: Optional[str] = None) -> int:
    """Close and forget the cached ``PostgresDb`` for one workspace (or every schema of ``db_name``).

    Called when a workspace is removed or its provisioning fails: each instance owns a connection pool,
    and a forgotten instance keeps that pool's connections open for the life of the process.
    """
    with _lock:
        keys = [k for k in _dbs if k[0] == db_name and (db_schema is None or k[1] == db_schema)]
        dropped = [_dbs.pop(k) for k in keys]
    for db in dropped:
        _close(db)
    return len(dropped)


def clear_cache() -> None:
    """Close and drop every cached instance (shutdown, and between test modules)."""
    with _lock:
        dropped = list(_dbs.values())
        _dbs.clear()
    for db in dropped:
        _close(db)
