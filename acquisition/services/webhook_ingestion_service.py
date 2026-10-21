"""Webhook ingestion + polling fallback for Instantly delivery/reply events."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from acquisition.config import get_settings
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.redaction import redact_secrets
from acquisition.models.campaign import (
    Campaign,
    CampaignProspect,
    OutreachStatus,
)
from acquisition.models.replies import ProviderEvent
from acquisition.services.email_body import strip_email_quotes
from acquisition.services.instantly_lead_control import stop_instantly_lead_outreach
from acquisition.services.instantly_service import InstantlyService
from acquisition.services.reply_automation_service import ReplyAutomationService

logger = logging.getLogger(__name__)

HANDLED_EVENTS = {
    "email_sent",
    "reply_received",
    "auto_reply_received",
    "email_bounced",
    "lead_unsubscribed",
    "campaign_completed",
    "lead_not_interested",
}


class WebhookAuthError(Exception):
    pass


class WebhookIngestionService:
    def __init__(
        self,
        db: Session,
        *,
        instantly_service: InstantlyService | None = None,
        reply_service: ReplyAutomationService | None = None,
        client: InstantlyClient | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self.db = db
        self.instantly_service = instantly_service or InstantlyService(db=db)
        self.reply_service = reply_service or ReplyAutomationService(
            db=db, client=client, openai_client=openai_client
        )
        self._client = client
        self.settings = get_settings()

    def verify_authorization(self, authorization: str | None) -> None:
        expected = (self.settings.instantly_webhook_secret or "").strip()
        if not expected:
            # Development: allow unsigned if secret unset, but log.
            logger.warning("INSTANTLY_WEBHOOK_SECRET unset; accepting webhook without auth")
            return
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token != expected:
            raise WebhookAuthError("Invalid webhook authorization")

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = str(payload.get("event_type") or "").strip()
        event_id = self._event_id(payload)
        if self._already_processed(event_id):
            return {"status": "duplicate", "event_id": event_id}

        summary = self._safe_summary(payload)
        link = self._resolve_link(payload)
        campaign = link.campaign if link else self._resolve_campaign(payload)

        logger.info(
            "instantly_webhook event_type=%s event_id=%s campaign_id=%s lead_email=%s "
            "email_id=%s matched_prospect=%s",
            event_type or "unknown",
            event_id,
            payload.get("campaign_id") or (str(campaign.id) if campaign else None),
            self._lead_email(payload) or None,
            payload.get("email_id") or payload.get("reply_to_uuid") or None,
            bool(link),
        )

        event = ProviderEvent(
            provider="instantly",
            event_id=event_id,
            event_type=event_type or "unknown",
            campaign_id=campaign.id if campaign else None,
            campaign_prospect_id=link.id if link else None,
            payload_summary=summary,
        )
        self.db.add(event)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return {"status": "duplicate", "event_id": event_id}

        if event_type not in HANDLED_EVENTS:
            return {"status": "ignored", "event_type": event_type}

        if link is None:
            return {"status": "unmatched", "event_type": event_type}

        now = datetime.now(timezone.utc)
        link.last_activity_at = now
        handled = "processed"

        if event_type == "email_sent":
            if link.outreach_status == OutreachStatus.PENDING:
                link.outreach_status = OutreachStatus.ACTIVE
        elif event_type == "reply_received":
            link.outreach_status = OutreachStatus.REPLIED
            link.last_reply_at = now
            self.reply_service.handle_inbound_reply(link, payload)
        elif event_type == "auto_reply_received":
            self.reply_service.handle_auto_reply(link, payload)
        elif event_type == "email_bounced":
            link.outreach_status = OutreachStatus.BOUNCED
            self._stop_remote(link)
        elif event_type in {"lead_unsubscribed", "lead_not_interested"}:
            link.outreach_status = OutreachStatus.UNSUBSCRIBED
            self._stop_remote(link)
        elif event_type == "campaign_completed":
            link.outreach_status = OutreachStatus.COMPLETED

        self.db.add(link)
        self.db.commit()
        return {"status": handled, "event_type": event_type, "event_id": event_id}

    def poll_recent_emails(self) -> dict[str, int]:
        """Fallback when Instantly webhooks are unavailable.

        Instantly ue_type: 1=sent from campaign, 2=received, 3=sent, 4=scheduled.
        Ingest campaign sends as email_sent and received mail as replies.
        """
        client = self._resolve_client()
        # Unfiltered list includes sent + received (email_type=received alone misses sends).
        emails = client.list_emails(limit=100)
        processed = duplicates = sent = replies = 0
        for item in emails:
            ue_type = item.get("ue_type")
            try:
                ue_type_i = int(ue_type) if ue_type is not None else None
            except (TypeError, ValueError):
                ue_type_i = None

            if ue_type_i in {1, 3}:
                lead_email = (
                    item.get("lead")
                    or item.get("lead_email")
                    or item.get("to_address_email_list")
                    or item.get("to_address_email")
                )
                if isinstance(lead_email, list):
                    lead_email = lead_email[0] if lead_email else None
                payload = {
                    "event_type": "email_sent",
                    "timestamp": item.get("timestamp_email")
                    or item.get("timestamp_created")
                    or item.get("timestamp"),
                    "campaign_id": item.get("campaign_id") or item.get("campaign"),
                    "lead_email": lead_email,
                    "email_id": item.get("id") or item.get("reply_to_uuid"),
                    "email_account": item.get("eaccount") or item.get("email_account"),
                    "workspace": "poll",
                }
                result = self.ingest(payload)
                if result.get("status") == "duplicate":
                    duplicates += 1
                else:
                    processed += 1
                    sent += 1
                continue

            if ue_type_i is not None and ue_type_i != 2:
                continue

            body = item.get("body")
            reply_text = ""
            if isinstance(body, dict):
                reply_text = str(body.get("text") or "").strip()
                if not reply_text:
                    # Instantly often stores received bodies as HTML only.
                    html = str(body.get("html") or "")
                    reply_text = re.sub(r"<[^>]+>", " ", html)
                    reply_text = re.sub(r"\s+", " ", reply_text).strip()
            else:
                reply_text = str(item.get("text") or item.get("body_text") or body or "").strip()

            reply_text = strip_email_quotes(reply_text)

            lead_email = (
                item.get("from_address_email")
                or item.get("lead_email")
                or item.get("from")
            )
            payload = {
                "event_type": "reply_received",
                "timestamp": item.get("timestamp_created") or item.get("timestamp"),
                "campaign_id": item.get("campaign_id") or item.get("campaign"),
                "lead_email": lead_email,
                "email_id": item.get("id") or item.get("reply_to_uuid"),
                "email_account": item.get("eaccount") or item.get("email_account"),
                "reply_subject": item.get("subject"),
                "reply_text": reply_text,
                "workspace": "poll",
            }
            result = self.ingest(payload)
            if result.get("status") == "duplicate":
                duplicates += 1
            else:
                processed += 1
                replies += 1
        return {
            "processed": processed,
            "duplicates": duplicates,
            "sent": sent,
            "replies": replies,
        }

    def _stop_remote(self, link: CampaignProspect) -> None:
        if link.campaign is None:
            return
        try:
            client = self._resolve_client()
            stop_instantly_lead_outreach(client, campaign=link.campaign, link=link)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to stop Instantly lead: %s", type(exc).__name__)

    def _resolve_link(self, payload: dict[str, Any]) -> CampaignProspect | None:
        email = self._lead_email(payload)
        campaign_remote = str(payload.get("campaign_id") or "").strip()
        if not email:
            return None
        stmt = (
            select(CampaignProspect)
            .options(
                selectinload(CampaignProspect.prospect),
                selectinload(CampaignProspect.campaign),
            )
            .join(CampaignProspect.prospect)
            .join(CampaignProspect.campaign)
        )
        links = list(self.db.scalars(stmt).all())
        email_matches: list[CampaignProspect] = []
        for link in links:
            if not link.prospect or link.prospect.email.lower() != email:
                continue
            email_matches.append(link)
            if campaign_remote and link.campaign and link.campaign.instantly_campaign_id:
                if link.campaign.instantly_campaign_id == campaign_remote:
                    return link
        # Prefer exact Instantly campaign match; otherwise fall back to email so
        # replies still work after a campaign was recreated / remapped in Admin.
        if campaign_remote and email_matches:
            logger.warning(
                "Reply campaign_id=%s did not match local Instantly campaign for %s; "
                "falling back to email match (%s link(s))",
                campaign_remote,
                email,
                len(email_matches),
            )
        return email_matches[0] if email_matches else None

    def _lead_email(self, payload: dict[str, Any]) -> str:
        for key in ("lead_email", "email", "from_address_email", "lead"):
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get("email")
            cleaned = str(value or "").strip().lower()
            if cleaned and "@" in cleaned:
                return cleaned
        return ""

    def _resolve_campaign(self, payload: dict[str, Any]) -> Campaign | None:
        remote = str(payload.get("campaign_id") or "").strip()
        if not remote:
            return None
        return self.db.scalar(select(Campaign).where(Campaign.instantly_campaign_id == remote))

    def _already_processed(self, event_id: str) -> bool:
        existing = self.db.scalar(
            select(ProviderEvent).where(
                ProviderEvent.provider == "instantly",
                ProviderEvent.event_id == event_id,
            )
        )
        return existing is not None

    def _event_id(self, payload: dict[str, Any]) -> str:
        explicit = (
            payload.get("id")
            or payload.get("event_id")
            or payload.get("email_id")
            or payload.get("reply_to_uuid")
        )
        if explicit:
            return f"{payload.get('event_type')}:{explicit}"
        raw = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
        return f"{payload.get('event_type')}:{digest}"

    def _safe_summary(self, payload: dict[str, Any]) -> str:
        keys = (
            "event_type",
            "timestamp",
            "campaign_id",
            "campaign_name",
            "lead_email",
            "email",
            "email_account",
            "email_id",
            "reply_subject",
            "reply_text_snippet",
        )
        slim = {k: payload.get(k) for k in keys if k in payload}
        lead = self._lead_email(payload)
        if lead and "lead_email" not in slim:
            slim["lead_email"] = lead
        return redact_secrets(json.dumps(slim, default=str), api_key=None)[:1000]

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        return self.instantly_service.get_api_client()
