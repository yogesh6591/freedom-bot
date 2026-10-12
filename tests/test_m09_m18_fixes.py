"""
QA run 2026-09-30 fixes, Modules 9-18:

* M09-11/13/14 — alerts are detected from the client's own data, rare, and
  shown only to people who hold the area they touch.
* M11-08/09/10/13/19 — the assessment handoff asks for what is missing, flags
  contradictions, and never records a client's belief as a fact.
* M12-12 — an SOP can be removed.
* M13-01 / M15-20 — the owner view is for platform owners only.
* M14-18 — a resubmitted ticket merges into the open one.
* M17-08 — a zero price and a blank autonomy level are refused.
* M18-10/11/16 — baselines are validated and compared.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bizos import chat_history, insights, support
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DOMAIN_NAMES
from bizos.memory import store as memory
from bizos.onboarding import review_assessment
from bizos.pricing import DEFAULT_RULES, calculate_consult_quote, save_rules
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection
from bizos.tools.base import guarded_call
from bizos.types import MemoryCategory


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, email: str, slug: str) -> dict:
    r = app_client.post(
        "/api/auth/login",
        json={"email": email, "password": "bizos-dev-password", "client_slug": slug},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _restore(client, original: dict) -> None:
    control.update_client_settings(client.id, ClientSettings.from_dict(original), updated_by="pytest")


# ---------------------------------------------------------------- M13-01 / M15-20


def test_client_admin_cannot_use_owner_view(app_client, client_a, client_b):
    headers = _login(app_client, f"admin@{client_a.slug}.example.com", client_a.slug)
    assert app_client.get("/api/owner/portfolio", headers=headers).status_code == 403
    r = app_client.put(
        f"/api/owner/portfolio/{client_b.id}",
        json={"implementation_status": "blocked", "account_risk": "at_risk"},
        headers=headers,
    )
    assert r.status_code == 403
    me = app_client.get("/api/auth/me", headers=headers).json()
    assert me["user"]["is_platform_owner"] is False


def test_platform_owner_sees_clients_but_not_internal_workspace(app_client, client_a, client_b):
    internal_settings = ClientSettings()
    internal_settings.internal_workspace = True
    import uuid

    slug = f"cepochqa{uuid.uuid4().hex[:6]}"
    internal = control.create_client(
        company_name="Cepoch QA internal", slug=slug, settings=internal_settings, created_by="pytest"
    )
    from bizos.tenancy import provisioning

    provisioning.provision_client(internal)
    try:
        user = control.create_user(
            client_id=internal.id,
            email=f"owner@{slug}.example.com",
            password="bizos-dev-password",
            display_name="Owner",
            roles=(__import__("bizos.types", fromlist=["Role"]).Role.ADMIN,),
            created_by="pytest",
        )
        control.set_platform_owner(user.id, True)
        headers = _login(app_client, user.email, slug)
        body = app_client.get("/api/owner/portfolio", headers=headers).json()
        slugs = {c["slug"] for c in body["clients"]}
        assert {client_a.slug, client_b.slug} <= slugs
        assert slug not in slugs
        assert app_client.get("/api/auth/me", headers=headers).json()["user"]["is_platform_owner"]
    finally:
        provisioning.deprovision_client(internal)
        from bizos.control.db import control_connection

        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE id = :i"), {"i": internal.id})


# ---------------------------------------------------------------- M14-18


def test_duplicate_ticket_merges(client_a):
    first, merged1 = support.submit_ticket(
        client_id=client_a.id, subject="QA: Export button broken!", body="first", created_by="pytest"
    )
    second, merged2 = support.submit_ticket(
        client_id=client_a.id, subject="qa export button broken", body="again", created_by="pytest"
    )
    assert not merged1 and merged2
    assert first["id"] == second["id"]
    assert any(h["event"] == "duplicate_merged" for h in second["history"])
    # Once resolved, the same subject opens a fresh ticket.
    support.set_status(ticket_id=first["id"], status="RESOLVED", by="pytest")
    third, merged3 = support.submit_ticket(
        client_id=client_a.id, subject="QA: Export button broken!", created_by="pytest"
    )
    assert not merged3 and third["id"] != first["id"]


# ---------------------------------------------------------------- M17-08 / M17-11


def test_zero_price_and_blank_autonomy_are_refused(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with pytest.raises(ValueError, match="greater than zero"):
            save_rules(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                rules={"customer_price_usd": 0, "cepoch_cost_usd": 0},
                ctx=ctx,
            )
        priced = ClientSettings.from_dict({**original, "resale_pricing": dict(DEFAULT_RULES)})
        with pytest.raises(ValueError, match="autonomy is required"):
            calculate_consult_quote(priced, domains=["general"], autonomy="")
    finally:
        _restore(client_a, original)


# ---------------------------------------------------------------- M18


def test_baselines_are_validated_and_compared(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        for bad in ("-5", "abc"):
            with pytest.raises(ValueError):
                insights.save_baselines(
                    client_id=client_a.id,
                    settings=control.get_client(client_a.id).settings,
                    baselines=[{"metric": "Cycle time", "before_value": bad}],
                    ctx=ctx,
                )
        saved = insights.save_baselines(
            client_id=client_a.id,
            settings=control.get_client(client_a.id).settings,
            baselines=[
                {"id": "c", "metric": "Cycle time", "before_value": "12", "after_value": "7", "unit": "days"},
                {"id": "h", "metric": "Manual hours", "before_value": "80", "after_value": "35", "unit": "hours"},
                {"id": "m", "metric": "Rework", "before_value": "14", "after_value": "", "unit": "invoices"},
            ],
            ctx=ctx,
        )
        by_id = {b["id"]: b for b in insights.list_baselines(control.get_client(client_a.id).settings)}
        assert by_id["c"]["change"] == -5 and by_id["c"]["change_percent"] == -41.7
        assert by_id["c"]["comparison"] == "-5 days (-41.7%)"
        assert by_id["h"]["change"] == -45
        assert by_id["m"]["change"] is None and "waiting" in by_id["m"]["comparison"]
        assert saved[0]["comparison"] == "-5 days (-41.7%)"
    finally:
        _restore(client_a, original)


# ---------------------------------------------------------------- M09


def _backdate_finance_approvals(client_a, scope_factory, settings_with, n: int) -> list[str]:
    settings = settings_with(
        client_a.settings, enabled_domains=["general", "finance"], purchased_domains=list(DOMAIN_NAMES)
    )
    scope = scope_factory(client_a, "operator", domain="finance", settings=settings)
    ids = []
    with tenant_scope(scope.ctx):
        for i in range(n):
            outcome = guarded_call(
                scope,
                "finance_propose_adjustment",
                {"customer": "Acme Logistics", "amount": 100 + i, "reason": f"QA delay {i}", "kind": "credit"},
            )
            ids.append(outcome.action_id)
        with workspace_connection(scope.ctx) as conn:
            conn.execute(
                text(
                    "UPDATE approval_requests SET created_at = NOW() - INTERVAL '5 days' "
                    "WHERE action_id = ANY(:ids)"
                ),
                {"ids": ids},
            )
    return ids


def test_alerts_come_from_client_data_and_respect_areas(
    client_a, scope_factory, settings_with, ctx_factory
):
    original = control.get_client(client_a.id).settings.to_dict()
    admin = ctx_factory(client_a, "admin")
    try:
        _restore(client_a, {**original, "onboarding": {**(original.get("onboarding") or {}), "inefficiency_alerts": []}})
        # Nothing recurring yet: no alerts, not even demo text.
        with tenant_scope(admin):
            alerts = insights.scan_alerts(
                client_id=client_a.id, settings=control.get_client(client_a.id).settings, ctx=admin
            )
        assert [a for a in alerts if a.get("status") == "open"] == []

        _backdate_finance_approvals(client_a, scope_factory, settings_with, 3)
        with tenant_scope(admin):
            alerts = insights.scan_alerts(
                client_id=client_a.id, settings=control.get_client(client_a.id).settings, ctx=admin
            )
        delay = next(a for a in alerts if a["id"] == "scan_approval_delay_finance")
        assert delay["status"] == "open" and delay["source"] == "scan"
        assert delay["evidence"]["count"] >= 3
        assert "directional" in delay["directional_cost"]

        settings = control.get_client(client_a.id).settings
        # M09-14: the finance alert is hidden from people without finance.
        assert all(a["id"] != delay["id"] for a in insights.open_alerts(settings, ctx_factory(client_a, "employee")))
        assert any(a["id"] == delay["id"] for a in insights.open_alerts(settings, ctx_factory(client_a, "finance")))

        # Dismissed: stays quiet on the next scan (no spam).
        with tenant_scope(admin):
            insights.dismiss_alert(client_id=client_a.id, settings=settings, alert_id=delay["id"], ctx=admin)
            again = insights.scan_alerts(
                client_id=client_a.id, settings=control.get_client(client_a.id).settings, ctx=admin
            )
        assert next(a for a in again if a["id"] == delay["id"])["status"] == "dismissed"
    finally:
        _restore(client_a, original)


def test_repeated_question_alert_and_cap(client_a, ctx_factory):
    original = control.get_client(client_a.id).settings.to_dict()
    admin = ctx_factory(client_a, "admin")
    try:
        _restore(client_a, {**original, "onboarding": {**(original.get("onboarding") or {}), "inefficiency_alerts": []}})
        people = [ctx_factory(client_a, p) for p in ("viewer", "drafter", "operator")]
        for i, person in enumerate(people):
            chat_history.record_turn(
                person,
                session_id=f"ses_qa_repeat_{i}",
                user_message="How do we reconcile the carrier freight invoices?",
                reply="Not recorded.",
                domain="general",
                meta={},
            )
        with tenant_scope(admin):
            alerts = insights.scan_alerts(
                client_id=client_a.id, settings=control.get_client(client_a.id).settings, ctx=admin
            )
        repeated = [a for a in alerts if a.get("kind") == "repeated_question"]
        assert repeated and "SOP" in repeated[0]["detail"]
        assert sum(1 for a in alerts if a.get("status") == "open") <= insights.MAX_OPEN_ALERTS
    finally:
        _restore(client_a, original)


# ---------------------------------------------------------------- M11


def test_assessment_review_asks_flags_and_does_not_assume():
    review = review_assessment(
        {
            "goals": ["Cut invoice cycle time by 30%"],
            "pain": "Invoices take 12 days",
            "headcount": 25,
            "employees": "40",
            "assumption": "Client believes AI can fully automate approvals",
        }
    )
    joined = " ".join(review["open_questions"])
    assert "(systems)" in joined and "(permissions)" in joined and "(approval authority)" in joined
    assert "(goals)" not in joined
    assert review["inconsistencies"] and "employee count" in review["inconsistencies"][0]
    assert review["unverified"] == ["Client believes AI can fully automate approvals"]


def test_handoff_invents_no_systems_and_keeps_beliefs_unverified(client_b, ctx_factory):
    from bizos.onboarding import OnboardingProfile, apply_profile

    ctx = ctx_factory(client_b, "admin")
    original = control.get_client(client_b.id).settings.to_dict()
    assessment = {
        "goals": ["Cut invoice cycle time by 30%"],
        "pain": "Invoices take 12 days to go out",
        "root_cause": "Manual copy from job tickets",
        "assumption": "Client believes AI can fully automate approvals",
    }
    try:
        with tenant_scope(ctx):
            result = insights.handoff_assessment(
                client_id=client_b.id,
                settings=control.get_client(client_b.id).settings,
                assessment=assessment,
                ctx=ctx,
            )
            assert result["profile"]["systems"] == []
            assert any("(systems)" in q for q in result["needs_clarification"]["open_questions"])

            profile = OnboardingProfile.from_dict(result["profile"])
            applied = apply_profile(
                client_id=client_b.id,
                profile=profile,
                settings=control.get_client(client_b.id).settings,
                ctx=ctx,
            )
            assert applied["needs_clarification"]["unverified"]
            # The belief is PENDING, so it is never an authoritative answer.
            hits = memory.search("automate approvals", authoritative_only=True, ctx=ctx)
            assert all("fully automate" not in (h.current.content if h.current else "") for h in hits)
            belief = memory.get_by_key(MemoryCategory.FACT, "assessment_unverified_assumptions", ctx=ctx)
            assert belief is not None and str(belief.current.approval_status) == "PENDING"
            pain = memory.get_by_key(MemoryCategory.FACT, "assessment_pain_point", ctx=ctx)
            assert pain is not None and "12 days" in pain.current.content
    finally:
        _restore(client_b, original)


# ---------------------------------------------------------------- M12-12


def test_sop_can_be_removed(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        sop = memory.put(
            category=MemoryCategory.SOP,
            memory_key="qa_remove_sop",
            title="QA workflow to retire",
            content="1) Old step. 2) Another old step.",
            ctx=ctx,
        )
        memory.archive(sop.id, reason="Workflow retired", ctx=ctx)
        assert all(i.id != sop.id for i in memory.list_items(category=MemoryCategory.SOP, limit=500, ctx=ctx))
        assert memory.get_by_key(MemoryCategory.SOP, "qa_remove_sop", ctx=ctx) is None
