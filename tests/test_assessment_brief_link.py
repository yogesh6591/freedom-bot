"""The assessment reply gives a pre-call brief as a link, not the section texts."""

from types import SimpleNamespace

from app.outreach.assessment_tools import full_report


def test_full_report_contains_brief_link_and_no_section_texts():
    person = SimpleNamespace(executive_name="Nina", email="nina@x.com")
    snapshot = "UNIQUE_BRIEF_SNAPSHOT_TEXT_XYZ"
    discussion = "UNIQUE_BRIEF_DISCUSSION_TEXT_ABC"
    url = "https://example.test/acquire/assessments/11111111-1111-1111-1111-111111111111/pre-call-brief"
    stage = {
        "assessment_status": "completed",
        "qualification_status": "qualified",
        "completed_at": None,
        "page_opened_at": None,
        "qualification": {},
        "result": {},
        "journey_stage": "booked",
        "booking_status": "confirmed",
        "meeting_start": None,
        "pre_call_brief_status": "ready",
        "pre_call_brief": {
            "business_snapshot": snapshot,
            "discussion_points": discussion,
        },
        "brief_url": url,
    }
    text = full_report(person, stage, {"sent": 1, "queued": 0}, include_brief=True)
    assert url in text
    assert "- Open the pre-call brief in a new tab: " + url in text
    assert "- Pre-call brief: ready" in text
    assert snapshot not in text
    assert discussion not in text
    assert "business snapshot" not in text.lower()
    assert "discussion points" not in text.lower()
