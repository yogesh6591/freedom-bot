"""FB-043 auto handoff + FB-044 baselines/exclusions + FB-045 launch checklist."""

from __future__ import annotations

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.insights import handoff_assessment
from bizos.launch import checklist_summary, mark_item, sign_off
from bizos.onboarding import get_profile
from bizos.tenancy.context import tenant_scope


def test_handoff_from_checkout_records_source(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with tenant_scope(ctx):
            result = handoff_assessment(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                assessment={
                    "goals": ["Cut invoice cycle time"],
                    "systems": ["HubSpot"],
                    "exclusions": ["No tax filing"],
                    "baselines": [
                        {
                            "workflow": "Invoices",
                            "metric": "Days to close",
                            "before_value": "12",
                            "unit": "days",
                        }
                    ],
                },
                ctx=ctx,
                source="checkout",
                event="checkout",
            )
        assert result["handoff_source"] == "checkout"
        profile = get_profile(control.get_client(client_a.id).settings)
        assert profile.goals == ["Cut invoice cycle time"]
        assert "No tax filing" in profile.exclusions
        assert profile.baselines
        blob = control.get_client(client_a.id).settings.onboarding or {}
        assert blob.get("handoff_source") == "checkout"
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )


def test_launch_checklist_sign_off(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        settings = control.get_client(client_a.id).settings
        # Prefill enough for auto items.
        with tenant_scope(ctx):
            handoff_assessment(
                client_id=client_a.id,
                settings=settings,
                assessment={
                    "goals": ["Go live"],
                    "systems": ["CRM"],
                    "exclusions": ["Out of scope: marketing"],
                    "baselines": [
                        {
                            "workflow": "Lead response",
                            "metric": "Minutes",
                            "before_value": "240",
                        }
                    ],
                },
                ctx=ctx,
                source="contract",
            )
        settings = control.get_client(client_a.id).settings
        for item_id in ("isolation_tests", "order_dpa", "roles_approvers", "smoke_chat"):
            summary = mark_item(
                client_id=client_a.id,
                settings=settings,
                item_id=item_id,
                status="done",
                actor=ctx.user_id,
            )
            settings = control.get_client(client_a.id).settings
        assert summary["ready"] is True
        signed = sign_off(client_id=client_a.id, settings=settings, actor=ctx.user_id)
        assert signed["signed_off_at"]
        assert checklist_summary(control.get_client(client_a.id).settings)["ready"]
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )
