"""
API-level tests over the real FastAPI app.

These exercise the boundary the UI uses: cookie/bearer auth, the role gates on
each route, and the fact that a caller cannot select a workspace or a role from
the request.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bizos.control import store as control


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


def test_health_reports_registry_soundness(app_client):
    body = app_client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["tool_registry"]["sound"] is True


def test_login_and_me(app_client, client_a):
    token = _login(app_client, client_a, "operator")
    body = app_client.get("/api/auth/me", headers=_auth(token)).json()
    assert body["user"]["roles"] == ["OPERATOR"]
    assert body["client"]["slug"] == client_a.slug
    assert "general" in body["client"]["enabled_domains"]


def test_bad_password_is_rejected(app_client, client_a):
    response = app_client.post(
        "/api/auth/login",
        json={"email": f"operator@{client_a.slug}.example.com", "password": "wrong",
              "client_slug": client_a.slug},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


def test_unauthenticated_requests_are_rejected():
    """A caller with neither a bearer token nor a session cookie gets 401.

    Uses its own client rather than the module fixture: the shared TestClient
    keeps a cookie jar across requests, so a previous login there would make the
    request authenticated and the test meaningless.
    """
    from app.main import app

    with TestClient(app) as anonymous:
        for path in ("/api/auth/me", "/api/memory", "/api/approvals", "/api/audit",
                     "/api/users", "/api/chat/context"):
            assert anonymous.get(path).status_code == 401, path
        assert anonymous.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_session_cookie_authenticates(app_client, client_a):
    """The browser path: an HttpOnly cookie, no token in JavaScript."""
    response = app_client.post(
        "/api/auth/login",
        json={"email": f"viewer@{client_a.slug}.example.com", "password": "bizos-dev-password",
              "client_slug": client_a.slug},
    )
    cookie = response.cookies.get("bizos_session")
    assert cookie
    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=lax" in set_cookie
    assert app_client.get("/api/auth/me", cookies={"bizos_session": cookie}).status_code == 200


def test_role_gates_on_routes(app_client, client_a):
    viewer = _login(app_client, client_a, "viewer")
    approver = _login(app_client, client_a, "approver")
    admin = _login(app_client, client_a, "admin")

    # Admin-only
    assert app_client.get("/api/users", headers=_auth(viewer)).status_code == 403
    assert app_client.get("/api/users", headers=_auth(admin)).status_code == 200
    assert app_client.get("/api/policies", headers=_auth(approver)).status_code == 403

    # Audit needs APPROVER or above
    assert app_client.get("/api/audit", headers=_auth(viewer)).status_code == 403
    assert app_client.get("/api/audit", headers=_auth(approver)).status_code == 200

    # Reads everyone can do
    for path in ("/api/memory", "/api/approvals", "/api/integrations", "/api/domains"):
        assert app_client.get(path, headers=_auth(viewer)).status_code == 200, path


def test_a_token_cannot_reach_another_workspace(app_client, client_a, client_b):
    """The client comes from the signed token; there is no way to ask for another."""
    token_a = _login(app_client, client_a, "admin")
    users = app_client.get("/api/users", headers=_auth(token_a)).json()["items"]
    assert all(u["client_id"] == client_a.id for u in users)
    emails = {u["email"] for u in users}
    assert all(client_b.slug not in e for e in emails)

    # Even naming B's client id in a query string changes nothing.
    forged = app_client.get(
        f"/api/users?client_id={client_b.id}", headers=_auth(token_a)
    ).json()["items"]
    assert {u["client_id"] for u in forged} == {client_a.id}


def test_admin_cannot_modify_another_clients_user(app_client, client_a, client_b):
    token_a = _login(app_client, client_a, "admin")
    victim = next(u for u in control.list_users(client_b.id) if "viewer" in u.email)
    response = app_client.patch(
        f"/api/users/{victim.id}/roles", json={"roles": ["ADMIN"]}, headers=_auth(token_a)
    )
    assert response.status_code == 404
    # And the victim's roles are unchanged.
    assert control.get_user(victim.id).roles == {r for r in control.get_user(victim.id).roles}
    assert "ADMIN" not in {str(r) for r in control.get_user(victim.id).roles}


def test_approval_flow_over_http(app_client, client_a):
    """Propose via a tool, approve as an approver, execute."""
    from bizos.tenancy.context import tenant_scope
    from bizos.tools.base import guarded_call, RunScope
    from bizos.types import ExecutionMode

    operator = _login(app_client, client_a, "operator")
    approver = _login(app_client, client_a, "approver")

    user = next(u for u in control.list_users(client_a.id) if "operator" in u.email)
    ctx = control.build_context(user)
    scope = RunScope(
        ctx=ctx,
        settings=client_a.settings,
        domain="sales",
        requested_mode=ExecutionMode.WAIT_FOR_APPROVAL,
    )
    with tenant_scope(ctx):
        outcome = guarded_call(
            scope,
            "crm_create_contact",
            {
                "first_name": "HTTP",
                "last_name": "Lead",
                "email": "http@lead.test",
                "company": "HTTP Co",
            },
        )
    assert outcome.action_id, outcome
    action_id = outcome.action_id

    queue = app_client.get("/api/approvals", headers=_auth(approver)).json()["items"]
    target = next(a for a in queue if a["id"] == action_id and a["tool"] == "crm_create_contact")
    assert target["status"] == "PENDING_APPROVAL"
    assert target["policy_reason"]

    approved = app_client.post(
        f"/api/approvals/{target['id']}/approve",
        json={"comment": "looks right", "execute": True},
        headers=_auth(approver),
    ).json()
    # crm_create_contact is WRITE_INTERNAL, so the approver may also execute it.
    assert approved["action"]["status"] in {"COMPLETED", "APPROVED"}

    detail = app_client.get(
        f"/api/approvals/{target['id']}", headers=_auth(approver)
    ).json()
    assert [e["event"] for e in detail["events"]][:3] == [
        "PROPOSED", "APPROVAL_REQUESTED", "APPROVED"
    ]


def test_approver_can_execute_a_protected_write_after_approval(app_client, client_a):
    """Approvers may execute after approving when the tool grants EXECUTE_AFTER_APPROVAL."""
    from bizos.tenancy.context import tenant_scope
    from bizos.tools.base import guarded_call, RunScope
    from bizos.types import ExecutionMode

    approver = _login(app_client, client_a, "approver")

    user = next(u for u in control.list_users(client_a.id) if "operator" in u.email)
    ctx = control.build_context(user)
    scope = RunScope(
        ctx=ctx,
        settings=client_a.settings,
        domain="sales",
        requested_mode=ExecutionMode.WAIT_FOR_APPROVAL,
    )
    with tenant_scope(ctx):
        outcome = guarded_call(
            scope,
            "email_send_message",
            {
                "recipients": ["sep@duties.test"],
                "subject": "quote please",
                "body": "Hello — following up on pricing.",
            },
        )
    assert outcome.action_id
    action_id = outcome.action_id

    result = app_client.post(
        f"/api/approvals/{action_id}/approve",
        json={"execute": True}, headers=_auth(approver),
    ).json()
    assert result["executed"] is True
    assert result["action"]["status"] == "COMPLETED"


def test_viewer_cannot_execute_or_approve(app_client, client_a):
    from bizos.tenancy.context import tenant_scope
    from bizos.tools.base import guarded_call, RunScope
    from bizos.types import ExecutionMode

    viewer = _login(app_client, client_a, "viewer")

    user = next(u for u in control.list_users(client_a.id) if "operator" in u.email)
    ctx = control.build_context(user)
    scope = RunScope(
        ctx=ctx,
        settings=client_a.settings,
        domain="sales",
        requested_mode=ExecutionMode.WAIT_FOR_APPROVAL,
    )
    with tenant_scope(ctx):
        outcome = guarded_call(
            scope,
            "crm_create_contact",
            {"first_name": "Gate", "last_name": "Probe", "email": "gate@probe.test"},
        )
    action_id = outcome.action_id
    assert app_client.post(
        f"/api/approvals/{action_id}/approve", json={}, headers=_auth(viewer)
    ).status_code == 403
    assert app_client.post(
        f"/api/approvals/{action_id}/execute", headers=_auth(viewer)
    ).status_code == 403


def test_memory_correction_over_http(app_client, client_a):
    drafter = _login(app_client, client_a, "drafter")
    created = app_client.post(
        "/api/memory",
        json={"category": "FACT", "key": "http_correction", "title": "HTTP correction",
              "content": "Original value"},
        headers=_auth(drafter),
    ).json()
    corrected = app_client.post(
        f"/api/memory/{created['id']}/correct",
        json={"new_content": "Corrected value", "reason": "It changed"},
        headers=_auth(drafter),
    )
    assert corrected.status_code == 200
    detail = app_client.get(f"/api/memory/{created['id']}", headers=_auth(drafter)).json()
    statuses = {v["version_no"]: v["status"] for v in detail["versions"]}
    assert statuses[1] == "SUPERSEDED" and statuses[2] == "ACTIVE"


def test_viewer_cannot_write_memory(app_client, client_a):
    viewer = _login(app_client, client_a, "viewer")
    response = app_client.post(
        "/api/memory",
        json={"category": "FACT", "title": "Should fail", "content": "x"},
        headers=_auth(viewer),
    )
    assert response.status_code == 403


def test_admin_can_change_policy_and_it_takes_effect(app_client, client_a):
    admin = _login(app_client, client_a, "admin")
    original = app_client.get("/api/policies", headers=_auth(admin)).json()["settings"]
    try:
        updated = app_client.put(
            "/api/policies/settings",
            json={"risk_policy": {"max_records_per_action": 3}},
            headers=_auth(admin),
        ).json()
        assert updated["risk_policy"]["max_records_per_action"] == 3
    finally:
        app_client.put(
            "/api/policies/settings",
            json={"risk_policy": {
                "max_records_per_action": original["risk_policy"]["max_records_per_action"]
            }},
            headers=_auth(admin),
        )


def test_critical_floor_cannot_be_configured_away_over_http(app_client, client_a):
    admin = _login(app_client, client_a, "admin")
    result = app_client.put(
        "/api/policies/settings",
        json={"risk_policy": {"auto_allowed_max_risk": "CRITICAL"}},
        headers=_auth(admin),
    ).json()
    assert result["risk_policy"]["auto_allowed_max_risk"] == "HIGH"


def test_general_domain_cannot_be_disabled(app_client, client_a):
    admin = _login(app_client, client_a, "admin")
    response = app_client.patch(
        "/api/domains/general", json={"enabled": False}, headers=_auth(admin)
    )
    assert response.status_code == 400


def test_chat_reports_the_mode_and_surface(app_client, client_a):
    viewer = _login(app_client, client_a, "viewer")
    context = app_client.get("/api/chat/context?domain=sales", headers=_auth(viewer)).json()
    assert context["role"] == "VIEWER"
    assert context["mode"] == str(client_a.settings.mode)
    # A viewer can see write processes exist (FB-037) but can run none of them.
    assert [t for t in context["tools"] if t["write"] and t["can_run"]] == []
    assert any(t["write"] and t["can_view"] for t in context["tools"])


def test_chat_without_a_model_is_reported_clearly(app_client, client_a):
    from bizos import settings as app_settings

    if app_settings.llm_available():
        pytest.skip("a model is configured; the 503 path does not apply")
    viewer = _login(app_client, client_a, "viewer")
    response = app_client.post(
        "/api/chat", json={"message": "hello"}, headers=_auth(viewer)
    )
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
