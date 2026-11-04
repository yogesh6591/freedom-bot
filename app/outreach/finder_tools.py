"""Find new prospects: Exa finds the companies and the owner's name, Prospeo finds the verified email.

One chat tool, ``find_new_prospects``. It is small on purpose (credits are limited):
* at most ``MAX_FIND`` people per run
* Exa looks for companies that match Jeanne's targeting rules (Administration page), then for the founder
* Prospeo is asked for the email only for a named founder, and only a VERIFIED email is kept
* people already saved (same email or same company website) are never searched again or paid for
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from sqlalchemy import select

from acquisition.config import get_settings
from acquisition.models.prospect import Prospect
from acquisition.services.prospect_identity import domain_from_website_or_email
from acquisition.services.prospecting_rules_service import get_prospecting_rules
from app.outreach import lookup
from app.outreach.text import proper_case
from app.outreach.toolkit import Call, ToolError, tool

MAX_FIND = 5  # people per run
COMPANY_POOL = 12  # companies asked from Exa per run (some will be skipped)
MAX_LOOKUPS = 10  # Prospeo lookups per run, whatever the count
EXA_URL = "https://api.exa.ai/search"
SOURCE = "Exa + Prospeo"
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


def _query(rules: Any, focus: str) -> str:
    if focus.strip():
        kind = focus.strip()
    else:
        words: list[str] = []
        for category in sorted(rules.categories, key=lambda c: c.priority)[:3]:
            words.append(category.keywords[0] if category.keywords else category.name)
        kind = " or ".join(words) or "consulting or advisory firm"
    return f"founder-led {kind} firm, {rules.employees_min} to {rules.employees_max} employees, {rules.country}"


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
        raise ToolError(str(exc)) from exc
    data = data if isinstance(data, dict) else {}
    data = data.get("data") if isinstance(data.get("data"), dict) and "person" not in data else data
    mail = ((data.get("person") or {}).get("email")) or {}
    company = data.get("company") or {}
    if isinstance(mail, dict) and mail.get("email") and str(mail.get("status", "")).upper() == "VERIFIED":
        return str(mail["email"]).strip().lower(), company
    return None, company


def _taken(call: Call, domain: str) -> bool:
    return call.db.scalar(select(Prospect.id).where(Prospect.domain == domain).limit(1)) is not None


@tool("find_new_prospects", "count", "focus")
def find_new_prospects(call: Call, count: int = MAX_FIND, focus: str = "") -> str:
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
    companies = _exa(key, {
        "query": _query(rules, focus), "category": "company", "userLocation": "US", "numResults": COMPANY_POOL,
        "contents": {"summary": {"query": "What the firm does, number of employees, and where it is based"}},
    })
    added: list[Prospect] = []
    skipped: list[str] = []
    lookups = 0
    for row in companies:
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
        found = _founder(key, name)
        if found is None:
            skipped.append(f"{name}: no founder or owner found")
            continue
        person, job, linkedin = found
        lookups += 1
        email, facts = _verified_email(ctx, person, domain)
        if not email:
            skipped.append(f"{name}: no verified email for {person}")
            continue
        if lookup.by_email(call.db, email) is not None:
            skipped.append(f"{person} <{email}>: already saved")
            continue
        size = facts.get("employee_range") or facts.get("employee_count")
        prospect = Prospect(
            company=name, executive_name=proper_case(person), email=email, website=url, domain=domain,
            linkedin_url=linkedin or None, source=SOURCE, discovery_source="assistant",
            headcount_band=str(size) if size else None, fit_note=(str(row.get("summary") or "").strip()[:500] or None),
        )
        call.db.add(prospect)
        call.db.commit()
        added.append(prospect)
    lines = [f"Added {len(added)} of {want} asked: " + "; ".join(
        f"{p.executive_name} <{p.email}> ({p.company})" for p in added) + "." if added
        else f"Added 0 of {want} asked."]
    if skipped:
        lines.append("Skipped: " + " | ".join(skipped[:8]) + ".")
    lines.append(f"Prospeo lookups used: {lookups}. Nothing was emailed.")
    return " ".join(lines)
