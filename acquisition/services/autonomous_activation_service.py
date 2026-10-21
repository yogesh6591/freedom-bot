"""Autonomous activation: queue → CampaignProspect → personalize → Instantly.

Reuses CampaignService + LeadSyncService. No parallel outbound system.
Does NOT schedule daily jobs. Dry-run never calls Instantly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from acquisition.config import Settings, get_settings
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.models.campaign import (
    Campaign,
    CampaignProspect,
    MessageType,
)
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.services.assessment_tokens import build_assessment_url, generate_assessment_token
from acquisition.services.campaign_service import (
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
)
from acquisition.services.icp_settings_service import IcpSettingsService
from acquisition.services.lead_sync_service import LeadSyncError, LeadSyncService
from acquisition.services.personalized_outreach_service import (
    PersonalizedOutreachResult,
    PersonalizedOutreachService,
)
from acquisition.services.prospect_priority_queue_service import ProspectPriorityQueueService

logger = logging.getLogger(__name__)


class AutonomousActivationError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AutonomousActivationService:
    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        lead_sync: LeadSyncService | None = None,
        outreach: PersonalizedOutreachService | None = None,
        instantly_client: InstantlyClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.icp = IcpSettingsService(db)
        self.campaigns = CampaignService(db)
        self.queue = ProspectPriorityQueueService(db, settings=self.settings)
        self.outreach = outreach or PersonalizedOutreachService(db, settings=self.settings)
        self.lead_sync = lead_sync or LeadSyncService(
            db, campaign_service=self.campaigns, client=instantly_client
        )

    def build_activation_queue(self, *, limit: int | None = None) -> dict[str, Any]:
        return self.queue.build_activation_queue(limit=limit)

    def preview_activation(self, prospect_id: uuid.UUID) -> dict[str, Any]:
        """Eligibility + personalized message preview. Never calls Instantly."""
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise AutonomousActivationError(f"Prospect {prospect_id} not found")
        eligibility = self.queue.evaluate_eligibility(prospect)
        campaign = self._resolve_campaign(required=False)
        # Temporary token URL for preview only (not persisted unless activating).
        token = generate_assessment_token()
        assessment_url = build_assessment_url(token, settings=self.settings)
        message = self.outreach.generate(prospect, assessment_url=assessment_url)
        body_with_cta = f"{message.body.rstrip()}\n\n{assessment_url}"
        return {
            "prospect_id": str(prospect.id),
            "company": prospect.company,
            "email": prospect.email,
            "scores": {
                "priority": prospect.priority_score,
                "fit": prospect.fit_score,
                "timing": prospect.timing_score,
                "reachability": prospect.reachability_score,
            },
            "eligible": eligibility.eligible,
            "inclusion_reasons": eligibility.reasons,
            "exclusion_reasons": eligibility.exclusion_reasons,
            "campaign_id": str(campaign.id) if campaign else None,
            "campaign_name": campaign.name if campaign else None,
            "message": {
                **self._message_dict(message),
                "body_with_cta": body_with_cta,
            },
            "assessment_url_preview": assessment_url,
            "dry_run": True,
            "would_call_instantly": False,
        }

    def activate_prospect(
        self,
        prospect_id: uuid.UUID,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        prospect = self.db.get(Prospect, prospect_id)
        if prospect is None:
            raise AutonomousActivationError(f"Prospect {prospect_id} not found")

        eligibility = self.queue.evaluate_eligibility(prospect)
        if not eligibility.eligible:
            return {
                "ok": False,
                "prospect_id": str(prospect_id),
                "dry_run": dry_run,
                "activated": False,
                "exclusion_reasons": eligibility.exclusion_reasons,
                "errors": ["not_eligible"],
            }

        campaign = self._resolve_campaign(required=True)
        assert campaign is not None

        # Autonomous policy: approve for attach/sync only on live activation.
        if not dry_run and prospect.approval_status != ApprovalStatus.APPROVED:
            prospect.approval_status = ApprovalStatus.APPROVED
            self.db.add(prospect)
            self.db.commit()
            self.db.refresh(prospect)

        if dry_run:
            # Preview path with real campaign context — no attach / Instantly.
            token = generate_assessment_token()
            assessment_url = build_assessment_url(token, settings=self.settings)
            message = self.outreach.generate(prospect, assessment_url=assessment_url)
            full_body = f"{message.body.rstrip()}\n\n{assessment_url}"
            return {
                "ok": True,
                "prospect_id": str(prospect_id),
                "campaign_id": str(campaign.id),
                "assessment_url": assessment_url,
                "message": {
                    **self._message_dict(message),
                    "body_with_cta": full_body,
                },
                "inclusion_reasons": eligibility.reasons,
                "dry_run": True,
                "activated": False,
                "instantly_called": False,
                "would_call_instantly": True,
                "attribution": {
                    "outreach_message_key": f"autonomous:{message.message_angle}",
                    "outreach_message_snapshot": (
                        f"Subject: {message.subject}\n\n{full_body}"
                    ),
                    "outreach_signal_snapshot": message.supporting_signal,
                },
            }

        self._ensure_autonomous_initial_template(campaign)

        link = self._get_or_attach(campaign.id, prospect.id)
        if not link.assessment_token:
            link.assessment_token = generate_assessment_token()
            self.db.add(link)
            self.db.commit()
            self.db.refresh(link)

        assessment_url = build_assessment_url(link.assessment_token, settings=self.settings)
        message = self.outreach.generate(prospect, assessment_url=assessment_url)
        full_body = f"{message.body.rstrip()}\n\n{assessment_url}"
        attribution = {
            "outreach_message_key": f"autonomous:{message.message_angle}",
            "outreach_message_snapshot": (
                f"Subject: {message.subject}\n\n{full_body}\n\n"
                f"angle={message.message_angle}; confidence={message.confidence:.2f}"
            ),
            "outreach_signal_snapshot": message.supporting_signal
            or (prospect.source or None),
        }
        link.outreach_message_key = attribution["outreach_message_key"]
        link.outreach_message_snapshot = attribution["outreach_message_snapshot"]
        link.outreach_signal_snapshot = attribution["outreach_signal_snapshot"]
        self.db.add(link)
        self.db.commit()

        result: dict[str, Any] = {
            "ok": True,
            "prospect_id": str(prospect_id),
            "campaign_id": str(campaign.id),
            "campaign_prospect_id": str(link.id),
            "assessment_token": link.assessment_token,
            "assessment_url": assessment_url,
            "message": {
                **self._message_dict(message),
                "body_with_cta": full_body,
            },
            "inclusion_reasons": eligibility.reasons,
            "dry_run": False,
            "activated": False,
            "instantly_called": False,
            "attribution": attribution,
        }

        # Live Instantly activation via existing LeadSyncService.
        try:
            sync = self.lead_sync.sync_one_lead(
                campaign.id,
                prospect.id,
                extra_custom_variables={
                    "personalized_subject": message.subject,
                    "personalized_body": message.body,
                    "assessment_url": assessment_url,
                },
                attribution=attribution,
            )
        except LeadSyncError as exc:
            self.db.refresh(link)
            return {
                **result,
                "ok": False,
                "activated": False,
                "instantly_called": True,
                "errors": [exc.message],
                "sync_status": link.sync_status.value if link.sync_status else None,
            }

        self.db.refresh(link)
        result["activated"] = (
            link.sync_status.value == "synced" if link.sync_status else False
        )
        result["instantly_called"] = True
        result["sync"] = {
            "synced": sync.synced,
            "failed": sync.failed,
            "message": sync.message,
            "instantly_lead_id": link.instantly_lead_id,
            "sync_status": link.sync_status.value if link.sync_status else None,
            "outreach_status": link.outreach_status.value if link.outreach_status else None,
        }
        if not result["activated"]:
            result["ok"] = False
            result["errors"] = ["instantly_sync_did_not_succeed"]
        return result

    def run_activation_batch(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        queue = self.build_activation_queue(limit=limit)
        outcomes: list[dict[str, Any]] = []
        for row in queue["selected"]:
            pid = uuid.UUID(row["prospect_id"])
            try:
                outcomes.append(self.activate_prospect(pid, dry_run=dry_run))
            except Exception as exc:  # noqa: BLE001
                logger.exception("activation failed for %s", pid)
                outcomes.append(
                    {
                        "ok": False,
                        "prospect_id": str(pid),
                        "dry_run": dry_run,
                        "activated": False,
                        "errors": [str(exc)],
                    }
                )
        activated = sum(1 for o in outcomes if o.get("activated"))
        return {
            "queue": queue,
            "outcomes": outcomes,
            "dry_run": dry_run,
            "activated_count": activated,
            "attempted": len(outcomes),
        }

    def _resolve_campaign(self, *, required: bool) -> Campaign | None:
        icp = self.icp.as_dict()
        raw_id = icp.get("autonomous_campaign_id")
        if raw_id:
            try:
                campaign = self.campaigns.get_campaign(uuid.UUID(str(raw_id)))
            except CampaignNotFoundError as exc:
                if required:
                    raise AutonomousActivationError(
                        "Configured autonomous campaign was not found."
                    ) from exc
                return None
            return campaign
        if required:
            raise AutonomousActivationError(
                "No autonomous campaign configured. Set it under AI Settings → Activation."
            )
        return None

    def _get_or_attach(self, campaign_id: uuid.UUID, prospect_id: uuid.UUID) -> CampaignProspect:
        existing = self.db.scalar(
            select(CampaignProspect)
            .where(
                CampaignProspect.campaign_id == campaign_id,
                CampaignProspect.prospect_id == prospect_id,
            )
            .options(joinedload(CampaignProspect.prospect))
        )
        if existing is not None:
            return existing
        try:
            return self.campaigns.attach_prospect(campaign_id, prospect_id)
        except CampaignValidationError as exc:
            # Race: attached between check and attach.
            existing = self.db.scalar(
                select(CampaignProspect)
                .where(
                    CampaignProspect.campaign_id == campaign_id,
                    CampaignProspect.prospect_id == prospect_id,
                )
                .options(joinedload(CampaignProspect.prospect))
            )
            if existing is not None:
                return existing
            raise AutonomousActivationError(exc.message) from exc

    def _ensure_autonomous_initial_template(self, campaign: Campaign) -> None:
        """Ensure INITIAL uses personalized Instantly merge tags + assessment_url CTA."""
        placeholder = self.settings.assessment_url_placeholder or "{{assessment_url}}"
        messages = list(campaign.messages or [])
        initial = next(
            (
                m
                for m in sorted(messages, key=lambda row: row.sequence_order)
                if m.message_type == MessageType.INITIAL and m.is_active
            ),
            None,
        )
        desired_subject = "{{personalized_subject}}"
        desired_body = (
            "{{personalized_body}}\n\n"
            f"{placeholder}\n"
        )
        if initial is None:
            return
        changed = False
        if "{{personalized_body}}" not in (initial.body or ""):
            initial.body = desired_body
            changed = True
        if "{{personalized_subject}}" not in (initial.subject or ""):
            initial.subject = desired_subject
            changed = True
        if placeholder not in (initial.body or ""):
            initial.body = (initial.body or "").rstrip() + f"\n\n{placeholder}\n"
            changed = True
        if changed:
            self.db.add(initial)
            self.db.commit()

    @staticmethod
    def _message_dict(message: PersonalizedOutreachResult) -> dict[str, Any]:
        return {
            "subject": message.subject,
            "body": message.body,
            "message_angle": message.message_angle,
            "supporting_signal": message.supporting_signal,
            "supporting_source_ids": message.supporting_source_ids,
            "confidence": message.confidence,
            "sender_identity": message.sender_identity,
            "used_llm": message.used_llm,
        }
