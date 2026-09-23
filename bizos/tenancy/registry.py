"""
Workspace Registry
==================

Resolves a client to a database connection, and is the **only** way the platform
reaches customer data.

Two design rules make cross-client leakage structurally impossible rather than
merely unlikely:

1. **An engine is bound to one workspace.** ``engine_for(...)`` returns an engine
   whose URL names exactly one database and (for schema-isolated deployments)
   whose connections open with ``search_path`` pinned to exactly one schema. There
   is no engine that can see two clients, so there is no query that can join
   across them — not even a buggy or model-authored one.

2. **You cannot ask for a workspace without naming a client.** The session
   helpers take their client from the bound :class:`~bizos.tenancy.context.TenantContext`,
   which is only ever built from verified credentials. Calling them with no
   context raises.

A ``search_path``-pinned connection plus a statement guard (adapted from
``context/db/session.py``) also stops a schema-isolated workspace writing into a
neighbour's schema or into agno's.
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Connection, Engine, create_engine, event, text

from bizos import settings
from bizos.tenancy.context import TenantContext, require_context
from bizos.types import DeploymentType

# Postgres identifiers we generate ourselves (from a slug) — validated anyway,
# because these are interpolated into DDL where bind parameters are not allowed.
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class InvalidWorkspaceIdentifier(ValueError):
    """A database or schema name that is not a safe bare Postgres identifier."""


def validate_identifier(name: str) -> str:
    """Return ``name`` if it is a safe unquoted Postgres identifier, else raise.

    Applied to every database/schema name before it reaches DDL. Client-supplied
    text never arrives here directly — names are derived via
    :func:`bizos.util.ids.slugify` — but this is the backstop that makes SQL
    injection through an identifier impossible rather than improbable.
    """
    if not _IDENT_RE.match(name or ""):
        raise InvalidWorkspaceIdentifier(
            f"{name!r} is not a valid workspace identifier (expected ^[a-z_][a-z0-9_]{{0,62}}$)"
        )
    return name


# Statement patterns that would break out of a workspace's confinement: writing
# to another schema by qualifying it, or re-pointing search_path / switching role.
# Anchored to a statement boundary so a literal value containing the word cannot
# false-trigger. This backs up the search_path pin; it is not the primary defense.
_ESCAPE_RE = re.compile(
    r"""(?ix)
    (?:\A|;)\s*(?:set|reset)\s+(?:local\s+|session\s+)?(?:role|search_path)\b
    | (?:\A|;)\s*(?:alter|create|drop)\s+(?:role|user)\b
    | (?:\A|;)\s*(?:grant|revoke)\b
    | (?:\A|;)\s*copy\b
    """
)


def _guard_escapes(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
    """Reject statements that would subvert workspace confinement."""
    if _ESCAPE_RE.search(statement):
        raise RuntimeError(
            "Statement rejected: role/search_path changes and privilege grants are not "
            "permitted on a workspace connection."
        )


class WorkspaceRegistry:
    """Caches one engine per (database, schema, read-only) triple.

    Engines are expensive (they own a connection pool) and long-lived, so they are
    cached; the cache key includes the read-only flag because ADVISE mode needs a
    genuinely different Postgres session setting, not an application-level flag.
    """

    def __init__(self) -> None:
        self._engines: dict[tuple[str, str, bool], Engine] = {}
        self._lock = threading.Lock()

    # -- engine construction -------------------------------------------------

    def _build(self, db_name: str, db_schema: str, readonly: bool) -> Engine:
        validate_identifier(db_name)
        validate_identifier(db_schema)

        options = [f"-c search_path={db_schema}"]
        if readonly:
            # Enforced by Postgres for the whole session. A tool that somehow
            # reached this engine still cannot write, whatever the prompt said.
            options.append("-c default_transaction_read_only=on")

        engine = create_engine(
            settings.database_url(db_name),
            connect_args={"options": " ".join(options)},
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            future=True,
        )
        if not readonly:
            event.listen(engine, "before_cursor_execute", _guard_escapes)
        return engine

    def engine(self, db_name: str, db_schema: str = "public", *, readonly: bool = False) -> Engine:
        """Get (or create) the cached engine for one workspace."""
        key = (db_name, db_schema, readonly)
        engine = self._engines.get(key)
        if engine is not None:
            return engine
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = self._build(db_name, db_schema, readonly)
                self._engines[key] = engine
        return engine

    def engine_for(self, ctx: TenantContext, *, readonly: bool = False) -> Engine:
        """The engine for the workspace named by ``ctx``."""
        db_name, db_schema = workspace_location(ctx)
        return self.engine(db_name, db_schema, readonly=readonly)

    def dispose_all(self) -> None:
        """Close every pooled connection. Used at shutdown and between tests."""
        with self._lock:
            for engine in self._engines.values():
                engine.dispose()
            self._engines.clear()

    def dispose(self, db_name: str) -> None:
        """Drop every cached engine for one database (used before dropping it)."""
        with self._lock:
            for key in [k for k in self._engines if k[0] == db_name]:
                self._engines.pop(key).dispose()


#: Process-wide registry. One per process is correct: engines are pools, and the
#: isolation guarantee comes from the key, not from having separate registries.
registry = WorkspaceRegistry()


def workspace_location(ctx: TenantContext) -> tuple[str, str]:
    """The ``(database, schema)`` a client's tables live in.

    ``DEDICATED_DB``     → its own database, ``public`` schema.
    ``DEDICATED_SCHEMA`` → the shared workspace database, its own schema.
    """
    if ctx.deployment_type == DeploymentType.DEDICATED_SCHEMA:
        return (ctx.db_name or settings.shared_workspace_database(), ctx.db_schema or "public")
    return (ctx.db_name, "public")


def workspace_db_url(ctx: TenantContext) -> str:
    """SQLAlchemy URL for a client's workspace — what agno's ``PostgresDb`` gets."""
    db_name, _schema = workspace_location(ctx)
    return settings.database_url(db_name)


# ---------------------------------------------------------------------------
# Session helpers — the public surface for workspace access
# ---------------------------------------------------------------------------


@contextmanager
def workspace_connection(
    ctx: Optional[TenantContext] = None, *, readonly: bool = False
) -> Iterator[Connection]:
    """A transactional connection to the current (or given) client's workspace.

    Commits on clean exit, rolls back on exception. Passing ``ctx`` explicitly is
    for server-side callers (provisioning, the scheduler) that establish the
    tenant themselves; request handlers should rely on the bound context so the
    client can never be chosen by the caller.
    """
    tenant = ctx or require_context()
    engine = registry.engine_for(tenant, readonly=readonly)
    with engine.begin() as conn:
        yield conn


@contextmanager
def readonly_connection(ctx: Optional[TenantContext] = None) -> Iterator[Connection]:
    """A Postgres-level read-only connection to the workspace.

    Used for ADVISE mode and for every read tool, so that "this cannot write" is
    a property of the database session rather than a property of the prompt.
    """
    with workspace_connection(ctx, readonly=True) as conn:
        yield conn


def ping(ctx: Optional[TenantContext] = None) -> bool:
    """Whether the workspace is reachable."""
    try:
        with workspace_connection(ctx, readonly=True) as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
