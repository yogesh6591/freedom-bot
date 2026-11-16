"""Merged tools: argument mapping, enum narrowing, all-or-nothing, and gate parity with the old tools.

Each merged call must be gated exactly like the specific old tool its arguments map to.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.assistant.assistant import _packs_for
from app.assistant.company_tools import _bulk_scope
from bizos import admin_ops as ops
from bizos.agents.merged_tools import MERGED, MergeArgumentError, run, schema_for
from bizos.agents.tool_args import schema_for_tool
from bizos.connectors.base import ConnectorResult
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.tenancy.context import tenant_scope
from bizos.tools import base as tools_base
from bizos.tools.base import RunScope, guarded_call
from bizos.tools.dry_run import bound_scope, is_visible
from bizos.types import ExecutionMode, PolicyEffect


def _settings(client, mode: ExecutionMode = ExecutionMode.WAIT_FOR_APPROVAL) -> ClientSettings:
    base = control.get_client(client.id).settings
    return ClientSettings.from_dict({**base.to_dict(), "default_execution_mode": mode.value})


def _scope_for(client, ctx, mode=ExecutionMode.WAIT_FOR_APPROVAL):
    settings = _settings(client, mode)
    scope = RunScope(ctx=ctx, settings=settings, domain="general")
    packs = _packs_for(settings, "")
    return (lambda tool: bound_scope(scope, tool, packs)), scope, packs


def _user(client, prefix):
    return next(u for u in control.list_users(client.id) if u.email.startswith(f"{prefix}@"))


def _plan(name, args, scope_for=None):
    return [(c.tool, c.args) for c in MERGED[name].plan(scope_for, args)]


# --------------------------------------------------------------------------- argument mapping


@pytest.mark.parametrize("decision,tool", [
    ("approve", "approvals_approve"), ("reject", "approvals_reject"), ("request_changes", "approvals_request_changes"),
])
def test_approvals_decide_maps_each_decision_to_its_old_tool(decision, tool):
    assert _plan("approvals_decide", {"action_id": "act_1", "decision": decision, "comment": "fix it"}) == [
        (tool, {"action_id": "act_1", "comment": "fix it"})
    ]


@pytest.mark.parametrize("args,needle", [
    ({"action_id": "act_1", "decision": "maybe"}, "decision must be"),
    ({"action_id": "act_1", "decision": "request_changes"}, "needs a comment"),
    ({"decision": "approve"}, "action_id"),
    ({"match": "Meena expense", "decision": "approve"}, "title can match the wrong request"),
])
def test_approvals_decide_rejects_unmappable_arguments(args, needle):
    with pytest.raises(MergeArgumentError, match=needle):
        _plan("approvals_decide", args)


@pytest.mark.parametrize("kind,tool", [
    ("fact", "memory_add_fact"), ("sop", "memory_add_sop"), ("decision", "memory_record_decision"),
])
def test_memory_add_maps_each_kind_to_its_old_tool(kind, tool):
    [(called, args)] = _plan("memory_add", {"kind": kind, "title": "Renewal", "content": "Acme renews in March"})
    assert called == tool and args["title"] == "Renewal" and args["content"] == "Acme renews in March"
    assert ("decision" in args) == (kind == "decision")


def test_memory_add_needs_kind_title_and_content():
    with pytest.raises(MergeArgumentError):
        _plan("memory_add", {"kind": "note", "title": "t", "content": "c"})
    with pytest.raises(MergeArgumentError):
        _plan("memory_add", {"kind": "fact", "title": "t"})


def test_memory_add_passes_access_area_including_everyone():
    [(called, args)] = _plan(
        "memory_add",
        {"kind": "fact", "title": "Bonus", "content": "4 percent", "access_area": "finance"},
    )
    assert called == "memory_add_fact" and args["access_area"] == "finance"
    [(called, args)] = _plan(
        "memory_add",
        {"kind": "fact", "title": "Leave", "content": "24 days", "access_area": ""},
    )
    assert called == "memory_add_fact" and "access_area" in args and args["access_area"] == ""


def test_youtube_merges_call_the_extra_tool_only_when_asked():
    assert _plan("youtube_my_channel", {}) == [("youtube_my_channel", {})]
    assert _plan("youtube_my_channel", {"include_videos": True, "limit": 5}) == [
        ("youtube_my_channel", {}), ("youtube_my_videos", {"limit": 5})
    ]
    assert _plan("youtube_video", {"video_id": "v1"}) == [("youtube_video_details", {"video_ids": ["v1"]})]
    assert _plan("youtube_video", {"video_id": "v1", "include_comments": True, "order": "time"}) == [
        ("youtube_video_details", {"video_ids": ["v1"]}), ("youtube_video_comments", {"video_id": "v1", "order": "time"})
    ]


def test_admin_update_user_maps_add_remove_to_the_old_tools(client_scratch, ctx_factory):
    scope_for, _, _ = _scope_for(client_scratch, ctx_factory(client_scratch, "admin"))
    viewer = _user(client_scratch, "viewer")
    with tenant_scope(scope_for("admin_set_user_roles").ctx):
        plan = _plan("admin_update_user", {"user": viewer.email, "roles_add": ["approver"],
                                           "access_grant": ["finance"], "access_revoke": "hr"}, scope_for)
        assert plan == [
            ("admin_set_user_roles", {"user": viewer.id, "roles": sorted({str(r) for r in viewer.roles} | {"APPROVER"})}),
            ("admin_set_user_access", {"user": viewer.id, "add": ["finance"], "remove": ["hr"]}),
        ]
        with pytest.raises(MergeArgumentError, match="Say what changes"):
            _plan("admin_update_user", {"user": viewer.email}, scope_for)
        with pytest.raises(MergeArgumentError, match="at least one role"):
            _plan("admin_update_user", {"user": viewer.email, "roles_remove": [str(r) for r in viewer.roles]}, scope_for)


# --------------------------------------------------------------------------- enum narrowing


def test_schema_narrowing_keeps_only_allowed_choices():
    assert schema_for("approvals_decide", {"approvals_approve"})["properties"]["decision"]["enum"] == ["approve"]
    assert schema_for("approvals_decide", {"approvals_submit"}) is None
    assert schema_for("memory_add", {"memory_add_fact", "memory_record_decision"})["properties"]["kind"]["enum"] == [
        "fact", "decision"
    ]
    only_access = schema_for("admin_update_user", {"admin_set_user_access"})["properties"]
    assert "roles_add" not in only_access and "access_grant" in only_access
    assert "include_videos" not in schema_for("youtube_my_channel", {"youtube_my_channel"})["properties"]
    assert schema_for("youtube_video", {"youtube_video_comments"}) is None


def _allowed(client, ctx):
    _, scope, packs = _scope_for(client, ctx)
    return {t for m in MERGED.values() for t in m.wraps if is_visible(scope, t, packs)}


def test_narrowing_follows_the_users_real_permissions(client_scratch, ctx_factory):
    drafter = ctx_factory(client_scratch, "drafter")
    approver = ctx_factory(client_scratch, "approver")
    with tenant_scope(drafter):
        assert schema_for("approvals_decide", _allowed(client_scratch, drafter)) is None
        assert schema_for("admin_update_user", _allowed(client_scratch, drafter)) is None
    with tenant_scope(approver):
        enum = schema_for("approvals_decide", _allowed(client_scratch, approver))["properties"]["decision"]["enum"]
        assert enum == ["approve", "reject", "request_changes"]


def test_an_override_on_one_old_tool_narrows_only_that_choice(client_scratch, ctx_factory):
    admin, approver = ctx_factory(client_scratch, "admin"), ctx_factory(client_scratch, "approver")
    ops.set_tool_permission(admin, tool="approvals_reject", role="APPROVER", permission="DENIED")
    try:
        with tenant_scope(approver):
            from bizos.rbac import permissions as perms

            perms.load_overrides(approver, refresh=True)
            enum = schema_for("approvals_decide", _allowed(client_scratch, approver))["properties"]["decision"]["enum"]
        assert enum == ["approve", "request_changes"]
    finally:
        ops.clear_tool_permission(admin, tool="approvals_reject", role="APPROVER")


# --------------------------------------------------------------------------- gate parity


@pytest.fixture
def stubbed(monkeypatch):
    for name in list(tools_base.EFFECTS):
        monkeypatch.setitem(tools_base.EFFECTS, name, lambda scope, args: ConnectorResult(ok=True, summary="stub"))
    monkeypatch.setattr(tools_base.action_store, "create_action", lambda **kw: SimpleNamespace(id="act_stub"))


SAMPLES = [
    ("approvals_decide", {"action_id": "act_1", "decision": "approve"}),
    ("approvals_decide", {"action_id": "act_1", "decision": "reject"}),
    ("approvals_decide", {"action_id": "act_1", "decision": "request_changes", "comment": "fix"}),
    ("memory_add", {"kind": "fact", "title": "t", "content": "c"}),
    ("memory_add", {"kind": "sop", "title": "t", "content": "c"}),
    ("memory_add", {"kind": "decision", "title": "t", "content": "c"}),
    ("youtube_my_channel", {"include_videos": True}),
    ("youtube_video", {"video_id": "v1", "include_comments": True}),
    ("admin_update_user", {"user": "viewer", "roles_add": ["DRAFTER"]}),
    ("admin_update_user", {"user": "viewer", "access_grant": ["finance"]}),
    ("admin_update_user", {"user": "viewer", "roles_add": ["DRAFTER"], "access_grant": ["finance"]}),
]


@pytest.mark.parametrize("mode", tuple(ExecutionMode), ids=str)
@pytest.mark.parametrize("who", ["viewer", "drafter", "approver", "operator", "admin"])
def test_each_merged_call_is_gated_exactly_like_its_old_tool(client_scratch, ctx_factory, stubbed, who, mode):
    ctx = ctx_factory(client_scratch, who)
    scope_for, _, _ = _scope_for(client_scratch, ctx, mode)
    viewer = _user(client_scratch, "viewer").email
    with tenant_scope(ctx):
        for name, args in SAMPLES:
            args = {**args, "user": viewer} if "user" in args else args
            for call in MERGED[name].plan(scope_for, args):
                direct = guarded_call(scope_for(call.tool), call.tool, call.args)
                merged = run(name, args, scope_for)
                got = {o.tool: o.effect for o in merged.outcomes}
                if merged.refused:
                    assert direct.effect == PolicyEffect.DENY, (name, call.tool, merged.refused)
                else:
                    assert got[call.tool] == direct.effect, (who, mode, name, call.tool)


# --------------------------------------------------------------------------- all-or-nothing


def test_admin_update_user_undoes_the_role_change_when_access_fails(client_scratch, ctx_factory):
    admin = ctx_factory(client_scratch, "admin")
    scope_for, _, _ = _scope_for(client_scratch, admin)
    viewer = _user(client_scratch, "viewer")
    before = {str(r) for r in viewer.roles}
    with tenant_scope(admin):
        result = run("admin_update_user", {"user": viewer.email, "roles_add": ["APPROVER"],
                                           "access_grant": ["not_a_real_area"]}, scope_for)
    tools = [o.tool for o in result.outcomes]
    assert tools == ["admin_set_user_roles", "admin_set_user_access", "admin_set_user_roles"], tools
    assert not result.outcomes[1].ok
    assert {str(r) for r in _user(client_scratch, "viewer").roles} == before


def test_admin_update_user_changes_nothing_when_one_part_is_refused(client_scratch, ctx_factory):
    admin = ctx_factory(client_scratch, "admin")
    scope_for, _, _ = _scope_for(client_scratch, admin, ExecutionMode.ADVISE)
    viewer = _user(client_scratch, "viewer")
    with tenant_scope(admin):
        result = run("admin_update_user", {"user": viewer.email, "roles_add": ["APPROVER"],
                                           "access_grant": ["finance"]}, scope_for)
    assert result.refused and "Nothing was changed" in result.refused and result.outcomes == []


# --------------------------------------------------------------------------- unmerged tools changed in Phase 4


def test_bulk_update_needs_an_explicit_scope(client_scratch, ctx_factory):
    assert _bulk_scope({"lifecycle_stage": "ALL"}) == (True, None)
    assert _bulk_scope({"lifecycle_stage": "lead"}) == (True, "lead")
    assert _bulk_scope({"lifecycle_filter": "lead"}) == (True, "lead")  # actions queued before the rename
    assert _bulk_scope({}) == (False, None)
    assert schema_for_tool("crm_bulk_update_contacts")["required"] == ["field", "value", "lifecycle_stage"]
    ctx = ctx_factory(client_scratch, "operator")
    scope_for, _, _ = _scope_for(client_scratch, ctx)
    with tenant_scope(ctx):
        missing = guarded_call(scope_for("crm_bulk_update_contacts"), "crm_bulk_update_contacts",
                               {"field": "owner", "value": "Meena"})
    assert missing.effect == PolicyEffect.DENY and "lifecycle_stage is required" in missing.message


def test_permission_and_mode_tools_offer_only_real_values():
    props = schema_for_tool("admin_set_tool_permission")["properties"]
    assert props["permission"]["enum"] == ["DENIED", "DRAFT_ONLY", "APPROVE_ONLY", "EXECUTE_AFTER_APPROVAL", "ALLOWED", "default"]
    assert props["role"]["enum"] == ["VIEWER", "DRAFTER", "APPROVER", "OPERATOR", "ADMIN"]
    assert schema_for_tool("admin_set_tool_mode")["properties"]["mode"]["enum"] == ["ADVISE", "DRAFT", "WAIT_FOR_APPROVAL", "default"]
    assert "deal_id" in schema_for_tool("crm_create_task")["properties"]


def test_parts_gated_differently_are_refused_with_separate_requests_advice(client_scratch, ctx_factory, monkeypatch):
    from bizos.agents import merged_tools

    real = merged_tools.assess

    def mixed(scope, tool, arguments=None, *, record=True):
        checked = real(scope, tool, arguments, record=record)
        if tool == "admin_set_user_access":  # pretend this part would be queued for approval
            checked.effect = PolicyEffect.REQUIRE_APPROVAL
        return checked

    monkeypatch.setattr(merged_tools, "assess", mixed)
    admin = ctx_factory(client_scratch, "admin")
    scope_for, _, _ = _scope_for(client_scratch, admin)
    viewer = _user(client_scratch, "viewer")
    before = ({str(r) for r in viewer.roles}, set(viewer.data_scopes))
    with tenant_scope(admin):
        result = run("admin_update_user", {"user": viewer.email, "roles_add": ["APPROVER"],
                                           "access_grant": ["finance"]}, scope_for)
    assert result.outcomes == [] and "Make the two changes as separate requests." in result.refused
    after = _user(client_scratch, "viewer")
    assert ({str(r) for r in after.roles}, set(after.data_scopes)) == before
