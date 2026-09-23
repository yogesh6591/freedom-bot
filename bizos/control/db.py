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
