"""
Control-Plane Schema
====================

The control plane is the *only* shared database in the system. It holds the
client registry, the user directory, role grants and the domain catalogue —
metadata about tenants, never tenant content.

The distinction matters and is the whole point of §1: a shared table with a
``client_id`` column is acceptable for "which databases exist and who may sign
in to them", and unacceptable for a customer's CRM notes, memory or documents.
Those live only in the workspace the client owns.
"""

from __future__ import annotations

CONTROL_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS clients (
        id               TEXT PRIMARY KEY,
        slug             TEXT NOT NULL UNIQUE,
        company_name     TEXT NOT NULL,
        deployment_type  TEXT NOT NULL DEFAULT 'DEDICATED_DB',
        db_name          TEXT NOT NULL,
        db_schema        TEXT NOT NULL DEFAULT 'public',
        status           TEXT NOT NULL DEFAULT 'ACTIVE',
        -- ClientSettings (§21): enabled_domains, default_execution_mode,
        -- allow_phi, allow_card_data, enabled_integrations, memory_policy,
        -- retention_policy, approval_policy, risk_policy.
        settings         JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_by       TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id             TEXT PRIMARY KEY,
        client_id      TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        email          TEXT NOT NULL,
        display_name   TEXT NOT NULL DEFAULT '',
        password_hash  TEXT NOT NULL,
        status         TEXT NOT NULL DEFAULT 'ACTIVE',
        created_by     TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_login_at  TIMESTAMPTZ,
        failed_logins  INTEGER NOT NULL DEFAULT 0,
        locked_until   TIMESTAMPTZ,
        -- An email identifies a person *within* a client, not globally: the same
        -- consultant may hold accounts at two customers, and those accounts must
        -- stay separate principals.
        CONSTRAINT users_client_email_uniq UNIQUE (client_id, email)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS roles (
        name         TEXT PRIMARY KEY,
        rank         INTEGER NOT NULL,
        description  TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role        TEXT NOT NULL REFERENCES roles(name),
        granted_by  TEXT,
        granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS domain_packs (
        name         TEXT PRIMARY KEY,
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        phase        INTEGER NOT NULL DEFAULT 1,
        depth        TEXT NOT NULL DEFAULT 'full'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS client_domains (
        client_id  TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        domain     TEXT NOT NULL REFERENCES domain_packs(name),
        enabled    BOOLEAN NOT NULL DEFAULT FALSE,
        config     JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_by TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (client_id, domain)
    )
    """,
    # Control-plane audit: events that are not attributable to one workspace
    # (failed logins, client provisioning). Workspace-scoped events go to the
    # workspace's own append-only audit_events table.
    """
    CREATE TABLE IF NOT EXISTS control_audit_events (
        seq         BIGSERIAL PRIMARY KEY,
        id          TEXT NOT NULL UNIQUE,
        event_type  TEXT NOT NULL,
        client_id   TEXT,
        user_id     TEXT,
        actor_role  TEXT,
        status      TEXT,
        request     JSONB,
        result      JSONB,
        error       TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS control_audit_created_idx ON control_audit_events (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS users_email_idx ON users (lower(email))",
)

#: Seeded on every control-plane migration so the roles table always matches the
#: :class:`~bizos.types.Role` enum.
ROLE_SEED: tuple[tuple[str, int, str], ...] = (
    ("VIEWER", 0, "Ask questions, search permitted knowledge, view permitted information."),
    ("DRAFTER", 1, "Everything a Viewer can do, plus prepare drafts and propose actions."),
    ("APPROVER", 2, "Review, approve, reject and comment on proposed actions."),
    ("OPERATOR", 3, "Execute permitted actions and run approved workflows, subject to policy."),
    ("ADMIN", 4, "Manage users, roles, connectors, workflows, domains, policies and configuration."),
)
