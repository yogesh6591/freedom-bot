"""Sync FreedomBot campaigns to Instantly API V2 (definition + sequence only).

Draft campaigns are auto-promoted to Ready when sync succeeds validation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.errors import (
    InstantlyAuthError,
    InstantlyConfigError,
    InstantlyError,
    InstantlyProviderError,
    InstantlyTimeoutError,
)
from acquisition.integrations.instantly.mapper import (
    InstantlyCampaignPreview,
    InstantlyMappingError,
    build_instantly_campaign_payload,
    build_instantly_campaign_preview,
)
from acquisition.integrations.instantly.redaction import redact_secrets
from acquisition.models.campaign import Campaign, CampaignStatus, InstantlySyncStatus
from acquisition.schemas.campaign import CampaignUpdate
from acquisition.services.campaign_service import (
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
)
from acquisition.services.instantly_service import InstantlyService


class InstantlySyncError(Exception):
    def __init__(self, message: str, *, http_status: int = 422) -> None:
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class InstantlySyncResult:
    campaign_id: uuid.UUID
    instantly_campaign_id: str | None
    sync_status: InstantlySyncStatus
    last_synced_at: datetime | None
    sync_error: str | None
    created: bool
    message: str


class InstantlyCampaignSyncService:
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

    def preview(self, campaign_id: uuid.UUID) -> InstantlyCampaignPreview:
        campaign = self.campaign_service.get_campaign(campaign_id)
        try:
            return build_instantly_campaign_preview(campaign)
        except InstantlyMappingError as exc:
            raise InstantlySyncError(exc.message) from exc

    def sync(self, campaign_id: uuid.UUID) -> InstantlySyncResult:
        campaign = self.campaign_service.get_campaign(campaign_id)
        # Ready is an internal gate — auto-promote on sync so Launch doesn't need
        # a separate "Mark Ready" step next to Pause / Activate.
        if campaign.status == CampaignStatus.ARCHIVED:
            raise InstantlySyncError("Archived campaigns cannot be synced.")
        if campaign.status != CampaignStatus.READY:
            try:
                campaign = self.campaign_service.update_campaign(
                    campaign_id,
                    CampaignUpdate(status=CampaignStatus.READY),
                )
            except CampaignValidationError as exc:
                detail = "; ".join(exc.missing) if getattr(exc, "missing", None) else str(exc)
                raise InstantlySyncError(
                    detail or "Finish campaign setup before syncing."
                ) from exc

        try:
            payload = build_instantly_campaign_payload(campaign)
        except InstantlyMappingError as exc:
            self._mark_error(campaign, exc.message)
            raise InstantlySyncError(exc.message) from exc

        try:
            client = self._resolve_client()
            remote_id = (campaign.instantly_campaign_id or "").strip()
            created = False

            # Prefer update when we already linked an Instantly campaign.
            if remote_id:
                remote = client.update_campaign(remote_id, payload)
                remote_id = str(remote.get("id") or remote_id).strip()
            else:
                # Avoid duplicates: reuse an Instantly campaign with the same name.
                existing_id = client.find_campaign_id_by_name(str(payload.get("name") or ""))
                if existing_id:
                    remote = client.update_campaign(existing_id, payload)
                    remote_id = str(remote.get("id") or existing_id).strip()
                    created = False
                else:
                    remote = client.create_campaign(payload)
                    created = True
                    remote_id = str(remote.get("id") or "").strip()
                    if not remote_id:
                        raise InstantlyProviderError(
                            "Instantly create response missing campaign id"
                        )
        except InstantlyConfigError as exc:
            message = self._safe_message(exc.message, default="Instantly is not configured")
            self._mark_error(campaign, message)
            raise InstantlySyncError(message, http_status=422) from exc
        except InstantlyAuthError as exc:
            message = self._safe_message(exc.message, default="Instantly authentication failed")
            self._mark_error(campaign, message)
            raise InstantlySyncError(message, http_status=502) from exc
        except InstantlyTimeoutError as exc:
            message = self._safe_message(exc.message, default="Instantly request timed out")
            self._mark_error(campaign, message)
            raise InstantlySyncError(message, http_status=503) from exc
        except InstantlyProviderError as exc:
            message = self._safe_message(exc.message, default="Instantly provider error")
            self._mark_error(campaign, message)
            raise InstantlySyncError(message, http_status=502) from exc
        except InstantlyError as exc:
            message = self._safe_message(exc.message, default="Instantly sync failed")
            self._mark_error(campaign, message)
            raise InstantlySyncError(message, http_status=502) from exc

        now = datetime.now(timezone.utc)
        campaign.instantly_campaign_id = remote_id
        campaign.instantly_sync_status = InstantlySyncStatus.SYNCED
        campaign.instantly_last_synced_at = now
        campaign.instantly_sync_error = None
        self.db.add(campaign)
        self.db.commit()
        self.db.refresh(campaign)

        return InstantlySyncResult(
            campaign_id=campaign.id,
            instantly_campaign_id=campaign.instantly_campaign_id,
            sync_status=campaign.instantly_sync_status,
            last_synced_at=campaign.instantly_last_synced_at,
            sync_error=None,
            created=created,
            message="Campaign synced to Instantly" if created else "Campaign updated in Instantly",
        )

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        return self.instantly_service.get_api_client()

    def _mark_error(self, campaign: Campaign, message: str) -> None:
        campaign.instantly_sync_status = InstantlySyncStatus.SYNC_ERROR
        campaign.instantly_sync_error = message
        # Preserve existing instantly_campaign_id on failure.
        self.db.add(campaign)
        self.db.commit()
        self.db.refresh(campaign)

    def _safe_message(self, message: str, *, default: str) -> str:
        cleaned = redact_secrets(message, api_key=None).strip()
        return cleaned or default
