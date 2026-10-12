"""
Regulated-content classifier (FB-039)
=====================================

Heuristic category detection over tool arguments. Not a clinical/PCI scanner —
it is a deterministic rule set that flags licensed / regulated subject matter so
the policy engine can force human approval or deny PHI/card when not permitted.

Categories: legal, medical, credit, employment, insurance, safety, financial_advice,
minor (personal data about a child) and biometrics (biometric identifiers).
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
            r"\bmedical history\b",
            r"\ballerg(y|ies)\b",
            r"\broot canal\b",
            r"\bperiodontal\b",
            r"\bdental (chart|record|x-?ray)s?\b",
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

#: M07-13: personal data about a child. Strong signals are enough on their own;
#: a family word ("son", "kid") counts only next to a personal-data marker, so
#: "our childcare benefit" is not flagged.
_MINOR_STRONG = tuple(
    re.compile(p, re.I)
    for p in (
        r"\b(?:[1-9]|1[0-7])[-\s]?(?:year|yr)s?[-\s]?old\b",
        r"\bage[ds]?\s*:?\s*(?:[1-9]|1[0-7])\b",
        r"\b(?:elementary|primary|middle|junior high) school\b",
        r"\b(?:kindergarten(?:er)?|preschool(?:er)?|daycare|nursery school)\b",
        r"\b(?:[A-Z][a-z]+ )?Elementary\b",
        r"\bminors?\b",
        r"\b(?:parent|guardian)(?:'s)? (?:phone|contact|email|consent|name)\b",
        r"\blegal guardian\b",
        r"\bCOPPA\b",
    )
)
_MINOR_WEAK = re.compile(r"\b(?:child|children|kid|kids|son|daughter|toddler|infant|baby|student|pupil)\b", re.I)
_PERSONAL_MARKER = re.compile(
    r"\b(?:name|phone|email|address|school|birth|dob|age|grade|photo|contact)\b|@|\d{3}[-.\s]\d{4}",
    re.I,
)


def _minor_match(blob: str) -> str | None:
    for pattern in _MINOR_STRONG:
        m = pattern.search(blob)
        if m:
            return m.group(0)
    m = _MINOR_WEAK.search(blob)
    if m and _PERSONAL_MARKER.search(blob):
        return m.group(0)
    return None


#: FB-039: biometric identifiers (BIPA-style: fingerprint, face/hand geometry,
#: iris/retina scan, voiceprint). Strong signals name the identifier itself; the
#: bare word "biometric" or a device feature (Face ID) counts only next to a
#: personal-data marker, so "compare biometric time clocks" is not flagged.
_BIOMETRIC_STRONG = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bfinger\s*prints?\b",
        r"\bthumb\s*prints?\b",
        r"\bface\s*(?:scan|print|template|embedding|geometry|vector)s?\b",
        r"\bfacial\s*(?:recognition|geometry|template|scan|embedding)s?\b",
        r"\biris\s*(?:scan|print|template|image)s?\b",
        r"\bretina(?:l)?\s*(?:scan|print|image)s?\b",
        r"\bvoice\s*prints?\b",
        r"\bpalm\s*(?:print|vein)s?\b",
        r"\bhand\s*geometry\b",
        r"\bgait\s*(?:analysis|signature)s?\b",
        r"\bbiometric\s*(?:template|identifier|data|record|scan|sample|enrol+ment)s?\b",
    )
)
_BIOMETRIC_WEAK = re.compile(r"\b(?:biometric(?:s|al)?|face\s*id|touch\s*id)\b", re.I)
#: "fingerprint" in the technical sense (a hash), not a person's finger.
_TECH_FINGERPRINT = re.compile(
    r"\b(?:browser|digital|device|file|document|key|ssh|tls|ssl|certificate|gpg|pgp|"
    r"audio|content|request|schema|hash)\s+finger\s*prints?\b",
    re.I,
)


def _biometric_match(blob: str) -> str | None:
    cleaned = _TECH_FINGERPRINT.sub(" ", blob)
    for pattern in _BIOMETRIC_STRONG:
        m = pattern.search(cleaned)
        if m:
            return m.group(0)
    m = _BIOMETRIC_WEAK.search(cleaned)
    if m and _PERSONAL_MARKER.search(cleaned):
        return m.group(0)
    return None


#: Clinical terms specific enough to mean health information on their own.
#: The bare word "patient" is not one ("our patient retention rate").
_PHI_STRONG = re.compile(
    r"\b(?:diagnos(?:e|is|ed)|prescription|ICD[-\s]?10|medical (?:record|history)|"
    r"treatment plan|blood (?:pressure|type|glucose)|allerg(?:y|ies)|root canal|"
    r"periodontal|dental (?:chart|record|x-?ray)s?|MRN)\b",
    re.I,
)


def identifiable_phi(text: str) -> bool:
    """FB-039: health information tied to a person, not just a clinical word.

    Used before the model call (dental BAA vendor pattern): a strong clinical
    term, or "patient" next to a personal-data marker.
    """
    if not text:
        return False
    if _PHI_STRONG.search(text):
        return True
    return bool(re.search(r"\bpatients?\b", text, re.I) and _PERSONAL_MARKER.search(text))


_PHI_CATEGORIES = frozenset({"medical"})
_CARD_CATEGORIES = frozenset({"credit"})
_BIOMETRIC_CATEGORIES = frozenset({"biometrics"})
#: Categories that always require a licensed human before any write/executes.
_LICENSED_CATEGORIES = frozenset(
    {
        "legal",
        "medical",
        "credit",
        "employment",
        "insurance",
        "safety",
        "financial_advice",
        "biometrics",
    }
)


@dataclass(frozen=True)
class Classification:
    categories: frozenset[str]
    contains_phi: bool
    contains_card_data: bool
    requires_licensed_human: bool
    matches: tuple[tuple[str, str], ...]  # (category, snippet)
    #: M07-13: personal data about a child (under 18).
    contains_child_data: bool = False
    #: FB-039: biometric identifiers.
    contains_biometrics: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "categories": sorted(self.categories),
            "contains_phi": self.contains_phi,
            "contains_card_data": self.contains_card_data,
            "requires_licensed_human": self.requires_licensed_human,
            "contains_child_data": self.contains_child_data,
            "contains_biometrics": self.contains_biometrics,
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
    minor = _minor_match(blob)
    if minor:
        found.add("minor")
        matches.append(("minor", minor[:80]))
    biometric = _biometric_match(blob)
    if biometric:
        found.add("biometrics")
        matches.append(("biometrics", biometric[:80]))
    cats = frozenset(found)
    return Classification(
        categories=cats,
        contains_phi=bool(cats & _PHI_CATEGORIES),
        contains_card_data=bool(cats & _CARD_CATEGORIES),
        requires_licensed_human=bool(cats & _LICENSED_CATEGORIES),
        matches=tuple(matches),
        contains_child_data="minor" in cats,
        contains_biometrics=bool(cats & _BIOMETRIC_CATEGORIES),
    )


def classify_arguments(arguments: dict[str, Any] | None) -> Classification:
    """Classify a tool payload (all string leaves)."""
    texts = list(_walk_strings(arguments or {}))
    return classify_text(*texts)
