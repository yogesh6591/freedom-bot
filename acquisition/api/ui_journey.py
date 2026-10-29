"""Admin UI: acquisition journey settings + case studies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.db.session import get_db
from acquisition.integrations.calcom.client import CalcomError
from acquisition.models.acquisition import AssessmentCaseStudy, CaseStudyContentStatus
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.calcom_admin import apply_calcom_event_selection, calcom_admin_context
from acquisition.services.journey_publish_service import JourneyPublishError, publish_journey
from acquisition.services.demo_video_storage import (
    DemoVideoUploadError,
    is_placeholder_demo_url,
    save_uploaded_demo_video,
    save_uploaded_poster_image,
)
from acquisition.templating import create_templates
from acquisition.auth.admin import require_admin_html_request

templates = create_templates()
router = APIRouter(tags=["admin-ui-journey"], dependencies=[Depends(require_admin_html_request)])

_VALID_TABS = frozenset({"proof", "demo", "value", "calendar", "after_booking"})


def get_journey(db: Session = Depends(get_db)) -> AcquisitionJourneyService:
    return AcquisitionJourneyService(db)


def _checkbox(value: str) -> bool:
    return value.lower() in {"1", "true", "on", "yes"}


def _journey_redirect(*, flash: str, tab: str | None = None) -> RedirectResponse:
    clean_tab = (tab or "proof").strip().lower()
    if clean_tab not in _VALID_TABS:
        clean_tab = "proof"
    return RedirectResponse(
        url=f"/settings/journey?flash={flash}&tab={clean_tab}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/settings/journey", response_class=HTMLResponse)
def journey_settings_page(
    request: Request,
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    settings = journey.get_settings()
    studies = journey.list_all_case_studies()
    youtube_links = journey.list_all_youtube_links()
    edit_raw = (request.query_params.get("edit") or "").strip()
    editing_study = None
    if edit_raw:
        editing_study = next((s for s in studies if str(s.id) == edit_raw), None)
    yt_edit_raw = (request.query_params.get("yt_edit") or "").strip()
    editing_youtube = None
    if yt_edit_raw:
        editing_youtube = next((y for y in youtube_links if str(y.id) == yt_edit_raw), None)
    return templates.TemplateResponse(
        request,
        "settings/journey.html",
        {
            "settings": settings,
            "case_studies": studies,
            "editing_study": editing_study,
            "youtube_links": youtube_links,
            "editing_youtube": editing_youtube,
            "show_new_proof": request.query_params.get("new") == "1",
            "show_new_youtube": request.query_params.get("yt_new") == "1",
            "flash": request.query_params.get("flash"),
            "environment": get_settings().environment,
            "webhook_hint": (
                f"{(get_settings().public_webhook_base_url or get_settings().public_base_url or '').rstrip('/')}/api/webhooks/calcom"
            ),
            **calcom_admin_context(settings, db=db, include_events=True),
        },
    )


@router.post("/settings/journey")
def journey_settings_save(
    demo_enabled: Annotated[str, Form()] = "",
    demo_require_watch: Annotated[str, Form()] = "",
    demo_title: Annotated[str, Form()] = "",
    demo_description: Annotated[str, Form()] = "",
    demo_video: UploadFile | None = File(None),
    value_enabled: Annotated[str, Form()] = "",
    value_heading: Annotated[str, Form()] = "",
    value_investment_text: Annotated[str, Form()] = "",
    budget_question: Annotated[str, Form()] = "",
    budget_label_ready: Annotated[str, Form()] = "",
    budget_label_unclear: Annotated[str, Form()] = "",
    budget_label_not_ready: Annotated[str, Form()] = "",
    value_continue_button_text: Annotated[str, Form()] = "",
    event_type_id: Annotated[str, Form()] = "",
    booking_url: Annotated[str, Form()] = "",
    after_booking_video_enabled: Annotated[str, Form()] = "",
    after_booking_video_heading: Annotated[str, Form()] = "",
    after_booking_video_intro: Annotated[str, Form()] = "",
    after_booking_video: UploadFile | None = File(None),
    after_booking_video_poster: UploadFile | None = File(None),
    after_booking_video_script: Annotated[str, Form()] = "",
    after_booking_prep_heading: Annotated[str, Form()] = "",
    after_booking_prep_body: Annotated[str, Form()] = "",
    after_booking_prep_note: Annotated[str, Form()] = "",
    return_tab: Annotated[str, Form()] = "demo",
    journey: AcquisitionJourneyService = Depends(get_journey),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.models.acquisition import DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT

    row = journey.get_settings()
    tab = (return_tab or "demo").strip().lower()
    if tab not in _VALID_TABS:
        tab = "demo"

    if tab == "demo":
        if demo_video is not None and (demo_video.filename or "").strip():
            try:
                row.demo_video_url = save_uploaded_demo_video(demo_video)
                row.demo_is_test_content = False
            except DemoVideoUploadError:
                return _journey_redirect(flash="demo_upload_error", tab="demo")
        row.demo_enabled = _checkbox(demo_enabled)
        row.demo_require_watch = _checkbox(demo_require_watch)
        row.demo_title = demo_title.strip() or None
        row.demo_description = demo_description.strip() or None
        if not is_placeholder_demo_url(row.demo_video_url):
            row.demo_is_test_content = False

    elif tab == "value":
        row.value_enabled = _checkbox(value_enabled)
        row.value_heading = value_heading.strip() or None
        row.value_investment_text = value_investment_text.strip() or None
        # Legacy value_intro / value_supporting_note / value_context_html are not
        # edited in Admin; existing DB values are left unchanged.
        row.budget_question = budget_question.strip() or None
        row.budget_label_ready = budget_label_ready.strip() or None
        row.budget_label_unclear = budget_label_unclear.strip() or None
        row.budget_label_not_ready = budget_label_not_ready.strip() or None
        row.budget_options = None  # use the three label fields only
        row.value_continue_button_text = value_continue_button_text.strip() or None
        row.value_is_test_content = False

    elif tab == "calendar":
        event_id = (event_type_id or "").strip()
        if event_id:
            try:
                apply_calcom_event_selection(row=row, event_type_id=event_id, db=db)
                row.calendar_enabled = True
            except CalcomError:
                return _journey_redirect(flash="calendar_event_error", tab="calendar")
        # Booking URL module — keep public link in sync (event default or manual edit).
        cleaned_booking = (booking_url or "").strip()
        if cleaned_booking:
            row.calcom_booking_url = cleaned_booking
            row.calendar_url = cleaned_booking
            row.calendar_enabled = True
        elif event_id and (row.calcom_booking_url or row.calendar_url):
            row.calendar_enabled = True

    elif tab == "after_booking":
        if after_booking_video is not None and (after_booking_video.filename or "").strip():
            try:
                row.after_booking_video_url = save_uploaded_demo_video(after_booking_video)
                row.after_booking_video_is_test = False
            except DemoVideoUploadError:
                return _journey_redirect(flash="demo_upload_error", tab="after_booking")
        if after_booking_video_poster is not None and (
            after_booking_video_poster.filename or ""
        ).strip():
            try:
                row.after_booking_video_poster_url = save_uploaded_poster_image(
                    after_booking_video_poster
                )
            except DemoVideoUploadError:
                return _journey_redirect(flash="demo_upload_error", tab="after_booking")
        row.after_booking_video_enabled = _checkbox(after_booking_video_enabled)
        row.after_booking_video_heading = after_booking_video_heading.strip() or None
        row.after_booking_video_intro = after_booking_video_intro.strip() or None
        row.after_booking_video_script = (
            after_booking_video_script.strip() or DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT
        )
        row.after_booking_prep_heading = after_booking_prep_heading.strip() or None
        row.after_booking_prep_body = after_booking_prep_body.strip() or None
        row.after_booking_prep_note = after_booking_prep_note.strip() or None

    db.add(row)
    db.commit()
    return _journey_redirect(flash="saved", tab=tab)


@router.post("/settings/journey/publish")
def journey_publish(db: Session = Depends(get_db)) -> RedirectResponse:
    """Make Proof / Demo / Value / Calendar live for prospects."""
    try:
        publish_journey(db)
    except JourneyPublishError:
        return _journey_redirect(flash="publish_error", tab="proof")
    return _journey_redirect(flash="published", tab="proof")


@router.post("/settings/journey/case-studies")
def case_study_create(
    title: Annotated[str, Form()] = "",
    client_display_name: Annotated[str, Form()] = "",
    problem: Annotated[str, Form()] = "",
    change: Annotated[str, Form()] = "",
    measurable_result: Annotated[str, Form()] = "",
    display_order: Annotated[int, Form()] = 0,
    return_tab: Annotated[str, Form()] = "proof",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    title_clean = title.strip()
    if not title_clean:
        return _journey_redirect(flash="case_error", tab=return_tab)
    study = AssessmentCaseStudy(
        id=uuid.uuid4(),
        title=title_clean,
        client_display_name=client_display_name.strip() or None,
        problem=problem.strip(),
        change=change.strip(),
        measurable_result=measurable_result.strip(),
        display_order=display_order,
        enabled=False,  # drafts until Live is toggled in the list
        content_status=CaseStudyContentStatus.APPROVED.value,
    )
    db.add(study)
    db.commit()
    return _journey_redirect(flash="case_created", tab=return_tab)


@router.post("/settings/journey/case-studies/{study_id}")
def case_study_update(
    study_id: uuid.UUID,
    title: Annotated[str, Form()] = "",
    client_display_name: Annotated[str, Form()] = "",
    problem: Annotated[str, Form()] = "",
    change: Annotated[str, Form()] = "",
    measurable_result: Annotated[str, Form()] = "",
    display_order: Annotated[int, Form()] = 0,
    return_tab: Annotated[str, Form()] = "proof",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    study = db.get(AssessmentCaseStudy, study_id)
    if study is None:
        return _journey_redirect(flash="not_found", tab=return_tab)
    title_clean = title.strip()
    if not title_clean:
        return _journey_redirect(flash="case_error", tab=return_tab)
    study.title = title_clean
    study.client_display_name = client_display_name.strip() or None
    study.problem = problem.strip()
    study.change = change.strip()
    study.measurable_result = measurable_result.strip()
    study.display_order = display_order
    # Live / draft is controlled only from the list toggle — do not change enabled here.
    db.add(study)
    db.commit()
    return _journey_redirect(flash="case_updated", tab=return_tab)


@router.post("/settings/journey/case-studies/{study_id}/toggle")
def case_study_toggle(
    study_id: uuid.UUID,
    return_tab: Annotated[str, Form()] = "proof",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    study = db.get(AssessmentCaseStudy, study_id)
    if study is None:
        return _journey_redirect(flash="not_found", tab=return_tab)
    study.enabled = not study.enabled
    if study.enabled:
        # Going live implies publishable content for production.
        study.content_status = CaseStudyContentStatus.APPROVED.value
    db.add(study)
    db.commit()
    return _journey_redirect(flash="case_updated", tab=return_tab)


@router.post("/settings/journey/case-studies/{study_id}/delete")
def case_study_delete(
    study_id: uuid.UUID,
    return_tab: Annotated[str, Form()] = "proof",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    study = db.get(AssessmentCaseStudy, study_id)
    if study is not None:
        db.delete(study)
        db.commit()
    return _journey_redirect(flash="case_deleted", tab=return_tab)


@router.post("/settings/journey/youtube")
def youtube_create(
    title: Annotated[str, Form()] = "",
    youtube_url: Annotated[str, Form()] = "",
    display_order: Annotated[int, Form()] = 0,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.models.acquisition import AfterBookingYoutubeLink

    title_clean = title.strip()
    url_clean = youtube_url.strip()
    if not title_clean or not url_clean:
        return _journey_redirect(flash="yt_error", tab="after_booking")
    row = AfterBookingYoutubeLink(
        id=uuid.uuid4(),
        title=title_clean,
        youtube_url=url_clean,
        display_order=display_order,
        enabled=False,
        content_status=CaseStudyContentStatus.TEST.value,
    )
    db.add(row)
    db.commit()
    return _journey_redirect(flash="yt_saved", tab="after_booking")


@router.post("/settings/journey/youtube/{link_id}")
def youtube_update(
    link_id: uuid.UUID,
    title: Annotated[str, Form()] = "",
    youtube_url: Annotated[str, Form()] = "",
    display_order: Annotated[int, Form()] = 0,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.models.acquisition import AfterBookingYoutubeLink

    row = db.get(AfterBookingYoutubeLink, link_id)
    if row is None:
        return _journey_redirect(flash="not_found", tab="after_booking")
    title_clean = title.strip()
    url_clean = youtube_url.strip()
    if not title_clean or not url_clean:
        return _journey_redirect(flash="yt_error", tab="after_booking")
    row.title = title_clean
    row.youtube_url = url_clean
    row.display_order = display_order
    db.add(row)
    db.commit()
    return _journey_redirect(flash="yt_saved", tab="after_booking")


@router.post("/settings/journey/youtube/{link_id}/toggle")
def youtube_toggle(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.models.acquisition import AfterBookingYoutubeLink

    row = db.get(AfterBookingYoutubeLink, link_id)
    if row is None:
        return _journey_redirect(flash="not_found", tab="after_booking")
    row.enabled = not row.enabled
    if row.enabled:
        row.content_status = CaseStudyContentStatus.APPROVED.value
    db.add(row)
    db.commit()
    return _journey_redirect(flash="yt_saved", tab="after_booking")


@router.post("/settings/journey/youtube/{link_id}/delete")
def youtube_delete(
    link_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    from acquisition.models.acquisition import AfterBookingYoutubeLink

    row = db.get(AfterBookingYoutubeLink, link_id)
    if row is not None:
        db.delete(row)
        db.commit()
    return _journey_redirect(flash="yt_deleted", tab="after_booking")
