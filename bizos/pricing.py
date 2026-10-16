"""
Custom resale price rules (FB-048) + consult pricing math (FB-049)
=================================================================

JeanneCAIO sets customer price and margin. No public package price, no per-seat.
Legacy package prices ($2K / $75K / module lists) are refused.

Every number the calculator uses is one of her rules for this client: the base
package price, Cepoch cost (or target margin), add-on domain prices, autonomy
multipliers, integration fees, rounding, margin floor and discount cap. The
shipped face values are only a starting point until she edits them.

Venu confirms commercials. Confirmation is recorded with who/when, and any later
change to a number puts the rules back to "pending Venu" — a confirmed label can
never sit on numbers Venu has not seen.

FB-049 turns those rules into a quote on the call: pick scope, the price, cost,
margin, floor check and a line-by-line breakdown come back instantly.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.tenancy.context import TenantContext
from bizos.util.timeutil import utcnow

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending_venu_confirm"
STATUS_CONFIRMED = "venu_confirmed"
STATUSES = (STATUS_DRAFT, STATUS_PENDING, STATUS_CONFIRMED)

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
    "quote_status": STATUS_PENDING,
    "notes": (
        "Face quote for demo only. Jeanne sets final customer price; "
        "Venu confirms before contract."
    ),
}

#: Old package prices that must never come back as a quote or a rule.
LEGACY_PRICES_USD: tuple[float, ...] = (2000.0, 75000.0)

#: Base package domains covered by customer_price_usd (FB-035 default package).
BASE_PACKAGE_DOMAINS: tuple[str, ...] = tuple(DEFAULT_ENABLED_DOMAINS)

#: Face add-on USD on top of base package — Jeanne replaces these per client.
FACE_ADDON_USD: dict[str, float] = {
    "strategy": 1500.0,
    "finance": 2000.0,
    "brand": 1000.0,
    "legal": 1800.0,
}

#: Face autonomy multipliers — more autonomy is more build and more liability.
FACE_AUTONOMY_FACTOR: dict[str, float] = {
    "ADVISE": 0.95,
    "DRAFT": 1.0,
    "WAIT_FOR_APPROVAL": 1.05,
    "AUTO_WITHIN_SCOPE": 1.12,
}

#: Face integration fees — first N free, then per extra connector.
FACE_INTEGRATIONS_INCLUDED = 2
FACE_INTEGRATION_USD = 250.0

#: Face scope rules. Rounding 0 = exact cents; floor/cap are percentages.
DEFAULT_SCOPE_RULES: dict[str, Any] = {
    "addon_usd": dict(FACE_ADDON_USD),
    "autonomy_factor": dict(FACE_AUTONOMY_FACTOR),
    "integrations_included": FACE_INTEGRATIONS_INCLUDED,
    "integration_usd": FACE_INTEGRATION_USD,
    "round_to_usd": 0.0,
    "min_margin_percent": 30.0,
    "max_discount_percent": 15.0,
}

RULES_HISTORY_LIMIT = 12
SAVED_QUOTES_LIMIT = 20


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

#: Fields kept alongside the rules across saves.
_CARRIED = ("consult_quotes", "last_consult_quote_id", "rules_history", "scope_rules")


class PricingNotConfigured(ValueError):
    """No resale rules have been set for this client, so no price can be computed."""


def is_configured(rules: dict[str, Any]) -> bool:
    return bool(rules.get("configured")) and rules.get("customer_price_usd") is not None


def _raw(settings: ClientSettings) -> dict[str, Any]:
    raw = getattr(settings, "resale_pricing", None) or {}
    return raw if isinstance(raw, dict) else {}


def scope_rules(settings: ClientSettings) -> dict[str, Any]:
    """This client's scope weights: stored values over the face defaults."""
    stored = _raw(settings).get("scope_rules") or {}
    merged = {**DEFAULT_SCOPE_RULES, **{k: v for k, v in stored.items() if v is not None}}
    merged["addon_usd"] = {
        **({} if "addon_usd" in stored else DEFAULT_SCOPE_RULES["addon_usd"]),
        **(stored.get("addon_usd") or {}),
    }
    merged["autonomy_factor"] = {
        **DEFAULT_SCOPE_RULES["autonomy_factor"],
        **(stored.get("autonomy_factor") or {}),
    }
    merged["customized"] = bool(stored)
    return merged


def get_rules(settings: ClientSettings) -> dict[str, Any]:
    """This client's resale rules.

    A client with nothing stored gets the guardrails and **no numbers**
    (``configured: False``). The demo face values in :data:`DEFAULT_RULES` are
    written only by :func:`ensure_default_rules`; they are never a fallback, or
    every new client would inherit another client's price.
    """
    raw = _raw(settings)
    if raw.get("customer_price_usd") is not None:
        merged = {**DEFAULT_RULES, **raw, "configured": True}
        # Hard rules that cannot be turned off by a stale row.
        merged["allow_public_package_price"] = False
        merged["allow_per_seat_pricing"] = False
        merged["forbid_legacy_prices"] = True
        merged["price_set_by"] = merged.get("price_set_by") or "JeanneCAIO"
        merged["confirm_by"] = merged.get("confirm_by") or "Venu"
        merged["placeholder"] = merged.get("quote_status") != STATUS_CONFIRMED
        merged["scope_rules"] = scope_rules(settings)
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
        "scope_rules": scope_rules(settings),
    }
    for key in _CARRIED:
        if key in raw and key != "scope_rules":
            unconfigured[key] = raw[key]
    return unconfigured


def _is_legacy(value: float) -> bool:
    return any(math.isclose(value, legacy) for legacy in LEGACY_PRICES_USD)


def _clean_scope(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Validate an edited scope-rules block; unknown keys are dropped."""

    def num(value: Any, field: str, *, lo: float = 0.0, hi: Optional[float] = None) -> float:
        try:
            out = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a number") from exc
        if out < lo or (hi is not None and out > hi):
            raise ValueError(f"{field} must be between {lo:g} and {hi:g}" if hi is not None else f"{field} must be {lo:g} or more")
        return out

    out = {k: current[k] for k in DEFAULT_SCOPE_RULES}
    if "addon_usd" in incoming:
        base = set(BASE_PACKAGE_DOMAINS)
        addons: dict[str, float] = {}
        for domain, price in (incoming.get("addon_usd") or {}).items():
            name = str(domain).strip().casefold()
            if name not in DOMAIN_NAMES:
                raise ValueError(f"unknown domain: {name}")
            if name in base:
                raise ValueError(f"{name} is in the base package and has no add-on price")
            value = num(price, f"add-on price for {name}")
            if _is_legacy(value):
                raise ValueError(f"${value:,.0f} is an old package price and cannot be reused")
            addons[name] = round(value, 2)
        out["addon_usd"] = addons
    if "autonomy_factor" in incoming:
        factors = dict(current["autonomy_factor"])
        for mode, factor in (incoming.get("autonomy_factor") or {}).items():
            key = str(mode).strip().upper()
            if key not in FACE_AUTONOMY_FACTOR:
                raise ValueError(f"unknown autonomy mode: {key}")
            factors[key] = round(num(factor, f"{key} multiplier", lo=0.5, hi=3.0), 3)
        out["autonomy_factor"] = factors
    if incoming.get("integrations_included") is not None:
        out["integrations_included"] = int(num(incoming["integrations_included"], "integrations_included", hi=50))
    if incoming.get("integration_usd") is not None:
        out["integration_usd"] = round(num(incoming["integration_usd"], "integration_usd"), 2)
    if incoming.get("round_to_usd") is not None:
        out["round_to_usd"] = round(num(incoming["round_to_usd"], "round_to_usd", hi=10000), 2)
    if incoming.get("min_margin_percent") is not None:
        out["min_margin_percent"] = round(num(incoming["min_margin_percent"], "min_margin_percent", hi=95), 1)
    if incoming.get("max_discount_percent") is not None:
        out["max_discount_percent"] = round(num(incoming["max_discount_percent"], "max_discount_percent", hi=90), 1)
    return out


def save_rules(
    *,
    client_id: str,
    settings: ClientSettings,
    rules: dict[str, Any],
    ctx: TenantContext,
) -> dict[str, Any]:
    """Persist Jeanne's rules; keep commercial guardrails locked.

    Cost can be given directly or as a target ``margin_percent``. Changing any
    number after Venu confirmed puts the rules back to pending Venu.
    """
    current = get_rules(settings)

    def pick(field: str) -> Optional[float]:
        value = rules.get(field)
        if value is None:
            value = current.get(field)
        return None if value is None else float(value)

    customer = pick("customer_price_usd")
    if customer is None:
        raise ValueError("customer_price_usd is required — no resale rule is set for this client yet")
    if customer <= 0:
        # M17-08: a zero price would produce $0 quotes that look real.
        raise ValueError("customer_price_usd must be greater than zero")
    if _is_legacy(customer):
        raise ValueError(f"${customer:,.0f} is an old package price and cannot be reused")

    if rules.get("cepoch_cost_usd") is None and rules.get("margin_percent") is not None:
        target = float(rules["margin_percent"])
        if not 0 <= target < 100:
            raise ValueError("margin_percent must be from 0 up to (not including) 100")
        cost = customer * (1 - target / 100.0)
    else:
        cost = pick("cepoch_cost_usd")
        if cost is None:
            raise ValueError("cepoch_cost_usd or margin_percent is required — no resale rule is set yet")
    if cost < 0:
        raise ValueError("cepoch_cost_usd must be zero or more")
    if cost > customer:
        raise ValueError("cepoch_cost_usd is above the customer price — that is a negative margin")
    margin = (customer - cost) / customer * 100.0

    raw = _raw(settings)
    previous_scope = {k: current["scope_rules"][k] for k in DEFAULT_SCOPE_RULES}
    if isinstance(rules.get("scope_rules"), dict):
        scope: Optional[dict[str, Any]] = _clean_scope(rules["scope_rules"], previous_scope)
    else:
        scope = raw.get("scope_rules") or None
    changed = (
        not current.get("configured")
        or round(customer, 2) != current.get("customer_price_usd")
        or round(cost, 2) != current.get("cepoch_cost_usd")
        or (scope is not None and scope != previous_scope)
    )

    requested = str(rules.get("quote_status") or "").strip()
    previous = str(current.get("quote_status") or STATUS_PENDING)
    if requested not in STATUSES:
        requested = previous if previous in STATUSES else STATUS_PENDING
    status = requested
    reconfirm = False
    if status == STATUS_CONFIRMED and changed:
        status = STATUS_PENDING
        reconfirm = previous == STATUS_CONFIRMED or requested == STATUS_CONFIRMED

    now = utcnow().isoformat()
    stored: dict[str, Any] = {
        **_GUARDRAILS,
        "forbidden_legacy_examples": list(DEFAULT_RULES["forbidden_legacy_examples"]),
        "disclaimer": DEFAULT_RULES["disclaimer"],
        "configured": True,
        "customer_price_usd": round(customer, 2),
        "cepoch_cost_usd": round(cost, 2),
        "margin_percent": round(margin, 1),
        "quote_status": status,
        "placeholder": status != STATUS_CONFIRMED,
        "notes": str(rules["notes"] if rules.get("notes") is not None else current.get("notes") or "")[:2000],
        "updated_at": now,
        "updated_by": ctx.user_id,
        "reconfirm_needed": reconfirm,
    }
    if status == STATUS_CONFIRMED:
        if previous == STATUS_CONFIRMED and not changed:
            stored["confirmed_at"] = raw.get("confirmed_at")
            stored["confirmed_by"] = raw.get("confirmed_by")
        else:
            stored["confirmed_at"] = now
            stored["confirmed_by"] = ctx.user_id
    if stored["placeholder"]:
        stored["disclaimer"] = (
            "Not confirmed by Venu yet — treat every figure as a working number, "
            "not a price to send. Not a public price list."
        )
    else:
        stored["disclaimer"] = "Confirmed by Venu. Customer-specific — not a public price list."

    history = list(raw.get("rules_history") or [])
    if changed or status != previous:
        history.append(
            {
                "at": now,
                "by": ctx.user_id,
                "customer_price_usd": stored["customer_price_usd"],
                "cepoch_cost_usd": stored["cepoch_cost_usd"],
                "margin_percent": stored["margin_percent"],
                "quote_status": status,
                "event": (
                    "confirmed" if status == STATUS_CONFIRMED and previous != STATUS_CONFIRMED
                    else "reconfirm_needed" if reconfirm
                    else "updated"
                ),
            }
        )
    stored["rules_history"] = history[-RULES_HISTORY_LIMIT:]
    if scope is not None:
        stored["scope_rules"] = scope
    for key in ("consult_quotes", "last_consult_quote_id"):
        if key in raw:
            stored[key] = raw[key]

    payload = settings.to_dict()
    payload["resale_pricing"] = stored
    updated = control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    return get_rules(updated.settings)


def ensure_default_rules(
    *, client_id: str, settings: ClientSettings, actor: str = "__bootstrap__"
) -> None:
    if _raw(settings):
        return
    payload = settings.to_dict()
    payload["resale_pricing"] = dict(DEFAULT_RULES)
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=actor
    )


def face_scope_config(settings: Optional[ClientSettings] = None) -> dict[str, Any]:
    """Weights the consult calculator uses — this client's rules, or the face defaults."""
    scope = scope_rules(settings) if settings is not None else dict(DEFAULT_SCOPE_RULES)
    return {
        "placeholder": not scope.get("customized", False),
        "disclaimer": (
            "Scope weights are this client's rules."
            if scope.get("customized")
            else "Face scope weights — replace them with yours under Pricing rules."
        ),
        "base_package_domains": list(BASE_PACKAGE_DOMAINS),
        "addon_domains": [d for d in DOMAIN_NAMES if d not in BASE_PACKAGE_DOMAINS],
        "addon_usd": dict(scope["addon_usd"]),
        "autonomy_factor": dict(scope["autonomy_factor"]),
        "integrations_included": scope["integrations_included"],
        "integration_usd": scope["integration_usd"],
        "round_to_usd": scope["round_to_usd"],
        "min_margin_percent": scope["min_margin_percent"],
        "max_discount_percent": scope["max_discount_percent"],
        "no_public_package_price": True,
        "no_per_seat_pricing": True,
    }


def _money(value: float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


_AUTONOMY_LABEL = {
    "ADVISE": "advising only",
    "DRAFT": "drafting for your team",
    "WAIT_FOR_APPROVAL": "acting after approval",
    "AUTO_WITHIN_SCOPE": "acting within agreed scope",
}


def calculate_consult_quote(
    settings: ClientSettings,
    *,
    domains: list[str],
    autonomy: str = "WAIT_FOR_APPROVAL",
    integrations: int = 0,
    discount_percent: float = 0.0,
) -> dict[str, Any]:
    """
    FB-049: compute customer price / cost / margin from this client's rules.

    Base package domains are covered by rules.customer_price_usd. Add-ons,
    extra integrations, the autonomy multiplier, an optional discount and
    rounding adjust the total. Cost scales with the work (base cost ratio) and is
    never discounted, so a discount shows up as lost margin.
    """
    rules = get_rules(settings)
    if not is_configured(rules):
        raise PricingNotConfigured(
            "No resale price rules are set for this client. Set customer price and "
            "Cepoch cost under Admin → Pricing before calculating a quote."
        )
    scope = rules["scope_rules"]
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
    addon_prices: dict[str, float] = scope["addon_usd"]
    addons = [d for d in requested if d not in base_set]
    unpriced = [d for d in addons if d not in addon_prices]
    if unpriced:
        raise ValueError(f"no add-on price set for: {', '.join(unpriced)}")

    if not (autonomy or "").strip():
        # M17-11: a required scope input is asked for, never assumed.
        raise ValueError("autonomy is required — choose the autonomy level for this quote")
    mode = autonomy.strip().upper()
    factors: dict[str, float] = scope["autonomy_factor"]
    if mode not in factors:
        raise ValueError(f"unknown autonomy mode: {mode}")
    factor = float(factors[mode])

    discount = float(discount_percent or 0)
    if discount < 0:
        raise ValueError("discount_percent must be zero or more")
    if discount > scope["max_discount_percent"]:
        raise ValueError(
            f"discount of {discount:g}% is over the {scope['max_discount_percent']:g}% cap in the pricing rules"
        )

    included = int(scope["integrations_included"])
    integ = max(0, int(integrations))
    extra_integ = max(0, integ - included)
    integ_fee = round(extra_integ * float(scope["integration_usd"]), 2)

    addon_lines = [
        {"kind": "addon", "domain": d, "label": f"{d.title()} add-on", "customer_usd": addon_prices[d]}
        for d in addons
    ]
    addon_total = sum(line["customer_usd"] for line in addon_lines)
    subtotal = round(base_price + addon_total + integ_fee, 2)
    after_autonomy = round(subtotal * factor, 2)
    autonomy_adj = round(after_autonomy - subtotal, 2)
    discount_usd = round(after_autonomy * discount / 100.0, 2)
    before_rounding = round(after_autonomy - discount_usd, 2)
    step = float(scope["round_to_usd"] or 0)
    customer = round(math.ceil(before_rounding / step) * step, 2) if step > 0 else before_rounding
    rounding_adj = round(customer - before_rounding, 2)

    cost_ratio = (base_cost / base_price) if base_price > 0 else 0.6
    cost = round(after_autonomy * cost_ratio, 2)
    margin = round(((customer - cost) / customer * 100.0) if customer > 0 else 0.0, 1)
    floor = float(scope["min_margin_percent"])

    lines: list[dict[str, Any]] = [
        {
            "kind": "base",
            "domain": "base_package",
            "label": "Base package",
            "detail": ", ".join(BASE_PACKAGE_DOMAINS),
            "customer_usd": base_price,
            "domains": list(BASE_PACKAGE_DOMAINS),
        },
        *addon_lines,
    ]
    if integ_fee:
        lines.append(
            {
                "kind": "integrations",
                "domain": "extra_integrations",
                "label": f"{extra_integ} extra integration{'s' if extra_integ != 1 else ''}",
                "detail": f"{included} included, then {_money(float(scope['integration_usd']))} each",
                "customer_usd": integ_fee,
                "count": extra_integ,
            }
        )
    if autonomy_adj:
        lines.append(
            {
                "kind": "autonomy",
                "domain": "autonomy",
                "label": f"Autonomy: {_AUTONOMY_LABEL.get(mode, mode)}",
                "detail": f"× {factor:g}",
                "customer_usd": autonomy_adj,
            }
        )
    if discount_usd:
        lines.append(
            {"kind": "discount", "domain": "discount", "label": f"Discount {discount:g}%", "customer_usd": -discount_usd}
        )
    if rounding_adj:
        lines.append(
            {
                "kind": "rounding",
                "domain": "rounding",
                "label": f"Rounded up to the nearest {_money(step)}",
                "customer_usd": rounding_adj,
            }
        )

    warnings: list[str] = []
    below_floor = margin < floor
    if below_floor:
        warnings.append(
            f"Margin {margin:g}% is under your {floor:g}% floor — reduce the discount or rethink scope."
        )
    if _is_legacy(customer):
        warnings.append(f"{_money(customer)} matches an old package price — adjust before quoting.")
    confirmed = rules.get("quote_status") == STATUS_CONFIRMED
    if not confirmed:
        warnings.append("Rules are not confirmed by Venu yet — this is a working number.")

    scope_words = "base package" + (f" plus {', '.join(addons)}" if addons else "")
    talk_track = (
        f"For the {scope_words}, with FreedomBot {_AUTONOMY_LABEL.get(mode, mode)} and "
        f"{integ} integration{'s' if integ != 1 else ''} ({included} included), "
        f"the investment is {_money(customer)}"
        + (f" after a {discount:g}% discount" if discount_usd else "")
        + ". This is a custom engagement price, not per seat."
        + ("" if confirmed else " (Pending Venu's confirmation.)")
    )

    return {
        "placeholder": not confirmed,
        "disclaimer": (
            "Calculated from this client's confirmed pricing rules."
            if confirmed
            else "Calculated from pricing rules Venu has not confirmed yet."
        ),
        "price_set_by": rules.get("price_set_by") or "JeanneCAIO",
        "confirm_by": rules.get("confirm_by") or "Venu",
        "quote_status": rules.get("quote_status") or STATUS_PENDING,
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
            "integrations_included": included,
            "extra_integrations": extra_integ,
            "discount_percent": discount,
        },
        "lines": lines,
        "subtotal_usd": subtotal,
        "discount_usd": discount_usd,
        "customer_price_usd": customer,
        "cepoch_cost_usd": cost,
        "gross_profit_usd": round(customer - cost, 2),
        "margin_percent": margin,
        "min_margin_percent": floor,
        "below_floor": below_floor,
        "max_discount_percent": scope["max_discount_percent"],
        "warnings": warnings,
        "talk_track": talk_track,
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
    if quote.get("below_floor"):
        raise ValueError(
            f"This quote is under the {quote['min_margin_percent']:g}% margin floor and cannot be saved."
        )
    raw = dict(_raw(settings))
    history = list(raw.get("consult_quotes") or [])
    entry = {
        **quote,
        "id": f"cq_{len(history) + 1}_{int(utcnow().timestamp())}",
        "label": (label or "Consult quote")[:200],
        "saved_at": utcnow().isoformat(),
        "saved_by": ctx.user_id,
    }
    history.append(entry)
    raw["consult_quotes"] = history[-SAVED_QUOTES_LIMIT:]
    raw["last_consult_quote_id"] = entry["id"]

    payload = settings.to_dict()
    payload["resale_pricing"] = raw
    control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    return entry


def list_consult_quotes(settings: ClientSettings) -> list[dict[str, Any]]:
    """Saved quotes for this client, newest first."""
    return list(reversed(_raw(settings).get("consult_quotes") or []))
