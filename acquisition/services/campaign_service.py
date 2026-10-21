"""Campaign domain service."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from acquisition.config import get_settings
from acquisition.models.campaign import (
    Campaign,
    CampaignMessage,
    CampaignProspect,
    CampaignStatus,
    MessageChannel,
    MessageType,
)
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.schemas.campaign import (
    CampaignCreate,
    CampaignMessageCreate,
    CampaignMessageUpdate,
    CampaignUpdate,
)


class CampaignNotFoundError(Exception):
    def __init__(self, campaign_id: uuid.UUID) -> None:
        self.campaign_id = campaign_id
        super().__init__(f"Campaign {campaign_id} not found")


class CampaignMessageNotFoundError(Exception):
    def __init__(self, message_id: uuid.UUID) -> None:
        self.message_id = message_id
        super().__init__(f"Campaign message {message_id} not found")


def initial_email_text(
    *,
    sender_name: str,
    first_name: str,
    company: str | None,
    assessment_url: str,
) -> tuple[str, str]:
    """The first email a manual campaign is created with, with the known values filled in."""
    company_name = (company or "").strip()
    if company_name:
        subject = f"Quick question about operational bandwidth at {company_name}"
        assessment_line = f"Would you be open to a short free assessment for {company_name}?"
    else:
        subject = "Quick question about operational bandwidth"
        assessment_line = "Would you be open to a short free assessment?"
    greeting = (first_name or "").strip() or "there"
    body = (
        f"Hi {greeting},\n\n"
        "I help executive teams find where AI and automation can free up "
        "capacity without a heavy IT project.\n\n"
        f"{assessment_line}\n\n"
        f"{assessment_url}\n\n"
        "Either way, happy to share what we typically find in the first review.\n\n"
        "Best,\n"
        f"{sender_name}"
    )
    return subject, body


class CampaignValidationError(Exception):
    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        self.message = message
        self.missing = missing or []
        super().__init__(message)


class CampaignService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def _system_assessment_base_url(self) -> str | None:
        """Public app origin — per-prospect assessment links are generated from tokens."""
        base = (self.settings.public_base_url or "").strip().rstrip("/")
        return base or None

    def list_campaigns(
        self,
        *,
        search: str | None = None,
        status: CampaignStatus | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[tuple[Campaign, int, int]], int]:
        filters = []
        if status is not None:
            filters.append(Campaign.status == status)
        if search:
            pattern = f"%{search.strip()}%"
            filters.append(Campaign.name.ilike(pattern))

        count_stmt = select(func.count()).select_from(Campaign)
        if filters:
            count_stmt = count_stmt.where(*filters)
        total = int(self.db.scalar(count_stmt) or 0)

        message_count = (
            select(func.count(CampaignMessage.id))
            .where(CampaignMessage.campaign_id == Campaign.id)
            .correlate(Campaign)
            .scalar_subquery()
        )
        prospect_count = (
            select(func.count(CampaignProspect.id))
            .where(CampaignProspect.campaign_id == Campaign.id)
            .correlate(Campaign)
            .scalar_subquery()
        )

        list_stmt = select(Campaign, message_count, prospect_count)
        if filters:
            list_stmt = list_stmt.where(*filters)
        rows = self.db.execute(
            list_stmt.order_by(Campaign.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return [(row[0], int(row[1] or 0), int(row[2] or 0)) for row in rows], total

    def get_campaign(self, campaign_id: uuid.UUID) -> Campaign:
        stmt = (
            select(Campaign)
            .where(Campaign.id == campaign_id)
            .options(
                selectinload(Campaign.messages),
                selectinload(Campaign.campaign_prospects).joinedload(CampaignProspect.prospect),
            )
        )
        campaign = self.db.scalar(stmt)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        return campaign

    def create_campaign(self, payload: CampaignCreate) -> Campaign:
        if payload.status == CampaignStatus.READY:
            # New campaigns start as draft; ready requires attachments/messages.
            raise CampaignValidationError(
                "New campaigns must start as draft. Update status to ready after configuration."
            )

        sender = payload.sender_name or self.settings.default_sender_name
        campaign = Campaign(
            name=payload.name,
            sender_name=sender,
            assessment_url=payload.assessment_url or self._system_assessment_base_url(),
            status=CampaignStatus.DRAFT,
            send_timezone=self.settings.default_send_timezone,
            send_window_start=self.settings.default_send_window_start,
            send_window_end=self.settings.default_send_window_end,
            send_monday=self.settings.default_send_monday,
            send_tuesday=self.settings.default_send_tuesday,
            send_wednesday=self.settings.default_send_wednesday,
            send_thursday=self.settings.default_send_thursday,
            send_friday=self.settings.default_send_friday,
            send_saturday=self.settings.default_send_saturday,
            send_sunday=self.settings.default_send_sunday,
        )
        self.db.add(campaign)
        self.db.flush()

        if payload.seed_default_sequence:
            self._seed_default_sequence(campaign)

        self.db.commit()
        return self.get_campaign(campaign.id)

    def update_campaign(self, campaign_id: uuid.UUID, payload: CampaignUpdate) -> Campaign:
        campaign = self.get_campaign(campaign_id)
        data = payload.model_dump(exclude_unset=True)

        if "assessment_url" in data:
            incoming = data.get("assessment_url")
            if incoming is None or not str(incoming).strip():
                # UI no longer edits this; keep existing or fill from PUBLIC_BASE_URL.
                data["assessment_url"] = (
                    campaign.assessment_url or self._system_assessment_base_url()
                )

        if not (campaign.assessment_url or "").strip() and "assessment_url" not in data:
            data["assessment_url"] = self._system_assessment_base_url()

        if "status" in data and data["status"] == CampaignStatus.READY:
            missing = self._ready_missing_requirements(campaign, pending_update=data)
            if missing:
                raise CampaignValidationError(
                    "Campaign cannot become ready until configuration is complete.",
                    missing=missing,
                )

        for key, value in data.items():
            setattr(campaign, key, value)

        self.db.add(campaign)
        self.db.commit()
        return self.get_campaign(campaign.id)

    def delete_campaign(self, campaign_id: uuid.UUID) -> None:
        campaign = self.db.get(Campaign, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)

        remote_id = (campaign.instantly_campaign_id or "").strip()
        if remote_id:
            self._delete_instantly_campaign(remote_id)

        self.db.delete(campaign)
        self.db.commit()

    def _delete_instantly_campaign(self, instantly_campaign_id: str) -> None:
        """Remove the remote Instantly campaign before local delete."""
        from acquisition.integrations.instantly.errors import InstantlyConfigError, InstantlyError
        from acquisition.integrations.instantly.redaction import redact_secrets
        from acquisition.services.instantly_service import InstantlyService

        try:
            client = InstantlyService(self.db).get_api_client()
            client.delete_campaign(instantly_campaign_id)
        except InstantlyConfigError as exc:
            raise CampaignValidationError(
                "Campaign is linked to Instantly but Instantly is not configured. "
                "Connect Instantly under Integrations, then delete again."
            ) from exc
        except InstantlyError as exc:
            raise CampaignValidationError(
                redact_secrets(
                    f"Could not delete campaign in Instantly: {exc.message}",
                    api_key=None,
                )
            ) from exc

    def create_message(
        self,
        campaign_id: uuid.UUID,
        payload: CampaignMessageCreate,
    ) -> CampaignMessage:
        campaign = self.get_campaign(campaign_id)
        order = payload.sequence_order or self._next_sequence_order(campaign_id)
        if order < 1:
            raise CampaignValidationError("sequence_order must be >= 1")
        self._ensure_unique_order(campaign_id, order)

        if payload.delay_after_previous_hours < 0:
            raise CampaignValidationError("delay_after_previous_hours must be >= 0")

        message = CampaignMessage(
            campaign_id=campaign.id,
            sequence_order=order,
            message_type=payload.message_type,
            channel=payload.channel,
            subject=payload.subject,
            body=payload.body,
            delay_after_previous_hours=payload.delay_after_previous_hours,
            is_active=payload.is_active,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def update_message(
        self,
        campaign_id: uuid.UUID,
        message_id: uuid.UUID,
        payload: CampaignMessageUpdate,
    ) -> CampaignMessage:
        message = self._get_message(campaign_id, message_id)
        data = payload.model_dump(exclude_unset=True)

        if "delay_after_previous_hours" in data and data["delay_after_previous_hours"] is not None:
            if data["delay_after_previous_hours"] < 0:
                raise CampaignValidationError("delay_after_previous_hours must be >= 0")

        if "sequence_order" in data and data["sequence_order"] is not None:
            new_order = data["sequence_order"]
            if new_order < 1:
                raise CampaignValidationError("sequence_order must be >= 1")
            if new_order != message.sequence_order:
                self._reorder_message(message, new_order)
                data.pop("sequence_order")

        becoming_active = data.get("is_active") is True and not message.is_active
        if becoming_active:
            subject = data.get("subject", message.subject)
            body = data.get("body", message.body)
            if message.channel == MessageChannel.EMAIL or data.get("channel") == MessageChannel.EMAIL:
                if not (subject and str(subject).strip()) or not (body and str(body).strip()):
                    raise CampaignValidationError(
                        "Active email messages require both subject and body."
                    )

        for key, value in data.items():
            setattr(message, key, value)

        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def delete_message(self, campaign_id: uuid.UUID, message_id: uuid.UUID) -> None:
        message = self._get_message(campaign_id, message_id)
        self.db.delete(message)
        self.db.flush()
        self._normalize_sequence_orders(campaign_id)
        self.db.commit()

    def attach_prospect(self, campaign_id: uuid.UUID, prospect_id: uuid.UUID) -> CampaignProspect:
        campaign = self.get_campaign(campaign_id)
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise CampaignValidationError(f"Potential customer {prospect_id} not found")

        if prospect.approval_status != ApprovalStatus.APPROVED:
            raise CampaignValidationError(
                f"Only approved potential customers can be added to a campaign. "
                f"Status is '{prospect.approval_status.value}'."
            )

        existing = self.db.scalar(
            select(CampaignProspect).where(
                CampaignProspect.campaign_id == campaign.id,
                CampaignProspect.prospect_id == prospect_id,
            )
        )
        if existing is not None:
            raise CampaignValidationError("Potential customer is already attached to this campaign.")

        link = CampaignProspect(campaign_id=campaign.id, prospect_id=prospect_id)
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return self.db.scalar(
            select(CampaignProspect)
            .where(CampaignProspect.id == link.id)
            .options(joinedload(CampaignProspect.prospect))
        )

    def detach_prospect(self, campaign_id: uuid.UUID, prospect_id: uuid.UUID) -> None:
        link = self.db.scalar(
            select(CampaignProspect).where(
                CampaignProspect.campaign_id == campaign_id,
                CampaignProspect.prospect_id == prospect_id,
            )
        )
        if link is None:
            raise CampaignValidationError("Potential customer is not attached to this campaign.")
        self.db.delete(link)
        self.db.commit()

    def _seed_default_sequence(self, campaign: Campaign) -> None:
        placeholder = self.settings.assessment_url_placeholder
        initial_subject, initial_body = initial_email_text(
            sender_name=campaign.sender_name,
            first_name="{{firstName}}",
            company="{{companyName}}",
            assessment_url=placeholder,
        )
        templates = [
            (
                1,
                MessageType.INITIAL,
                self.settings.default_initial_delay_hours,
                initial_subject,
                initial_body,
            ),
            (
                2,
                MessageType.FOLLOW_UP,
                self.settings.default_followup_1_delay_hours,
                "Following up — free assessment for {{companyName}}",
                (
                    "Hi {{firstName}},\n\n"
                    "Just bumping this in case it got buried.\n\n"
                    "The assessment is free and takes a few minutes. It maps where "
                    "your team is spending time that could be streamlined.\n\n"
                    f"{placeholder}\n\n"
                    "Happy to send a one-page summary if that is easier.\n\n"
                    "Best,\n"
                    f"{campaign.sender_name}"
                ),
            ),
            (
                3,
                MessageType.FOLLOW_UP,
                self.settings.default_followup_2_delay_hours,
                "Worth 10 minutes for {{companyName}}?",
                (
                    "Hi {{firstName}},\n\n"
                    "Last quick note — several operators in similar roles use the "
                    "assessment to spot bottlenecks before they hire or buy tools.\n\n"
                    f"Start here when you have a moment:\n{placeholder}\n\n"
                    "If timing is off, tell me and I will close the loop.\n\n"
                    "Best,\n"
                    f"{campaign.sender_name}"
                ),
            ),
            (
                4,
                MessageType.FOLLOW_UP,
                self.settings.default_followup_3_delay_hours,
                "Closing the loop on the assessment",
                (
                    "Hi {{firstName}},\n\n"
                    "I will close this thread so I am not crowding your inbox.\n\n"
                    "If a free operational assessment is useful later, the link stays open:\n"
                    f"{placeholder}\n\n"
                    "Wishing you a strong quarter.\n\n"
                    "Best,\n"
                    f"{campaign.sender_name}"
                ),
            ),
        ]
        for order, message_type, delay, subject, body in templates:
            self.db.add(
                CampaignMessage(
                    campaign_id=campaign.id,
                    sequence_order=order,
                    message_type=message_type,
                    channel=MessageChannel.EMAIL,
                    subject=subject,
                    body=body,
                    delay_after_previous_hours=delay,
                    is_active=True,
                )
            )

    def ready_checklist(
        self,
        campaign: Campaign,
        *,
        pending_update: dict | None = None,
    ) -> list[dict]:
        """Structured ready checklist for UI (done + short action)."""
        pending_update = pending_update or {}
        name = pending_update.get("name", campaign.name)
        sender = pending_update.get("sender_name", campaign.sender_name)
        assessment_url = pending_update.get("assessment_url", campaign.assessment_url)
        if not assessment_url or not str(assessment_url).strip():
            assessment_url = self._system_assessment_base_url()

        basics_gaps: list[str] = []
        if not name or not str(name).strip():
            basics_gaps.append("name")
        if not sender or not str(sender).strip():
            basics_gaps.append("sender")
        url_ok = False
        if not assessment_url or not str(assessment_url).strip():
            basics_gaps.append("public base URL (PUBLIC_BASE_URL)")
        else:
            from urllib.parse import urlparse

            parsed = urlparse(str(assessment_url).strip())
            url_ok = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
            if not url_ok:
                basics_gaps.append("valid public base URL")

        if not basics_gaps:
            basics_detail = None
        elif len(basics_gaps) == 1:
            basics_detail = f"Add a {basics_gaps[0]}"
        else:
            basics_detail = "Add " + ", ".join(basics_gaps[:-1]) + f", and {basics_gaps[-1]}"

        active_messages = sorted(
            [m for m in campaign.messages if m.is_active],
            key=lambda m: m.sequence_order,
        )
        active_initials = [
            m for m in active_messages if m.message_type == MessageType.INITIAL
        ]

        structure_ok = True
        structure_detail: str | None = None
        if not active_initials:
            structure_ok = False
            structure_detail = "Keep exactly one active initial email"
        elif len(active_initials) > 1:
            structure_ok = False
            structure_detail = (
                f"Only one active initial email is allowed "
                f"(found {len(active_initials)})"
            )
        elif active_initials[0].sequence_order != 1:
            structure_ok = False
            structure_detail = "Initial email must be first in the sequence"

        incomplete_orders: list[int] = []
        delay_bad: list[int] = []
        for message in active_messages:
            needs_copy = not (
                message.subject
                and message.subject.strip()
                and message.body
                and message.body.strip()
            )
            if needs_copy:
                incomplete_orders.append(message.sequence_order)
            if (
                message.delay_after_previous_hours is None
                or message.delay_after_previous_hours < 0
            ):
                delay_bad.append(message.sequence_order)

        if incomplete_orders:
            orders = ", ".join(str(o) for o in incomplete_orders)
            copy_detail = f"Add subject & body on email{'s' if len(incomplete_orders) > 1 else ''} {orders}"
        elif delay_bad:
            orders = ", ".join(str(o) for o in delay_bad)
            copy_detail = f"Set a valid delay on email{'s' if len(delay_bad) > 1 else ''} {orders}"
        else:
            copy_detail = None
        copy_ok = not incomplete_orders and not delay_bad and bool(active_messages)

        has_prospects = bool(campaign.campaign_prospects)

        return [
            {
                "key": "details",
                "label": "Campaign details",
                "done": not basics_gaps,
                "detail": basics_detail,
            },
            {
                "key": "structure",
                "label": "Sequence structure",
                "done": structure_ok,
                "detail": structure_detail,
            },
            {
                "key": "copy",
                "label": "Email copy",
                "done": copy_ok,
                "detail": copy_detail or ("Add at least one active email" if not active_messages else None),
            },
            {
                "key": "prospects",
                "label": "Potential customers",
                "done": has_prospects,
                "detail": None if has_prospects else "Attach at least one approved potential customer",
            },
        ]

    def _ready_missing_requirements(
        self,
        campaign: Campaign,
        *,
        pending_update: dict | None = None,
    ) -> list[str]:
        """Return short blockers for status=ready (API + validation)."""
        pending_update = pending_update or {}
        missing: list[str] = []

        name = pending_update.get("name", campaign.name)
        sender = pending_update.get("sender_name", campaign.sender_name)
        assessment_url = pending_update.get("assessment_url", campaign.assessment_url)
        if not assessment_url or not str(assessment_url).strip():
            assessment_url = self._system_assessment_base_url()

        if not name or not str(name).strip():
            missing.append("Campaign name is required")
        if not sender or not str(sender).strip():
            missing.append("Sender name is required")
        if not assessment_url or not str(assessment_url).strip():
            missing.append("PUBLIC_BASE_URL must be configured for assessment links")
        else:
            from urllib.parse import urlparse

            parsed = urlparse(str(assessment_url).strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                missing.append("PUBLIC_BASE_URL must be a valid http or https URL")

        active_messages = [m for m in campaign.messages if m.is_active]
        active_initials = [
            m for m in active_messages if m.message_type == MessageType.INITIAL
        ]

        if not active_initials:
            missing.append("Exactly one active initial email is required")
        elif len(active_initials) > 1:
            missing.append(
                "Only one active initial email is allowed "
                f"(found {len(active_initials)})"
            )
        elif active_initials[0].sequence_order != 1:
            missing.append("The active initial email must have sequence_order=1")

        for message in sorted(active_messages, key=lambda m: m.sequence_order):
            order = message.sequence_order
            if not (message.subject and message.subject.strip()):
                missing.append(f"Email {order} is missing a subject")
            if not (message.body and message.body.strip()):
                missing.append(f"Email {order} is missing a body")
            if (
                message.delay_after_previous_hours is None
                or message.delay_after_previous_hours < 0
            ):
                missing.append(f"Email {order} needs a valid delay")

        if not campaign.campaign_prospects:
            missing.append("At least one approved potential customer must be attached")

        return missing

    def _get_message(self, campaign_id: uuid.UUID, message_id: uuid.UUID) -> CampaignMessage:
        # Ensure campaign exists
        if self.db.get(Campaign, campaign_id) is None:
            raise CampaignNotFoundError(campaign_id)
        message = self.db.scalar(
            select(CampaignMessage).where(
                CampaignMessage.id == message_id,
                CampaignMessage.campaign_id == campaign_id,
            )
        )
        if message is None:
            raise CampaignMessageNotFoundError(message_id)
        return message

    def _next_sequence_order(self, campaign_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(CampaignMessage.sequence_order)).where(
                CampaignMessage.campaign_id == campaign_id
            )
        )
        return int(current or 0) + 1

    def _ensure_unique_order(self, campaign_id: uuid.UUID, order: int) -> None:
        exists = self.db.scalar(
            select(CampaignMessage.id).where(
                CampaignMessage.campaign_id == campaign_id,
                CampaignMessage.sequence_order == order,
            )
        )
        if exists is not None:
            raise CampaignValidationError(
                f"sequence_order {order} is already used in this campaign"
            )

    def _reorder_message(self, message: CampaignMessage, new_order: int) -> None:
        """Move a message to new_order, shifting siblings as needed."""
        campaign_id = message.campaign_id
        old_order = message.sequence_order
        if new_order == old_order:
            return

        siblings = list(
            self.db.scalars(
                select(CampaignMessage)
                .where(CampaignMessage.campaign_id == campaign_id)
                .order_by(CampaignMessage.sequence_order)
            ).all()
        )
        ordered = [m for m in siblings if m.id != message.id]
        insert_at = max(0, min(new_order - 1, len(ordered)))
        ordered.insert(insert_at, message)

        # Two-pass assignment avoids unique (campaign_id, sequence_order) conflicts.
        for index, sibling in enumerate(ordered, start=1):
            sibling.sequence_order = 10_000 + index
            self.db.add(sibling)
        self.db.flush()
        for index, sibling in enumerate(ordered, start=1):
            sibling.sequence_order = index
            self.db.add(sibling)
        self.db.flush()

    def _normalize_sequence_orders(self, campaign_id: uuid.UUID) -> None:
        messages = list(
            self.db.scalars(
                select(CampaignMessage)
                .where(CampaignMessage.campaign_id == campaign_id)
                .order_by(CampaignMessage.sequence_order)
            ).all()
        )
        # Two-pass to avoid unique conflicts
        for index, message in enumerate(messages, start=1):
            message.sequence_order = 10_000 + index
            self.db.add(message)
        self.db.flush()
        for index, message in enumerate(messages, start=1):
            message.sequence_order = index
            self.db.add(message)
        self.db.flush()
