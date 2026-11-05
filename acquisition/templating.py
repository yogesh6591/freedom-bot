"""Shared Jinja2 templates with platform-wide display helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.templating import Jinja2Templates

from acquisition.time_format import format_datetime_12h

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_ENUM_LABELS = {
    "decision_maker": "Decision maker",
    "not_decision_maker": "Not decision maker",
    "influencer": "Influencer",
    "unknown": "Unknown",
    "likely": "Likely",
    "unlikely": "Unlikely",
    "ready": "Ready",
    "not_ready": "Not ready",
    "unclear": "Unclear",
    "strong": "Strong",
    "moderate": "Moderate",
    "weak": "Weak",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "yes": "Yes",
    "no": "No",
    "unresolved": "Unresolved",
}


def format_admin_value(value: Any) -> str:
    """Humanize Admin display values — no raw Python lists / snake_case enums."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [format_admin_value(item) for item in value]
        parts = [part for part in parts if part and part != "—"]
        return " · ".join(parts) if parts else "—"
    if isinstance(value, dict):
        # Structured nested objects shouldn't dump as dict repr.
        inner = value.get("value", value)
        if inner is value:
            return "—"
        return format_admin_value(inner)

    text = str(value).strip()
    if not text:
        return "—"
    key = text.lower().replace(" ", "_")
    if key in _ENUM_LABELS:
        return _ENUM_LABELS[key]
    if "_" in text and " " not in text and text == text.lower():
        return text.replace("_", " ").title()
    if text[:1].islower() and " " not in text and len(text) < 24:
        return text[:1].upper() + text[1:]
    return text


def format_money(amount: Any, currency: str | None = "USD") -> str:
    """Admin/checkout money: USD as $25,000 (no trailing .00); other codes as CODE 1,234.50."""
    from decimal import Decimal, InvalidOperation

    if amount is None or amount == "":
        return "—"
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return str(amount)
    if value == value.to_integral_value():
        number = f"{value:,.0f}"
    else:
        number = f"{value:,.2f}"
    code = (currency or "USD").strip().upper() or "USD"
    if code == "USD":
        return f"${number}"
    return f"{code} {number}"


_SOURCE_LABELS = {
    "openai_agentic": "OpenAI agentic",
    "openai_web": "OpenAI web",
    "nppes": "NPPES registry",
    "exa": "Exa index",
    "exa_prospeo": "Exa + Prospeo",
    "legacy_search": "Legacy search",
    "autonomous_discovery": "Engine",
    "mock": "Mock",
}


def _humanize(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    text = text.replace("_", " ")
    return text[:1].upper() + text[1:]


def _score_class(value: object) -> str:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return "hi" if v >= 70 else "mid" if v >= 55 else "lo"


def _short_band(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return text.split(" (", 1)[0].strip() or text


def _is_placeholder_email(value: object) -> bool:
    from acquisition.services.prospect_identity import is_placeholder_email

    return is_placeholder_email(str(value or ""))


def _source_label(value: object) -> str:
    key = str(value or "").strip()
    return _SOURCE_LABELS.get(key, _humanize(key)) if key else "—"


def create_templates(directory: Path | str | None = None) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(directory or TEMPLATES_DIR))
    templates.env.globals["format_datetime"] = format_datetime_12h
    templates.env.globals["format_admin_value"] = format_admin_value
    templates.env.globals["format_money"] = format_money
    templates.env.filters["humanize"] = _humanize
    templates.env.filters["score_class"] = _score_class
    templates.env.filters["short_band"] = _short_band
    templates.env.filters["source_label"] = _source_label
    templates.env.tests["placeholder_email"] = _is_placeholder_email
    return templates
