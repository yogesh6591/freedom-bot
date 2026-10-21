"""A workspace can never be left without an active admin, on any path.

The rule lives in shared code (bizos.control.store + bizos.admin_ops), so the REST
API, the unified assistant and the legacy engine are all covered:
- no demotion or suspension may leave a client with zero active admins;
- nobody can remove their own Admin role or suspend themselves.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from bizos import admin_ops as ops
from bizos.control import store as control
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.types import DeploymentType, ExecutionMode, Role
from tests.conftest import _destroy, _make_client

PASSWORD = "bizos-dev-password"


@pytest.fixture
def client():
    """A fresh workspace per test: exactly one active admin (the demo ``admin`` user)."""
    suffix = uuid.uuid4().hex[:8]
    made = _make_client(
        f"Lockout {suffix}", f"tlock{suffix}", DeploymentType.DEDICATED_SCHEMA,
        ExecutionMode.WAIT_FOR_APPROVAL,
    )
    yield made
    _destroy(made)


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _user(client, prefix: str):
    return next(u for u in control.list_users(client.id) if u.email.startswith(f"{prefix}@"))


def _ctx(client, prefix: str):
    return control.build_context(_user(client, prefix))


def _active_admins(client) -> list[str]:
    return [
        u.email for u in control.list_users(client.id)
        if u.status == "ACTIVE" and Role.ADMIN in u.roles
    ]


def _second_admin(client):
    """Promote the demo operator to admin and return them."""
    ops.set_roles(_ctx(client, "admin"), _user(client, "operator").id, ["ADMIN"])
    return _user(client, "operator")


def _login(app_client, client, prefix: str) -> dict[str, str]:
    response = app_client.post(
        "/api/auth/login",
        json={"email": f"{prefix}@{client.slug}.example.com", "password": PASSWORD, "client_slug": client.slug},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------- shared store rule


def test_store_refuses_to_demote_or_suspend_the_last_active_admin(client):
    admin = _user(client, "admin")
    with pytest.raises(control.LastActiveAdmin):
        control.set_user_roles(admin.id, [Role.OPERATOR], granted_by="pytest")
    with pytest.raises(control.LastActiveAdmin):
        control.set_user_status(admin.id, "SUSPENDED")
    assert _active_admins(client) == [admin.email]


def test_store_allows_admin_changes_that_keep_an_active_admin(client):
    second = _second_admin(client)
    # Demoting the second admin is fine: the demo admin remains.
    updated = control.set_user_roles(second.id, [Role.OPERATOR], granted_by="pytest")
    assert Role.ADMIN not in updated.roles
    # Suspending a non-admin is never blocked.
    assert control.set_user_status(_user(client, "viewer").id, "SUSPENDED").status == "SUSPENDED"


def test_suspended_admins_do_not_count(client):
    second = _second_admin(client)
    control.set_user_status(second.id, "SUSPENDED")
    with pytest.raises(control.LastActiveAdmin):
        control.set_user_roles(_user(client, "admin").id, [Role.VIEWER], granted_by="pytest")


# --------------------------------------------------------------------------- admin_ops (every caller)


def test_nobody_can_remove_their_own_admin_role_or_suspend_themselves(client):
    _second_admin(client)  # another admin exists, so only the self rule can refuse
    me = _ctx(client, "admin")
    with pytest.raises(ops.AdminForbidden):
        ops.set_roles(me, me.user_id, ["OPERATOR"])
    with pytest.raises(ops.AdminForbidden):
        ops.set_status(me, me.user_id, "SUSPENDED")
    assert "ADMIN" in {str(r) for r in _user(client, "admin").roles}


def test_a_stale_admin_session_cannot_remove_the_last_active_admin(client):
    second = _second_admin(client)
    stale_first = _ctx(client, "admin")  # built while the demo admin was still active
    ops.set_status(_ctx(client, "operator"), _user(client, "admin").id, "SUSPENDED")
    with pytest.raises(ops.AdminConflict, match="no active admin"):
        ops.set_roles(stale_first, second.id, ["VIEWER"])
    with pytest.raises(ops.AdminConflict, match="no active admin"):
        ops.set_status(stale_first, second.id, "SUSPENDED")
    assert _active_admins(client) == [second.email]


def test_two_admins_demoting_each_other_at_once_leave_one_admin(client):
    second = _second_admin(client)
    first_ctx, second_ctx = _ctx(client, "admin"), _ctx(client, "operator")
    start = threading.Barrier(2)
    errors: list[Exception] = []

    def demote(ctx, target_id):
        start.wait()
        try:
            ops.set_roles(ctx, target_id, ["VIEWER"])
        except ops.AdminConflict as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=demote, args=(first_ctx, second.id)),
        threading.Thread(target=demote, args=(second_ctx, _user(client, "admin").id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(_active_admins(client)) == 1
    assert len(errors) == 1


# --------------------------------------------------------------------------- REST API


def test_rest_sole_admin_cannot_demote_or_suspend_themselves(app_client, client):
    headers = _login(app_client, client, "admin")
    me = _user(client, "admin")
    roles = app_client.patch(f"/api/users/{me.id}/roles", json={"roles": ["VIEWER"]}, headers=headers)
    assert roles.status_code == 403, roles.text
    status = app_client.patch(f"/api/users/{me.id}/status", json={"status": "SUSPENDED"}, headers=headers)
    assert status.status_code == 403, status.text
    assert _active_admins(client) == [me.email]


def test_rest_admin_can_still_demote_another_admin(app_client, client):
    second = _second_admin(client)
    headers = _login(app_client, client, "admin")
    response = app_client.patch(f"/api/users/{second.id}/roles", json={"roles": ["OPERATOR"]}, headers=headers)
    assert response.status_code == 200, response.text
    assert "ADMIN" not in response.json()["roles"]


# --------------------------------------------------------------------------- assistant tools


def test_chat_tools_cannot_remove_the_last_active_admin(client):
    second = _second_admin(client)
    stale_first = _ctx(client, "admin")
    ops.set_status(_ctx(client, "operator"), _user(client, "admin").id, "SUSPENDED")
    scope = RunScope(ctx=stale_first, settings=control.get_client(client.id).settings, domain="general")
    with tenant_scope(scope.ctx):
        roles = guarded_call(scope, "admin_set_user_roles", {"user": second.email, "roles": ["VIEWER"]})
        status = guarded_call(scope, "admin_set_user_status", {"user": second.email, "status": "SUSPENDED"})
    assert not roles.ok and "no active admin" in roles.message
    assert not status.ok and "no active admin" in status.message
    assert _active_admins(client) == [second.email]
