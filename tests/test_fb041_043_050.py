"""FB-041 / FB-043 / FB-050 — alerts, assessment handoff, baselines."""

from __future__ import annotations

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.insights import (
    DEFAULT_ALERTS,
    ensure_default_alerts,
    dismiss_alert,
    handoff_assessment,
    list_alerts,
    list_baselines,
    open_alerts,
    profile_from_assessment,
    save_baselines,
)
from bizos.tenancy.context import tenant_scope


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


def test_alerts_default_and_dismiss(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        settings = control.get_client(client_a.id).settings
        # Clear then use defaults from helper.
        payload = settings.to_dict()
        payload["onboarding"] = {**(settings.onboarding or {}), "inefficiency_alerts": []}
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(payload), updated_by="pytest"
        )
        settings = control.get_client(client_a.id).settings
        # No stored alerts means no alerts — demo text is never a fallback (M01-20).
        assert list_alerts(settings) == []
        ensure_default_alerts(client_id=client_a.id, settings=settings, actor="pytest")
        settings = control.get_client(client_a.id).settings
        alerts = list_alerts(settings)
        assert len(alerts) >= 2
        assert {a["id"] for a in alerts} >= {a["id"] for a in DEFAULT_ALERTS}
        with tenant_scope(ctx):
            dismiss_alert(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                alert_id=DEFAULT_ALERTS[0]["id"],
                ctx=ctx,
            )
        settings = control.get_client(client_a.id).settings
        open_ids = {a["id"] for a in open_alerts(settings)}
        assert DEFAULT_ALERTS[0]["id"] not in open_ids
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )


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
