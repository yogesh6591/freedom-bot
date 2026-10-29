"""Email wording helpers for outreach: blanks, per-person values, tokenising, rendering.

Moved out of the old campaign assistant so the outreach flow does not depend on it.
Behaviour is unchanged: same blanks ({{firstName}} ...), same rules for where a value
comes from (typed > database > chat > missing).
"""

from __future__ import annotations

import re
from typing import Any

from acquisition.services.assessment_tokens import split_executive_name

TOKEN_FIRST, TOKEN_LAST, TOKEN_COMPANY = "{{firstName}}", "{{lastName}}", "{{companyName}}"
TOKEN_ASSESS = "{{assessment_url}}"
FIRST_NAME_FALLBACK = "there"
_GREETING = r"(?:Hi|Hello|Dear|Hey)"
_LITERAL_GREETING = re.compile(rf"\b{_GREETING}\s+(?!there\b)([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)?)\s*[,!]")
_PROTECTED_LINK = re.compile(r"(https?://\S+|\{+\s*assessment[\s_-]*url\s*\}+)", re.I)


def clean_field(value: Any) -> str:
    """A value typed for a blank: one short line, no markup, nothing that could add content to the email."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"[{}<>\\]", "", text)[:80].strip()


def proper_case(value: str) -> str:
    """A name typed in chat as all-lowercase or all-capitals ("nina", "BROOKS") reads as a name: "Nina", "Brooks"."""
    text = (value or "").strip()
    if text and (text == text.lower() or text == text.upper()):
        return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), text)
    return text


def _local_part_is_handle(local: str) -> bool:
    """A mailbox is a handle when it has digits or separators, not a single given name."""
    token = (local or "").strip()
    if not token:
        return True
    return any(ch.isdigit() or ch in "._+%" for ch in token)


def _mailbox_name(email: str, value: str) -> bool:
    """True when ``value`` is the address itself, not a person's name.

    A given name that matches the local part of a simple address is a real name and is kept.
    """
    raw = (value or "").strip()
    addr = (email or "").strip()
    if not raw or not addr:
        return False
    if raw.casefold() == addr.casefold():
        return True
    local = addr.split("@", 1)[0].strip()
    if raw.casefold() != local.casefold():
        return False
    return _local_part_is_handle(local)


def _evidence_is_the_address(evidence: str, email: str) -> bool:
    """True when the only evidence is the recipient address (or its mailbox), not a spoken name."""
    quote = " ".join((evidence or "").casefold().split())
    addr = " ".join((email or "").casefold().split())
    if not quote or not addr:
        return False
    local = addr.split("@", 1)[0]
    return quote in {addr, local}




def _find_person(records: dict, email: str) -> dict | None:
    key = (email or "").strip().casefold()
    return next((p for p in (records.get("people") or []) if str(p.get("email") or "").casefold() == key), None)


def person_fields(records: dict, state: dict, recipient: dict) -> dict:
    """What fills the blanks for one recipient, and where each value came from.

    Order: typed by the user (card or chat) > the database > the name given in chat > missing.
    """
    email = str(recipient.get("email") or "")
    typed = (state.get("overrides") or {}).get(email.casefold(), {})
    known = _find_person(records, email) or {}
    known_name = str(known.get("name") or "").strip()
    if _mailbox_name(email, known_name):
        known_name = ""
    k_first, k_last = split_executive_name(known_name) if known_name else ("", "")
    chat_name = str(recipient.get("name") or "").strip()
    c_first, c_last = split_executive_name(chat_name) if chat_name else ("", "")
    c_first, c_last = proper_case(c_first), proper_case(c_last)
    out: dict = {"email": email}
    sources: dict = {}
    for field, from_db, from_chat in (
        ("first_name", k_first, c_first),
        ("last_name", k_last, c_last),
        ("company", str(known.get("company") or "").strip(), ""),
    ):
        if field in typed:
            out[field], sources[field] = typed[field], "typed"
        elif from_db:
            out[field], sources[field] = from_db, "database"
        elif from_chat:
            out[field], sources[field] = from_chat, "chat"
        else:
            out[field], sources[field] = "", "missing"
    out["sources"] = sources
    return out


def effective_fields(f: dict) -> dict:
    """The values that really go out. A missing first name becomes "there" (never a guess)."""
    return {
        "email": f["email"],
        "first_name": f["first_name"] or FIRST_NAME_FALLBACK,
        "last_name": f["last_name"],
        "company": f["company"],
    }


_BLANK_FORMS = (
    (re.compile(r"\{{1,2}\s*first[\s_-]*name\s*\}{1,2}|\[\s*first[\s_-]*name\s*\]|<\s*first[\s_-]*name\s*>", re.I), TOKEN_FIRST),
    (re.compile(r"\{{1,2}\s*last[\s_-]*name\s*\}{1,2}|\[\s*last[\s_-]*name\s*\]|<\s*last[\s_-]*name\s*>", re.I), TOKEN_LAST),
    (re.compile(r"\{{1,2}\s*company(?:[\s_-]*name)?\s*\}{1,2}|\[\s*company(?:[\s_-]*name)?\s*\]|<\s*company(?:[\s_-]*name)?\s*>", re.I), TOKEN_COMPANY),
)


def normalize_blanks(text: str) -> str:
    """Any way of writing a blank ({first name}, {{first_name}}, [First Name], <firstName>) becomes the one blank we use."""
    out = text or ""
    for pattern, token in _BLANK_FORMS:
        out = pattern.sub(token, out)
    return out


def _assessment_settings() -> tuple[str, str]:
    from acquisition.config import get_settings

    settings = get_settings()
    holder = (settings.assessment_url_placeholder or TOKEN_ASSESS).strip() or TOKEN_ASSESS
    base = (settings.public_base_url or "").strip().rstrip("/")
    return holder, base


def has_assessment_link(text: str) -> bool:
    holder, base = _assessment_settings()
    blob = text or ""
    if holder and holder in blob:
        return True
    if re.search(r"\{+\s*assessment[\s_-]*url\s*\}+", blob, re.I):
        return True
    return bool(base and base in blob)


def keep_assessment_link(text: str) -> str:
    """Keep the assessment link as the {{assessment_url}} blank. Never drop it while framing names."""
    holder, base = _assessment_settings()
    out = text or ""
    if base:
        out = re.sub(rf"{re.escape(base)}(?:/a/\S+)?", holder, out)
    out = re.sub(r"\{+\s*assessment[\s_-]*url\s*\}+", holder, out, flags=re.I)
    if holder not in out:
        out = f"{out.rstrip()}\n\n{holder}"
    return out


def strip_assessment_link(text: str) -> str:
    """Remove the assessment CTA from a field that must not carry it (the subject)."""
    holder, base = _assessment_settings()
    out = text or ""
    if base:
        out = re.sub(rf"{re.escape(base)}(?:/a/\S+)?", "", out)
    out = re.sub(r"\{+\s*assessment[\s_-]*url\s*\}+", "", out, flags=re.I)
    if holder:
        out = out.replace(holder, "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _map_unprotected(text: str, transform) -> str:
    bits = _PROTECTED_LINK.split(text or "")
    return "".join(bit if i % 2 else transform(bit) for i, bit in enumerate(bits))


def tokenize(text: str, fields: list[dict]) -> str:
    """Turn the real names/companies a draft contains into blanks, so each person gets their own."""
    original = text or ""
    keep_link = has_assessment_link(original)

    def _names(chunk: str) -> str:
        out = normalize_blanks(chunk)
        # One greeting that lists several recipients ("Hi Nina and Raj,") becomes ONE blank, so each person is
        # greeted by their own name and never by the other's.
        people = []
        for f in fields:
            if f["first_name"]:
                last = rf"(?:\s+(?i:{re.escape(f['last_name'])}))?" if f["last_name"] else ""
                people.append(rf"(?i:{re.escape(f['first_name'])}){last}")
        if len(people) >= 2:
            one = "(?:" + "|".join(sorted(people, key=len, reverse=True)) + ")"
            out = re.sub(
                rf"\b(?P<g>{_GREETING})\s+{one}(?:\s*(?:,|&|\band\b)\s*(?:and\s+)?{one})+",
                lambda m: f"{m.group('g')} {TOKEN_FIRST}",
                out,
            )
        for f in fields:
            first, last, company = f["first_name"], f["last_name"], f["company"]
            if first:
                last_part = rf"(?P<last>\s+(?i:{re.escape(last)}))?" if last else ""
                out = re.sub(
                    rf"\b(?P<g>{_GREETING})\s+(?i:{re.escape(first)}){last_part}\b",
                    lambda m, last=last: f"{m.group('g')} {TOKEN_FIRST}" + (f" {TOKEN_LAST}" if last and m.group("last") else ""),
                    out,
                )
            if company:
                out = re.sub(rf"(?<!\w){re.escape(company)}(?!\w)", TOKEN_COMPANY, out)
        return re.sub(rf"\b({_GREETING})\s+there\b", lambda m: f"{m.group(1)} {TOKEN_FIRST}", out, count=1)

    framed = _map_unprotected(original, _names)
    if keep_link:
        return keep_assessment_link(framed)
    return framed


def render(text: str, f: dict, link: str | None = None) -> str:
    """One person's view of the template. ``link`` is that person's own assessment link when they have one."""
    e = effective_fields(f)
    holder, base = _assessment_settings()
    out = (text or "").replace(TOKEN_FIRST, e["first_name"]).replace(TOKEN_LAST, e["last_name"]).replace(TOKEN_COMPANY, e["company"])
    if holder:
        out = out.replace(holder, link or base or holder)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"[ \t]+([,.!?:;])", r"\1", out)


def literal_greeting_names(text: str) -> list[str]:
    return [m.group(1) for m in _LITERAL_GREETING.finditer(text or "")]


def finalize_template(subject: str, body: str) -> tuple[str, str]:
    """The wording as stored on the campaign: the assessment link lives in the body only."""
    return strip_assessment_link(subject), keep_assessment_link(body)


