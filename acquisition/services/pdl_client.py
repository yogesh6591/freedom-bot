"""People Data Labs person search for Prospecting rules. Search only; the key never leaves process env."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from acquisition.services.apollo_client import pick_category
from acquisition.services.prospecting_rules_service import ProspectingCategory, ProspectingRules

logger = logging.getLogger(__name__)

PERSON_SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
SEARCH_SIZE = 5
DATA_INCLUDE = ",".join(
    (
        "full_name",
        "first_name",
        "last_name",
        "job_title",
        "job_company_name",
        "job_company_website",
        "job_company_industry",
        "job_company_industry_v2",
        "industry",
        "location_name",
        "location_locality",
        "location_region",
        "location_country",
    )
)

# Canonical values from https://docs.peopledatalabs.com/docs/job-title-levels
_JOB_TITLE_LEVELS = frozenset(
    {"cxo", "director", "entry", "manager", "owner", "partner", "senior", "training", "unpaid", "vp"}
)
# Canonical values from https://docs.peopledatalabs.com/docs/company-sizes
_COMPANY_SIZES: tuple[tuple[int, Optional[int], str], ...] = (
    (1, 10, "1-10"),
    (11, 50, "11-50"),
    (51, 200, "51-200"),
    (201, 500, "201-500"),
    (501, 1000, "501-1000"),
    (1001, 5000, "1001-5000"),
    (5001, 10000, "5001-10000"),
    (10001, None, "10001+"),
)
# Canonical values from https://docs.peopledatalabs.com/docs/inferred-revenue-ranges
_REVENUE_RANGES: tuple[tuple[int, Optional[int], str], ...] = (
    (0, 1_000_000, "$0-$1M"),
    (1_000_000, 10_000_000, "$1M-$10M"),
    (10_000_000, 25_000_000, "$10M-$25M"),
    (25_000_000, 50_000_000, "$25M-$50M"),
    (50_000_000, 100_000_000, "$50M-$100M"),
    (100_000_000, 250_000_000, "$100M-$250M"),
    (250_000_000, 500_000_000, "$250M-$500M"),
    (500_000_000, 1_000_000_000, "$500M-$1B"),
    (1_000_000_000, 10_000_000_000, "$1B-$10B"),
    (10_000_000_000, None, "$10B+"),
)
_US_ALIASES = frozenset({"united states", "usa", "us", "united states of america"})
_CONTACT_KEYS = frozenset(
    {
        "email",
        "emails",
        "work_email",
        "personal_emails",
        "recommended_personal_email",
        "phone",
        "phones",
        "phone_numbers",
        "mobile_phone",
        "mobile_phones",
        "work_phone",
        "telephone",
    }
)


class PDLError(Exception):
    """User-facing PDL failure. ``message`` is safe to show in the admin UI."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class PDLConfigError(PDLError):
    """The API key is missing from the environment."""


@dataclass
class PDLPerson:
    company: str = ""
    website: str = ""
    name: str = ""
    title: str = ""
    location: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "company": self.company,
            "website": self.website,
            "name": self.name,
            "title": self.title,
            "location": self.location,
        }


@dataclass
class PDLSearchResult:
    total_matches: int
    dropped_by_exclusions: int
    category_name: str
    category_keywords: list[str]
    filters: dict[str, Any]
    not_applied: list[str] = field(default_factory=list)
    people: list[PDLPerson] = field(default_factory=list)


def pdl_key_configured() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    return (os.environ.get("PDL_API_KEY") or "").strip()


def overlapping_company_sizes(employees_min: int, employees_max: int) -> list[str]:
    lo = int(employees_min)
    hi = int(employees_max)
    if hi < lo:
        lo, hi = hi, lo
    out: list[str] = []
    for start, end, label in _COMPANY_SIZES:
        top = end if end is not None else hi
        if top >= lo and start <= hi:
            out.append(label)
    return out


def overlapping_revenue_ranges(revenue_min: int, revenue_max: int) -> list[str]:
    lo = int(revenue_min)
    hi = int(revenue_max)
    if hi < lo:
        lo, hi = hi, lo
    out: list[str] = []
    for start, end, label in _REVENUE_RANGES:
        top = end if end is not None else hi
        if top >= lo and start <= hi:
            out.append(label)
    return out


def job_title_levels_for(titles: list[str]) -> list[str]:
    levels: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = " ".join(str(raw or "").split()).casefold()
        if not title:
            continue
        if title.startswith("chief ") or (len(title) <= 4 and title.startswith("c") and title.endswith("o")):
            if "cxo" not in seen:
                seen.add("cxo")
                levels.append("cxo")
        for word in title.replace("/", " ").replace("-", " ").split():
            if word in _JOB_TITLE_LEVELS and word not in seen:
                seen.add(word)
                levels.append(word)
    return levels


def build_search_body(
    rules: ProspectingRules,
    *,
    category: ProspectingCategory | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Documented Person Search JSON body. Always size=5; never paginates."""
    titles = [str(t).strip() for t in (rules.titles or []) if str(t).strip()]
    keywords = [str(k).strip() for k in (category.keywords if category else []) if str(k).strip()]
    sizes = overlapping_company_sizes(int(rules.employees_min), int(rules.employees_max))
    levels = job_title_levels_for(titles)
    not_applied: list[str] = [
        "sweet-spot revenue (PDL has no sweet-spot filter)",
        "verified-email requirement (search does not request emails)",
        "discovery daily target",
        "outreach daily cap",
        "bounce-rate pause",
    ]
    must: list[dict[str, Any]] = [
        {"term": {"location_country": "united states"}},
        {"term": {"job_company_location_country": "united states"}},
    ]
    country = " ".join(str(rules.country or "").split()).casefold()
    if country and country not in _US_ALIASES:
        not_applied.append(f"country {rules.country!s} (this test search is US only)")

    if sizes:
        must.append({"terms": {"job_company_size": sizes}})
    else:
        not_applied.append("employee range (no overlapping PDL company size)")

    revenue_labels: list[str] = []
    if rules.revenue_is_soft:
        not_applied.append("revenue (soft — not sent)")
    else:
        revenue_labels = overlapping_revenue_ranges(int(rules.revenue_min), int(rules.revenue_max))
        if revenue_labels:
            must.append({"terms": {"job_company_inferred_revenue": revenue_labels}})
        else:
            not_applied.append("revenue (no overlapping PDL inferred-revenue range)")

    title_should: list[dict[str, Any]] = [
        {"match_phrase": {"job_title": title.casefold()}} for title in titles
    ]
    if levels:
        title_should.append({"terms": {"job_title_levels": levels}})
    if title_should:
        must.append({"bool": {"should": title_should}})
    else:
        not_applied.append("titles (none on the rules)")

    if keywords:
        keyword_should: list[dict[str, Any]] = []
        for keyword in keywords:
            folded = keyword.casefold()
            keyword_should.extend(
                [
                    {"match": {"job_title": folded}},
                    {"match": {"job_company_name": folded}},
                    {"term": {"job_company_industry": folded}},
                    {"term": {"industry": folded}},
                ]
            )
        must.append({"bool": {"should": keyword_should}})
    else:
        not_applied.append("category keywords (none on this category)")

    body = {
        "query": {"bool": {"must": must}},
        "size": SEARCH_SIZE,
        "titlecase": True,
        "data_include": DATA_INCLUDE,
    }
    filters = {
        "category": category.name if category else "",
        "keywords": list(keywords),
        "titles": titles,
        "job_title_levels": levels,
        "location_country": "united states",
        "job_company_location_country": "united states",
        "job_company_size": sizes,
        "job_company_inferred_revenue": revenue_labels,
        "size": SEARCH_SIZE,
    }
    return body, filters, not_applied


def _company_blob(person: dict[str, Any]) -> str:
    parts = [
        person.get("job_company_name"),
        person.get("job_company_industry"),
        person.get("job_company_industry_v2"),
        person.get("industry"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def apply_exclusions(
    people: list[dict[str, Any]], exclusions: list[str]
) -> tuple[list[dict[str, Any]], int]:
    phrases = [p.casefold() for p in exclusions if str(p).strip()]
    if not phrases:
        return list(people), 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for person in people:
        blob = _company_blob(person if isinstance(person, dict) else {})
        if any(phrase in blob for phrase in phrases):
            dropped += 1
            continue
        kept.append(person)
    return kept, dropped


def _join(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if str(p or "").strip())


def sanitize_person(raw: dict[str, Any]) -> PDLPerson:
    name = str(raw.get("full_name") or "").strip()
    if not name:
        name = " ".join(
            part
            for part in (
                str(raw.get("first_name") or "").strip(),
                str(raw.get("last_name") or "").strip(),
            )
            if part
        )
    location = str(raw.get("location_name") or "").strip() or _join(
        raw.get("location_locality"),
        raw.get("location_region"),
        raw.get("location_country"),
    )
    return PDLPerson(
        company=str(raw.get("job_company_name") or "").strip(),
        website=str(raw.get("job_company_website") or "").strip(),
        name=name,
        title=str(raw.get("job_title") or "").strip(),
        location=location,
    )


def _payload_text(response: httpx.Response) -> tuple[dict[str, Any], str]:
    text = (response.text or "")[:2000]
    try:
        data = response.json()
    except ValueError:
        return {}, text
    return data if isinstance(data, dict) else {}, text


def _pdl_text(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("message", "type"):
            text = str(error.get(key) or "").strip()
            if text:
                return text
    elif isinstance(error, str) and error.strip():
        return error.strip()
    return str(payload.get("message") or "").strip()


def _raise_http_error(response: httpx.Response) -> None:
    payload, _text = _payload_text(response)
    status = response.status_code
    pdl = _pdl_text(payload)
    if status == 401:
        ours = "PDL did not accept the API key."
        raise PDLError(f"{ours} — {pdl}" if pdl else ours, status_code=401)
    if status == 402:
        ours = "PDL says this search needs more credits."
        raise PDLError(f"{ours} — {pdl}" if pdl else ours, status_code=402)
    if status == 429:
        ours = "PDL is rate-limiting this search. Try again in a moment."
        raise PDLError(f"{ours} — {pdl}" if pdl else ours, status_code=429)
    if status >= 500:
        ours = "PDL had a server error. Try again in a moment."
        raise PDLError(f"{ours} — {pdl}" if pdl else ours, status_code=status)
    ours = "PDL refused this search."
    raise PDLError(f"{ours} — {pdl}" if pdl else ours, status_code=status)


def _assert_person_search(url: str, body: dict[str, Any]) -> None:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path != "/v5/person/search":
        raise PDLError("PDL search is not configured correctly.")
    if int(body.get("size") or 0) != SEARCH_SIZE:
        raise PDLError("PDL search size is not configured correctly.")
    if "from" in body or "scroll_token" in body:
        raise PDLError("PDL search must not paginate.")


def _http_post(url: str, *, headers: dict[str, str], json: dict[str, Any]) -> httpx.Response:
    _assert_person_search(url, json)
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        return client.post(url, headers=headers, json=json)


def _post(body: dict[str, Any]) -> httpx.Response:
    key = _api_key()
    if not key:
        raise PDLConfigError("PDL key not set")
    headers = {
        "X-Api-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        response = _http_post(PERSON_SEARCH_URL, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        logger.warning("pdl person search timed out")
        raise PDLError("PDL did not respond in time.") from exc
    except httpx.HTTPError as exc:
        logger.warning("pdl person search network error type=%s", type(exc).__name__)
        raise PDLError("Could not reach PDL. Try again.") from exc
    logger.info("pdl person search status=%s", response.status_code)
    if response.status_code == 404:
        return response
    if response.status_code >= 400:
        _raise_http_error(response)
    return response


def search_people(rules: ProspectingRules, *, category_name: str | None = None) -> PDLSearchResult:
    """One person-search page of at most 5 records. Does not save prospects and does not request emails."""
    category = pick_category(rules, category_name)
    body, filters, not_applied = build_search_body(rules, category=category)
    response = _post(body)
    payload, _text = _payload_text(response)
    if response.status_code >= 400 and response.status_code != 404:
        _raise_http_error(response)
    raw_people = payload.get("data")
    people = [row for row in raw_people if isinstance(row, dict)] if isinstance(raw_people, list) else []
    if any(_CONTACT_KEYS.intersection({str(k).casefold() for k in row.keys()}) for row in people):
        people = [{k: v for k, v in row.items() if str(k).casefold() not in _CONTACT_KEYS} for row in people]
    kept, dropped = apply_exclusions(people, list(rules.exclusions or []))
    total = payload.get("total")
    try:
        total_matches = int(total)
    except (TypeError, ValueError):
        total_matches = len(people)
    logger.info(
        "pdl person search status=%s returned=%s dropped=%s total=%s",
        response.status_code,
        len(people),
        dropped,
        total_matches,
    )
    return PDLSearchResult(
        total_matches=total_matches,
        dropped_by_exclusions=dropped,
        category_name=category.name if category else "",
        category_keywords=list(category.keywords) if category else [],
        filters=filters,
        not_applied=not_applied,
        people=[sanitize_person(row) for row in kept[:SEARCH_SIZE]],
    )
