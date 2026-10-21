"""Seed replaceable journey content for local/dev (and default proof case study)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.acquisition import AssessmentCaseStudy, CaseStudyContentStatus, JourneySettings
from acquisition.services.demo_video_storage import packaged_executive_demo_url

TEST_DEMO_URL = "/static/demo/test-demo-placeholder.html"

# Default public Proof — real Cepoch Data Insights experience, attributed to
# the team behind FreedomBot (not a JeanneCAIO client claim).
DEFAULT_PROOF_CASE_STUDY = {
    "title": "How the team behind FreedomBot improved access to business information",
    "client_display_name": "Experience from the team behind FreedomBot",
    "problem": (
        "Analysts were spending significant time pulling repetitive reports and "
        "handling ad-hoc data requests. Simple requests often depended on IT, while "
        "important information was spread across separate data sources and silos."
    ),
    "change": (
        "The team implemented an AI-powered Copilot connected to enterprise data and "
        "content repositories, allowing users to ask business questions and get answers "
        "without manually searching, slicing data, or waiting on IT."
    ),
    "measurable_result": (
        "Faster access to business answers\n"
        "Reduced dependence on manual reporting\n"
        "Improved Marketing and Operations productivity\n"
        "Faster decision-making\n"
        "Minimal additional technology footprint"
    ),
    "display_order": 0,
}

# Additional approved proofs — real Cepoch delivery experience, attributed the same
# way as the default (team behind FreedomBot). Chosen to mirror FreedomBot’s focus:
# consolidating knowledge and reducing operational coordination friction.
ADDITIONAL_PROOF_CASE_STUDIES = [
    {
        "title": "How the team behind FreedomBot turned product knowledge into instant answers",
        "client_display_name": "Experience from the team behind FreedomBot",
        "problem": (
            "A global food processor serving thousands of B2B clients relied on a "
            "large customer-service team for product questions. Complex requests — "
            "specifications, compliance details, ingredient status — required slow "
            "manual lookups, and volume was growing faster than the team could scale."
        ),
        "change": (
            "The team built an AI assistant on a unified enterprise knowledge base, "
            "trained on internal product and company data, with natural-language "
            "understanding and direct integration into the client portal so sales and "
            "customers could get accurate answers where they already worked."
        ),
        "measurable_result": (
            "About 40% of client questions answered automatically\n"
            "24/7 on-demand support without adding headcount\n"
            "Faster sales cycles and improved responsiveness\n"
            "Far less time spent on repetitive specification lookups"
        ),
        "display_order": 1,
    },
    {
        "title": "How the team behind FreedomBot put customer work into one operational workspace",
        "client_display_name": "Experience from the team behind FreedomBot",
        "problem": (
            "Service businesses were running on disconnected tools — website chat, "
            "social messages, phone callbacks, CRM notes, and invoice review each lived "
            "in a different place. Staff switched tabs all day, responses slipped, and "
            "back-office work depended on tribal knowledge and manual coordination."
        ),
        "change": (
            "The team delivered a multi-tenant workspace that unified inbound channels, "
            "leads, and back-office workflows: shared inbox, embeddable chatbot, voice "
            "handling, CRM integrations, and AI-assisted invoice review — with write "
            "actions kept behind explicit operator approval."
        ),
        "measurable_result": (
            "One workspace replaced separate handling for chat, social, and voice leads\n"
            "AI first-response and scoring reduced manual sorting of inbound work\n"
            "Invoice audits pulled CRM, email, and file context in a single pass\n"
            "New industry setups shipped through configuration instead of rebuilds"
        ),
        "display_order": 2,
    },
]

# Legacy TEST placeholders (Admin-only / layout fixtures). Kept disabled once the
# default approved proof exists so public Proof shows the real case study.
TEST_CASE_STUDIES = [
    {
        "title": "TEST CASE STUDY — Replace Before Production",
        "client_display_name": "Sample Co (placeholder)",
        "problem": "Placeholder problem narrative for journey testing only.",
        "change": "Placeholder change/solution narrative — not a real engagement.",
        "measurable_result": "Placeholder outcome language — not a real customer metric.",
        "display_order": 10,
    },
    {
        "title": "TEST CASE STUDY 2 — Replace Before Production",
        "client_display_name": "Example Firm (placeholder)",
        "problem": "Second placeholder proof card for layout testing.",
        "change": "Illustrative process clarification — not an approved case study.",
        "measurable_result": "No production claim. Delete or replace from Admin.",
        "display_order": 11,
    },
]


def seed_development_journey_content(db: Session, *, force: bool = False) -> None:
    """Idempotent seed. Ensures the default approved Cepoch-based proof exists."""
    settings = db.get(JourneySettings, 1)
    if settings is None:
        settings = JourneySettings(id=1)
        db.add(settings)
        db.flush()

    if force or not settings.demo_video_url:
        settings.demo_enabled = True
        settings.demo_title = "FreedomBot walkthrough"
        settings.demo_description = (
            "A short overview of how FreedomBot supports operational clarity."
        )
        if force:
            # Explicit Admin "seed test content" — HTML placeholder for layout only.
            settings.demo_require_watch = False
            settings.demo_video_url = TEST_DEMO_URL
            settings.demo_is_test_content = True
        else:
            packaged = packaged_executive_demo_url()
            if packaged:
                settings.demo_video_url = packaged
                settings.demo_require_watch = True
                settings.demo_is_test_content = False
            else:
                settings.demo_video_url = None
                settings.demo_require_watch = False
                settings.demo_is_test_content = False
                settings.demo_description = (
                    "Upload the walkthrough video from Admin → Acquisition → Demo."
                )

    if force or not settings.value_heading:
        settings.value_enabled = True
        settings.value_is_test_content = False
        settings.value_heading = "What this could mean for your business"
        settings.value_intro = (
            "Based on your assessment, the biggest opportunity appears to be reducing "
            "the operational friction caused by fragmented workflows, repeated decision "
            "escalation, scattered information, and unnecessary back-and-forth."
        )
        settings.value_supporting_note = (
            "FreedomBot is designed to help teams surface the right information, create "
            "more consistent workflows, and reduce dependency on manual coordination.\n\n"
            "The goal is not simply to add another tool. It is to help consolidate "
            "knowledge, guidance, automation, and decision support around the way the "
            "business actually operates."
        )
        settings.value_investment_text = (
            "Every FreedomBot implementation is customized to the business. Final scope "
            "and investment depend on the problems being addressed, the systems involved, "
            "and the level of automation or integration required."
        )
        settings.budget_question = (
            "Would you be comfortable discussing a customized implementation and "
            "investment with Jeanne?"
        )
        settings.budget_label_ready = "Yes, I’m ready to discuss next steps"
        settings.budget_label_unclear = "I’m interested, but I need more context"
        settings.budget_label_not_ready = "Not right now"
        settings.budget_options = None
        settings.value_continue_button_text = "Continue"
        settings.value_context_html = None

    db.add(settings)

    existing = list(db.scalars(select(AssessmentCaseStudy)).all())
    default_title = DEFAULT_PROOF_CASE_STUDY["title"]
    default_row = next((s for s in existing if (s.title or "") == default_title), None)

    if default_row is None:
        default_row = AssessmentCaseStudy(
            id=uuid.uuid4(),
            title=default_title,
            client_display_name=DEFAULT_PROOF_CASE_STUDY["client_display_name"],
            problem=DEFAULT_PROOF_CASE_STUDY["problem"],
            change=DEFAULT_PROOF_CASE_STUDY["change"],
            measurable_result=DEFAULT_PROOF_CASE_STUDY["measurable_result"],
            display_order=DEFAULT_PROOF_CASE_STUDY["display_order"],
            enabled=True,
            content_status=CaseStudyContentStatus.APPROVED.value,
        )
        db.add(default_row)
    elif force:
        default_row.client_display_name = DEFAULT_PROOF_CASE_STUDY["client_display_name"]
        default_row.problem = DEFAULT_PROOF_CASE_STUDY["problem"]
        default_row.change = DEFAULT_PROOF_CASE_STUDY["change"]
        default_row.measurable_result = DEFAULT_PROOF_CASE_STUDY["measurable_result"]
        default_row.display_order = DEFAULT_PROOF_CASE_STUDY["display_order"]
        default_row.enabled = True
        default_row.content_status = CaseStudyContentStatus.APPROVED.value
        db.add(default_row)
    else:
        # Ensure the default stays approved + enabled even if Admin toggled status by mistake
        # during early setup — only lift enabled if it was never present before seed.
        if default_row.content_status != CaseStudyContentStatus.APPROVED.value:
            default_row.content_status = CaseStudyContentStatus.APPROVED.value
            db.add(default_row)

    # Idempotently ensure the additional approved FreedomBot-aligned proofs exist.
    existing_by_title = {(s.title or ""): s for s in list(db.scalars(select(AssessmentCaseStudy)).all())}
    for item in ADDITIONAL_PROOF_CASE_STUDIES:
        row = existing_by_title.get(item["title"])
        if row is None:
            db.add(
                AssessmentCaseStudy(
                    id=uuid.uuid4(),
                    title=item["title"],
                    client_display_name=item["client_display_name"],
                    problem=item["problem"],
                    change=item["change"],
                    measurable_result=item["measurable_result"],
                    display_order=item["display_order"],
                    enabled=True,
                    content_status=CaseStudyContentStatus.APPROVED.value,
                )
            )
        elif force:
            row.client_display_name = item["client_display_name"]
            row.problem = item["problem"]
            row.change = item["change"]
            row.measurable_result = item["measurable_result"]
            row.display_order = item["display_order"]
            row.enabled = True
            row.content_status = CaseStudyContentStatus.APPROVED.value
            db.add(row)
        elif row.content_status != CaseStudyContentStatus.APPROVED.value:
            row.content_status = CaseStudyContentStatus.APPROVED.value
            db.add(row)

    # Prefer real proofs on public Proof: disable legacy TEST placeholders.
    approved_titles = {DEFAULT_PROOF_CASE_STUDY["title"]} | {
        item["title"] for item in ADDITIONAL_PROOF_CASE_STUDIES
    }
    for s in list(db.scalars(select(AssessmentCaseStudy)).all()):
        if (s.title or "") in approved_titles:
            continue
        is_test = (
            s.content_status == CaseStudyContentStatus.TEST.value
            or (s.title or "").startswith("TEST CASE STUDY")
        )
        if is_test and s.enabled:
            s.enabled = False
            db.add(s)

    # Optionally re-create disabled TEST fixtures for Admin layout testing.
    has_test = any(
        (s.content_status == CaseStudyContentStatus.TEST.value)
        or (s.title or "").startswith("TEST CASE STUDY")
        for s in existing
    )
    if force or not has_test:
        if force:
            for s in list(db.scalars(select(AssessmentCaseStudy)).all()):
                if s.title == default_title:
                    continue
                if (s.title or "").startswith("TEST CASE STUDY") or s.content_status == (
                    CaseStudyContentStatus.TEST.value
                ):
                    db.delete(s)
            db.flush()
        for item in TEST_CASE_STUDIES:
            db.add(
                AssessmentCaseStudy(
                    id=uuid.uuid4(),
                    title=item["title"],
                    client_display_name=item["client_display_name"],
                    problem=item["problem"],
                    change=item["change"],
                    measurable_result=item["measurable_result"],
                    display_order=item["display_order"],
                    enabled=False,
                    content_status=CaseStudyContentStatus.TEST.value,
                )
            )
    db.commit()
