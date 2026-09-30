"""
Custom resale price rules (FB-048) + consult pricing math (FB-049)
=================================================================

JeanneCAIO sets customer price and margin. No public package price, no per-seat.
Legacy package prices ($2K / $75K / module lists) are forbidden.

Face / placeholder values are allowed until Jeanne + Venu sign off — every
response is labeled so demos never look like a published price list.

FB-049 builds a consult quote from those rules + replaceable face scope weights
(domains, autonomy, integrations). Swap Admin → Pricing numbers (and optionally
FACE_SCOPE weights) after commercial sign-off — no redesign required.
"""

from __future__ import annotations

from typing import Any

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.tenancy.context import TenantContext
from bizos.util.timeutil import utcnow

#: Placeholder commercial face values — replace after Jeanne/Venu confirm.
DEFAULT_RULES: dict[str, Any] = {
    "placeholder": True,
    "disclaimer": (
        "Placeholder face values for FB-048. Replace after JeanneCAIO + Venu confirm. "
        "Not a public price list."
    ),
    "price_set_by": "JeanneCAIO",
    "confirm_by": "Venu",
    "allow_public_package_price": False,
    "allow_per_seat_pricing": False,
    "forbid_legacy_prices": True,
    "forbidden_legacy_examples": ["$2K", "$75K", "module package list"],
    "customer_price_usd": 12000.0,
    "cepoch_cost_usd": 7200.0,
    "margin_percent": 40.0,
    "quote_status": "pending_venu_confirm",
    "notes": (
        "Face quote for demo only. Jeanne sets final customer price; "
        "Venu confirms before contract."
    ),
}

#: Base package domains covered by customer_price_usd (FB-035 default package).
BASE_PACKAGE_DOMAINS: tuple[str, ...] = tuple(DEFAULT_ENABLED_DOMAINS)

#: Face add-on USD on top of base package — replace after Jeanne/Venu agree.
FACE_ADDON_USD: dict[str, float] = {
    "strategy": 1500.0,
    "finance": 2000.0,
    "brand": 1000.0,
    "legal": 1800.0,
}

#: Face autonomy multipliers — replace after commercial rules are set.
FACE_AUTONOMY_FACTOR: dict[str, float] = {
    "ADVISE": 0.95,
    "DRAFT": 1.0,
    "WAIT_FOR_APPROVAL": 1.05,
    "AUTO_WITHIN_SCOPE": 1.12,
}

#: Face integration fees — first N free, then per extra connector.
FACE_INTEGRATIONS_INCLUDED = 2
FACE_INTEGRATION_USD = 250.0


#: Commercial guardrails that hold whether or not a price has been set.
_GUARDRAILS: dict[str, Any] = {
    k: DEFAULT_RULES[k]
    for k in (
        "price_set_by",
        "confirm_by",
        "allow_public_package_price",
        "allow_per_seat_pricing",
        "forbid_legacy_prices",
        "forbidden_legacy_examples",
    )
}


class PricingNotConfigured(ValueError):
    """No resale rules have been set for this client, so no price can be computed."""


def is_configured(rules: dict[str, Any]) -> bool:
    return bool(rules.get("configured")) and rules.get("customer_price_usd") is not None


def get_rules(settings: ClientSettings) -> dict[str, Any]:
    """This client's resale rules.

    A client with nothing stored gets the guardrails and **no numbers**
    (``configured: False``). The demo face values in :data:`DEFAULT_RULES` are
    written only by :func:`ensure_default_rules`; they are never a fallback, or
    every new client would inherit another client's price.
    """
    raw = getattr(settings, "resale_pricing", None) or {}
    if isinstance(raw, dict) and raw.get("customer_price_usd") is not None:
        merged = {**DEFAULT_RULES, **raw, "configured": True}
        # Hard rules that cannot be turned off by a stale row.
        merged["allow_public_package_price"] = False
        merged["allow_per_seat_pricing"] = False
        merged["forbid_legacy_prices"] = True
        merged["price_set_by"] = merged.get("price_set_by") or "JeanneCAIO"
        merged["confirm_by"] = merged.get("confirm_by") or "Venu"
        return merged
    unconfigured = {
        **_GUARDRAILS,
        "placeholder": False,
        "configured": False,
        "disclaimer": "No resale price rules are set for this client. Enter them before quoting.",
        "customer_price_usd": None,
        "cepoch_cost_usd": None,
        "margin_percent": None,
        "quote_status": "not_configured",
        "notes": "",
    }
    if isinstance(raw, dict) and isinstance(raw.get("consult_quotes"), list):
        unconfigured["consult_quotes"] = raw["consult_quotes"]
    return unconfigured


def save_rules(
    *,
    client_id: str,
    settings: ClientSettings,
    rules: dict[str, Any],
    ctx: TenantContext,
) -> dict[str, Any]:
    """Persist editable face fields; keep commercial guardrails locked."""
    current = get_rules(settings)

    def pick(field: str) -> float:
        value = rules.get(field)
        if value is None:
            value = current.get(field)
        if value is None:
            raise ValueError(f"{field} is required — no resale rule is set for this client yet")
        return float(value)

    customer = pick("customer_price_usd")
    cost = pick("cepoch_cost_usd")
    if customer < 0 or cost < 0:
        raise ValueError("prices must be non-negative")
    margin = ((customer - cost) / customer * 100.0) if customer > 0 else 0.0
    status = str(rules.get("quote_status") or current.get("quote_status") or "pending_venu_confirm")
    if status not in {"pending_venu_confirm", "venu_confirmed", "draft"}:
        status = "pending_venu_confirm"

    stored = {
        "placeholder": True,
        "disclaimer": DEFAULT_RULES["disclaimer"],
        "price_set_by": "JeanneCAIO",
        "confirm_by": "Venu",
        "allow_public_package_price": False,
        "allow_per_seat_pricing": False,
        "forbid_legacy_prices": True,
        "forbidden_legacy_examples": list(DEFAULT_RULES["forbidden_legacy_examples"]),
        "configured": True,
        "customer_price_usd": round(customer, 2),
        "cepoch_cost_usd": round(cost, 2),
        "margin_percent": round(margin, 1),
        "quote_status": status,
        "notes": str(rules.get("notes") or current.get("notes") or "")[:2000],
        "updated_at": utcnow().isoformat(),
        "updated_by": ctx.user_id,
    }
    # Preserve any saved consult quotes under the same blob.
    raw = getattr(settings, "resale_pricing", None) or {}
    if isinstance(raw, dict) and isinstance(raw.get("consult_quotes"), list):
        stored["consult_quotes"] = raw["consult_quotes"]

    payload = settings.to_dict()
    payload["resale_pricing"] = stored
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    return stored


def ensure_default_rules(
    *, client_id: str, settings: ClientSettings, actor: str = "__bootstrap__"
) -> None:
    if isinstance(getattr(settings, "resale_pricing", None), dict) and settings.resale_pricing:
        return
    payload = settings.to_dict()
    payload["resale_pricing"] = dict(DEFAULT_RULES)
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=actor
    )


def face_scope_config() -> dict[str, Any]:
    """Weights used by the consult calculator — all face / replaceable."""
    return {
        "placeholder": True,
        "disclaimer": (
            "FB-049 face scope weights. Replace after JeanneCAIO + Venu confirm commercials."
        ),
        "base_package_domains": list(BASE_PACKAGE_DOMAINS),
        "addon_usd": dict(FACE_ADDON_USD),
        "autonomy_factor": dict(FACE_AUTONOMY_FACTOR),
        "integrations_included": FACE_INTEGRATIONS_INCLUDED,
        "integration_usd": FACE_INTEGRATION_USD,
        "no_public_package_price": True,
        "no_per_seat_pricing": True,
    }


def calculate_consult_quote(
    settings: ClientSettings,
    *,
    domains: list[str],
    autonomy: str = "WAIT_FOR_APPROVAL",
    integrations: int = 0,
) -> dict[str, Any]:
    """
    FB-049: compute customer price / cost / margin from FB-048 rules + face weights.

    Base package domains are covered by rules.customer_price_usd.
    Add-ons, autonomy, and extra integrations adjust the face total.
    Cost tracks the base cost/price ratio so margin intent stays consistent.
    """
    rules = get_rules(settings)
    if not is_configured(rules):
        raise PricingNotConfigured(
            "No resale price rules are set for this client. Set customer price and "
            "Cepoch cost under Admin → Pricing before calculating a quote."
        )
    base_price = float(rules["customer_price_usd"])
    base_cost = float(rules["cepoch_cost_usd"] or 0)
    if base_price < 0 or base_cost < 0:
        raise ValueError("base prices must be non-negative")

    requested = []
    seen: set[str] = set()
    for raw in domains:
        name = (raw or "").strip().casefold()
        if not name or name in seen:
            continue
        if name not in DOMAIN_NAMES:
            raise ValueError(f"unknown domain: {name}")
        seen.add(name)
        requested.append(name)

    if "general" not in seen:
        requested.insert(0, "general")
        seen.add("general")

    base_set = set(BASE_PACKAGE_DOMAINS)
    addons = [d for d in requested if d not in base_set]
    unknown_addons = [d for d in addons if d not in FACE_ADDON_USD]
    if unknown_addons:
        raise ValueError(f"no face add-on price for: {', '.join(unknown_addons)}")

    addon_lines = [
        {"domain": d, "customer_usd": FACE_ADDON_USD[d], "kind": "addon"} for d in addons
    ]
    addon_total = sum(line["customer_usd"] for line in addon_lines)

    mode = (autonomy or "WAIT_FOR_APPROVAL").strip().upper()
    if mode not in FACE_AUTONOMY_FACTOR:
        raise ValueError(f"unknown autonomy mode: {mode}")
    factor = FACE_AUTONOMY_FACTOR[mode]

    integ = max(0, int(integrations))
    extra_integ = max(0, integ - FACE_INTEGRATIONS_INCLUDED)
    integ_fee = extra_integ * FACE_INTEGRATION_USD

    subtotal = base_price + addon_total + integ_fee
    customer = round(subtotal * factor, 2)
    # Preserve face margin ratio from FB-048 base rules.
    cost_ratio = (base_cost / base_price) if base_price > 0 else 0.6
    cost = round(customer * cost_ratio, 2)
    margin = round(((customer - cost) / customer * 100.0) if customer > 0 else 0.0, 1)

    return {
        "placeholder": True,
        "disclaimer": (
            "FB-049 consult quote from face FB-048 rules + face scope weights. "
            "Replace numbers after JeanneCAIO + Venu confirm."
        ),
        "price_set_by": rules.get("price_set_by") or "JeanneCAIO",
        "confirm_by": rules.get("confirm_by") or "Venu",
        "quote_status": rules.get("quote_status") or "pending_venu_confirm",
        "rules_base": {
            "customer_price_usd": base_price,
            "cepoch_cost_usd": base_cost,
            "margin_percent": rules.get("margin_percent"),
        },
        "inputs": {
            "domains": requested,
            "addons": addons,
            "autonomy": mode,
            "autonomy_factor": factor,
            "integrations": integ,
            "integrations_included": FACE_INTEGRATIONS_INCLUDED,
            "extra_integrations": extra_integ,
        },
        "lines": [
            {
                "domain": "base_package",
                "customer_usd": base_price,
                "kind": "base",
                "domains": list(BASE_PACKAGE_DOMAINS),
            },
            *addon_lines,
            *(
                [
                    {
                        "domain": "extra_integrations",
                        "customer_usd": integ_fee,
                        "kind": "integrations",
                        "count": extra_integ,
                    }
                ]
                if integ_fee
                else []
            ),
        ],
        "subtotal_usd": round(subtotal, 2),
        "customer_price_usd": customer,
        "cepoch_cost_usd": cost,
        "margin_percent": margin,
        "allow_public_package_price": False,
        "allow_per_seat_pricing": False,
        "calculated_at": utcnow().isoformat(),
    }


def save_consult_quote(
    *,
    client_id: str,
    settings: ClientSettings,
    quote: dict[str, Any],
    ctx: TenantContext,
    label: str = "",
) -> dict[str, Any]:
    """Persist a calculated consult quote under resale_pricing.consult_quotes."""
    rules = get_rules(settings)
    history = list(rules.get("consult_quotes") or [])
    entry = {
        **quote,
        "id": f"cq_{len(history) + 1}_{int(utcnow().timestamp())}",
        "label": (label or "Consult quote")[:200],
        "saved_at": utcnow().isoformat(),
        "saved_by": ctx.user_id,
    }
    history.append(entry)
    # Keep last 20 demos.
    history = history[-20:]

    stored = {k: v for k, v in rules.items() if k != "consult_quotes"}
    stored["consult_quotes"] = history
    stored["last_consult_quote_id"] = entry["id"]
    stored["updated_at"] = utcnow().isoformat()
    stored["updated_by"] = ctx.user_id

    payload = settings.to_dict()
    payload["resale_pricing"] = stored
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    return entry
