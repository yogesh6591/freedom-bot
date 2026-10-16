"""FB-049 — consult pricing math on face FB-048 rules."""

from __future__ import annotations

from bizos.control import store as control
from bizos.control.models import ClientSettings
import pytest

from bizos.pricing import (
    DEFAULT_RULES,
    FACE_ADDON_USD,
    PricingNotConfigured,
    calculate_consult_quote,
    face_scope_config,
    save_consult_quote,
    save_rules,
)
from bizos.tenancy.context import tenant_scope


def _face_priced(client) -> ClientSettings:
    """The client's settings with the demo face rules applied, as bootstrap does for Acme."""
    return ClientSettings.from_dict(
        {**client.settings.to_dict(), "resale_pricing": dict(DEFAULT_RULES)}
    )


def test_consult_quote_refuses_without_rules(client_a):
    with pytest.raises(PricingNotConfigured):
        calculate_consult_quote(client_a.settings, domains=["general"])


def test_face_scope_config_has_no_public_or_per_seat():
    cfg = face_scope_config()
    assert cfg["placeholder"] is True
    assert cfg["no_public_package_price"] is True
    assert cfg["no_per_seat_pricing"] is True
    assert "finance" in cfg["addon_usd"]


def test_consult_quote_base_package_matches_rules(client_a):
    quote = calculate_consult_quote(
        _face_priced(client_a),
        domains=["general", "operations", "sales", "intake", "planning"],
        autonomy="DRAFT",
        integrations=2,
    )
    assert quote["placeholder"] is True
    assert quote["customer_price_usd"] == 12000.0
    assert quote["cepoch_cost_usd"] == 7200.0
    assert quote["margin_percent"] == 40.0
    assert quote["inputs"]["addons"] == []
    assert quote["allow_per_seat_pricing"] is False


def test_consult_quote_addons_and_autonomy_and_integrations(client_a):
    quote = calculate_consult_quote(
        _face_priced(client_a),
        domains=["operations", "finance", "legal"],
        autonomy="WAIT_FOR_APPROVAL",
        integrations=4,
    )
    # base 12000 + finance 2000 + legal 1800 + 2 extra integ * 250 = 16300
    # × 1.05 autonomy = 17115
    assert quote["subtotal_usd"] == 12000 + FACE_ADDON_USD["finance"] + FACE_ADDON_USD["legal"] + 500
    assert quote["customer_price_usd"] == round(quote["subtotal_usd"] * 1.05, 2)
    assert "finance" in quote["inputs"]["addons"]
    assert "legal" in quote["inputs"]["addons"]
    assert "general" in quote["inputs"]["domains"]
    assert quote["inputs"]["extra_integrations"] == 2
    # Cost tracks base ratio 7200/12000 = 0.6
    assert quote["cepoch_cost_usd"] == round(quote["customer_price_usd"] * 0.6, 2)
    assert quote["margin_percent"] == 40.0


def test_consult_quote_rejects_unknown_domain(client_a):
    try:
        calculate_consult_quote(_face_priced(client_a), domains=["not-a-domain"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown domain" in str(exc)


def test_save_consult_quote_persists(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        with tenant_scope(ctx):
            settings = control.get_client(client_a.id).settings
            save_rules(
                client_id=client_a.id,
                settings=settings,
                rules={
                    "customer_price_usd": 10000,
                    "cepoch_cost_usd": 6000,
                    "quote_status": "pending_venu_confirm",
                    "notes": "face",
                },
                ctx=ctx,
            )
            settings = control.get_client(client_a.id).settings
            quote = calculate_consult_quote(
                settings,
                domains=["general", "operations", "sales", "intake", "planning", "brand"],
                autonomy="DRAFT",
                integrations=0,
            )
            # 10000 + brand 1000 = 11000
            assert quote["customer_price_usd"] == 11000.0
            saved = save_consult_quote(
                client_id=client_a.id,
                settings=settings,
                quote=quote,
                ctx=ctx,
                label="Demo face quote",
            )
        assert saved["id"]
        stored = control.get_client(client_a.id).settings.resale_pricing or {}
        assert stored.get("last_consult_quote_id") == saved["id"]
        assert any(q["id"] == saved["id"] for q in stored.get("consult_quotes") or [])
    finally:
        control.update_client_settings(
            client_a.id, ClientSettings.from_dict(original), updated_by="pytest"
        )


def _priced_with(client, ctx, **rules):
    with tenant_scope(ctx):
        return save_rules(
            client_id=client.id,
            settings=control.get_client(client.id).settings,
            rules=rules,
            ctx=ctx,
        )


def test_jeanne_sets_margin_and_her_own_scope_weights(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        rules = _priced_with(
            client_a,
            ctx,
            customer_price_usd=20000,
            margin_percent=45,
            scope_rules={
                "addon_usd": {"finance": 3500, "legal": 2500},
                "integrations_included": 1,
                "integration_usd": 400,
                "round_to_usd": 500,
            },
        )
        assert rules["cepoch_cost_usd"] == 11000.0 and rules["margin_percent"] == 45.0
        cfg = face_scope_config(control.get_client(client_a.id).settings)
        assert cfg["addon_usd"] == {"finance": 3500.0, "legal": 2500.0}
        assert cfg["placeholder"] is False

        quote = calculate_consult_quote(
            control.get_client(client_a.id).settings,
            domains=["finance"],
            autonomy="DRAFT",
            integrations=3,
        )
        # 20000 + 3500 + 2 extra × 400 = 24300, rounded up to 24500.
        assert quote["subtotal_usd"] == 24300.0
        assert quote["customer_price_usd"] == 24500.0
        assert [line["kind"] for line in quote["lines"]] == ["base", "addon", "integrations", "rounding"]
        assert "per seat" in quote["talk_track"] and "$24,500" in quote["talk_track"]

        # Strategy is no longer priced for this client, so it cannot be quoted.
        with pytest.raises(ValueError, match="no add-on price set for: strategy"):
            calculate_consult_quote(
                control.get_client(client_a.id).settings, domains=["strategy"], autonomy="DRAFT"
            )
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_legacy_prices_and_negative_margins_are_refused(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        for bad in (2000, 75000):
            with pytest.raises(ValueError, match="old package price"):
                _priced_with(client_a, ctx, customer_price_usd=bad, cepoch_cost_usd=100)
        with pytest.raises(ValueError, match="negative margin"):
            _priced_with(client_a, ctx, customer_price_usd=10000, cepoch_cost_usd=12000)
        with pytest.raises(ValueError, match="old package price"):
            _priced_with(
                client_a, ctx, customer_price_usd=10000, cepoch_cost_usd=6000,
                scope_rules={"addon_usd": {"legal": 2000}},
            )
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_venu_confirmation_is_recorded_and_reset_by_any_change(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        _priced_with(client_a, ctx, customer_price_usd=10000, cepoch_cost_usd=6000)
        confirmed = _priced_with(client_a, ctx, quote_status="venu_confirmed")
        assert confirmed["quote_status"] == "venu_confirmed"
        assert confirmed["placeholder"] is False and confirmed["confirmed_by"] == ctx.user_id
        quote = calculate_consult_quote(control.get_client(client_a.id).settings, domains=[], autonomy="DRAFT")
        assert quote["placeholder"] is False and "Pending Venu" not in quote["talk_track"]

        # Notes alone keep the confirmation; a new number needs Venu again.
        assert _priced_with(client_a, ctx, notes="call notes")["quote_status"] == "venu_confirmed"
        changed = _priced_with(client_a, ctx, customer_price_usd=11000, quote_status="venu_confirmed")
        assert changed["quote_status"] == "pending_venu_confirm"
        assert changed["reconfirm_needed"] is True and changed["placeholder"] is True
        assert [h["event"] for h in changed["rules_history"]][-2:] == ["confirmed", "reconfirm_needed"]
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")


def test_discount_is_capped_and_margin_floor_blocks_saving(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    original = control.get_client(client_a.id).settings.to_dict()
    try:
        _priced_with(
            client_a, ctx, customer_price_usd=10000, cepoch_cost_usd=6000,
            scope_rules={"min_margin_percent": 35, "max_discount_percent": 20},
        )
        settings = control.get_client(client_a.id).settings
        with pytest.raises(ValueError, match="over the 20% cap"):
            calculate_consult_quote(settings, domains=[], autonomy="DRAFT", discount_percent=25)
        ok = calculate_consult_quote(settings, domains=[], autonomy="DRAFT", discount_percent=5)
        # 10000 − 5% = 9500 against unchanged cost 6000 → 36.8%
        assert ok["customer_price_usd"] == 9500.0 and ok["cepoch_cost_usd"] == 6000.0
        assert ok["margin_percent"] == 36.8 and ok["below_floor"] is False
        thin = calculate_consult_quote(settings, domains=[], autonomy="DRAFT", discount_percent=15)
        assert thin["below_floor"] is True and any("floor" in w for w in thin["warnings"])
        with tenant_scope(ctx), pytest.raises(ValueError, match="margin floor"):
            save_consult_quote(client_id=client_a.id, settings=settings, quote=thin, ctx=ctx)
    finally:
        control.update_client_settings(client_a.id, ClientSettings.from_dict(original), updated_by="pytest")
