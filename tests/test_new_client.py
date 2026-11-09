"""New client from the owner page: who can do it, what it makes, and that a failure leaves nothing behind.

No model, email or connected system is called.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from bizos import settings as app_settings
from bizos import template
from bizos.bootstrap import admin_context
from bizos.control import store as control
from bizos.control.db import control_connection
from bizos.control.models import ClientSettings
from bizos.memory import store as memory
from bizos.tenancy import new_client, provisioning
from bizos.tenancy.context import tenant_scope
from bizos.types import DeploymentType, Role
from tests.conftest import _destroy

PASSWORD = "bizos-dev-password"
ADMIN_PASSWORD = "a-long-first-password"


@pytest.fixture(scope="module")
def api():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(api, email: str, slug: str, password: str = PASSWORD) -> dict[str, str]:
    r = api.post("/api/auth/login", json={"email": email, "password": password, "client_slug": slug})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def owner(api):
    """A platform owner in an internal workspace, signed in."""
    cfg = ClientSettings()
    cfg.internal_workspace = True
    slug = f"towner{uuid.uuid4().hex[:6]}"
    internal = control.create_client(company_name="Owner QA", slug=slug, settings=cfg, created_by="pytest",
                                     deployment_type=DeploymentType.DEDICATED_SCHEMA)
    provisioning.provision_client(internal)
    user = control.create_user(client_id=internal.id, email=f"owner@{slug}.example.com", password=PASSWORD,
                               display_name="Owner", roles=(Role.ADMIN,), created_by="pytest")
    control.set_platform_owner(user.id, True)
    yield _login(api, user.email, slug)
    _destroy(internal)


@pytest.fixture
def made():
    """Slugs this test created; removed afterwards."""
    slugs: list[str] = []
    yield slugs
    for slug in slugs:
        try:
            _destroy(control.get_client_by_slug(slug))
        except control.ClientNotFound:
            pass


def _body(slug: str, **extra):
    return {"company_name": f"Globex {slug}", "slug": slug, "admin_name": "Gina", "admin_email": f"gina@{slug}.example.com",
            "admin_password": ADMIN_PASSWORD, **extra}


def _exists(slug: str) -> bool:
    try:
        control.get_client_by_slug(slug)
    except control.ClientNotFound:
        return False
    return True


def _control_audit(status: str, slug: str) -> list[dict]:
    with control_connection() as conn:
        rows = conn.execute(
            text("SELECT user_id, actor_role, request, error FROM control_audit_events "
                 "WHERE status = :s AND request->>'slug' = :slug"), {"s": status, "slug": slug}
        ).mappings().all()
    return [dict(r) for r in rows]


def test_a_platform_owner_makes_a_working_client(api, owner, made):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    r = api.post("/api/owner/clients", json=_body(slug, purchased_domains=["sales", "finance"]), headers=owner)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["client"]["slug"] == slug and body["admin"] == {"email": f"gina@{slug}.example.com", "display_name": "Gina"}
    assert body["client"]["purchased_domains"] == ["general", "sales", "finance"]
    assert ADMIN_PASSWORD not in r.text

    client = control.get_client_by_slug(slug)
    assert client.settings.enabled_domains == ["general", "sales", "finance"]
    assert client.settings.tool_modes == template.recommended_tool_modes()
    ctx = admin_context(client)
    assert provisioning.unwalled_tables(ctx) == [] and provisioning.missing_tables(ctx) == []
    with tenant_scope(ctx):
        assert memory.list_items(ctx=ctx), "the template's starter memory is seeded"

    # The first admin can sign in to the new workspace, and only there.
    headers = _login(api, f"gina@{slug}.example.com", slug, ADMIN_PASSWORD)
    me = api.get("/api/auth/me", headers=headers).json()
    assert me["client"]["slug"] == slug and "ADMIN" in me["user"]["roles"] and not me["user"]["is_platform_owner"]
    assert api.get("/api/owner/portfolio", headers=headers).status_code == 403

    # It shows on the owner page, and the control-plane audit says who made it, without the password.
    assert slug in {c["slug"] for c in api.get("/api/owner/portfolio", headers=owner).json()["clients"]}
    [row] = _control_audit("CLIENT_CREATED", slug)
    assert row["actor_role"] == "PLATFORM_OWNER" and ADMIN_PASSWORD not in str(row)


def test_the_template_package_is_used_when_nothing_is_picked(api, owner, made):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    r = api.post("/api/owner/clients", json=_body(slug), headers=owner)
    assert r.status_code == 201, r.text
    expected = template.load_template()["purchased_domains"]
    assert set(r.json()["client"]["purchased_domains"]) == set(expected) | {"general"}


def test_the_identifier_is_made_from_the_company_name(api, owner, made):
    name = f"Initech {uuid.uuid4().hex[:5]} & Co."
    expected = name.casefold().replace(" & co.", "_co").replace(" ", "_")
    made.append(expected)
    body = {**_body("x"), "company_name": name, "slug": None}
    r = api.post("/api/owner/clients", json=body, headers=owner)
    assert r.status_code == 201, r.text
    assert r.json()["client"]["slug"] == expected and r.json()["client"]["company_name"] == name


def test_the_form_offers_every_domain_with_general_always_on(api, owner):
    form = api.get("/api/owner/clients/new", headers=owner).json()
    assert [d["name"] for d in form["domains"] if d["always_on"]] == ["general"]
    assert "general" in form["default_domains"] and form["min_password_length"] == 12


def test_a_client_admin_cannot_make_clients(api, client_a, made):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    email = next(u.email for u in control.list_users(client_a.id) if u.email.startswith("admin@"))
    headers = _login(api, email, client_a.slug)
    assert api.get("/api/owner/clients/new", headers=headers).status_code == 403
    assert api.post("/api/owner/clients", json=_body(slug), headers=headers).status_code == 403
    assert not _exists(slug)


def test_signed_out_callers_cannot_make_clients(api, made):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    fresh = TestClient(api.app)
    assert fresh.post("/api/owner/clients", json=_body(slug)).status_code == 401
    assert not _exists(slug)


def test_a_taken_identifier_is_refused_and_the_other_client_is_untouched(api, owner, client_a):
    before = {u.email for u in control.list_users(client_a.id)}
    r = api.post("/api/owner/clients", json=_body(client_a.slug), headers=owner)
    assert r.status_code == 409 and "already taken" in r.json()["detail"]
    assert {u.email for u in control.list_users(client_a.id)} == before


def test_a_leftover_database_from_an_earlier_client_is_never_reused(api, owner, made, monkeypatch):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    leftover = new_client._leftover_workspace
    calls = []
    monkeypatch.setattr(new_client, "_leftover_workspace", lambda s: calls.append(s) or True)
    r = api.post("/api/owner/clients", json=_body(slug), headers=owner)
    assert r.status_code == 409 and "still on the server" in r.json()["detail"] and calls == [slug]
    assert not _exists(slug)
    monkeypatch.setattr(new_client, "_leftover_workspace", leftover)
    assert new_client._leftover_workspace(slug) is False


def test_the_leftover_check_sees_a_real_database(owner, made):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    if app_settings.default_deployment_type() == DeploymentType.DEDICATED_SCHEMA:
        pytest.skip("this server keeps workspaces as schemas")
    name = f"{app_settings.workspace_db_prefix()}{slug}"
    provisioning.create_database(name)
    try:
        assert new_client._leftover_workspace(slug) is True
    finally:
        provisioning.drop_database(name)


@pytest.mark.parametrize("change,code", [
    ({"admin_password": "short"}, 422),
    ({"admin_email": "not-an-email"}, 422),
    ({"company_name": ""}, 422),
    ({"company_name": "   "}, 400),
    ({"purchased_domains": ["sales", "made_up"]}, 400),
])
def test_bad_input_makes_nothing(api, owner, made, change, code):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    r = api.post("/api/owner/clients", json=_body(slug, **change), headers=owner)
    assert r.status_code == code, r.text
    assert not _exists(slug)


def _fail(*_args, **_kwargs):
    raise RuntimeError("disk full")


@pytest.mark.parametrize("target", ["unwalled", "integrations", "admin"])
def test_a_failed_setup_leaves_nothing_behind_and_can_be_retried(api, owner, made, monkeypatch, target):
    slug = f"tnew{uuid.uuid4().hex[:6]}"
    made.append(slug)
    with monkeypatch.context() as patch:
        if target == "unwalled":
            patch.setattr(new_client.provisioning, "unwalled_tables", lambda ctx: ["audit_events"])
        elif target == "integrations":
            patch.setattr(new_client, "seed_default_integrations", _fail)
        else:
            patch.setattr(new_client.admin_ops.control, "create_user", _fail)
        r = api.post("/api/owner/clients", json=_body(slug), headers=owner)
    assert r.status_code == 500 and "nothing was kept" in r.json()["detail"]
    assert "disk full" not in r.text
    assert not _exists(slug)
    assert not provisioning.database_exists(f"{app_settings.workspace_db_prefix()}{slug}")
    [row] = _control_audit("CLIENT_CREATE_FAILED", slug)
    assert ADMIN_PASSWORD not in str(row)

    retry = api.post("/api/owner/clients", json=_body(slug), headers=owner)
    assert retry.status_code == 201, retry.text


def test_the_server_command_runs_the_same_setup(monkeypatch):
    """scripts/provision_client.py goes through set_up_workspace, like the screen."""
    import scripts.provision_client as script

    seen = []
    monkeypatch.setattr(script.new_client, "set_up_workspace",
                        lambda client, config, *, actor: seen.append(client.slug) or {"provisioning": {}, "seeded_memory": 0})
    slug = f"tcli{uuid.uuid4().hex[:6]}"
    args = type("Args", (), {"config": None, "slug": slug, "name": "Script Co", "admin_email": None, "admin_password": None,
                             "out": str(__import__("tempfile").mkdtemp()), "db_host": "localhost", "api_port": 8101,
                             "ui_port": 3101})()
    try:
        assert script.provision(args)["slug"] == slug and seen == [slug]
    finally:
        with control_connection() as conn:
            conn.execute(text("DELETE FROM clients WHERE slug = :s"), {"s": slug})
