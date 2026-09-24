"""
Regulated-content classifier (FB-039)
=====================================

Heuristic category detection over tool arguments. Not a clinical/PCI scanner —
it is a deterministic rule set that flags licensed / regulated subject matter so
the policy engine can force human approval or deny PHI/card when not permitted.

Categories: legal, medical, credit, employment, insurance, safety, financial_advice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: category -> compiled patterns that indicate that category of content.
_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "legal": tuple(
        re.compile(p, re.I)
        for p in (
            r"\blegal advice\b",
            r"\battorney[-\s]?client\b",
            r"\bbreach of contract\b",
            r"\bliable\b|\bliability\b",
            r"\bindemnif",
            r"\bstatute of limitations\b",
            r"\byou (are|were) in violation\b",
            r"\bthis (is|constitutes) (a )?breach\b",
        )
    ),
    "medical": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bdiagnos(e|is|ed)\b",
            r"\bpatient\b",
            r"\bHIPAA\b",
            r"\bPHI\b",
            r"\bprescription\b",
            r"\bICD[-\s]?10\b",
            r"\bmedical record\b",
            r"\btreatment plan\b",
            r"\bblood (pressure|type|glucose)\b",
        )
    ),
    "credit": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bcredit (score|report|decision)\b",
            r"\bFICO\b",
            r"\bdeny(ing)? (the )?loan\b",
            r"\bunderwrit",
            r"\bSSN\b|\bsocial security\b",
            r"\bcard number\b|\bcvv\b|\bpan\b",
            r"\b4[0-9]{12}(?:[0-9]{3})?\b",  # crude card-shaped digit run
        )
    ),
    "employment": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bterminat(e|ion|ed)\b.*(employee|staff|worker)",
            r"\bfire (him|her|them|the employee)\b",
            r"\bdisciplinary action\b",
            r"\bwage (garnish|claim)\b",
            r"\bEEOC\b|\bunfair dismissal\b",
        )
    ),
    "insurance": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bclaim (denial|denied|approval)\b",
            r"\bcoverage determination\b",
            r"\binsurability\b",
            r"\bpolicy (void|cancelled)\b",
        )
    ),
    "safety": tuple(
        re.compile(p, re.I)
        for p in (
            r"\bsafety critical\b",
            r"\blife[-\s]?threatening\b",
            r"\bemergency shutdown\b",
            r"\bhazard(ous)? (material|waste)\b",
        )
    ),
    "financial_advice": tuple(
        re.compile(p, re.I)
        for p in (
            r"\binvest(ment)? advice\b",
            r"\byou should (buy|sell|hold)\b",
            r"\bguaranteed returns?\b",
            r"\bportfolio allocation\b",
            r"\bSEC[-\s]?regulated\b",
        )
    ),
}

_PHI_CATEGORIES = frozenset({"medical"})
_CARD_CATEGORIES = frozenset({"credit"})
#: Categories that always require a licensed human before any write/executes.
_LICENSED_CATEGORIES = frozenset(
    {"legal", "medical", "credit", "employment", "insurance", "safety", "financial_advice"}
)


@dataclass(frozen=True)
class Classification:
    categories: frozenset[str]
    contains_phi: bool
    contains_card_data: bool
    requires_licensed_human: bool
    matches: tuple[tuple[str, str], ...]  # (category, snippet)

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": sorted(self.categories),
            "contains_phi": self.contains_phi,
            "contains_card_data": self.contains_card_data,
            "requires_licensed_human": self.requires_licensed_human,
            "matches": [{"category": c, "snippet": s} for c, s in self.matches],
        }


def _walk_strings(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk_strings(v)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _walk_strings(v)


def classify_text(*texts: str) -> Classification:
    """Classify free text for regulated / licensed subject matter."""
    found: set[str] = set()
    matches: list[tuple[str, str]] = []
    blob = "\n".join(t for t in texts if t)
    if not blob.strip():
        return Classification(frozenset(), False, False, False, ())
    for category, patterns in _PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(blob)
            if m:
                found.add(category)
                snippet = m.group(0)[:80]
                matches.append((category, snippet))
                break
    cats = frozenset(found)
    return Classification(
        categories=cats,
        contains_phi=bool(cats & _PHI_CATEGORIES),
        contains_card_data=bool(cats & _CARD_CATEGORIES),
        requires_licensed_human=bool(cats & _LICENSED_CATEGORIES),
        matches=tuple(matches),
    )


def classify_arguments(arguments: dict[str, Any] | None) -> Classification:
    """Classify a tool payload (all string leaves)."""
    texts = list(_walk_strings(arguments or {}))
    return classify_text(*texts)
