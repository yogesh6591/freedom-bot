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
    # Restricted data areas (exec/hr/salary/finance/legal) a user may see. Held
    # on the user record, not the token, so a revoked scope takes effect at once.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS data_scopes TEXT[] NOT NULL DEFAULT '{}'",
    # M15-20: the JeanneCAIO owner view spans every client, so it is gated on a
    # platform-level flag held by Cepoch staff — never on a client's ADMIN role.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_owner BOOLEAN NOT NULL DEFAULT FALSE",
    # The job title an admin picks (HR, Finance...). A label over the permission level
    # in user_roles; see freedombot/rbac/job_roles.py. NULL until an admin picks one.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS job_role TEXT",
    # Retired per-person CRM level, {"crm": "DRAFT" | "WAIT_FOR_APPROVAL"}. Folded into access_mode
    # (and cleared) by db._fold_legacy_person_access on migration; nothing reads it after that.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS app_modes JSONB NOT NULL DEFAULT '{}'::jsonb",
    # Retired role per department, {"sales": "draft"}. Folded into access_mode and data tags (and cleared)
    # by db._fold_legacy_person_access on migration; nothing reads it after that.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS department_modes JSONB NOT NULL DEFAULT '{}'::jsonb",
    # The person's one role: advise, draft, approve or auto. Only ever narrows; see
    # freedombot/policy/person_role.py. NULL means no extra limit (accounts from before roles).
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS access_mode TEXT",
    # A person asking an admin for extra data tags. Granting goes through admin_ops.set_scopes.
    """
    CREATE TABLE IF NOT EXISTS access_requests (
        id          TEXT PRIMARY KEY,
        client_id   TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        areas       TEXT[] NOT NULL,
        reason      TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL DEFAULT 'PENDING',
        decided_by  TEXT,
        decided_at  TIMESTAMPTZ,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS access_requests_pending_idx ON access_requests (client_id, status, created_at DESC)",
    # FB-046 — post-sale support tickets (control-plane metadata; not a sales CRM).
    """
    CREATE TABLE IF NOT EXISTS support_tickets (
        id               TEXT PRIMARY KEY,
        client_id        TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        subject          TEXT NOT NULL,
        body             TEXT NOT NULL DEFAULT '',
        severity         TEXT NOT NULL DEFAULT 'NORMAL',
        status           TEXT NOT NULL DEFAULT 'OPEN',
        assignee         TEXT NOT NULL DEFAULT 'CEPOCH',
        escalate_reason  TEXT,
        created_by       TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        history          JSONB NOT NULL DEFAULT '[]'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS support_tickets_client_idx ON support_tickets (client_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS support_tickets_assignee_idx ON support_tickets (assignee, status)",
    # Ticketing and escalation: what kind of issue, where it came from, the chat it came with, and how
    # it ended. Routing and notes are for support staff only (freedombot/support.py requester_view).
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'TECHNICAL'",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'MANUAL'",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS triage BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS context JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS resolution TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS dedupe_key TEXT",
    "CREATE INDEX IF NOT EXISTS support_tickets_dedupe_idx ON support_tickets (dedupe_key)",
    # In-app notice to the team a ticket was routed or reassigned to. Only Cepoch and Jeanne get these.
    """
    CREATE TABLE IF NOT EXISTS support_notifications (
        id          TEXT PRIMARY KEY,
        ticket_id   TEXT NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        team        TEXT NOT NULL,
        kind        TEXT NOT NULL,
        message     TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        read_at     TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS support_notifications_team_idx ON support_notifications (team, read_at, created_at DESC)",
    # Who works the support queue: CEPOCH (technical, first layer) or JEANNE (business). Set only on accounts
    # in Cepoch's internal workspace; workspace admins never get it.
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS support_team TEXT",
    "UPDATE users u SET support_team = 'JEANNE' FROM clients c WHERE u.client_id = c.id AND c.slug = 'cepoch' "
    "AND lower(u.email) = 'jeanne@cepoch.com' AND u.support_team IS NULL",
    "UPDATE users u SET support_team = 'CEPOCH' FROM clients c WHERE u.client_id = c.id AND c.slug = 'cepoch' "
    "AND lower(u.email) = 'yogesh@cepoch.com' AND u.support_team IS NULL",
    # Workspace timezone: existing workspaces get the default once; a value an admin set is kept.
    "UPDATE clients SET settings = jsonb_set(settings, '{timezone}', '\"Asia/Kolkata\"') "
    "WHERE NOT (settings ? 'timezone')",
)

#: Seeded on every control-plane migration so the roles table always matches the
#: :class:`~freedombot.types.Role` enum.
ROLE_SEED: tuple[tuple[str, int, str], ...] = (
    ("VIEWER", 0, "Ask questions, search permitted knowledge, view permitted information."),
    ("DRAFTER", 1, "Everything a Viewer can do, plus prepare drafts and propose actions."),
    ("APPROVER", 2, "Review, approve, reject and comment on proposed actions."),
    ("OPERATOR", 3, "Execute permitted actions and run approved workflows, subject to policy."),
    ("ADMIN", 4, "Manage users, roles, connectors, workflows, domains, policies and configuration."),
)
