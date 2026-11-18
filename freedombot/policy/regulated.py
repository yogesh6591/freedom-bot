"""
Regulated Data the Order/DPA Does Not Allow
===========================================

One check for every place data can enter, be kept, or leave: what a person types, a file they attach
or upload to memory, the earlier turns sent back to the model, memory used as background, a tool's
result, and the reply.

Categories (FB-039): ``phi`` (health information about a person), ``card`` (payment card details),
``ssn``, ``child`` (a child's personal data) and ``biometrics``. A category is blocked when the
workspace's Order/DPA does not allow it (no BAA is needed for PHI). Blocked data is never
kept and never handed back: it is withheld whole (PHI, child data, biometrics) or masked (card numbers,
CVVs, expiry dates, SSNs).

Licensed advice is a different kind of rule (what the assistant may say, not data a person holds); it
is refused before the model in ``app/api/chat.py``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from freedombot.policy.classifier import (
    _SSN_DASHED,
    _SSN_LABELLED,
    classify_text,
    find_ssn,
    health_about_person,
    phi_about_someone,
)

CATEGORIES = ("phi", "card", "ssn", "child", "biometrics")

#: Audit status for each category when a message, file or result is refused or withheld.
STATUS = {
    "phi": "PHI_BLOCKED", "card": "CARD_DATA_BLOCKED", "ssn": "SSN_BLOCKED", "child": "CHILD_DATA_BLOCKED",
    "biometrics": "BIOMETRICS_BLOCKED",
}
WHAT = {
    "phi": "health information", "card": "payment card details", "ssn": "a Social Security number",
    "child": "a child's personal data", "biometrics": "biometric data",
}
_STATUS_CATEGORY = {v: k for k, v in STATUS.items()} | {"PHI_NO_BAA_BLOCKED": "phi"}
#: Statuses for regulated data in a message; the message itself is then never kept.
REGULATED_STATUSES = frozenset(_STATUS_CATEGORY)

#: Payment-card digit run, and card details given as values (a CVV/CVC or an expiry date).
CARD_DIGITS = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CARD_PAYLOAD = re.compile(
    r"(?i)\b(?:cvv2?|cvc2?|card security code)\b\s*(?:is|:|#|=)?\s*\d{3,4}\b"
    r"|\b(?:exp(?:iry|ires|iration)?(?: date)?|valid thru)\b\s*(?:is|on|:|=)?\s*\d{1,2}\s*/\s*\d{2,4}\b"
)

_REPLY_WITHHELD = {
    "phi": ("I can't share health information about a person here. This workspace's Order/DPA doesn't allow "
            "PHI, so I won't repeat or pass on those details. An admin can update the Order/DPA setting if your "
            "contract covers PHI."),
    "child": ("I can't share personal details about a child here. This workspace isn't authorized to handle "
              "children's personal data, so I won't repeat or pass them on."),
    "biometrics": ("I can't share biometric data (fingerprints, face or voice prints, iris scans) here. This "
                   "workspace's Order/DPA doesn't allow it, so I won't repeat or pass it on."),
}
PHI_REPLY_WITHHELD = _REPLY_WITHHELD["phi"]
_CARD_MASK = "[card details withheld]"
_SSN_MASK = "[SSN withheld]"


def blocked(settings: Any) -> frozenset[str]:
    """The categories this workspace's Order/DPA does not allow."""
    out = {c for c in ("card", "ssn", "child", "biometrics") if not settings.order_dpa_allows_category(c)}
    if not settings.phi_permitted():
        out.add("phi")
    return frozenset(out)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


def _card_numbers(text: str) -> list[str]:
    """Digit runs that are real card numbers (the Luhn check), not order numbers or phone numbers."""
    return [m.group(0) for m in CARD_DIGITS.finditer(text or "")
            if 13 <= len(re.sub(r"\D", "", m.group(0))) <= 19 and _luhn(re.sub(r"\D", "", m.group(0)))]


def found(text: str, *, strict: bool = True) -> set[str]:
    """The regulated categories ``text`` holds as data.

    ``strict`` (files, memory, records, history): health information about a person, in plain words or as a
    clinical term next to a person or personal detail. Otherwise (a reply the model wrote): only plain-word
    health details tied to a person, so "I can't give a diagnosis" is not PHI. A policy that merely mentions
    "diagnosis" is never PHI.
    """
    if not text:
        return set()
    out: set[str] = set()
    if (phi_about_someone(text) if strict else health_about_person(text)):
        out.add("phi")
    if _card_numbers(text) or CARD_PAYLOAD.search(text):
        out.add("card")
    if find_ssn(text):
        out.add("ssn")
    classified = classify_text(text)
    if classified.contains_child_data:
        out.add("child")
    if classified.contains_biometrics:
        out.add("biometrics")
    return out


def find_blocked(text: str, settings: Any, *, strict: bool = True) -> Optional[str]:
    """The first blocked category ``text`` holds, or ``None``."""
    hits = found(text, strict=strict) & blocked(settings)
    return next((c for c in CATEGORIES if c in hits), None)


def withheld_message(status: str) -> str:
    """What is kept in place of a message the Order/DPA does not allow."""
    what = WHAT.get(_STATUS_CATEGORY.get(status, ""), "regulated data")
    return f"[Message not saved: it contained {what}, which this workspace's Order/DPA does not allow.]"


def _mask(text: str, cats: frozenset[str]) -> str:
    if "card" in cats:
        for number in _card_numbers(text):
            text = text.replace(number, _CARD_MASK)
        text = CARD_PAYLOAD.sub(_CARD_MASK, text)
    if "ssn" in cats:
        text = _SSN_DASHED.sub(_SSN_MASK, _SSN_LABELLED.sub(_SSN_MASK, text))
    return text


def screen_reply(content: str, settings: Any) -> tuple[str, Optional[str]]:
    """Keep blocked data out of a reply the model wrote. Returns ``(content, audit status or None)``.

    PHI, child data and biometrics replace the whole reply; card details and SSNs are masked in place.
    """
    if not content:
        return content, None
    cats = blocked(settings)
    hits = found(content, strict=False) & cats
    for category in ("phi", "child", "biometrics"):
        if category in hits:
            return _REPLY_WITHHELD[category], f"{category.upper()}_REPLY_WITHHELD"
    masked = _mask(content, cats)
    if masked != content:
        return masked, "CARD_REPLY_MASKED" if "card" in hits else "SSN_REPLY_MASKED"
    return content, None


def screen_for_model(text: str, settings: Any) -> tuple[str, Optional[str]]:
    """Keep blocked data out of what the model reads: a tool's result, an earlier turn, a memory item.

    Returns ``(text, category or None)``. Whole items with PHI, child data or biometrics are withheld;
    card details and SSNs are masked.
    """
    if not text:
        return text, None
    cats = blocked(settings)
    hits = found(text) & cats
    for category in ("phi", "child", "biometrics"):
        if category in hits:
            return (f"[Withheld: this holds {WHAT[category]}, which this workspace's Order/DPA does not allow. "
                    "Do not ask for it or guess it; tell the user it can't be shown here.]"), category
    masked = _mask(text, cats)
    return masked, ("card" if "card" in hits else "ssn") if masked != text else None
