"""Admin can delete an assessment from the list; the prospect stays."""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from acquisition.auth.admin import (
    ROLE_ADMIN,
    SESSION_AT_KEY,
    SESSION_AUTH_KEY,
    SESSION_CSRF_KEY,
    SESSION_ROLE_KEY,
)
from acquisition.config import get_settings
from acquisition.db.session import SessionLocal, engine
from acquisition.models.assessment import AssessmentConversation, AssessmentMessage, AssessmentMessageRole
from acquisition.models.campaign import Campaign, CampaignProspect
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.services.assessment_journey_reset import delete_assessment_link


TEST_DB_NAME = "acquisition_test"


def _session_cookie(data: dict) -> tuple[str, str]:
    settings = get_settings()
    name = settings.admin_session_cookie_name or "freedombot_admin_session"
    signed = TimestampSigner(str(settings.secret_key)).sign(b64encode(json.dumps(data).encode("utf-8")))
    return name, signed.decode("utf-8")


def _sign_in(client: TestClient, *, csrf: str = "csrf-assess-delete") -> str:
    now = datetime.now(timezone.utc)
    name, value = _session_cookie(
        {
            SESSION_AUTH_KEY: True,
            SESSION_AT_KEY: now.isoformat(),
            SESSION_CSRF_KEY: csrf,
            SESSION_ROLE_KEY: ROLE_ADMIN,
            "admin_expires_at": (now + timedelta(hours=12)).isoformat(),
        }
    )
    client.cookies.set(name, value)
    return csrf


def _make_assessment(db):
    prospect = Prospect(
        company="Cepoch",
        executive_name="Ada Admin",
        email=f"ada-{uuid4().hex[:8]}@cepoch.example.com",
        approval_status=ApprovalStatus.APPROVED,
    )
    db.add(prospect)
    db.flush()
    campaign = Campaign(name="test-delete", sender_name="Jeanne")
    db.add(campaign)
    db.flush()
    link = CampaignProspect(
        campaign_id=campaign.id,
        prospect_id=prospect.id,
        assessment_token=uuid4().hex,
    )
    db.add(link)
    db.flush()
    conversation = AssessmentConversation(campaign_prospect_id=link.id)
    db.add(conversation)
    db.flush()
    db.add(
        AssessmentMessage(
            conversation_id=conversation.id,
            role=AssessmentMessageRole.USER,
            content="hello",
        )
    )
    db.commit()
    db.refresh(link)
    db.refresh(prospect)
    return prospect, link


def test_assessments_list_and_detail_have_delete_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    list_html = (root / "acquisition/templates/assessments/list.html").read_text(encoding="utf-8")
    detail_html = (root / "acquisition/templates/assessments/detail.html").read_text(encoding="utf-8")
    assert 'href="/assessments/{{ row.link.id }}">View</a>' in list_html
    assert 'action="/assessments/{{ row.link.id }}/delete"' in list_html
    assert ">Delete</button>" in list_html
    assert 'action="/assessments/{{ link.id }}/delete"' in detail_html
    assert "Delete assessment" in detail_html


def test_delete_assessment_link_removes_conversation_keeps_prospect():
    assert engine.url.database == TEST_DB_NAME
    db = SessionLocal()
    try:
        prospect, link = _make_assessment(db)
        prospect_id = prospect.id
        link_id = link.id
        delete_assessment_link(db, link)
        assert db.get(CampaignProspect, link_id) is None
        assert db.get(Prospect, prospect_id) is not None
        left = db.query(AssessmentConversation).filter_by(campaign_prospect_id=link_id).all()
        assert left == []
    finally:
        db.close()


def test_delete_assessment_post_redirects(monkeypatch):
    assert engine.url.database == TEST_DB_NAME
    from acquisition.main import app

    db = SessionLocal()
    try:
        prospect, link = _make_assessment(db)
        link_id = link.id
        prospect_id = prospect.id
    finally:
        db.close()

    monkeypatch.setattr(
        "acquisition.services.assessment_journey_reset._cancel_cal_bookings_for_link",
        lambda _link: [],
    )
    with TestClient(app) as client:
        csrf = _sign_in(client)
        res = client.post(
            f"/assessments/{link_id}/delete",
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
    assert res.status_code == 303
    assert res.headers["location"].endswith("/assessments?flash=deleted")
    db = SessionLocal()
    try:
        assert db.get(CampaignProspect, link_id) is None
        assert db.get(Prospect, prospect_id) is not None
    finally:
        db.close()
