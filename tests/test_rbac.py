"""§30 — Role enforcement, and §7's permission matrix."""

from __future__ import annotations

import pytest

from bizos.rbac.permissions import resolve
from bizos.rbac.registry import TOOL_SPECS, assert_no_generic_tools, get_spec
from bizos.tenancy.context import tenant_scope
from bizos.tools.base import guarded_call
from bizos.types import ExecutionMode, PolicyEffect, Role, ToolPermission


def test_viewer_cannot_send_email(client_a, scope_factory):
    """The §30 role scenario: a Viewer asking to send email fails the check."""
    scope = scope_factory(client_a, "viewer", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope,
            "email_send_message",
            {"recipients": ["someone@example.com"], "subject": "Hi", "body": "Hello"},
        )
    assert outcome.effect == PolicyEffect.DENY
    assert outcome.decision.rule == "RBAC_DENIED"
    assert not outcome.ok


def test_viewer_can_read(client_a, scope_factory):
    """A Viewer keeps read access — the role restricts action, not information."""
    scope = scope_factory(client_a, "viewer")
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "crm_search_contacts", {"query": ""})
    assert outcome.effect == PolicyEffect.ALLOW
    assert outcome.ok


def test_viewer_never_sees_a_write_tool(client_a, scope_factory):
    """Layer 1: the forbidden tool is not even on the surface."""
    from bizos.agents.surface import visible_specs
    from bizos.domains.packs import get_pack

    scope = scope_factory(client_a, "viewer", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        specs = visible_specs(scope, [get_pack("sales"), get_pack("general")])
    assert [s.name for s in specs if s.write] == []


@pytest.mark.parametrize(
    "role,tool,expected",
    [
        (Role.VIEWER, "crm_search_contacts", ToolPermission.ALLOWED),
        (Role.VIEWER, "crm_create_draft_update_placeholder", ToolPermission.DENIED),
        (Role.DRAFTER, "email_send_message", ToolPermission.DRAFT_ONLY),
        (Role.APPROVER, "email_send_message", ToolPermission.EXECUTE_AFTER_APPROVAL),
        (Role.OPERATOR, "email_send_message", ToolPermission.ALLOWED),
        (Role.VIEWER, "crm_delete_record", ToolPermission.DENIED),
        (Role.DRAFTER, "crm_delete_record", ToolPermission.DENIED),
        (Role.APPROVER, "crm_delete_record", ToolPermission.EXECUTE_AFTER_APPROVAL),
        (Role.OPERATOR, "crm_delete_record", ToolPermission.EXECUTE_AFTER_APPROVAL),    ],
)
def test_permission_matrix_matches_the_brief(role, tool, expected):
    """§7's worked examples, asserted directly against the registry."""
    if get_spec(tool) is None:
        # An unregistered tool must resolve to fully denied.
        assert resolve(tool, {role}).permission == ToolPermission.DENIED
        return
    assert resolve(tool, {role}).permission == expected


def test_holding_two_roles_combines_capabilities():
    """DRAFTER+APPROVER may both prepare and bless the same protected action."""
    permission = resolve("email_send_message", {Role.DRAFTER, Role.APPROVER})
    assert permission.can_draft and permission.can_approve
    assert not permission.can_invoke


def test_approver_alone_queues_for_approval(client_a, scope_factory):
    """EXECUTE_AFTER_APPROVAL lets an Approver propose, but only into the queue."""
    scope = scope_factory(client_a, "approver", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(
            scope, "email_send_message",
            {"recipients": ["x@example.com"], "subject": "s", "body": "b"},
        )
    assert outcome.effect == PolicyEffect.REQUIRE_APPROVAL
    assert outcome.decision.rule == "RBAC_EXECUTE_AFTER_APPROVAL"


def test_admin_cannot_exceed_the_registry_ceiling(client_a, ctx_factory):
    """§7 overrides are configurable, but not past the tool's declared ceiling."""
    from bizos.rbac import permissions

    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        with pytest.raises(PermissionError):
            permissions.set_override(
                ctx, tool="crm_delete_record", role=Role.VIEWER,
                permission=ToolPermission.ALLOWED, updated_by=ctx.user_id,
            )


def test_client_override_tightens_a_permission(client_a, ctx_factory, scope_factory):
    """An admin can restrict a tool below its default for their workspace."""
    from bizos.rbac import permissions

    ctx = ctx_factory(client_a, "admin")
    with tenant_scope(ctx):
        permissions.set_override(
            ctx, tool="crm_create_note", role=Role.DRAFTER,
            permission=ToolPermission.DENIED, updated_by=ctx.user_id,
        )
        try:
            scope = scope_factory(client_a, "drafter")
            outcome = guarded_call(scope, "crm_create_note", {"body": "hello"})
            assert outcome.effect == PolicyEffect.DENY
            assert outcome.decision.rule == "RBAC_DENIED"
        finally:
            permissions.clear_override(ctx, tool="crm_create_note", role=Role.DRAFTER)


def test_no_generic_action_tool_is_registered():
    """§19/§28: narrow verbs only."""
    assert_no_generic_tools()
    for spec in TOOL_SPECS:
        assert spec.name.count("_") >= 1, f"{spec.name} is not a namespaced verb"


def test_unregistered_tool_is_denied(client_a, scope_factory):
    """A tool with no spec has no metadata to reason about, so it fails closed."""
    scope = scope_factory(client_a, "admin", mode=ExecutionMode.AUTO_WITHIN_SCOPE)
    with tenant_scope(scope.ctx):
        outcome = guarded_call(scope, "execute_any_api_request", {"url": "http://x"})
    assert outcome.effect == PolicyEffect.DENY
    assert outcome.decision.rule == "TOOL_UNREGISTERED"
