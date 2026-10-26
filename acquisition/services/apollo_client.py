"""Apollo people search for Prospecting rules. Search only; the key never leaves process env."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from acquisition.services.prospecting_rules_service import ProspectingCategory, ProspectingRules

logger = logging.getLogger(__name__)

PEOPLE_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
MAX_429_RETRIES = 1
SHORT_RETRY_AFTER_SECONDS = 2.0

_EMAIL_KEYS = frozenset(
    {
        "email",
        "emails",
        "email_status",
        "personal_emails",
        "work_email",
        "revealed_email",
    }
)


class ApolloError(Exception):
    """User-facing Apollo failure. ``message`` is safe to show in the admin UI."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ApolloConfigError(ApolloError):
    """The API key is missing from the environment."""


@dataclass
class ApolloPerson:
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
class ApolloSearchResult:
    total_matches: int
    dropped_by_exclusions: int
    category_name: str
    category_keywords: list[str]
    filters: dict[str, Any]
    people: list[ApolloPerson] = field(default_factory=list)


def apollo_key_configured() -> bool:
    return bool(_api_key())


def _api_key() -> str:
    return (os.environ.get("APOLLO_API_KEY") or "").strip()


def pick_category(rules: ProspectingRules, category_name: str | None = None) -> ProspectingCategory | None:
    categories = list(rules.categories or [])
    if not categories:
        return None
    wanted = " ".join(str(category_name or "").split()).casefold()
    if wanted:
        for category in categories:
            if category.name.casefold() == wanted:
                return category
    return categories[0]


def build_search_params(
    rules: ProspectingRules,
    *,
    category: ProspectingCategory | None,
    page: int,
    per_page: int,
) -> list[tuple[str, Any]]:
    """Documented query parameters for people search (OpenAPI ``mixed_people/api_search``)."""
    location = (rules.country or "United States").strip() or "United States"
    page = max(1, int(page))
    per_page = min(100, max(1, int(per_page)))
    params: list[tuple[str, Any]] = []
    for title in rules.titles or []:
        text = str(title or "").strip()
        if text:
            params.append(("person_titles[]", text))
    params.append(("person_locations[]", location))
    params.append(("organization_locations[]", location))
    params.append(
        (
            "organization_num_employees_ranges[]",
            f"{int(rules.employees_min)},{int(rules.employees_max)}",
        )
    )
    keywords = " ".join(str(k).strip() for k in (category.keywords if category else []) if str(k).strip())
    if keywords:
        params.append(("q_keywords", keywords))
    if not rules.revenue_is_soft:
        params.append(("revenue_range[min]", int(rules.revenue_min)))
        params.append(("revenue_range[max]", int(rules.revenue_max)))
    params.append(("page", page))
    params.append(("per_page", per_page))
    return params


def filters_from_params(params: list[tuple[str, Any]], *, category: ProspectingCategory | None) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for key, value in params:
        if key.endswith("[]"):
            grouped.setdefault(key, []).append(value)
        else:
            grouped[key] = value
    return {
        "category": category.name if category else "",
        "keywords": list(category.keywords) if category else [],
        "q_keywords": grouped.get("q_keywords") or "",
        "person_titles": grouped.get("person_titles[]") or [],
        "person_locations": grouped.get("person_locations[]") or [],
        "organization_locations": grouped.get("organization_locations[]") or [],
        "organization_num_employees_ranges": grouped.get("organization_num_employees_ranges[]") or [],
        "revenue_range_min": grouped.get("revenue_range[min]"),
        "revenue_range_max": grouped.get("revenue_range[max]"),
        "page": grouped.get("page"),
        "per_page": grouped.get("per_page"),
    }


def _company_blob(person: dict[str, Any]) -> str:
    org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    parts: list[str] = [str(org.get("name") or "")]
    industry = org.get("industry")
    if industry:
        parts.append(str(industry))
    industries = org.get("industries")
    if isinstance(industries, list):
        parts.extend(str(item) for item in industries)
    keywords = org.get("keywords")
    if isinstance(keywords, list):
        parts.extend(str(item) for item in keywords)
    elif keywords:
        parts.append(str(keywords))
    return " ".join(parts).casefold()


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


def _website(org: dict[str, Any]) -> str:
    for key in ("website_url", "website", "primary_domain", "domain"):
        value = str(org.get(key) or "").strip()
        if value:
            return value
    return ""


def _person_name(person: dict[str, Any]) -> str:
    full = str(person.get("name") or "").strip()
    if full:
        return full
    return " ".join(
        part
        for part in (
            str(person.get("first_name") or "").strip(),
            str(person.get("last_name") or person.get("last_name_obfuscated") or "").strip(),
        )
        if part
    )


def _person_location(person: dict[str, Any], org: dict[str, Any]) -> str:
    return _join(
        person.get("city") or org.get("city"),
        person.get("state") or org.get("state"),
        person.get("country") or org.get("country"),
    )


def sanitize_person(raw: dict[str, Any]) -> ApolloPerson:
    org = raw.get("organization") if isinstance(raw.get("organization"), dict) else {}
    return ApolloPerson(
        company=str(org.get("name") or "").strip(),
        website=_website(org),
        name=_person_name(raw),
        title=str(raw.get("title") or "").strip(),
        location=_person_location(raw, org),
    )


def _payload_text(response: httpx.Response) -> tuple[dict[str, Any], str]:
    text = (response.text or "")[:2000]
    try:
        data = response.json()
    except ValueError:
        return {}, text
    return data if isinstance(data, dict) else {}, text


def _plan_limit(status: int, payload: dict[str, Any], text: str) -> bool:
    details = payload.get("error_details") if isinstance(payload.get("error_details"), dict) else {}
    blob = " ".join(
        str(part or "")
        for part in (
            payload.get("error"),
            payload.get("error_code"),
            payload.get("message"),
            details.get("code"),
            details.get("message"),
            text,
        )
    ).casefold()
    if status not in {401, 402, 403, 422}:
        return False
    markers = (
        "not available on",
        "not available for this plan",
        "current plan",
        "your plan",
        "upgrade your plan",
        "paid plan",
        "free plan",
        "requires a paid",
        "plan does not include",
    )
    return any(marker in blob for marker in markers)


def _retry_after_seconds(response: httpx.Response) -> Optional[float]:
    raw = (response.headers.get("Retry-After") or response.headers.get("retry-after") or "").strip()
    if not raw:
        details = {}
        try:
            body = response.json()
            if isinstance(body, dict):
                details = body.get("error_details") or {}
        except ValueError:
            details = {}
        suggestions = details.get("suggestions") if isinstance(details, dict) else None
        if isinstance(suggestions, list):
            for item in suggestions:
                if isinstance(item, dict) and item.get("retry_after_seconds") is not None:
                    try:
                        return float(item["retry_after_seconds"])
                    except (TypeError, ValueError):
                        continue
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _apollo_text(payload: dict[str, Any]) -> str:
    details = payload.get("error_details") if isinstance(payload.get("error_details"), dict) else {}
    for value in (payload.get("error"), payload.get("message"), details.get("message")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _raise_http_error(response: httpx.Response) -> None:
    payload, text = _payload_text(response)
    status = response.status_code
    apollo = _apollo_text(payload)
    if _plan_limit(status, payload, text):
        ours = "Apollo says people search needs a paid plan"
        raise ApolloError(f"{ours} — {apollo}" if apollo else ours, status_code=status)
    if status == 401:
        raise ApolloError("Apollo did not accept the API key.", status_code=401)
    if status == 429:
        wait = _retry_after_seconds(response)
        if wait is not None:
            raise ApolloError(
                f"Apollo asked us to wait {int(wait)} seconds before searching again.",
                status_code=429,
            )
        raise ApolloError("Apollo is rate-limiting this search. Try again in a moment.", status_code=429)
    if status >= 500:
        raise ApolloError("Apollo had a server error. Try again in a moment.", status_code=status)
    raise ApolloError("Apollo refused this search.", status_code=status)


def _assert_people_search_url(url: str) -> None:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path != "/api/v1/mixed_people/api_search":
        raise ApolloError("Apollo search is not configured correctly.")


def _http_post(url: str, *, headers: dict[str, str], params: list[tuple[str, Any]]) -> httpx.Response:
    _assert_people_search_url(url)
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        return client.post(url, headers=headers, params=params)


def _post(params: list[tuple[str, Any]]) -> httpx.Response:
    key = _api_key()
    if not key:
        raise ApolloConfigError("Apollo key not set")
    headers = {
        "X-Api-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
    }
    attempts = 0
    while True:
        try:
            response = _http_post(PEOPLE_SEARCH_URL, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("apollo people search timed out")
            raise ApolloError("Apollo did not respond in time.") from exc
        except httpx.HTTPError as exc:
            logger.warning("apollo people search network error type=%s", type(exc).__name__)
            raise ApolloError("Could not reach Apollo. Try again.") from exc
        logger.info("apollo people search status=%s", response.status_code)
        if response.status_code == 429 and attempts < MAX_429_RETRIES:
            wait = _retry_after_seconds(response)
            if wait is not None and 0 <= wait <= SHORT_RETRY_AFTER_SECONDS:
                attempts += 1
                logger.info("apollo people search retrying after status=429 seconds=%s", wait)
                continue
        if response.status_code >= 400:
            _raise_http_error(response)
        return response


def search_people(
    rules: ProspectingRules,
    page: int = 1,
    per_page: int = 10,
    *,
    category_name: str | None = None,
) -> ApolloSearchResult:
    """One people-search page. Does not save prospects and does not request emails."""
    category = pick_category(rules, category_name)
    params = build_search_params(rules, category=category, page=page, per_page=per_page)
    response = _post(params)
    payload, _text = _payload_text(response)
    raw_people = payload.get("people")
    if not isinstance(raw_people, list):
        raw_people = payload.get("contacts") if isinstance(payload.get("contacts"), list) else []
    people = [row for row in raw_people if isinstance(row, dict)]
    kept, dropped = apply_exclusions(people, list(rules.exclusions or []))
    total = payload.get("total_entries")
    try:
        total_matches = int(total)
    except (TypeError, ValueError):
        total_matches = len(people)
    logger.info(
        "apollo people search status=%s returned=%s dropped=%s total=%s",
        response.status_code,
        len(people),
        dropped,
        total_matches,
    )
    return ApolloSearchResult(
        total_matches=total_matches,
        dropped_by_exclusions=dropped,
        category_name=category.name if category else "",
        category_keywords=list(category.keywords) if category else [],
        filters=filters_from_params(params, category=category),
        people=[sanitize_person(row) for row in kept],
    )
