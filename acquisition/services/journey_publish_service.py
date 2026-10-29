"""Publish the acquisition journey: make Proof, Demo, Value and Calendar live for prospects.

Used by the admin "Publish" button and by the chat, so both apply the same rules.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from acquisition.models.acquisition import AssessmentCaseStudy, CaseStudyContentStatus
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.demo_video_storage import is_placeholder_demo_url


class JourneyPublishError(Exception):
    """The journey cannot go live yet. The message says what is missing."""


def is_test_case_study(study: AssessmentCaseStudy) -> bool:
    """Placeholder content that must never reach a prospect."""
    title = study.title or ""
    return (
        str(getattr(study, "content_status", "") or "").lower() == "test"
        or title.upper().startswith("TEST CASE STUDY")
        or "replace before production" in title.lower()
    )


def readiness(db: Session) -> tuple[list[AssessmentCaseStudy], bool]:
    """(real case studies, whether a real demo video is set)."""
    journey = AcquisitionJourneyService(db)
    row = journey.get_settings()
    real = [s for s in journey.list_all_case_studies() if not is_test_case_study(s)]
    has_video = bool((row.demo_video_url or "").strip()) and not is_placeholder_demo_url(row.demo_video_url)
    return real, has_video


def publish_journey(db: Session) -> None:
    """Turn on every journey section, make real case studies live and hide test ones. Commits."""
    journey = AcquisitionJourneyService(db)
    row = journey.get_settings()
    studies = journey.list_all_case_studies()
    real, has_video = readiness(db)
    if not real:
        raise JourneyPublishError("there is no real case study yet (only test or placeholder ones)")
    if not has_video:
        raise JourneyPublishError("there is no real demo video yet (it is uploaded in the admin Journey settings)")
    for study in studies:
        if is_test_case_study(study):
            study.enabled = False
        else:
            study.enabled = True
            study.content_status = CaseStudyContentStatus.APPROVED.value
        db.add(study)
    row.demo_enabled = True
    row.demo_is_test_content = False
    row.value_enabled = True
    row.value_is_test_content = False
    row.calendar_enabled = True
    db.add(row)
    db.commit()
