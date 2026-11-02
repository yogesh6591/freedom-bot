"""Original proof cards and packaged demo/welcome video can be restored."""

from __future__ import annotations

from acquisition.db.session import SessionLocal, engine
from acquisition.models.acquisition import AssessmentCaseStudy, CaseStudyContentStatus, JourneySettings
from acquisition.services.demo_video_storage import packaged_executive_demo_url
from acquisition.services.journey_seed import (
    ORIGINAL_PROOF_TITLES,
    restore_original_journey_content,
)


TEST_DB_NAME = "acquisition_test"


def test_restore_original_proofs_and_videos():
    assert engine.url.database == TEST_DB_NAME
    packaged = packaged_executive_demo_url()
    assert packaged == "/static/demo/FreedomBot_Executive_Demo_v1.mp4"

    db = SessionLocal()
    try:
        settings = db.get(JourneySettings, 1)
        if settings is None:
            settings = JourneySettings(id=1)
            db.add(settings)
        settings.demo_video_url = "https://www.youtube.com/watch?v=XAfxi-Xo_sk"
        settings.after_booking_video_url = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        db.add(
            AssessmentCaseStudy(
                title="Faster claims handling",
                client_display_name="Acme Insurance",
                problem="placeholder",
                change="placeholder",
                measurable_result="placeholder",
                display_order=0,
                enabled=True,
                content_status=CaseStudyContentStatus.APPROVED.value,
            )
        )
        db.commit()

        restore_original_journey_content(db, restore_videos=True)

        titles = {
            row.title: row
            for row in db.query(AssessmentCaseStudy).all()
        }
        assert ORIGINAL_PROOF_TITLES <= set(titles)
        for title in ORIGINAL_PROOF_TITLES:
            row = titles[title]
            assert row.enabled is True
            assert row.content_status == CaseStudyContentStatus.APPROVED.value
        acme = titles["Faster claims handling"]
        assert acme.enabled is False

        settings = db.get(JourneySettings, 1)
        assert settings.demo_video_url == packaged
        assert settings.demo_is_test_content is False
        assert settings.after_booking_video_url == packaged
        assert settings.after_booking_video_is_test is False
    finally:
        db.close()
