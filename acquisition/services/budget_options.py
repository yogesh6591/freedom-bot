"""Normalize Value-page readiness options (max 4).

Outcomes stay ready / unclear / not_ready so calendar gating is unchanged.
"""

from __future__ import annotations

from typing import Any

BUDGET_OUTCOMES = frozenset({"ready", "unclear", "not_ready"})
MAX_BUDGET_OPTIONS = 4

DEFAULT_BUDGET_OPTIONS: list[dict[str, str]] = [
    {
        "outcome": "ready",
        "label": "Yes, I’m ready to discuss next steps",
    },
    {
        "outcome": "unclear",
        "label": "I’m interested, but I need more context",
    },
    {
        "outcome": "not_ready",
        "label": "Not right now",
    },
]


def _clean_outcome(raw: Any) -> str | None:
    value = str(raw or "").strip().lower()
    return value if value in BUDGET_OUTCOMES else None


def normalize_budget_options(raw: Any) -> list[dict[str, str]]:
    """Return 1–4 cleaned {outcome, label} dicts, or defaults if empty."""
    items: list[dict[str, str]] = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            outcome = _clean_outcome(entry.get("outcome"))
            label = str(entry.get("label") or "").strip()
            if not outcome or not label:
                continue
            items.append({"outcome": outcome, "label": label[:255]})
            if len(items) >= MAX_BUDGET_OPTIONS:
                break
    return items or [dict(item) for item in DEFAULT_BUDGET_OPTIONS]


def budget_options_from_legacy(
    *,
    ready: str | None,
    unclear: str | None,
    not_ready: str | None,
) -> list[dict[str, str]]:
    """Build options from the three legacy label columns."""
    built: list[dict[str, str]] = []
    for outcome, label in (
        ("ready", ready),
        ("unclear", unclear),
        ("not_ready", not_ready),
    ):
        text = (label or "").strip()
        if text:
            built.append({"outcome": outcome, "label": text[:255]})
    return normalize_budget_options(built)


def budget_options_for_settings(settings: Any) -> list[dict[str, str]]:
    """Prefer JSON options; fall back to legacy columns."""
    raw = getattr(settings, "budget_options", None)
    if isinstance(raw, list) and raw:
        return normalize_budget_options(raw)
    return budget_options_from_legacy(
        ready=getattr(settings, "budget_label_ready", None),
        unclear=getattr(settings, "budget_label_unclear", None),
        not_ready=getattr(settings, "budget_label_not_ready", None),
    )


def parse_budget_options_form(
    labels: list[str] | str | None,
    outcomes: list[str] | str | None,
) -> list[dict[str, str]]:
    """Parse parallel form lists from Admin Value save."""

    def _as_list(value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    label_list = _as_list(labels)
    outcome_list = _as_list(outcomes)
    combined: list[dict[str, str]] = []
    for idx, label in enumerate(label_list):
        outcome = outcome_list[idx] if idx < len(outcome_list) else ""
        combined.append({"outcome": outcome, "label": label})
    return normalize_budget_options(combined)


def sync_legacy_budget_labels(options: list[dict[str, str]]) -> dict[str, str | None]:
    """First label per outcome → legacy columns (for older readers)."""
    out: dict[str, str | None] = {
        "budget_label_ready": None,
        "budget_label_unclear": None,
        "budget_label_not_ready": None,
    }
    key_map = {
        "ready": "budget_label_ready",
        "unclear": "budget_label_unclear",
        "not_ready": "budget_label_not_ready",
    }
    for item in options:
        column = key_map.get(item["outcome"])
        if column and out[column] is None:
            out[column] = item["label"]
    return out
