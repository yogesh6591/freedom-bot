"""
Workspace Provisioning
======================

Creates the physical isolation boundary for a client: its own Postgres database
(``DEDICATED_DB``) or its own schema (``DEDICATED_SCHEMA``), then applies the
workspace DDL and agno's own tables into it.

``CREATE DATABASE`` cannot run inside a transaction block, so that one statement
uses an ``AUTOCOMMIT`` connection to the maintenance database. Everything after
it is ordinary transactional DDL against the new workspace.

Provisioning is idempotent: re-running it on an existing workspace upgrades the
schema rather than failing, which is what makes deploying a new platform version
to existing tenants a no-op.
"""

from __future__ import annotations

from typing import Optional

from agno.utils.log import log_warning
from sqlalchemy import create_engine, text

from bizos import settings
from bizos.control.models import Client
from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import registry, validate_identifier, workspace_location
from bizos.tenancy.schema import (
    APPEND_ONLY_DDL,
    UPGRADE_DDL,
    WORKSPACE_DDL,
    WORKSPACE_TABLES,
    client_wall_ddl,
)
from bizos.types import DeploymentType


def _maintenance_engine():
    """AUTOCOMMIT engine on the control database, for CREATE/DROP DATABASE."""
    return create_engine(
        settings.control_db_url(), isolation_level="AUTOCOMMIT", poolclass=None, future=True
    )


def database_exists(db_name: str) -> bool:
    engine = _maintenance_engine()
    try:
        with engine.connect() as conn:
            return (
                conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
                ).first()
                is not None
            )
    finally:
        engine.dispose()


def create_database(db_name: str) -> bool:
    """Create ``db_name`` if absent. Returns whether it was created now."""
    validate_identifier(db_name)
    if database_exists(db_name):
        return False
    engine = _maintenance_engine()
    try:
        with engine.connect() as conn:
            # Identifier validated above; Postgres does not accept a bind
            # parameter in a CREATE DATABASE target.
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        return True
    finally:
        engine.dispose()


def drop_database(db_name: str) -> None:
    """Drop a workspace database. Used by tests and by client deletion."""
    validate_identifier(db_name)
    registry.dispose(db_name)
    engine = _maintenance_engine()
    try:
        with engine.connect() as conn:
            # Best effort: terminating a backend owned by a superuser needs
            # superuser rights, which the application role deliberately does not
            # have. A failure here is not fatal — the DROP below reports honestly
            # if a connection really is still holding the database open.
            try:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :n AND pid <> pg_backend_pid()"
                    ),
                    {"n": db_name},
                )
            except Exception as exc:
                log_warning(f"could not terminate connections to {db_name}: {exc}")
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        engine.dispose()


def provision_client(client: Client, *, with_agno_tables: bool = True) -> dict:
    """Create and migrate ``client``'s workspace. Idempotent.

    Returns a small report so the caller (and the test suite) can assert what
    actually happened rather than trusting that it did.
    """
    ctx = _provisioning_context(client)
    db_name, db_schema = workspace_location(ctx)
    report: dict = {
        "client_id": client.id,
        "deployment_type": str(client.deployment_type),
        "db_name": db_name,
        "db_schema": db_schema,
        "database_created": False,
        "schema_created": False,
        "tables": [],
    }

    if client.deployment_type == DeploymentType.DEDICATED_DB:
        report["database_created"] = create_database(db_name)
    else:
        # The shared host database for schema-isolated tenants.
        report["database_created"] = create_database(db_name)
        engine = registry.engine(db_name, "public")
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{validate_identifier(db_schema)}"'))
        report["schema_created"] = True

    apply_workspace_schema(ctx)
    if with_agno_tables:
        provision_agno_tables(ctx)
    report["tables"] = verify_workspace(ctx)
    return report


def _provisioning_context(client: Client) -> TenantContext:
    """A minimal context naming the workspace — used only for engine resolution."""
    from bizos.types import Role

    return TenantContext(
        client_id=client.id,
        client_slug=client.slug,
        user_id="__provisioner__",
        roles=frozenset({Role.ADMIN}),
        deployment_type=client.deployment_type,
        db_name=client.db_name,
        db_schema=client.db_schema,
    )


def apply_workspace_schema(ctx: TenantContext) -> None:
    """Apply the platform DDL to one workspace."""
    db_name, db_schema = workspace_location(ctx)
    engine = registry.engine(db_name, db_schema)
    with engine.begin() as conn:
        for statement in WORKSPACE_DDL:
            try:
                conn.execute(text(statement))
            except Exception as exc:  # pragma: no cover - surfaces the failing table
                head = " ".join(statement.split())[:120]
                raise RuntimeError(f"Workspace DDL failed: {head}…: {exc}") from exc
        for statement in UPGRADE_DDL:
            conn.execute(text(statement))
        for statement in client_wall_ddl(ctx.client_id):
            conn.execute(text(statement))
        for statement in APPEND_ONLY_DDL:
            conn.execute(text(statement))


def provision_agno_tables(ctx: TenantContext) -> None:
    """Let agno create its own tables inside the client's workspace.

    Reuses ``PostgresDb`` rather than duplicating agno's schema. Sessions, runs,
    conversational memory, knowledge rows, traces and agno's own approvals then
    live behind the same isolation boundary as the platform tables.
    """
    from bizos.tenancy.agnodb import workspace_db

    db = workspace_db(ctx)
    # Touching a table triggers agno's lazy create-if-absent for that table.
    for table_type in ("sessions", "memories", "metrics", "knowledge", "approvals"):
        try:
            db._get_table(table_type=table_type, create_table_if_not_found=True)  # type: ignore[attr-defined]
        except Exception:
            # Agno also creates tables lazily on first real use; a failure here
            # (e.g. an adapter without that table type) must not block
            # provisioning of the platform's own schema.
            continue


def verify_workspace(ctx: TenantContext) -> list[str]:
    """Return the platform tables present in the workspace.

    Used by provisioning and by the isolation test to assert completeness rather
    than assume it.
    """
    db_name, db_schema = workspace_location(ctx)
    engine = registry.engine(db_name, db_schema)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = ANY(:names)"
            ),
            {"s": db_schema, "names": list(WORKSPACE_TABLES)},
        ).fetchall()
    return sorted(r.table_name for r in rows)


def unwalled_tables(ctx: TenantContext) -> list[str]:
    """Workspace tables lacking the per-client ``client_id`` wall (FB-033)."""
    db_name, db_schema = workspace_location(ctx)
    engine = registry.engine(db_name, db_schema)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname FROM pg_constraint k "
                "JOIN pg_class c ON c.oid = k.conrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND k.conname = c.relname || '_client_wall'"
            ),
            {"s": db_schema},
        ).fetchall()
    walled = {r.relname for r in rows}
    return sorted(set(WORKSPACE_TABLES) - walled)


def missing_tables(ctx: TenantContext) -> list[str]:
    """Platform tables the workspace is missing."""
    present = set(verify_workspace(ctx))
    return sorted(set(WORKSPACE_TABLES) - present)


def deprovision_client(client: Client) -> None:
    """Destroy a client's workspace. Irreversible; used by tests and offboarding."""
    ctx = _provisioning_context(client)
    db_name, db_schema = workspace_location(ctx)
    if client.deployment_type == DeploymentType.DEDICATED_DB:
        drop_database(db_name)
        return
    registry.dispose(db_name)
    engine = registry.engine(db_name, "public")
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{validate_identifier(db_schema)}" CASCADE'))
