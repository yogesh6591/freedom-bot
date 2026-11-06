"""show_journey_settings includes the demo and after-booking video URLs."""

from __future__ import annotations

from acquisition.db.session import SessionLocal, engine
from acquisition.models.acquisition import JourneySettings
from app.outreach.journey_tools import show_journey_settings
from app.outreach.toolkit import Call

TEST_DB_NAME = "acquisition_test"
DEMO_URL = "https://www.youtube.com/watch?v=demoUrlTest111"
AFTER_URL = "https://www.youtube.com/watch?v=afterUrlTest222"


def test_show_journey_settings_contains_both_video_urls():
    assert engine.url.database == TEST_DB_NAME
    db = SessionLocal()
    prev_demo = prev_after = None
    try:
        settings = db.get(JourneySettings, 1)
        if settings is None:
            settings = JourneySettings(id=1)
            db.add(settings)
        prev_demo = settings.demo_video_url
        prev_after = settings.after_booking_video_url
        settings.demo_video_url = DEMO_URL
        settings.after_booking_video_url = AFTER_URL
        db.commit()

        out = show_journey_settings(Call(db=db, state={}, latest="show journey", user_id="test-user"))
        assert DEMO_URL in out, out
        assert AFTER_URL in out, out
    finally:
        settings = db.get(JourneySettings, 1)
        if settings is not None:
            settings.demo_video_url = prev_demo
            settings.after_booking_video_url = prev_after
            db.commit()
        db.close()
