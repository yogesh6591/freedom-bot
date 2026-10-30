from datetime import datetime, timedelta, timezone

import pytest

from acquisition.services import outreach_service as o


def test_merge_fills_blanks():
    out = o.merge("Hi {{firstName}}, {{ companyName }} {{assessment_url}}",
                  {"firstName": "Ana", "companyName": "Acme", "assessment_url": "http://x/a/t"})
    assert out == "Hi Ana, Acme http://x/a/t"


def test_merge_missing_value_raises():
    # companyName and lastName are optional: a blank value renders empty, not an error.
    assert o.merge("Hi {{firstName}} {{companyName}}", {"firstName": "Ana", "companyName": " "}) == "Hi Ana "
    assert o.merge("{{lastName}}", {"lastName": " "}) == ""
    with pytest.raises(o.OutreachError):
        o.merge("Hi {{firstName}}", {"firstName": " "})


def test_merge_unknown_blank_raises():
    with pytest.raises(o.OutreachError):
        o.merge("{{nope}}", {"firstName": "A"})


def test_split_name():
    assert o.split_name("Ana Maria Lopez") == ("Ana", "Maria Lopez")
    assert o.split_name("") == ("", "")


def test_legacy_footer_is_stripped_from_the_body():
    optout = "Not interested? Reply 'unsubscribe' and I won't email you again."
    address = "Cepoch, 123 Test Street, Chicago, IL 60601 (TEST ONLY)"
    body = "Hi Nina, can we talk?"
    assert o.without_legacy_footer(body + f"\n\n--\n{optout}\n{address}", optout, address) == body
    assert o.without_legacy_footer(body, optout, address) == body
    assert o.without_legacy_footer(body + "\n\n--\nThanks,\nJeanne", optout, address) == body + "\n\n--\nThanks,\nJeanne"


def test_enqueue_does_not_append_unsubscribe_footer():
    from uuid import uuid4

    from sqlalchemy import select

    from acquisition.db.session import SessionLocal
    from acquisition.models.outreach import SentEmail
    from acquisition.models.outreach_settings import OutreachSettings
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    try:
        settings = db.get(OutreachSettings, 1)
        if settings is None:
            settings = OutreachSettings(id=1)
            db.add(settings)
        settings.business_address = "Cepoch, 123 Test Street, Chicago, IL 60601 (TEST ONLY)"
        settings.optout_line = "Not interested? Reply 'unsubscribe' and I won't email you again."
        prospect = Prospect(
            company="Acme",
            executive_name="Nina Vale",
            email=f"nina.vale.{uuid4().hex[:8]}@example.com",
            approval_status=ApprovalStatus.APPROVED,
        )
        db.add(prospect)
        db.flush()
        rows = o.enqueue(
            db,
            prospect_ids=[prospect.id],
            subject_template="Hello",
            body_template="Hi {{firstName}}, can we talk?",
        )
        assert len(rows) == 1
        assert rows[0].body == "Hi Nina, can we talk?"
        assert "unsubscribe" not in rows[0].body.casefold()
        assert "123 Test Street" not in rows[0].body
        assert "\n--\n" not in rows[0].body
        stored = db.scalar(select(SentEmail).where(SentEmail.id == rows[0].id))
        assert stored is not None and stored.body == "Hi Nina, can we talk?"
        db.rollback()
    finally:
        db.close()


def test_pacing():
    now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
    assert o.can_send_now(sent_today=0, ceiling=400, last_sent_at=None, now=now, gap_seconds=25) == (True, "")
    assert o.can_send_now(sent_today=400, ceiling=400, last_sent_at=None, now=now, gap_seconds=25) == (False, "daily_ceiling")
    assert o.can_send_now(sent_today=1, ceiling=400, last_sent_at=now - timedelta(seconds=10), now=now, gap_seconds=25) == (False, "gap")
    assert o.can_send_now(sent_today=1, ceiling=400, last_sent_at=now - timedelta(seconds=30), now=now, gap_seconds=25)[0]


def test_local_day_start_chicago():
    now = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)  # 22:00 Oct 27 in Chicago (CDT)
    assert o.local_day_start(now, "America/Chicago") == datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc)


def test_sending_card_has_no_address_or_optout_fields():
    from types import SimpleNamespace

    from acquisition.templating import create_templates

    html = create_templates().env.get_template("integrations/index.html").render(
        request=SimpleNamespace(session={}, url=SimpleNamespace(path="/integrations")),
        errors=[],
        flash=None,
        email={"ready": True, "worker_on": True, "sender_gmail": "ops@x.com", "reason": "", "gap_seconds": 25, "daily_ceiling": 400},
        openai={"configured": False, "model": ""},
        stripe={"configured": False, "enabled": False, "currency": "usd", "key_hint": "", "webhook_secret_set": False},
        calcom=SimpleNamespace(configured=False, account=None),
        calcom_account=None,
        calcom_error=None,
    )
    assert "Business address" not in html
    assert "Opt-out line" not in html
    assert "shown in every email footer" not in html
    assert 'name="business_address"' not in html
    assert 'name="optout_line"' not in html
    assert "Sending is on" in html
    assert 'name="worker_enabled"' in html
