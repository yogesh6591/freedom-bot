"""Post-assessment acquisition journey: proof → demo → value → calendar gates."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.acquisition import AssessmentCaseStudy, JourneySettings
from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import AssessmentStatus, CampaignProspect, JourneyStage
from acquisition.models.journey import QualificationStatus
from acquisition.services.assessment_service import AssessmentEventError, AssessmentEventService
from acquisition.services.personalized_value_service import PersonalizedValueService

logger = logging.getLogger(__name__)

# Calendar hold polls every 500ms with sync=1; do not hit Cal.com on every tick.
_CALCOM_SYNC_COOLDOWN_SEC = 2.0
_last_calcom_sync: dict[str, float] = {}

_BUDGET_OUTCOMES = frozenset({"ready", "not_ready", "unclear", "not_assessed"})

# Admin/internal phrasing that must never appear on the public Proof page.
_PUBLIC_PROOF_BLOCKLIST = (
    "placeholder",
    "replace before production",
    "delete or replace",
    "from admin",
    "journey testing",
    "not a real",
    "not an approved",
    "no production claim",
    "test case study",
    "for testing only",
    "layout testing",
)


def _looks_like_internal_proof_copy(text: str | None) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return True
    return any(marker in raw for marker in _PUBLIC_PROOF_BLOCKLIST)


def _result_items(measurable_result: str | None) -> list[str]:
    raw = (measurable_result or "").strip()
    if not raw:
        return []
    lines = [ln.strip().lstrip("-•").strip() for ln in raw.splitlines() if ln.strip()]
    return lines if lines else [raw]


def _public_case_study_view(study: AssessmentCaseStudy, *, test_index: int) -> dict[str, Any]:
    """Prospect-facing case study fields. Admin keeps raw titles/status elsewhere."""
    status = str(getattr(study, "content_status", "") or "").strip().lower()
    if status == "test":
        label = "Illustrative Example" if test_index == 0 else f"Illustrative Example {test_index + 1}"
        problem = None if _looks_like_internal_proof_copy(study.problem) else (study.problem or None)
        change = None if _looks_like_internal_proof_copy(study.change) else (study.change or None)
        result = (
            None
            if _looks_like_internal_proof_copy(study.measurable_result)
            else (study.measurable_result or None)
        )
        # Neutral illustrative copy when stored TEST body is Admin-facing.
        if problem is None:
            problem = (
                "Sales-to-delivery details often live in too many places, so handoffs "
                "create rework and missed expectations."
            )
        if change is None:
            change = (
                "A clearer operating rhythm around handoffs, pricing guidance, and "
                "decision ownership reduces friction."
            )
        if result is None:
            result = (
                "Teams spend less time chasing context and more time delivering "
                "predictably. This is an illustrative example, not a named client result."
            )
        return {
            "id": str(study.id),
            "title": label,
            "client_display_name": None,
            "problem": problem,
            "change": change,
            "measurable_result": result,
            "result_items": _result_items(result),
            "is_illustrative": True,
        }
    result_text = study.measurable_result or ""
    return {
        "id": str(study.id),
        "title": study.title,
        "client_display_name": study.client_display_name,
        "problem": study.problem,
        "change": study.change,
        "measurable_result": result_text,
        "result_items": _result_items(result_text),
        "is_illustrative": False,
    }


class AcquisitionJourneyError(Exception):
    def __init__(self, message: str, *, http_status: int = 403, code: str = "gate") -> None:
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code


def assessment_qualification_status(profile: AssessmentProfile | None) -> str:
    if profile is None:
        return QualificationStatus.PENDING.value
    qual = profile.qualification if isinstance(profile.qualification, dict) else {}
    raw = (
        profile.qualification_status.value
        if profile.qualification_status is not None
        else None
    )
    if not raw:
        raw = qual.get("assessment_status") or qual.get("status")
    return str(raw or QualificationStatus.PENDING.value).strip().lower()


def budget_readiness_of(profile: AssessmentProfile | None) -> str:
    if profile is None or not isinstance(profile.qualification, dict):
        return "not_assessed"
    raw = str(profile.qualification.get("budget_readiness") or "not_assessed").strip().lower()
    return raw if raw in _BUDGET_OUTCOMES else "not_assessed"


def economic_capacity_of(profile: AssessmentProfile | None) -> str | None:
    if profile is None or not isinstance(profile.qualification, dict):
        return None
    raw = profile.qualification.get("economic_capacity")
    if raw is None:
        raw = profile.qualification.get("ability_to_pay")
    if raw is None:
        return None
    return str(raw).strip().lower() or None


def is_calendar_eligible(
    *,
    link: CampaignProspect,
    profile: AssessmentProfile | None,
) -> bool:
    """Backend calendar gate — never unlock from assessment qualification alone.

    calendar_eligible = true ONLY when:
    - assessment_status = qualified
    - proof_viewed_at set
    - demo_completed_at set
    - value_context_completed_at set
    - budget_readiness = ready
    """
    if assessment_qualification_status(profile) != QualificationStatus.QUALIFIED.value:
        return False
    if link.proof_viewed_at is None:
        return False
    if link.demo_completed_at is None:
        return False
    if link.value_context_completed_at is None:
        return False
    return budget_readiness_of(profile) == "ready"


def record_calendar_unlocked_once(link: CampaignProspect, *, eligible: bool) -> bool:
    """Persist calendar_unlocked_at the first time eligibility is true. Never overwrite."""
    if not eligible:
        return False
    if getattr(link, "calendar_unlocked_at", None) is not None:
        return False
    link.calendar_unlocked_at = datetime.now(timezone.utc)
    return True


class AcquisitionJourneyService:
    def __init__(self, db: Session, *, events: AssessmentEventService | None = None) -> None:
        self.db = db
        self.events = events or AssessmentEventService(db)

    # --- settings / content -------------------------------------------------

    def get_settings(self) -> JourneySettings:
        from acquisition.models.acquisition import DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT

        row = self.db.get(JourneySettings, 1)
        if row is None:
            row = JourneySettings(
                id=1,
                demo_enabled=False,
                demo_require_watch=True,
                value_enabled=False,
                calendar_enabled=False,
                after_booking_video_script=DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        elif not (row.after_booking_video_script or "").strip():
            row.after_booking_video_script = DEFAULT_AFTER_BOOKING_VIDEO_SCRIPT
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    def list_enabled_case_studies(self) -> list[AssessmentCaseStudy]:
        from acquisition.config import get_settings
        from acquisition.models.acquisition import CaseStudyContentStatus

        stmt = select(AssessmentCaseStudy).where(AssessmentCaseStudy.enabled.is_(True))
        # Production never surfaces test-labeled case studies to prospects.
        if get_settings().environment.strip().lower() == "production":
            stmt = stmt.where(
                AssessmentCaseStudy.content_status == CaseStudyContentStatus.APPROVED.value
            )
        stmt = stmt.order_by(
            AssessmentCaseStudy.display_order.asc(), AssessmentCaseStudy.created_at.asc()
        )
        rows = list(self.db.scalars(stmt).all())
        # When any approved studies are enabled, prefer those (no matching library yet —
        # just avoid mixing TEST placeholders into the default proof experience).
        approved = [
            r
            for r in rows
            if str(getattr(r, "content_status", "") or "").lower()
            == CaseStudyContentStatus.APPROVED.value
        ]
        return approved if approved else rows

    def list_all_case_studies(self) -> list[AssessmentCaseStudy]:
        stmt = select(AssessmentCaseStudy).order_by(
            AssessmentCaseStudy.display_order.asc(), AssessmentCaseStudy.created_at.asc()
        )
        return list(self.db.scalars(stmt).all())

    def public_booking_url(self, settings: JourneySettings | None = None) -> str | None:
        """Resolved public Cal.com booking URL (non-secret). Prefer selected event."""
        row = settings or self.get_settings()
        for candidate in (row.calcom_booking_url, row.calendar_url):
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return None

    def calendar_lock_reasons(
        self, link: CampaignProspect, profile: AssessmentProfile | None
    ) -> list[str]:
        """Admin-only explanation of why calendar is locked."""
        if is_calendar_eligible(link=link, profile=profile):
            return []
        reasons: list[str] = []
        status = assessment_qualification_status(profile)
        if status != QualificationStatus.QUALIFIED.value:
            reasons.append(f"Assessment status is {status} (needs qualified)")
        if link.proof_viewed_at is None:
            reasons.append("Proof not completed/viewed")
        if link.demo_completed_at is None:
            if link.demo_started_at is None:
                reasons.append("Demo not started")
            else:
                reasons.append("Demo not completed")
        elif bool(self.get_settings().demo_require_watch) and link.demo_watch_completed_at is None:
            reasons.append("Demo full watch not completed")
        if link.value_context_completed_at is None:
            reasons.append("Value context not completed")
        budget = budget_readiness_of(profile)
        if budget != "ready":
            reasons.append(f"Budget readiness is {budget} (needs ready)")
        return reasons
    # --- loaders ------------------------------------------------------------

    def resolve(self, token: str) -> tuple[CampaignProspect, AssessmentProfile]:
        link = self.events.resolve_link(token)
        profile = self.db.scalar(
            select(AssessmentProfile).where(AssessmentProfile.campaign_prospect_id == link.id)
        )
        if profile is None:
            raise AcquisitionJourneyError("Assessment profile missing", http_status=404, code="missing")
        return link, profile

    def require_assessment_completed(self, link: CampaignProspect) -> None:
        if link.assessment_status != AssessmentStatus.COMPLETED:
            raise AcquisitionJourneyError(
                "Assessment is not complete yet",
                http_status=403,
                code="assessment_incomplete",
            )

    # --- gate checks --------------------------------------------------------

    def can_access_proof(self, link: CampaignProspect, profile: AssessmentProfile) -> bool:
        self.require_assessment_completed(link)
        return assessment_qualification_status(profile) == QualificationStatus.QUALIFIED.value

    def can_access_demo(self, link: CampaignProspect, profile: AssessmentProfile) -> bool:
        return self.can_access_proof(link, profile) and link.proof_viewed_at is not None

    def demo_is_complete_for_value(
        self, link: CampaignProspect, settings: JourneySettings | None = None
    ) -> bool:
        """Value unlocks after demo completion; full-watch also requires watch attestation."""
        if link.demo_completed_at is None:
            return False
        row = settings or self.get_settings()
        if bool(row.demo_require_watch):
            return link.demo_watch_completed_at is not None
        return True

    def can_access_value(self, link: CampaignProspect, profile: AssessmentProfile) -> bool:
        return self.can_access_demo(link, profile) and self.demo_is_complete_for_value(link)

    def can_access_calendar(self, link: CampaignProspect, profile: AssessmentProfile) -> bool:
        return is_calendar_eligible(link=link, profile=profile)

    # --- branch after assessment --------------------------------------------

    def apply_post_assessment_branch(
        self, link: CampaignProspect, profile: AssessmentProfile
    ) -> JourneyStage:
        """Map qualification to acquisition stage (does not unlock calendar)."""
        status = assessment_qualification_status(profile)
        if status == QualificationStatus.UNQUALIFIED.value:
            link.journey_stage = JourneyStage.UNQUALIFIED_EXIT
        elif status == QualificationStatus.PENDING.value:
            link.journey_stage = JourneyStage.PENDING_REVIEW
        elif status == QualificationStatus.QUALIFIED.value:
            # Stay at qualified until they enter proof; never regress later stages.
            if link.journey_stage not in {
                JourneyStage.PROOF,
                JourneyStage.DEMO,
                JourneyStage.VALUE,
                JourneyStage.CALENDAR_UNLOCKED,
                JourneyStage.BOOKED,
            }:
                link.journey_stage = JourneyStage.QUALIFIED
        else:
            link.journey_stage = JourneyStage.ASSESSMENT_COMPLETED
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return link.journey_stage

    # --- stage actions ------------------------------------------------------

    def enter_proof(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_proof(link, profile):
            raise AcquisitionJourneyError(
                "Proof is only available after assessment qualification",
                code="proof_locked",
            )
        if link.journey_stage in {JourneyStage.QUALIFIED, JourneyStage.ASSESSMENT_COMPLETED}:
            link.journey_stage = JourneyStage.PROOF
            link.last_activity_at = datetime.now(timezone.utc)
            self.db.add(link)
            self.db.commit()
            self.db.refresh(link)
        studies = self.list_enabled_case_studies()
        public_studies: list[dict[str, Any]] = []
        test_index = 0
        for s in studies:
            public_studies.append(_public_case_study_view(s, test_index=test_index))
            if str(getattr(s, "content_status", "") or "").lower() == "test":
                test_index += 1
        return {
            "token": token,
            "journey_stage": link.journey_stage.value,
            "case_studies": public_studies,
            "proof_ready": bool(public_studies),
            "proof_viewed_at": link.proof_viewed_at.isoformat() if link.proof_viewed_at else None,
            "can_continue_to_demo": True,
        }

    def complete_proof(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_proof(link, profile):
            raise AcquisitionJourneyError("Proof is locked", code="proof_locked")
        now = datetime.now(timezone.utc)
        if link.proof_viewed_at is None:
            link.proof_viewed_at = now
        link.journey_stage = JourneyStage.PROOF
        link.last_activity_at = now
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return {"ok": True, "proof_viewed_at": link.proof_viewed_at.isoformat(), "next": "demo"}

    def enter_demo(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_demo(link, profile):
            raise AcquisitionJourneyError(
                "Demo unlocks after the proof stage is completed",
                code="demo_locked",
            )
        settings = self.get_settings()
        now = datetime.now(timezone.utc)
        # Opening marks started, never completed.
        if link.demo_started_at is None:
            link.demo_started_at = now
        link.journey_stage = JourneyStage.DEMO
        link.last_activity_at = now
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        video_url = None
        if settings.demo_enabled and settings.demo_video_url:
            video_url = settings.demo_video_url.strip() or None
        embed_kind = "none"
        if video_url:
            from acquisition.services.demo_video_storage import demo_player

            video_url, embed_kind = demo_player(video_url)
        return {
            "token": token,
            "journey_stage": link.journey_stage.value,
            "demo_title": (settings.demo_title or "").strip() or "FreedomBot Demo",
            "demo_description": (settings.demo_description or "").strip() or None,
            "demo_video_url": video_url,
            "demo_embed_kind": embed_kind,
            "demo_ready": bool(video_url),
            "demo_require_watch": bool(settings.demo_require_watch),
            "demo_started_at": link.demo_started_at.isoformat() if link.demo_started_at else None,
            "demo_completed_at": (
                link.demo_completed_at.isoformat() if link.demo_completed_at else None
            ),
            "demo_watch_completed_at": (
                link.demo_watch_completed_at.isoformat()
                if link.demo_watch_completed_at
                else None
            ),
            "can_continue_to_value": self.demo_is_complete_for_value(link, settings),
        }

    def complete_demo(self, token: str, *, watched: bool = False) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_demo(link, profile):
            raise AcquisitionJourneyError("Demo is locked", code="demo_locked")
        settings = self.get_settings()
        if bool(settings.demo_require_watch) and not watched:
            raise AcquisitionJourneyError(
                "Watch the full demo to continue",
                http_status=403,
                code="demo_watch_required",
            )
        now = datetime.now(timezone.utc)
        if link.demo_started_at is None:
            link.demo_started_at = now
        link.demo_completed_at = now
        if watched:
            link.demo_watch_completed_at = now
        link.journey_stage = JourneyStage.DEMO
        link.last_activity_at = now
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return {
            "ok": True,
            "demo_completed_at": link.demo_completed_at.isoformat(),
            "demo_watch_completed_at": (
                link.demo_watch_completed_at.isoformat()
                if link.demo_watch_completed_at
                else None
            ),
            "next": "value",
        }

    def enter_value(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_value(link, profile):
            raise AcquisitionJourneyError(
                "Value context unlocks after demo completion",
                code="value_locked",
            )
        settings = self.get_settings()
        if link.journey_stage != JourneyStage.CALENDAR_UNLOCKED:
            link.journey_stage = JourneyStage.VALUE
            link.last_activity_at = datetime.now(timezone.utc)
            self.db.add(link)
            self.db.commit()
            self.db.refresh(link)

        personalized_value = None
        if profile is not None:
            conversation = self.db.scalar(
                select(AssessmentConversation).where(
                    AssessmentConversation.campaign_prospect_id == link.id
                )
            )
            personalized_value = PersonalizedValueService(self.db).ensure_for_profile(
                link, profile, conversation
            )

        investment = (settings.value_investment_text or "").strip() if settings.value_enabled else ""
        legacy_html = (settings.value_context_html or "").strip() if settings.value_enabled else ""
        heading = (settings.value_heading or "").strip()
        # Investment/price wording is Admin-only. Personalized value is prospect-specific.
        has_investment = bool(investment or legacy_html)
        value_ready = bool(
            settings.value_enabled and (personalized_value or has_investment or heading)
        )
        return {
            "token": token,
            "journey_stage": link.journey_stage.value,
            "value_enabled": bool(settings.value_enabled),
            "value_ready": value_ready,
            "value_heading": heading or "What this could mean for your business",
            "personalized_value": personalized_value,
            "value_investment_text": investment or None,
            "value_context_html": legacy_html or None,
            # Legacy admin fields retained in settings but not required on public Value.
            "value_intro": None,
            "value_supporting_note": None,
            "budget_question": (settings.budget_question or "").strip()
            or (
                "Would you be comfortable discussing a customized implementation and "
                "investment with Jeanne?"
            ),
            "budget_label_ready": (settings.budget_label_ready or "").strip()
            or "Yes, I’m ready to discuss next steps",
            "budget_label_unclear": (settings.budget_label_unclear or "").strip()
            or "I’m interested, but I need more context",
            "budget_label_not_ready": (settings.budget_label_not_ready or "").strip()
            or "Not right now",
            "continue_button_text": (settings.value_continue_button_text or "").strip()
            or "Continue",
            "value_context_completed_at": (
                link.value_context_completed_at.isoformat()
                if link.value_context_completed_at
                else None
            ),
            "budget_readiness": budget_readiness_of(profile),
            "economic_capacity": economic_capacity_of(profile),
            "calendar_eligible": is_calendar_eligible(link=link, profile=profile),
            # Never include calendar_url / event ids here.
        }

    def complete_value(self, token: str, *, budget_readiness: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_value(link, profile):
            raise AcquisitionJourneyError("Value stage is locked", code="value_locked")
        outcome = str(budget_readiness or "").strip().lower()
        if outcome not in {"ready", "not_ready", "unclear"}:
            raise AcquisitionJourneyError(
                "budget_readiness must be ready, not_ready, or unclear",
                http_status=422,
                code="invalid_budget",
            )
        # Never infer ready from economic_capacity / revenue.
        qual = dict(profile.qualification) if isinstance(profile.qualification, dict) else {}
        qual["budget_readiness"] = outcome
        reasons = qual.get("signal_reasons")
        reasons = dict(reasons) if isinstance(reasons, dict) else {}
        reasons["budget_readiness"] = {
            "ready": "Prospect said they are ready to discuss next steps on the value step.",
            "unclear": "Prospect wants more context before discussing investment.",
            "not_ready": "Prospect said not right now on the value step.",
        }[outcome]
        qual["signal_reasons"] = reasons
        profile.qualification = qual
        now = datetime.now(timezone.utc)
        link.value_context_completed_at = now
        link.last_activity_at = now
        eligible = is_calendar_eligible(link=link, profile=profile)
        if eligible:
            link.journey_stage = JourneyStage.CALENDAR_UNLOCKED
            record_calendar_unlocked_once(link, eligible=True)
        else:
            link.journey_stage = JourneyStage.VALUE
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(profile, "qualification")
        self.db.add(profile)
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        self.db.refresh(profile)
        payload: dict[str, Any] = {
            "ok": True,
            "budget_readiness": outcome,
            "value_context_completed_at": link.value_context_completed_at.isoformat(),
            "calendar_eligible": eligible,
            "journey_stage": link.journey_stage.value,
            "next": "calendar" if eligible else None,
        }
        # Never attach calendar_url even when eligible — calendar page loads it.
        return payload

    def enter_calendar(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        if not self.can_access_calendar(link, profile):
            raise AcquisitionJourneyError(
                "Calendar is not available yet",
                code="calendar_locked",
            )
        settings = self.get_settings()
        booking_url = self.public_booking_url(settings)
        if not settings.calendar_enabled or not booking_url:
            raise AcquisitionJourneyError(
                "Calendar booking is not configured yet",
                http_status=503,
                code="calendar_unconfigured",
            )
        from acquisition.services.booking_service import (
            BookingService,
            build_calendar_embed_url,
            calendar_embed_parts,
            calendar_success_url,
        )
        from acquisition.integrations.calcom.client import CalcomClient, CalcomError
        from acquisition.timezones import platform_timezone_name

        # Keep Cal.com host on platform timezone + 12h clock; hide guest UI.
        host_email: str | None = None
        try:
            client = CalcomClient(db=self.db)
            if client.configured:
                me = client.ensure_timezone(platform_timezone_name())
                try:
                    me = client.ensure_time_format(12) or me
                except CalcomError:
                    logger.warning("Could not sync Cal.com host timeFormat", exc_info=False)
                host_email = (me.get("email") or "").strip() or None
                event_id = (getattr(settings, "calcom_event_type_id", None) or "").strip()
                if event_id:
                    try:
                        client.ensure_event_booker_defaults(event_id)
                    except CalcomError:
                        logger.warning(
                            "Could not apply Cal.com booker defaults on event %s",
                            event_id,
                            exc_info=False,
                        )
        except CalcomError:
            logger.warning("Could not sync Cal.com host timezone", exc_info=False)
        except Exception:  # noqa: BLE001
            logger.warning("Could not sync Cal.com host timezone", exc_info=False)

        if link.journey_stage != JourneyStage.BOOKED:
            link.journey_stage = JourneyStage.CALENDAR_UNLOCKED
            link.last_activity_at = datetime.now(timezone.utc)
            record_calendar_unlocked_once(
                link, eligible=is_calendar_eligible(link=link, profile=profile)
            )
            self.db.add(link)
            self.db.commit()
            self.db.refresh(link)

        prospect = getattr(link, "prospect", None)
        if prospect is None and getattr(link, "prospect_id", None) is not None:
            from acquisition.models.prospect import Prospect

            prospect = self.db.get(Prospect, link.prospect_id)
        guest_name = (getattr(prospect, "executive_name", None) or "").strip() or None
        guest_email = (getattr(prospect, "email", None) or "").strip() or None

        from acquisition.config import get_settings

        cc_emails: list[str] = []
        if host_email:
            cc_emails.append(host_email)
        configured_cc = (get_settings().calcom_booking_cc_emails or "").strip()
        if configured_cc:
            cc_emails.extend(part.strip() for part in configured_cc.split(",") if part.strip())

        embed_url = build_calendar_embed_url(
            booking_url=booking_url,
            token=token,
            guest_name=guest_name,
            guest_email=guest_email,
            cc_emails=cc_emails,
        )
        parts = calendar_embed_parts(booking_url)
        booked = BookingService(self.db).is_booking_confirmed(link)
        # Eligible only: expose booking URL — never API key or unused event internals beyond URL.
        return {
            "token": token,
            "journey_stage": link.journey_stage.value,
            "calendar_eligible": True,
            "calendar_url": embed_url,
            "cal_link": parts["cal_link"] or None,
            "cal_origin": parts["cal_origin"],
            "guest_name": guest_name,
            "guest_email": guest_email,
            "success_url": calendar_success_url(token) or None,
            "event_title": (settings.calcom_event_title or "").strip() or None,
            "booking_confirmed": booked,
            "booked_page_url": f"/a/{token}/booked" if booked else None,
        }

    def can_access_booked(self, link: CampaignProspect, profile: AssessmentProfile) -> bool:
        from acquisition.services.booking_service import BookingService

        return BookingService(self.db).is_booking_confirmed(link)

    def load_booked_page(self, token: str, *, uid: str | None = None) -> dict[str, Any]:
        """Welcome page payload. If Cal.com already booked, confirm before rendering."""
        try:
            payload = self.enter_booked(token)
            payload.setdefault("confirming", False)
            return payload
        except AcquisitionJourneyError:
            link, profile = self.resolve(token)
            if not self.can_access_calendar(link, profile):
                raise
            self.booking_status_payload(token, sync=True, uid=uid)
            try:
                payload = self.enter_booked(token)
                payload.setdefault("confirming", False)
                return payload
            except AcquisitionJourneyError:
                return {
                    "confirming": True,
                    "token": token,
                    "booking_status": getattr(link, "booking_status", "not_booked"),
                }

    def enter_booked(self, token: str) -> dict[str, Any]:
        link, profile = self.resolve(token)
        from acquisition.services.booking_service import BookingService

        booking = BookingService(self.db)
        if not booking.is_booking_confirmed(link):
            raise AcquisitionJourneyError(
                "Booking is not confirmed yet",
                http_status=403,
                code="booking_required",
            )
        settings = self.get_settings()
        youtube = self.list_enabled_youtube_links()
        video_url = (settings.after_booking_video_url or "").strip() or None
        video_enabled = bool(settings.after_booking_video_enabled) and bool(video_url)
        embed_kind = "none"
        if video_url:
            from acquisition.services.demo_video_storage import demo_player

            video_url, embed_kind = demo_player(video_url)
        from acquisition.time_format import format_meeting_card, split_prep_blocks
        from acquisition.timezones import platform_timezone_name

        meeting_card = format_meeting_card(
            start=link.meeting_start,
            end=link.meeting_end,
            timezone_name=platform_timezone_name(),
        )
        join_url = (link.meeting_join_url or "").strip() or None
        location = (link.meeting_location or "").strip() or None
        if not join_url and location and location.lower().startswith(("http://", "https://")):
            join_url = location
            location = None
        # Never surface raw URLs as "location" text on the public page.
        if location and location.lower().startswith(("http://", "https://")):
            if not join_url:
                join_url = location
            location = None

        prep_body = (settings.after_booking_prep_body or "").strip() or None
        return {
            "token": token,
            "journey_stage": link.journey_stage.value,
            "booking_status": link.booking_status,
            "booked_at": link.booked_at.isoformat() if link.booked_at else None,
            "meeting_start": link.meeting_start.isoformat() if link.meeting_start else None,
            "meeting_end": link.meeting_end.isoformat() if link.meeting_end else None,
            "meeting_timezone": link.meeting_timezone,
            "meeting_date_label": meeting_card["date_label"],
            "meeting_time_label": meeting_card["time_label"],
            "meeting_type_label": "Video meeting",
            "meeting_location": location,
            "meeting_join_url": join_url,
            "calcom_booking_uid": link.calcom_booking_uid,
            "event_title": (settings.calcom_event_title or "").strip() or None,
            "welcome_enabled": video_enabled,
            "welcome_heading": (settings.after_booking_video_heading or "").strip()
            or "A quick welcome before your meeting",
            "welcome_intro": (settings.after_booking_video_intro or "").strip() or None,
            "welcome_video_url": video_url if video_enabled else None,
            "welcome_poster_url": (
                (settings.after_booking_video_poster_url or "").strip() or None
                if video_enabled
                else None
            ),
            "welcome_embed_kind": embed_kind if video_enabled else "none",
            "prep_heading": (settings.after_booking_prep_heading or "").strip()
            or "Prepare for your conversation with Jeanne",
            "prep_body": prep_body,
            "prep_blocks": split_prep_blocks(prep_body),
            "prep_note": (settings.after_booking_prep_note or "").strip() or None,
            "youtube_links": youtube,
            "show_youtube": bool(youtube),
        }

    def booking_status_payload(
        self, token: str, *, sync: bool = False, uid: str | None = None
    ) -> dict[str, Any]:
        link, _profile = self.resolve(token)
        from acquisition.services.booking_service import BookingService

        booking = BookingService(self.db)
        booking_uid = (uid or "").strip() or None
        if (sync or booking_uid) and not booking.is_booking_confirmed(link):
            self._sync_booking_from_calcom(token, link, uid=booking_uid)
        confirmed = booking.is_booking_confirmed(link)
        return {
            "token": token,
            "confirmed": confirmed,
            "booking_status": link.booking_status,
            "booked_at": link.booked_at.isoformat() if link.booked_at else None,
            "meeting_start": link.meeting_start.isoformat() if link.meeting_start else None,
        }

    def _sync_booking_from_calcom(
        self, token: str, link: CampaignProspect, *, uid: str | None = None
    ) -> None:
        """Pull a missed Cal.com booking for this assessment token (webhook backup)."""
        from acquisition.services.calcom_webhook_service import (
            CalcomWebhookService,
            CalcomWebhookServiceError,
        )

        wanted_uid = (uid or "").strip() or None
        if not wanted_uid:
            now = time.monotonic()
            last = _last_calcom_sync.get(token, 0.0)
            if now - last < _CALCOM_SYNC_COOLDOWN_SEC:
                return
            _last_calcom_sync[token] = now
        try:
            CalcomWebhookService(self.db).reconcile_for_token(token, uid=wanted_uid)
            self.db.refresh(link)
        except CalcomWebhookServiceError:
            logger.warning(
                "Could not sync booking from Cal.com for token prefix=%s",
                (token or "")[:12],
            )
        except Exception:
            logger.warning(
                "Could not sync booking from Cal.com for token prefix=%s",
                (token or "")[:12],
                exc_info=True,
            )

    def list_all_youtube_links(self) -> list:
        from acquisition.models.acquisition import AfterBookingYoutubeLink

        return list(
            self.db.scalars(
                select(AfterBookingYoutubeLink).order_by(
                    AfterBookingYoutubeLink.display_order.asc(),
                    AfterBookingYoutubeLink.created_at.asc(),
                )
            ).all()
        )

    def list_enabled_youtube_links(self) -> list[dict[str, Any]]:
        from acquisition.models.acquisition import AfterBookingYoutubeLink

        rows = self.db.scalars(
            select(AfterBookingYoutubeLink)
            .where(AfterBookingYoutubeLink.enabled.is_(True))
            .order_by(
                AfterBookingYoutubeLink.display_order.asc(),
                AfterBookingYoutubeLink.created_at.asc(),
            )
        ).all()
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "youtube_url": r.youtube_url,
                "display_order": r.display_order,
            }
            for r in rows
        ]

    # --- public result branch payload (safe) --------------------------------

    def result_branch_context(
        self, link: CampaignProspect, profile: AssessmentProfile
    ) -> dict[str, Any]:
        status = assessment_qualification_status(profile)
        eligible = is_calendar_eligible(link=link, profile=profile)
        ctx: dict[str, Any] = {
            "qualification_assessment_status": status,
            "journey_stage": link.journey_stage.value,
            "show_continue_to_proof": status == QualificationStatus.QUALIFIED.value,
            "show_calendar_cta": eligible,
            "calendar_eligible": eligible,
            "exit_kind": None,
        }
        if status == QualificationStatus.UNQUALIFIED.value:
            ctx["exit_kind"] = "unqualified_exit"
        elif status == QualificationStatus.PENDING.value:
            ctx["exit_kind"] = "pending_review"
        # Never include calendar_url for ineligible (or even eligible on result —
        # only calendar route returns the URL).
        return ctx

    def admin_progress(
        self, link: CampaignProspect, profile: AssessmentProfile | None
    ) -> dict[str, Any]:
        status = assessment_qualification_status(profile)
        proof_state = "not viewed"
        if link.proof_viewed_at is not None:
            proof_state = "viewed / completed"
        demo_state = "not started"
        if link.demo_completed_at is not None:
            demo_state = "completed"
        elif link.demo_started_at is not None:
            demo_state = "started"
        value_state = "completed" if link.value_context_completed_at else "not completed"
        eligible = is_calendar_eligible(link=link, profile=profile)
        return {
            "assessment_status": status,
            "journey_stage": link.journey_stage.value,
            "proof_viewed_at": link.proof_viewed_at.isoformat() if link.proof_viewed_at else None,
            "proof_state": proof_state,
            "demo_started_at": link.demo_started_at.isoformat() if link.demo_started_at else None,
            "demo_completed_at": (
                link.demo_completed_at.isoformat() if link.demo_completed_at else None
            ),
            "demo_state": demo_state,
            "value_context_completed_at": (
                link.value_context_completed_at.isoformat()
                if link.value_context_completed_at
                else None
            ),
            "value_state": value_state,
            "economic_capacity": economic_capacity_of(profile),
            "budget_readiness": budget_readiness_of(profile),
            "calendar_eligible": eligible,
            "calendar_eligibility_label": "eligible" if eligible else "locked",
            "calendar_lock_reasons": self.calendar_lock_reasons(link, profile),
            "booking_status": getattr(link, "booking_status", None) or "not_booked",
            "booked_at": link.booked_at.isoformat() if getattr(link, "booked_at", None) else None,
            "meeting_start": (
                link.meeting_start.isoformat() if getattr(link, "meeting_start", None) else None
            ),
            "meeting_end": (
                link.meeting_end.isoformat() if getattr(link, "meeting_end", None) else None
            ),
            "meeting_timezone": getattr(link, "meeting_timezone", None),
            "calcom_booking_uid": getattr(link, "calcom_booking_uid", None),
            "calcom_event_title": (self.get_settings().calcom_event_title or "").strip() or None,
        }

    def admin_set_budget_readiness(
        self, link: CampaignProspect, profile: AssessmentProfile, outcome: str
    ) -> AssessmentProfile:
        value = str(outcome or "").strip().lower()
        if value not in _BUDGET_OUTCOMES:
            raise AcquisitionJourneyError(
                "Invalid budget_readiness",
                http_status=422,
                code="invalid_budget",
            )
        qual = dict(profile.qualification) if isinstance(profile.qualification, dict) else {}
        qual["budget_readiness"] = value
        profile.qualification = qual
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(profile, "qualification")
        eligible = is_calendar_eligible(link=link, profile=profile)
        if eligible and link.journey_stage != JourneyStage.CALENDAR_UNLOCKED:
            link.journey_stage = JourneyStage.CALENDAR_UNLOCKED
        if eligible:
            record_calendar_unlocked_once(link, eligible=True)
            self.db.add(link)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile
