"""
Workspace Schema
================

DDL for the tables that live **inside a client's workspace** — the customer's own
data. Every statement here is applied to a database (or schema) that belongs to
exactly one client, which is why none of these tables carries a ``tenant_id``
column: there is nothing to discriminate against, because there is no other
client in this namespace.

Agno's own tables (``agno_sessions``, ``agno_runs``, ``agno_memories``,
``agno_knowledge``, ``agno_traces``…) are created alongside these by
``PostgresDb`` pointed at the same workspace, so sessions, conversational memory,
knowledge and traces are isolated by the same boundary.

Statements are idempotent (``IF NOT EXISTS``) so provisioning can be re-run to
upgrade an existing workspace.
"""

from __future__ import annotations

#: Applied in order. Split into separate statements so a failure names the
#: table that failed rather than a 400-line blob.
WORKSPACE_DDL: tuple[str, ...] = (
    # ---------------------------------------------------------------- memory
    # An item is the *identity* of a piece of organizational knowledge ("the
    # refund period"). Its value over time lives in memory_versions. Corrections
    # never overwrite: they close one version and open the next.
    """
    CREATE TABLE IF NOT EXISTS memory_items (
        id              TEXT PRIMARY KEY,
        category        TEXT NOT NULL,
        memory_key      TEXT NOT NULL,
        title           TEXT NOT NULL,
        domain          TEXT,
        tags            TEXT[] NOT NULL DEFAULT '{}',
        classification  TEXT NOT NULL DEFAULT 'INTERNAL',
        created_by      TEXT NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT memory_items_category_key_uniq UNIQUE (category, memory_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_versions (
        id               TEXT PRIMARY KEY,
        item_id          TEXT NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
        version_no       INTEGER NOT NULL,
        content          TEXT NOT NULL,
        -- Category-specific structured fields. DECISION rows carry
        -- {decision, decided_on, approved_by, context, reason, status};
        -- CORRECTION rows carry {corrects_version_id, previous_value, reason}.
        attributes       JSONB NOT NULL DEFAULT '{}'::jsonb,
        status           TEXT NOT NULL DEFAULT 'ACTIVE',
        -- provenance (§3)
        source_type      TEXT NOT NULL DEFAULT 'MANUAL',
        source_id        TEXT,
        created_by       TEXT NOT NULL,
        updated_by       TEXT,
        confidence       DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        approval_status  TEXT NOT NULL DEFAULT 'APPROVED',
        approved_by      TEXT,
        approved_at      TIMESTAMPTZ,
        effective_from   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        effective_until  TIMESTAMPTZ,
        superseded_by    TEXT REFERENCES memory_versions(id) ON DELETE SET NULL,
        supersedes       TEXT REFERENCES memory_versions(id) ON DELETE SET NULL,
        correction_reason TEXT,
        corrected_by     TEXT,
        corrected_at     TIMESTAMPTZ,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT memory_versions_item_version_uniq UNIQUE (item_id, version_no)
    )
    """,
    # Exactly one ACTIVE version per item, enforced by the database rather than
    # by application discipline. A correction that forgot to close the old row
    # fails the transaction instead of leaving two conflicting "current" values.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS memory_versions_one_active
        ON memory_versions (item_id) WHERE status = 'ACTIVE'
    """,
    "CREATE INDEX IF NOT EXISTS memory_versions_item_idx ON memory_versions (item_id, version_no DESC)",
    "CREATE INDEX IF NOT EXISTS memory_items_category_idx ON memory_items (category, updated_at DESC)",
    # --------------------------------------------------------------- actions
    # A proposed business action. Outlives the agent run that proposed it, which
    # is why this is not agno's run-pause approval record.
    """
    CREATE TABLE IF NOT EXISTS actions (
        id                  TEXT PRIMARY KEY,
        title               TEXT NOT NULL,
        description         TEXT NOT NULL DEFAULT '',
        explanation         TEXT NOT NULL DEFAULT '',
        status              TEXT NOT NULL DEFAULT 'DRAFT',
        tool                TEXT NOT NULL,
        integration         TEXT,
        domain              TEXT,
        payload             JSONB NOT NULL DEFAULT '{}'::jsonb,
        risk_level          TEXT NOT NULL DEFAULT 'MEDIUM',
        classification      TEXT NOT NULL DEFAULT 'INTERNAL',
        execution_mode      TEXT NOT NULL,
        policy_reason       TEXT NOT NULL DEFAULT '',
        requested_by        TEXT NOT NULL,
        requested_by_role   TEXT,
        agent_id            TEXT,
        session_id          TEXT,
        run_id              TEXT,
        workflow_id         TEXT,
        workflow_run_id     TEXT,
        record_count        INTEGER NOT NULL DEFAULT 1,
        financial_amount    NUMERIC(18, 2),
        result              JSONB,
        error               TEXT,
        expires_at          TIMESTAMPTZ,
        executed_at         TIMESTAMPTZ,
        executed_by         TEXT,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS actions_status_idx ON actions (status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS actions_requested_by_idx ON actions (requested_by, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS approval_requests (
        id             TEXT PRIMARY KEY,
        action_id      TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
        status         TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
        reason         TEXT NOT NULL DEFAULT '',
        required_role  TEXT NOT NULL DEFAULT 'APPROVER',
        requested_by   TEXT NOT NULL,
        assigned_to    TEXT,
        decided_by     TEXT,
        decided_at     TIMESTAMPTZ,
        comment        TEXT,
        expires_at     TIMESTAMPTZ,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS approval_requests_status_idx ON approval_requests (status, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS approval_requests_action_idx ON approval_requests (action_id)",
    # Every state transition, append-only, so an auditor can reconstruct the
    # full history of who moved an action and when.
    """
    CREATE TABLE IF NOT EXISTS approval_events (
        id             BIGSERIAL PRIMARY KEY,
        action_id      TEXT NOT NULL REFERENCES actions(id) ON DELETE CASCADE,
        request_id     TEXT REFERENCES approval_requests(id) ON DELETE SET NULL,
        event          TEXT NOT NULL,
        from_status    TEXT,
        to_status      TEXT,
        actor_id       TEXT NOT NULL,
        actor_role     TEXT,
        comment        TEXT,
        payload_before JSONB,
        payload_after  JSONB,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS approval_events_action_idx ON approval_events (action_id, id)",
    # -------------------------------------------------------- human review
    """
    CREATE TABLE IF NOT EXISTS human_review_items (
        id                  TEXT PRIMARY KEY,
        question            TEXT NOT NULL,
        context             TEXT NOT NULL DEFAULT '',
        choices             JSONB NOT NULL DEFAULT '[]'::jsonb,
        recommended_option  TEXT,
        confidence          DOUBLE PRECISION,
        status              TEXT NOT NULL DEFAULT 'OPEN',
        agent_id            TEXT,
        workflow_id         TEXT,
        workflow_run_id     TEXT,
        action_id           TEXT REFERENCES actions(id) ON DELETE SET NULL,
        session_id          TEXT,
        created_by          TEXT NOT NULL,
        resolved_by         TEXT,
        resolution          TEXT,
        resolution_note     TEXT,
        resolved_at         TIMESTAMPTZ,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS human_review_status_idx ON human_review_items (status, created_at DESC)",
    # --------------------------------------------------------------- audit
    # Append-only by database trigger (see APPEND_ONLY_DDL below), not by
    # convention. `seq` gives a gap-free ordering independent of clock skew.
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        seq              BIGSERIAL PRIMARY KEY,
        id               TEXT NOT NULL UNIQUE,
        event_type       TEXT NOT NULL,
        client_id        TEXT NOT NULL,
        user_id          TEXT,
        actor_role       TEXT,
        agent_id         TEXT,
        session_id       TEXT,
        run_id           TEXT,
        workflow_id      TEXT,
        workflow_run_id  TEXT,
        action_id        TEXT,
        tool             TEXT,
        integration      TEXT,
        domain           TEXT,
        decision         TEXT,
        status           TEXT,
        approver         TEXT,
        risk_level       TEXT,
        classification   TEXT,
        request          JSONB,
        result           JSONB,
        error            TEXT,
        latency_ms       INTEGER,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS audit_events_created_idx ON audit_events (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS audit_events_type_idx ON audit_events (event_type, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS audit_events_user_idx ON audit_events (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS audit_events_action_idx ON audit_events (action_id)",
    # ------------------------------------------------- policy / permissions
    # Per-client overrides of the built-in tool permission matrix. Absent a row,
    # the code default in bizos.rbac.tools applies.
    """
    CREATE TABLE IF NOT EXISTS tool_permissions (
        tool_name   TEXT NOT NULL,
        role        TEXT NOT NULL,
        permission  TEXT NOT NULL,
        updated_by  TEXT,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (tool_name, role)
    )
    """,
    # Per-client policy overrides: risk thresholds, limits, approval rules.
    """
    CREATE TABLE IF NOT EXISTS policies (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        scope       TEXT NOT NULL DEFAULT 'client',
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        rules       JSONB NOT NULL DEFAULT '{}'::jsonb,
        description TEXT NOT NULL DEFAULT '',
        updated_by  TEXT,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # ---------------------------------------------------------- integrations
    # Never holds a live credential. `credential_ref` names a row in `secrets`.
    """
    CREATE TABLE IF NOT EXISTS integrations (
        id             TEXT PRIMARY KEY,
        category       TEXT NOT NULL,
        provider       TEXT NOT NULL,
        display_name   TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL DEFAULT 'DISCONNECTED',
        enabled        BOOLEAN NOT NULL DEFAULT TRUE,
        config         JSONB NOT NULL DEFAULT '{}'::jsonb,
        scopes         TEXT[] NOT NULL DEFAULT '{}',
        credential_ref TEXT,
        connected_by   TEXT,
        connected_at   TIMESTAMPTZ,
        last_check_at  TIMESTAMPTZ,
        last_error     TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT integrations_provider_uniq UNIQUE (category, provider)
    )
    """,
    # Encrypted-at-rest secret store, scoped to this workspace. The plaintext is
    # never selected into an LLM-visible path: only bizos.secrets reads it.
    """
    CREATE TABLE IF NOT EXISTS secrets (
        name        TEXT PRIMARY KEY,
        ciphertext  BYTEA NOT NULL,
        nonce       BYTEA NOT NULL,
        created_by  TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # ------------------------------------------------------------ workflows
    """
    CREATE TABLE IF NOT EXISTS workflow_configs (
        id                TEXT PRIMARY KEY,
        template          TEXT NOT NULL,
        name              TEXT NOT NULL,
        description       TEXT NOT NULL DEFAULT '',
        domain            TEXT,
        enabled           BOOLEAN NOT NULL DEFAULT FALSE,
        execution_mode    TEXT,
        cron              TEXT,
        connector_config  JSONB NOT NULL DEFAULT '{}'::jsonb,
        approval_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
        recipients        TEXT[] NOT NULL DEFAULT '{}',
        steps             JSONB NOT NULL DEFAULT '[]'::jsonb,
        updated_by        TEXT,
        created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_runs (
        id            TEXT PRIMARY KEY,
        workflow_id   TEXT NOT NULL REFERENCES workflow_configs(id) ON DELETE CASCADE,
        template      TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'RUNNING',
        trigger       TEXT NOT NULL DEFAULT 'manual',
        triggered_by  TEXT NOT NULL,
        agno_run_id   TEXT,
        input         JSONB NOT NULL DEFAULT '{}'::jsonb,
        output        JSONB,
        error         TEXT,
        started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at   TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS workflow_runs_wf_idx ON workflow_runs (workflow_id, started_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        id              BIGSERIAL PRIMARY KEY,
        workflow_run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
        step_index      INTEGER NOT NULL,
        name            TEXT NOT NULL,
        status          TEXT NOT NULL,
        summary         TEXT NOT NULL DEFAULT '',
        output          JSONB,
        error           TEXT,
        action_id       TEXT,
        started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        finished_at     TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS workflow_steps_run_idx ON workflow_steps (workflow_run_id, step_index)",
    # ------------------------------------------------------------- domains
    """
    CREATE TABLE IF NOT EXISTS domain_config (
        domain      TEXT PRIMARY KEY,
        enabled     BOOLEAN NOT NULL DEFAULT FALSE,
        config      JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_by  TEXT,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # --------------------------------------------------- mock business data
    # A deterministic, seedable stand-in for a real CRM / mailbox / calendar so
    # the four execution modes, the approval queue and the workflows can be
    # exercised end-to-end without a live vendor account. These live *inside*
    # the workspace, which is what makes the tenant-isolation test meaningful.
    """
    CREATE TABLE IF NOT EXISTS crm_contacts (
        id          TEXT PRIMARY KEY,
        first_name  TEXT NOT NULL DEFAULT '',
        last_name   TEXT NOT NULL DEFAULT '',
        email       TEXT,
        phone       TEXT,
        company     TEXT,
        title       TEXT,
        owner       TEXT,
        lifecycle   TEXT NOT NULL DEFAULT 'lead',
        tags        TEXT[] NOT NULL DEFAULT '{}',
        properties  JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS crm_contacts_email_idx ON crm_contacts (lower(email))",
    """
    CREATE TABLE IF NOT EXISTS crm_companies (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        domain      TEXT,
        industry    TEXT,
        size        TEXT,
        properties  JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crm_deals (
        id            TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        contact_id    TEXT REFERENCES crm_contacts(id) ON DELETE SET NULL,
        company_id    TEXT REFERENCES crm_companies(id) ON DELETE SET NULL,
        stage         TEXT NOT NULL DEFAULT 'new',
        amount        NUMERIC(18, 2) NOT NULL DEFAULT 0,
        currency      TEXT NOT NULL DEFAULT 'USD',
        close_date    DATE,
        owner         TEXT,
        properties    JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS crm_deals_stage_idx ON crm_deals (stage, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS crm_notes (
        id          TEXT PRIMARY KEY,
        contact_id  TEXT REFERENCES crm_contacts(id) ON DELETE CASCADE,
        deal_id     TEXT REFERENCES crm_deals(id) ON DELETE CASCADE,
        body        TEXT NOT NULL,
        author      TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crm_tasks (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        body        TEXT NOT NULL DEFAULT '',
        contact_id  TEXT REFERENCES crm_contacts(id) ON DELETE SET NULL,
        deal_id     TEXT REFERENCES crm_deals(id) ON DELETE SET NULL,
        assignee    TEXT,
        due_at      TIMESTAMPTZ,
        status      TEXT NOT NULL DEFAULT 'open',
        created_by  TEXT NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS email_messages (
        id          TEXT PRIMARY KEY,
        thread_id   TEXT NOT NULL,
        direction   TEXT NOT NULL DEFAULT 'inbound',
        sender      TEXT NOT NULL,
        recipients  TEXT[] NOT NULL DEFAULT '{}',
        cc          TEXT[] NOT NULL DEFAULT '{}',
        subject     TEXT NOT NULL DEFAULT '',
        body        TEXT NOT NULL DEFAULT '',
        labels      TEXT[] NOT NULL DEFAULT '{}',
        sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS email_messages_thread_idx ON email_messages (thread_id, sent_at)",
    """
    CREATE TABLE IF NOT EXISTS email_drafts (
        id           TEXT PRIMARY KEY,
        thread_id    TEXT,
        recipients   TEXT[] NOT NULL DEFAULT '{}',
        cc           TEXT[] NOT NULL DEFAULT '{}',
        subject      TEXT NOT NULL DEFAULT '',
        body         TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'draft',
        action_id    TEXT,
        created_by   TEXT NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        sent_at      TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_events (
        id          TEXT PRIMARY KEY,
        calendar_id TEXT NOT NULL DEFAULT 'primary',
        title       TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        location    TEXT,
        starts_at   TIMESTAMPTZ NOT NULL,
        ends_at     TIMESTAMPTZ NOT NULL,
        attendees   TEXT[] NOT NULL DEFAULT '{}',
        status      TEXT NOT NULL DEFAULT 'confirmed',
        organizer   TEXT,
        created_by  TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS calendar_events_time_idx ON calendar_events (starts_at)",
    """
    CREATE TABLE IF NOT EXISTS n8n_deliveries (
        id          TEXT PRIMARY KEY,
        event       TEXT NOT NULL,
        workflow    TEXT NOT NULL DEFAULT '',
        status      TEXT NOT NULL,
        request     JSONB NOT NULL DEFAULT '{}'::jsonb,
        response    TEXT,
        error       TEXT,
        created_by  TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS n8n_deliveries_created_idx ON n8n_deliveries (created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS accounting_customers (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        email        TEXT,
        currency     TEXT NOT NULL DEFAULT 'USD',
        balance_due  DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS accounting_customers_email_idx ON accounting_customers (lower(email))",
    """
    CREATE TABLE IF NOT EXISTS accounting_invoices (
        id              TEXT PRIMARY KEY,
        number          TEXT NOT NULL,
        customer_id     TEXT REFERENCES accounting_customers(id) ON DELETE SET NULL,
        customer_name   TEXT NOT NULL DEFAULT '',
        customer_email  TEXT,
        status          TEXT NOT NULL DEFAULT 'open',
        currency        TEXT NOT NULL DEFAULT 'USD',
        total           DOUBLE PRECISION NOT NULL DEFAULT 0,
        balance_due     DOUBLE PRECISION NOT NULL DEFAULT 0,
        issued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        due_at          TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT accounting_invoices_number_uniq UNIQUE (number)
    )
    """,
    "CREATE INDEX IF NOT EXISTS accounting_invoices_status_idx ON accounting_invoices (status)",
    """
    CREATE TABLE IF NOT EXISTS accounting_payments (
        id            TEXT PRIMARY KEY,
        customer_id   TEXT REFERENCES accounting_customers(id) ON DELETE SET NULL,
        customer_key  TEXT NOT NULL DEFAULT '',
        invoice_id    TEXT REFERENCES accounting_invoices(id) ON DELETE SET NULL,
        amount        DOUBLE PRECISION NOT NULL,
        currency      TEXT NOT NULL DEFAULT 'USD',
        method        TEXT NOT NULL DEFAULT 'ach',
        note          TEXT NOT NULL DEFAULT '',
        created_by    TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


#: Additive columns for workspaces provisioned before they existed.
UPGRADE_DDL: tuple[str, ...] = (
    # FB-037: restricted data area of a memory item (NULL = general).
    "ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS access_area TEXT",
    "CREATE INDEX IF NOT EXISTS memory_items_area_idx ON memory_items (access_area)",
    # FB-038: which execution mode governed the audited event.
    "ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS execution_mode TEXT",
)


def client_wall_ddl(client_id: str) -> tuple[str, ...]:
    """FB-033: stamp every workspace row with its client and refuse any other.

    Each workspace table gets a ``client_id`` column defaulting to the owning
    client and a CHECK that it equals that client, so a row can never be written
    into this workspace on behalf of another client — even by code that forgets
    to set it. Isolation still primarily comes from the separate database or
    schema; this is the second wall inside it.
    """
    literal = "'" + client_id.replace("'", "''") + "'"
    out: list[str] = []
    for table in WORKSPACE_TABLES:
        out.append(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS client_id TEXT NOT NULL DEFAULT {literal}"
        )
        out.append(f"ALTER TABLE {table} ALTER COLUMN client_id SET DEFAULT {literal}")
        out.append(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{table}_client_wall'
                      AND conrelid = '{table}'::regclass
                ) THEN
                    ALTER TABLE {table}
                        ADD CONSTRAINT {table}_client_wall CHECK (client_id = {literal});
                END IF;
            END $$
            """
        )
    return tuple(out)


#: Makes ``audit_events`` genuinely append-only rather than append-by-convention.
#: A BEFORE trigger raises on any UPDATE or DELETE, including from the table's
#: owner — which a ``REVOKE`` cannot achieve, since the owner keeps its rights.
APPEND_ONLY_DDL: tuple[str, ...] = (
    # The one sanctioned removal is the retention purge (FB-038): a DELETE of a
    # row older than the cutoff that the purge transaction set with
    # ``SET LOCAL bizos.audit_purge_before``. Updates are never permitted, and a
    # delete of anything newer than the cutoff still raises.
    """
    CREATE OR REPLACE FUNCTION bizos_reject_mutation() RETURNS TRIGGER AS $$
    DECLARE
        cutoff TEXT := current_setting('bizos.audit_purge_before', true);
    BEGIN
        IF TG_OP = 'DELETE' AND cutoff IS NOT NULL AND cutoff <> ''
           AND OLD.created_at < cutoff::timestamptz THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
    END;
    $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events",
    """
    CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION bizos_reject_mutation()
    """,
    # Same protection for the approval transition history.
    "DROP TRIGGER IF EXISTS approval_events_append_only ON approval_events",
    """
    CREATE TRIGGER approval_events_append_only
        BEFORE UPDATE OR DELETE ON approval_events
        FOR EACH ROW EXECUTE FUNCTION bizos_reject_mutation()
    """,
)


#: Tables the platform owns inside a workspace. Used by the provisioning
#: verifier and by the isolation test to assert a workspace is complete.
WORKSPACE_TABLES: tuple[str, ...] = (
    "memory_items",
    "memory_versions",
    "actions",
    "approval_requests",
    "approval_events",
    "human_review_items",
    "audit_events",
    "tool_permissions",
    "policies",
    "integrations",
    "secrets",
    "workflow_configs",
    "workflow_runs",
    "workflow_steps",
    "domain_config",
    "crm_contacts",
    "crm_companies",
    "crm_deals",
    "crm_notes",
    "crm_tasks",
    "email_messages",
    "email_drafts",
    "calendar_events",
    "n8n_deliveries",
    "accounting_customers",
    "accounting_invoices",
    "accounting_payments",
)
