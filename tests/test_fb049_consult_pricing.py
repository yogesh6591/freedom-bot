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
