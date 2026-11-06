"""Find new prospects: Exa finds the companies and the owner's name, Prospeo finds the verified email.

One chat tool, ``find_new_prospects``. It is small on purpose (credits are limited):
* at most ``MAX_FIND`` people per run
* Exa looks for companies that match Jeanne's targeting rules (Administration page), then for the founder
* Prospeo is asked for the email only for a named founder, and only a VERIFIED email is kept
* people already saved (same email or same company website) are never searched again or paid for
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx
from sqlalchemy import select

from acquisition.config import get_settings
from acquisition.models.prospect import Prospect
from acquisition.services.prospect_identity import domain_from_website_or_email, unique_placeholder_email
from acquisition.services.prospecting_rules_service import get_prospecting_rules
from app.outreach import lookup
from app.outreach.text import proper_case
from app.outreach.toolkit import Call, ToolError, tool

MAX_FIND = 5  # people per run
COMPANY_POOL = 12  # companies asked from Exa per run (some will be skipped)
MAX_LOOKUPS = 10  # Prospeo lookups per run, whatever the count
MAX_NO_EMAIL = 0  # founders saved without an email per run: none, only verified emails are kept
EXA_URL = "https://api.exa.ai/search"
SOURCE = "Exa + Prospeo"
STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_OF = {name: code for code, name in STATES.items()}
_OWNER = re.compile(r"\b(founder|co-?founder|owner|ceo|chief executive|president|managing (partner|director))\b", re.I)
_FORMER = re.compile(r"\b(former|formerly|ex-|previously|retired|past)\b", re.I)
_SPLIT = re.compile(r"\s[-–—|·•]\s")


def _exa(key: str, body: dict) -> list[dict]:
    try:
        reply = httpx.post(EXA_URL, json=body, headers={"x-api-key": key}, timeout=45)
        reply.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"the company search failed: {exc}") from exc
    return [r for r in (reply.json().get("results") or []) if isinstance(r, dict)]


def _state(value: str) -> str:
    """The full state name for a name or abbreviation the user typed, or "" when none was given."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if text.upper() in STATES:
        return STATES[text.upper()]
    match = next((n for n in STATES.values() if n.casefold() == text.casefold()), None)
    if match is None:
        raise ToolError(f"{text!r} is not a US state. Ask the user for a state name or its two-letter code.")
    return match


def _labelled(rules: Any, focus: str, state: str = "", lo: Optional[int] = None,
              hi: Optional[int] = None) -> list[tuple[str, str]]:
    """(category, search text) pairs: one search per category, best priority first (or one search for the kind of
    firm the user named, labelled with the closest saved category or "Other").
    Whatever the user named replaces that part of the saved rules; everything else comes from the rules."""
    place = f"based in {state}, United States" if state else rules.country
    tail = f"{lo or rules.employees_min} to {hi or rules.employees_max} employees, {place}"
    if focus.strip():
        low = focus.strip().casefold()
        name = next((c.name for c in rules.categories
                     if any(w and w.casefold() in low for w in [c.name, *c.keywords])), "Other")
        return [(name, f"founder-led {focus.strip()} firm, {tail}")]
    out = []
    for category in sorted(rules.categories, key=lambda c: c.priority):
        kind = " or ".join(category.keywords[:2]) or category.name
        out.append((category.name, f"founder-led {kind} firm, {tail}"))
    return out or [("Other", f"founder-led consulting or advisory firm, {tail}")]


def _queries(rules: Any, focus: str, state: str = "", lo: Optional[int] = None, hi: Optional[int] = None) -> list[str]:
    return [q for _, q in _labelled(rules, focus, state, lo, hi)]


def _small_model(prompt: str) -> str:
    """One cheap call (gpt-4o-mini unless FINDER_CLASSIFY_MODEL says otherwise)."""
    from openai import OpenAI

    key = (get_settings().openai_api_key or "").strip()
    if not key:
        raise RuntimeError("no OpenAI key is set up")
    model = (os.environ.get("FINDER_CLASSIFY_MODEL") or "gpt-4o-mini").strip()
    reply = OpenAI(api_key=key, timeout=60).chat.completions.create(
        model=model, temperature=0, messages=[{"role": "user", "content": prompt}])
    return reply.choices[0].message.content or ""


def _classify(rules: Any, focus: str, rows: list[dict]) -> dict[int, tuple[str, Optional[str], bool]]:
    """{row number: (category, what the firm is, matches what was asked)} from ONE small-model call for all the
    firms found. Empty when the call fails, so the search's own category is used and nothing is dropped."""
    if not rows:
        return {}
    names = [c.name for c in rules.categories]
    wanted = (f"the kind of firm the user asked for: {focus.strip()}" if focus.strip()
              else "one of these kinds of firm: " + "; ".join(names))
    skip = ", ".join(rules.exclusions) or "none"
    firms = "\n".join(f"{i}. {r.get('title') or ''} - {str(r.get('summary') or '')[:400]}" for i, r in enumerate(rows))
    prompt = (
        "Classify each firm from its short description. Reply with ONLY a JSON array, one object per firm: "
        '{"i": number, "category": exactly one of [' + ", ".join(f'"{n}"' for n in names) + ', "Other"], '
        '"business_type": "what the firm is in 2 to 4 words", "matches": true or false}. '
        f"matches is true only when the firm is founder-led and is {wanted}, and is not an excluded kind "
        f"(excluded: {skip}). Use only what the description says.\n\n{firms}")
    try:
        data = json.loads(re.search(r"\[.*\]", _small_model(prompt), re.S).group(0))
    except Exception:  # noqa: BLE001 - no answer: keep the search's own category
        return {}
    out: dict[int, tuple[str, Optional[str], bool]] = {}
    for item in data if isinstance(data, list) else []:
        try:
            i = int(item["i"])
        except (KeyError, TypeError, ValueError):
            continue
        said = str(item.get("category") or "").strip()
        category = next((n for n in names if n.casefold() == said.casefold()), "Other")
        label = str(item.get("business_type") or "").strip()[:128] or None
        out[i] = (category, label, item.get("matches") is True)
    return out


def _companies(key: str, rules: Any, focus: str, state: str = "", lo: Optional[int] = None,
               hi: Optional[int] = None) -> list[dict]:
    """Companies from every category, taken in turns so each category gets a chance, minus the excluded kinds."""
    pairs = _labelled(rules, focus, state, lo, hi)
    per = max(3, -(-COMPANY_POOL // len(pairs)))
    lists = [[dict(r, _category=cat, _query=q) for r in _exa(key, {
                 "query": q, "category": "company", "userLocation": "US", "numResults": per,
                 "contents": {"summary": {"query": "What the firm does, number of employees, and where it is based"}}})]
             for cat, q in pairs]
    banned = [x.casefold() for x in rules.exclusions]
    seen: set[str] = set()
    out: list[dict] = []
    for i in range(per):
        for rows in lists:
            if i >= len(rows):
                continue
            row = rows[i]
            url = str(row.get("url") or "")
            text = f"{row.get('title') or ''} {row.get('summary') or ''}".casefold()
            if url in seen or any(word in text for word in banned):
                continue
            if state and state.casefold() not in text:
                continue  # the search page does not place the firm in this state
            seen.add(url)
            out.append(row)
    return out[:COMPANY_POOL]


def _trim(text: str, size: int) -> str:
    """The text cut at a whole word, with no half word left at the end."""
    text = " ".join(text.split())
    if len(text) <= size:
        return text
    return text[:size].rsplit(" ", 1)[0].rstrip(",;:- ") + "..."


def _company_name(title: str) -> str:
    return _SPLIT.split(title or "")[0].strip()


def _person_name(title: str) -> Optional[str]:
    """``Stephanie Warlick - Founder - 5FT View | LinkedIn`` -> ``Stephanie Warlick``; None if it is not a name."""
    head = _SPLIT.split(title or "")[0].strip()
    parts = head.split()
    if not 2 <= len(parts) <= 4 or not all(re.fullmatch(r"[A-Za-z][A-Za-z.'’-]*", p) for p in parts):
        return None
    return head


def _founder(key: str, company: str) -> Optional[tuple[str, str, str]]:
    """(name, job text, LinkedIn url) of the firm's founder/owner as found by Exa, or None."""
    token = next((w.lower() for w in re.findall(r"[A-Za-z0-9]+", company) if len(w) >= 3), "")
    rows = _exa(key, {"query": f"founder or CEO of {company}", "category": "people", "numResults": 3,
                      "contents": {"summary": {"query": "Current job title and company"}}})
    for row in rows:
        name = _person_name(str(row.get("title") or ""))
        text = f"{row.get('title') or ''} {row.get('summary') or ''}"
        if not name or not _OWNER.search(text) or _FORMER.search(str(row.get("summary") or "")):
            continue
        if token and token not in text.lower():
            continue  # a different company's founder
        return name, str(row.get("summary") or "").strip()[:300], str(row.get("url") or "")
    return None


def _verified_email(ctx: Any, name: str, domain: str) -> tuple[Optional[str], dict]:
    """(email, Prospeo company facts). The email is only returned when Prospeo has verified it."""
    from bizos.connectors import composio
    from bizos.connectors.base import ConnectorError

    first, _, last = name.partition(" ")
    try:
        data = composio.execute(ctx, "prospeo", "PROSPEO_ENRICH_PERSON", {
            "person": {"first_name": first, "last_name": last.strip(), "company_website": domain},
            "only_verified_email": True,
        })
    except ConnectorError as exc:
        if any(word in str(exc).upper() for word in ("INVALID_DATAPOINTS", "NO_MATCH", "NOT_FOUND")):
            return None, {}  # this one person cannot be matched: no verified email, move on to the next company
        raise ToolError(str(exc)) from exc
    data = data if isinstance(data, dict) else {}
    data = data.get("data") if isinstance(data.get("data"), dict) and "person" not in data else data
    mail = ((data.get("person") or {}).get("email")) or {}
    company = data.get("company") or {}
    if isinstance(mail, dict) and mail.get("email") and str(mail.get("status", "")).upper() == "VERIFIED":
        return str(mail["email"]).strip().lower(), company
    return None, company


def _num(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") and int(value) > 0 else None
    except (TypeError, ValueError):
        return None


def _outside(facts: dict, place: str) -> bool:
    """True when Prospeo says the firm is based somewhere other than ``place``."""
    where = facts.get("location") or {}
    found = f"{where.get('state') or ''} {where.get('raw_address') or ''}".casefold()
    return bool(place and found.strip() and place.casefold() not in found
                and not re.search(rf"\b{STATE_OF[place].casefold()}\b", found))


def _taken(call: Call, domain: str) -> bool:
    return call.db.scalar(select(Prospect.id).where(Prospect.domain == domain).limit(1)) is not None


def _save(call: Call, name: str, person: str, linkedin: str, url: str, domain: str, _unused: str, category: str,
          label: Optional[str], row: dict, facts: dict, email: str, verified: bool = False) -> Prospect:
    size = facts.get("employee_range") or facts.get("employee_count")
    prospect = Prospect(
        company=name, executive_name=proper_case(person), email=email, website=url, domain=domain,
        linkedin_url=linkedin or None, source=SOURCE, discovery_source="exa_prospeo",
        email_confidence="verified" if verified else "placeholder", icp_category=category, business_type=label,
        discovery_query=str(row.get("_query") or "")[:600] or None,
        headcount_band=str(size) if size else None, fit_note=(_trim(str(row.get("summary") or ""), 500) or None),
    )
    call.db.add(prospect)
    call.db.commit()
    return prospect


@tool("find_new_prospects", "count", "focus", "state", "employees_min", "employees_max")
def find_new_prospects(call: Call, count: int = MAX_FIND, focus: str = "", state: str = "",
                       employees_min: Optional[int] = None, employees_max: Optional[int] = None) -> str:
    """Find up to 5 new owner-led firms that match Jeanne's targeting rules and save them with a verified email."""
    if not call.user_id:
        raise ToolError("no signed-in user, so Prospeo cannot be used.")
    key = get_settings().exa_api_key
    if not key:
        raise ToolError("the company search is not set up (EXA_API_KEY is missing).")
    try:
        want = max(1, min(int(count or MAX_FIND), MAX_FIND))
    except (TypeError, ValueError):
        want = MAX_FIND
    from bizos.control import store as control

    ctx = control.build_context(control.get_user(call.user_id))
    from bizos.connectors import composio

    if not composio._active_accounts(ctx).get("prospeo"):
        raise ToolError("Prospeo is not connected for your account. Connect it on the Integrations page first.")

    rules = get_prospecting_rules()
    place = _state(state)
    lo, hi = _num(employees_min), _num(employees_max)
    companies = _companies(key, rules, focus, place, lo, hi)
    added: list[Prospect] = []
    nomail: list[Prospect] = []
    skipped: list[str] = []
    lookups = 0
    kinds = _classify(rules, focus, companies)  # one cheap call; the firms that do not match are never looked up
    for number, row in enumerate(companies):
        if len(added) >= want or lookups >= MAX_LOOKUPS:
            break
        url = str(row.get("url") or "")
        name = _company_name(str(row.get("title") or "")) or url
        domain = domain_from_website_or_email(website=url)
        if not domain:
            continue
        if _taken(call, domain):
            skipped.append(f"{name}: already saved")
            continue
        category, label, matches = kinds.get(number, (str(row.get("_category") or "Other"), None, True))
        if not matches:
            skipped.append(f"{name}: not what was asked ({label or 'another kind of firm'})")
            continue
        found = _founder(key, name)
        if found is None:
            skipped.append(f"{name}: no founder or owner found")
            continue
        person, job, linkedin = found
        lookups += 1
        email, facts = _verified_email(ctx, person, domain)
        if place and _outside(facts, place):
            skipped.append(f"{name}: based outside {place}")
            continue
        if not email:
            if len(nomail) >= MAX_NO_EMAIL:
                skipped.append(f"{name}: no verified email for {person}")
                continue
            nomail.append(_save(call, name, person, linkedin, url, domain, "", category, label, row, facts, unique_placeholder_email()))
            continue
        if lookup.by_email(call.db, email) is not None:
            skipped.append(f"{person} <{email}>: already saved")
            continue
        added.append(_save(call, name, person, linkedin, url, domain, "", category, label, row, facts, email, True))
    lines = [f"Added {len(added)} of {want} asked: " + "; ".join(
        f"{p.executive_name} <{p.email}> ({p.company}; category: {p.icp_category or '-'}; "
        f"business type: {p.business_type or '-'}; found by: {p.discovery_query or '-'})" for p in added) + "." if added
        else f"Added 0 of {want} asked."]
    if nomail:
        lines.append("Also saved without an email (founder found, no verified email): " + "; ".join(
            f"{p.executive_name} ({p.company}; category: {p.icp_category or '-'}; business type: {p.business_type or '-'})"
            for p in nomail) + ". Their Email shows No email yet. They can be researched but cannot be emailed.")
    if skipped:
        lines.append("Skipped: " + " | ".join(skipped[:8]) + ".")
    lines.append(f"Prospeo lookups used: {lookups}. Nothing was emailed. Show the people added in the prospects table "
                 "with the Category column, as for any prospect answer.")
    return " ".join(lines)
