"""Scratch checks for the Composio-backed Cal.com client (mocked execute)."""
from unittest.mock import patch

import pytest

from acquisition.integrations.calcom import owner
from acquisition.integrations.calcom.client import CalcomClient, CalcomError
from bizos.connectors.base import ConnectorError


class Ctx:  # stands in for a TenantContext
    client_id = "c"
    user_id = "u"


class Fake:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, ctx, toolkit, action, arguments):
        self.calls.append((toolkit, action, arguments))
        reply = self.replies[action]
        if isinstance(reply, Exception):
            raise reply
        return reply


def run(replies):
    fake = Fake(replies)
    cm = patch("bizos.connectors.composio.execute", fake)
    return fake, cm


def client():
    return CalcomClient(ctx=Ctx())


def test_not_connected(monkeypatch):
    monkeypatch.setattr(owner, "owner_user_id", lambda: None)
    monkeypatch.setattr(owner, "owner_context", lambda: None)
    c = CalcomClient()
    assert not c.configured
    with pytest.raises(CalcomError) as e:
        c.get_me()
    assert e.value.http_status == 503


def test_me_and_updates():
    me = {"data": {"id": 1, "username": "bob", "email": "b@x", "timeZone": "UTC", "timeFormat": 24}, "status": "success"}
    fake, cm = run({"CAL_RETRIEVE_MY_INFORMATION": me, "CAL_UPDATE_USER_PROFILE_DETAILS": {"data": {**me["data"], "timeZone": "America/Chicago"}}})
    with cm:
        c = client()
        assert c.get_me()["username"] == "bob"
        assert c.ensure_timezone("America/Chicago")["timeZone"] == "America/Chicago"
        c.ensure_time_format(12)
        c.ensure_timezone("UTC")  # already right: no update
    updates = [a for _, act, a in fake.calls if act == "CAL_UPDATE_USER_PROFILE_DETAILS"]
    assert updates == [{"timeZone": "America/Chicago"}, {"timeFormat": 12}]


def test_event_types():
    groups = {"status": "success", "data": {"eventTypeGroups": [
        {"teamId": 5, "bookerUrl": "https://cal.com", "profile": {"slug": "team"}, "eventTypes": [{"id": 9, "slug": "t", "title": "T", "length": 10}]},
        {"teamId": None, "bookerUrl": "https://cal.com", "profile": {"slug": "bob"}, "eventTypes": [{"id": 1, "slug": "intro", "title": "Intro", "length": 30, "hidden": False}]},
    ]}}
    fake, cm = run({"CAL_LIST_EVENT_TYPES": groups, "CAL_EDIT_EVENT_TYPE_BY_ID": {"data": {"event_type": {"id": 1, "disableGuests": True, "slug": "intro", "title": "Intro"}}}})
    with cm:
        c = client()
        out = c.list_event_types(username="bob")
        assert out == [{"id": 1, "title": "Intro", "slug": "intro", "lengthInMinutes": 30, "bookingUrl": "https://cal.com/bob/intro", "description": None, "hidden": False}]
        assert c.ensure_event_booker_defaults("1")["disableGuests"] is True
    assert fake.calls[0][2] == {"username": "bob"}
    assert fake.calls[1][2] == {"id": 1, "disableGuests": True}
    with cm:
        with pytest.raises(CalcomError):
            c.ensure_event_booker_defaults("abc")


def test_webhooks():
    fake, cm = run({
        "CAL_RETRIEVE_WEBHOOKS_LIST": {"data": [{"id": 7, "subscriberUrl": "https://a/api/webhooks/calcom"}], "status": "success"},
        "CAL_POST_WEBHOOK_EVENT": {"data": {"id": "uuid-1", "active": True}, "status": "success"},
        "CAL_DELETE_WEBHOOK_BY_ID": {"data": {"id": 7}},
    })
    with cm:
        c = client()
        assert c.list_webhooks()[0]["id"] == 7
        assert c.create_webhook(subscriber_url=" https://b ", triggers=["BOOKING_CREATED"], secret="s")["id"] == "uuid-1"
        c.delete_webhook(7)
    assert fake.calls[1][2] == {"subscriberUrl": "https://b", "triggers": ["BOOKING_CREATED"], "active": True, "secret": "s"}
    assert fake.calls[2][2] == {"webhookId": "7"}


def test_bookings():
    booking = {"id": 3, "uid": "new", "status": "accepted", "start": "2026-11-01T15:00:00Z", "end": "2026-11-01T15:30:00Z", "meetingUrl": "https://m"}
    fake, cm = run({
        "CAL_RESCHEDULE_BOOKING_BY_UID": {"data": booking, "status": "success"},
        "CAL_RETRIEVE_BOOKING_DETAILS_BY_UID": {"data": booking},
        "CAL_FETCH_ALL_BOOKINGS": {"data": {"bookings": [booking, "junk"], "totalCount": 1}, "pagination": {}},
        "CAL_CANCEL_BOOKING_VIA_UID": {"data": {}},
    })
    with cm:
        c = client()
        got = c.reschedule_booking("old", start_utc="2026-11-01T15:00:00Z", reason="r")
        assert got["uid"] == "new" and got["id"] == "3" and got["meetingUrl"] == "https://m"
        assert c.get_booking("new")["uid"] == "new"
        assert len(c.list_bookings(take=5, status=None)) == 1
        c.list_bookings(take=5, status="accepted")
        c.cancel_booking("new", reason="x")
    assert fake.calls[0][2] == {"bookingUid": "old", "start": "2026-11-01T15:00:00Z", "reschedulingReason": "r"}
    assert fake.calls[2][2]["status"] == ["upcoming", "recurring", "past", "cancelled", "unconfirmed"]
    assert fake.calls[3][2]["status"] == ["upcoming"]
    assert fake.calls[4][2] == {"bookingUid": "new", "cancellationReason": "x"}


def test_cancel_gone_is_ok_but_other_errors_raise():
    fake, cm = run({"CAL_CANCEL_BOOKING_VIA_UID": ConnectorError("Cal.com request failed: Booking not found")})
    with cm:
        client().cancel_booking("x")
    fake, cm = run({"CAL_CANCEL_BOOKING_VIA_UID": ConnectorError("Cal.com request failed: boom")})
    with cm:
        with pytest.raises(CalcomError):
            client().cancel_booking("x")


def test_owner_prefers_sender(monkeypatch):
    monkeypatch.setattr(owner, "_cache", None)
    opts = [{"user_id": "a"}, {"user_id": "b"}]
    with patch("app.outreach.gmail_users.list_connected", lambda toolkit="gmail": opts) as _:
        with patch("acquisition.services.outreach_settings.effective") as eff:
            eff.return_value.sender_user_id = "b"
            assert owner._pick_user_id() == "b"
            eff.return_value.sender_user_id = ""
            assert owner._pick_user_id() == "a"
    with patch("app.outreach.gmail_users.list_connected", lambda toolkit="gmail": []):
        assert owner._pick_user_id() is None


def test_reconcile_for_token_matches_attendee_email(monkeypatch):
    from acquisition.services import calcom_webhook_service as mod

    class Link:
        prospect = type("P", (), {"email": "raj@x.com"})()

    class FakeDb:
        def scalar(self, _q):
            return Link()

    class FakeClient:
        configured = True

        def list_bookings(self, *, take=25, status=None):
            return [
                {
                    "uid": "u1",
                    "status": "upcoming",
                    "start": "2026-10-30T15:00:00Z",
                    "end": "2026-10-30T15:30:00Z",
                    "attendees": [{"email": "raj@x.com"}],
                }
            ]

    seen = {}

    class FakeBooking:
        def __init__(self, db):
            pass

        def handle_calcom_webhook(self, payload, *, event_id):
            seen["token"] = payload["payload"]["metadata"]["fb_token"]
            seen["uid"] = payload["payload"]["uid"]
            seen["event_id"] = event_id
            return {"status": "confirmed"}

    monkeypatch.setattr(mod, "BookingService", FakeBooking)
    out = mod.CalcomWebhookService(FakeDb(), client=FakeClient()).reconcile_for_token("tok_abc")
    assert out["applied"][0]["result"] == "confirmed"
    assert seen["token"] == "tok_abc"
    assert seen["uid"] == "u1"


def test_webhook_service_and_page(monkeypatch):
    from acquisition.db.session import SessionLocal
    from acquisition.services.calcom_webhook_service import CalcomWebhookService

    monkeypatch.setenv("PUBLIC_WEBHOOK_BASE_URL", "https://pub.example")
    from acquisition.config import get_settings
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None
    fake, cm = run({
        "CAL_RETRIEVE_WEBHOOKS_LIST": {"data": [{"id": 4, "subscriberUrl": "https://old/api/webhooks/calcom"}]},
        "CAL_POST_WEBHOOK_EVENT": {"data": {"id": "u"}},
        "CAL_DELETE_WEBHOOK_BY_ID": {"data": {}},
        "CAL_FETCH_ALL_BOOKINGS": {"data": []},
    })
    db = SessionLocal()
    try:
        with cm:
            svc = CalcomWebhookService(db, client=client())
            out = svc.register_webhook()
            assert out["status"] == "registered"
            assert svc.reconcile_recent_bookings()["checked"] == 0
    finally:
        db.close()
    acts = [a for _, a, _ in fake.calls]
    assert "CAL_DELETE_WEBHOOK_BY_ID" in acts and "CAL_POST_WEBHOOK_EVENT" in acts
