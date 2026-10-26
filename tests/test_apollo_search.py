"""Apollo people search for Prospecting rules. HTTP is mocked; nothing is saved."""

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
from acquisition.services import apollo_client
from acquisition.services.apollo_client import (
    PEOPLE_SEARCH_URL,
    ApolloConfigError,
    ApolloError,
    apply_exclusions,
    build_search_params,
    pick_category,
    sanitize_person,
    search_people,
)
from acquisition.services.prospecting_rules_service import default_prospecting_rules

SECRET = "super-secret-apollo-key-xyz"
NINA = "Nina Unique-Testname"


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


def _sign_in(client: TestClient, *, role: str = ROLE_ADMIN, csrf: str = "csrf-apollo-test") -> str:
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
        request=httpx.Request("POST", PEOPLE_SEARCH_URL),
    )


def _people_payload(*people: dict, total: int | None = None) -> dict:
    rows = list(people)
    return {"total_entries": total if total is not None else len(rows), "people": rows}


def _mock_post(monkeypatch, handler):
    calls: list[dict] = []

    def capturing(url, *, headers, params):
        calls.append({"url": url, "headers": headers, "params": list(params)})
        if callable(handler):
            return handler(calls[-1])
        return handler

    monkeypatch.setattr(apollo_client, "_http_post", capturing)
    return calls


def test_request_is_built_from_one_category_of_the_rules():
    rules = default_prospecting_rules()
    first = pick_category(rules)
    grouped: dict[str, list] = {}
    for key, value in build_search_params(rules, category=first, page=1, per_page=10):
        grouped.setdefault(key, []).append(value)
    assert grouped["person_titles[]"] == rules.titles
    assert grouped["person_locations[]"] == ["United States"]
    assert grouped["organization_locations[]"] == ["United States"]
    assert grouped["organization_num_employees_ranges[]"] == ["25,250"]
    assert grouped["q_keywords"] == ["fractional CXO fractional chief"]
    assert "revenue_range[min]" not in grouped
    assert grouped["per_page"] == [10]
    agencies = pick_category(rules, "Agencies")
    agency_params = dict(build_search_params(rules, category=agencies, page=1, per_page=10))
    assert agency_params["q_keywords"] == "agency"
    assert "fractional" not in agency_params["q_keywords"]


def test_hard_revenue_filter_is_sent_only_when_not_soft():
    rules = default_prospecting_rules()
    rules.revenue_is_soft = False
    grouped = {k: v for k, v in build_search_params(rules, category=rules.categories[0], page=1, per_page=10)}
    assert grouped["revenue_range[min]"] == 5_000_000
    assert grouped["revenue_range[max]"] == 50_000_000


def test_exclusions_drop_matching_companies_and_leave_the_rest():
    people = [
        {"first_name": "Ann", "title": "CEO", "organization": {"name": "Lee Restaurants", "industry": "food"}},
        {"first_name": "Bo", "title": "CEO", "organization": {"name": "North Advisory", "keywords": ["consultant"]}},
        {"first_name": "Cy", "title": "Owner", "organization": {"name": "Apex Law Firms", "industry": "legal"}},
    ]
    kept, dropped = apply_exclusions(people, ["restaurants", "law firms"])
    assert dropped == 2
    assert [p["first_name"] for p in kept] == ["Bo"]


def test_sanitize_person_never_keeps_emails(monkeypatch):
    raw = {
        "name": NINA,
        "title": "CEO",
        "email": "nina@secret.example",
        "city": "Austin",
        "state": "Texas",
        "country": "United States",
        "organization": {
            "name": "North Advisory",
            "website_url": "https://north.example",
            "industry": "consulting",
        },
    }
    out = sanitize_person(raw).to_dict()
    assert out == {
        "company": "North Advisory",
        "website": "https://north.example",
        "name": NINA,
        "title": "CEO",
        "location": "Austin, Texas, United States",
    }
    assert "email" not in out
    assert "nina@secret.example" not in json.dumps(out)


def test_search_people_calls_only_people_search_and_strips_emails(monkeypatch, caplog):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    caplog.set_level("INFO", logger="acquisition.services.apollo_client")
    payload = _people_payload(
        {
            "name": NINA,
            "title": "CEO",
            "email": "nina@secret.example",
            "organization": {"name": "North Advisory", "website_url": "https://north.example"},
        },
        {
            "name": "Gus",
            "title": "Owner",
            "organization": {"name": "Gus Restaurants", "industry": "restaurants"},
        },
        total=40,
    )
    calls = _mock_post(monkeypatch, _response(200, payload))
    result = search_people(default_prospecting_rules(), page=1, per_page=10)
    assert [c["url"] for c in calls] == [PEOPLE_SEARCH_URL]
    assert "mixed_people/api_search" in calls[0]["url"]
    assert calls[0]["headers"]["X-Api-Key"] == SECRET
    assert result.total_matches == 40
    assert result.dropped_by_exclusions == 1
    assert result.category_name == "Fractional CXO businesses"
    assert result.category_keywords == ["fractional CXO", "fractional chief"]
    assert [p.name for p in result.people] == [NINA]
    dumped = json.dumps(result.people[0].to_dict())
    assert "nina@secret.example" not in dumped
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in logs
    assert NINA not in logs
    assert "nina@secret.example" not in logs
    assert "status=200" in logs
    assert "dropped=1" in logs


def test_missing_key_does_not_call_http(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    calls = _mock_post(monkeypatch, lambda _call: pytest.fail("HTTP must not run without a key"))
    with pytest.raises(ApolloConfigError, match="Apollo key not set"):
        search_people(default_prospecting_rules())
    assert calls == []


def test_401_is_plain_words(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    _mock_post(monkeypatch, _response(401, {"error": "Invalid API key"}))
    with pytest.raises(ApolloError, match="Apollo did not accept the API key") as exc:
        search_people(default_prospecting_rules())
    assert exc.value.status_code == 401


def test_429_respects_retry_after_without_looping(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    _mock_post(monkeypatch, _response(429, {"error": "rate"}, headers={"Retry-After": "30"}))
    with pytest.raises(ApolloError, match="wait 30 seconds") as exc:
        search_people(default_prospecting_rules())
    assert exc.value.status_code == 429


def test_429_retries_once_when_retry_after_is_short(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    replies = [
        _response(429, {"error": "rate"}, headers={"Retry-After": "1"}),
        _response(200, _people_payload(total=0)),
    ]
    calls = _mock_post(monkeypatch, lambda _call: replies.pop(0))
    result = search_people(default_prospecting_rules())
    assert len(calls) == 2
    assert result.total_matches == 0


def test_network_failure_is_plain_words(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)

    def boom(url, *, headers, params):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(apollo_client, "_http_post", boom)
    with pytest.raises(ApolloError, match="Could not reach Apollo"):
        search_people(default_prospecting_rules())


def test_timeout_is_plain_words(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)

    def boom(url, *, headers, params):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(apollo_client, "_http_post", boom)
    with pytest.raises(ApolloError, match="did not respond in time"):
        search_people(default_prospecting_rules())


def test_plan_limit_uses_the_paid_plan_sentence(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    _mock_post(
        monkeypatch,
        _response(403, {"error": "This endpoint is not available on your current plan."}),
    )
    with pytest.raises(ApolloError, match="Apollo says people search needs a paid plan") as exc:
        search_people(default_prospecting_rules())
    assert "This endpoint is not available on your current plan." in exc.value.message


def test_no_credit_urls_in_source_and_only_people_search_is_called(monkeypatch):
    sources = [
        Path("acquisition/services/apollo_client.py"),
        Path("acquisition/api/ui_prospecting.py"),
        Path("acquisition/api/ui_outbound.py"),
        Path("acquisition/templates/settings/ai.html"),
    ]
    forbidden = (
        "mixed_companies",
        "organizations/search",
        "people/match",
        "people/bulk_match",
        "people/enrich",
        "bulk_match",
    )
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden:
            assert fragment not in text, f"{fragment} in {path}"
        if path.name == "apollo_client.py":
            assert "mixed_people/api_search" in text
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    calls = _mock_post(monkeypatch, _response(200, _people_payload(total=0)))
    search_people(default_prospecting_rules(), category_name="Agencies")
    assert [c["url"] for c in calls] == [PEOPLE_SEARCH_URL]
    q = dict(calls[0]["params"])["q_keywords"]
    assert q == "agency"


def test_admin_test_search_endpoint_and_auth(acq_client, monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    payload = _people_payload(
        {
            "name": NINA,
            "title": "CEO",
            "email": "nina@secret.example",
            "organization": {"name": "North Advisory", "website_url": "https://north.example"},
        },
        total=9,
    )
    calls = _mock_post(monkeypatch, _response(200, payload))
    acq_client.cookies.clear()
    denied = acq_client.post("/settings/ai/prospecting-rules/test-search", json={}, follow_redirects=False)
    assert denied.status_code in {303, 401, 403}
    acq_client.cookies.clear()
    _sign_in(acq_client, role=ROLE_ACQUISITION_OWNER)
    owner = acq_client.post(
        "/settings/ai/prospecting-rules/test-search",
        json={},
        headers={"X-CSRF-Token": "csrf-apollo-test"},
        follow_redirects=False,
    )
    assert owner.status_code == 303
    acq_client.cookies.clear()
    csrf = _sign_in(acq_client)
    ok = acq_client.post(
        "/settings/ai/prospecting-rules/test-search",
        json={"category": "Fractional CXO businesses"},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    dumped = json.dumps(body)
    assert SECRET not in dumped
    assert "nina@secret.example" not in dumped
    assert "email" not in dumped
    assert body["total_matches"] == 9
    assert body["dropped_by_exclusions"] == 0
    assert body["category"]["name"] == "Fractional CXO businesses"
    assert body["results"][0]["name"] == NINA
    assert body["results"][0]["company"] == "North Advisory"
    assert [c["url"] for c in calls] == [PEOPLE_SEARCH_URL]


def test_test_search_without_key_returns_plain_message(acq_client, monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    acq_client.cookies.clear()
    csrf = _sign_in(acq_client)
    r = acq_client.post(
        "/settings/ai/prospecting-rules/test-search",
        json={},
        headers={"X-CSRF-Token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert r.json()["error"] == "Apollo key not set"
    assert SECRET not in r.text


def test_settings_page_shows_key_status(acq_client, monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    acq_client.cookies.clear()
    _sign_in(acq_client)
    page = acq_client.get("/settings/ai", follow_redirects=False)
    assert page.status_code == 200
    assert "Apollo key not set" in page.text
    assert "pr-test-category" in page.text
    monkeypatch.setenv("APOLLO_API_KEY", SECRET)
    page2 = acq_client.get("/settings/ai", follow_redirects=False)
    assert page2.status_code == 200
    assert "Test search" in page2.text
    assert SECRET not in page2.text


def test_test_search_button_url_matches_a_route(acq_client):
    acq_client.cookies.clear()
    _sign_in(acq_client)
    page = acq_client.get("/settings/ai", follow_redirects=False)
    assert page.status_code == 200
    match = re.search(r'send\("POST",\s*"([^"]+test-search[^"]*)"', page.text)
    assert match, "Test search must POST through send() like the other prospecting buttons"
    path = match.group(1)
    assert not path.startswith("/acquire")
    from acquisition.main import app

    routes = [
        getattr(route, "path", None)
        for route in app.routes
        if "POST" in (getattr(route, "methods", None) or [])
    ]
    assert path in routes, f"{path} is not a POST route"
