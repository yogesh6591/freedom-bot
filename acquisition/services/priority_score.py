"""Backend priority score math for acquisition (authoritative)."""

from __future__ import annotations


class ScoringWeightsError(ValueError):
    """Raised when Admin scoring weights do not total 100%."""


def scoring_weights_as_percent(score_weights: dict | None) -> tuple[float, float, float]:
    weights = score_weights or {}
    fit = float(weights.get("fit", 0.45)) * 100.0
    timing = float(weights.get("timing", 0.25)) * 100.0
    reachability = float(weights.get("reachability", 0.30)) * 100.0
    return fit, timing, reachability


def validate_scoring_weights(score_weights: dict | None) -> tuple[float, float, float]:
    fit, timing, reachability = scoring_weights_as_percent(score_weights)
    total = fit + timing + reachability
    if round(total, 6) != 100:
        raise ScoringWeightsError(
            f"Scoring weights must total 100% (got fit={fit:.2f}, timing={timing:.2f}, "
            f"reachability={reachability:.2f}, total={total:.2f}). "
            "Fix Admin → Targeting → Scoring before running discovery."
        )
    return fit, timing, reachability


def calculate_priority_score(
    fit_score: float,
    timing_score: float,
    reachability_score: float,
    fit_weight: float,
    timing_weight: float,
    reachability_weight: float,
) -> float:
    total = fit_weight + timing_weight + reachability_weight
    if round(total, 6) != 100:
        raise ScoringWeightsError("Scoring weights must total 100%")
    priority = (
        float(fit_score) * float(fit_weight) / 100.0
        + float(timing_score) * float(timing_weight) / 100.0
        + float(reachability_score) * float(reachability_weight) / 100.0
    )
    return round(priority, 2)
