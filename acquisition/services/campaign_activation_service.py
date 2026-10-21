"""Sending-account selection and Instantly activate/pause gates."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.errors import InstantlyConfigError, InstantlyError
from acquisition.integrations.instantly.redaction import redact_secrets
from acquisition.models.campaign import (
    Campaign,
    CampaignStatus,
    InstantlyRuntimeStatus,
    InstantlySyncStatus,
    LeadSyncStatus,
    MessageChannel,
)
from acquisition.models.integration import ConnectionStatus, IntegrationProvider
from acquisition.models.prospect import ApprovalStatus
from acquisition.services.campaign_service import CampaignNotFoundError, CampaignService
from acquisition.services.instantly_service import InstantlyService


class ActivationError(Exception):
    def __init__(self, message: str, *, missing: list[str] | None = None, http_status: int = 422) -> None:
        self.message = message
        self.missing = missing or []
        self.http_status = http_status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SendingAccountInfo:
    email: str
    status: str | None
    provider_code: int | None
    raw: dict


class CampaignActivationService:
    def __init__(
        self,
        db: Session,
        *,
        campaign_service: CampaignService | None = None,
        instantly_service: InstantlyService | None = None,
        client: InstantlyClient | None = None,
    ) -> None:
        self.db = db
        self.campaign_service = campaign_service or CampaignService(db)
        self.instantly_service = instantly_service or InstantlyService(db=db)
        self._client = client
        self.settings = get_settings()

    def list_sending_accounts(self) -> list[SendingAccountInfo]:
        client = self._resolve_client()
        try:
            accounts = client.list_accounts(limit=100)
        except InstantlyError as exc:
            raise ActivationError(
                redact_secrets(exc.message, api_key=None),
                http_status=502,
            ) from exc
        results: list[SendingAccountInfo] = []
        for item in accounts:
            email = str(item.get("email") or item.get("username") or "").strip()
            if not email:
                continue
            results.append(
                SendingAccountInfo(
                    email=email,
                    status=str(item.get("status")) if item.get("status") is not None else None,
                    provider_code=item.get("provider_code") if isinstance(item.get("provider_code"), int) else None,
                    raw={k: v for k, v in item.items() if k not in {"password", "smtp_password", "token"}},
                )
            )
        return results

    def save_sending_accounts(self, campaign_id: uuid.UUID, emails: list[str]) -> Campaign:
        campaign = self.campaign_service.get_campaign(campaign_id)
        cleaned = sorted({e.strip().lower() for e in emails if e and e.strip()})
        if not cleaned:
            raise ActivationError("Select at least one sending account.")
        campaign.instantly_email_list = json.dumps(cleaned)
        self.db.add(campaign)
        self.db.commit()
        return self.campaign_service.get_campaign(campaign_id)

    def get_selected_emails(self, campaign: Campaign) -> list[str]:
        if not campaign.instantly_email_list:
            return []
        try:
            data = json.loads(campaign.instantly_email_list)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [str(item) for item in data]

    def activation_checklist(self, campaign_id: uuid.UUID) -> list[dict]:
        """Structured activation checklist for UI (done + short action)."""
        campaign = self.campaign_service.get_campaign(campaign_id)
        placeholder = self.settings.assessment_url_placeholder

        is_ready = campaign.status == CampaignStatus.READY
        is_synced = bool(
            campaign.instantly_campaign_id
            and campaign.instantly_sync_status == InstantlySyncStatus.SYNCED
        )
        has_sender = bool(self.get_selected_emails(campaign))

        links = campaign.campaign_prospects
        has_prospects = bool(links)
        all_approved = has_prospects and all(
            link.prospect is not None
            and link.prospect.approval_status == ApprovalStatus.APPROVED
            for link in links
        )
        all_leads_synced = all_approved and all(
            link.sync_status == LeadSyncStatus.SYNCED for link in links
        )

        active_msgs = [
            m
            for m in campaign.messages
            if m.is_active and m.channel == MessageChannel.EMAIL
        ]
        has_cta = bool(active_msgs) and any(
            placeholder in ((m.body or "") + (m.subject or "")) for m in active_msgs
        )

        conn = self.instantly_service.get_summary()
        instantly_ok = (
            conn is not None
            and conn.api_key_configured
            and conn.connection_status != ConnectionStatus.ERROR
        )

        if not has_prospects:
            prospect_detail = "Attach someone in the Customers tab"
        elif not all_approved:
            prospect_detail = "Every attached potential customer must be approved"
        elif not all_leads_synced:
            prospect_detail = "Sync potential customers in the Customers tab"
        else:
            prospect_detail = None

        if not active_msgs:
            cta_detail = "Add active email copy in Sequence"
        elif not has_cta:
            cta_detail = f"Include {placeholder} in Sequence copy"
        else:
            cta_detail = None

        if conn is None or not conn.api_key_configured:
            instantly_detail = "Connect Instantly under Integrations"
        elif conn.connection_status == ConnectionStatus.ERROR:
            instantly_detail = "Fix Instantly connection under Integrations"
        else:
            instantly_detail = None

        return [
            {
                "key": "ready",
                "label": "Campaign ready",
                "done": is_ready,
                "detail": None if is_ready else "Finish setup, then Sync Campaign in Launch",
            },
            {
                "key": "campaign_sync",
                "label": "Campaign synced",
                "done": is_synced,
                "detail": None if is_synced else "Sync campaign in the Launch tab",
            },
            {
                "key": "prospects",
                "label": "Potential customers ready",
                "done": all_leads_synced,
                "detail": prospect_detail,
            },
            {
                "key": "sending",
                "label": "Sending account",
                "done": has_sender,
                "detail": None if has_sender else "Select a sending account in Launch",
            },
            {
                "key": "cta",
                "label": "Assessment link in copy",
                "done": has_cta,
                "detail": cta_detail,
            },
            {
                "key": "instantly",
                "label": "Instantly connected",
                "done": instantly_ok,
                "detail": instantly_detail,
            },
        ]

    def activation_blockers(self, campaign_id: uuid.UUID) -> list[str]:
        return [
            item["detail"] or item["label"]
            for item in self.activation_checklist(campaign_id)
            if not item["done"]
        ]

    def activate(self, campaign_id: uuid.UUID, *, confirm: bool = False) -> Campaign:
        if not confirm:
            raise ActivationError("Activation requires explicit confirmation.")
        blockers = self.activation_blockers(campaign_id)
        if blockers:
            raise ActivationError(
                "Campaign cannot be activated until prerequisites are met.",
                missing=blockers,
            )
        campaign = self.campaign_service.get_campaign(campaign_id)
        client = self._resolve_client()
        emails = self.get_selected_emails(campaign)
        try:
            client.update_campaign(
                campaign.instantly_campaign_id or "",
                {"email_list": emails},
            )
            client.activate_campaign(campaign.instantly_campaign_id or "")
        except InstantlyError as exc:
            raise ActivationError(
                redact_secrets(exc.message, api_key=None),
                http_status=502,
            ) from exc
        campaign.instantly_runtime_status = InstantlyRuntimeStatus.ACTIVE
        self.db.add(campaign)
        self.db.commit()
        return self.campaign_service.get_campaign(campaign_id)

    def pause(self, campaign_id: uuid.UUID) -> Campaign:
        campaign = self.campaign_service.get_campaign(campaign_id)
        if not campaign.instantly_campaign_id:
            raise ActivationError("Campaign is not synced to Instantly.")
        client = self._resolve_client()
        try:
            client.pause_campaign(campaign.instantly_campaign_id)
        except InstantlyError as exc:
            raise ActivationError(
                redact_secrets(exc.message, api_key=None),
                http_status=502,
            ) from exc
        campaign.instantly_runtime_status = InstantlyRuntimeStatus.PAUSED
        self.db.add(campaign)
        self.db.commit()
        return self.campaign_service.get_campaign(campaign_id)

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        try:
            return self.instantly_service.get_api_client()
        except InstantlyConfigError as exc:
            raise ActivationError(exc.message) from exc
