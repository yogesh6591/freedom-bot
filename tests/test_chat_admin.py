"""Administration from chat: admin-only, same rules as the Administration page."""

from __future__ import annotations

import pytest

from bizos.agents.surface import visible_specs
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.packs import ADMIN_TOOLS, get_pack
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import RunScope, guarded_call
from bizos.types import ExecutionMode, PolicyEffect


def _scope(client, ctx_factory, who: str, mode: ExecutionMode | None = None) -> RunScope:
    settings = control.get_client(client.id).settings
    if mode is not None:
        settings = ClientSettings.from_dict({**settings.to_dict(), "default_execution_mode": mode.value})
    return RunScope(ctx=ctx_factory(client, who), settings=settings, domain="general")


def _call(client, ctx_factory, tool_name: str, who: str = "admin", **args):
    scope = _scope(client, ctx_factory, who)
    with tenant_scope(scope.ctx):
        return guarded_call(scope, tool_name, args)


def _user(client, prefix: str):
    return next(u for u in control.list_users(client.id) if u.email.startswith(f"{prefix}@"))


def test_every_admin_page_tab_has_chat_tools():
    names = set(ADMIN_TOOLS)
    for tool in (
        "admin_apply_setup", "admin_scan_alerts", "admin_launch_sign_off", "admin_set_resale_rules",
        "admin_consult_quote", "admin_create_user", "admin_update_settings", "admin_toggle_domain",
        "admin_record_change_order", "admin_set_tool_permission", "admin_set_tool_mode",
    ):
        assert tool in names
    assert names <= set(get_pack("general").tools)


@pytest.mark.parametrize("who", ["viewer", "drafter", "approver", "operator"])
def test_non_admins_neither_see_nor_run_admin_tools(client_scratch, ctx_factory, who):
    scope = _scope(client_scratch, ctx_factory, who)
    with tenant_scope(scope.ctx):
        visible = {s.name for s in visible_specs(scope, [get_pack("general")])}
        assert not visible & set(ADMIN_TOOLS)
        for tool in ("admin_list_users", "admin_set_user_roles", "admin_update_settings"):
            assert guarded_call(scope, tool, {"user": "viewer", "roles": ["ADMIN"]}).effect == PolicyEffect.DENY
    assert "ADMIN" not in {str(r) for r in _user(client_scratch, "viewer").roles}


def test_admin_sees_tools_and_changes_run_without_queueing(client_scratch, ctx_factory):
    scope = _scope(client_scratch, ctx_factory, "admin")
    assert scope.mode == ExecutionMode.WAIT_FOR_APPROVAL
    with tenant_scope(scope.ctx):
        visible = {s.name for s in visible_specs(scope, [get_pack("general")])}
    assert set(ADMIN_TOOLS) <= visible

    out = _call(client_scratch, ctx_factory, "admin_set_user_roles", user="drafter", roles=["APPROVER"])
    assert out.ok and out.effect == PolicyEffect.ALLOW and out.action_id is None, out.message
    assert {str(r) for r in _user(client_scratch, "drafter").roles} == {"APPROVER"}


def test_admin_tools_are_refused_in_advise_mode(client_scratch, ctx_factory):
    scope = _scope(client_scratch, ctx_factory, "admin", mode=ExecutionMode.ADVISE)
    with tenant_scope(scope.ctx):
        assert guarded_call(scope, "admin_list_users", {}).ok
        assert guarded_call(scope, "admin_toggle_domain", {"domain": "sales", "enabled": False}).effect == PolicyEffect.DENY


def test_users_create_access_status_and_password(client_scratch, ctx_factory):
    created = _call(
        client_scratch, ctx_factory, "admin_create_user",
        email=f"newhire@{client_scratch.slug}.example.com", display_name="New Hire",
        roles=["DRAFTER"], data_access=["finance"],
    )
    assert created.ok, created.message
    password = created.data["temporary_password"]
    assert len(password) >= 12
    assert control.authenticate(created.data["email"], password, client_slug=client_scratch.slug)

    access = _call(client_scratch, ctx_factory, "admin_set_user_access", user="New Hire", add=["hr"], remove=["finance"])
    assert access.ok and access.data["data_access"] == ["hr"], access.message

    suspended = _call(client_scratch, ctx_factory, "admin_set_user_status", user="newhire", status="disable")
    assert suspended.ok and suspended.data["status"] == "SUSPENDED"

    reset = _call(client_scratch, ctx_factory, "admin_reset_password", user="newhire")
    assert reset.ok and reset.data["temporary_password"] != password

    dup = _call(client_scratch, ctx_factory, "admin_create_user", email=created.data["email"])
    assert not dup.ok and "already exists" in dup.message


def test_admin_cannot_lock_themselves_out(client_scratch, ctx_factory):
    me = _user(client_scratch, "admin").email
    roles = _call(client_scratch, ctx_factory, "admin_set_user_roles", user=me, roles=["VIEWER"])
    assert not roles.ok and "own Admin role" in roles.message
    status = _call(client_scratch, ctx_factory, "admin_set_user_status", user=me, status="SUSPENDED")
    assert not status.ok
    assert "ADMIN" in {str(r) for r in _user(client_scratch, "admin").roles}


def test_policies_packs_and_change_orders(client_scratch, ctx_factory):
    mode = _call(client_scratch, ctx_factory, "admin_update_settings",
                 default_execution_mode="draft", approval_policy={"allow_self_approval": False})
    assert mode.ok, mode.message
    settings = control.get_client(client_scratch.id).settings
    assert settings.mode == ExecutionMode.DRAFT
    assert settings.approval_policy.allow_self_approval is False
    assert settings.approval_policy.expires_after_hours == 72

    bad = _call(client_scratch, ctx_factory, "admin_update_settings", default_execution_mode="turbo")
    assert not bad.ok

    unpaid = next(d for d in ("finance", "legal", "brand", "strategy") if not settings.domain_purchased(d))
    refused = _call(client_scratch, ctx_factory, "admin_toggle_domain", domain=unpaid, enabled=True)
    assert not refused.ok and "change order" in refused.message
    order = _call(client_scratch, ctx_factory, "admin_record_change_order", domain=unpaid, reference="CO-TEST-1")
    assert order.ok, order.message
    settings = control.get_client(client_scratch.id).settings
    assert unpaid in settings.purchased_domains and unpaid in settings.enabled_domains

    general = _call(client_scratch, ctx_factory, "admin_toggle_domain", domain="general", enabled=False)
    assert not general.ok


def test_tool_permissions_and_tool_modes(client_scratch, ctx_factory):
    from bizos.rbac import permissions as perms
    from bizos.rbac.registry import get_spec
    from bizos.types import Role, ToolPermission

    admin_ctx = ctx_factory(client_scratch, "admin")
    spec = get_spec("crm_create_task")
    out = _call(client_scratch, ctx_factory, "admin_set_tool_permission",
                tool="crm_create_task", role="drafter", permission="DENIED")
    assert out.ok, out.message
    with tenant_scope(admin_ctx):
        overrides = perms.load_overrides(admin_ctx, refresh=True)
        assert perms.permission_for_role(spec, Role.DRAFTER, overrides) == ToolPermission.DENIED
    reset = _call(client_scratch, ctx_factory, "admin_set_tool_permission",
                  tool="crm_create_task", role="drafter", permission="default")
    assert reset.ok
    with tenant_scope(admin_ctx):
        overrides = perms.load_overrides(admin_ctx, refresh=True)
        assert perms.permission_for_role(spec, Role.DRAFTER, overrides) != ToolPermission.DENIED

    mode = _call(client_scratch, ctx_factory, "admin_set_tool_mode", tool="crm_create_task", mode="ADVISE")
    assert mode.ok and control.get_client(client_scratch.id).settings.tool_modes["crm_create_task"] == "ADVISE"
    cleared = _call(client_scratch, ctx_factory, "admin_set_tool_mode", tool="crm_create_task", mode="default")
    assert cleared.ok and "crm_create_task" not in control.get_client(client_scratch.id).settings.tool_modes
    unknown = _call(client_scratch, ctx_factory, "admin_set_tool_mode", tool="no_such_tool", mode="DRAFT")
    assert not unknown.ok


def test_setup_keeps_omitted_fields_and_live_mode(client_scratch, ctx_factory):
    first = _call(client_scratch, ctx_factory, "admin_apply_setup",
                  goals=["Cut invoice turnaround"], systems=["HubSpot"], autonomy_mode="WAIT_FOR_APPROVAL")
    assert first.ok, first.message
    _call(client_scratch, ctx_factory, "admin_update_settings", default_execution_mode="DRAFT")
    second = _call(client_scratch, ctx_factory, "admin_apply_setup", exclusions=["Payroll"])
    assert second.ok, second.message
    setup = _call(client_scratch, ctx_factory, "admin_get_setup").data
    assert setup["goals"] == ["Cut invoice turnaround"]
    assert setup["systems"] == ["HubSpot"] and setup["exclusions"] == ["Payroll"]
    assert control.get_client(client_scratch.id).settings.mode == ExecutionMode.DRAFT


def test_launch_alerts_and_pricing_reads(client_scratch, ctx_factory):
    checklist = _call(client_scratch, ctx_factory, "admin_launch_checklist")
    assert checklist.ok and checklist.data["items"]
    sign_off = _call(client_scratch, ctx_factory, "admin_launch_sign_off")
    if not checklist.data["ready"]:
        assert not sign_off.ok

    assert _call(client_scratch, ctx_factory, "admin_scan_alerts").ok
    assert _call(client_scratch, ctx_factory, "admin_list_alerts").ok
    missing = _call(client_scratch, ctx_factory, "admin_dismiss_alert", alert_id="nope")
    assert not missing.ok

    overview = _call(client_scratch, ctx_factory, "admin_workspace_overview")
    assert overview.ok and overview.data["users"]["total"] >= 5
    assert _call(client_scratch, ctx_factory, "admin_pricing").ok
    assert _call(client_scratch, ctx_factory, "admin_tool_permissions", tool="email").data["tools"]
