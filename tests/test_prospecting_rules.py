"""Prospecting rules settings: defaults, save, validation, history, auth, audit."""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import text

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
from acquisition.models.prospect import IcpSettings
from acquisition.models.prospecting import ProspectingRulesRow, ProspectingRulesVersion
from acquisition.services.prospecting_rules_service import (
    STALE_MESSAGE,
    ProspectingRulesError,
    ProspectingRulesService,
    ProspectingStaleError,
    default_prospecting_rules,
    get_prospecting_rules,
    parse_and_validate,
)
from bizos.audit import events as audit
from bizos.tenancy.registry import workspace_connection


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


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


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


def _sign_in(client: TestClient, *, role: str = ROLE_ADMIN, csrf: str = "csrf-prospecting-test") -> str:
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


def _save(db, raw=None, *, version=0, actor="tester", action="save", ctx=None):
    payload = default_prospecting_rules().to_payload() if raw is None else raw
    return ProspectingRulesService(db).save(payload, expected_version=version, actor=actor, action=action, ctx=ctx)


def test_defaults_match_jeanne_targeting():
    rules = default_prospecting_rules()
    assert rules.version == 0
    assert rules.discovery_enabled is False
    assert rules.outreach_enabled is False
    assert rules.country == "United States"
    assert [c.name for c in rules.categories] == [
        "Fractional CXO businesses",
        "Consultants and advisory firms",
        "Agencies",
        "Psychology and behavioral-health group practices",
    ]
    assert rules.titles == ["Founder", "Owner", "CEO", "President", "Managing Partner"]
    assert rules.employees_min == 25
    assert rules.employees_max == 250
    assert rules.revenue_min == 5_000_000
    assert rules.revenue_max == 50_000_000
    assert rules.revenue_sweet_min == 7_000_000
    assert rules.revenue_sweet_max == 30_000_000
    assert rules.revenue_is_soft is True
    assert rules.exclusions == [
        "generic small business",
        "trades",
        "home services",
        "contractors and construction",
        "restaurants",
        "retail",
        "law firms",
        "accounting firms",
    ]
    assert rules.discovery_daily_target == 5
    assert rules.outreach_daily_cap == 5
    assert rules.require_verified_email is True
    assert rules.pause_if_bounce_rate_percent == 5.0


def test_unsaved_current_is_defaults(db):
    current = ProspectingRulesService(db).current()
    assert current.to_payload() == default_prospecting_rules().to_payload()
    assert current.version == 0


def test_save_and_load_round_trip(db):
    payload = default_prospecting_rules().to_payload()
    payload["discovery_enabled"] = True
    payload["outreach_daily_cap"] = 8
    payload["titles"] = ["Founder", "founder", "  CEO  ", ""]
    saved = _save(db, payload)
    assert saved.version == 1
    assert saved.discovery_enabled is True
    assert saved.outreach_daily_cap == 8
    assert saved.titles == ["Founder", "CEO"]
    loaded = ProspectingRulesService(db).current()
    assert loaded.to_dict() == saved.to_dict()


def test_get_prospecting_rules_returns_saved_config(db):
    payload = default_prospecting_rules().to_payload()
    payload["country"] = "United States"
    payload["employees_min"] = 40
    _save(db, payload)
    loaded = get_prospecting_rules()
    assert loaded.employees_min == 40
    assert loaded.version == 1


def test_get_prospecting_rules_falls_back_when_db_fails(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("acquisition.db.session.SessionLocal", boom)
    rules = get_prospecting_rules()
    assert rules.version == 0
    assert rules.to_payload() == default_prospecting_rules().to_payload()


def test_validation_rejects_inverted_ranges_and_empty_names():
    payload = default_prospecting_rules().to_payload()
    payload["employees_min"] = 400
    payload["employees_max"] = 10
    payload["revenue_min"] = 9
    payload["revenue_max"] = 1
    payload["categories"] = [{"name": "  ", "keywords": [], "priority": 1}]
    with pytest.raises(ProspectingRulesError) as err:
        parse_and_validate(payload)
    messages = " ".join(err.value.messages)
    assert "employees" in messages.lower()
    assert "revenue" in messages.lower()
    assert "name" in messages.lower()


def test_validation_requires_category_and_title_when_discovery_on():
    payload = default_prospecting_rules().to_payload()
    payload["discovery_enabled"] = True
    payload["categories"] = []
    payload["titles"] = []
    with pytest.raises(ProspectingRulesError) as err:
        parse_and_validate(payload)
    text_blob = " ".join(err.value.messages).lower()
    assert "category" in text_blob
    assert "title" in text_blob


def test_validation_rejects_negative_numbers():
    payload = default_prospecting_rules().to_payload()
    payload["outreach_daily_cap"] = -1
    payload["pause_if_bounce_rate_percent"] = 140
    with pytest.raises(ProspectingRulesError) as err:
        parse_and_validate(payload)
    blob = " ".join(err.value.messages).lower()
    assert "0 or more" in blob
    assert "100 or less" in blob


def test_stale_save_is_rejected(db):
    first = _save(db)
    assert first.version == 1
    with pytest.raises(ProspectingStaleError) as err:
        _save(db, version=0)
    assert str(err.value) == STALE_MESSAGE
    assert ProspectingRulesService(db).current().version == 1


def test_history_restore_and_reset(db):
    first = _save(db, {**default_prospecting_rules().to_payload(), "outreach_daily_cap": 3})
    second = _save(
        db,
        {**default_prospecting_rules().to_payload(), "outreach_daily_cap": 9},
        version=first.version,
    )
    history = ProspectingRulesService(db).history()
    assert [row["version"] for row in history] == [second.version, first.version]
    restored = ProspectingRulesService(db).restore(first.version, expected_version=second.version, actor="tester")
    assert restored.outreach_daily_cap == 3
    assert restored.version == second.version + 1
    reset = ProspectingRulesService(db).reset(expected_version=restored.version, actor="tester")
    assert reset.to_payload() == default_prospecting_rules().to_payload()
    assert reset.version == restored.version + 1
    actions = [row["action"] for row in ProspectingRulesService(db).history()]
    assert actions[:3] == ["reset", "restore", "save"]


def test_save_does_not_write_icp_settings(db):
    icp = db.get(IcpSettings, 1)
    before = None if icp is None else (
        icp.discovery_enabled,
        icp.autonomous_outreach_enabled,
        list(icp.buyer_titles or []),
        dict(icp.revenue or {}),
        list(icp.priority_categories or []),
        list(icp.excluded_categories or []),
        icp.discovery_cycle_limit,
        icp.activation_batch_limit,
    )
    _save(db, {**default_prospecting_rules().to_payload(), "discovery_enabled": True, "titles": ["CEO"]})
    db.expire_all()
    icp = db.get(IcpSettings, 1)
    after = None if icp is None else (
        icp.discovery_enabled,
        icp.autonomous_outreach_enabled,
        list(icp.buyer_titles or []),
        dict(icp.revenue or {}),
        list(icp.priority_categories or []),
        list(icp.excluded_categories or []),
        icp.discovery_cycle_limit,
        icp.activation_batch_limit,
    )
    assert after == before


def test_save_writes_audit_entry(db, client_a, ctx_factory):
    ctx = ctx_factory(client_a, "admin")
    before = default_prospecting_rules().to_dict()
    saved = _save(
        db,
        {**default_prospecting_rules().to_payload(), "outreach_daily_cap": 4},
        ctx=ctx,
        actor=ctx.user_id,
    )
    rows = audit.search(ctx=ctx, event_types=["CONFIG_CHANGED"], tool="prospecting_rules", limit=5)
    assert rows, "expected a CONFIG_CHANGED audit row for prospecting_rules"
    row = rows[0]
    assert row["tool"] == "prospecting_rules"
    assert row["status"] == "save"
    assert (row.get("request") or {}).get("before", {}).get("outreach_daily_cap") == before["outreach_daily_cap"]
    assert (row.get("result") or {}).get("after", {}).get("outreach_daily_cap") == saved.outreach_daily_cap
    with workspace_connection(ctx) as conn:
        stored = conn.execute(
            text("SELECT tool, status FROM audit_events WHERE tool = 'prospecting_rules' ORDER BY created_at DESC LIMIT 1")
        ).first()
    assert stored.tool == "prospecting_rules"


def test_anonymous_and_owner_cannot_read_rules(acq_client):
    acq_client.cookies.clear()
    denied = acq_client.get("/settings/ai/prospecting-rules", follow_redirects=False)
    assert denied.status_code in {303, 401, 403}
    _sign_in(acq_client, role=ROLE_ACQUISITION_OWNER)
    owner = acq_client.get("/settings/ai/prospecting-rules", follow_redirects=False)
    assert owner.status_code == 303
    assert "/acquisition/database" in (owner.headers.get("location") or "")


def test_admin_can_save_load_history_reset_and_stale_put(acq_client):
    acq_client.cookies.clear()
    csrf = _sign_in(acq_client)
    headers = {"X-CSRF-Token": csrf}
    current = acq_client.get("/settings/ai/prospecting-rules", follow_redirects=False)
    assert current.status_code == 200
    body = current.json()
    assert body["discovery_enabled"] is False
    assert body["version"] == 0
    payload = default_prospecting_rules().to_dict()
    payload["version"] = 0
    payload["outreach_daily_cap"] = 6
    saved = acq_client.put("/settings/ai/prospecting-rules", json=payload, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["outreach_daily_cap"] == 6
    stale = acq_client.put("/settings/ai/prospecting-rules", json=payload, headers=headers)
    assert stale.status_code == 409
    assert stale.json()["error"] == STALE_MESSAGE
    history = acq_client.get("/settings/ai/prospecting-rules/history")
    assert history.status_code == 200
    assert history.json()["versions"][0]["version"] == 1
    restored = acq_client.post(
        "/settings/ai/prospecting-rules/restore",
        json={"version": 1, "restore_version": 1},
        headers=headers,
    )
    assert restored.status_code == 200
    reset = acq_client.post(
        "/settings/ai/prospecting-rules/reset",
        json={"version": restored.json()["version"]},
        headers=headers,
    )
    assert reset.status_code == 200
    assert reset.json()["outreach_daily_cap"] == 5
    page = acq_client.get("/settings/ai")
    assert page.status_code == 200
    html = page.text
    assert ">Chatbot<" in html
    assert ">Prospecting rules<" in html
    assert 'data-pane="prospecting"' in html
    assert "id=\"prospecting-form\"" in html


def test_migration_043_is_reversible_and_unclashed():
    versions = Path("acquisition_alembic/versions")
    clashes = sorted(path.name for path in versions.glob("043_*.py"))
    assert clashes == ["043_prospecting_rules.py"]
    source = (versions / "043_prospecting_rules.py").read_text()
    assert 'revision: str = "043_prospecting_rules"' in source
    assert 'down_revision' in source and "042_campaign_prospect_overrides" in source
    downgrade = source.split("def downgrade")[1]
    assert downgrade.index("prospecting_rules_versions") < downgrade.index('drop_table("prospecting_rules")')
    assert 'drop_table("prospecting_rules_versions")' in downgrade
    assert 'drop_table("prospecting_rules")' in downgrade
