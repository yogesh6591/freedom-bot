"""FB-048 — custom resale price rules (face values)."""

from __future__ import annotations

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.pricing import DEFAULT_RULES, get_rules, save_rules
from bizos.tenancy.context import tenant_scope


def test_default_resale_rules_forbid_public_and_per_seat(client_a):
    rules = get_rules(client_a.settings)
    assert rules["allow_public_package_price"] is False
    assert rules["allow_per_seat_pricing"] is False
    assert rules["forbid_legacy_prices"] is True
    assert rules["price_set_by"] == "JeanneCAIO"
    assert rules["confirm_by"] == "Venu"
    assert "$2K" in rules["forbidden_legacy_examples"]
    # A client nobody has priced has no price — not the demo face value (M01-20).
    assert rules["configured"] is False
    assert rules["customer_price_usd"] is None
    assert rules["margin_percent"] is None


def test_save_resale_rules_computes_margin(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with tenant_scope(ctx):
            saved = save_rules(
                client_id=client_a.id,
                settings=control.get_client(client_a.id).settings,
                rules={
                    "customer_price_usd": 10000,
                    "cepoch_cost_usd": 6000,
                    "quote_status": "pending_venu_confirm",
                    "notes": "Face quote",
                },
                ctx=ctx,
            )
        assert saved["margin_percent"] == 40.0
        assert saved["allow_public_package_price"] is False
        stored = get_rules(control.get_client(client_a.id).settings)
        assert stored["customer_price_usd"] == 10000.0
        assert stored["placeholder"] is True
        # Guardrails stay locked even if caller tried to open them.
        assert DEFAULT_RULES["forbid_legacy_prices"] is True
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )
