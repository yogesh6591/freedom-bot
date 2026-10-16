"""FB-041 / FB-043 / FB-050 — alerts, assessment handoff, baselines."""

from __future__ import annotations

from sqlalchemy import text

from bizos import chat_history, insights
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.insights import (
    dismiss_alert,
    handoff_assessment,
    list_alerts,
    list_baselines,
    open_alerts,
    profile_from_assessment,
    save_baselines,
)
from bizos.memory import store as memory
from bizos.tenancy.context import tenant_scope
from bizos.tenancy.registry import workspace_connection
from bizos.types import ApprovalStatus, MemoryCategory
from bizos.util.ids import new_id


def test_profile_from_assessment_maps_fields():
    profile = profile_from_assessment(
        {
            "pain": "Slow payroll close",
            "goals": ["Cut payroll cycle time"],
            "systems": ["Workspace CRM", "n8n"],
            "sops": [{"title": "Payroll SOP", "content": "Upload then approve."}],
            "roles_notes": "Ops runs; Exec approves.",
            "autonomy_mode": "DRAFT",
        }
    )
    assert "Cut payroll cycle time" in profile.goals
    assert "Workspace CRM" in profile.systems
    assert profile.sops[0]["title"] == "Payroll SOP"
    assert profile.autonomy_mode == "DRAFT"
    assert profile.assessment["pain"] == "Slow payroll close"


def test_handoff_prefills_onboarding(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with tenant_scope(ctx):
            result = handoff_assessment(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                assessment={
                    "goals": ["Ship intake automation"],
                    "systems": ["Email", "CRM"],
                    "priority": "lead response",
                },
                ctx=ctx,
            )
        assert result["profile"]["goals"] == ["Ship intake automation"]
        assert "Email" in result["profile"]["systems"]
        stored = control.get_client(client_a.id).settings.onboarding
        assert stored.get("handoff_at")
        assert stored.get("assessment", {}).get("priority") == "lead response"
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )


def _reset_alerts(client) -> dict:
    original = control.get_client(client.id).settings.to_dict()
    payload = dict(original)
    payload["onboarding"] = {**(original.get("onboarding") or {}), "inefficiency_alerts": []}
    control.update_client_settings(client.id, ClientSettings.from_dict(payload), updated_by="pytest")
    return original


def _scan(client, ctx):
    with tenant_scope(ctx):
        return insights.scan_alerts(
            client_id=client.id, settings=control.get_client(client.id).settings, ctx=ctx
        )


def _ask(people, question: str, tag: str) -> None:
    for i, person in enumerate(people):
        chat_history.record_turn(
            person, session_id=f"ses_fb041_{tag}_{i}", user_message=question,
            reply="Not recorded.", domain="general", meta={},
        )


def test_hand_written_demo_alerts_are_not_shown(client_a):
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        payload = dict(original)
        payload["onboarding"] = {
            **(original.get("onboarding") or {}),
            "inefficiency_alerts": [
                {"id": "alert_payroll_delay", "title": "Payroll", "detail": "", "status": "open", "source": "demo"}
            ],
        }
        control.update_client_settings(client_a.id, ClientSettings.from_dict(payload), updated_by="pytest")
        assert list_alerts(control.get_client(client_a.id).settings) == []
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_repeated_question_alert_follows_memory_and_dismisses(client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    original = _reset_alerts(client_a)
    question = "How do we handle pallet damage claims with the carrier?"
    try:
        _ask([ctx_factory(client_a, p) for p in ("viewer", "drafter", "operator")], question, "pallet")
        alert = next(a for a in _scan(client_a, admin) if a.get("kind") == "repeated_question")
        assert alert["status"] == "open" and "No approved fact or SOP" in alert["detail"]
        assert "$" in alert["directional_cost"]

        with tenant_scope(admin):
            dismiss_alert(
                client_id=client_a.id, settings=control.get_client(client_a.id).settings,
                alert_id=alert["id"], ctx=admin,
            )
        assert alert["id"] not in {a["id"] for a in open_alerts(control.get_client(client_a.id).settings)}

        # Once the answer is recorded in memory, the same questions are no longer waste.
        _reset_alerts(client_a)
        with tenant_scope(admin):
            memory.put(
                category=MemoryCategory.SOP, title="Pallet damage claims with the carrier",
                content="Photograph the pallet, file the carrier claim within 48 hours, notify ops.",
                approval_status=ApprovalStatus.APPROVED, ctx=admin,
            )
        assert not [a for a in _scan(client_a, admin) if a.get("kind") == "repeated_question"
                    and a["id"] == alert["id"] and a["status"] == "open"]
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_pending_memory_alert_from_unapproved_knowledge(client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    original = _reset_alerts(client_a)
    ids = []
    try:
        with tenant_scope(admin):
            for i in range(insights.MIN_OCCURRENCES):
                item = memory.put(
                    category=MemoryCategory.FACT, title=f"FB041 pending fact {i}",
                    content=f"Value {i}", approval_status=ApprovalStatus.PENDING, ctx=admin,
                )
                ids.append(item.id)
        with workspace_connection(admin) as conn:
            conn.execute(
                text("UPDATE memory_versions SET created_at = NOW() - INTERVAL '5 days' WHERE item_id = ANY(:ids)"),
                {"ids": ids},
            )
        alert = next(a for a in _scan(client_a, admin) if a["id"] == "scan_pending_memory")
        assert alert["evidence"]["count"] >= insights.MIN_OCCURRENCES
    finally:
        with workspace_connection(admin) as conn:
            conn.execute(text("DELETE FROM memory_items WHERE id = ANY(:ids)"), {"ids": ids})
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_senior_routine_alert_from_user_actions(client_a, ctx_factory):
    admin = ctx_factory(client_a, "admin")
    original = _reset_alerts(client_a)
    ids = [new_id("act") for _ in range(insights.MIN_OCCURRENCES)]
    try:
        with workspace_connection(admin) as conn:
            for action_id in ids:
                conn.execute(
                    text(
                        "INSERT INTO actions (id, title, status, tool, risk_level, execution_mode, "
                        "requested_by, requested_by_role) VALUES (:i, 'Tag contact', 'COMPLETED', "
                        "'crm_tag_contact', 'LOW', 'AUTO_WITHIN_SCOPE', :u, 'ADMIN')"
                    ),
                    {"i": action_id, "u": admin.user_id},
                )
        alert = next(a for a in _scan(client_a, admin) if a.get("kind") == "senior_routine")
        assert alert["evidence"]["count"] >= insights.MIN_OCCURRENCES
        assert "senior time" in alert["directional_cost"]
    finally:
        with workspace_connection(admin) as conn:
            conn.execute(text("DELETE FROM actions WHERE id = ANY(:ids)"), {"ids": ids})
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_hourly_rates_override_changes_directional_dollars():
    custom = ClientSettings.from_dict({"onboarding": {"hourly_rates": {"senior": 240}}})
    assert insights._rates(custom)["senior"] == 240.0
    assert insights._dollars(60, 240.0) == "~$240"


def test_baselines_save_roundtrip(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with tenant_scope(ctx):
            saved = save_baselines(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                baselines=[
                    {
                        "id": "baseline_payroll_attendance",
                        "workflow": "Monthly payroll from attendance",
                        "metric": "Cycle time",
                        "before_value": "3.5",
                        "after_value": "1.5",
                        "unit": "business days",
                        "notes": "After bot-assisted path.",
                    }
                ],
                ctx=ctx,
            )
        assert saved[0]["after_value"] == "1.5"
        items = list_baselines(control.get_client(client_a.id).settings)
        assert items[0]["after_value"] == "1.5"
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )
