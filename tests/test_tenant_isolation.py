"""
§30 — Tenant isolation.

    Client A records "Pricing discount = 15%".
    Client B asks "What is our discount?".
    Client B must never receive Client A's information.

The test asserts the stronger property that makes that outcome inevitable: the
two clients' data lives in different Postgres namespaces, and there is no engine
in the process that can see both.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from bizos.memory import store as memory
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.context import NoTenantContextError
from bizos.tenancy.registry import readonly_connection, registry, workspace_location
from bizos.tools.base import guarded_call
from bizos.types import DeploymentType, MemoryCategory


def test_workspaces_are_physically_separate(client_a, client_b, ctx_factory):
    """A and B resolve to different databases or schemas, not a shared table."""
    a = workspace_location(ctx_factory(client_a, "admin"))
    b = workspace_location(ctx_factory(client_b, "admin"))
    assert a != b
    assert client_a.deployment_type == DeploymentType.DEDICATED_DB
    assert client_b.deployment_type == DeploymentType.DEDICATED_SCHEMA
    # Isolation is physical; the client_id column inside each workspace (FB-033)
    # is a second wall pinned to the one owning client, not a shared-table filter.
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx), readonly_connection(ctx) as conn:
        columns = {
            r.column_name
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'memory_items'"
                )
            ).fetchall()
        }
    assert "tenant_id" not in columns and "client_id" in columns


def test_memory_never_crosses_clients(client_a, client_b, ctx_factory, scope_factory):
    """The §30 scenario, end to end."""
    ctx_a = ctx_factory(client_a, "admin")
    ctx_b = ctx_factory(client_b, "admin")

    with tenant_scope(ctx_a):
        memory.put(
            category=MemoryCategory.FACT,
            memory_key="pricing_discount",
            title="Pricing discount",
            content="Pricing discount = 15%",
            settings=client_a.settings,
            ctx=ctx_a,
        )

    # A sees it.
    with tenant_scope(ctx_a):
        hits_a = memory.search("discount", ctx=ctx_a)
    assert any("15%" in (i.current.content or "") for i in hits_a)

    # B does not — B has its own, different discount policy.
    with tenant_scope(ctx_b):
        hits_b = memory.search("discount", ctx=ctx_b)
    contents_b = [i.current.content for i in hits_b if i.current]
    assert all("15%" not in (c or "") for c in contents_b), contents_b
    assert any("5%" in (c or "") for c in contents_b), "B should still see its own value"


def test_crm_records_never_cross_clients(client_a, client_b, scope_factory):
    """A CRM read in B cannot return A's contacts."""
    scope_a = scope_factory(client_a, "operator")
    scope_b = scope_factory(client_b, "operator")

    with tenant_scope(scope_a.ctx):
        a_contacts = guarded_call(scope_a, "crm_search_contacts", {"query": ""}).data
    with tenant_scope(scope_b.ctx):
        b_contacts = guarded_call(scope_b, "crm_search_contacts", {"query": ""}).data

    emails_a = {c["email"] for c in a_contacts}
    emails_b = {c["email"] for c in b_contacts}
    assert emails_a and emails_b
    assert emails_a.isdisjoint(emails_b)

    # Searching B for one of A's contacts by email returns nothing.
    target = next(iter(emails_a))
    with tenant_scope(scope_b.ctx):
        leaked = guarded_call(scope_b, "crm_search_contacts", {"query": target}).data
    assert leaked == []


def test_audit_trails_are_isolated(client_a, client_b, ctx_factory):
    """One client's audit log is not readable from another's workspace."""
    from bizos.audit import events as audit

    ctx_a = ctx_factory(client_a, "admin")
    ctx_b = ctx_factory(client_b, "admin")
    with tenant_scope(ctx_a):
        rows_a = audit.search(ctx=ctx_a, limit=500)
    with tenant_scope(ctx_b):
        rows_b = audit.search(ctx=ctx_b, limit=500)
    assert {r["client_id"] for r in rows_a} <= {client_a.id}
    assert {r["client_id"] for r in rows_b} <= {client_b.id}


def test_workspace_access_requires_a_bound_tenant():
    """Reaching customer data with no resolved client raises rather than guessing."""
    from bizos.tenancy.registry import workspace_connection

    with pytest.raises(NoTenantContextError):
        with workspace_connection():
            pass


def test_engine_cache_is_keyed_per_workspace(client_a, client_b, ctx_factory):
    """The two clients get different engine objects, not a shared one."""
    engine_a = registry.engine_for(ctx_factory(client_a, "admin"))
    engine_b = registry.engine_for(ctx_factory(client_b, "admin"))
    assert engine_a is not engine_b
