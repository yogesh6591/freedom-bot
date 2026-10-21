"""Sync approved CampaignProspects to Instantly leads (idempotent)."""

from __future__ import annotations

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
    CampaignMessage,
    CampaignProspect,
    CampaignStatus,
    InstantlySyncStatus,
    LeadSyncStatus,
    MessageType,
)
from acquisition.models.prospect import ApprovalStatus
from acquisition.services.assessment_tokens import (
    build_assessment_url,
    generate_assessment_token,
    split_executive_name,
)
from acquisition.services.campaign_service import CampaignNotFoundError, CampaignService
from acquisition.services.instantly_service import InstantlyService
from acquisition.services.outreach_conflict_service import find_active_outreach_conflict


def _company_for_lead(company: str | None, email: str | None) -> str:
    """Keep a real company. An email host such as gmail.com is not one."""
    text = (company or "").strip()
    host = (email or "").partition("@")[2].strip()
    if not text or (host and text.casefold() == host.casefold()):
        return ""
    return text


class LeadSyncError(Exception):
    def __init__(self, message: str, *, http_status: int = 422) -> None:
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LeadSyncResult:
    campaign_id: uuid.UUID
    synced: int
    failed: int
    skipped: int
    message: str


class LeadSyncService:
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

    def sync_campaign_leads(self, campaign_id: uuid.UUID) -> LeadSyncResult:
        campaign = self.campaign_service.get_campaign(campaign_id)
        self._require_campaign_ready_for_leads(campaign)
        client = self._prepare_client(campaign)

        synced = failed = skipped = 0
        for link in campaign.campaign_prospects:
            prospect = link.prospect
            if prospect is None or prospect.approval_status != ApprovalStatus.APPROVED:
                skipped += 1
                continue
            conflict = find_active_outreach_conflict(
                self.db,
                prospect_id=link.prospect_id,
                email=prospect.email,
                excluding_link_id=link.id,
            )
            if conflict is not None:
                skipped += 1
                link.sync_status = LeadSyncStatus.SYNC_ERROR
                link.sync_error = conflict.admin_message[:500]
                self.db.add(link)
                self.db.commit()
                continue
            try:
                self._sync_one(client, campaign, link)
                synced += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                link.sync_status = LeadSyncStatus.SYNC_ERROR
                link.sync_error = redact_secrets(str(exc), api_key=None)[:500]
                self.db.add(link)
                self.db.commit()

        return LeadSyncResult(
            campaign_id=campaign.id,
            synced=synced,
            failed=failed,
            skipped=skipped,
            message=f"Lead sync complete: {synced} synced, {failed} failed, {skipped} skipped",
        )

    def sync_one_lead(
        self,
        campaign_id: uuid.UUID,
        prospect_id: uuid.UUID,
        *,
        extra_custom_variables: dict[str, str] | None = None,
        attribution: dict[str, str | None] | None = None,
    ) -> LeadSyncResult:
        """Sync a single attached approved prospect to Instantly."""
        campaign = self.campaign_service.get_campaign(campaign_id)
        self._require_campaign_ready_for_leads(campaign)

        link = next(
            (
                item
                for item in campaign.campaign_prospects
                if item.prospect_id == prospect_id
            ),
            None,
        )
        if link is None:
            raise LeadSyncError("Potential customer is not attached to this campaign.", http_status=404)

        prospect = link.prospect
        if prospect is None or prospect.approval_status != ApprovalStatus.APPROVED:
            raise LeadSyncError("Only approved potential customers can be synced.")

        conflict = find_active_outreach_conflict(
            self.db,
            prospect_id=link.prospect_id,
            email=prospect.email,
            excluding_link_id=link.id,
        )
        if conflict is not None:
            link.sync_status = LeadSyncStatus.SYNC_ERROR
            link.sync_error = conflict.admin_message[:500]
            self.db.add(link)
            self.db.commit()
            raise LeadSyncError(conflict.admin_message)

        client = self._prepare_client(campaign, extra_variables=list((extra_custom_variables or {}).keys()))
        try:
            self._sync_one(
                client,
                campaign,
                link,
                extra_custom_variables=extra_custom_variables,
                attribution=attribution,
            )
        except LeadSyncError:
            raise
        except Exception as exc:  # noqa: BLE001
            link.sync_status = LeadSyncStatus.SYNC_ERROR
            link.sync_error = redact_secrets(str(exc), api_key=None)[:500]
            self.db.add(link)
            self.db.commit()
            raise LeadSyncError(link.sync_error or "Lead sync failed.") from exc

        return LeadSyncResult(
            campaign_id=campaign.id,
            synced=1,
            failed=0,
            skipped=0,
            message=f"Synced {prospect.email}",
        )
    def _require_campaign_ready_for_leads(self, campaign: Campaign) -> None:
        if campaign.status != CampaignStatus.READY:
            raise LeadSyncError("Campaign must be Ready before syncing leads.")
        if not campaign.instantly_campaign_id or campaign.instantly_sync_status != InstantlySyncStatus.SYNCED:
            raise LeadSyncError("Campaign must be synced to Instantly before syncing leads.")

    def _prepare_client(
        self, campaign: Campaign, *, extra_variables: list[str] | None = None
    ) -> InstantlyClient:
        client = self._resolve_client()
        variables = ["assessment_url", "personalized_subject", "personalized_body"]
        for name in extra_variables or []:
            if name and name not in variables:
                variables.append(name)
        try:
            client.add_campaign_variables(campaign.instantly_campaign_id or "", variables)
        except InstantlyError:
            # Variable may already exist; continue.
            pass
        return client

    def _sync_one(
        self,
        client: InstantlyClient,
        campaign: Campaign,
        link: CampaignProspect,
        *,
        extra_custom_variables: dict[str, str] | None = None,
        attribution: dict[str, str | None] | None = None,
    ) -> None:
        prospect = link.prospect
        assert prospect is not None
        if not link.assessment_token:
            link.assessment_token = generate_assessment_token()
        assessment_url = build_assessment_url(link.assessment_token, settings=self.settings)
        first_name, last_name = split_executive_name(prospect.executive_name)

        custom_variables: dict[str, str] = {"assessment_url": assessment_url}
        if extra_custom_variables:
            for key, value in extra_custom_variables.items():
                if key and value is not None:
                    custom_variables[str(key)] = str(value)

        company = _company_for_lead(prospect.company, prospect.email)
        payload: dict = {
            "email": prospect.email,
            "first_name": first_name or None,
            "last_name": last_name or None,
            "company_name": company or None,
            "website": prospect.website,
            "job_title": prospect.executive_title,
            "campaign": campaign.instantly_campaign_id,
            "skip_if_in_campaign": False,
            "custom_variables": custom_variables,
        }
        # Drop nulls Instantly may reject.
        payload = {k: v for k, v in payload.items() if v is not None}

        on_campaign = self._find_lead_id(
            client, campaign.instantly_campaign_id or "", prospect.email
        )
        if on_campaign:
            remote = client.update_lead(
                on_campaign,
                {
                    "email": prospect.email,
                    "first_name": first_name or None,
                    "last_name": last_name or None,
                    "company_name": company or "",
                    "website": prospect.website,
                    "job_title": prospect.executive_title,
                    "payload": custom_variables,
                },
            )
            lead_id = str(remote.get("id") or on_campaign)
        else:
            remote = client.create_lead(payload)
            lead_id = str(remote.get("id") or "").strip()
            if not lead_id:
                lead_id = self._find_lead_id(client, campaign.instantly_campaign_id or "", prospect.email)
            if not lead_id:
                raise LeadSyncError("Instantly create lead response missing id")

        now = datetime.now(timezone.utc)
        link.instantly_lead_id = lead_id
        link.sync_status = LeadSyncStatus.SYNCED
        link.sync_error = None
        link.last_synced_at = now
        # Do not mark outreach ACTIVE here — that meant "email sent" in the UI.
        # ACTIVE is set when Instantly reports email_sent (webhook or poll).
        if link.last_activity_at is None:
            link.last_activity_at = now
        self._apply_attribution(link, attribution)
        self._snapshot_outreach_attribution(campaign, link, prospect)
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)

    def _apply_attribution(
        self, link: CampaignProspect, attribution: dict[str, str | None] | None
    ) -> None:
        if not attribution:
            return
        if attribution.get("outreach_message_key") and not (link.outreach_message_key or "").strip():
            link.outreach_message_key = str(attribution["outreach_message_key"])[:255]
        if attribution.get("outreach_message_snapshot") and not (
            link.outreach_message_snapshot or ""
        ).strip():
            link.outreach_message_snapshot = str(attribution["outreach_message_snapshot"])
        if attribution.get("outreach_signal_snapshot") and not (
            link.outreach_signal_snapshot or ""
        ).strip():
            link.outreach_signal_snapshot = str(attribution["outreach_signal_snapshot"])

    def _snapshot_outreach_attribution(
        self, campaign: Campaign, link: CampaignProspect, prospect
    ) -> None:
        """Capture message/signal once at first successful sync (stable attribution)."""
        if not (link.outreach_message_snapshot or "").strip():
            messages = list(getattr(campaign, "messages", None) or [])
            initial = next(
                (
                    m
                    for m in sorted(messages, key=lambda row: row.sequence_order)
                    if m.is_active and m.message_type == MessageType.INITIAL
                ),
                None,
            )
            if initial is None and messages:
                initial = sorted(messages, key=lambda row: row.sequence_order)[0]
            if initial is not None:
                link.outreach_message_key = str(initial.id)
                subject = (initial.subject or "").strip()
                body = (initial.body or "").strip()
                link.outreach_message_snapshot = (
                    f"Subject: {subject}\n\n{body}".strip() if (subject or body) else None
                )
        if not (link.outreach_signal_snapshot or "").strip():
            parts: list[str] = []
            source = (getattr(prospect, "source", None) or "").strip()
            fit = (getattr(prospect, "fit_note", None) or "").strip()
            signals = getattr(prospect, "public_signals", None)
            if source:
                parts.append(source)
            if fit:
                parts.append(fit)
            if isinstance(signals, list) and signals:
                parts.append(str(signals[0]))
            link.outreach_signal_snapshot = ": ".join(parts) if parts else None

    def _find_lead_id(self, client: InstantlyClient, campaign_id: str, email: str) -> str:
        data = client.list_leads({"campaign": campaign_id, "search": email, "limit": 20})
        items = data.get("items") or []
        for item in items:
            if str(item.get("email") or "").lower() == email.lower():
                return str(item.get("id") or "")
        return ""

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        try:
            return self.instantly_service.get_api_client()
        except InstantlyConfigError as exc:
            raise LeadSyncError(exc.message) from exc
