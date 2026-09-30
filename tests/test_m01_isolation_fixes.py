"""
Module 1 isolation fixes (QA run 2026-09-30).

* M01-06 — a tab showing one workspace is refused when the shared cookie now
  belongs to another client or user.
* M01-11 — chat conversations are saved per workspace and per user and can be
  listed and reopened.
* M01-12 — a memory item can be removed (archived) and stops being used.
* M01-20 — a new client does not inherit another client's alerts, baselines or
  price.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bizos import chat_history
from bizos.control import store as control
from bizos.insights import list_alerts, list_baselines
from bizos.memory import store as memory
from bizos.pricing import PricingNotConfigured, calculate_consult_quote, get_rules
from bizos.tenancy.context import tenant_scope
from bizos.types import MemoryCategory


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, client, prefix: str) -> dict:
    response = app_client.post(
        "/api/auth/login",
        json={
            "email": f"{prefix}@{client.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client.slug,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(login: dict, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {login['access_token']}", **extra}


# ---------------------------------------------------------------- M01-06


def test_stale_tab_is_refused_for_another_client(app_client, client_a, client_b):
    a = _login(app_client, client_a, "operator")
    b = _login(app_client, client_b, "operator")
    # Tab still showing client A, but the cookie/token now belongs to client B.
    response = app_client.get(
        "/api/memory",
        headers=_auth(b, **{"X-Bizos-Client": a["client"]["id"], "X-Bizos-User": a["user"]["id"]}),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "workspace_changed"


def test_stale_tab_is_refused_for_another_user_same_client(app_client, client_a):
    op = _login(app_client, client_a, "operator")
    viewer = _login(app_client, client_a, "viewer")
    response = app_client.get(
        "/api/memory",
        headers=_auth(viewer, **{"X-Bizos-Client": op["client"]["id"], "X-Bizos-User": op["user"]["id"]}),
    )
    assert response.status_code == 409


def test_matching_tab_and_session_lookup_still_work(app_client, client_a):
    op = _login(app_client, client_a, "operator")
    headers = _auth(op, **{"X-Bizos-Client": op["client"]["id"], "X-Bizos-User": op["user"]["id"]})
    assert app_client.get("/api/memory", headers=headers).status_code == 200
    # /me without the headers is how a tab discovers the current session.
    assert app_client.get("/api/auth/me", headers=_auth(op)).status_code == 200


# ---------------------------------------------------------------- M01-11


def test_chat_history_is_per_user_and_per_workspace(client_a, client_b, ctx_factory):
    op = ctx_factory(client_a, "operator")
    viewer = ctx_factory(client_a, "viewer")
    other_client = ctx_factory(client_b, "operator")

    chat_history.record_turn(
        op,
        session_id="ses_qa_hist_1",
        user_message="What is our refund period?",
        reply="Fact: Refund period = 30 days.",
        domain="finance",
        meta={"mode": "WAIT_FOR_APPROVAL", "tool_activity": [], "awaiting_approval": [], "drafts": []},
    )
    chat_history.record_turn(
        op,
        session_id="ses_qa_hist_1",
        user_message="And payment terms?",
        reply="Fact: Net-30.",
        domain="finance",
        meta={},
    )

    sessions = chat_history.list_sessions(op)
    mine = next(s for s in sessions if s["session_id"] == "ses_qa_hist_1")
    assert mine["turns"] == 2
    assert mine["title"] == "What is our refund period?"

    detail = chat_history.get_session(op, "ses_qa_hist_1")
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][1]["content"] == "Fact: Refund period = 30 days."
    assert detail["messages"][1]["domain"] == "finance"

    # Another person in the same workspace cannot list, open or continue it.
    assert all(s["session_id"] != "ses_qa_hist_1" for s in chat_history.list_sessions(viewer))
    with pytest.raises(chat_history.SessionNotFound):
        chat_history.get_session(viewer, "ses_qa_hist_1")
    with pytest.raises(chat_history.SessionNotFound):
        chat_history.assert_can_continue(viewer, "ses_qa_hist_1")
    chat_history.assert_can_continue(op, "ses_qa_hist_1")

    # Another client's workspace has no trace of it.
    assert chat_history.list_sessions(other_client) == []
    with pytest.raises(chat_history.SessionNotFound):
        chat_history.get_session(other_client, "ses_qa_hist_1")


def test_chat_history_routes(app_client, client_a, ctx_factory):
    op_ctx = ctx_factory(client_a, "operator")
    chat_history.record_turn(
        op_ctx, session_id="ses_qa_hist_api", user_message="hello", reply="Hi.", domain="general", meta={}
    )
    op = _login(app_client, client_a, "operator")
    listed = app_client.get("/api/chat/sessions", headers=_auth(op)).json()["items"]
    assert any(s["session_id"] == "ses_qa_hist_api" for s in listed)
    opened = app_client.get("/api/chat/sessions/ses_qa_hist_api", headers=_auth(op))
    assert opened.status_code == 200
    viewer = _login(app_client, client_a, "viewer")
    assert app_client.get("/api/chat/sessions/ses_qa_hist_api", headers=_auth(viewer)).status_code == 404
    # Continuing someone else's conversation is refused before any model call.
    response = app_client.post(
        "/api/chat", json={"message": "hi", "session_id": "ses_qa_hist_api"}, headers=_auth(viewer)
    )
    assert response.status_code == 404


def test_chat_history_table_is_walled(client_a, ctx_factory):
    from bizos.tenancy.provisioning import unwalled_tables

    chat_history.ensure_table(ctx_factory(client_a, "operator"))
    assert "chat_messages" not in unwalled_tables(ctx_factory(client_a, "admin"))


# ---------------------------------------------------------------- M01-12


def test_archive_removes_item_from_use_but_keeps_history(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="qa_archive_me",
            title="QA archive me",
            content="Loading dock code is ZEBRA-QA-9.",
            ctx=ctx,
        )
        assert any(i.id == item.id for i in memory.search("ZEBRA-QA-9", ctx=ctx))

        archived = memory.archive(item.id, reason="Obsolete", ctx=ctx)
        assert archived.current is None
        assert archived.versions and archived.versions[0].status == "ARCHIVED"

        assert all(i.id != item.id for i in memory.list_items(limit=500, ctx=ctx))
        assert all(i.id != item.id for i in memory.search("ZEBRA-QA-9", ctx=ctx))
        assert memory.get_by_key(MemoryCategory.FACT, "qa_archive_me", ctx=ctx) is None
        assert memory.get_by_key(
            MemoryCategory.FACT, "qa_archive_me", ctx=ctx, include_archived=True
        ) is not None

        with pytest.raises(memory.MemoryNotFound):
            memory.archive(item.id, reason="again", ctx=ctx)

        # Recording the key again starts a fresh active version without colliding.
        again = memory.put(
            category=MemoryCategory.FACT,
            memory_key="qa_archive_me",
            title="QA archive me",
            content="Loading dock code is ZEBRA-QA-10.",
            ctx=ctx,
        )
        assert again.current is not None and again.current.version_no == 2


def test_archive_route_requires_approver(app_client, client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="qa_archive_route",
            title="QA archive route",
            content="Temporary.",
            ctx=ctx,
        )
    drafter = _login(app_client, client_a, "drafter")
    denied = app_client.post(
        f"/api/memory/{item.id}/archive", json={"reason": "x"}, headers=_auth(drafter)
    )
    assert denied.status_code == 403
    approver = _login(app_client, client_a, "approver")
    missing_reason = app_client.post(
        f"/api/memory/{item.id}/archive", json={"reason": ""}, headers=_auth(approver)
    )
    assert missing_reason.status_code == 422
    ok = app_client.post(
        f"/api/memory/{item.id}/archive", json={"reason": "No longer true"}, headers=_auth(approver)
    )
    assert ok.status_code == 200
    audit = app_client.get(
        "/api/audit", params={"event_type": "MEMORY_ARCHIVED"}, headers=_auth(approver)
    ).json()["items"]
    assert any(item.id in str(row.get("request")) for row in audit)


def test_archive_cannot_reach_another_client(client_a, client_b, ctx_factory):
    a_ctx = ctx_factory(client_a, "admin")
    b_ctx = ctx_factory(client_b, "admin")
    with tenant_scope(a_ctx):
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="qa_cross_archive",
            title="QA cross archive",
            content="Acme only.",
            ctx=a_ctx,
        )
    with tenant_scope(b_ctx):
        with pytest.raises(memory.MemoryNotFound):
            memory.archive(item.id, reason="attempt", ctx=b_ctx)
    with tenant_scope(a_ctx):
        assert memory.get_item(item.id, ctx=a_ctx).current is not None


# ---------------------------------------------------------------- M01-20


def test_new_client_has_no_inherited_demo_values(client_b):
    settings = control.get_client(client_b.id).settings
    assert list_alerts(settings) == []
    assert list_baselines(settings) == []
    rules = get_rules(settings)
    assert rules["configured"] is False
    assert rules["customer_price_usd"] is None
    with pytest.raises(PricingNotConfigured):
        calculate_consult_quote(settings, domains=["general"])


def test_new_client_routes_show_nothing_inherited(app_client, client_b):
    admin = _login(app_client, client_b, "admin")
    headers = _auth(admin)
    assert app_client.get("/api/insights/alerts", headers=headers).json()["items"] == []
    assert app_client.get("/api/insights/baselines", headers=headers).json()["items"] == []
    rules = app_client.get("/api/pricing/resale-rules", headers=headers).json()["rules"]
    assert rules["customer_price_usd"] is None
    quote = app_client.post("/api/pricing/consult-quote", json={"domains": []}, headers=headers)
    assert quote.status_code == 400
    assert "No resale price rules" in quote.json()["detail"]


def test_partial_pricing_save_keeps_values(app_client, client_b):
    admin = _login(app_client, client_b, "admin")
    headers = _auth(admin)
    # The first save must name a price; nothing falls back to a demo figure.
    first = app_client.put("/api/pricing/resale-rules", json={"notes": "only notes"}, headers=headers)
    assert first.status_code == 400
    saved = app_client.put(
        "/api/pricing/resale-rules",
        json={"customer_price_usd": 9000, "cepoch_cost_usd": 4500},
        headers=headers,
    )
    assert saved.status_code == 200
    notes_only = app_client.put(
        "/api/pricing/resale-rules", json={"notes": "updated notes"}, headers=headers
    ).json()["rules"]
    assert notes_only["customer_price_usd"] == 9000.0
    assert notes_only["cepoch_cost_usd"] == 4500.0
    assert notes_only["notes"] == "updated notes"
