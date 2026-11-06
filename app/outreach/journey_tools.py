"""Journey content: settings text, case studies, video links, and publish.

Covers what the admin Journey settings page edits. A video file is attached in the chat (the link it is saved
under is then set here); posters are uploaded in the admin panel. Text edits, section switches, links, and making a video link live are immediate. Making a case
study live, publishing, and anything deleted asks the user for one yes first.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from acquisition.models.acquisition import (
    AfterBookingYoutubeLink,
    AssessmentCaseStudy,
    CaseStudyContentStatus,
    JourneySettings,
)
from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
from acquisition.services.journey_publish_service import (
    JourneyPublishError,
    is_test_case_study,
    publish_journey,
    readiness,
)
from app.outreach import matching
from app.outreach.confirm import action_key
from app.outreach.toolkit import Call, ToolError, tool

# A video attached in the chat is saved under this path on the server (the same place the admin panel uses).
_OWN_VIDEO = re.compile(r"/static/demo/[\w.-]+\.(mp4|webm|mov|m4v)", re.I)
_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}


# ------------------------------------------------------------------------------------------------ settings catalog
@dataclass(frozen=True)
class Setting:
    key: str
    column: str  # attribute on JourneySettings
    label: str
    kind: str = "text"  # text | flag | link
    max_len: Optional[int] = None
    tab: str = ""


SETTINGS: tuple[Setting, ...] = (
    Setting("demo_enabled", "demo_enabled", "demo section on", "flag", tab="demo"),
    Setting("demo_require_watch", "demo_require_watch", "must watch the demo", "flag", tab="demo"),
    Setting("demo_video_url", "demo_video_url", "demo video link", "link", max_len=1024, tab="demo"),
    Setting("demo_title", "demo_title", "demo title", max_len=255, tab="demo"),
    Setting("demo_description", "demo_description", "demo description", tab="demo"),
    Setting("value_enabled", "value_enabled", "value section on", "flag", tab="value"),
    Setting("value_heading", "value_heading", "value heading", max_len=255, tab="value"),
    Setting("value_investment_text", "value_investment_text", "investment text", tab="value"),
    Setting("budget_question", "budget_question", "budget question", tab="value"),
    Setting("budget_label_ready", "budget_label_ready", "budget answer: ready", max_len=255, tab="value"),
    Setting("budget_label_unclear", "budget_label_unclear", "budget answer: unclear", max_len=255, tab="value"),
    Setting("budget_label_not_ready", "budget_label_not_ready", "budget answer: not ready", max_len=255, tab="value"),
    Setting("value_continue_button_text", "value_continue_button_text", "continue button text", max_len=128, tab="value"),
    Setting("calendar_enabled", "calendar_enabled", "calendar section on", "flag", tab="calendar"),
    Setting("booking_url", "calcom_booking_url", "booking link", max_len=1024, tab="calendar"),
    Setting("after_booking_video_enabled", "after_booking_video_enabled", "after-booking video on", "flag", tab="after_booking"),
    Setting("after_booking_video_url", "after_booking_video_url", "welcome video link", "link", max_len=1024, tab="after_booking"),
    Setting("after_booking_video_heading", "after_booking_video_heading", "after-booking video heading", max_len=255, tab="after_booking"),
    Setting("after_booking_video_intro", "after_booking_video_intro", "after-booking video intro", tab="after_booking"),
    Setting("after_booking_video_script", "after_booking_video_script", "welcome video script", tab="after_booking"),
    Setting("after_booking_prep_heading", "after_booking_prep_heading", "preparation heading", max_len=255, tab="after_booking"),
    Setting("after_booking_prep_body", "after_booking_prep_body", "preparation text", tab="after_booking"),
    Setting("after_booking_prep_note", "after_booking_prep_note", "preparation note", tab="after_booking"),
)
BY_KEY = {s.key: s for s in SETTINGS}
SETTING_KEYS = tuple(BY_KEY)


def _flag(setting: Setting, value: Any) -> bool:
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ToolError(f"{setting.key} must be on or off (true or false).")


def _link(setting: Setting, value: Any) -> Optional[str]:
    """The demo video link: YouTube, Vimeo or a direct https video file. Empty clears it."""
    from acquisition.services.demo_video_storage import is_valid_demo_link

    text = _text(setting, value)
    if text and not (_OWN_VIDEO.fullmatch(text) or is_valid_demo_link(text)):
        raise ToolError("the video link must be a YouTube or Vimeo link, a direct https link to an .mp4, .webm, .mov or .m4v file, or the link of a video attached in this chat.")
    return text


def _text(setting: Setting, value: Any) -> Optional[str]:
    text = str(value if value is not None else "").strip()
    if setting.max_len and len(text) > setting.max_len:
        raise ToolError(f"the {setting.label} is too long (max {setting.max_len} characters).")
    return text or None


def _row(call: Call) -> JourneySettings:
    return AcquisitionJourneyService(call.db).get_settings()


def _missing(has_case_study: bool, has_video: bool) -> str:
    """What publishing still needs, as a phrase ('' when it can go)."""
    return " and ".join(w for ok, w in ((has_case_study, "a real case study"), (has_video, "a real demo video")) if not ok)


def _clip(value: Any, n: int = 70) -> str:
    text = " ".join(str(value or "").split())
    return "-" if not text else (text if len(text) <= n else text[: n - 1] + "…")


# ------------------------------------------------------------------------------------------------ show
@tool("show_journey_settings")
def show_journey_settings(call: Call) -> str:
    """The journey in one view: sections on/off, key texts, case studies, video links and whether it can be published."""
    row = _row(call)
    journey = AcquisitionJourneyService(call.db)
    real, has_video = readiness(call.db)
    on = lambda flag: "on" if flag else "off"  # noqa: E731
    demo_url = (row.demo_video_url or "").strip()
    after_url = (row.after_booking_video_url or "").strip()
    lines = [
        f"Demo: {on(row.demo_enabled)}, title {_clip(row.demo_title)}, "
        f"video {demo_url if has_video and demo_url else 'not set (placeholder)'}, "
        f"must watch {on(row.demo_require_watch)}",
        f"Value: {on(row.value_enabled)}, heading {_clip(row.value_heading)}",
        f"Calendar: {on(row.calendar_enabled)}, event {_clip(row.calcom_event_title)}, link {_clip(row.calcom_booking_url or row.calendar_url, 60)}",
        f"After booking: video {on(row.after_booking_video_enabled)}, "
        f"url {after_url or '-'}, heading {_clip(row.after_booking_video_heading)}, "
        f"prep heading {_clip(row.after_booking_prep_heading)}",
    ]
    studies = journey.list_all_case_studies()
    lines.append(f"Case studies ({len(studies)}):")
    lines += [f"- {s.title}" + (f" ({s.client_display_name})" if s.client_display_name else "")
              + f": {'live' if s.enabled else 'draft'}" + (" [test content]" if is_test_case_study(s) else "") for s in studies] or ["- none"]
    links = journey.list_all_youtube_links()
    lines.append(f"Video links ({len(links)}):")
    lines += [f"- {y.title}: {'live' if y.enabled else 'draft'}" for y in links] or ["- none"]
    missing = _missing(bool(real), has_video)
    lines.append(f"Can publish: not yet, needs {missing}" if missing else "Can publish: yes")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ settings
@tool("update_journey_settings", "changes")
def update_journey_settings(call: Call, changes: Optional[dict] = None) -> str:
    """Change journey text and section switches."""
    if not isinstance(changes, dict) or not changes:
        raise ToolError(f"changes is empty. Settings: {', '.join(SETTING_KEYS)}.")
    unknown = [k for k in changes if k not in BY_KEY]
    if unknown:
        raise ToolError(f"unknown setting {', '.join(unknown)}. Settings: {', '.join(SETTING_KEYS)}.")
    convert = {"flag": _flag, "link": _link}
    values = {k: convert.get(BY_KEY[k].kind, _text)(BY_KEY[k], v) for k, v in changes.items()}
    flags = {k: v for k, v in values.items() if BY_KEY[k].kind == "flag"}
    row = _row(call)

    from acquisition.services.demo_video_storage import is_embed_link

    # A YouTube or Vimeo link plays, but the page cannot tell when it ends, so "must watch" cannot work with it.
    link_after = values["demo_video_url"] if "demo_video_url" in values else row.demo_video_url
    side_note = ""
    if is_embed_link(link_after):
        if flags.get("demo_require_watch"):
            raise ToolError("must-watch needs an uploaded video file; it cannot be tracked on a YouTube or Vimeo link.")
        if row.demo_require_watch and "demo_require_watch" not in values and "demo_video_url" in values:
            values["demo_require_watch"] = flags["demo_require_watch"] = False
            side_note = " Must-watch is turned off, because it cannot be tracked on a YouTube or Vimeo link."

    for key, value in values.items():
        setting = BY_KEY[key]
        setattr(row, setting.column, value)
        if key == "booking_url":
            row.calendar_url = value  # the public link is kept in sync, as the admin page does
    tabs = {BY_KEY[k].tab for k in values}
    if "value" in tabs:
        row.value_is_test_content = False
        if any(k.startswith("budget_label_") for k in values):
            row.budget_options = None  # the three label fields are the source
    if "demo" in tabs:
        from acquisition.services.demo_video_storage import is_placeholder_demo_url

        if not is_placeholder_demo_url(row.demo_video_url):
            row.demo_is_test_content = False
    if "after_booking_video_url" in values and values["after_booking_video_url"]:
        row.after_booking_video_is_test = False
    call.db.add(row)
    call.db.commit()
    return "Saved: " + ", ".join(f"{BY_KEY[k].label}" for k in values) + "." + side_note


# ------------------------------------------------------------------------------------------------ case studies and links
def _pick(items: list, text: str, titles) -> Any:
    """The case study / link the user means, by (typo-tolerant) title."""
    want = (text or "").strip()
    if not want:
        raise ToolError("say which one (its title).")
    scored = sorted(((max(matching.score(want, t) for t in titles(i) if t), i) for i in items), key=lambda x: -x[0])
    scored = [(s, i) for s, i in scored if s >= matching.CUTOFF]
    if not scored:
        raise ToolError(f"nothing is called {want!r}. Show the journey settings to see the titles.")
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.06:
        raise ToolError("several match: " + "; ".join(i.title for _, i in scored[:5]) + ". Ask the user which one.")
    return scored[0][1]


_STUDY_FIELDS = ("title", "client_display_name", "problem", "change", "measurable_result")


@tool("edit_case_study", "action", "which", "title", "client_display_name", "problem", "change", "measurable_result",
      "display_order", "approval_evidence")
def edit_case_study(call: Call, action: str = "", which: str = "", title: str = "", client_display_name: str = "",
                    problem: str = "", change: str = "", measurable_result: str = "", display_order: Optional[int] = None,
                    approval_evidence: str = "") -> str:
    """Add, change, make live or draft, or delete a proof case study."""
    action = (action or "").strip().lower()
    given = {"title": title, "client_display_name": client_display_name, "problem": problem, "change": change,
             "measurable_result": measurable_result}
    given = {k: " ".join(str(v).split()) if k in ("title", "client_display_name") else str(v).strip()
             for k, v in given.items() if str(v or "").strip()}
    if action == "add":
        if not given.get("title"):
            raise ToolError("a new case study needs a title.")
        study = AssessmentCaseStudy(
            id=uuid.uuid4(), title=given["title"], client_display_name=given.get("client_display_name"),
            problem=given.get("problem", ""), change=given.get("change", ""),
            measurable_result=given.get("measurable_result", ""), display_order=_order(display_order),
            enabled=False, content_status=CaseStudyContentStatus.APPROVED.value)
        call.db.add(study)
        call.db.commit()
        return f"Added case study {study.title!r} as a draft. Make it live to show it to prospects."
    if action not in ("edit", "go_live", "make_draft", "delete"):
        raise ToolError("action must be add, edit, go_live, make_draft or delete.")
    study = _pick(AcquisitionJourneyService(call.db).list_all_case_studies(), which,
                  lambda s: (s.title, s.client_display_name or ""))
    if action == "edit":
        if not given and display_order is None:
            raise ToolError("nothing to change. Give the fields to change.")
        for k, v in given.items():
            setattr(study, k, v)
        if display_order is not None:
            study.display_order = _order(display_order)
        call.db.add(study)
        call.db.commit()
        return f"Updated case study {study.title!r}: " + ", ".join([*given, *(["display_order"] if display_order is not None else [])]) + "."
    if action == "make_draft":
        study.enabled = False
        call.db.add(study)
        call.db.commit()
        return f"Case study {study.title!r} is now a draft (hidden from prospects)."
    verb = "delete" if action == "delete" else "make live"
    extra = " Deleting cannot be undone." if action == "delete" else " Prospects will see it."
    stop = call.confirmed(action_key(f"case_{action}", str(study.id)), approval_evidence,
                          f"{verb.capitalize()} the case study {study.title!r}?{extra}")
    if stop:
        return stop
    if action == "delete":
        call.db.delete(study)
        call.db.commit()
        return f"Deleted case study {study.title!r}."
    study.enabled = True
    study.content_status = CaseStudyContentStatus.APPROVED.value
    call.db.add(study)
    call.db.commit()
    return f"Case study {study.title!r} is now live."


_YOUTUBE = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/\S+$", re.I)


@tool("edit_video_link", "action", "which", "title", "url", "display_order", "approval_evidence")
def edit_video_link(call: Call, action: str = "", which: str = "", title: str = "", url: str = "",
                    display_order: Optional[int] = None, approval_evidence: str = "") -> str:
    """Add, change, make live or draft, or delete a YouTube link shown after booking."""
    action = (action or "").strip().lower()
    title, url = " ".join(title.split()), (url or "").strip()
    if url and not _YOUTUBE.match(url):
        raise ToolError("the link must be a YouTube address (youtube.com or youtu.be).")
    if action == "add":
        if not title or not url:
            raise ToolError("a new video link needs a title and a YouTube link.")
        link = AfterBookingYoutubeLink(id=uuid.uuid4(), title=title, youtube_url=url, display_order=_order(display_order),
                                       enabled=False, content_status=CaseStudyContentStatus.TEST.value)
        call.db.add(link)
        call.db.commit()
        return f"Added video link {title!r} as a draft. Make it live to show it after booking."
    if action not in ("edit", "go_live", "make_draft", "delete"):
        raise ToolError("action must be add, edit, go_live, make_draft or delete.")
    link = _pick(AcquisitionJourneyService(call.db).list_all_youtube_links(), which, lambda y: (y.title,))
    if action == "edit":
        if not (title or url or display_order is not None):
            raise ToolError("nothing to change. Give a new title, link or order.")
        link.title = title or link.title
        link.youtube_url = url or link.youtube_url
        if display_order is not None:
            link.display_order = _order(display_order)
        call.db.add(link)
        call.db.commit()
        return f"Updated video link {link.title!r}."
    if action == "make_draft":
        link.enabled = False
        call.db.add(link)
        call.db.commit()
        return f"Video link {link.title!r} is now a draft (hidden)."
    if action == "delete":
        stop = call.confirmed(action_key("video_delete", str(link.id)), approval_evidence,
                              f"Delete the video link {link.title!r}?")
        if stop:
            return stop
        call.db.delete(link)
        call.db.commit()
        return f"Deleted video link {link.title!r}."
    link.enabled = True
    link.content_status = CaseStudyContentStatus.APPROVED.value
    call.db.add(link)
    call.db.commit()
    return f"Video link {link.title!r} is now live."


def _order(value: Optional[int]) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise ToolError("display_order must be a whole number.") from None


# ------------------------------------------------------------------------------------------------ publish
@tool("publish_journey", "approval_evidence")
def publish_journey_tool(call: Call, approval_evidence: str = "") -> str:
    """Make Proof, Demo, Value and Calendar live for prospects (the admin Publish button)."""
    real, has_video = readiness(call.db)
    studies = AcquisitionJourneyService(call.db).list_all_case_studies()
    hidden = sum(1 for s in studies if is_test_case_study(s))
    missing = _missing(bool(real), has_video)
    if missing:
        raise ToolError(f"cannot publish yet: it still needs {missing}. Nothing changed.")
    stop = call.confirmed(action_key("publish", [str(s.id) for s in real]), approval_evidence,
                          f"Publish the journey? Proof, Demo, Value and Calendar go live for every prospect, "
                          f"{len(real)} case stud{'y' if len(real) == 1 else 'ies'} become live"
                          + (f" and {hidden} test one(s) are hidden." if hidden else "."))
    if stop:
        return stop
    try:
        publish_journey(call.db)
    except JourneyPublishError as exc:
        raise ToolError(f"cannot publish yet: {exc}.") from exc
    return f"Published. Proof, Demo, Value and Calendar are live; {len(real)} case stud{'y is' if len(real) == 1 else 'ies are'} live."
