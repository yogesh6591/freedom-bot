"""Control-plane engine and migration."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Connection, Engine, create_engine, text

from bizos import settings
from bizos.control.schema import CONTROL_DDL, ROLE_SEED
from bizos.domains.catalog import DOMAIN_CATALOG

_engine: Engine | None = None
_lock = threading.Lock()


def control_engine() -> Engine:
    """The lazily-built control-plane engine (one per process)."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = create_engine(
                    settings.control_db_url(),
                    pool_size=5,
                    max_overflow=10,
                    pool_pre_ping=True,
                    future=True,
                )
    return _engine


def dispose_control_engine() -> None:
    """Close the control-plane pool (shutdown, and between test modules)."""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None


@contextmanager
def control_connection() -> Iterator[Connection]:
    """A transactional control-plane connection."""
    with control_engine().begin() as conn:
        yield conn


def migrate_control_plane() -> None:
    """Create/upgrade control-plane tables and seed the static catalogues.

    Idempotent — safe to run on every boot, which is how the app applies it.
    """
    with control_connection() as conn:
        for statement in CONTROL_DDL:
            conn.execute(text(statement))
        for name, rank, description in ROLE_SEED:
            conn.execute(
                text(
                    "INSERT INTO roles (name, rank, description) VALUES (:n, :r, :d) "
                    "ON CONFLICT (name) DO UPDATE SET rank = EXCLUDED.rank, "
                    "description = EXCLUDED.description"
                ),
                {"n": name, "r": rank, "d": description},
            )
        for pack in DOMAIN_CATALOG:
            conn.execute(
                text(
                    "INSERT INTO domain_packs (name, title, description, phase, depth) "
                    "VALUES (:n, :t, :d, :p, :x) "
                    "ON CONFLICT (name) DO UPDATE SET title = EXCLUDED.title, "
                    "description = EXCLUDED.description, phase = EXCLUDED.phase, depth = EXCLUDED.depth"
                ),
                {
                    "n": pack.name,
                    "t": pack.title,
                    "d": pack.description,
                    "p": pack.phase,
                    "x": pack.depth,
                },
            )
        _fold_legacy_person_access(conn)


def _fold_legacy_person_access(conn: Connection) -> None:
    """Move each person's earlier per-department roles and CRM level into one role and data tags, once.

    Never less strict (bizos/policy/person_role.fold_legacy): the role is the strictest they had and each
    department they held becomes a data tag. The old values are then cleared, so this runs once.
    """
    import json

    from bizos.policy.person_role import fold_legacy

    rows = conn.execute(
        text("SELECT id, job_role, data_scopes, access_mode, department_modes, app_modes FROM users "
             "WHERE department_modes <> '{}'::jsonb OR app_modes <> '{}'::jsonb")
    ).fetchall()
    for row in rows:
        crm = row.app_modes if isinstance(row.app_modes, dict) else json.loads(row.app_modes or "{}")
        mode, tags = fold_legacy(row.department_modes, crm.get("crm"), row.job_role, row.data_scopes or ())
        if row.access_mode and mode:
            from bizos.policy.person_role import _strictest

            mode = _strictest([row.access_mode, mode])
        conn.execute(
            text("UPDATE users SET access_mode = COALESCE(:m, access_mode), data_scopes = :t, "
                 "department_modes = '{}'::jsonb, app_modes = '{}'::jsonb, updated_at = NOW() WHERE id = :i"),
            {"m": mode, "t": tags, "i": row.id},
        )
