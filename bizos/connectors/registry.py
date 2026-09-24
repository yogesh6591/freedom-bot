"""
Connector Registry
==================

Resolves an integration category to the concrete adapter a client has connected,
and answers "which integrations are connected" for the policy engine.

The mapping lives in the workspace's ``integrations`` table, so two clients can
run different providers for the same category — one on the workspace CRM, another
on HubSpot — without any code change above this layer.

Credentials are **never** returned from here. ``integrations.credential_ref`` names
a row in the workspace ``secrets`` table, and only :mod:`bizos.connectors.secrets`
can decrypt it. Nothing that reaches the model ever holds a token (§14).
"""

from __future__ import annotations

import json
from typing import Any, Optional, Type

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorNotConfigured
from bizos.connectors.calendar import GoogleCalendarConnector, WorkspaceCalendarConnector
from bizos.connectors.email import GmailConnector, WorkspaceEmailConnector
from bizos.connectors.mock_crm import CrmConnector
from bizos.connectors.n8n import N8nWebhookConnector
from bizos.tenancy.context import TenantContext, require_context
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id

#: category -> provider -> adapter class
PROVIDERS: dict[str, dict[str, Type[Connector]]] = {
    "crm": {"workspace": CrmConnector},
    "email": {"workspace": WorkspaceEmailConnector, "gmail": GmailConnector},
    "calendar": {"workspace": WorkspaceCalendarConnector, "google_calendar": GoogleCalendarConnector},
    "n8n": {"webhook": N8nWebhookConnector},
}

#: What a fresh workspace gets connected at provisioning time: the workspace-backed
#: adapters, so the platform is fully exercisable before any vendor account exists.
DEFAULT_INTEGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("crm", "workspace", "Workspace CRM"),
    ("email", "workspace", "Workspace Email"),
    ("calendar", "workspace", "Workspace Calendar"),
    ("n8n", "webhook", "n8n Webhook"),
)

#: Categories declared for the UI and policy engine but with no Phase 1 adapter.
DECLARED_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("storage", "Google Drive / OneDrive / SharePoint — not implemented in Phase 1"),
    ("communication", "Slack / Teams — not implemented in Phase 1"),
)


class IntegrationNotConnected(ConnectorNotConfigured):
    """No enabled integration exists for the requested category."""


def seed_default_integrations(ctx: TenantContext) -> None:
    """Connect the workspace-backed adapters for a newly provisioned client."""
    with workspace_connection(ctx) as conn:
        for category, provider, display in DEFAULT_INTEGRATIONS:
            conn.execute(
                text(
                    "INSERT INTO integrations (id, category, provider, display_name, status, "
                    "enabled, connected_by, connected_at) VALUES "
                    "(:i, :c, :p, :d, 'CONNECTED', TRUE, :u, NOW()) "
                    "ON CONFLICT (category, provider) DO NOTHING"
                ),
                {"i": new_id("int"), "c": category, "p": provider, "d": display, "u": ctx.user_id},
            )


def list_integrations(ctx: Optional[TenantContext] = None) -> list[dict[str, Any]]:
    """Every integration row, plus the declared-but-unimplemented categories."""
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text(
                "SELECT id, category, provider, display_name, status, enabled, config, scopes, "
                "connected_by, connected_at, last_check_at, last_error FROM integrations "
                "ORDER BY category, provider"
            )
        ).fetchall()
    out = [dict(r._mapping) for r in rows]
    connected = {r["category"] for r in out}
    for category, note in DECLARED_CATEGORIES:
        if category not in connected:
            out.append(
                {
                    "id": None,
                    "category": category,
                    "provider": None,
                    "display_name": category.title(),
                    "status": "NOT_IMPLEMENTED",
                    "enabled": False,
                    "config": {},
                    "scopes": [],
                    "last_error": note,
                }
            )
    return out


def connected_categories(ctx: Optional[TenantContext] = None) -> frozenset[str]:
    """Categories with an enabled, connected integration.

    This is the ``connected_integrations`` input the policy engine uses to deny a
    tool whose connector is not actually available.
    """
    try:
        with readonly_connection(ctx) as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT category FROM integrations "
                    "WHERE enabled = TRUE AND status = 'CONNECTED'"
                )
            ).fetchall()
        return frozenset(r.category for r in rows)
    except Exception:
        # Fail closed: an unreadable integration table means no connector is
        # confirmed available, so connector-backed tools are denied.
        return frozenset()


def get_connector(category: str, *, ctx: Optional[TenantContext] = None) -> Connector:
    """The adapter instance for one category in the current workspace."""
    tenant = ctx or require_context()
    with readonly_connection(tenant) as conn:
        row = conn.execute(
            text(
                "SELECT provider, config FROM integrations WHERE category = :c AND enabled = TRUE "
                "AND status = 'CONNECTED' ORDER BY connected_at DESC LIMIT 1"
            ),
            {"c": category},
        ).first()
    if row is None:
        raise IntegrationNotConnected(
            f"No {category} integration is connected for this workspace. "
            "Connect one under Integrations."
        )
    adapter = PROVIDERS.get(category, {}).get(row.provider)
    if adapter is None:
        raise IntegrationNotConnected(
            f"No adapter is available for {category}/{row.provider} in this deployment."
        )
    return adapter(tenant, row.config or {})


def set_integration(
    *,
    category: str,
    provider: str,
    display_name: str = "",
    enabled: bool = True,
    status: str = "CONNECTED",
    config: Optional[dict[str, Any]] = None,
    scopes: Optional[list[str]] = None,
    ctx: Optional[TenantContext] = None,
) -> dict[str, Any]:
    """Connect or reconfigure an integration.

    ``config`` must not contain credentials; those go to the secret store and are
    referenced by ``credential_ref``. A caller that passes something secret-shaped
    has it stripped here rather than persisted in a readable column.
    """
    from bizos.util.redaction import is_secret_key

    tenant = ctx or require_context()
    safe_config = {k: v for k, v in (config or {}).items() if not is_secret_key(k)}
    with workspace_connection(tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO integrations (id, category, provider, display_name, status, enabled, "
                "config, scopes, connected_by, connected_at) VALUES "
                "(:i, :c, :p, :d, :s, :e, CAST(:cf AS JSONB), :sc, :u, NOW()) "
                "ON CONFLICT (category, provider) DO UPDATE SET display_name = EXCLUDED.display_name, "
                "status = EXCLUDED.status, enabled = EXCLUDED.enabled, config = EXCLUDED.config, "
                "scopes = EXCLUDED.scopes, connected_by = EXCLUDED.connected_by, updated_at = NOW()"
            ),
            {
                "i": new_id("int"),
                "c": category,
                "p": provider,
                "d": display_name or f"{provider} {category}",
                "s": status,
                "e": enabled,
                "cf": json.dumps(safe_config, default=str),
                "sc": list(scopes or []),
                "u": tenant.user_id,
            },
        )
    return {"category": category, "provider": provider, "status": status, "enabled": enabled}


def health_check(ctx: Optional[TenantContext] = None) -> list[dict[str, Any]]:
    """Ping every connected integration and record the result."""
    tenant = ctx or require_context()
    results: list[dict[str, Any]] = []
    for category in sorted(connected_categories(tenant)):
        try:
            outcome = get_connector(category, ctx=tenant).health()
            entry = {"category": category, "ok": outcome.ok, "summary": outcome.summary, "error": outcome.error}
        except Exception as exc:
            entry = {"category": category, "ok": False, "summary": "", "error": str(exc)}
        with workspace_connection(tenant) as conn:
            conn.execute(
                text(
                    "UPDATE integrations SET last_check_at = NOW(), last_error = :e "
                    "WHERE category = :c AND enabled = TRUE"
                ),
                {"e": entry["error"], "c": category},
            )
        results.append(entry)
    return results
