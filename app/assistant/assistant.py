"""Campaign assistant: people, campaigns, the email card, and send via Instantly.

Company/ops tool effects live in ``app.assistant.company_tools``.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

####################################################################################################
# PART 2 - CAMPAIGN HELPERS (load people/campaigns, who is already in a campaign, send through Instantly)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# context  (app/assistant/context.py) - Facts the assistant may use: saved campaigns, people, and the initial email.
# ------------------------------------------------------------------------------------------------
import itertools
import json


def _clock_context(value: str) -> str:
    hour_text, minute = (value or "00:00").split(":", 1)
    hour = int(hour_text)
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute} {suffix}"


def _sender_name(db, ctx) -> str:
    from acquisition.config import get_settings
    from acquisition.models.campaign import Campaign

    current = (getattr(ctx, "display_name", None) or "").strip()
    if current:
        return current
    latest = db.query(Campaign).order_by(Campaign.created_at.desc()).first()
    if latest and (latest.sender_name or "").strip():
        return latest.sender_name.strip()
    return get_settings().default_sender_name


def known_records(ctx=None) -> dict:
    """Current acquisition rows. Empty lists mean nothing is saved, not that the model may invent rows."""
    from sqlalchemy.orm import selectinload

    from acquisition.config import get_settings
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import (
        Campaign,
        CampaignProspect,
        MessageChannel,
        MessageType,
    )
    from acquisition.models.prospect import Prospect
    from acquisition.services.outreach_conflict_service import is_active_outreach_link
    from acquisition.services.assessment_tokens import split_executive_name
    from acquisition.services.campaign_service import initial_email_text

    db = SessionLocal()
    try:
        campaigns = []
        rows = db.query(Campaign).options(selectinload(Campaign.messages)).all()
        for campaign in rows:
            initial = next(
                (
                    message
                    for message in campaign.messages
                    if message.is_active
                    and message.channel == MessageChannel.EMAIL
                    and message.message_type == MessageType.INITIAL
                ),
                None,
            )
            start = _clock_context(campaign.send_window_start)
            end = _clock_context(campaign.send_window_end)
            zone = (campaign.send_timezone or "").strip()
            campaigns.append(
                {
                    "campaign_id": str(campaign.id),
                    "campaign_name": campaign.name,
                    "schedule_start": start,
                    "schedule_end": end,
                    "timezone": zone,
                    "schedule_label": f"{start}–{end}",
                    "initial_email": None
                    if initial is None
                    else {
                        "subject": initial.subject or "",
                        "body": initial.body or "",
                    },
                }
            )
        sender_name = _sender_name(db, ctx)
        assessment_url = (get_settings().public_base_url or "").strip()
        no_company_subject, no_company_body = initial_email_text(
            sender_name=sender_name,
            first_name="",
            company="",
            assessment_url=assessment_url,
        )
        active_by_prospect: dict = {}
        for link in db.query(CampaignProspect).options(selectinload(CampaignProspect.campaign)).all():
            camp = link.campaign
            # Same rule the lead sync uses, so "already in a campaign" means one thing everywhere.
            if camp is None or not is_active_outreach_link(link, campaign=camp):
                continue
            active_by_prospect.setdefault(link.prospect_id, [])
            cid = str(camp.id)
            if any(item["campaign_id"] == cid for item in active_by_prospect[link.prospect_id]):
                continue
            active_by_prospect[link.prospect_id].append(
                {"campaign_id": cid, "campaign_name": camp.name}
            )
        people = []
        for person in db.query(Prospect).order_by(Prospect.executive_name).all():
            first_name, _last = split_executive_name(person.executive_name)
            company = (person.company or "").strip()
            subject, body = initial_email_text(
                sender_name=sender_name,
                first_name=first_name,
                company=company,
                assessment_url=assessment_url,
            )
            people.append(
                {
                    "name": person.executive_name,
                    "first_name": first_name,
                    "email": person.email,
                    "company": company,
                    "approval_status": person.approval_status.value,
                    "active_campaigns": active_by_prospect.get(person.id, []),
                    "email_to_show": {"subject": subject, "body": body},
                }
            )
        return {
            "sender_name": sender_name,
            "sender_accounts": sending_account_emails(db),
            "assessment_url": assessment_url,
            "email_without_company": {"subject": no_company_subject, "body": no_company_body},
            "campaigns": campaigns,
            "people": people,
        }
    finally:
        db.close()


def sending_account_emails(db=None) -> list[str]:
    """Mailbox addresses Instantly can send from. Empty when Instantly is not connected."""
    from acquisition.db.session import SessionLocal
    from acquisition.services.campaign_activation_service import CampaignActivationService

    own_session = db is None
    session = db or SessionLocal()
    try:
        accounts = CampaignActivationService(session).list_sending_accounts()
    except Exception:
        return []
    finally:
        if own_session:
            session.close()
    emails: list[str] = []
    for account in accounts:
        email = (account.email or "").strip()
        if email and email not in emails:
            emails.append(email)
    return emails


def known_records_text(ctx=None) -> str:
    try:
        records = known_records(ctx)
    except Exception:
        records = {"campaigns": [], "people": [], "unavailable": True}
    return json.dumps(records, ensure_ascii=False)

# ------------------------------------------------------------------------------------------------
# lead_status  (app/assistant/lead_status.py) - Who is already in a campaign, and what the assistant may do about it.
# ------------------------------------------------------------------------------------------------
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from acquisition.integrations.instantly.errors import InstantlyError
from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignProspect,
    JourneyStage,
    OutreachStatus,
)
from acquisition.models.prospect import Prospect
from acquisition.models.replies import ReplyConversation
from acquisition.services.instantly_lead_control import stop_instantly_lead_outreach
from acquisition.services.outreach_conflict_service import (
    is_active_outreach_link,
    normalize_outreach_email,
)

FREE = "free"
MOVE_NOT_CONTACTED = "move_not_contacted"
MOVE_CONTACTED = "move_contacted"
REFUSE_CONVERSATION = "refuse_conversation"
REFUSE_OPTED_OUT = "refuse_opted_out"

_RANK = {
    FREE: 0,
    MOVE_NOT_CONTACTED: 1,
    MOVE_CONTACTED: 2,
    REFUSE_CONVERSATION: 3,
    REFUSE_OPTED_OUT: 4,
}
_OPTED_OUT = {OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED}


class StopError(Exception):
    """Stopping a lead in its old campaign failed. Nothing was changed locally."""


@dataclass(slots=True)
class LinkInfo:
    link_id: object
    campaign_id: str
    campaign_name: str
    runtime_status: str
    outreach_status: str
    sync_status: str
    active: bool
    contacted: bool
    conversation: bool
    opted_out: bool

    def as_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "campaign_runtime": self.runtime_status,
            "outreach_status": self.outreach_status,
            "already_emailed": self.contacted,
            "in_conversation": self.conversation,
        }


@dataclass(slots=True)
class PersonStatus:
    email: str
    name: str = ""
    known: bool = False
    tier: str = FREE
    links: list[LinkInfo] = field(default_factory=list)

    @property
    def label(self) -> str:
        return (self.name or self.email).strip()

    @property
    def blocking(self) -> list[LinkInfo]:
        return [link for link in self.links if link.active]

    @property
    def blocking_names(self) -> str:
        names = []
        for link in self.blocking:
            if link.campaign_name and link.campaign_name not in names:
                names.append(link.campaign_name)
        return " and ".join(names) or "another campaign"

    def as_dict(self) -> dict:
        return {
            "email": self.email,
            "name": self.name,
            "tier": self.tier,
            "what_to_do": _GUIDANCE[self.tier],
            "active_in": [link.as_dict() for link in self.blocking],
        }


_GUIDANCE = {
    FREE: "Not in any campaign. Add normally.",
    MOVE_NOT_CONTACTED: (
        "Already in another campaign but never emailed. Tell the user where, then ask one yes/no to stop them "
        "there and add them to the new campaign. If yes, call approve_move."
    ),
    MOVE_CONTACTED: (
        "Already emailed in another campaign. Tell the user where and that moving stops their follow-ups there. "
        "Ask for a clear yes before approve_move."
    ),
    REFUSE_CONVERSATION: (
        "They replied, started the assessment, or booked. Do NOT move or add them. Tell the user plainly and "
        "suggest handling them personally."
    ),
    REFUSE_OPTED_OUT: (
        "They unsubscribed or bounced. Never add them. Tell the user plainly; this is not negotiable."
    ),
}


def _in_conversation(link: CampaignProspect, has_reply_row: bool) -> bool:
    return bool(
        has_reply_row
        or link.outreach_status in {OutreachStatus.REPLIED, OutreachStatus.ASSESSMENT_STARTED}
        or link.last_reply_at is not None
        or link.assessment_started_at is not None
        or link.assessment_status != AssessmentStatus.NOT_STARTED
        or link.booked_at is not None
        or (link.booking_status or "").strip().lower() == "confirmed"
        or link.journey_stage not in {JourneyStage.OUTREACH}
    )


def describe_people(db: Session, emails: list[str]) -> list[PersonStatus]:
    """Read-only. One PersonStatus per email, in the order given."""
    out: list[PersonStatus] = []
    seen: set[str] = set()
    for raw in emails:
        email = normalize_outreach_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        person = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email))
        status = PersonStatus(
            email=str(raw).strip(),
            name=(person.executive_name or "").strip() if person else "",
            known=person is not None,
        )
        links = list(
            db.scalars(
                select(CampaignProspect)
                .options(selectinload(CampaignProspect.campaign))
                .join(Prospect, Prospect.id == CampaignProspect.prospect_id)
                .where(func.lower(Prospect.email) == email)
            ).all()
        )
        reply_link_ids = set()
        if links:
            reply_link_ids = set(
                db.scalars(
                    select(ReplyConversation.campaign_prospect_id).where(
                        ReplyConversation.campaign_prospect_id.in_([link.id for link in links])
                    )
                ).all()
            )
        tier = FREE
        for link in links:
            campaign = link.campaign
            active = is_active_outreach_link(link, campaign=campaign)
            opted_out = link.outreach_status in _OPTED_OUT
            info = LinkInfo(
                link_id=link.id,
                campaign_id=str(link.campaign_id),
                campaign_name=(campaign.name if campaign else "") or "",
                runtime_status=(campaign.instantly_runtime_status.value if campaign else ""),
                outreach_status=link.outreach_status.value,
                sync_status=link.sync_status.value,
                active=active,
                contacted=link.outreach_status == OutreachStatus.ACTIVE,
                conversation=_in_conversation(link, link.id in reply_link_ids),
                opted_out=opted_out,
            )
            status.links.append(info)
            if opted_out:
                candidate = REFUSE_OPTED_OUT
            elif not active:
                continue
            elif info.conversation:
                candidate = REFUSE_CONVERSATION
            elif info.contacted:
                candidate = MOVE_CONTACTED
            else:
                candidate = MOVE_NOT_CONTACTED
            if _RANK[candidate] > _RANK[tier]:
                tier = candidate
        status.tier = tier
        out.append(status)
    return out


def check_people(emails: list[str]) -> list[PersonStatus]:
    """Same as describe_people but opens (and closes) its own session."""
    from acquisition.db.session import SessionLocal

    db = SessionLocal()
    try:
        return describe_people(db, emails)
    finally:
        db.close()


def _instantly_lead_id(client, link) -> str:
    """The lead's id in Instantly: the saved one, else look it up by campaign and email."""
    if link.instantly_lead_id:
        return str(link.instantly_lead_id)
    email = (link.prospect.email if link.prospect else "") or ""
    cid = link.campaign.instantly_campaign_id if link.campaign else ""
    if not (email and cid):
        return ""
    data = client.list_leads({"campaign": cid, "search": email, "limit": 20})
    for item in data.get("items") or []:
        if str(item.get("email") or "").lower() == email.lower():
            return str(item.get("id") or "")
    return ""


def stop_for_move(db: Session, client, link_id) -> None:
    """Take one person out of their old campaign so they appear only in the new one.

    Instantly first: the lead is deleted from the old campaign (not labelled "Not interested"),
    which also cancels anything still scheduled for them there. Only after that succeeds is the
    local link removed. If Instantly fails, nothing local changes and the person is still
    (correctly) shown as active there. The person, their other campaigns and the audit log stay.
    """
    link = db.scalar(
        select(CampaignProspect)
        .options(selectinload(CampaignProspect.campaign), selectinload(CampaignProspect.prospect))
        .where(CampaignProspect.id == link_id)
    )
    if link is None:
        return
    try:
        lead_id = _instantly_lead_id(client, link)
        if lead_id:
            try:
                client._request("DELETE", f"/leads/{lead_id}", json={})
            except InstantlyError as exc:
                if getattr(exc, "status_code", None) != 404:  # 404 = already gone
                    raise
    except InstantlyError as exc:
        raise StopError(str(exc) or "Instantly did not accept the request to remove the lead.") from exc
    try:
        db.delete(link)
        db.commit()
    except Exception:  # noqa: BLE001  - could not remove locally: keep the record, but make it inactive
        db.rollback()
        link.outreach_status = OutreachStatus.STOPPED
        db.add(link)
        db.commit()

# ------------------------------------------------------------------------------------------------
# fulfill  (app/assistant/fulfill.py) - Create the approved campaign and hand the email to Instantly.
# ------------------------------------------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import ValidationError

from acquisition.db.session import SessionLocal
from acquisition.integrations.instantly.timezones import normalize_instantly_timezone
from acquisition.models.campaign import (
    Campaign,
    CampaignProspect,
    InstantlyRuntimeStatus,
    LeadSyncStatus,
    MessageType,
)
from acquisition.models.prospect import ApprovalStatus as ProspectApprovalStatus, Prospect
from acquisition.schemas.campaign import CampaignCreate, CampaignMessageUpdate, CampaignUpdate
from acquisition.schemas.prospect import ProspectCreate
from acquisition.services.assessment_tokens import split_executive_name
from acquisition.services.campaign_activation_service import ActivationError, CampaignActivationService
from acquisition.services.campaign_service import (
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
)
from acquisition.services.instantly_campaign_sync_service import (
    InstantlyCampaignSyncService,
    InstantlySyncError,
)
from acquisition.services.lead_sync_service import LeadSyncError, LeadSyncService
from acquisition.services.instantly_service import InstantlyService
from acquisition.services.prospect_service import ProspectNotFoundError, ProspectService


class FulfillError(Exception):
    def __init__(self, message: str, *, campaign_id: str = "") -> None:
        self.campaign_id = campaign_id
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FulfillResult:
    campaign_id: str
    message: str
    prior_emails: tuple = ()


@dataclass(slots=True)
class _Progress:
    """What this call changed, so a failure can put things back."""

    campaign_id: str = ""
    created_here: bool = False
    attached_now: list = field(default_factory=list)  # prospect ids attached during this call
    reused: list = field(default_factory=list)  # STOPPED links reactivated this call (never detach these)
    stopped: list[str] = field(default_factory=list)  # people stopped in their old campaign
    prior_emails: list = field(default_factory=list)


def fulfill_approved_email(ctx, parsed: dict) -> FulfillResult:
    """Use the approved fields to create or update the campaign, then sync and activate it."""
    name = str(parsed.get("campaign_name") or "").strip()
    if not name:
        raise FulfillError("The campaign needs a name before it can be created.")
    subject, body = _subject_body(parsed)
    start, end, zone = _window(parsed.get("campaign_schedule_timing") or [])
    recipients = _recipients_fulfill(parsed)
    if not recipients:
        raise FulfillError("The email needs a recipient before it can be sent.")
    accounts = sending_account_emails()
    if not accounts:
        raise FulfillError("Instantly has no sending account, so the email cannot be sent.")
    move_from = {str(item or "").strip().lower() for item in parsed.get("move_from") or []}
    readds = {str(item or "").strip().lower() for item in parsed.get("readds") or []}

    sender = (getattr(ctx, "display_name", None) or "").strip()
    db = SessionLocal()
    progress = _Progress()
    try:
        campaigns = CampaignService(db)
        if not sender:
            sender = (campaigns.settings.default_sender_name or "").strip()
        requested = str(parsed.get("campaign_id") or "").strip()

        # Preflight: decide everything that can be decided BEFORE writing anything.
        to_stop = _preflight(db, recipients, requested, move_from, readds)

        campaign, progress.created_here = _campaign(db, campaigns, requested, name=name, sender=sender)
        campaign_id = progress.campaign_id = str(campaign.id)
        stated_name = str(parsed.get("person_name") or "").strip()
        already_active = (
            requested
            and campaign_id == requested
            and campaign.instantly_runtime_status == InstantlyRuntimeStatus.ACTIVE
        )
        if not already_active:
            campaigns.update_campaign(
                campaign.id,
                CampaignUpdate(
                    name=name,
                    sender_name=sender or campaign.sender_name,
                    send_timezone=zone,
                    send_window_start=start,
                    send_window_end=end,
                ),
            )
            campaign = campaigns.get_campaign(campaign.id)
        CampaignActivationService(db).save_sending_accounts(campaign.id, accounts)
        prospects = ProspectService(db)
        people = []
        fields_by_email = {
            str(row.get("email") or "").strip().lower(): row
            for row in parsed.get("people_fields") or []
        }
        for email in recipients:
            row = fields_by_email.get(email.lower()) or {}
            first = str(row.get("first_name") or "").strip()
            last = str(row.get("last_name") or "").strip()
            if row.get("first_is_fallback"):
                first = ""
            own_name = " ".join(part for part in (first, last) if part)
            if not own_name and len(recipients) == 1:
                own_name = stated_name
            person = _person(db, prospects, email, own_name)
            people.append(person)
            if person.approval_status != ProspectApprovalStatus.APPROVED:
                prospects.bulk_approve([person.id])
            existing = db.scalar(
                select(CampaignProspect).where(
                    CampaignProspect.campaign_id == campaign.id,
                    CampaignProspect.prospect_id == person.id,
                )
            )
            if existing is not None and existing.outreach_status == OutreachStatus.STOPPED:
                if email.strip().lower() not in readds:
                    raise FulfillError(
                        f"{email} was stopped in this campaign. Confirm with the user before adding them back.",
                        campaign_id=campaign_id,
                    )
                progress.reused.append(_snapshot_link(existing))
                if existing.last_activity_at is not None:
                    progress.prior_emails.append(
                        {
                            "email": email,
                            "campaign_id": campaign.id,
                            "first_emailed_at": existing.last_activity_at.isoformat(),
                            "instantly_lead_id": existing.instantly_lead_id,
                        }
                    )
                _reactivate_stopped_link(existing)
                db.add(existing)
                db.commit()
                continue
            try:
                campaigns.attach_prospect(campaign.id, person.id)
                progress.attached_now.append(person.id)
            except CampaignValidationError as exc:
                if "already attached" in exc.message:
                    continue
                raise FulfillError(exc.message, campaign_id=campaign_id) from exc
        _apply_person_fields(db, campaign.id, people, parsed.get("people_fields"))
        if parsed.get("email_template"):
            template_subject, template_body = finalize_template(subject, body)
        else:
            template_subject, template_body = _template_copy(
                subject,
                body,
                people,
                assessment_url=(campaigns.settings.public_base_url or "").strip(),
                placeholder=campaigns.settings.assessment_url_placeholder,
            )
        keep_saved_email = parsed.get("email_scope") == "keep" and not progress.created_here
        if not keep_saved_email:
            _write_initial(campaigns, campaign, template_subject, template_body)
        companies = (
            [str(f.get("company") or "").strip() for f in parsed.get("people_fields") or []]
            if parsed.get("people_fields")
            else [(person.company or "").strip() for person in people]
        )
        if people and all(not c for c in companies):
            _drop_company_placeholder(db, campaign.id)
        InstantlyCampaignSyncService(db).sync(campaign.id)

        # Stop the old outreach as late as possible: everything above can fail without touching it.
        _stop_old_outreach(db, to_stop, progress)

        leads = LeadSyncService(db).sync_campaign_leads(campaign.id)
        if leads.synced < 1:
            raise FulfillError(
                leads.message or "The recipient was not added to the campaign.",
                campaign_id=campaign_id,
            )
        moved_note = _moved_note(to_stop)
        if already_active:
            return FulfillResult(
                campaign_id=campaign_id,
                message=(
                    f"Added {', '.join(recipients)} to the existing {name} campaign. Nothing has been delivered yet: "
                    "Instantly sends inside the campaign's send window."
                    + (
                        " The updated email also applies to everyone in the campaign who has not been emailed yet."
                        if str(parsed.get("email_scope") or "") == "everyone"
                        else ""
                    )
                    + moved_note
                ),
                prior_emails=tuple(progress.prior_emails),
            )
        CampaignActivationService(db).activate(campaign.id, confirm=True)
    except FulfillError as exc:
        _undo(db, progress)
        raise FulfillError(
            _with_note(str(exc), progress), campaign_id=_surviving_id(db, progress, exc.campaign_id)
        ) from exc
    except ValidationError as exc:
        _undo(db, progress)
        raise FulfillError(
            _with_note("The campaign or recipient could not be saved.", progress),
            campaign_id=_surviving_id(db, progress, progress.campaign_id),
        ) from exc
    except (CampaignValidationError, InstantlySyncError, LeadSyncError, ActivationError) as exc:
        detail = getattr(exc, "message", None) or str(exc)
        missing = getattr(exc, "missing", None) or []
        if missing:
            detail = f"{detail} {' '.join(missing)}"
        _undo(db, progress)
        raise FulfillError(
            _with_note(detail.strip(), progress),
            campaign_id=_surviving_id(db, progress, progress.campaign_id),
        ) from exc
    finally:
        db.close()

    return FulfillResult(
        campaign_id=campaign_id,
        message=(
            f"Created the {name} campaign in Instantly and scheduled this email. Nothing has been delivered yet: "
            f"Instantly sends it inside the campaign's send window.{moved_note}"
        ),
        prior_emails=tuple(progress.prior_emails),
    )


def _preflight(db, recipients: list[str], requested: str, move_from: set[str], readds: set[str]) -> list:
    """Refuse or plan every recipient before anything is written. Returns the old links to stop."""
    to_stop: list = []
    for status in describe_people(db, recipients):
        label = status.label
        if any(link.opted_out for link in status.links):
            raise FulfillError(
                f"{label} unsubscribed or bounced earlier, so they cannot be added to any campaign."
            )
        same_stopped = [
            link
            for link in status.links
            if link.outreach_status == OutreachStatus.STOPPED.value and requested and link.campaign_id == requested
        ]
        if same_stopped:
            info = same_stopped[0]
            if info.conversation:
                raise FulfillError(
                    f"{label} already replied, started the assessment, or booked in {info.campaign_name or 'this campaign'}, "
                    "so they cannot be added back."
                )
            if status.email.strip().lower() not in readds:
                raise FulfillError(
                    f"{label} was stopped in {info.campaign_name or 'this campaign'} earlier. "
                    "Confirm with the user before adding them back."
                )
        elsewhere = [link for link in status.blocking if link.campaign_id != requested]
        if any(link.conversation for link in elsewhere):
            raise FulfillError(
                f"{label} already replied or started the assessment in {status.blocking_names}, "
                "so I will not move them. That one is best handled personally."
            )
        if not elsewhere:
            continue
        if status.email.strip().lower() not in move_from:
            raise FulfillError(
                f"{label} is already in {status.blocking_names}. Nothing was changed. "
                "Do you want me to stop them there and add them to this campaign?"
            )
        to_stop.extend(elsewhere)
    return to_stop


def _stop_old_outreach(db, to_stop: list, progress: _Progress) -> None:
    if not to_stop:
        return
    try:
        client = InstantlyService(db).get_api_client()
    except Exception as exc:  # noqa: BLE001
        raise FulfillError("Could not reach Instantly to stop the old outreach. Nothing was moved.") from exc
    for link in to_stop:
        try:
            stop_for_move(db, client, link.link_id)
        except StopError as exc:
            raise FulfillError(
                f"Instantly would not stop the outreach in {link.campaign_name}: {exc}"
            ) from exc
        progress.stopped.append(link.campaign_name or "the old campaign")


def _moved_note(to_stop: list) -> str:
    names = []
    for link in to_stop:
        if link.campaign_name and link.campaign_name not in names:
            names.append(link.campaign_name)
    return f" Their outreach in {' and '.join(names)} was stopped." if names else ""


def _with_note(message: str, progress: _Progress) -> str:
    if progress.stopped:
        return (
            f"{message} Note: outreach was already stopped in {' and '.join(dict.fromkeys(progress.stopped))}, "
            "so they are not in an active campaign right now. Ask me to try again."
        )
    return message


def _snapshot_link(link: CampaignProspect) -> dict:
    return {
        "link_id": link.id,
        "outreach_status": link.outreach_status,
        "instantly_lead_id": link.instantly_lead_id,
        "sync_status": link.sync_status,
        "last_activity_at": link.last_activity_at,
        "last_synced_at": link.last_synced_at,
        "sync_error": link.sync_error,
    }


def _reactivate_stopped_link(link: CampaignProspect) -> None:
    """Clear Instantly marks so the next send recreates the lead. Keep last_activity_at (first emailed)."""
    link.outreach_status = OutreachStatus.PENDING
    link.instantly_lead_id = None
    link.sync_status = LeadSyncStatus.NOT_SYNCED
    link.last_synced_at = None
    link.sync_error = None


def _restore_reused_links(db, progress: _Progress) -> None:
    """Put reactivated STOPPED links back, including Instantly marks. Never detach them."""
    if not progress.reused:
        return
    try:
        for snap in progress.reused:
            link = db.get(CampaignProspect, snap["link_id"])
            if link is None:
                continue
            link.outreach_status = snap["outreach_status"]
            link.instantly_lead_id = snap["instantly_lead_id"]
            link.sync_status = snap["sync_status"]
            link.last_activity_at = snap["last_activity_at"]
            link.last_synced_at = snap["last_synced_at"]
            link.sync_error = snap["sync_error"]
            db.add(link)
        db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _undo(db, progress: _Progress) -> None:
    """Best effort: leave no empty campaign or half-attached people behind."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    _restore_reused_links(db, progress)
    if not progress.campaign_id:
        return
    try:
        campaigns = CampaignService(db)
        cid = uuid.UUID(progress.campaign_id)
        campaign = db.get(Campaign, cid)
        if campaign is None:
            return
        if campaign.instantly_runtime_status == InstantlyRuntimeStatus.ACTIVE:
            return
        if progress.created_here:
            campaigns.delete_campaign(cid)
            progress.campaign_id = ""
            return
        for prospect_id in progress.attached_now:
            link = db.scalar(
                select(CampaignProspect).where(
                    CampaignProspect.campaign_id == cid,
                    CampaignProspect.prospect_id == prospect_id,
                )
            )
            if link is not None and not link.instantly_lead_id:
                campaigns.detach_prospect(cid, prospect_id)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _surviving_id(db, progress: _Progress, fallback: str) -> str:
    """Do not hand back the id of a campaign that was just deleted."""
    if not progress.campaign_id:
        return ""
    return fallback or progress.campaign_id


def _template_copy(
    subject: str,
    body: str,
    people: list,
    *,
    assessment_url: str,
    placeholder: str,
) -> tuple[str, str]:
    """Keep the approved wording. Swap the preview's name and company for Instantly fields."""
    def _swap(chunk: str) -> str:
        out = chunk
        for person in people:
            first, _last = split_executive_name(person.executive_name or "")
            company = (person.company or "").strip()
            if first:
                out = out.replace(f"Hi {first},", "Hi {{firstName}},")
            if company:
                out = out.replace(company, "{{companyName}}")
        return out

    text_subject = strip_assessment_link(_map_unprotected(subject, _swap))
    text_body = _map_unprotected(body, _swap)
    if has_assessment_link(text_body) or (assessment_url and assessment_url in (body or "")):
        text_body = keep_assessment_link(text_body)
    elif assessment_url:
        text_body = text_body.replace(assessment_url, placeholder)
    if "Hi there," in text_body:
        text_body = text_body.replace("Hi there,", "Hi {{firstName}},", 1)
    return text_subject, text_body


def _apply_person_fields(db, campaign_id, people: list, final_fields: list | None) -> None:
    """Save, on each person's link to THIS campaign, any name/company that differs from their record.
    Lead sync reads these, so what the user typed for one person never reaches anyone else."""
    if not final_fields:
        return
    by_email = {str(f.get("email") or "").casefold(): f for f in final_fields}
    for person in people:
        final = by_email.get((person.email or "").casefold())
        if not final:
            continue
        link = db.scalar(
            select(CampaignProspect).where(
                CampaignProspect.campaign_id == campaign_id, CampaignProspect.prospect_id == person.id
            )
        )
        if link is None:
            continue
        first, last, company = (final.get("first_name") or ""), (final.get("last_name") or ""), (final.get("company") or "")
        # A person who was only a mailbox so far becomes the person the user described.
        if _mailbox_name(person.email or "", (person.executive_name or "").strip()) and not final.get("first_is_fallback"):
            person.executive_name = " ".join(x for x in (first, last) if x)
            db.add(person)
        if company and not (person.company or "").strip():
            person.company = company
            db.add(person)
        stored_first, stored_last = split_executive_name(person.executive_name or "")
        stored_company = (person.company or "").strip()
        link.first_name_override = first if first != stored_first else None
        link.last_name_override = last if last != stored_last else None
        link.company_override = company if company != stored_company else None
        db.add(link)
    db.commit()


def _write_initial(campaigns: CampaignService, campaign, subject: str, body: str) -> None:
    current = campaigns.get_campaign(campaign.id)
    initial = next(
        (
            message
            for message in current.messages
            if message.is_active and message.message_type == MessageType.INITIAL
        ),
        None,
    )
    if initial is None:
        raise FulfillError("The campaign has no initial email to send.", campaign_id=str(campaign.id))
    if (initial.subject or "") == subject and (initial.body or "") == body:
        return
    campaigns.update_message(
        current.id,
        initial.id,
        CampaignMessageUpdate(subject=subject, body=body, delay_after_previous_hours=0),
    )


def _campaign(db, campaigns: CampaignService, raw_id, *, name: str, sender: str):
    """Return (campaign, created_here). A bad id is an error, never a silent new campaign."""
    text = str(raw_id or "").strip()
    if text:
        try:
            return campaigns.get_campaign(uuid.UUID(text)), False
        except (ValueError, CampaignNotFoundError) as exc:
            raise FulfillError(
                "I could not find the campaign you picked. Nothing was created. Please pick it again."
            ) from exc
    clash = db.scalar(select(Campaign).where(func.lower(Campaign.name) == name.strip().lower()))
    if clash is not None:
        raise FulfillError(
            f"A campaign called {clash.name} already exists. Do you want to use it, or pick a different name?"
        )
    created = campaigns.create_campaign(
        CampaignCreate(name=name, sender_name=sender or None, seed_default_sequence=True)
    )
    return created, True


def _person(db, prospects: ProspectService, email: str, person_name: str) -> Prospect:
    stated = person_name.strip()
    found = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email.lower()))
    if found is not None:
        _keep_stated_name(db, found, stated)
        _clear_mailbox_company(db, found)
        return found
    local, _, _domain = email.partition("@")
    created = prospects.create_prospect(
        ProspectCreate(
            company=local or email,
            executive_name=stated or (local or email).strip(),
            email=email,
            source="assistant",
        )
    )
    created.company = ""
    db.add(created)
    db.commit()
    db.refresh(created)
    return created


def _drop_company_placeholder(db, campaign_id) -> None:
    """Follow-ups must not address a company that was never identified."""
    from acquisition.models.campaign import CampaignMessage

    rows = list(
        db.scalars(select(CampaignMessage).where(CampaignMessage.campaign_id == campaign_id)).all()
    )
    changed = False
    for message in rows:
        subject = (message.subject or "").replace(" for {{companyName}}", "").replace(" {{companyName}}", "")
        body = (message.body or "").replace(" for {{companyName}}", "").replace("{{companyName}}", "")
        if subject != (message.subject or "") or body != (message.body or ""):
            message.subject = subject
            message.body = body
            db.add(message)
            changed = True
    if changed:
        db.commit()


def _clear_mailbox_company(db, prospect: Prospect) -> None:
    """An email host is not a company. Leave company empty when none was identified."""
    company = (prospect.company or "").strip()
    host = (prospect.email or "").partition("@")[2].strip()
    if not company or not host or company.casefold() != host.casefold():
        return
    prospect.company = ""
    db.add(prospect)
    db.commit()


def _keep_stated_name(db, prospect: Prospect, stated: str) -> None:
    """Replace a mailbox stored in the name field with the name the user gave."""
    if not stated:
        return
    current = (prospect.executive_name or "").strip()
    local = prospect.email.split("@", 1)[0].strip()
    if current.casefold() not in {local.casefold(), prospect.email.casefold(), ""}:
        return
    if current == stated:
        return
    prospect.executive_name = stated
    db.add(prospect)
    db.commit()
    first_name, last_name = split_executive_name(stated)
    lead_ids = [
        lead_id
        for lead_id in db.scalars(
            select(CampaignProspect.instantly_lead_id).where(
                CampaignProspect.prospect_id == prospect.id,
                CampaignProspect.instantly_lead_id.is_not(None),
            )
        ).all()
        if lead_id
    ]
    if not lead_ids:
        return
    try:
        client = InstantlyService(db).get_api_client()
    except Exception:
        return
    for lead_id in lead_ids:
        try:
            client.update_lead(
                lead_id,
                {"first_name": first_name or None, "last_name": last_name or None},
            )
        except Exception:
            continue


def replace_person_name(
    *,
    email: str,
    first_name: str,
    last_name: str = "",
    company: str | None = None,
) -> None:
    """Keep a name the user chose for this person from now on."""
    from sqlalchemy import func, select

    address = (email or "").strip()
    if "@" not in address:
        return
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    stated = " ".join(part for part in (first, last) if part)
    db = SessionLocal()
    try:
        prospect = db.scalar(select(Prospect).where(func.lower(Prospect.email) == address.lower()))
        if prospect is None:
            return
        if stated:
            prospect.executive_name = stated
        if company is not None and company.strip():
            prospect.company = company.strip()
        db.add(prospect)
        db.commit()
        if not stated:
            return
        first_name, last_name = split_executive_name(stated)
        lead_ids = [
            lead_id
            for lead_id in db.scalars(
                select(CampaignProspect.instantly_lead_id).where(
                    CampaignProspect.prospect_id == prospect.id,
                    CampaignProspect.instantly_lead_id.is_not(None),
                )
            ).all()
            if lead_id
        ]
        if not lead_ids:
            return
        try:
            client = InstantlyService(db).get_api_client()
        except Exception:
            return
        for lead_id in lead_ids:
            try:
                client.update_lead(
                    lead_id,
                    {"first_name": first_name or None, "last_name": last_name or None},
                )
            except Exception:
                continue
    finally:
        db.close()


def _recipients_fulfill(parsed: dict) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    single = str(parsed.get("email_address") or "").strip()
    values = [single] if single else []
    values.extend(str(item or "").strip() for item in parsed.get("email_address_list") or [])
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            found.append(value)
    return found


def _subject_body(parsed: dict) -> tuple[str, str]:
    if parsed.get("email_template"):
        subject = str(parsed.get("email_subject") or "").strip()
        body = str(parsed.get("email_body") or "").strip()
        if not subject or not body:
            raise FulfillError("The approved email is missing a subject or a body.")
        return subject, body
    draft = str(parsed.get("email_draft") or "").strip()
    if draft.lower().startswith("subject:"):
        first, _, rest = draft.partition("\n")
        subject = first.split(":", 1)[1].strip()
        body = rest.strip()
        if subject and body:
            return subject, body
    subject = str(parsed.get("email_subject") or "").strip()
    body = str(parsed.get("email_body") or "").strip()
    if not subject or not body:
        raise FulfillError("The approved email is missing a subject or a body.")
    return subject, body


def _window(timing: list) -> tuple[str, str, str]:
    if len(timing) < 2:
        raise FulfillError("The campaign needs a start time and an end time.")
    start, start_zone = _clock_fulfill(str(timing[0]))
    end, end_zone = _clock_fulfill(str(timing[1]))
    if start >= end:
        raise FulfillError("The send window start has to be earlier than the end.")
    zone = normalize_instantly_timezone(start_zone or end_zone or "America/Chicago")
    if zone not in _allowed_zones():
        raise FulfillError("The schedule timezone is not one Instantly can use.")
    return start, end, zone


def _clock_fulfill(value: str) -> tuple[str, str | None]:
    parts = value.replace(".", " ").split()
    zone = None
    if parts and parts[-1].lower() not in {"am", "pm"} and parts[-1].isalpha():
        zone = parts[-1]
        parts = parts[:-1]
    clock = " ".join(parts).upper()
    for layout in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = datetime.strptime(clock, layout)
        except ValueError:
            continue
        return parsed.strftime("%H:%M"), zone
    raise FulfillError(f"Could not read the send time {value.strip() or '(blank)'}.")


def _allowed_zones() -> set[str]:
    from acquisition.integrations.instantly.timezones import INSTANTLY_TIMEZONE_SET

    return INSTANTLY_TIMEZONE_SET


####################################################################################################
# PART 3 - CAMPAIGN TOOLS (update_selection, show_contacts, show_campaigns, propose_email, approve_move, remove_from_campaign, send_approved)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# campaign_tools  (app/assistant/campaign_tools.py) - Campaign tools for the unified assistant.
# ------------------------------------------------------------------------------------------------
import difflib
from typing import Any, Callable

from agno.tools import tool



def _audit(event: str, ctx, payload: dict, **extra) -> None:
    """Record the send in the platform audit log. Never lets logging break the send."""
    try:
        from bizos.audit import events as audit
        from bizos.types import AuditEventType

        audit.log(
            AuditEventType(event),
            ctx=ctx,
            tool="campaign_send_approved",
            status=event,
            request={
                "campaign": payload.get("campaign_name"),
                "recipients": payload.get("email_address_list"),
                "moved_from_other_campaigns": payload.get("move_from") or [],
                "readded_to_this_campaign": payload.get("readds") or [],
                "subject": payload.get("email_subject"),
            },
            result={k: v for k, v in extra.items() if v},
        )
    except Exception:  # noqa: BLE001
        pass


_WHY = {"type": "string", "description": "1-2 specific sentences: what you understood and why this call is right now."}

CAMPAIGN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_selection",
            "description": (
                "Commit what the user decided: recipients, campaign, and/or daily send window. Merges into the "
                "current selection. Existing campaign: pass campaign_id (its saved window is used). New campaign: "
                "pass campaign_name plus window_start and window_end. Call this as soon as the user's meaning gives you any of these."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipients": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"email": {"type": "string"}, "name": {"type": "string"}},
                            "required": ["email"],
                        },
                    },
                    "recipients_mode": {
                        "type": "string",
                        "enum": ["replace", "add"],
                        "description": "add = keep current recipients and add these; replace = these become the only recipients.",
                    },
                    "campaign_id": {"type": "string"},
                    "campaign_name": {"type": "string"},
                    "confirmed_new_campaign": {
                        "type": "boolean",
                        "description": "true only after the user confirmed they want a brand-new campaign despite a similar existing name.",
                    },
                    "window_start": {"type": "string", "description": "clock time like 9:00 am"},
                    "window_end": {"type": "string", "description": "clock time like 5:00 pm"},
                    "window_evidence": {
                        "type": "string",
                        "description": "REQUIRED with window_start/window_end: copy the user's own words that gave the hours (e.g. '9 am to 5 pm'). Never invent or default hours; if the user has not said them, do not pass a window, ask instead.",
                    },
                    "why": _WHY,
                },
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_contacts",
            "description": "Show the saved contacts table so the user can pick. Refused if recipients are already committed (including this turn). Use change_recipients=true ONLY when the user explicitly asks to see the list again; to switch people, call update_selection instead.",
            "parameters": {
                "type": "object",
                "properties": {"change_recipients": {"type": "boolean"}, "why": _WHY},
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_campaigns",
            "description": "Show the saved campaigns table so the user can pick (only ones the committed recipients are not already active in). Requires recipients to be committed. Refused if a campaign is already committed unless change_campaign=true. When the user only wants to LOOK at their campaigns (for example 'show my campaigns'), call it with show_all=true: it lists every saved campaign at any time, even with no recipients or a pending selection, and changes nothing.",
            "parameters": {
                "type": "object",
                "properties": {"change_campaign": {"type": "boolean"}, "show_all": {"type": "boolean"}, "why": _WHY},
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_email",
            "description": (
                "Build the draft from the committed selection and show it as a card for approval. Requires recipients, campaign "
                "and window to be committed. Call it for a NEW draft or when the user asks for a change. Do not call it to answer "
                "a question about the draft. If the user edited the draft in the card, the current text is returned to you: build "
                "on it and pass change_evidence (the user's words asking for the change)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "change_evidence": {"type": "string", "description": "the user's own words asking to change the existing draft"},
                    "why": _WHY,
                },
                "required": ["subject", "body", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_move",
            "description": (
                "Record that the user said yes to stopping these people in their other campaign, or to adding them back "
                "after they were stopped in this one. Only after you explained it and the user's LATEST message clearly "
                "agrees. Refused if you had not told them yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "emails": {"type": "array", "items": {"type": "string"}},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes"},
                    "why": _WHY,
                },
                "required": ["emails", "approval_evidence", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_campaign",
            "description": (
                "Take one person out of a campaign. If they have not been emailed, ask one yes/no and remove. "
                "If they were already emailed, say plainly that the email is already sent and cannot be taken back, "
                "and that removing them only stops their follow-ups; ask ONE yes/no. If they replied, booked, "
                "unsubscribed or bounced, say so and change nothing. The person and their history stay. "
                "Ask ONE yes/no first; call only after the user's NEXT message says yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "campaign": {"type": "string", "description": "campaign name or id"},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes"},
                    "why": _WHY,
                },
                "required": ["email", "campaign", "approval_evidence", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_campaign_people",
            "description": (
                "Read-only. Lists the people in ONE campaign with their status (waiting, emailed, replied, started the "
                "assessment, booked, stopped...). Use it for 'who is in <campaign>?'. Changes nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {"campaign": {"type": "string", "description": "campaign name or id"}, "why": _WHY},
                "required": ["campaign", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_campaign",
            "description": (
                "Pause a campaign (also for 'stop' a campaign): nothing is emailed from it until it is resumed. Nothing is "
                "deleted. Call it only when the user asks in this message to pause or stop that campaign."
            ),
            "parameters": {
                "type": "object",
                "properties": {"campaign": {"type": "string", "description": "campaign name or id"}, "why": _WHY},
                "required": ["campaign", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_campaign",
            "description": (
                "Resume (turn back on) a paused campaign. Emails may start sending, so the first call returns the yes/no "
                "question to ask; call again only after the user's NEXT message says yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign": {"type": "string", "description": "campaign name or id"},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes (empty on the first call)"},
                    "why": _WHY,
                },
                "required": ["campaign", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_campaign",
            "description": (
                "Delete a campaign completely from FreedomBot AND Instantly, like the admin Delete button. The first call "
                "returns the yes/no question to ask (with counts); call again only after the user's NEXT message says yes. "
                "The people stay in the workspace. Never for 'stop' or 'pause'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign": {"type": "string", "description": "campaign name or id"},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes (empty on the first call)"},
                    "why": _WHY,
                },
                "required": ["campaign", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_prospect",
            "description": (
                "Delete one person completely, like the admin Delete Prospect button, and remove their leads from Instantly "
                "so no more emails go out. The first call returns the yes/no question to ask; call again only after the "
                "user's NEXT message says yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes (empty on the first call)"},
                    "why": _WHY,
                },
                "required": ["email", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_person_details",
            "description": (
                "Save a first name, last name or company for ONE chosen recipient. A name the user wrote with the email "
                "always wins. If they only gave the address, judge a human name from the mailbox and pass that email as "
                "evidence. Never overwrite a name already on file using only the address. Never invent a company from the "
                "mailbox. Applies to that person only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "company": {"type": "string"},
                    "evidence": {"type": "string", "description": "the user's own words that gave these details"},
                    "why": _WHY,
                },
                "required": ["email", "evidence", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "choose_email_scope",
            "description": (
                "Called with scope 'everyone' after the user says yes to changing the campaign's first email for everyone "
                "who has not been emailed yet. The bot must never offer or pick 'keep' on its own."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["everyone", "keep"]},
                    "approval_evidence": {"type": "string", "description": "the user's words that chose this"},
                    "why": _WHY,
                },
                "required": ["scope", "approval_evidence", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_approved",
            "description": "Create the campaign and send the pending draft. Only after the user's latest message approves it.",
            "parameters": {
                "type": "object",
                "properties": {"approval_evidence": {"type": "string", "description": "the user's words that count as approval"}, "why": _WHY},
                "required": ["approval_evidence", "why"],
            },
        },
    },
]




# ---------- helpers (validation only, no wording) ----------

def _clock_campaign(value: str) -> str | None:
    text = " ".join((value or "").upper().replace(".", "").split())
    for candidate in (text, text.replace(" ", "")):
        for layout in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M"):
            try:
                t = datetime.strptime(candidate, layout)
            except ValueError:
                continue
            return f"{t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}"
    return None


def _people(records: dict) -> list[dict]:
    return records.get("people") or []


def _find_person(records: dict, email: str) -> dict | None:
    key = (email or "").strip().casefold()
    return next((p for p in _people(records) if str(p.get("email") or "").casefold() == key), None)


def _find_campaign(records: dict, cid: str = "", name: str = "") -> dict | None:
    for c in records.get("campaigns") or []:
        if cid and str(c.get("campaign_id") or "") == cid:
            return c
        if name and str(c.get("campaign_name") or "").strip().casefold() == name.strip().casefold():
            return c
    return None


def _active_ids(person: dict | None) -> set[str]:
    return {str(c.get("campaign_id") or "") for c in (person or {}).get("active_campaigns") or []}


def _campaign_timing(c: dict) -> str:
    label = str(c.get("schedule_label") or "").strip()
    return label or f"{c.get('schedule_start') or ''}–{c.get('schedule_end') or ''}".strip("–")



def _missing(state: dict) -> list[str]:
    out = []
    if not state.get("recipients"):
        out.append("recipients")
    if not state.get("campaign_name"):
        out.append("campaign")
    if len(state.get("window") or []) < 2:
        out.append("daily send window")
    return out


def _keep_approvals(state: dict, keep: set[str]) -> None:
    state["moves"] = [email for email in state.get("moves") or [] if email in keep]
    state["readds"] = [email for email in state.get("readds") or [] if email in keep]


def campaign_state_text(state: dict, people_status: list | None = None, fields: list | None = None) -> str:
    view = {
        "people_status": people_status if people_status is not None else "unavailable",
        "moves_user_approved": state.get("moves") or [],
        "readds_user_approved": state.get("readds") or [],
        "committed_selection": {
            "recipients": state.get("recipients") or [],
            "campaign_id": state.get("campaign_id") or "",
            "campaign_name": state.get("campaign_name") or "",
            "daily_window": state.get("window") or [],
        },
        "still_missing": _missing(state),
        "last_shown_to_user_in_tables_in_order": state.get("shown") or {"people": [], "campaigns": []},
        "pending_draft": state.get("draft", {}).get("email_draft") if state.get("draft") else None,
        "previous_draft_wording_reuse_it": state.get("previous_draft") or None,
        "person_details_for_the_email": [
            {k: v for k, v in f.items() if k != "email"} | {"email": f["email"]} for f in (fields or [])
        ],
        "email_scope_decision": state.get("scope") or "",
        "your_previous_turn_explanation": state.get("last_explanation") or "",
    }
    return "Current state:\n" + json.dumps(view, indent=1, default=str)




# ---------- tool execution ----------

import re

# ---------- the email as a template: blanks, per-person values, who receives it ----------
# One campaign has ONE first email. It holds blanks ({{firstName}} ...) that each person's own details fill.
# Words typed outside a blank go to everyone in that campaign. What the user types for one person in the
# email card ("details") belongs to that person only.

TOKEN_FIRST, TOKEN_LAST, TOKEN_COMPANY = "{{firstName}}", "{{lastName}}", "{{companyName}}"
TOKEN_ASSESS = "{{assessment_url}}"
FIRST_NAME_FALLBACK = "there"
_GREETING = r"(?:Hi|Hello|Dear|Hey)"
_LITERAL_GREETING = re.compile(rf"\b{_GREETING}\s+(?!there\b)([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)?)\s*[,!]")
_PROTECTED_LINK = re.compile(r"(https?://\S+|\{+\s*assessment[\s_-]*url\s*\}+)", re.I)


def clean_field(value: Any) -> str:
    """A value typed for a blank: one short line, no markup, nothing that could add content to the email."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"[{}<>\\]", "", text)[:80].strip()


def proper_case(value: str) -> str:
    """A name typed in chat as all-lowercase or all-capitals ("nina", "BROOKS") reads as a name: "Nina", "Brooks"."""
    text = (value or "").strip()
    if text and (text == text.lower() or text == text.upper()):
        return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), text)
    return text


def _local_part_is_handle(local: str) -> bool:
    """A mailbox is a handle when it has digits or separators, not a single given name."""
    token = (local or "").strip()
    if not token:
        return True
    return any(ch.isdigit() or ch in "._+%" for ch in token)


def _mailbox_name(email: str, value: str) -> bool:
    """True when ``value`` is the address itself, not a person's name.

    A given name that matches the local part of a simple address is a real name and is kept.
    """
    raw = (value or "").strip()
    addr = (email or "").strip()
    if not raw or not addr:
        return False
    if raw.casefold() == addr.casefold():
        return True
    local = addr.split("@", 1)[0].strip()
    if raw.casefold() != local.casefold():
        return False
    return _local_part_is_handle(local)


def _evidence_is_the_address(evidence: str, email: str) -> bool:
    """True when the only evidence is the recipient address (or its mailbox), not a spoken name."""
    quote = " ".join((evidence or "").casefold().split())
    addr = " ".join((email or "").casefold().split())
    if not quote or not addr:
        return False
    local = addr.split("@", 1)[0]
    return quote in {addr, local}


def person_fields(records: dict, state: dict, recipient: dict) -> dict:
    """What fills the blanks for one recipient, and where each value came from.

    Order: typed by the user (card or chat) > the database > the name given in chat > missing.
    """
    email = str(recipient.get("email") or "")
    typed = (state.get("overrides") or {}).get(email.casefold(), {})
    known = _find_person(records, email) or {}
    known_name = str(known.get("name") or "").strip()
    if _mailbox_name(email, known_name):
        known_name = ""
    k_first, k_last = split_executive_name(known_name) if known_name else ("", "")
    chat_name = str(recipient.get("name") or "").strip()
    c_first, c_last = split_executive_name(chat_name) if chat_name else ("", "")
    c_first, c_last = proper_case(c_first), proper_case(c_last)
    out: dict = {"email": email}
    sources: dict = {}
    for field, from_db, from_chat in (
        ("first_name", k_first, c_first),
        ("last_name", k_last, c_last),
        ("company", str(known.get("company") or "").strip(), ""),
    ):
        if field in typed:
            out[field], sources[field] = typed[field], "typed"
        elif from_db:
            out[field], sources[field] = from_db, "database"
        elif from_chat:
            out[field], sources[field] = from_chat, "chat"
        else:
            out[field], sources[field] = "", "missing"
    out["sources"] = sources
    return out


def effective_fields(f: dict) -> dict:
    """The values that really go out. A missing first name becomes "there" (never a guess)."""
    return {
        "email": f["email"],
        "first_name": f["first_name"] or FIRST_NAME_FALLBACK,
        "last_name": f["last_name"],
        "company": f["company"],
    }


_BLANK_FORMS = (
    (re.compile(r"\{{1,2}\s*first[\s_-]*name\s*\}{1,2}|\[\s*first[\s_-]*name\s*\]|<\s*first[\s_-]*name\s*>", re.I), TOKEN_FIRST),
    (re.compile(r"\{{1,2}\s*last[\s_-]*name\s*\}{1,2}|\[\s*last[\s_-]*name\s*\]|<\s*last[\s_-]*name\s*>", re.I), TOKEN_LAST),
    (re.compile(r"\{{1,2}\s*company(?:[\s_-]*name)?\s*\}{1,2}|\[\s*company(?:[\s_-]*name)?\s*\]|<\s*company(?:[\s_-]*name)?\s*>", re.I), TOKEN_COMPANY),
)


def normalize_blanks(text: str) -> str:
    """Any way of writing a blank ({first name}, {{first_name}}, [First Name], <firstName>) becomes the one blank we use."""
    out = text or ""
    for pattern, token in _BLANK_FORMS:
        out = pattern.sub(token, out)
    return out


def _assessment_settings() -> tuple[str, str]:
    from acquisition.config import get_settings

    settings = get_settings()
    holder = (settings.assessment_url_placeholder or TOKEN_ASSESS).strip() or TOKEN_ASSESS
    base = (settings.public_base_url or "").strip().rstrip("/")
    return holder, base


def has_assessment_link(text: str) -> bool:
    holder, base = _assessment_settings()
    blob = text or ""
    if holder and holder in blob:
        return True
    if re.search(r"\{+\s*assessment[\s_-]*url\s*\}+", blob, re.I):
        return True
    return bool(base and base in blob)


def keep_assessment_link(text: str) -> str:
    """Keep the assessment link as Instantly's placeholder. Never drop it while framing names."""
    holder, base = _assessment_settings()
    out = text or ""
    if base:
        out = re.sub(rf"{re.escape(base)}(?:/a/\S+)?", holder, out)
    out = re.sub(r"\{+\s*assessment[\s_-]*url\s*\}+", holder, out, flags=re.I)
    if holder not in out:
        out = f"{out.rstrip()}\n\n{holder}"
    return out


def strip_assessment_link(text: str) -> str:
    """Remove the assessment CTA from a field that must not carry it (the subject)."""
    holder, base = _assessment_settings()
    out = text or ""
    if base:
        out = re.sub(rf"{re.escape(base)}(?:/a/\S+)?", "", out)
    out = re.sub(r"\{+\s*assessment[\s_-]*url\s*\}+", "", out, flags=re.I)
    if holder:
        out = out.replace(holder, "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _map_unprotected(text: str, transform) -> str:
    bits = _PROTECTED_LINK.split(text or "")
    return "".join(bit if i % 2 else transform(bit) for i, bit in enumerate(bits))


def tokenize(text: str, fields: list[dict]) -> str:
    """Turn the real names/companies a draft contains into blanks, so each person gets their own."""
    original = text or ""
    keep_link = has_assessment_link(original)

    def _names(chunk: str) -> str:
        out = normalize_blanks(chunk)
        # One greeting that lists several recipients ("Hi Nina and Raj,") becomes ONE blank, so each person is
        # greeted by their own name and never by the other's.
        people = []
        for f in fields:
            if f["first_name"]:
                last = rf"(?:\s+(?i:{re.escape(f['last_name'])}))?" if f["last_name"] else ""
                people.append(rf"(?i:{re.escape(f['first_name'])}){last}")
        if len(people) >= 2:
            one = "(?:" + "|".join(sorted(people, key=len, reverse=True)) + ")"
            out = re.sub(
                rf"\b(?P<g>{_GREETING})\s+{one}(?:\s*(?:,|&|\band\b)\s*(?:and\s+)?{one})+",
                lambda m: f"{m.group('g')} {TOKEN_FIRST}",
                out,
            )
        for f in fields:
            first, last, company = f["first_name"], f["last_name"], f["company"]
            if first:
                last_part = rf"(?P<last>\s+(?i:{re.escape(last)}))?" if last else ""
                out = re.sub(
                    rf"\b(?P<g>{_GREETING})\s+(?i:{re.escape(first)}){last_part}\b",
                    lambda m, last=last: f"{m.group('g')} {TOKEN_FIRST}" + (f" {TOKEN_LAST}" if last and m.group("last") else ""),
                    out,
                )
            if company:
                out = re.sub(rf"(?<!\w){re.escape(company)}(?!\w)", TOKEN_COMPANY, out)
        return re.sub(rf"\b({_GREETING})\s+there\b", lambda m: f"{m.group(1)} {TOKEN_FIRST}", out, count=1)

    framed = _map_unprotected(original, _names)
    if keep_link:
        return keep_assessment_link(framed)
    return framed


def render(text: str, f: dict) -> str:
    e = effective_fields(f)
    holder, base = _assessment_settings()
    out = (text or "").replace(TOKEN_FIRST, e["first_name"]).replace(TOKEN_LAST, e["last_name"]).replace(TOKEN_COMPANY, e["company"])
    if holder:
        out = out.replace(holder, base or holder)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return re.sub(r"[ \t]+([,.!?:;])", r"\1", out)


def literal_greeting_names(text: str) -> list[str]:
    return [m.group(1) for m in _LITERAL_GREETING.finditer(text or "")]


def finalize_template(subject: str, body: str) -> tuple[str, str]:
    """The wording as stored on the campaign: the assessment link lives in the body only."""
    return strip_assessment_link(subject), keep_assessment_link(body)


def waiting_in_campaign(campaign_id: str, exclude: set[str]) -> list[dict]:
    """People already in this campaign who have not been emailed yet: they will get any changed wording."""
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import CampaignProspect, OutreachStatus
    from acquisition.models.prospect import Prospect

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Prospect.executive_name, Prospect.email)
            .join(CampaignProspect, CampaignProspect.prospect_id == Prospect.id)
            .where(
                CampaignProspect.campaign_id == campaign_id,
                CampaignProspect.outreach_status == OutreachStatus.PENDING,
            )
        ).all()
    except Exception:  # noqa: BLE001
        return []
    finally:
        db.close()
    return [
        {"name": name or email, "email": email}
        for name, email in rows
        if (email or "").casefold() not in exclude
    ]


def draft_view(records: dict, state: dict) -> dict:
    """Everything the email card needs: the template, each person's details, what each person will receive,
    who else gets this wording, and warnings. Built from saved state, never from the model."""
    d = state.get("draft") or {}
    if not d:
        return {}
    recipients = state.get("recipients") or []
    fields = [person_fields(records, state, r) for r in recipients]
    subject, body = d.get("email_subject") or "", d.get("email_body") or ""
    is_template = bool(d.get("email_template"))
    previews = [
        {
            "email": f["email"],
            "subject": render(subject, f) if is_template else subject,
            "body": render(body, f) if is_template else body,
        }
        for f in fields
    ]
    warnings: list[str] = []
    if is_template:
        for f in fields:
            if not f["first_name"] and TOKEN_FIRST in subject + body:
                warnings.append(f'{f["email"]}: no first name on file. The email will say "Hi there,". Type a name in Details if you know it.')
        for name in literal_greeting_names(body):
            warnings.append(
                f'The greeting says "{name}" as plain text, so everyone who gets this email sees exactly that. '
                "Put a person's name in Details instead."
            )
        if TOKEN_COMPANY in subject + body:
            for f in fields:
                if not f["company"]:
                    warnings.append(f'{f["email"]}: no company on file, so the company part of the email will be empty.')
    cid = str(state.get("campaign_id") or "")
    camp = _find_campaign(records, cid, "") if cid else None
    existing = camp is not None
    changed, others = False, []
    changed_parts: list[str] = []
    first_difference = ""
    if existing and is_template:
        saved = camp.get("initial_email") or {}
        t_subject, t_body = finalize_template(subject, body)
        norm = lambda t: " ".join(str(t or "").split())  # noqa: E731
        if norm(t_subject) != norm(saved.get("subject")):
            changed_parts.append("subject")
        if norm(t_body) != norm(saved.get("body")):
            changed_parts.append("body")
            saved_lines = [ln for ln in str(saved.get("body") or "").splitlines() if ln.strip()]
            new_lines = [ln for ln in t_body.splitlines() if ln.strip()]
            for old_ln, new_ln in itertools.zip_longest(saved_lines, new_lines, fillvalue=""):
                if norm(old_ln) != norm(new_ln):
                    first_difference = f"saved: {norm(old_ln)[:80]} | draft: {norm(new_ln)[:80]}"
                    break
        changed = bool(changed_parts)
        if changed:
            others = waiting_in_campaign(cid, {f["email"].casefold() for f in fields})
    scope = str(state.get("scope") or "")
    label = lambda f: " ".join(x for x in (f["first_name"], f["last_name"]) if x) or f["email"]  # noqa: E731
    return {
        "draft_id": d.get("id") or "",
        "template": {"subject": subject, "body": body},
        "assessment_url": (records.get("assessment_url") or "").strip(),
        "people": fields,
        "previews": previews,
        "warnings": warnings,
        "audience": {
            "campaign": state.get("campaign_name") or "",
            "campaign_is_new": not existing,
            "will_send_now": [label(f) for f in fields],
            "also_waiting": others,
            "template_changed": changed,
            "changed_parts": changed_parts,
            "first_difference": first_difference,
            "scope": scope,
            "scope_needed": bool(existing and changed and others and not scope),
        },
    }


def refresh_preview(records: dict, state: dict) -> None:
    """Keep the plain-text preview (the first recipient's view) in step with the template."""
    d = state.get("draft") or {}
    fields = [person_fields(records, state, r) for r in state.get("recipients") or []]
    if not d or not fields:
        return
    if d.get("email_template"):
        d["email_draft"] = f"Subject: {render(d['email_subject'], fields[0])}\n\n{render(d['email_body'], fields[0])}"


def edit_draft(ctx: Any, meta: dict, *, subject: str | None = None, body: str | None = None,
               people: dict | None = None, scope: str | None = None, rendered: bool = False) -> dict:
    """Apply what the user changed in the email card to the saved state of the latest turn.

    ``subject``/``body`` are the TEMPLATE (blanks visible). ``people`` maps an email to the details the user
    typed for that person only. ``scope`` is "everyone", "keep", or "" to clear. Returns the new email view.
    With ``rendered=True`` the text is what ONE person's email looks like (older card): real names are turned
    back into blanks first, so they cannot reach everyone else.
    """
    state = meta.get("state")
    if not isinstance(state, dict) or not state.get("draft"):
        raise ValueError("There is no email draft to edit.")
    records = known_records(ctx)
    d = state["draft"]
    if subject is not None or body is not None:
        new_subject = (subject if subject is not None else d.get("email_subject", "")).strip()
        new_body = (body if body is not None else d.get("email_body", "")).strip()
        if not new_subject or not new_body:
            raise ValueError("The subject and the body cannot be empty.")
        new_subject, new_body = normalize_blanks(new_subject), normalize_blanks(new_body)
        if rendered:
            fields = [person_fields(records, state, r) for r in state.get("recipients") or []]
            new_subject, new_body = tokenize(new_subject, fields), tokenize(new_body, fields)
        if (new_subject, new_body) != (d.get("email_subject"), d.get("email_body")) or not d.get("email_template"):
            d["email_subject"], d["email_body"], d["email_template"] = new_subject, new_body, True
            d["user_edited"] = True  # the model must not overwrite this by regenerating
            state.pop("scope", None)  # a changed email needs its own decision
    recipients = {str(r["email"]).casefold(): r for r in state.get("recipients") or []}
    for email, values in (people or {}).items():
        key = str(email).casefold()
        if key not in recipients:
            raise ValueError(f"{email} is not one of the recipients.")
        base_state = {**state, "overrides": {k: v for k, v in (state.get("overrides") or {}).items() if k != key}}
        base = person_fields(records, base_state, recipients[key])
        typed = (state.setdefault("overrides", {})).setdefault(key, {})
        for field in ("first_name", "last_name", "company"):
            if field not in (values or {}):
                continue
            value = clean_field(values[field])
            if value == base[field]:
                typed.pop(field, None)  # back to what the database says: not an override
            else:
                typed[field] = value
        if not typed:
            state["overrides"].pop(key, None)
    if scope is not None:
        if scope in ("everyone", "keep"):
            state["scope"] = scope
        elif scope == "":
            state.pop("scope", None)
        else:
            raise ValueError('scope must be "everyone" or "keep".')
    refresh_preview(records, state)
    view = draft_view(records, state)
    payload = meta.get("json") if isinstance(meta.get("json"), dict) else {}
    payload.update(
        {
            "email_subject": d["email_subject"],
            "email_body": d["email_body"],
            "email_draft": d.get("email_draft") or "",
            "email_view": view,
        }
    )
    meta["json"], meta["state"] = payload, state
    return view


# ---------- the send gate: the user's latest message must really approve ----------
# The model decides what counts as approval, but it may not send on a message that plainly is not one.

_APPROVE_WORD = re.compile(
    r"(?<!\w)(yes|yep|yeah|yup|ok|okay|sure|approve|approved|go ahead|go for it|go|send|ship it|do it|proceed|confirm|"
    r"confirmed|good|great|perfect|fine|lgtm|sounds good|looks good|correct|right|haan|theek hai|thik hai|kar do|bhej do|bhejo)(?!\w)"
    r"|\U0001F44D|\u2705",
    re.I,
)
_QUESTION_START = re.compile(
    r"^\s*(what|where|why|how|when|which|who|whom|is|are|was|were|does|do|did|can|could|should|would|will|show|tell|explain|list|display)\b",
    re.I,
)
_SEND_WORD = re.compile(r"\b(send|approve[ds]?|go ahead|ship|do it|proceed|confirm(ed)?)\b", re.I)
_HOLD_WORD = re.compile(r"\b(don'?t|do not|never|not yet|wait|hold on|hold off|stop|cancel)\b|^\s*no\b", re.I)
#: The user asked to change the whole email (subject AND body), not just one part.
_WHOLE_EMAIL = re.compile(
    r"\b(?:whole|entire|full|complete)\s+(?:email|mail|message|draft)\b"
    r"|\bsubject\s*(?:,|and|&|\+|as well|too|also)\s*(?:the\s+)?body\b"
    r"|\bbody\s*(?:,|and|&|\+|as well|too|also)\s*(?:the\s+)?subject\b"
    r"|\b(?:including|plus|with|and)\s+(?:the\s+)?subject\b"
    r"|\bsubject\s+(?:too|also|as well)\b",
    re.I,
)
def _has_clock_hours(text: str) -> bool:
    """True when the user's words give real clock times (two numbers like '9 am to 5 pm', or noon)."""
    nums = re.findall(r"(?<![\w:])\d{1,2}(?::\d{2})?(?!\d)", text or "")
    return len(nums) >= 2 or bool(re.search(r"\bnoon\b", text or "", re.I) and len(nums) >= 1)


_CHANGE_VERB = re.compile(r"\b(?:change|make|rewrite|redo|replace|update|turn|convert|switch|edit|write)\b", re.I)


def approval_problem(latest: str, evidence: str) -> str:
    """Empty string when the latest user message can count as approval to send, else why it cannot."""
    norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
    if not norm(latest):
        return "there is no user message to take approval from"
    if not norm(evidence) or norm(evidence) not in norm(latest):
        return "approval_evidence must be the user's own words from their LATEST message"
    if _HOLD_WORD.search(latest):
        return "the user's latest message holds back or declines; do not send"
    if not _APPROVE_WORD.search(latest):
        return "the user's latest message does not approve sending"
    if _QUESTION_START.match(latest) and not _SEND_WORD.search(latest):
        return "the user's latest message is a question, not an approval"
    return ""


_SENT_CLAIM = re.compile(
    r"\b(?:email|message|draft)\b[^.\n]{0,40}\b(?:was|has been|is|been) sent\b"
    r"|\bcampaign\b[^.\n]{0,40}\b(?:was|has been) created\b|\b(?:i(?:'ve| have)? )?sent (?:the|this|your) email\b",
    re.I,
)
#: In the explanation only: "I will call send_approved now" but no call was made.
_WILL_SEND = re.compile(r"\b(?:will|going to|now|about to)\s+(?:call|use|run|carry out)\s+(?:the\s+)?send_approved\b", re.I)

NOT_SENT_TEXT = (
    "Nothing has been sent yet. The email draft is still waiting. Say \"send it\" and I will send it."
)

#: After a successful send_approved the email is only SCHEDULED in Instantly. Saying it was sent/delivered is wrong.
_DELIVERED_CLAIM = re.compile(
    r"\b(?:email|message|mail)\b[^.\n]{0,40}\b(?:was|has been|is|been|got) (?:sent|delivered)\b"
    r"|\b(?:i(?:'ve| have)? )?sent (?:the|this|your|it|that)\b"
    r"|^\s*(?:email )?sent\b|\bemail sent\b|\bdelivered (?:the|this|your|it)\b",
    re.I | re.M,
)
_DENIES_DELIVERY = re.compile(r"\b(?:not|nothing|n't|no)\b[^.\n]{0,40}\b(?:sent|delivered)\b", re.I)


def claims_delivered(text: str) -> bool:
    """True when the reply says the email was sent or delivered without saying it is only scheduled."""
    blob = text or ""
    if re.search(r"\bschedul", blob, re.I) or _DENIES_DELIVERY.search(blob):
        return False
    return bool(_DELIVERED_CLAIM.search(blob))


def _campaign_row(db, raw: str):
    """A campaign by id or by exact (case-insensitive) name, or None."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return db.get(Campaign, uuid.UUID(raw))
    except ValueError:
        return db.scalar(select(Campaign).where(func.lower(Campaign.name) == raw.lower()))


def _campaign_counts(db, campaign_id) -> dict:
    """How many people in a campaign were emailed, replied, started the assessment or booked."""
    links = db.scalars(select(CampaignProspect).where(CampaignProspect.campaign_id == campaign_id)).all()
    counts = {"people": len(links), "emailed": 0, "replied": 0, "assessment": 0, "booked": 0}
    for link in links:
        if link.outreach_status != OutreachStatus.PENDING:
            counts["emailed"] += 1
        if link.outreach_status == OutreachStatus.REPLIED or link.last_reply_at is not None:
            counts["replied"] += 1
        if (
            link.outreach_status == OutreachStatus.ASSESSMENT_STARTED
            or link.assessment_started_at is not None
            or link.assessment_status != AssessmentStatus.NOT_STARTED
        ):
            counts["assessment"] += 1
        if link.booked_at is not None or (link.booking_status or "").strip().lower() == "confirmed":
            counts["booked"] += 1
    return counts


def _audit_change(ctx, action: str, detail: dict) -> None:
    """Audit-log a pause/resume/delete done from chat. Never lets logging break the action."""
    try:
        from bizos.audit import events as audit
        from bizos.types import AuditEventType

        audit.log(
            AuditEventType("ACTION_EXECUTED"),
            ctx=ctx,
            tool=f"campaign_{action}",
            status="ACTION_EXECUTED",
            request=detail,
            result={"action": action},
        )
    except Exception:  # noqa: BLE001
        pass


class CampaignSession:
    """One turn of the campaign flow: the committed selection plus the tools that change it.

    Code validates and persists state; it never writes user-facing words. Every result goes back to the
    model, which decides what to say."""

    def __init__(self, ctx, records: dict, state: dict, *, send_block: str = ""):
        self.ctx, self.records = ctx, records
        #: Non-empty when this workspace's mode forbids sending (Advise). The model is told why.
        self.send_block = send_block
        self.state = json.loads(json.dumps(state)) if state else {}
        self.people_to_show: list[dict] = []
        self.campaigns_to_show: list[dict] = []
        self.sent = False
        self.log: list[dict] = []
        self.self_check: list[str] = []
        self.errors = 0
        self.asked_similar = False  # the similar-name question is the table the user needs
        self.card_shown = False  # a NEW email card was produced this turn
        self.user_message = ""  # set by the agent; lets the self-check see what the user asked
        self.user_texts: list[str] = []  # everything the user has written in this chat (for evidence checks)
        self.recipients_committed = False  # recipients committed during this turn
        self.tried_recipients = False  # the model tried to name recipients this turn (found or not)
        self.started_with_recipients = bool(self.state.get("recipients"))
        # People already explained to the user in an earlier turn. A move can only be approved for these.
        self.offered_before = set(self.state.get("move_offered") or [])
        # Pause/resume/delete questions asked in the previous turn. A delete is only carried out when the user
        # answers yes to a question they already saw; the offer lives for exactly one following turn.
        self.delete_offered_before = set(self.state.get("delete_offered") or [])
        self.state["delete_offered"] = []

    def call(self, name: str, args: dict) -> str:
        why = str(args.pop("why", "") or "")
        if name == "update_selection" and args.get("recipients"):
            self.tried_recipients = True
        try:
            logged_args = json.dumps(args, ensure_ascii=False, default=str)[:600]
        except Exception:  # noqa: BLE001
            logged_args = "unserialisable"
        try:
            result = getattr(self, f"_{name}")(**args)
        except TypeError:
            result = "error: bad arguments"
        if result.startswith("error") or '"error"' in result:
            self.errors += 1
        self.log.append({"tool": name, "args": logged_args, "why": why, "result": result[:300]})
        return result

    def _user_said(self, quote: str) -> bool:
        """True when ``quote`` really is something the user wrote (this turn or earlier)."""
        norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
        q = norm(quote)
        return bool(q) and any(q in norm(text) for text in self.user_texts)

    def claims_unsent(self, reply: str = "", explanation: str = "") -> bool:
        """True when the model says the email was sent/the campaign created, but send_approved never succeeded."""
        if self.sent or not self.state.get("draft"):
            return False
        tried = any(c.get("tool") == "send_approved" for c in self.log)
        if _SENT_CLAIM.search(reply or ""):
            return True
        # Said "I will call send_approved" but never did. (If it was called and failed, the model is reporting that.)
        return (not tried) and bool(_WILL_SEND.search(explanation or ""))

    def scheduled_text(self) -> str:
        """Plain wording for a send that only scheduled the email (used if the model keeps saying "sent")."""
        s = self.state
        name = str(s.get("campaign_name") or "").strip()
        window = s.get("window") or []
        when = f" It goes out between {window[0]} and {window[1]}." if len(window) >= 2 else ""
        label = f"The {name} campaign" if name else "The campaign"
        return f"{label} is created in Instantly and the email is scheduled. Nothing has been delivered yet.{when}"

    def problems(self, reply: str = "", explanation: str = "") -> list[str]:
        """Consistency check between the model's turn and the state it just built.
        Returned as notes for the model to act on; code never rewrites the reply."""
        s, out = self.state, []
        if self.sent and claims_delivered(reply):
            out.append(
                "Your reply says the email was sent or delivered. It is only SCHEDULED in Instantly. Say the campaign "
                "is created and the email is scheduled to go out in its send window, and that nothing has been "
                "delivered yet. Do not use the words sent or delivered as a done deal."
            )
        if self.claims_unsent(reply, explanation):
            out.append(
                "Your reply says the email was sent or the campaign was created, but send_approved has NOT "
                "succeeded this turn, so nothing was sent. If the user's latest message approves the pending "
                "draft, call send_approved now (approval_evidence = their words) and report only what it "
                "returns. If it does not clearly approve, do not claim a send; ask one short question."
            )
        miss = _missing(s)
        if (
            not self.log
            and not s.get("recipients")
            and not s.get("campaign_name")
            and not s.get("campaign_id")
            and re.search(r"\bcampaigns?\b", self.user_message or "", re.I)
        ):
            out.append(
                "The user's message mentions a campaign but you committed nothing. If they gave you any "
                "recipient, campaign name or send hours, call update_selection with it now, BEFORE you ask "
                "your question. If this message is not about emailing people in a campaign, keep your answer "
                "and say so in the explanation."
            )
        if (
            "recipients" in miss
            and any(c.get("tool") == "update_selection" for c in self.log)
            and not self.people_to_show
            and not any(c.get("tool") in ("show_contacts", "show_campaigns") for c in self.log)
            and not self.tried_recipients
        ):
            out.append(
                "The user has not said who to email and no contacts table is shown. Call show_contacts now so they can "
                "pick; do not wait for them to ask for the list. Your message is one short question (include the send "
                "hours in it if they are also missing)."
            )
        if self.people_to_show and (self.recipients_committed or self.started_with_recipients):
            out.append(
                "You showed the contacts table although recipients were already chosen. Unless the user explicitly "
                "asked to see the list again, commit their choice with update_selection and show the campaigns table."
            )
        if not miss and not s.get("draft") and not self.sent and not self.errors:
            out.append(
                "Recipients, campaign and window are all committed but there is no draft. Call propose_email now "
                "and do not ask the user anything."
            )
        if self.recipients_committed and miss[:1] == ["campaign"] and not self.campaigns_to_show and not self.asked_similar:
            out.append(
                "Recipients are committed and a campaign is still needed, but no campaigns table is shown. If the user "
                "has not said they want a new campaign, call show_campaigns so they can see the options you ask about."
            )
        nameless = [f["email"] for f in self._fields() if not f.get("first_name")]
        if (
            nameless
            and not self.sent
            and not miss
            and not any(c.get("tool") == "set_person_details" for c in self.log)
        ):
            out.append(
                "These recipients have no first name on file: "
                + ", ".join(nameless)
                + ". If the user already gave a name with the email, call set_person_details with that name "
                "and their words as evidence. If they only gave the address, judge a human first and last name "
                "from the mailbox (a name that is clearly in the local part) and call set_person_details with "
                "evidence set to that email. Never overwrite a name the user already gave. Do not invent a "
                "company. If the mailbox is not a person's name, leave it blank."
            )
        return out

    def _assess(self):
        """Fresh per-person status from the database (same rule as the lead sync). None if it cannot be read."""
        emails = [r["email"] for r in self.state.get("recipients") or []]
        if not emails:
            return []
        try:
            return check_people(emails)
        except Exception:  # noqa: BLE001
            return None

    def status_view(self) -> list | None:
        found = self._assess()
        return None if found is None else [st.as_dict() for st in found]

    def _link_is_committed_campaign(self, link) -> bool:
        cid = (self.state.get("campaign_id") or "").strip()
        cname = (self.state.get("campaign_name") or "").strip()
        if not cid and not cname:
            return False
        return (cid and link.campaign_id == cid) or (
            (not cid) and cname and link.campaign_name.casefold() == cname.casefold()
        )

    def _stopped_in_committed(self, st):
        return [
            link
            for link in st.links
            if link.outreach_status == OutreachStatus.STOPPED.value and self._link_is_committed_campaign(link)
        ]

    def _readd_candidates(self, statuses) -> list:
        approved = {str(email).lower() for email in self.state.get("readds") or []}
        out = []
        for st in statuses:
            if st.email.strip().lower() in approved:
                continue
            stopped = self._stopped_in_committed(st)
            if not stopped:
                continue
            info = stopped[0]
            if info.opted_out or info.conversation:
                continue
            out.append(st)
        return out

    def _gate(self, statuses) -> tuple[list[str], list, list]:
        """(refusals, people who still need a move approval, people who still need a re-add approval)."""
        cid = self.state.get("campaign_id") or ""
        approved = {str(e).lower() for e in self.state.get("moves") or []}
        refusals: list[str] = []
        needs: list = []
        for st in statuses:
            if any(link.opted_out for link in st.links):
                refusals.append(f"{st.label} unsubscribed or bounced earlier. Never add them to any campaign.")
                continue
            stopped = self._stopped_in_committed(st)
            if stopped and (stopped[0].conversation or stopped[0].opted_out):
                camp = stopped[0].campaign_name or self.state.get("campaign_name") or "this campaign"
                refusals.append(
                    f"{st.label} already replied, started the assessment, or booked in {camp}. "
                    "Do not add them back; suggest handling them personally."
                )
                continue
            elsewhere = [link for link in st.blocking if link.campaign_id != cid]
            if any(link.conversation for link in elsewhere):
                refusals.append(
                    f"{st.label} already replied, started the assessment or booked in {st.blocking_names}. "
                    "Do not move or add them; suggest handling them personally."
                )
            elif elsewhere and st.email.lower() not in approved:
                needs.append(st)
        return refusals, needs, self._readd_candidates(statuses)

    def _blocked(self) -> str:
        """Empty string when the selection may go forward, else a JSON error for the model."""
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        refusals, needs, readds = self._gate(statuses)
        if refusals:
            return json.dumps(
                {
                    "error": "refused_person",
                    "reasons": refusals,
                    "instruction": "Tell the user plainly. Offer to continue without these people (update_selection with recipients_mode replace).",
                }
            )
        if needs:
            self._mark_offered(needs)
        if readds:
            self._mark_offered(readds)
        if needs and readds:
            return json.dumps(
                {
                    "error": "needs_move_approval",
                    "needs_readd_approval": True,
                    "people": [st.as_dict() for st in needs],
                    "readd": [st.as_dict() for st in readds],
                    "instruction": (
                        "Ask ONE combined yes/no: say where each move person is (adding them here stops them there) "
                        "AND that each re-add person was stopped in this campaign earlier, so adding them back sends "
                        "the whole email sequence again, including the first email. "
                        "Call approve_move only after their NEXT message says yes."
                    ),
                }
            )
        if readds:
            questions = []
            for st in readds:
                who = (split_executive_name(st.name)[0] if st.name else "") or st.label
                stopped = self._stopped_in_committed(st)
                camp = (stopped[0].campaign_name if stopped else "") or self.state.get("campaign_name") or "this campaign"
                questions.append(
                    f"{who} was stopped in {camp} earlier. If I add them again they get the whole email "
                    "sequence again, including the first email. Add them back?"
                )
            return json.dumps(
                {
                    "error": "needs_readd_approval",
                    "people": [st.as_dict() for st in readds],
                    "question": questions[0] if len(questions) == 1 else questions,
                    "instruction": (
                        "Ask this exact yes/no (or one combined question if several people). "
                        "Call approve_move only after their NEXT message says yes. If they say no, change nothing."
                    ),
                }
            )
        if needs:
            return json.dumps(
                {
                    "error": "needs_move_approval",
                    "people": [st.as_dict() for st in needs],
                    "instruction": (
                        "Tell the user where each person is. Adding them here stops their outreach there (history stays). "
                        "Ask one yes/no. Call approve_move only after their NEXT message says yes."
                    ),
                }
            )
        return ""

    def _mark_offered(self, people) -> None:
        offered = set(self.state.get("move_offered") or [])
        offered.update(st.email.lower() for st in people)
        self.state["move_offered"] = sorted(offered)

    def _invalidate_draft(self) -> None:
        d = self.state.get("draft") or {}
        if d.get("email_subject") and d.get("email_body"):
            # Keep the wording, so a change of window/people/campaign never sends the model back to the default
            # template and loses what the user already wrote.
            self.state["previous_draft"] = {"subject": d["email_subject"], "body": d["email_body"]}
        self.state.pop("draft", None)
        self.state.pop("scope", None)

    def _fields(self) -> list[dict]:
        return [person_fields(self.records, self.state, r) for r in self.state.get("recipients") or []]

    def _set_person_details(self, email: str = "", first_name=None, last_name=None, company=None, evidence: str = "") -> str:
        """Save a name/company for ONE recipient. A name the user wrote wins over a mailbox guess."""
        key = str(email or "").strip().casefold()
        known = {str(r["email"]).casefold(): r for r in self.state.get("recipients") or []}
        if key not in known:
            return "error: that address is not one of the chosen recipients"
        current = person_fields(self.records, self.state, known[key])
        from_mailbox = _evidence_is_the_address(evidence, known[key]["email"])
        if not from_mailbox and not self._user_said(evidence):
            return json.dumps(
                {
                    "error": "details_not_given_by_user",
                    "instruction": "Use only a name or company the user actually wrote, or the chosen email as evidence when reading a name from the mailbox.",
                }
            )
        if from_mailbox and current.get("first_name"):
            return json.dumps(
                {
                    "ok": True,
                    "details": current,
                    "note": "A name is already on file for this person. It was not replaced from the email address. Use the user's words as evidence to change it.",
                }
            )
        if from_mailbox:
            company = None
        typed = self.state.setdefault("overrides", {}).setdefault(key, {})
        for field, value in (("first_name", first_name), ("last_name", last_name), ("company", company)):
            if value is not None:
                typed[field] = clean_field(value)
        refresh_preview(self.records, self.state)
        return json.dumps({"ok": True, "details": person_fields(self.records, self.state, known[key])})

    def _choose_email_scope(self, scope: str = "", approval_evidence: str = "") -> str:
        """Record the user's decision when a changed email would also reach people already waiting in the campaign."""
        if scope not in ("everyone", "keep"):
            return 'error: scope must be "everyone" or "keep"'
        if not self.state.get("draft"):
            return "error: no draft"
        if not self._user_said(approval_evidence):
            return "error: use the user's own words as approval_evidence"
        self.state["scope"] = scope
        return json.dumps({"ok": True, "scope": scope})

    def _update_selection(self, recipients=None, recipients_mode="replace", campaign_id="", campaign_name="", confirmed_new_campaign=False, window_start="", window_end="", window_evidence="") -> str:
        s = self.state
        notes: list[str] = []

        if recipients:
            rows, seen = [], set()
            for r in recipients:
                email = str(r.get("email") or "").strip()
                if "@" not in email or email.casefold() in seen:
                    continue
                seen.add(email.casefold())
                known = _find_person(self.records, email)
                rows.append(
                    {
                        "email": str((known or {}).get("email") or email),
                        "name": str((known or {}).get("name") or r.get("name") or "").strip(),
                    }
                )
            if not rows:
                return "error: none of the recipients had a valid email"
            if recipients_mode == "add":
                have = {r["email"].casefold() for r in s.get("recipients") or []}
                rows = (s.get("recipients") or []) + [r for r in rows if r["email"].casefold() not in have]
            if rows != s.get("recipients"):
                self._invalidate_draft()
            s["recipients"] = rows
            _keep_approvals(s, {r["email"].lower() for r in rows})
            self.recipients_committed = True
            self.people_to_show = []
            s.setdefault("shown", {})["people"] = []

        if campaign_id or campaign_name:
            existing = _find_campaign(self.records, campaign_id, campaign_name)
            if existing:
                new_id_, new_name = str(existing.get("campaign_id") or ""), str(existing.get("campaign_name") or "")
                start = _clock_campaign(str(existing.get("schedule_start") or "")) or str(existing.get("schedule_start") or "")
                end = _clock_campaign(str(existing.get("schedule_end") or "")) or str(existing.get("schedule_end") or "")
                window = [start, end]
            else:
                typed = (campaign_name or "").strip()
                similar = [] if confirmed_new_campaign else self._similar_names(typed)
                if similar:
                    self.asked_similar = True
                    return json.dumps(
                        {
                            "error": "similar_campaign_name",
                            "typed": typed,
                            "similar_to": similar,
                            "instruction": (
                                "Ask the user whether they mean the existing campaign or a new one with the typed name. "
                                "Existing: update_selection with its campaign_id. New: update_selection again with "
                                "confirmed_new_campaign=true. If people_status says someone is active in another "
                                "campaign or was stopped in this one, put the move or re-add into this SAME question "
                                "so the user answers once."
                            ),
                            "selection": self._sel(),
                            "people_status": self._status_for_result(),
                        }
                    )
                new_id_, new_name = "", typed
                window = s.get("window") if new_name == s.get("campaign_name") else []
            if (new_id_, new_name, window) != (s.get("campaign_id"), s.get("campaign_name"), s.get("window")):
                self._invalidate_draft()
            s["campaign_id"], s["campaign_name"], s["window"] = new_id_, new_name, window or []
            self.campaigns_to_show = []
            s.setdefault("shown", {})["campaigns"] = []

        if window_start or window_end:
            if _find_campaign(self.records, s.get("campaign_id", ""), s.get("campaign_name", "")):
                notes.append("ignored window: an existing campaign keeps its saved window")
            else:
                if not self._user_said(window_evidence) or not _has_clock_hours(window_evidence):
                    return json.dumps(
                        {
                            "error": "window_not_given_by_user",
                            "instruction": (
                                "The user has not told you exact daily send hours (words like 'morning' or 'afternoon' are "
                                "not hours), so I did not save any. Never invent or default them. Ask: 'What daily send "
                                "hours should it use, for example 9 am to 5 pm?'. "
                                "When they answer, call update_selection with the hours and window_evidence set to their words."
                            ),
                            "selection": self._sel(),
                        }
                    )
                a, b = _clock_campaign(window_start), _clock_campaign(window_end)
                if not (a and b):
                    return "error: need both window_start and window_end as clock times"
                try:
                    if _clock_fulfill(a)[0] >= _clock_fulfill(b)[0]:
                        raise FulfillError("start not before end")
                except FulfillError:
                    return json.dumps(
                        {
                            "error": "window_start_not_before_end",
                            "instruction": (
                                "The send window must start earlier than it ends within one day (for example 9 am to 5 pm). "
                                "An overnight window such as 5 pm to 6 am is not supported, and I did not save it. "
                                "Tell the user this in one sentence and ask for hours that fit inside one day."
                            ),
                            "selection": self._sel(),
                        }
                    )
                if [a, b] != s.get("window"):
                    self._invalidate_draft()
                s["window"] = [a, b]

        conflict = self._conflict()
        if conflict:
            s["campaign_id"], s["campaign_name"], s["window"] = "", "", []
            self._invalidate_draft()
            return json.dumps({"error": conflict, "selection": self._sel(), "still_missing": _missing(s), "people_status": self._status_for_result()})
        return json.dumps(
            {
                "ok": True,
                "selection": self._sel(),
                "still_missing": _missing(s),
                "notes": notes,
                "people_status": self._status_for_result(),
            }
        )

    def _status_for_result(self):
        statuses = self._assess()
        if statuses is None:
            return "unavailable: tell the user you could not check existing campaigns"
        readd = {st.email.lower() for st in self._readd_candidates(statuses)}
        self._mark_offered([st for st in statuses if st.tier.startswith("move_")])
        self._mark_offered([st for st in statuses if st.email.lower() in readd])
        rows = []
        for st in statuses:
            row = st.as_dict()
            if st.email.lower() in readd:
                row["tier"] = "readd_stopped"
                row["what_to_do"] = (
                    "Was stopped in this campaign earlier. Ask one yes/no: adding them back sends the whole "
                    "email sequence again, including the first email. If yes, call approve_move on the next turn."
                )
            rows.append(row)
        return rows

    def _similar_names(self, typed: str) -> list[str]:
        key = typed.casefold()
        if not key:
            return []
        names = [str(c.get("campaign_name") or "") for c in self.records.get("campaigns") or []]
        by_key = {n.casefold(): n for n in names if n}
        return [by_key[k] for k in difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.8) if k != key]

    def _sel(self) -> dict:
        s = self.state
        return {
            "recipients": s.get("recipients") or [],
            "campaign": s.get("campaign_name") or "",
            "window": s.get("window") or [],
        }

    def _conflict(self) -> str:
        s = self.state
        cid = s.get("campaign_id") or ""
        if not cid:
            return ""
        clash = [
            r["email"] for r in s.get("recipients") or []
            if cid in _active_ids(_find_person(self.records, r["email"]))
        ]
        if clash:
            return f"{', '.join(clash)} already active in campaign '{s.get('campaign_name')}'. Campaign choice cleared. Offer another existing campaign or a new one."
        return ""

    def _show_contacts(self, change_recipients: bool = False) -> str:
        s = self.state
        if self.recipients_committed:
            return "error: you just committed recipients this turn. Do not show contacts; show the campaigns table or continue."
        if s.get("recipients") and not change_recipients:
            names = ", ".join(r["email"] for r in s["recipients"])
            return f"error: recipients already committed ({names}). Do not show the contacts table; to switch people call update_selection."
        rows = [
            {"name": str(p.get("name") or ""), "email": str(p.get("email") or "")}
            for p in _people(self.records)
            if p.get("email")
        ]
        if not rows:
            return "error: no saved contacts"
        self.people_to_show = rows
        s.setdefault("shown", {})["people"] = rows
        return json.dumps({"table_shown_in_order": rows})

    def _show_campaigns(self, change_campaign: bool = False, show_all: bool = False) -> str:
        s = self.state
        if show_all:
            # Only looking: every saved campaign, no exclusions, and the pending selection stays as it is.
            rows = [
                {"name": str(c.get("campaign_name") or ""), "timing": _campaign_timing(c)}
                for c in self.records.get("campaigns") or []
                if c.get("campaign_name")
            ]
            self.campaigns_to_show = rows
            pending = bool(s.get("recipients") or s.get("campaign_name"))
            return json.dumps(
                {
                    "table_shown_in_order": rows,
                    "pending_selection": pending,
                    "note": (
                        "informational only: the user's pending selection is unchanged. Say in one short sentence that "
                        "these are their campaigns."
                        + (" Also say their earlier request is still waiting." if pending else " Do not mention any pending request: there is none.")
                    ),
                }
            )
        if not s.get("recipients"):
            return "error: commit recipients first so I can exclude campaigns they are already active in"
        if s.get("campaign_name") and not change_campaign:
            return f"error: campaign '{s['campaign_name']}' is already committed. Do not show the table again; continue to the email."
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        refusals, _needs, _readds = self._gate(statuses)
        if refusals:
            return json.dumps({"error": "refused_person", "reasons": refusals})
        blocked: set[str] = set()
        for st in statuses:
            blocked |= {link.campaign_id for link in st.blocking}
        for r in s["recipients"]:
            blocked |= _active_ids(_find_person(self.records, r["email"]))
        rows = [
            {"name": str(c.get("campaign_name") or ""), "timing": _campaign_timing(c)}
            for c in self.records.get("campaigns") or []
            if c.get("campaign_name") and str(c.get("campaign_id") or "") not in blocked
        ]
        self.campaigns_to_show = rows
        s.setdefault("shown", {})["campaigns"] = rows
        return json.dumps(
            {
                "table_shown_in_order": rows,
                "excluded_because_already_active_ids": sorted(blocked),
                "note": "empty table means no other saved campaign is available; ask for a new campaign name and daily window",
            }
        )

    def _propose_email(self, subject: str, body: str, change_evidence: str = "") -> str:
        s = self.state
        miss = _missing(s)
        if miss:
            return f"error: cannot propose yet, still missing: {', '.join(miss)}"
        conflict = self._conflict()
        if conflict:
            return f"error: {conflict}"
        blocked = self._blocked()
        if blocked:
            return blocked
        if not (subject or "").strip() or not (body or "").strip():
            return "error: subject and body required"
        subject, body = subject.strip(), body.strip()
        fields = self._fields()
        subject, body = tokenize(subject, fields), tokenize(body, fields)
        # Campaign first emails keep the assessment CTA in the body even if the model omitted it.
        # Never put it on the subject line.
        subject = strip_assessment_link(subject)
        body = keep_assessment_link(body)
        prev, prev_scope = s.get("draft") or {}, s.get("scope")
        if prev and (prev.get("email_subject"), prev.get("email_body")) == (subject, body):
            return "ok: this is the draft that is already shown. Do not call propose_email again; answer the user in words."
        if prev and _WHOLE_EMAIL.search(self.user_message or "") and _CHANGE_VERB.search(self.user_message or ""):
            subject_changed = (prev.get("email_subject") or "") != subject
            body_changed = (prev.get("email_body") or "") != body
            if subject_changed != body_changed:
                return json.dumps(
                    {
                        "error": "change_both",
                        "changed_only": "subject" if subject_changed else "body",
                        "instruction": (
                            "The user asked to change the whole email, subject and body. You changed only the "
                            f"{'subject' if subject_changed else 'body'}. Call propose_email again with BOTH a new "
                            "subject and a new body that match what the user asked for."
                        ),
                    }
                )
        if prev.get("user_edited") and not (change_evidence and self._user_said(change_evidence)):
            return json.dumps(
                {
                    "error": "user_edited_draft",
                    "current_subject": prev.get("email_subject"),
                    "current_body": prev.get("email_body"),
                    "instruction": (
                        "The user edited this draft themselves in the card. Keep their wording. Call propose_email again only if "
                        "the user's latest message asks for a change; then build on the current text above and pass "
                        "change_evidence with their words. For questions about the draft, answer in words."
                    ),
                }
            )
        self._invalidate_draft()
        if prev_scope and (prev.get("email_subject"), prev.get("email_body")) == (subject, body):
            s["scope"] = prev_scope
        s["draft"] = {
            "id": uuid.uuid4().hex[:10],
            "email_subject": subject,
            "email_body": body,
            "email_template": True,
            "email_draft": "",
            "email_from": ", ".join(sending_account_emails()),
        }
        s.pop("previous_draft", None)
        refresh_preview(self.records, s)
        self.card_shown = True
        self.people_to_show, self.campaigns_to_show = [], []
        s["shown"] = {"people": [], "campaigns": []}
        return "ok: draft is shown to the user as a card. Ask one short question about it."

    def _payload(self) -> dict:
        s, d = self.state, self.state["draft"]
        emails = [r["email"] for r in s["recipients"]]
        return {
            "email_address": emails[0] if len(emails) == 1 else "",
            "email_address_list": emails,
            "person_name": " and ".join(r["name"] for r in s["recipients"] if r.get("name")),
            "campaign_id": s.get("campaign_id") or "",
            "campaign_name": s["campaign_name"],
            "campaign_schedule_timing": s["window"],
            "move_from": list(s.get("moves") or []),
            "readds": list(s.get("readds") or []),
            "email_from": d["email_from"],
            "email_template": bool(d.get("email_template")),
            "email_scope": s.get("scope") or "",
            "people_fields": [{**effective_fields(f), "first_is_fallback": not f["first_name"]} for f in self._fields()],
            "email_subject": d["email_subject"],
            "email_body": d["email_body"],
            "email_draft": d["email_draft"],
            "tool": "execute",
        }

    def _approve_move(self, emails=None, approval_evidence: str = "") -> str:
        s = self.state
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        by_email = {st.email.lower(): st for st in statuses}
        accepted, readds_accepted, problems = [], [], []
        for raw in emails or []:
            key = str(raw or "").strip().lower()
            st = by_email.get(key)
            if st is None:
                problems.append(f"{raw}: not one of the chosen recipients")
                continue
            if st.tier in ("refuse_opted_out", "refuse_conversation"):
                problems.append(f"{st.label}: cannot be moved ({st.tier})")
                continue
            stopped = self._stopped_in_committed(st)
            if stopped:
                info = stopped[0]
                if info.conversation or info.opted_out:
                    problems.append(f"{st.label}: already replied, started the assessment, or booked, so they cannot be added back")
                elif key not in self.offered_before:
                    problems.append(
                        f"{st.label}: you have not explained this to the user in an earlier turn. Explain it, ask, and wait for their answer."
                    )
                else:
                    readds_accepted.append(key)
                continue
            if not st.tier.startswith("move_"):
                problems.append(f"{st.label}: nothing to move, they are not active in another campaign")
            elif key not in self.offered_before:
                problems.append(
                    f"{st.label}: you have not explained this to the user in an earlier turn. Explain it, ask, and wait for their answer."
                )
            else:
                accepted.append(key)
        if problems:
            return json.dumps({"error": "move_not_approved", "problems": problems, "recorded": []})
        s["moves"] = sorted(set(s.get("moves") or []) | set(accepted))
        s["readds"] = sorted(set(s.get("readds") or []) | set(readds_accepted))
        self._invalidate_draft()
        return json.dumps(
            {
                "ok": True,
                "moves_approved_for": s["moves"],
                "readds_approved_for": s["readds"],
                "approval_evidence": (approval_evidence or "")[:200],
            }
        )

    def _remove_from_campaign(self, email="", campaign="", approval_evidence="") -> str:
        address = str(email or "").strip()
        raw_campaign = str(campaign or "").strip()
        if "@" not in address or not raw_campaign:
            return json.dumps({"error": "Say who to remove and which campaign."})
        db = SessionLocal()
        try:
            try:
                row = db.get(Campaign, uuid.UUID(raw_campaign))
            except ValueError:
                row = db.scalar(select(Campaign).where(func.lower(Campaign.name) == raw_campaign.lower()))
            if row is None:
                return json.dumps({"error": f"I could not find a campaign called {raw_campaign}."})
            statuses = describe_people(db, [address])
            st = statuses[0] if statuses else None
            info = next(
                (link for link in (st.links if st else []) if link.campaign_id == str(row.id)),
                None,
            )
            who = (split_executive_name(st.name)[0] if st and st.name else "") or (st.label if st else address)
            camp_name = row.name
            if info is None:
                return json.dumps({"error": f"{who} is not in {camp_name}."})
            if info.opted_out:
                return json.dumps(
                    {"error": f"{who} unsubscribed or bounced in {camp_name}, so they cannot be removed from it."}
                )
            if info.conversation:
                return json.dumps(
                    {
                        "error": (
                            f"{who} already replied, started the assessment, or booked in {camp_name}, "
                            "so they cannot be removed from it."
                        )
                    }
                )
            if info.outreach_status == OutreachStatus.STOPPED.value:
                return json.dumps({"error": f"{who} is already stopped in {camp_name}."})
            already_emailed = info.contacted or info.outreach_status != OutreachStatus.PENDING.value
            why_not = approval_problem(self.user_message, approval_evidence)
            if why_not:
                if already_emailed:
                    instruction = (
                        f"{who} already got the email in {camp_name}. Removing them cannot unsend it, but it stops their follow-ups "
                        f"there. Ask ONE yes/no, for example: Stop follow-ups for {who} in {camp_name}? Call remove_from_campaign "
                        "only after their NEXT message says yes."
                    )
                else:
                    instruction = (
                        f"Ask one yes/no, for example: Remove {who} from {camp_name}? They have not been emailed yet. "
                        "Call remove_from_campaign only after their NEXT message says yes."
                    )
                return json.dumps({"error": "not_approved", "reason": why_not, "instruction": instruction})
            try:
                client = InstantlyService(db).get_api_client()
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"error": str(exc) or "Could not reach Instantly. Nothing was changed."})
            if already_emailed:
                link_row = db.scalar(
                    select(CampaignProspect)
                    .options(selectinload(CampaignProspect.campaign), selectinload(CampaignProspect.prospect))
                    .where(CampaignProspect.id == info.link_id)
                )
                if link_row is None:
                    return json.dumps({"error": f"{who} is not in {camp_name}."})
                try:
                    lead_id = _instantly_lead_id(client, link_row)
                    if lead_id:
                        try:
                            client._request("DELETE", f"/leads/{lead_id}", json={})
                        except InstantlyError as exc:
                            if getattr(exc, "status_code", None) != 404:
                                raise
                except InstantlyError as exc:
                    return json.dumps({"error": str(exc) or "Instantly did not remove them. Nothing was changed."})
                link_row.outreach_status = OutreachStatus.STOPPED
                db.add(link_row)
                db.commit()
                done = (
                    f"Stopped follow-ups for {who} in {camp_name}. "
                    "The email already sent cannot be taken back."
                )
            else:
                try:
                    stop_for_move(db, client, info.link_id)
                except StopError as exc:
                    return json.dumps({"error": str(exc) or "Instantly did not remove them. Nothing was changed."})
                done = f"Removed {who} from {camp_name}."
        finally:
            db.close()
        s = self.state
        kept = [r for r in s.get("recipients") or [] if str(r.get("email") or "").lower() != address.lower()]
        if kept != (s.get("recipients") or []):
            s["recipients"] = kept
            _keep_approvals(s, {str(r.get("email") or "").lower() for r in kept})
            self._invalidate_draft()
        return done

    # ---- pause / resume / delete (same services as the admin buttons) ----

    def _confirm_destructive(self, key: str, approval_evidence: str, question: str) -> str:
        """Empty string only when the user already SAW this question in an earlier turn and now clearly says yes.

        Otherwise returns the not_approved result that tells the model to ask the one yes/no."""
        why_not = approval_problem(self.user_message, approval_evidence)
        if key in self.delete_offered_before and not why_not:
            return ""
        offers = self.state.setdefault("delete_offered", [])
        if key not in offers:
            offers.append(key)
        return json.dumps(
            {
                "error": "not_approved",
                "reason": why_not or "the user has not yet been asked this question",
                "instruction": (
                    f"Nothing was changed. Ask the user ONE yes/no question, in your own words, with these facts: {question} "
                    "Call this tool again only after their NEXT message says yes, and pass their words as approval_evidence."
                ),
            }
        )

    def _show_campaign_people(self, campaign="") -> str:
        """Read-only: who is in one campaign, and where each person stands."""
        words = {
            "pending": "waiting (not emailed yet)",
            "active": "emailed",
            "replied": "replied",
            "assessment_started": "started the assessment",
            "completed": "completed",
            "bounced": "bounced",
            "unsubscribed": "unsubscribed",
            "cold": "cold",
            "stopped": "stopped",
        }
        db = SessionLocal()
        try:
            row = _campaign_row(db, campaign)
            if row is None:
                return json.dumps({"error": f"I could not find a campaign called {campaign}."})
            name = row.name
            links = db.scalars(
                select(CampaignProspect)
                .options(selectinload(CampaignProspect.prospect))
                .where(CampaignProspect.campaign_id == row.id)
            ).all()
            people = []
            for link in links:
                person = link.prospect
                raw = getattr(link.outreach_status, "value", link.outreach_status)
                status = words.get(str(raw), str(raw))
                if link.booked_at is not None or (link.booking_status or "").strip().lower() == "confirmed":
                    status += ", booked"
                people.append(
                    {
                        "name": str(getattr(person, "executive_name", "") or "") if person else "",
                        "email": str(getattr(person, "email", "") or "") if person else "",
                        "status": status,
                    }
                )
        finally:
            db.close()
        people.sort(key=lambda p: (p["name"] or p["email"]).casefold())
        return json.dumps(
            {
                "campaign": name,
                "total": len(people),
                "people": people,
                "note": "Read-only. Answer with a short table (name, email, status). If there are no people, say the campaign is empty.",
            }
        )

    def _pause_campaign(self, campaign="") -> str:
        db = SessionLocal()
        try:
            row = _campaign_row(db, campaign)
            if row is None:
                return json.dumps({"error": f"I could not find a campaign called {campaign}."})
            name = row.name
            try:
                CampaignActivationService(db).pause(row.id)
            except (ActivationError, CampaignNotFoundError, CampaignValidationError) as exc:
                return json.dumps({"error": getattr(exc, "message", None) or str(exc) or "Instantly did not pause it. Nothing changed."})
        finally:
            db.close()
        _audit_change(self.ctx, "pause_campaign", {"campaign": name})
        return f"Paused {name}. Nothing is emailed from it until it is resumed."

    def _resume_campaign(self, campaign="", approval_evidence="") -> str:
        db = SessionLocal()
        try:
            row = _campaign_row(db, campaign)
            if row is None:
                return json.dumps({"error": f"I could not find a campaign called {campaign}."})
            name = row.name
            gate = self._confirm_destructive(
                f"resume_campaign:{row.id}",
                approval_evidence,
                f"Resume the {name} campaign? Emails to the people still waiting in it may start sending in its send window.",
            )
            if gate:
                return gate
            try:
                CampaignActivationService(db).activate(row.id, confirm=True)
            except (ActivationError, CampaignNotFoundError, CampaignValidationError) as exc:
                detail = getattr(exc, "message", None) or str(exc) or "Instantly did not resume it."
                missing = getattr(exc, "missing", None) or []
                if missing:
                    detail = f"{detail} {' '.join(str(m) for m in missing)}"
                return json.dumps({"error": detail.strip()})
        finally:
            db.close()
        _audit_change(self.ctx, "resume_campaign", {"campaign": name})
        return f"Resumed {name}. Its emails go out inside the campaign's send window."

    def _delete_campaign(self, campaign="", approval_evidence="") -> str:
        db = SessionLocal()
        try:
            row = _campaign_row(db, campaign)
            if row is None:
                return json.dumps({"error": f"I could not find a campaign called {campaign}."})
            name, row_id = row.name, row.id
            counts = _campaign_counts(db, row_id)
            question = (
                f"Delete the {name} campaign from FreedomBot and from Instantly? It has {counts['people']} people: "
                f"{counts['emailed']} emailed, {counts['replied']} replied, {counts['assessment']} started the assessment, "
                f"{counts['booked']} booked. "
            )
            if counts["replied"] or counts["assessment"] or counts["booked"]:
                question += "Their replies, assessment chats and bookings in this campaign are deleted too. "
            question += "The people stay in the workspace. This cannot be undone."
            gate = self._confirm_destructive(f"delete_campaign:{row_id}", approval_evidence, question)
            if gate:
                return gate
            try:
                CampaignService(db).delete_campaign(row_id)
            except (CampaignNotFoundError, CampaignValidationError) as exc:
                return json.dumps({"error": getattr(exc, "message", None) or str(exc) or "The campaign was not deleted."})
        finally:
            db.close()
        s = self.state
        if str(s.get("campaign_id") or "") == str(row_id):
            s["campaign_id"], s["campaign_name"], s["window"] = "", "", []
            self._invalidate_draft()
        _audit_change(self.ctx, "delete_campaign", {"campaign": name, **counts})
        return f"Deleted the {name} campaign from FreedomBot and Instantly."

    def _delete_prospect(self, email="", approval_evidence="") -> str:
        address = str(email or "").strip()
        if "@" not in address:
            return json.dumps({"error": "Say which person to delete (their email address)."})
        db = SessionLocal()
        try:
            prospect = db.scalar(select(Prospect).where(func.lower(Prospect.email) == address.lower()))
            if prospect is None:
                return json.dumps({"error": f"I could not find {address}."})
            prospect_id = prospect.id
            statuses = describe_people(db, [address])
            st = statuses[0] if statuses else None
            who = (st.label if st else "") or address
            infos = list(st.links) if st else []
            question = f"Delete {who} completely from FreedomBot? "
            if infos:
                names = sorted({i.campaign_name or "a campaign" for i in infos})
                question += f"They are in {', '.join(names)}, and any lead in Instantly is removed so no more emails go out. "
            if any(i.conversation for i in infos):
                question += "Their replies, assessment chats and bookings are deleted too. "
            if any(i.opted_out for i in infos):
                question += (
                    "They unsubscribed or bounced earlier: deleting them also erases that record, so they could be added "
                    "and emailed again. "
                )
            question += "This cannot be undone."
            gate = self._confirm_destructive(f"delete_prospect:{prospect_id}", approval_evidence, question)
            if gate:
                return gate
            link_rows = db.scalars(
                select(CampaignProspect)
                .options(selectinload(CampaignProspect.campaign), selectinload(CampaignProspect.prospect))
                .where(CampaignProspect.prospect_id == prospect_id)
            ).all()
            if link_rows:
                try:
                    client = InstantlyService(db).get_api_client()
                except Exception as exc:  # noqa: BLE001
                    return json.dumps({"error": str(exc) or "Could not reach Instantly. Nothing was deleted."})
                for link_row in link_rows:
                    try:
                        lead_id = _instantly_lead_id(client, link_row)
                        if lead_id:
                            try:
                                client._request("DELETE", f"/leads/{lead_id}", json={})
                            except InstantlyError as exc:
                                if getattr(exc, "status_code", None) != 404:
                                    raise
                    except InstantlyError as exc:
                        return json.dumps(
                            {
                                "error": (
                                    (str(exc) or "Instantly did not remove them.")
                                    + " The person was not deleted from FreedomBot; some of their Instantly leads may already be gone."
                                )
                            }
                        )
            try:
                ProspectService(db).delete_prospect(prospect_id)
            except ProspectNotFoundError:
                return json.dumps({"error": f"I could not find {address}."})
        finally:
            db.close()
        s = self.state
        kept = [r for r in s.get("recipients") or [] if str(r.get("email") or "").lower() != address.lower()]
        if kept != (s.get("recipients") or []):
            s["recipients"] = kept
            _keep_approvals(s, {str(r.get("email") or "").lower() for r in kept})
            self._invalidate_draft()
        _audit_change(self.ctx, "delete_prospect", {"email": address})
        return f"Deleted {who} from FreedomBot, and removed their leads from Instantly."

    def _send_approved(self, approval_evidence: str = "") -> str:
        if not self.state.get("draft"):
            return "error: no draft pending, nothing to send"
        why_not = approval_problem(self.user_message, approval_evidence)
        if why_not:
            return json.dumps(
                {
                    "error": "not_approved",
                    "reason": why_not,
                    "instruction": (
                        "Nothing was sent. Answer what the user actually asked, in words, and ask one short question: "
                        "send it as is, or change something?"
                    ),
                }
            )
        if self.send_block:
            return f"error: {self.send_block}"
        conflict = self._conflict()
        if conflict:
            return f"error: {conflict}"
        blocked = self._blocked()
        if blocked:
            return blocked
        view = draft_view(self.records, self.state)
        audience = view.get("audience") or {}
        if audience.get("scope_needed"):
            return json.dumps(
                {
                    "error": "needs_scope",
                    "campaign": audience.get("campaign"),
                    "changed_parts": audience.get("changed_parts"),
                    "first_difference": audience.get("first_difference"),
                    "also_waiting": [p["name"] for p in audience.get("also_waiting") or []],
                    "instruction": (
                        "This email differs from the campaign's saved first email, and these people are in the campaign "
                        "but have not been emailed yet, so they would get the changed wording too. Tell the user this edit "
                        "also changes the email for the names in also_waiting, and ask ONE short yes/no question, e.g. "
                        '"This also changes the email for Gus and Nina. Go ahead?" Do not mention keep or the campaign\'s '
                        "current email. If they say yes, call choose_email_scope with scope everyone, then send_approved "
                        "again. If they say no, do not send and do not call choose_email_scope; reply: "
                        '"Okay, nothing sent. Want to change the wording back to the campaign\'s email, or edit it more?"'
                    ),
                }
            )
        payload = self._payload()
        try:
            outcome = fulfill_approved_email(self.ctx, payload)
        except FulfillError as exc:
            if exc.campaign_id:
                self.state["campaign_id"] = exc.campaign_id
            _audit("ACTION_FAILED", self.ctx, payload, error=str(exc)[:300], evidence=approval_evidence)
            return f"error: {exc}"
        self.sent = True
        self.state["campaign_id"] = outcome.campaign_id
        _audit(
            "ACTION_EXECUTED",
            self.ctx,
            payload,
            result=outcome.message[:300],
            evidence=approval_evidence,
            prior_emails=list(outcome.prior_emails),
        )
        return f"scheduled (not delivered yet): {outcome.message}"




# ---------- Agno wiring ----------

def build_campaign_tools(session: CampaignSession) -> list[Callable]:
    """Agno callables for the campaign tools, all bound to one ``CampaignSession``."""
    tools: list[Callable] = []
    for spec in CAMPAIGN_TOOLS:
        fn = spec["function"]
        name = fn["name"]

        def _make(tool_name: str, description: str, schema: dict) -> Callable:
            @tool(name=tool_name, description=description)
            def _invoke(**kwargs: Any) -> str:
                return session.call(tool_name, dict(kwargs))

            # Replace Agno's **kwargs schema with the real one.
            _invoke.parameters = schema
            _invoke.strict = False
            _invoke.skip_entrypoint_processing = True
            return _invoke

        tools.append(_make(name, fn["description"], fn["parameters"]))
    return tools


####################################################################################################
# PART 4 - THE PROMPT
####################################################################################################

# ------------------------------------------------------------------------------------------------
# prompts  (app/assistant/prompts.py) - THE prompt. Everything the model is told lives in this one file.
# ------------------------------------------------------------------------------------------------

from bizos.agents import instructions as agent_instructions

MAX_PEOPLE_IN_PROMPT = 200

APPROACH = """
# HOW YOU WORK (every task, every turn)

You are one assistant for the whole workspace. You decide, from the conversation, the records and your
tool results, which tool to use, in which order, and what to say. The platform never writes your words:
it validates, returns facts (including why something was refused), and records what happened.

- Your answer is a JSON object with two fields. `message` is the only text the user sees: natural,
  specific, varied; never repeat the same sentence twice in a row. `explanation` is for the developer
  trace and is never shown to the user.
- EVERY tool call needs `why`: one or two specific sentences on what you understood and why this call
  is right now.
- Call a tool only when its requirements are met. If something required is missing, ask for that one
  thing. If a tool returns an error, fix it or tell the user honestly. Never claim something was saved,
  sent, queued or scheduled unless a tool result says so.
- Before answering, check your own draft against the tool results of this turn: does it claim anything
  no tool did? Does it mention anything the user is not allowed to see? Does it ask something the user
  already told you?
- If a "Self-check" note follows your draft, a platform check flagged it. Fix the reply in your own
  words, using tools if needed, then answer again.
- `explanation` content (5 to 9 sentences): (a) what you understood the user to mean, including how you
  resolved references like "first 2" or "that one"; (b) what is decided vs still missing; (c) which tools
  you called, or deliberately did not, and why; (d) what you are showing or not showing and why, and what
  the user should do next; (e) why you took this action rather than another.
- Greetings, small talk, thanks, typos: answer like a person. Do not start any flow or call any tool.
"""

CAMPAIGN_SECTION = """
# OUTBOUND CAMPAIGNS (admin only; the campaign tools are in your tool list)

Use the campaign tools when the user wants to email prospects as an outbound campaign: pick people, pick or
create a campaign, review one email, send it once they approve. These are different from the email_* tools,
which send one message from the user's own mailbox (a reply, a one-off note). "Add them to a campaign",
"campaign", "my people / contacts / prospects" (the list below) point at the campaign tools. "Email John
about the invoice" or "reply to this thread" points at email_*. If it is genuinely unclear, ask one short
question. The "people" below are Acquisition prospects; CRM contacts are separate.

Every turn you get a "Current state" block. It is the truth: the committed selection (recipients, campaign,
daily send window), what is still missing, the exact rows you last showed the user in tables (in order), the
pending draft, and people_status for the chosen recipients.

1. Read the latest message, the conversation and Current state. Resolve references ("first 2", "both",
   "that one", "Craig", "same as before") against "last shown" or the committed selection.
2. The moment the user's meaning gives you a fact (recipients, campaign, hours), COMMIT it with
   update_selection in this same turn. Do not wait, do not re-ask, do not show the table again.
   COMMIT FIRST, ASK SECOND: even when something is still missing, call update_selection with everything
   you already know BEFORE you write your question. Asking a question without committing what the user
   already told you is wrong, because the next turn starts from what was committed.
   Example: "email Ethan in a new campaign called snowflake" gives you the recipient (Ethan) and the
   campaign name (snowflake). Call update_selection with both now. Then ask only for the missing daily
   send window. (If the name is close to an existing campaign you will get similar_campaign_name; then ask
   the existing-or-new question.)
3. Then do the next useful thing: ask for the one missing item, show the table that item needs, or propose
   the email.

TABLES (UI cards)
- The contacts table appears only if you call show_contacts; the campaigns table only if you call
  show_campaigns. Show one only when the user must choose and you do not yet have the answer. Never show
  contacts after recipients are committed, and never "just in case".
- If the user only asks to SEE their campaigns ("show my campaigns", "what campaigns do I have"), call
  show_campaigns with show_all=true right away, whatever else is pending. Do not ask who to email, do not
  change the pending selection, and answer in one short sentence.
- If the user has not named anyone to email and no recipient is committed, call show_contacts in that SAME
  turn (after saving anything they did say, such as the campaign name). Never ask "who?" in words alone and
  never wait for them to ask to see the list: they cannot choose from people they cannot see.
- Right after you commit recipients, the next table is the CAMPAIGNS table (unless they already named a
  campaign). Asking "which campaign?" without showing it leaves the user blind.
- After the user picks a campaign, commit it; do not show the table again.
- "add last 2", "also Debbie", "both": resolve against the known people order or the last shown rows and
  commit with update_selection (recipients_mode "add" to keep current ones, "replace" to switch).
- Never list names, emails or campaigns in your message. The table is the list; after a table your message
  is one short question.

MOVING FORWARD
- Once recipients, campaign and window are all committed, call propose_email in that same turn. Never ask
  "do you want to proceed" and never re-confirm a choice they already made. The email card is the only
  confirmation step. Never raise a caveat that did not come from a tool result.

WHAT AN EMAIL NEEDS (all committed, then propose_email)
1. recipients from the known people, matched loosely; an address the user types that isn't known is fine;
2. a campaign: an existing one (show_campaigns) or a new one (needs a name);
3. a daily send window: two clock times (e.g. 9:00 am to 5:00 pm), never calendar dates. An existing
   campaign brings its own saved window. For a NEW campaign the hours must come from the user's own words:
   never invent, guess or default them. If they have not said the hours, ask for them, and pass their words
   as window_evidence when you commit.

PEOPLE ALREADY IN A CAMPAIGN (one person = one active campaign)
- Current state and every update_selection result carry people_status: a tier per person and a what_to_do
  line. Follow it. Only say what people_status says.
- free: add normally, say nothing about it.
- move_not_contacted / move_contacted: the person is active in another campaign (active_in names it). Say
  plainly where they are and that adding them here means stopping them there (their history stays). Ask ONE
  clear yes/no. Only after the user's NEXT message says yes, call approve_move. Never in the same turn you
  first ask, never on a vague answer. If they say no, offer to drop that person or choose differently.
- readd_stopped / needs_readd_approval: they were stopped in THIS campaign earlier (for example after
  remove_from_campaign). Do not add them yet. Say they were stopped here, and that adding them again sends
  the whole email sequence again, including the first email. Ask ONE yes/no, for example: "<who> was stopped
  in <campaign> earlier. If I add them again they get the whole email sequence again, including the first
  email. Add them back?" Only after the user's NEXT message says yes, call approve_move. If they say no,
  change nothing. If they stopped themselves (replied stop / unsubscribe), a tool returns a refusal: keep it.
- refuse_conversation: they replied, started the assessment or booked. Do not move or add them; say so and
  suggest handling them personally. Offer to continue without them.
- refuse_opted_out: they unsubscribed or bounced. Never add them. Say so plainly; offer to continue without.
- If a tool returns needs_move_approval, needs_readd_approval or a refusal, explain it in plain words, then wait for the user.
- NEVER ask the same thing twice and never make the user confirm in two steps. Say the move or re-add in the
  question itself, as part of ONE question. If you explain a move or re-add without asking yes/no, that wastes a turn: don't.
- If a name question (similar name), a move and a re-add apply together, ask ONE combined question, for example:
  "Do you mean the existing 'spring' (not 'soring')? Nina is in 'duo', so adding her stops her there. Raj was
  stopped in spring earlier; adding him back sends the whole sequence again, including the first email. Yes?"
  When the user then answers yes, or names the campaign, or says "go ahead", "ok" or any agreement, that answers
  ALL of them: commit the campaign (campaign_id) AND call approve_move in that same turn, then continue. Do not
  ask about the move or re-add again. Only if their answer clearly changes the plan (a different campaign, or "no")
  do you ask again.
- If you already stated the move or re-add in your last message and they agree in any wording, it is approved.

REMOVING SOMEONE FROM A CAMPAIGN
- "remove Raj from east" means take that person out of that campaign only. They stay in the workspace; their history stays.
- If they have not been emailed, ask one yes/no and remove. If they were already emailed, say plainly that the email is already sent and cannot be taken back, and that removing them only stops their follow-ups; ask ONE yes/no. If they replied, booked, unsubscribed or bounced, say so and change nothing.
- Ask ONE yes/no first. Only after the user's NEXT message says yes, call remove_from_campaign. Never in the same turn you first ask, never on a vague answer, never choose for them. If they say no, change nothing.
- After the tool returns, reply in one line with that sentence (for example "Removed Raj from east."). If it returns an error, tell the user that error in plain words; change nothing.

WHO IS IN A CAMPAIGN
- "who is in Harbor Light Test?", "list the people in <campaign>": call show_campaign_people with that campaign and
  answer with a short table (name, email, status). It is read-only: do not change the pending selection or ask who to email.

PAUSE, RESUME AND DELETE
- "pause" or "stop" a campaign: call pause_campaign right away (it is reversible). Reply with the one sentence it returns.
- "resume", "restart" or "turn it back on": call resume_campaign. It returns a yes/no question; ask it. Only after the
  user's NEXT message says yes, call it again with their words as approval_evidence.
- "delete" a campaign: call delete_campaign. It returns a yes/no question with the counts; ask exactly that, in your own
  words. Only after the user's NEXT message says yes, call it again with their words as approval_evidence. Never delete
  because the user said "stop" or "pause".
- "delete" a person (a prospect): call delete_prospect with their email, the same way. This is different from removing
  someone from one campaign; use remove_from_campaign when they only want them out of one campaign.
- Never pause, resume or delete unless the user asked for it in this message. Never answer the yes/no for them. If they say
  no, change nothing. After a tool succeeds, reply with its one sentence; if it returns an error, tell the user that error in
  plain words. Deleting many people or campaigns at once ("delete all prospects") is not supported here: say so and ask
  for one at a time.

SIMILAR CAMPAIGN NAMES
- If update_selection returns similar_campaign_name, do not guess. Ask: "Do you mean the existing <similar
  name>, or a new campaign called <typed name>?" Then commit the existing one by campaign_id, or call
  update_selection again with confirmed_new_campaign=true. Do this even if the user already said "new".
  If someone also needs to be moved or added back, put that in this same question (see above); one question only.

EMAIL AND SENDING
- The email card is shown by propose_email, once per new draft. Do NOT call propose_email to answer questions
  about the draft ("what is the subject?", "is it okay?", "hmm maybe"): answer in one short sentence of words and
  never paste the email text. Call it again only when the user asks for a change. If the user edited the draft in
  the card, their wording stays; build any requested change on top of it.
- For a NEW campaign use the template from the records (email_to_show for a known person, otherwise
  email_without_company) exactly, except change "Hi there," to "Hi {first name},". For an EXISTING campaign
  start from that campaign's own initial_email in the records, unchanged, unless the user asks for changes.
  The platform turns real first names and company names into blanks ({{firstName}}, {{companyName}}), so
  each person gets their own; you do not need to. Keep the assessment URL in the body only (the real
  link in the records, or {{assessment_url}} if the campaign's email already uses that blank). Never put
  it in the subject. That is not a name placeholder — never omit it from the body. Apply edits the user
  asks for, then propose_email again. After propose_email succeeds the UI shows the email card; your
  message is one short question about it (approve as is, or change something). Do not paste the subject
  or body.
- Call send_approved only when a draft is pending AND the user's latest message clearly approves it, in any
  wording. Changes requested or ambiguous means do not send; ask. After it returns, confirm in one natural
  sentence using only what the tool returned. The email is SCHEDULED in Instantly, not delivered: say the
  campaign is created and the email is scheduled to go out in its send window; never say it was "sent" or
  "delivered". If send_approved returns an error, tell the user that error in plain
  words and what they can do; never retry blindly and never change the hours or anything else on your own.
- If the state has previous_draft_wording_reuse_it, the user already had an email draft and only the people,
  campaign or send window changed. Call propose_email with exactly that subject and body (keep its {{blanks}}
  as they are). Never go back to the saved campaign template or write a new email unless the user asked for
  new wording.
- CHANGING THE EMAIL: decide what the user wants changed before you call propose_email. If they name only the
  subject, change only the subject. If they name only the body, change only the body. If they say "the email",
  "the whole email", "everything", "both", or give a topic or theme ("make it about X", "anything related to X"),
  change BOTH the subject and the body so they match, in the same propose_email call. Never change just one of
  them when the request covers both. Before you call propose_email, check that the new subject and body
  together answer every part of the user's request.
- With several recipients write ONE greeting, "Hi there,". The platform greets each person by their own first
  name. Never list the people in the greeting ("Hi Nina and Raj,") and never type a name placeholder such as
  {first name} yourself: write the greeting in plain words. Do keep the assessment URL in the body from the
  template. Never put it in the subject.
- Names and companies: the card shows each person's details and where they came from. A name the user gave
  with the email always wins — put it on the recipient (and set_person_details with their words). If they
  only gave an email, judge a human first and last name from the mailbox itself when a name is clearly
  there, and call set_person_details with evidence = that email before you propose. If you are unsure,
  still try to read a name from the address rather than leaving it blank. Never replace a given name with
  a mailbox guess. Never invent a company from the address. If the mailbox is not a person's name, leave
  it missing (the greeting stays "Hi there,").
- If send_approved returns needs_scope, the user changed an existing campaign's email and other people in it
  have not been emailed yet, so they would get the change too. Tell the user this edit also changes the email
  for the names in also_waiting, and ask ONE short yes/no question, e.g. "This also changes the email for Gus
  and Nina. Go ahead?" Never offer keep, never mention the campaign's current email as a choice, and never
  choose for them. If they say yes, call choose_email_scope with everyone, then send_approved again. If they
  say no, do not send and do not call choose_email_scope; reply: "Okay, nothing sent. Want to change the
  wording back to the campaign's email, or edit it more?"
  Ask this question at most ONCE. If the user already said earlier in this conversation that the change is for
  everyone in the campaign ("change it for everyone", "for all of them"), do not ask: call choose_email_scope
  with everyone, using those words as approval_evidence, then send_approved. After you have asked, any
  approving answer ("yes", "send", "send it", "go ahead", "ok") means yes: call choose_email_scope with
  everyone using their words, then send_approved in the same turn. Never repeat the question.
- Never invent people, addresses, campaigns or drafts. The records below are the only truth.
"""


def _records_text(records: dict) -> str:
    """The records block for the prompt. Capped so a large prospect list cannot blow up every turn."""
    people = records.get("people") or []
    shown = dict(records)
    if len(people) > MAX_PEOPLE_IN_PROMPT:
        shown["people"] = people[:MAX_PEOPLE_IN_PROMPT]
        shown["people_note"] = (
            f"{len(people) - MAX_PEOPLE_IN_PROMPT} more people exist but are not listed. If the user names "
            "someone who is not listed, ask for their email address."
        )
    return json.dumps(shown, ensure_ascii=False)



def build_prompt(*, ctx, settings, scope, packs, grounded_text: str, session, records: dict) -> str:
    """The campaign system prompt for one turn. ``session`` is None when campaigns are not available."""
    text = APPROACH
    if session is not None:
        text += "\n" + CAMPAIGN_SECTION
        text += "\n## Campaign records (the only truth for people and campaigns)\n" + _records_text(records)
        text += "\nSending accounts: " + ", ".join(sending_account_emails()) + "\n"
        text += "\n" + campaign_state_text(session.state, session.status_view(), session._fields())
    return text


####################################################################################################
# PART 5 - THE AGENT (runs one chat turn)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# agent  (app/assistant/agent.py) - Campaign chat turn.
# ------------------------------------------------------------------------------------------------
import logging
import os
import re

from agno.agent import Agent
from agno.models.message import Message
from agno.run.base import RunStatus
from fastapi import HTTPException
from pydantic import BaseModel, Field

from bizos import chat_history
from bizos import settings as app_settings
from bizos.actions import store as actions
from bizos.agents import grounding
from bizos.agents.claims import enforce_claims
from bizos.agents.persona import AUTO, enforce_persona
from bizos.domains.packs import DomainPack, enabled_packs, get_pack
from bizos.tenancy.agnodb import workspace_db
from bizos.types import ActionStatus
from bizos.util.ids import new_id

log = logging.getLogger(__name__)

#: How many times the model is asked to rewrite a reply that failed a check.
MAX_NUDGES = 2
#: Upper bound on tool calls inside one model run.
TOOL_CALL_LIMIT = 16
#: People listed in the prompt. The rest are still found by exact address.
#: Tool names that are campaign state or display only; they never count as "doing" something.
_CAMPAIGN_READ_ONLY = {"show_contacts", "show_campaigns", "show_campaign_people"}
_CAMPAIGN_NAMES = {spec["function"]["name"] for spec in CAMPAIGN_TOOLS}

_SECTION_MARKER = re.compile(r"\[\[/?area(:\s*[a-z_]+\s*)?\]\]\n?", re.I)


class AssistantTurn(BaseModel):
    """What the model returns at the end of every turn."""

    message: str = Field(description="What the user sees. Natural, specific, no raw JSON.")
    explanation: str = Field(
        description=(
            "Developer trace, never shown to the user. 5-9 specific, honest sentences: what you understood, "
            "what is decided vs missing, which tools you called or deliberately did not and why, what you "
            "are showing and why, and what the user should do next."
        )
    )


# --------------------------------------------------------------------------- prompt

# --------------------------------------------------------------------------- model

_effort = {"tried": []}  # effort settings already rejected by the provider, so we stop retrying them
_api = {"responses": False}  # set when the provider says this model needs /v1/responses (never lowers reasoning)


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _effort_candidates() -> list[Optional[str]]:
    """Reasoning-effort settings to try, in order. ``None`` means do not send the parameter.

    By default ONLY the configured setting is used: medium means medium, and a refusal is shown as it is.
    Set MODEL_REASONING_FALLBACK=on to let the app retry with "none" and then without the parameter.
    """
    configured = (os.environ.get("MODEL_REASONING_EFFORT") or "").strip().lower() or None
    chain: list[Optional[str]] = [configured]
    if _truthy("MODEL_REASONING_FALLBACK"):
        # Some chat models accept function tools only with effort "none"; others reject the parameter.
        for fallback in ("none", None):
            if fallback not in chain:
                chain.append(fallback)
    return [e for e in chain if e not in _effort["tried"]] or [configured]


def _responses_model(**kwargs: Any):
    """A /v1/responses model that keeps each tool call together with its reasoning item.

    Agno only chains steps through ``previous_response_id`` for model names it knows (o3, o4-mini, gpt-5).
    For any other model that reasons (gpt-6-luna), it would resend the tool call WITHOUT its reasoning item
    and the provider refuses ("function_call was provided without its required reasoning item"). Treating
    the model as a reasoning model makes Agno chain the steps the supported way.
    """
    from agno.models.openai import OpenAIResponses

    class _ReasoningResponses(OpenAIResponses):
        def _using_reasoning_model(self) -> bool:
            return bool(self.reasoning_effort) or super()._using_reasoning_model()

    return _ReasoningResponses(**kwargs)


def _model(effort: Optional[str]):
    mid = app_settings.model_id()
    provider, _, name = mid.partition(":")
    if provider == "openai" and name:
        kwargs: dict[str, Any] = {"id": name}
        if effort:
            kwargs["reasoning_effort"] = effort
        # MODEL_API=responses uses /v1/responses. Newer models (for example gpt-6-luna) accept function
        # tools together with reasoning only there; /v1/chat/completions refuses that combination.
        if _api["responses"] or (os.environ.get("MODEL_API") or "chat").strip().lower() == "responses":
            return _responses_model(**kwargs)
        from agno.models.openai import OpenAIChat

        return OpenAIChat(**kwargs)
    return mid  # other providers: let Agno resolve "provider:model"


# --------------------------------------------------------------------------- turn bookkeeping

class _Turn:
    """Everything one chat turn accumulates across model runs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []  # ordered tool log (ops and campaign)
        self.tool_runs: list[Any] = []  # Agno ToolExecution records
        self.self_check: list[str] = []
        self.guardrail_events: list[str] = []
        self.nudges = 0

    def activity(self, session: Optional[CampaignSession]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call in self.tool_runs:
            name = getattr(call, "tool_name", None)
            if not name:
                continue
            ok = not getattr(call, "tool_call_error", False)
            row: dict[str, Any] = {"tool": name, "ok": ok}
            if name in _CAMPAIGN_NAMES:
                # Campaign tools have no registry spec and report failure in their result text.
                result = str(getattr(call, "result", "") or "")
                if result.startswith("error") or '"error"' in result[:40]:
                    ok = row["ok"] = False
                # ...so say here whether they changed anything.
                row["wrote"] = bool(ok and name not in _CAMPAIGN_READ_ONLY)
            out.append(row)
        return out


def _final(output: Any) -> tuple[str, str, str]:
    """(message, explanation, raw) from an Agno run."""
    content = getattr(output, "content", None)
    if isinstance(content, AssistantTurn):
        raw = content.model_dump_json()
        return content.message.strip(), content.explanation.strip(), raw
    if isinstance(content, BaseModel):
        data = content.model_dump()
    elif isinstance(content, dict):
        data = content
    else:
        raw = str(content or "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.strip(), "", raw
    raw = json.dumps(data, default=str)
    return str(data.get("message") or "").strip(), str(data.get("explanation") or "").strip(), raw


def _clean(text: str) -> str:
    """Storage syntax from memory is never something to show."""
    return _SECTION_MARKER.sub("", text or "").strip()


def _platform_checks(
    content: str,
    *,
    grounded: Any,
    settings: Any,
    message: str,
    activity: list[dict[str, Any]],
    new_actions: list[dict[str, Any]],
    ran_match: bool,
) -> tuple[str, list[str]]:
    """The platform's deterministic checks. Returns (compliant wording, names of checks that fired)."""
    fired: list[str] = []
    name = agent_instructions.assistant_name(settings)

    content, changed = enforce_persona(content, name)
    if changed:
        fired.append("PERSONA_ENFORCED")
    content, changed = grounding.enforce_restrictions(
        content, grounded.hidden, answered_visible=bool(grounded.visible_keys)
    )
    if changed:
        fired.append("RESTRICTION_NOTED")
    content, changed = grounding.enforce_conflicts(content, grounded.conflicts)
    if changed:
        fired.append("CONFLICT_ENFORCED")
    content, changed = grounding.enforce_package(
        content,
        grounded.unpurchased,
        company=getattr(settings, "company_name", "") or "",
        asked_for_action=grounded.unpurchased_action,
        answered_visible=bool(grounded.visible_keys),
    )
    if changed:
        fired.append("PACKAGE_SCOPE_NOTED")
    content, changed = enforce_claims(content, message=message, activity=activity, new_actions=new_actions)
    if changed:
        fired.append("FALSE_ACTION_CLAIM_CORRECTED")
    if ran_match and not re.search(r"\bhuman\b", content, re.I):
        content = content.rstrip() + (
            "\n\nThis comparison is assistive only — the hiring decision stays with a human."
        )
        fired.append("HIRING_DECISION_NOTE")
    return content, fired


# --------------------------------------------------------------------------- the turn

def _pending_state(saved: dict) -> dict:
    for m in reversed(saved.get("messages") or []):
        if m["role"] == "assistant":
            state = (m.get("meta") or {}).get("state")
            return state if isinstance(state, dict) else {}
    return {}


def _history(saved: dict) -> list[Message]:
    return [
        Message(role=m["role"], content=m["content"])
        for m in saved.get("messages") or []
        if m["role"] in ("user", "assistant") and m.get("content")
    ]


def _packs_for(settings: Any, pinned: str) -> list[DomainPack]:
    general = get_pack("general")
    if pinned and pinned != AUTO:
        picked = [p for p in (get_pack(pinned), general) if p is not None]
        if picked:
            return list(dict.fromkeys(picked))
    packs = enabled_packs(settings.enabled_domains)
    if general is not None and general not in packs:
        packs.append(general)
    return packs


def _response_domain(turn: _Turn, tool_domains: dict[str, str]) -> str:
    """The domain the turn ended up working in, from the tools the model actually used."""
    for call in turn.tool_runs:
        name = getattr(call, "tool_name", "")
        if name in _CAMPAIGN_NAMES:
            return "acquisition"
        domain = tool_domains.get(name)
        if domain and domain != "general":
            return domain
    return "general"


def _guardrail_audit(ctx: Any, scope: Any, session_id: str, status: str, domain: str = "") -> None:
    try:
        from bizos.audit.events import log
        from bizos.types import AuditEventType

        fields: dict[str, Any] = {"session_id": session_id, "status": status, "execution_mode": str(scope.mode)}
        if domain:
            fields["domain"] = domain
        log(AuditEventType.GUARDRAIL_TRIGGERED, ctx=ctx, **fields)
    except Exception:  # noqa: BLE001 - auditing must not break the reply
        pass


def reply(body: Any, ctx: Any, settings: Any) -> dict[str, Any]:
    """One chat turn. Must be called with the tenant bound (``bound(ctx)``)."""
    from app.api.chat import _regulated_data_refusal as regulated_data_refusal
    from app.api.deps import run_scope

    session_id = body.session_id or new_id("ses")
    saved: dict = {"messages": []}
    if body.session_id:
        try:
            chat_history.assert_can_continue(ctx, body.session_id)
            saved = chat_history.get_session(ctx, body.session_id)
        except chat_history.SessionNotFound:
            # assert_can_continue only refuses someone else's conversation; an unknown id starts fresh.
            if chat_history.owner_of(ctx, body.session_id) is not None:
                raise HTTPException(status_code=404, detail="Conversation not found") from None
    prior_state = _pending_state(saved)

    if not app_settings.llm_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Chat requires a model. Set OPENAI_API_KEY (and MODEL_ID) to enable it. "
                "Memory, approvals, audit and the connectors work without one."
            ),
        )

    scope = run_scope(
        ctx,
        settings,
        domain="general",
        requested_mode=getattr(body, "execution_mode", None),
        session_id=session_id,
    )
    message = body.message
    before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}

    # ---- before the model: regulated data never reaches the vendor, and restricted candidates are not discussed
    grounded = grounding.analyze(ctx, message, settings.enabled_domains)
    refusal = regulated_data_refusal(message, settings)
    canned = (refusal[1] if refusal else None) or grounding.restricted_candidate_reply(grounded, message)
    if canned is not None:
        if refusal:
            _guardrail_audit(ctx, scope, session_id, refusal[0])
        return _finish(
            ctx,
            scope,
            settings,
            body,
            session_id,
            text=canned,
            explanation="Answered by the platform before any model call (regulated or restricted data).",
            turn=_Turn(),
            session=None,
            tool_domains={},
            new_actions=[],
            final_state=prior_state,
        )

    packs = _packs_for(settings, getattr(body, "domain", AUTO))
    turn = _Turn()
    ran_match = False
    grounded_text = grounded.text
    tools: list[Any] = []
    tool_domains: dict[str, str] = {}

    session: Optional[CampaignSession] = None
    records: dict = {}
    campaign_allowed = ctx.is_admin and getattr(body, "domain", AUTO) in (AUTO, "acquisition", None)
    if campaign_allowed:
        try:
            records = known_records(ctx)
        except Exception as exc:  # noqa: BLE001 - still attach campaign tools; they report the error
            log.warning("campaign records unavailable: %s: %s", type(exc).__name__, exc)
            records = {
                "sender_name": "",
                "sender_accounts": [],
                "assessment_url": "",
                "email_without_company": {"subject": "", "body": ""},
                "campaigns": [],
                "people": [],
            }
        from bizos.types import ExecutionMode

        block = ""
        if scope.mode == ExecutionMode.ADVISE:
            block = (
                "this workspace is in Advise mode, so emails can be drafted here but not sent. "
                "An admin can change the mode."
            )
        session = CampaignSession(ctx, records, prior_state, send_block=block)
        session.log = turn.calls
        session.user_message = message
        session.user_texts = [m["content"] for m in saved.get("messages") or [] if m["role"] == "user"] + [message]
        tools = _described(build_campaign_tools(session))

    def prompt_now() -> str:
        return build_prompt(
            ctx=ctx,
            settings=settings,
            scope=scope,
            packs=packs,
            grounded_text=grounded_text,
            session=session,
            records=records,
        )

    history = _history(saved)
    next_input: list[Message] = history + [Message(role="user", content=message)]
    text = explanation = ""
    raw = ""
    activity: list[dict[str, Any]] = []
    fixed_text = ""
    fired: list[str] = []

    for attempt in range(MAX_NUDGES + 1):
        output = _run_agent(ctx, settings, session_id, tools, prompt_now(), next_input, turn)
        text, explanation, raw = _final(output)
        text = _clean(text)
        activity = turn.activity(session)
        new_actions = [
            a.to_dict() for a in actions.list_actions(limit=200, ctx=ctx) if a.id not in before
        ]
        fixed_text, fired = _platform_checks(
            text,
            grounded=grounded,
            settings=settings,
            message=message,
            activity=activity,
            new_actions=new_actions,
            ran_match=ran_match,
        )
        issues: list[str] = []
        if fired:
            issues.append(
                "Platform checks flagged your draft ("
                + ", ".join(fired)
                + "). A compliant version would read:\n"
                + fixed_text
                + "\nRewrite your reply in your own words so these checks pass."
            )
        if session is not None:
            issues.extend(session.problems(text, explanation))
        if not text:
            issues.append(
                "Your reply was empty. Answer the user now in one or two plain sentences, using the current state "
                "and the tool results."
            )
        if issues and attempt < MAX_NUDGES:
            turn.nudges += 1
            turn.self_check.extend(issues)
            # Carry the whole run forward (tool calls and results included), so nothing is repeated.
            carried = [m for m in (output.messages or []) if m.role not in ("system", "developer")]
            next_input = carried + [
                Message(
                    role="system",
                    content="Self-check before replying:\n- " + "\n- ".join(issues)
                    + "\nFix this with tools if needed, then give your final answer in the same JSON format. "
                    "If a point truly does not apply, keep your answer and say why in the explanation.",
                )
            ]
            continue
        break

    # The deterministic wording is the last line of defence, not the first.
    if fired:
        text = fixed_text
        turn.guardrail_events.extend(fired)
        for status in fired:
            _guardrail_audit(ctx, scope, session_id, status, domain=",".join(sorted(grounded.unpurchased)) if status == "PACKAGE_SCOPE_NOTED" else "")
    if session is not None and session.claims_unsent(text, explanation):
        # Last line of defence: never tell the user something was sent when it was not.
        failed = [c for c in session.log if c.get("tool") == "send_approved" and str(c.get("result", "")).startswith("error")]
        text = (
            "I could not send it: " + str(failed[-1]["result"]).removeprefix("error:").strip()
            if failed
            else NOT_SENT_TEXT
        )
        turn.guardrail_events.append("FALSE_SEND_CLAIM_CORRECTED")
    if session is not None and session.sent and claims_delivered(text):
        # Last line of defence: the email is scheduled, not delivered.
        text = session.scheduled_text()
        turn.guardrail_events.append("SENT_WORDING_CORRECTED")
    if not text:
        # Never show the user a blank error: say something safe and keep the chat state as it is.
        turn.guardrail_events.append("EMPTY_REPLY_FALLBACK")
        if session is not None and session.state.get("draft"):
            text = "The draft is ready for review. Approve it as is, or tell me what you'd like changed."
        else:
            text = "Sorry, I didn't catch that. Could you say it again?"

    new_actions = [a.to_dict() for a in actions.list_actions(limit=200, ctx=ctx) if a.id not in before]
    final_state = prior_state
    if session is not None:
        final_state = {} if session.sent else {**session.state, "last_explanation": explanation}
    return _finish(
        ctx,
        scope,
        settings,
        body,
        session_id,
        text=text,
        explanation=explanation,
        turn=turn,
        session=session,
        tool_domains=tool_domains,
        new_actions=new_actions,
        final_state=final_state,
    )


def _described(functions: list[Any]) -> list[Any]:
    """Mark campaign tools so the model can tell them from the email_* tools."""
    for fn in functions:
        fn.description = "[Outbound campaign flow] " + (fn.description or "")
    return functions


def _needs_responses_api(message: str) -> bool:
    """The provider said: this model takes tools + reasoning only on /v1/responses."""
    return "/v1/responses" in (message or "") and not _api["responses"]


def _run_agent(
    ctx: Any, settings: Any, session_id: str, tools: list[Any], prompt: str, messages: list[Message], turn: _Turn
) -> Any:
    """Run the Agno agent once.

    If the provider says the model needs /v1/responses, switch to that route and run again with the SAME
    reasoning effort (nothing was executed, because the request itself was refused). Other reasoning-effort
    settings are tried only when MODEL_REASONING_FALLBACK=on.
    """
    last_error = ""
    for effort in _effort_candidates():
        output = None
        for _attempt in (1, 2):
            agent = Agent(
                name=agent_instructions.assistant_name(settings),
                id="bizos-assistant",
                model=_model(effort),
                user_id=ctx.user_id,
                session_id=session_id,
                db=workspace_db(ctx),
                instructions=prompt,
                tools=tools,
                output_schema=AssistantTurn,
                tool_call_limit=TOOL_CALL_LIMIT,
                # Conversation context comes from our own per-user chat history (passed in `messages`),
                # which is also what the user sees; Agno still records the run in the workspace database.
                add_history_to_context=False,
                # Tools are built per request and must not be cached across tenants.
                cache_callables=False,
                enable_agentic_memory=False,
                add_memories_to_context=True,
                telemetry=False,
                store_events=False,
            )
            try:
                output = agent.run(input=messages)
            except Exception as exc:  # noqa: BLE001
                if _needs_responses_api(str(exc)):
                    log.warning("switching to the Responses API for %s: %s", app_settings.model_id(), exc)
                    _api["responses"] = True
                    continue
                raise HTTPException(status_code=502, detail=f"Model call failed: {exc}") from exc
            if output.status == RunStatus.error and not output.tools and _needs_responses_api(str(output.content or "")):
                log.warning("switching to the Responses API for %s: %s", app_settings.model_id(), output.content)
                _api["responses"] = True
                continue
            break
        turn.tool_runs.extend(output.tools or [])
        if output.status != RunStatus.error:
            return output
        last_error = str(output.content or "model error")
        log.warning("model run failed (reasoning_effort=%s): %s", effort, last_error)
        if output.tools:  # something already ran; never replay it
            break
        _effort["tried"].append(effort)
    raise HTTPException(status_code=502, detail=f"Model call failed: {last_error}")


# --------------------------------------------------------------------------- response

def _finish(
    ctx: Any,
    scope: Any,
    settings: Any,
    body: Any,
    session_id: str,
    *,
    text: str,
    explanation: str,
    turn: _Turn,
    session: Optional[CampaignSession],
    tool_domains: dict[str, str],
    new_actions: list[dict[str, Any]],
    final_state: dict,
) -> dict[str, Any]:
    activity = [{"tool": a["tool"], "ok": a["ok"]} for a in turn.activity(session)]
    s = session.state if session is not None else {}
    d = s.get("draft") or {}
    recipients = [r["email"] for r in s.get("recipients") or []]
    pinned = (getattr(body, "domain", AUTO) or AUTO).strip().lower()
    domain = pinned if pinned not in (AUTO, "") else _response_domain(turn, tool_domains)
    awaiting = [a for a in new_actions if a["status"] == str(ActionStatus.PENDING_APPROVAL)]
    drafts = [a for a in new_actions if a["status"] == str(ActionStatus.DRAFT)]

    trace = {
        "engine": "agno",
        "tool": "execute" if session is not None and session.sent else "",
        "explanation": explanation,
        "tool_log": turn.calls,
        "self_check": turn.self_check,
        "guardrails": turn.guardrail_events,
        "campaign_id": s.get("campaign_id") or "",
        "campaign_name": s.get("campaign_name") or "",
        "campaign_schedule_timing": s.get("window") or [],
        "email_address": recipients[0] if len(recipients) == 1 else "",
        "email_address_list": recipients,
        "email_from": d.get("email_from") or "",
        "email_subject": d.get("email_subject") or "",
        "email_body": d.get("email_body") or "",
        "email_draft": d.get("email_draft") or "",
        "email_view": draft_view(session.records, s) if session is not None and d else {},
        "show_email_card": bool(session is not None and d and session.card_shown),
        "reply_note": text,
        "campaigns_to_show": session.campaigns_to_show if session is not None else [],
        "people_to_show": session.people_to_show if session is not None else [],
    }
    if not ctx.is_admin:
        # Ordinary users get the answer, not the machinery (tool arguments, reasoning, checks).
        trace = {"engine": "agno"}
    response = {
        "session_id": session_id,
        "content": text,
        "response": text,  # same text under the key the campaign UI reads
        "mode": str(scope.mode),
        "domain": domain,
        "routed": getattr(body, "domain", AUTO) == AUTO,
        "tool_activity": activity,
        "actions": new_actions,
        "awaiting_approval": awaiting,
        "drafts": drafts,
        "email_draft": trace.get("email_draft", ""),
        "trace": trace,
    }
    _remember(ctx, body.message, response, final_state)
    return response


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {k: action.get(k) for k in ("id", "title", "tool", "status", "risk_level", "policy_reason")}


def _remember(ctx: Any, message: str, response: dict[str, Any], state: dict) -> None:
    """Save the turn as shown. A storage failure must not lose the reply."""
    try:
        chat_history.record_turn(
            ctx,
            session_id=response["session_id"],
            user_message=message,
            reply=response["content"],
            domain=response["domain"],
            meta={
                "engine": "agno",
                "mode": response["mode"],
                "tool_activity": response["tool_activity"],
                "awaiting_approval": [_action_summary(a) for a in response["awaiting_approval"]],
                "drafts": [_action_summary(a) for a in response["drafts"]],
                "state": state,
                "json": response["trace"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error("chat history write failed: %s: %s", type(exc).__name__, exc)
