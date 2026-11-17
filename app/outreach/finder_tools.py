"""Find new prospects. Connected tools are used when there are any; web search covers what is not connected.

One chat tool, ``find_new_prospects``. It is small on purpose (credits are limited):
* at most ``MAX_FIND`` people per run
* Exa (when its key is set) looks for companies that match Jeanne's targeting rules, then for the founder;
  without Exa, web search finds the companies and the founder
* Prospeo (when connected) is asked for the email only for a named founder, and only a VERIFIED email is kept;
  without Prospeo, only an email printed on a public page of the firm's own domain is kept, never a guessed one
* a connected tool that fails during the run is dropped for the rest of that run and web search takes over
* round 1 applies every rule; later rounds relax only the size range, never the excluded kinds or the place
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
from acquisition.tenant import prospect_client_clause, stamp_prospect
from acquisition.services.prospect_identity import domain_from_website_or_email, unique_placeholder_email
from acquisition.services.prospecting_rules_service import get_prospecting_rules
from app.outreach import lookup
from app.outreach.text import proper_case
from app.outreach.toolkit import Call, ToolError, tool

MAX_FIND = 5  # people per run
COMPANY_POOL = 12  # companies asked from Exa per run (some will be skipped)
MAX_LOOKUPS = 15  # Prospeo lookups per run, whatever the count
MAX_ROUNDS = 3  # Exa searches per run: each round asks for more firms than the last
MAX_NO_EMAIL = 0  # founders saved without an email per run: none, an email is always needed
WEB_POOL = 8  # firms asked from web search per round
EXA_URL = "https://api.exa.ai/search"
SOURCE = "Exa + Prospeo"
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
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
              hi: Optional[int] = None, relax: bool = False) -> list[tuple[str, str]]:
    """(category, search text) pairs: one search per category, best priority first (or one search for the kind of
    firm the user named, labelled with the closest saved category or "Other").
    Whatever the user named replaces that part of the saved rules; everything else comes from the rules."""
    place = f"based in {state}, United States" if state else rules.country
    tail = place if relax else f"{lo or rules.employees_min} to {hi or rules.employees_max} employees, {place}"
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


def _queries(rules: Any, focus: str, state: str = "", lo: Optional[int] = None, hi: Optional[int] = None,
             relax: bool = False) -> list[str]:
    return [q for _, q in _labelled(rules, focus, state, lo, hi, relax)]


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
               hi: Optional[int] = None, size: int = COMPANY_POOL, relax: bool = False) -> list[dict]:
    """Companies from every category, taken in turns so each category gets a chance, minus the excluded kinds."""
    pairs = _labelled(rules, focus, state, lo, hi, relax)
    per = max(3, -(-size // len(pairs)))
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
    return out[:size]


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


def _web_ask(prompt: str) -> str:
    """One web-search answer from GPT (the same research model and key the web research uses)."""
    from openai import OpenAI

    settings = get_settings()
    key = (settings.openai_api_key or "").strip()
    if not key:
        raise ToolError("web search needs an OpenAI key, and none is set up.")
    model = (settings.openai_web_model or settings.openai_model or "").strip() or "gpt-4.1"
    client = OpenAI(api_key=key, timeout=240)
    options = [{"tools": [{"type": "web_search", "search_context_size": "medium"}]}, {"tools": [{"type": "web_search"}]}]
    for i, extra in enumerate(options):
        try:
            return getattr(client.responses.create(model=model, input=prompt, **extra), "output_text", "") or ""
        except Exception as exc:  # noqa: BLE001 - only a refused option falls back to the simpler request
            if i == len(options) - 1 or type(exc).__name__ not in ("BadRequestError", "UnprocessableEntityError"):
                raise ToolError(f"the web search failed: {exc}") from exc
    return ""


def _json_in(text: str, opener: str) -> Any:
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


def _web_email(email: Any, domain: str) -> Optional[str]:
    """The email when it looks real and sits on the firm's own domain; None otherwise (never a guessed address)."""
    from acquisition.services.prospect_identity import is_placeholder_email

    text = str(email or "").strip().lower()
    if not _EMAIL.fullmatch(text) or is_placeholder_email(text):
        return None
    host = text.rsplit("@", 1)[1]
    return text if host == domain or host.endswith("." + domain) else None


def _web_firms(rules: Any, focus: str, state: str, lo: Optional[int], hi: Optional[int], skip: set[str],
               relax: bool = False) -> list[dict]:
    """Firms found by web search, shaped like Exa rows, each with its founder and any email printed on a public page."""
    pairs = _labelled(rules, focus, state, lo, hi, relax)
    names = [c.name for c in rules.categories]
    prompt = (
        f"Use web search to find up to {WEB_POOL} real, owner-led firms that match these searches, best priority first: "
        + " | ".join(q for _, q in pairs) + f". Never include these kinds of business: {', '.join(rules.exclusions) or 'none'}. "
        f"Never include these websites: {', '.join(sorted(d for d in skip if d)[:60]) or 'none'}. "
        "For each firm find its current founder, owner or CEO (name and title), the founder's public LinkedIn URL if you "
        "see one, and a business email address for that person ONLY if it is printed on a public page. Never guess an "
        "address and never build one from a name pattern: use null when you did not see one, and give the page where you "
        "saw it in email_page. Return ONLY a JSON array, one object per firm: "
        '{"company": text, "website": text, "category": one of [' + ", ".join(f'"{n}"' for n in names) + ', "Other"], '
        '"founder_name": text, "founder_title": text, "founder_linkedin": text or null, "email": text or null, '
        '"email_page": text or null, "summary": "what the firm does, in one sentence"}.')
    data = _json_in(_web_ask(prompt), "[")
    banned = [x.casefold() for x in rules.exclusions]
    out: list[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        person = _person_name(str(item.get("founder_name") or ""))
        url = str(item.get("website") or "").strip()
        company = str(item.get("company") or "").strip()
        if not (person and url and company):
            continue
        if not url.lower().startswith("http"):
            url = "https://" + url
        text = f"{company} {item.get('summary') or ''}".casefold()

        def _ban_hit(word: str) -> bool:
            if word in text:
                return True
            if " " in word:
                head, tail = word.rsplit(" ", 1)
                if tail.endswith("s") and f"{head} {tail[:-1]}" in text:
                    return True
                if not tail.endswith("s") and f"{head} {tail}s" in text:
                    return True
            else:
                if word.endswith("s") and word[:-1] in text:
                    return True
                if not word.endswith("s") and f"{word}s" in text:
                    return True
            return False

        if any(_ban_hit(word) for word in banned):
            continue
        email = item.get("email") if str(item.get("email_page") or "").strip() else None
        out.append({"title": company, "url": url, "summary": str(item.get("summary") or ""),
                    "_category": str(item.get("category") or "Other"), "_query": pairs[0][1], "_web": True,
                    "_founder": (person, str(item.get("founder_title") or "").strip()[:300],
                                 str(item.get("founder_linkedin") or "").strip()),
                    "_email": email})
    return out


def _web_email_for(person: str, company: str, domain: str) -> Optional[str]:
    """An email for this person printed on a public page of the firm's own domain, or None."""
    data = _json_in(_web_ask(
        f"Use web search to find a business email address for {person}, founder or owner of {company} ({domain}). "
        "Give it ONLY if it is printed on a public page. Never guess an address and never build one from a name "
        'pattern. Return ONLY a JSON object: {"email": text or null, "page": text or null}.'), "{")
    if not isinstance(data, dict) or not str(data.get("page") or "").strip():
        return None
    return _web_email(data.get("email"), domain)


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
    return (
        call.db.scalar(
            select(Prospect.id).where(prospect_client_clause(), Prospect.domain == domain).limit(1)
        )
        is not None
    )


def _save(call: Call, name: str, person: str, linkedin: str, url: str, domain: str, _unused: str, category: str,
          label: Optional[str], row: dict, facts: dict, email: str, verified: bool = False,
          via_exa: bool = True, relaxed: bool = False) -> Prospect:
    size = facts.get("employee_range") or facts.get("employee_count")
    if via_exa and verified:
        source, found_by = SOURCE, "exa_prospeo"
    elif via_exa:
        source, found_by = "Exa + web search", "exa_web"
    else:
        source, found_by = "Web search", "web_search"
    note = _trim(str(row.get("summary") or ""), 480)
    if relaxed:
        note = (note + " (found with the size range relaxed)").strip()
    prospect = Prospect(
        company=name, executive_name=proper_case(person), email=email, website=url, domain=domain,
        linkedin_url=linkedin or None, source=source, discovery_source=found_by,
        email_confidence="verified" if verified else ("web" if email and "pending-discovery" not in email else "placeholder"),
        icp_category=category, business_type=label,
        discovery_query=str(row.get("_query") or "")[:600] or None,
        headcount_band=str(size) if size else None, fit_note=(note or None),
    )
    stamp_prospect(prospect)
    call.db.add(prospect)
    call.db.commit()
    return prospect


@tool("find_new_prospects", "count", "focus", "state", "employees_min", "employees_max")
def find_new_prospects(call: Call, count: int = MAX_FIND, focus: str = "", state: str = "",
                       employees_min: Optional[int] = None, employees_max: Optional[int] = None) -> str:
    """Find up to 5 new owner-led firms that match Jeanne's targeting rules and save them with an email."""
    if not call.user_id:
        raise ToolError("no signed-in user, so the connected tools cannot be checked.")
    try:
        want = max(1, min(int(count or MAX_FIND), MAX_FIND))
    except (TypeError, ValueError):
        want = MAX_FIND
    from bizos.control import store as control

    ctx = control.build_context(control.get_user(call.user_id))
    from bizos.connectors import composio

    # Which tools are connected. Code decides this, never the model; whatever is not connected is covered by web search.
    key = (get_settings().exa_api_key or "").strip()
    exa_on = bool(key)
    try:
        prospeo_on = bool(composio._active_accounts(ctx).get("prospeo"))
    except Exception:  # noqa: BLE001 - a status lookup that fails counts as not connected
        prospeo_on = False
    if not prospeo_on:
        exa_on = False

    rules = get_prospecting_rules()
    place = _state(state)
    lo, hi = _num(employees_min), _num(employees_max)
    added: list[tuple[Prospect, str]] = []
    nomail: list[Prospect] = []
    skipped: list[str] = []
    notes: list[str] = []
    lookups = 0
    tried: set[str] = set()
    for round_no in range(MAX_ROUNDS):
        if len(added) >= want or lookups >= MAX_LOOKUPS:
            break
        relax = round_no >= 1  # round 1 applies every rule; later rounds relax only the size range
        rows: list[dict] = []
        if exa_on:
            try:
                rows = _companies(key, rules, focus, place, lo, hi, COMPANY_POOL * (round_no + 1), relax)
            except ToolError:
                exa_on = False
                notes.append("Exa did not answer, so web search was used for the rest of this run.")
        if not exa_on:
            rows = _web_firms(rules, focus, place, lo, hi, tried, relax)
        # firms already tried in an earlier round are dropped
        companies = [r for r in rows
                     if (domain_from_website_or_email(website=str(r.get('url') or '')) or '') not in tried]
        if not companies:
            if exa_on:
                break
            continue
        tried.update(domain_from_website_or_email(website=str(r.get('url') or '')) or '' for r in companies)
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
            found = row.get("_founder")
            if found is None and exa_on:
                try:
                    found = _founder(key, name)
                except ToolError:
                    exa_on = False
                    notes.append("Exa did not answer, so web search was used for the rest of this run.")
            if found is None:
                skipped.append(f"{name}: no founder or owner found")
                continue
            person, job, linkedin = found
            email: Optional[str] = None
            facts: dict = {}
            verified = False
            if prospeo_on:
                lookups += 1
                try:
                    email, facts = _verified_email(ctx, person, domain)
                    verified = bool(email)
                except Exception:  # noqa: BLE001 - Prospeo failed (error, no credits): web search covers the rest
                    prospeo_on = False
                    notes.append("Prospeo did not answer, so emails were taken from public pages for the rest of this run.")
            if not verified and not prospeo_on:
                email = _web_email(row.get("_email"), domain)
                if not email and not row.get("_web") and lookups < MAX_LOOKUPS:
                    lookups += 1
                    try:
                        email = _web_email_for(person, name, domain)
                    except ToolError:
                        email = None
            if place and _outside(facts, place):
                skipped.append(f"{name}: based outside {place}")
                continue
            if not email:
                if len(nomail) >= MAX_NO_EMAIL:
                    skipped.append(f"{name}: no email found for {person}")
                    continue
                nomail.append(_save(call, name, person, linkedin, url, domain, "", category, label, row, facts,
                                    unique_placeholder_email(), via_exa=not row.get("_web"), relaxed=relax))
                continue
            if lookup.by_email(call.db, email) is not None:
                skipped.append(f"{person} <{email}>: already saved")
                continue
            made = _save(call, name, person, linkedin, url, domain, "", category, label, row, facts, email, verified,
                         via_exa=not row.get("_web"), relaxed=relax)
            added.append((made, "verified by Prospeo" if verified else "found on a public page, not verified"))
    lines = [f"Added {len(added)} of {want} asked: " + "; ".join(
        f"{p.executive_name} <{p.email}> ({p.company}; category: {p.icp_category or '-'}; "
        f"business type: {p.business_type or '-'}; found by: {p.discovery_query or '-'}; "
        f"source: {p.source}; email: {how})" for p, how in added) + "." if added
        else f"Added 0 of {want} asked."]
    if nomail:
        lines.append("Also saved without an email (founder found, no email): " + "; ".join(
            f"{p.executive_name} ({p.company}; category: {p.icp_category or '-'}; business type: {p.business_type or '-'})"
            for p in nomail) + ". Their Email shows No email yet. They can be researched but cannot be emailed.")
    if notes:
        lines.append(" ".join(dict.fromkeys(notes)))
    if not exa_on:
        lines.append("Exa is not connected, so web search found the firms.")
    if not prospeo_on:
        lines.append("Prospeo is not connected or stopped, so emails come from public pages and are not verified.")
    if skipped:
        lines.append("Skipped: " + " | ".join(skipped[:8]) + ".")
    if len(added) < want:
        lines.append(f"Only {len(added)} of {want} could be found within the search limit ({lookups} email lookups); ask again to continue.")
    lines.append(f"Email lookups used: {lookups}. Nothing was emailed. Show the people added in the prospects table "
                 "with the Category column, as for any prospect answer.")
    return " ".join(lines)
