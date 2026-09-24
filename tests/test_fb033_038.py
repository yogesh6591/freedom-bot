"""FB-033 – FB-038 coverage: client walls, one chat, paid domains, fact vs estimate,
restricted data, per-tool modes and audit retention."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from bizos.control.models import ClientSettings
from bizos.memory import store as memory
from bizos.policy.engine import evaluate
from bizos.policy.models import PolicyRequest
from bizos.tenancy import provisioning
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection
from bizos.tools.base import guarded_call
from bizos.types import (
    ApprovalStatus,
    ExecutionMode,
    MemoryCategory,
    PolicyEffect,
    SourceType,
)


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, client, prefix: str) -> str:
    response = app_client.post(
        "/api/auth/login",
        json={
            "email": f"{prefix}@{client.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client.slug,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(scope, tool, **kw) -> PolicyRequest:
    return PolicyRequest(
        ctx=scope.ctx, settings=scope.settings, tool=tool, domain=scope.domain,
        requested_mode=scope.requested_mode, connected_integrations=scope.integrations, **kw,
    )


# ------------------------------------------------------------------ FB-033


def test_every_workspace_table_carries_the_client_wall(client_a, client_b, ctx_factory):
    for client in (client_a, client_b):
        assert provisioning.unwalled_tables(ctx_factory(client, "admin")) == []


def test_a_row_for_another_client_is_refused(client_a, client_b, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with pytest.raises(IntegrityError):
        with workspace_connection(ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO memory_items (id, category, memory_key, title, created_by, client_id) "
                    "VALUES ('mem_wall', 'FACT', 'wall_probe', 'x', 'pytest', :c)"
                ),
                {"c": client_b.id},
            )
    # Without an explicit value the row is stamped with the owning client.
    with workspace_connection(ctx) as conn:
        owner = conn.execute(text("SELECT DISTINCT client_id FROM memory_items")).scalars().all()
    assert owner == [client_a.id]


def test_pinned_deployment_refuses_other_clients(app_client, client_a, client_b, monkeypatch):
    token_a = _login(app_client, client_a, "operator")
    monkeypatch.setenv("CLIENT_SLUG", client_b.slug)
    assert app_client.get("/api/auth/me", headers=_auth(token_a)).status_code == 403
    refused = app_client.post(
        "/api/auth/login",
        json={
            "email": f"operator@{client_a.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client_a.slug,
        },
    )
    assert refused.status_code == 401
    monkeypatch.delenv("CLIENT_SLUG")
    assert app_client.get("/api/auth/me", headers=_auth(token_a)).status_code == 200


def test_template_clone_gives_base_package_and_tool_modes():
    from bizos import template

    config = template.merge(template.load_template(), {"purchased_domains": ["general", "sales"]})
    settings = template.settings_from(config, company_name="Clone Co")
    assert settings.purchased_domains == ["general", "sales"]
    assert settings.enabled_domains == ["general", "sales"]
    assert settings.tool_modes["memory_search"] == "AUTO_WITHIN_SCOPE"
    assert settings.tool_modes["email_send_message"] == "WAIT_FOR_APPROVAL"
    assert settings.tool_modes["crm_update_contact_field"] == "WAIT_FOR_APPROVAL"
    # The template itself is not modified by a client's overrides.
    assert template.load_template()["purchased_domains"] == [
        "general", "operations", "sales", "intake", "planning",
    ]


def test_provision_client_script_builds_a_walled_deployment(tmp_path):
    from scripts.provision_client import provision
    from bizos.control import store as control
    from bizos.control.db import control_connection
    from bizos.util.ids import new_id

    slug = f"prov{new_id('x')[-6:]}".lower()
    args = argparse.Namespace(
        name=f"Provisioned {slug}", slug=slug, config=None, admin_email=f"owner@{slug}.example.com",
        admin_password="a-strong-test-password", out=str(tmp_path), db_host="db",
        api_port=8199, ui_port=3199,
    )
    report = provision(args)
    try:
        assert report["created"] is True
        assert report["seeded_memory"] >= 1
        env = (tmp_path / slug / "client.env").read_text()
        assert f"CLIENT_SLUG={slug}" in env
        assert (tmp_path / slug / "compose.yaml").exists()
        client = control.get_client_by_slug(slug)
        assert client.db_name != ""
        assert "finance" not in client.settings.enabled_domains
    finally:
        client = control.get_client_by_slug(slug)
        provisioning.deprovision_client(client)
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": client.id})


# ------------------------------------------------------------------ FB-034


def test_persona_never_presents_as_jeanne(client_a, scope_factory):
    from bizos.agents import instructions
    from bizos.agents.persona import enforce_persona

    scope = scope_factory(client_a, "operator")
    prompt = instructions.render(
        ctx=scope.ctx, settings=scope.settings, mode=scope.mode, domain="general"
    )
    assert "You are not Jeanne" in prompt
    assert "do not ask them to\nrepeat context" in prompt or "repeat context" in prompt

    fixed, changed = enforce_persona("Hello, I'm Jeanne.\nRegards,\nJeanne", "FreedomBot")
    assert changed and "Jeanne" not in fixed
    untouched, changed = enforce_persona("Jeanne asked for the report.", "FreedomBot")
    assert not changed and untouched == "Jeanne asked for the report."


def test_one_chat_routes_between_domains():
    from bizos.agents.persona import route

    enabled = ["general", "strategy", "operations", "finance"]
    assert route("What are this quarter's strategic priorities?", enabled) == "strategy"
    assert route("Draft the handoff checklist for the new process", enabled) == "operations"
    assert route("What refund terms apply to that invoice?", enabled) == "finance"
    # A short follow-up stays where the conversation was.
    assert route("ok, go ahead", enabled, previous="finance") == "finance"
    # A domain the client has not enabled is never chosen.
    assert route("What does the contract clause say?", enabled) == "general"


# ------------------------------------------------------------------ FB-035


def test_unpaid_domain_needs_a_change_order(app_client, client_a):
    from bizos.control import store as control

    original = control.get_client(client_a.id).settings.to_dict()
    admin = _login(app_client, client_a, "admin")
    try:
        refused = app_client.patch("/api/domains/legal", json={"enabled": True}, headers=_auth(admin))
        assert refused.status_code == 409
        refused = app_client.put(
            "/api/policies/settings",
            json={"enabled_domains": ["general", "legal"]},
            headers=_auth(admin),
        )
        assert refused.status_code == 409
        ordered = app_client.post(
            "/api/domains/legal/change-order",
            json={"reference": "CO-001", "note": "Legal add-on"},
            headers=_auth(admin),
        )
        assert ordered.status_code == 200, ordered.text
        assert "legal" in ordered.json()["purchased_domains"]
        assert "legal" in ordered.json()["enabled_domains"]
        listed = app_client.get("/api/domains", headers=_auth(admin)).json()["items"]
        assert next(d for d in listed if d["name"] == "legal")["purchased"] is True
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )


# ------------------------------------------------------------------ FB-036


def test_results_are_labelled_fact_or_estimate(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        memory.put(
            category=MemoryCategory.FACT, memory_key="fb036_guess", title="Churn guess",
            content="Churn is probably around 4%", source_type=SourceType.AI_INFERENCE,
            confidence=0.4, settings=client_a.settings, ctx=ctx,
        )
        hits = memory.search("", authoritative_only=False, limit=100, ctx=ctx)
    labels = {i.memory_key: i.label for i in hits}
    assert labels["fb036_guess"] == "estimate"
    assert labels["refund_period"] == "fact"
    # Approved values outrank unapproved ones in retrieval.
    statuses = [str(i.current.approval_status) for i in hits]
    assert statuses == sorted(statuses, key=lambda s: 0 if s == "APPROVED" else 1)


def test_conflicting_policies_escalate_to_a_person(client_a, ctx_factory, scope_factory):
    admin = ctx_factory(client_a, "admin")
    drafter = ctx_factory(client_a, "drafter")
    approver = ctx_factory(client_a, "approver")
    with tenant_scope(admin):
        first = memory.put(
            category=MemoryCategory.FACT, memory_key="fb036_cap_a", title="Discount cap (sales)",
            content="Max discount 10%", attributes={"topic": "fb036 discount cap"},
            settings=client_a.settings, ctx=admin,
        )
        second = memory.put(
            category=MemoryCategory.FACT, memory_key="fb036_cap_b", title="Discount cap (finance)",
            content="Max discount 20%", attributes={"topic": "fb036 discount cap"},
            settings=client_a.settings, ctx=admin,
        )
    assert first.current.approval_status == ApprovalStatus.APPROVED
    # The second, disagreeing value is not allowed to become authoritative on its own.
    assert second.current.approval_status == ApprovalStatus.PENDING
    assert first.id in second.current.attributes["conflict_with"]
    with tenant_scope(admin):
        topics = {c["topic"] for c in memory.find_conflicts(ctx=admin)}
    assert "fb036 discount cap" in topics

    scope = scope_factory(client_a, "operator", domain="general")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "memory_search", {"query": "discount cap", "authoritative_only": False})
        assert "CONFLICT" in outcome.message
        escalation = guarded_call(
            scope, "memory_escalate_conflict",
            {"topic": "fb036 discount cap", "question": "10% or 20%?", "item_ids": [first.id, second.id]},
        )
    assert escalation.effect == PolicyEffect.REQUIRE_APPROVAL

    with tenant_scope(drafter), pytest.raises(PermissionError):
        memory.approve_version(second.current.id, ctx=drafter)
    with tenant_scope(approver):
        memory.approve_version(second.current.id, ctx=approver)
        remaining = {c["topic"] for c in memory.find_conflicts(ctx=approver)}
        loser = memory.history(first.id, ctx=approver)
    assert "fb036 discount cap" not in remaining
    assert loser.current is None  # superseded, kept in history
    assert len(loser.versions) == 1


# ------------------------------------------------------------------ FB-037


def test_restricted_rows_never_return_to_the_wrong_user(client_a, ctx_factory):
    operator = ctx_factory(client_a, "operator")  # finance only
    approver = ctx_factory(client_a, "approver")  # finance, legal, exec
    admin = ctx_factory(client_a, "admin")
    with tenant_scope(operator):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=100, ctx=operator)}
        listed = {i.memory_key for i in memory.list_items(ctx=operator)}
        direct = memory.get_by_key(MemoryCategory.FACT, "salary_bands", ctx=operator)
    assert "salary_bands" not in keys and "salary_bands" not in listed and direct is None
    assert "board_q3_plan" not in keys
    with tenant_scope(approver):
        keys = {i.memory_key for i in memory.search("", authoritative_only=False, limit=100, ctx=approver)}
    assert "board_q3_plan" in keys and "salary_bands" not in keys
    with tenant_scope(admin):
        item = memory.get_by_key(MemoryCategory.FACT, "salary_bands", ctx=admin)
    assert item is not None and item.access_area == "salary"
    with tenant_scope(operator), pytest.raises(memory.MemoryNotFound):
        memory.history(item.id, ctx=operator)


def test_restricted_items_404_over_the_api(app_client, client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    with tenant_scope(admin):
        item = memory.get_by_key(MemoryCategory.FACT, "salary_bands", ctx=admin)
    viewer = _login(app_client, client_a, "viewer")
    assert app_client.get(f"/api/memory/{item.id}", headers=_auth(viewer)).status_code == 404
    token = _login(app_client, client_a, "admin")
    assert app_client.get(f"/api/memory/{item.id}", headers=_auth(token)).status_code == 200


def test_data_area_tools_follow_user_scopes(client_a, scope_factory, settings_with):
    settings = settings_with(client_a.settings, enabled_domains=["general", "finance", "legal"])
    operator = scope_factory(client_a, "operator", domain="finance", settings=settings)
    drafter = scope_factory(client_a, "drafter", domain="finance", settings=settings)
    legal_scope = scope_factory(client_a, "operator", domain="legal", settings=settings)
    with tenant_scope(operator.ctx):
        assert evaluate(_request(operator, "finance_propose_adjustment")).rule != "DATA_AREA_RESTRICTED"
        legal = evaluate(_request(legal_scope, "legal_find_clause"))
    assert legal.effect == PolicyEffect.DENY and legal.rule == "DATA_AREA_RESTRICTED"
    with tenant_scope(drafter.ctx):
        blocked = evaluate(_request(drafter, "finance_propose_adjustment"))
    assert blocked.effect == PolicyEffect.DENY


def test_view_and_run_are_separate_on_every_tool():
    from bizos.rbac.registry import TOOL_SPECS

    for spec in TOOL_SPECS:
        data = spec.to_dict()
        assert set(data["view"]) == set(data["run"])
    email = next(s for s in TOOL_SPECS if s.name == "email_send_message").to_dict()
    assert email["view"]["VIEWER"] is True and email["run"]["VIEWER"] is False


def test_admin_can_grant_data_scopes(app_client, client_a):
    from bizos.control import store as control

    admin = _login(app_client, client_a, "admin")
    users = app_client.get("/api/users", headers=_auth(admin)).json()["items"]
    drafter = next(u for u in users if u["email"].startswith("drafter@"))
    try:
        bad = app_client.patch(
            f"/api/users/{drafter['id']}/scopes", json={"data_scopes": ["gossip"]}, headers=_auth(admin)
        )
        assert bad.status_code == 400
        ok = app_client.patch(
            f"/api/users/{drafter['id']}/scopes", json={"data_scopes": ["hr"]}, headers=_auth(admin)
        )
        assert ok.status_code == 200 and ok.json()["data_scopes"] == ["hr"]
    finally:
        control.set_user_scopes(drafter["id"], [])


# ------------------------------------------------------------------ FB-038


def test_per_tool_mode_narrows_but_never_widens(client_a, scope_factory, settings_with):
    auto = settings_with(
        client_a.settings,
        default_execution_mode=ExecutionMode.AUTO_WITHIN_SCOPE.value,
        risk_policy={"auto_allowed_max_risk": "HIGH", "always_approve_at_or_above": "CRITICAL"},
        tool_modes={"crm_create_note": "WAIT_FOR_APPROVAL"},
    )
    scope = scope_factory(client_a, "operator", domain="sales", settings=auto)
    with tenant_scope(scope.ctx):
        decision = evaluate(_request(scope, "crm_create_note"))
    assert decision.effect == PolicyEffect.REQUIRE_APPROVAL
    assert decision.mode == ExecutionMode.WAIT_FOR_APPROVAL

    advise = settings_with(
        client_a.settings,
        default_execution_mode=ExecutionMode.ADVISE.value,
        tool_modes={"crm_create_note": "AUTO_WITHIN_SCOPE"},
    )
    scope = scope_factory(client_a, "operator", domain="sales", settings=advise)
    with tenant_scope(scope.ctx):
        assert evaluate(_request(scope, "crm_create_note")).effect == PolicyEffect.DENY
        # Reads are never held back by mode.
        assert evaluate(_request(scope, "memory_search")).effect == PolicyEffect.ALLOW


def test_audit_rows_record_who_what_tool_and_mode(client_a, scope_factory):
    from bizos.audit import events as audit

    scope = scope_factory(client_a, "operator", domain="sales")
    with tenant_scope(scope.ctx):
        guarded_call(scope, "memory_search", {"query": "refund"})
        rows = audit.search(ctx=scope.ctx, event_types=["POLICY_DECISION"], tool="memory_search", limit=1)
    row = rows[0]
    assert row["user_id"] == scope.ctx.user_id
    assert row["actor_role"] == "OPERATOR"
    assert row["tool"] == "memory_search"
    assert row["execution_mode"] == str(scope.mode)


def test_retention_purge_is_the_only_deletion_allowed(client_a, ctx_factory):
    from bizos.audit import events as audit

    ctx = ctx_factory(client_a, "admin")
    old = datetime.now(timezone.utc) - timedelta(days=400)
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO audit_events (id, event_type, client_id, created_at) "
                "VALUES ('aud_fb038_old', 'LOGIN', :c, :t)"
            ),
            {"c": client_a.id, "t": old},
        )
    # Outside the purge, the log is still append-only.
    with pytest.raises(Exception):
        with workspace_connection(ctx) as conn:
            conn.execute(text("DELETE FROM audit_events WHERE id = 'aud_fb038_old'"))
    with pytest.raises(Exception):
        with workspace_connection(ctx) as conn:
            conn.execute(text("UPDATE audit_events SET status = 'x' WHERE id = 'aud_fb038_old'"))

    with tenant_scope(ctx):
        result = audit.purge_expired(1, ctx=ctx)  # clamped to the 30-day floor
    assert result["retention_days"] == audit.MIN_AUDIT_RETENTION_DAYS
    assert result["deleted"] >= 1
    with workspace_connection(ctx) as conn:
        assert conn.execute(text("SELECT count(*) FROM audit_events WHERE id = 'aud_fb038_old'")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'AUDIT_PURGED'")).scalar() >= 1
