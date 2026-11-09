"""Job roles and access requests.

A job role is a label over the permission levels, so these tests pin the things that must stay true:
only Admin grants admin, no job role changes what a level may do, and a request grants nothing until
an admin decides it.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from bizos import admin_ops as ops
from bizos.control import access_requests
from bizos.control import store as control
from bizos.rbac import matrix
from bizos.rbac.job_roles import JOB_ROLES, resolve_job_role, roles_for, tags_after_change
from bizos.types import DATA_AREAS, DeploymentType, ExecutionMode, Role
from tests.conftest import _destroy, _make_client

PASSWORD = "bizos-dev-password"


@pytest.mark.parametrize("key, level, tags", [
    ("HR", Role.OPERATOR, ("hr", "salary")),
    ("FINANCE", Role.OPERATOR, ("finance",)),
    ("OPERATIONS", Role.OPERATOR, ("ops",)),
    ("SALES", Role.OPERATOR, ("sales",)),
    ("ADMIN", Role.ADMIN, ()),
    ("EMPLOYEE", Role.VIEWER, ()),
])
def test_each_job_role_maps_to_one_existing_level(key, level, tags):
    assert JOB_ROLES[key].level is level and JOB_ROLES[key].tags == tags


def test_only_the_admin_job_role_grants_admin():
    assert [j.key for j in JOB_ROLES.values() if Role.ADMIN in roles_for(j, can_approve=True)] == ["ADMIN"]


def test_every_job_role_tag_is_a_real_data_area():
    assert all(tag in DATA_AREAS for job in JOB_ROLES.values() for tag in job.tags)


def test_can_approve_adds_the_approver_level_and_nothing_else():
    assert roles_for(JOB_ROLES["SALES"], can_approve=True) == {Role.OPERATOR, Role.APPROVER}
    assert roles_for(JOB_ROLES["SALES"]) == {Role.OPERATOR}
    assert roles_for(JOB_ROLES["ADMIN"], can_approve=True) == {Role.ADMIN}


def test_changing_role_swaps_its_tags_and_keeps_the_rest():
    assert tags_after_change({"hr", "salary", "legal"}, JOB_ROLES["HR"], JOB_ROLES["FINANCE"]) == {"legal", "finance"}
    assert tags_after_change({"legal"}, None, JOB_ROLES["EMPLOYEE"]) == {"legal"}


@pytest.mark.parametrize("stored, roles, tags, shown", [
    ("FINANCE", {Role.OPERATOR}, {"hr", "salary"}, "FINANCE"),
    (None, {Role.OPERATOR}, {"hr", "salary"}, "HR"),
    (None, {Role.OPERATOR}, {"ops"}, "OPERATIONS"),
    (None, {Role.OPERATOR}, set(), None),
    (None, {Role.VIEWER}, {"finance"}, "EMPLOYEE"),
    (None, {Role.DRAFTER}, {"ops"}, None),
    (None, {Role.APPROVER}, {"finance"}, None),
    (None, {Role.ADMIN}, set(), "ADMIN"),
    # The level moved after the title was stored (the assistant's admin tools can do that).
    ("FINANCE", {Role.VIEWER}, {"finance"}, "EMPLOYEE"),
    ("EMPLOYEE", {Role.ADMIN}, set(), "ADMIN"),
])
def test_the_title_shown_always_matches_the_level_held(stored, roles, tags, shown):
    assert resolve_job_role(stored, roles, tags) == shown


def test_the_permission_matrix_still_has_the_same_five_levels():
    assert set(matrix.WRITE_PROTECTED) == {Role.VIEWER, Role.DRAFTER, Role.APPROVER, Role.OPERATOR, Role.ADMIN}


@pytest.fixture
def client():
    suffix = uuid.uuid4().hex[:8]
    made = _make_client(
        f"Jobs {suffix}", f"tjob{suffix}", DeploymentType.DEDICATED_SCHEMA, ExecutionMode.WAIT_FOR_APPROVAL
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


def _auth(app_client, client, prefix: str) -> dict[str, str]:
    token = app_client.post(
        "/api/auth/login",
        json={"client_slug": client.slug, "email": _user(client, prefix).email, "password": PASSWORD},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_picking_a_job_role_sets_the_level_and_tags(app_client, client):
    admin = _auth(app_client, client, "admin")
    viewer = _user(client, "viewer")
    control.set_user_scopes(viewer.id, [])
    body = app_client.patch(f"/api/users/{viewer.id}/job-role", json={"job_role": "HR"}, headers=admin).json()
    assert body["job_role"] == "HR" and body["job_role_label"] == "HR"
    assert body["roles"] == ["OPERATOR"] and body["data_scopes"] == ["hr", "salary"]

    body = app_client.patch(f"/api/users/{viewer.id}/job-role", json={"job_role": "EMPLOYEE"}, headers=admin).json()
    assert body["roles"] == ["VIEWER"] and body["data_scopes"] == []


def test_only_an_admin_can_set_a_job_role(app_client, client):
    operator = _auth(app_client, client, "operator")
    viewer = _user(client, "viewer")
    response = app_client.patch(f"/api/users/{viewer.id}/job-role", json={"job_role": "ADMIN"}, headers=operator)
    assert response.status_code == 403
    assert control.get_user(viewer.id).roles == {Role.VIEWER}


def test_an_unknown_job_role_is_refused(app_client, client):
    admin = _auth(app_client, client, "admin")
    viewer = _user(client, "viewer")
    response = app_client.patch(f"/api/users/{viewer.id}/job-role", json={"job_role": "CEO"}, headers=admin)
    assert response.status_code == 400


def test_an_admin_cannot_demote_themselves_through_a_job_role(app_client, client):
    admin_user = _user(client, "admin")
    response = app_client.patch(
        f"/api/users/{admin_user.id}/job-role", json={"job_role": "SALES"},
        headers=_auth(app_client, client, "admin"),
    )
    assert response.status_code == 403
    assert Role.ADMIN in control.get_user(admin_user.id).roles


def test_can_approve_survives_a_job_role_change(app_client, client):
    admin = _auth(app_client, client, "admin")
    viewer = _user(client, "viewer")
    body = app_client.patch(f"/api/users/{viewer.id}/can-approve", json={"can_approve": True}, headers=admin).json()
    assert body["can_approve"] is True
    body = app_client.patch(f"/api/users/{viewer.id}/job-role", json={"job_role": "SALES"}, headers=admin).json()
    assert body["roles"] == ["APPROVER", "OPERATOR"] and body["can_approve"] is True
    body = app_client.patch(f"/api/users/{viewer.id}/can-approve", json={"can_approve": False}, headers=admin).json()
    assert body["roles"] == ["OPERATOR"] and body["can_approve"] is False


def test_a_new_user_gets_the_level_of_their_job_role(app_client, client):
    response = app_client.post(
        "/api/users",
        json={"email": f"new{uuid.uuid4().hex[:6]}@example.com", "password": PASSWORD, "job_role": "FINANCE"},
        headers=_auth(app_client, client, "admin"),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["roles"] == ["OPERATOR"] and body["data_scopes"] == ["finance"] and body["job_role"] == "FINANCE"


def test_a_request_grants_nothing_until_an_admin_decides(app_client, client):
    viewer = _user(client, "viewer")
    headers = _auth(app_client, client, "viewer")
    made = app_client.post("/api/access/requests", json={"areas": ["finance"], "reason": "Month end"}, headers=headers)
    assert made.status_code == 201
    assert "finance" not in control.get_user(viewer.id).data_scopes
    assert app_client.get("/api/access", headers=headers).json()["pending"] == ["finance"]

    admin = _auth(app_client, client, "admin")
    assert [r["id"] for r in app_client.get("/api/access/requests", headers=admin).json()["items"]] == [made.json()["id"]]
    decided = app_client.post(f"/api/access/requests/{made.json()['id']}/decide", json={"grant": True}, headers=admin)
    assert decided.json()["status"] == "GRANTED"
    assert "finance" in control.get_user(viewer.id).data_scopes
    assert app_client.get("/api/access/requests", headers=admin).json()["items"] == []


def test_a_declined_request_grants_nothing(app_client, client):
    viewer = _user(client, "viewer")
    made = app_client.post(
        "/api/access/requests", json={"areas": ["legal"]}, headers=_auth(app_client, client, "viewer")
    ).json()
    admin = _auth(app_client, client, "admin")
    decided = app_client.post(f"/api/access/requests/{made['id']}/decide", json={"grant": False}, headers=admin)
    assert decided.json()["status"] == "DECLINED"
    assert "legal" not in control.get_user(viewer.id).data_scopes


def test_a_request_cannot_be_decided_twice(app_client, client):
    made = app_client.post(
        "/api/access/requests", json={"areas": ["legal"]}, headers=_auth(app_client, client, "viewer")
    ).json()
    admin = _auth(app_client, client, "admin")
    assert app_client.post(f"/api/access/requests/{made['id']}/decide", json={"grant": False}, headers=admin).status_code == 200
    again = app_client.post(f"/api/access/requests/{made['id']}/decide", json={"grant": True}, headers=admin)
    assert again.status_code == 409
    assert "legal" not in control.get_user(_user(client, "viewer").id).data_scopes


def test_only_an_admin_can_see_or_decide_requests(app_client, client):
    headers = _auth(app_client, client, "viewer")
    made = app_client.post("/api/access/requests", json={"areas": ["hr"]}, headers=headers).json()
    assert app_client.get("/api/access/requests", headers=headers).status_code == 403
    response = app_client.post(f"/api/access/requests/{made['id']}/decide", json={"grant": True}, headers=headers)
    assert response.status_code == 403
    assert "hr" not in control.get_user(_user(client, "viewer").id).data_scopes


def test_an_admin_of_another_client_cannot_decide_a_request(app_client, client, client_b):
    made = app_client.post(
        "/api/access/requests", json={"areas": ["hr"]}, headers=_auth(app_client, client, "viewer")
    ).json()
    outsider = _auth(app_client, client_b, "admin")
    assert app_client.get("/api/access/requests", headers=outsider).json()["items"] == []
    response = app_client.post(f"/api/access/requests/{made['id']}/decide", json={"grant": True}, headers=outsider)
    assert response.status_code == 404
    assert access_requests.get(client.id, made["id"])["status"] == "PENDING"


def test_a_person_cannot_ask_for_what_they_have_or_already_asked_for(app_client, client):
    headers = _auth(app_client, client, "viewer")
    assert app_client.post("/api/access/requests", json={"areas": ["hr"]}, headers=headers).status_code == 201
    assert app_client.post("/api/access/requests", json={"areas": ["hr"]}, headers=headers).status_code == 409
    admin = _auth(app_client, client, "admin")
    assert app_client.post("/api/access/requests", json={"areas": ["finance"]}, headers=admin).status_code == 409


def test_an_unknown_area_is_refused(app_client, client):
    response = app_client.post(
        "/api/access/requests", json={"areas": ["admin"]}, headers=_auth(app_client, client, "viewer")
    )
    assert response.status_code == 400


def test_the_overview_names_the_admin_to_email(app_client, client):
    control.set_user_scopes(_user(client, "viewer").id, [])
    body = app_client.get("/api/access", headers=_auth(app_client, client, "viewer")).json()
    assert body["admin_emails"] == [_user(client, "admin").email]
    assert "sales" in body["areas"] and body["granted"] == []


def test_an_admin_grants_app_tags_and_anyone_can_ask_for_them(app_client, client):
    admin, viewer = _auth(app_client, client, "admin"), _user(client, "viewer")
    control.set_user_scopes(viewer.id, [])

    asked = app_client.post("/api/access/requests", json={"areas": ["crm", "email"]}, headers=_auth(app_client, client, "viewer"))
    assert asked.status_code in (200, 201)

    body = app_client.patch(f"/api/users/{viewer.id}/scopes", json={"data_scopes": ["crm", "email", "calendar"]}, headers=admin).json()
    assert body["data_scopes"] == ["calendar", "crm", "email"]
    assert set(app_client.get("/api/access", headers=_auth(app_client, client, "viewer")).json()["granted"]) == {"calendar", "crm", "email"}
