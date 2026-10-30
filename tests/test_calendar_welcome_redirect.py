"""After a Cal.com booking, the prospect should land on the welcome page."""

from pathlib import Path
from types import SimpleNamespace

from acquisition.services.acquisition_journey_service import AcquisitionJourneyError
from acquisition.services.booking_service import build_calendar_embed_url, calendar_embed_parts
from acquisition.services.calcom_webhook_service import CalcomWebhookService


ROOT = Path(__file__).resolve().parents[1]


def test_calendar_and_booked_pages_forward_booking_uid():
    calendar = (ROOT / "acquisition/templates/assessment/calendar.html").read_text(
        encoding="utf-8"
    )
    booked = (ROOT / "acquisition/templates/assessment/booked.html").read_text(
        encoding="utf-8"
    )
    assert "originator" in calendar
    assert "bookingUid" in calendar
    assert "uid=" in calendar
    assert "bookingsuccessful" in calendar.lower()
    assert "data-fb-booked-url" in calendar
    assert "uid=" in booked
    assert "bookingUid" in booked
    assert "app.cal.com/embed/embed.js" in calendar
    assert "bookingSuccessfulV2" in calendar or 'action: "*"' in calendar
    assert "fb-cal-inline" in calendar
    assert 'action: "*"' in calendar
    assert "uidFromEmbedFrame" in calendar
    assert "fullType" in calendar
    assert "watchEmbedFrame" in calendar
    assert "window.top" in booked
    assert "hideBranding" in calendar
    assert '"successRedirectUrl=" + encodeURIComponent(successUrl)' in calendar
    assert "videocallurl" in calendar.lower()


def test_calendar_embed_uses_public_success_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example/acquire")
    from acquisition.config import get_settings

    get_settings.cache_clear()
    try:
        url = build_calendar_embed_url(
            booking_url="https://cal.com/jeanne/intro",
            token="tok_abc",
        )
    finally:
        get_settings.cache_clear()
    assert "successRedirectUrl=" in url
    assert "https%3A%2F%2Fapp.example%2Facquire%2Fa%2Ftok_abc%2Fbooked" in url
    parts = calendar_embed_parts("https://cal.com/jeanne/intro")
    assert parts == {"cal_link": "jeanne/intro", "cal_origin": "https://cal.com"}


def test_reconcile_for_token_uses_uid_when_provided(monkeypatch):
    from acquisition.services import calcom_webhook_service as mod

    class Link:
        prospect = type("P", (), {"email": "raj@x.com"})()

    class FakeDb:
        def scalar(self, _q):
            return Link()

    seen = {}

    class FakeClient:
        configured = True

        def get_booking_record(self, uid):
            seen["fetched"] = uid
            return {
                "uid": uid,
                "status": "accepted",
                "start": "2026-10-30T15:00:00Z",
                "end": "2026-10-30T15:30:00Z",
                "attendees": [{"email": "raj@x.com"}],
            }

        def list_bookings(self, *, take=25, status=None):
            seen["listed"] = True
            return []

    class FakeBooking:
        def __init__(self, db):
            pass

        def handle_calcom_webhook(self, payload, *, event_id):
            seen["token"] = payload["payload"]["metadata"]["fb_token"]
            seen["uid"] = payload["payload"]["uid"]
            return {"status": "confirmed"}

    monkeypatch.setattr(mod, "BookingService", FakeBooking)
    out = CalcomWebhookService(FakeDb(), client=FakeClient()).reconcile_for_token(
        "tok_abc", uid="book_1"
    )
    assert out["applied"][0]["result"] == "confirmed"
    assert seen["fetched"] == "book_1"
    assert seen["uid"] == "book_1"
    assert seen["token"] == "tok_abc"
    assert "listed" not in seen


def test_reconcile_for_token_ignores_uid_for_another_prospect(monkeypatch):
    from acquisition.services import calcom_webhook_service as mod

    class Link:
        prospect = type("P", (), {"email": "raj@x.com"})()

    class FakeDb:
        def scalar(self, _q):
            return Link()

    seen = {"listed": 0}

    class FakeClient:
        configured = True

        def get_booking_record(self, uid):
            return {
                "uid": uid,
                "status": "accepted",
                "metadata": {"fb_token": "other_token"},
                "attendees": [{"email": "other@x.com"}],
            }

        def list_bookings(self, *, take=25, status=None):
            seen["listed"] += 1
            return []

    class FakeBooking:
        def __init__(self, db):
            pass

        def handle_calcom_webhook(self, payload, *, event_id):
            raise AssertionError("must not confirm another prospect's booking")

    monkeypatch.setattr(mod, "BookingService", FakeBooking)
    out = CalcomWebhookService(FakeDb(), client=FakeClient()).reconcile_for_token(
        "tok_abc", uid="book_other"
    )
    assert out["applied"] == []
    assert seen["listed"] == 1


def test_reconcile_for_token_claims_unmapped_uid(monkeypatch):
    from acquisition.services import calcom_webhook_service as mod

    class Link:
        assessment_token = "tok_abc"
        prospect = type("P", (), {"email": "raj@x.com"})()

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def scalar(self, _q):
            self.calls += 1
            if self.calls == 1:
                return Link()
            return None

    seen = {}

    class FakeClient:
        configured = True

        def get_booking_record(self, uid):
            return {
                "uid": uid,
                "status": "accepted",
                "start": "2026-10-30T15:00:00Z",
                "end": "2026-10-30T15:30:00Z",
                "attendees": [{"email": "other@x.com"}],
            }

        def list_bookings(self, *, take=25, status=None):
            seen["listed"] = True
            return []

    class FakeBooking:
        def __init__(self, db):
            pass

        def handle_calcom_webhook(self, payload, *, event_id):
            seen["token"] = payload["payload"]["metadata"]["fb_token"]
            seen["uid"] = payload["payload"]["uid"]
            return {"status": "confirmed"}

    monkeypatch.setattr(mod, "BookingService", FakeBooking)
    out = CalcomWebhookService(FakeDb(), client=FakeClient()).reconcile_for_token(
        "tok_abc", uid="book_new"
    )
    assert out["applied"][0]["result"] == "confirmed"
    assert seen["uid"] == "book_new"
    assert seen["token"] == "tok_abc"
    assert "listed" not in seen


def test_booking_status_payload_forwards_uid(monkeypatch):
    from acquisition.services import acquisition_journey_service as journey_mod

    class Link:
        booking_status = "not_booked"
        booked_at = None
        meeting_start = None

    class FakeDb:
        def refresh(self, _obj):
            return None

    class FakeBooking:
        def __init__(self, db):
            pass

        def is_booking_confirmed(self, link):
            return False

    called = {}

    class FakeWebhook:
        def __init__(self, db):
            pass

        def reconcile_for_token(self, token, *, take=25, uid=None):
            called["token"] = token
            called["uid"] = uid
            return {"status": "ok"}

    monkeypatch.setattr(journey_mod, "_last_calcom_sync", {})
    monkeypatch.setattr("acquisition.services.booking_service.BookingService", FakeBooking)
    monkeypatch.setattr(
        "acquisition.services.calcom_webhook_service.CalcomWebhookService",
        FakeWebhook,
    )
    svc = journey_mod.AcquisitionJourneyService(FakeDb())
    monkeypatch.setattr(svc, "resolve", lambda token: (Link(), None))
    svc.booking_status_payload("tok_sync", uid="book_1")
    assert called == {"token": "tok_sync", "uid": "book_1"}


def test_load_booked_page_confirms_before_welcome(monkeypatch):
    from acquisition.services.acquisition_journey_service import AcquisitionJourneyService

    class Link:
        booking_status = "not_booked"

    svc = AcquisitionJourneyService(SimpleNamespace())
    states = {"n": 0}

    def enter_booked(token):
        states["n"] += 1
        if states["n"] == 1:
            raise AcquisitionJourneyError("Booking is not confirmed yet", http_status=403)
        return {"token": token, "welcome_heading": "A quick welcome before your meeting"}

    def booking_status_payload(token, *, sync=False, uid=None):
        states["synced"] = {"token": token, "sync": sync, "uid": uid}
        return {"confirmed": True}

    monkeypatch.setattr(svc, "enter_booked", enter_booked)
    monkeypatch.setattr(svc, "resolve", lambda token: (Link(), None))
    monkeypatch.setattr(svc, "can_access_calendar", lambda link, profile: True)
    monkeypatch.setattr(svc, "booking_status_payload", booking_status_payload)

    payload = svc.load_booked_page("tok_abc", uid="book_1")
    assert payload["confirming"] is False
    assert payload["welcome_heading"] == "A quick welcome before your meeting"
    assert states["synced"] == {"token": "tok_abc", "sync": True, "uid": "book_1"}
    assert states["n"] == 2
