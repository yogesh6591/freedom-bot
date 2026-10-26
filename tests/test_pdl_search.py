"""PDL person search for Prospecting rules. HTTP is mocked; nothing is saved."""

from __future__ import annotations

import json
import re
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from acquisition.auth.admin import (
    ROLE_ACQUISITION_OWNER,
    ROLE_ADMIN,
    SESSION_AT_KEY,
    SESSION_AUTH_KEY,
    SESSION_CSRF_KEY,
    SESSION_ROLE_KEY,
)
from acquisition.config import get_settings
from acquisition.db.session import SessionLocal
from acquisition.models.prospecting import ProspectingRulesRow, ProspectingRulesVersion
from acquisition.services import pdl_client
from acquisition.services.apollo_client import pick_category
from acquisition.services.pdl_client import (
    PERSON_SEARCH_URL,
    SEARCH_SIZE,
    PDLConfigError,
    PDLError,
    apply_exclusions,
    build_search_body,
    job_title_levels_for,
    overlapping_company_sizes,
    sanitize_person,
    search_people,
)
from acquisition.services.prospecting_rules_service import default_prospecting_rules

SECRET = "super-secret-pdl-key-xyz"
NINA = "Nina Unique-Testname"
# https://docs.peopledatalabs.com/docs/input-parameters-person-search-api
_ALLOWED_CLAUSES = frozenset(
    {"term", "terms", "exists", "bool", "match", "range", "match_phrase", "wildcard", "prefix", "match_all"}
)
_ALLOWED_BOOL_KEYS = frozenset({"must", "should", "must_not", "filter"})
_QUERY_FIELDS = frozenset(
    {
        "job_title",
        "job_title_levels",
        "location_country",
        "job_company_location_country",
        "job_company_size",
        "job_company_industry",
        "job_company_name",
        "job_company_inferred_revenue",
        "industry",
        "field",
    }
)


@pytest.fixture(autouse=True)
def _clear_rules():
    db = SessionLocal()
    try:
        db.query(ProspectingRulesVersion).delete()
        db.query(ProspectingRulesRow).delete()
        db.commit()
        yield
        db.query(ProspectingRulesVersion).delete()
        db.query(ProspectingRulesRow).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module")
def acq_client():
    from acquisition.main import app

    with TestClient(app) as client:
        yield client


def _session_cookie(data: dict) -> tuple[str, str]:
    settings = get_settings()
    name = settings.admin_session_cookie_name or "freedombot_admin_session"
    signed = TimestampSigner(str(settings.secret_key)).sign(b64encode(json.dumps(data).encode("utf-8")))
    return name, signed.decode("utf-8")


def _sign_in(client: TestClient, *, role: str = ROLE_ADMIN, csrf: str = "csrf-pdl-test") -> str:
    now = datetime.now(timezone.utc)
    name, value = _session_cookie(
        {
            SESSION_AUTH_KEY: True,
            SESSION_AT_KEY: now.isoformat(),
            SESSION_CSRF_KEY: csrf,
            SESSION_ROLE_KEY: role,
            "admin_expires_at": (now + timedelta(hours=12)).isoformat(),
        }
    )
    client.cookies.set(name, value)
    return csrf


def _response(status: int, payload: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers=headers or {},
        request=httpx.Request("POST", PERSON_SEARCH_URL),
    )


def _people_payload(*people: dict, total: int | None = None) -> dict:
    rows = list(people)
    return {"status": 200, "total": total if total is not None else len(rows), "data": rows}


def _mock_post(monkeypatch, handler):
    calls: list[dict] = []

    def capturing(url, *, headers, json):
        calls.append({"url": url, "headers": headers, "json": dict(json)})
        if callable(handler):
            return handler(calls[-1])
        return handler

    monkeypatch.setattr(pdl_client, "_http_post", capturing)
    return calls


def _assert_allowed_query(node):
    if isinstance(node, list):
        for item in node:
            _assert_allowed_query(item)
        return
    if not isinstance(node, dict):
        return
    keys = set(node)
    if keys <= _ALLOWED_BOOL_KEYS:
        _assert_allowed_query(list(node.values()))
        return
    for clause, payload in node.items():
        assert clause in _ALLOWED_CLAUSES, f"disallowed clause {clause}"
        assert clause != "minimum_should_match"
        if clause == "bool":
            extra = set(payload) - _ALLOWED_BOOL_KEYS
            assert not extra, extra
            _assert_allowed_query(payload)
            continue
        if isinstance(payload, dict):
            for field in payload:
                assert field in _QUERY_FIELDS, f"unexpected field {field}"
            continue
        _assert_allowed_query(payload)


def test_request_is_built_from_one_category_of_the_rules():
    rules = default_prospecting_rules()
    first = pick_category(rules)
    body, filters, not_applied = build_search_body(rules, category=first)
    assert body["size"] == SEARCH_SIZE == 5
    assert "from" not in body
    assert "scroll_token" not in body
    assert "minimum_should_match" not in json.dumps(body)
    must = body["query"]["bool"]["must"]
    assert {"term": {"location_country": "united states"}} in must
    assert {"term": {"job_company_location_country": "united states"}} in must
    assert {"terms": {"job_company_size": ["11-50", "51-200", "201-500"]}} in must
    title_bool = next(
        clause
        for clause in must
        if isinstance(clause, dict) and "bool" in clause and any("job_title" in json.dumps(item) for item in clause["bool"].get("should") or [])
    )
    assert set(title_bool["bool"]) == {"should"}
    assert {"match_phrase": {"job_title": "founder"}} in title_bool["bool"]["should"]
    assert {"terms": {"job_title_levels": filters["job_title_levels"]}} in title_bool["bool"]["should"]
    _assert_allowed_query(body["query"])
    assert filters["titles"] == rules.titles
    assert filters["location_country"] == "united states"
    assert filters["job_company_location_country"] == "united states"
    assert filters["job_company_size"] == ["11-50", "51-200", "201-500"]
    assert filters["keywords"] == ["fractional CXO", "fractional chief"]
    assert filters["job_company_inferred_revenue"] == []
    assert any("revenue (soft" in item for item in not_applied)
    assert "cxo" in filters["job_title_levels"]
    assert "owner" in filters["job_title_levels"]
    assert "partner" in filters["job_title_levels"]
    agencies = pick_category(rules, "Agencies")
    agency_body, agency_filters, _skipped = build_search_body(rules, category=agencies)
    assert agency_filters["keywords"] == ["agency"]
    assert "fractional" not in " ".join(agency_filters["keywords"])
    assert "minimum_should_match" not in json.dumps(agency_body)
    _assert_allowed_query(agency_body["query"])


def test_source_and_bodies_never_send_minimum_should_match():
    sources = [
        Path("acquisition/services/pdl_client.py"),
        Path("acquisition/api/ui_prospecting.py"),
        Path("acquisition/templates/settings/ai.html"),
    ]
    for path in sources:
        assert "minimum_should_match" not in path.read_text(encoding="utf-8"), path
    rules = default_prospecting_rules()
    rules.revenue_is_soft = False
    body, _filters, _skipped = build_search_body(rules, category=rules.categories[0])
    assert "minimum_should_match" not in json.dumps(body)
    _assert_allowed_query(body["query"])


def test_hard_revenue_filter_is_sent_only_when_not_soft():
    rules = default_prospecting_rules()
    rules.revenue_is_soft = False
    _body, filters, not_applied = build_search_body(rules, category=rules.categories[0])
    assert "$5M-$50M" not in filters["job_company_inferred_revenue"]
    assert "$10M-$25M" in filters["job_company_inferred_revenue"]
    assert "$25M-$50M" in filters["job_company_inferred_revenue"]
    assert not any(item.startswith("revenue (soft") for item in not_applied)


def test_search_size_is_capped_at_five_and_never_paginates(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    calls = _mock_post(monkeypatch, _response(200, _people_payload(total=0)))
    search_people(default_prospecting_rules())
    assert calls[0]["json"]["size"] == 5
    assert "from" not in calls[0]["json"]
    assert "scroll_token" not in calls[0]["json"]
    dumped = json.dumps(calls[0]["json"])
    assert "minimum_should_match" not in dumped
    assert "person/enrich" not in dumped
    assert "company/search" not in dumped
    _assert_allowed_query(calls[0]["json"]["query"])


def test_exclusions_drop_matching_companies_and_leave_the_rest():
    people = [
        {"full_name": "Ann", "job_title": "CEO", "job_company_name": "Lee Restaurants", "industry": "food"},
        {"full_name": "Bo", "job_title": "CEO", "job_company_name": "North Advisory", "job_company_industry": "consultant"},
        {"full_name": "Cy", "job_title": "Owner", "job_company_name": "Apex Law Firms", "industry": "legal"},
    ]
    kept, dropped = apply_exclusions(people, ["restaurants", "law firms"])
    assert dropped == 2
    assert [p["full_name"] for p in kept] == ["Bo"]


def test_sanitize_person_never_keeps_emails():
    raw = {
        "full_name": NINA,
        "job_title": "CEO",
        "emails": [{"address": "nina@secret.example"}],
        "phone_numbers": ["+15555550100"],
        "location_locality": "Austin",
        "location_region": "Texas",
        "location_country": "United States",
        "job_company_name": "North Advisory",
        "job_company_website": "https://north.example",
        "job_company_industry": "consulting",
    }
    out = sanitize_person(raw).to_dict()
    assert out == {
        "company": "North Advisory",
        "website": "https://north.example",
        "name": NINA,
        "title": "CEO",
        "location": "Austin, Texas, United States",
    }
    dumped = json.dumps(out)
    assert "email" not in dumped
    assert "phone" not in dumped
    assert "nina@secret.example" not in dumped
    assert "+15555550100" not in dumped


def test_search_people_calls_only_person_search_and_strips_emails(monkeypatch, caplog):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    caplog.set_level("INFO", logger="acquisition.services.pdl_client")
    payload = _people_payload(
        {
            "full_name": NINA,
            "job_title": "CEO",
            "emails": [{"address": "nina@secret.example"}],
            "phone_numbers": ["+15555550100"],
            "job_company_name": "North Advisory",
            "job_company_website": "https://north.example",
            "location_name": "Austin, Texas, United States",
        },
        {
            "full_name": "Gus",
            "job_title": "Owner",
            "job_company_name": "Gus Restaurants",
            "industry": "restaurants",
        },
        total=40,
    )
    calls = _mock_post(monkeypatch, _response(200, payload))
    result = search_people(default_prospecting_rules())
    assert [c["url"] for c in calls] == [PERSON_SEARCH_URL]
    assert calls[0]["headers"]["X-Api-Key"] == SECRET
    assert "api_key" not in calls[0]["json"]
    assert result.total_matches == 40
    assert result.dropped_by_exclusions == 1
    assert result.category_name == "Fractional CXO businesses"
    assert result.category_keywords == ["fractional CXO", "fractional chief"]
    assert [p.name for p in result.people] == [NINA]
    dumped = json.dumps(result.people[0].to_dict())
    assert "nina@secret.example" not in dumped
    assert "+15555550100" not in dumped
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in logs
    assert NINA not in logs
    assert "nina@secret.example" not in logs
    assert "+15555550100" not in logs
    assert "status=200" in logs
    assert "dropped=1" in logs


def test_missing_key_does_not_call_http(monkeypatch):
    monkeypatch.delenv("PDL_API_KEY", raising=False)
    calls = _mock_post(monkeypatch, lambda _call: pytest.fail("HTTP must not run without a key"))
    with pytest.raises(PDLConfigError, match="PDL key not set"):
        search_people(default_prospecting_rules())
    assert calls == []


def test_401_includes_pdl_error_text(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    _mock_post(
        monkeypatch,
        _response(401, {"status": 401, "error": {"type": "authentication_error", "message": "Invalid API key"}}),
    )
    with pytest.raises(PDLError, match="PDL did not accept the API key") as exc:
        search_people(default_prospecting_rules())
    assert exc.value.status_code == 401
    assert "Invalid API key" in exc.value.message


def test_402_includes_pdl_error_text(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    _mock_post(
        monkeypatch,
        _response(
            402,
            {
                "status": 402,
                "error": {
                    "type": "payment_required",
                    "message": "You have reached your account maximum (all matches have been used).",
                },
            },
        ),
    )
    with pytest.raises(PDLError) as exc:
        search_people(default_prospecting_rules())
    assert exc.value.status_code == 402
    assert "You have reached your account maximum (all matches have been used)." in exc.value.message


def test_plan_refusal_includes_pdl_error_text(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    _mock_post(
        monkeypatch,
        _response(
            400,
            {
                "status": 400,
                "error": {
                    "type": "invalid_request_error",
                    "message": "Person Search is not included in your free plan.",
                },
            },
        ),
    )
    with pytest.raises(PDLError) as exc:
        search_people(default_prospecting_rules())
    assert "Person Search is not included in your free plan." in exc.value.message


def test_network_failure_is_plain_words(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)

    def boom(url, *, headers, json):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(pdl_client, "_http_post", boom)
    with pytest.raises(PDLError, match="Could not reach PDL"):
        search_people(default_prospecting_rules())


def test_timeout_is_plain_words(monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)

    def boom(url, *, headers, json):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(pdl_client, "_http_post", boom)
    with pytest.raises(PDLError, match="did not respond in time"):
        search_people(default_prospecting_rules())


def test_no_credit_urls_in_source_and_only_person_search_is_called(monkeypatch):
    sources = [
        Path("acquisition/services/pdl_client.py"),
        Path("acquisition/api/ui_prospecting.py"),
        Path("acquisition/api/ui_outbound.py"),
        Path("acquisition/templates/settings/ai.html"),
    ]
    forbidden = (
        "person/enrich",
        "person/identify",
        "person/bulk",
        "company/search",
        "company/enrich",
        "autocomplete",
    )
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden:
            assert fragment not in text, f"{fragment} in {path}"
        if path.name == "pdl_client.py":
            assert "/v5/person/search" in text
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    calls = _mock_post(monkeypatch, _response(200, _people_payload(total=0)))
    search_people(default_prospecting_rules(), category_name="Agencies")
    assert [c["url"] for c in calls] == [PERSON_SEARCH_URL]
    dumped = json.dumps(calls[0]["json"])
    assert "agency" in dumped
    assert "fractional" not in dumped


def test_admin_test_search_endpoint_and_auth(acq_client, monkeypatch):
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    payload = _people_payload(
        {
            "full_name": NINA,
            "job_title": "CEO",
            "emails": [{"address": "nina@secret.example"}],
            "job_company_name": "North Advisory",
            "job_company_website": "https://north.example",
            "location_name": "Austin, Texas, United States",
        },
        total=9,
    )
    calls = _mock_post(monkeypatch, _response(200, payload))
    acq_client.cookies.clear()
    denied = acq_client.post("/settings/ai/prospecting-rules/test-search-pdl", json={}, follow_redirects=False)
    assert denied.status_code in {303, 401, 403}
    acq_client.cookies.clear()
    _sign_in(acq_client, role=ROLE_ACQUISITION_OWNER)
    owner = acq_client.post(
        "/settings/ai/prospecting-rules/test-search-pdl",
        json={},
        headers={"X-CSRF-Token": "csrf-pdl-test"},
        follow_redirects=False,
    )
    assert owner.status_code == 303
    acq_client.cookies.clear()
    csrf = _sign_in(acq_client)
    ok = acq_client.post(
        "/settings/ai/prospecting-rules/test-search-pdl",
        json={"category": "Fractional CXO businesses"},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    dumped = json.dumps(body)
    assert SECRET not in dumped
    assert "nina@secret.example" not in dumped
    assert "email" not in json.dumps(body["results"])
    assert body["total_matches"] == 9
    assert body["dropped_by_exclusions"] == 0
    assert body["category"]["name"] == "Fractional CXO businesses"
    assert body["results"][0]["name"] == NINA
    assert body["results"][0]["company"] == "North Advisory"
    assert body["filters"]["size"] == 5
    assert "not_applied" in body
    assert [c["url"] for c in calls] == [PERSON_SEARCH_URL]


def test_test_search_without_key_returns_plain_message(acq_client, monkeypatch):
    monkeypatch.delenv("PDL_API_KEY", raising=False)
    acq_client.cookies.clear()
    csrf = _sign_in(acq_client)
    r = acq_client.post(
        "/settings/ai/prospecting-rules/test-search-pdl",
        json={},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert r.json()["error"] == "PDL key not set"
    assert SECRET not in r.text


def test_settings_page_shows_key_status(acq_client, monkeypatch):
    monkeypatch.delenv("PDL_API_KEY", raising=False)
    acq_client.cookies.clear()
    _sign_in(acq_client)
    page = acq_client.get("/settings/ai", follow_redirects=False)
    assert page.status_code == 200
    assert "PDL key not set" in page.text
    assert "Uses up to 5 of 100 free credits." in page.text
    monkeypatch.setenv("PDL_API_KEY", SECRET)
    page2 = acq_client.get("/settings/ai", follow_redirects=False)
    assert page2.status_code == 200
    assert "PDL test search" in page2.text
    assert SECRET not in page2.text


def test_test_search_button_url_matches_a_route(acq_client):
    acq_client.cookies.clear()
    _sign_in(acq_client)
    page = acq_client.get("/settings/ai", follow_redirects=False)
    assert page.status_code == 200
    match = re.search(r'send\("POST",\s*"([^"]+test-search-pdl[^"]*)"', page.text)
    assert match, "PDL Test search must POST through send() like the other prospecting buttons"
    path = match.group(1)
    assert not path.startswith("/acquire")
    from acquisition.main import app

    routes = [
        getattr(route, "path", None)
        for route in app.routes
        if "POST" in (getattr(route, "methods", None) or [])
    ]
    assert path in routes, f"{path} is not a POST route"


def test_employee_size_overlap_and_title_levels():
    assert overlapping_company_sizes(25, 250) == ["11-50", "51-200", "201-500"]
    assert job_title_levels_for(["Founder", "Owner", "CEO", "President", "Managing Partner"]) == [
        "owner",
        "cxo",
        "partner",
    ]
