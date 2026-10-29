"""Assessments: status and report, approve/unapprove, rerun, reset, attendance and handoff.

The status reply is short on purpose: the result first, then a few one-line bullets. The full report
(strengths, concerns, quotes, opportunities) only comes back when the user asks for the details.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import CampaignProspect, ConsultationShowStatus, HandoffStatus, PurchaseOutcome
from acquisition.models.journey import QualificationStatus, empty_qualification
from acquisition.models.prospect import Prospect
from app.outreach import chicago, lookup, records
from app.outreach.confirm import action_key
from app.outreach.toolkit import Call, ToolError, tool

_GOOD = {"strong", "decision_maker", "likely", "high", "yes", "true"}
_BAD = {"low", "weak", "none", "unlikely", "no", "false"}
_QUALITIES = (  # (key in the stored qualification, label shown)
    ("icp_fit", "Company fit"),
    ("authority_role", "Decision-maker"),
    ("ability_to_pay", "Able to pay"),
    ("real_money_problem", "Real money problem"),
    ("openness_to_ai", "Openness to AI"),
    ("growth_change_readiness", "Ready to change"),
    ("budget_readiness", "Budget"),
)


def _words(value: Any) -> str:
    return str(value).replace("_", " ").strip()


def _clip(text: Any, n: int = 160) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "..."


# ------------------------------------------------------------------------------------------------ loading
def _link_for(call: Call, p: Prospect) -> CampaignProspect:
    link = records.link_row(call.db, p.id)
    if link is None or not link.assessment_token:
        raise ToolError(f"{p.executive_name} has no assessment link yet (they were never emailed).")
    return link


def _booked(link: CampaignProspect) -> bool:
    return (link.booking_status or "").strip().lower() == "confirmed" and link.booked_at is not None


def _profile(call: Call, link: CampaignProspect) -> Optional[AssessmentProfile]:
    return call.db.scalar(select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id))


def _finished(call: Call, who: str) -> tuple[Prospect, CampaignProspect, AssessmentProfile]:
    """A person whose assessment is finished and whose result can still be changed."""
    p = lookup.person(call, who)
    link = _link_for(call, p)
    profile = _profile(call, link)
    if profile is None or link.assessment_status.value != "completed":
        raise ToolError(f"{p.executive_name} has not finished the assessment, so there is no result to change.")
    if _booked(link):
        raise ToolError(f"{p.executive_name} already booked a call, so the result is locked.")
    if link.purchase_outcome == PurchaseOutcome.PURCHASED:
        raise ToolError(f"{p.executive_name} has already paid, so the result is locked.")
    return p, link, profile


# ------------------------------------------------------------------------------------------------ show
def _result_line(s: dict) -> str:
    qstatus = str(s.get("qualification_status") or "").strip().lower()
    status = s["assessment_status"]
    if status == "completed" and qstatus and qstatus not in ("pending", "none", "not_assessed"):
        return f"{qstatus.capitalize()}, assessment finished {chicago.fmt_day(s.get('completed_at'))}"
    if status == "completed":
        return "Assessment finished, result not decided yet"
    if status == "started":
        return f"In progress, started {chicago.fmt_day(s.get('started_at'))}"
    if s.get("page_opened_at"):
        return "Opened the assessment page, not started"
    return "Has not opened the assessment page"


def _progress_lines(s: dict, em: dict) -> list[str]:
    opened = f"; page opened {chicago.fmt_day(s['page_opened_at'])}" if s.get("page_opened_at") else "; page not opened"
    booking = s["booking_status"] if s["booking_status"] not in ("", None) else "not_booked"
    when = f", call {chicago.fmt_time(s['meeting_start'])} Chicago time" if s.get("meeting_start") else ""
    return [f"- Emails: {em['sent']} sent, {em['queued']} waiting{opened}", f"- Booking: {_words(booking)}{when}"]


def short_report(p: Prospect, s: dict, em: dict) -> str:
    """The result first, then up to five one-line bullets."""
    q, res = s.get("qualification") or {}, s.get("result") or {}
    finished = s["assessment_status"] == "completed"
    lines = [_result_line(s), f"{p.executive_name} <{p.email}>"]
    manual = "admin override" in str(q.get("notes") or "").lower()
    if manual:
        lines.append("- Why: set by an admin" + (f" (the AI said: {_clip(q['explanation'], 110)})" if q.get("explanation") else ""))
    elif q.get("explanation"):
        lines.append(f"- Why: {_clip(q['explanation'], 150)}")
    if finished and res.get("inefficiencies_summary"):
        lines.append(f"- Problem: {_clip(res['inefficiencies_summary'], 130)}")
    if finished and res.get("observed_impact"):
        lines.append(f"- Cost: {_clip(res['observed_impact'], 110)}")
    if not finished:
        lines.append(f"- Stage: {_words(s['journey_stage'])}")
    return "\n".join(lines + _progress_lines(s, em))


def full_report(p: Prospect, s: dict, em: dict, include_brief: bool) -> str:
    """Everything: reasoning, strengths, concerns, what they told us, progress, and optionally the pre-call brief."""
    q, res = s.get("qualification") or {}, s.get("result") or {}
    finished = s["assessment_status"] == "completed"
    lines = [_result_line(s), f"{p.executive_name} <{p.email}>"]
    manual = "admin override" in str(q.get("notes") or "").lower()
    if manual:
        lines.append("Set manually by an admin (the AI's own reasoning below may point the other way).")
    if q.get("explanation"):
        lines.append(f"{'AI reasoning' if manual else 'Why'}: {_clip(q['explanation'], 400)}")
    strengths, concerns = [], []
    for key, label in _QUALITIES:
        val = q.get(key)
        if val in (None, "", "not_assessed"):
            if key == "budget_readiness" and val == "not_assessed":
                concerns.append("Budget not assessed yet")
            continue
        v = str(val).strip().lower()
        shown = "yes" if v in ("true", "decision_maker") else "no" if v == "false" else _words(val)
        text = f"{label}: {shown}"
        if v in _GOOD:
            strengths.append(text)
        elif v in _BAD:
            concerns.append(text)
    for d in q.get("disqualifiers") or []:
        quote = f" - \"{_clip(d['evidence_quote'], 110)}\"" if d.get("evidence_quote") else ""
        concerns.append(f"Disqualifier: {_words(d.get('code', ''))}{quote}")
    if strengths:
        lines += ["Strengths:"] + [f"- {x}" for x in strengths]
    if concerns:
        lines += ["Concerns:"] + [f"- {x}" for x in concerns]
    if finished and res:
        lines.append("What they told us:")
        if res.get("inefficiencies_summary"):
            lines.append(f"- Problem: {_clip(res['inefficiencies_summary'], 300)}")
        for a in [a.get("label") for a in (res.get("opportunity_areas") or []) if isinstance(a, dict) and a.get("label")][:3]:
            lines.append(f"- Opportunity: {_clip(a, 120)}")
        if res.get("observed_impact"):
            lines.append(f"- Impact: {_clip(res['observed_impact'], 200)}")
    lines.append("Progress:")
    lines += _progress_lines(s, em)
    lines.append(f"- Journey stage: {_words(s['journey_stage'])}")
    if s["assessment_status"] == "started":
        state = s.get("structured_state") or {}
        missing = state.get("missing_fields") or state.get("missing") or []
        if missing:
            lines.append("- Still unanswered: " + _clip(", ".join(map(str, missing)), 200))
    if s.get("pre_call_brief_status"):
        lines.append(f"- Pre-call brief: {s['pre_call_brief_status']}")
        if include_brief and s.get("brief_url"):
            lines.append(f"- Open the pre-call brief in a new tab: {s['brief_url']}")
    return "\n".join(lines)


@tool("show_assessment", "who", "details", "include_brief")
def show_assessment(call: Call, who: str = "", details: bool = False, include_brief: bool = False) -> str:
    """Where one person is in the assessment: short by default, the full report on request."""
    p = lookup.person(call, who)
    stage = records.assessment_stage(call.db, p.id)
    em = records.email_state(call.db, p.id)
    if not stage.get("has_link"):
        return f"{p.executive_name} <{p.email}> - not emailed yet. Emails: {em['sent']} sent, {em['queued']} waiting. No assessment link exists yet."
    if details or include_brief:
        return full_report(p, stage, em, include_brief)
    return short_report(p, stage, em)


# ------------------------------------------------------------------------------------------------ approve / rerun / reset
def _current(profile: AssessmentProfile) -> str:
    return getattr(profile.qualification_status, "value", str(profile.qualification_status))


@tool("set_qualification", "who", "result")
def set_qualification(call: Call, who: str = "", result: str = "") -> str:
    """Approve (qualified) or unapprove (unqualified) a finished assessment, like the admin switch."""
    from acquisition.services.acquisition_journey_service import AcquisitionJourneyService

    result = (result or "").strip().lower()
    if result not in ("qualified", "unqualified"):
        raise ToolError("result must be 'qualified' (approve) or 'unqualified' (unapprove)")
    p, link, profile = _finished(call, who)
    current = _current(profile)
    if current == result:
        return f"{p.executive_name} is already {result}. Nothing changed."
    new_status = QualificationStatus(result)
    qual = dict(profile.qualification or empty_qualification())
    qual["status"] = qual["assessment_status"] = new_status.value
    note = f"Admin override: set to {new_status.value} (was {current})."
    old_notes = str(qual.get("notes") or "").strip()
    qual["notes"] = f"{old_notes}\n{note}".strip() if old_notes else note
    profile.qualification = qual
    profile.qualification_status = new_status
    flag_modified(profile, "qualification")
    call.db.add(profile)
    call.db.commit()
    call.db.refresh(profile)
    call.db.refresh(link)
    AcquisitionJourneyService(call.db).apply_post_assessment_branch(link, profile)
    return f"{p.executive_name} is now {result} (was {current})."


@tool("rerun_qualification", "who", "approval_evidence")
def rerun_qualification(call: Call, who: str = "", approval_evidence: str = "") -> str:
    """Run the AI qualification again on a finished assessment (the admin 'Re-run' button)."""
    from acquisition.services.acquisition_journey_service import AcquisitionJourneyService
    from acquisition.services.assessment_chat_service import AssessmentChatService

    p, link, profile = _finished(call, who)
    current = _current(profile)
    key = action_key("requalify", str(p.id))
    stop = call.confirmed(key, approval_evidence,
                          f"Re-run the AI qualification for {p.executive_name} <{p.email}>? It uses the current prompt and may "
                          f"change the result (now {current}), including a manual approval.")
    if stop:
        return stop
    conversation = call.db.scalar(
        select(AssessmentConversation).where(AssessmentConversation.campaign_prospect_id == link.id)
        .options(selectinload(AssessmentConversation.messages)))
    if conversation is None:
        raise ToolError(f"{p.executive_name} has no assessment conversation to re-run.")
    AssessmentChatService(call.db).completion.requalify(link, conversation, profile, preserve_journey=False)
    AcquisitionJourneyService(call.db).apply_post_assessment_branch(link, profile)
    call.db.refresh(profile)
    return f"Re-ran the qualification for {p.executive_name}: now {_current(profile)} (was {current})."


@tool("reset_journey", "who", "approval_evidence")
def reset_journey(call: Call, who: str = "", approval_evidence: str = "") -> str:
    """Rewind one person to the start: assessment, booking and payment offer are cleared."""
    from acquisition.services.assessment_journey_reset import reset_assessment_journey

    p = lookup.person(call, who)
    link = _link_for(call, p)
    if link.purchase_outcome == PurchaseOutcome.PURCHASED:
        raise ToolError(f"{p.executive_name} has paid. Resetting would erase the payment record, so this is not allowed.")
    key = action_key("reset", str(p.id))
    stop = call.confirmed(key, approval_evidence,
                          f"Reset {p.executive_name} <{p.email}> to the very start? Their assessment answers, result, "
                          "booking (the Cal.com call is cancelled) and payment offer are cleared. Their email history stays.")
    if stop:
        return stop
    reset_assessment_journey(call.db, link)
    return f"Reset {p.executive_name}: assessment, booking and offer are cleared. They can start again from their link."


# ------------------------------------------------------------------------------------------------ attendance / handoff
@tool("record_consultation", "who", "outcome")
def record_consultation(call: Call, who: str = "", outcome: str = "") -> str:
    """Record whether a booked person showed up to the consultation."""
    from datetime import datetime, timezone

    try:
        status = ConsultationShowStatus(_key(outcome))
    except ValueError:
        raise ToolError("outcome must be showed, no_show or not_recorded") from None
    p = lookup.person(call, who)
    link = _link_for(call, p)
    if not _booked(link):
        raise ToolError(f"{p.executive_name} has no confirmed booking, so attendance cannot be recorded.")
    link.consultation_show_status = status
    link.consultation_show_recorded_at = None if status == ConsultationShowStatus.NOT_RECORDED else datetime.now(timezone.utc)
    call.db.add(link)
    call.db.commit()
    return f"Recorded {p.executive_name} as {_words(status.value)}."


@tool("set_handoff_status", "who", "status")
def set_handoff_status(call: Call, who: str = "", status: str = "") -> str:
    """Set where the delivery handoff to Cepoch stands for one person."""
    from acquisition.services.jeanne_checkout_service import JeanneCheckoutError, mark_handoff

    try:
        wanted = HandoffStatus(_key(status))
    except ValueError:
        raise ToolError("status must be not_started, ready or handed_off") from None
    p = lookup.person(call, who)
    link = _link_for(call, p)
    try:
        mark_handoff(call.db, link, status=wanted)
    except JeanneCheckoutError as exc:
        raise ToolError(exc.message) from exc
    return f"Handoff for {p.executive_name} is now {_words(wanted.value)}."


def _key(text: str) -> str:
    return "_".join((text or "").strip().lower().replace("-", " ").split())
