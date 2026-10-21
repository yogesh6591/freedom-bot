"""AI-assisted reply automation for Instantly email replies."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.config import get_settings
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.errors import InstantlyError
from acquisition.integrations.instantly.redaction import redact_secrets
from acquisition.models.campaign import CampaignProspect, OutreachStatus, ReplyAIStatus, ReplyMessageDirection
from acquisition.models.replies import ReplyConversation, ReplyMessage
from acquisition.models.settings import DEFAULT_AI_SYSTEM_PROMPT, AIReplySettings
from acquisition.services.assessment_tokens import build_assessment_url
from acquisition.services.email_body import (
    break_long_answer_paragraphs,
    plain_text_to_email_html,
    strip_email_quotes,
)
from acquisition.services.instantly_lead_control import stop_instantly_lead_outreach
from acquisition.services.instantly_service import InstantlyService
from acquisition.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

STOP_PATTERNS = re.compile(
    r"\b(unsubscribe|stop contacting|do not contact|don't contact|remove me|not interested)\b",
    re.IGNORECASE,
)
OOO_PATTERNS = re.compile(
    r"\b(out of office|automatic reply|auto[- ]?reply|away from (the )?office)\b",
    re.IGNORECASE,
)
# Cal.com / calendar system mail — never treat as a prospect reply.
CALENDAR_NOTIFY_PATTERNS = re.compile(
    r"(?:^|\n)\s*(?:a new event has been scheduled|your event has been scheduled)\b"
    r"|\bneed to reschedule or cancel\?\s*https?://(?:www\.)?cal\.com/"
    r"|\binvitee timezone:\s*"
    r"|\bbetween .+ and .+\n\s*We sent an email to all participants",
    re.IGNORECASE,
)


class ReplyAutomationService:
    def __init__(
        self,
        db: Session,
        *,
        instantly_service: InstantlyService | None = None,
        client: InstantlyClient | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self.db = db
        self.instantly_service = instantly_service or InstantlyService(db=db)
        self._client = client
        self._openai_client = openai_client
        self.settings = get_settings()

    def handle_auto_reply(self, link: CampaignProspect, payload: dict[str, Any]) -> None:
        conversation = self._get_or_create_conversation(link)
        body = str(payload.get("reply_text") or payload.get("reply_text_snippet") or "").strip()
        conversation.latest_incoming_snippet = body[:500]
        conversation.status = ReplyAIStatus.SKIPPED_AUTO_REPLY
        self.db.add(conversation)
        self.db.commit()

    def handle_inbound_reply(self, link: CampaignProspect, payload: dict[str, Any]) -> None:
        raw_body = str(
            payload.get("reply_text")
            or payload.get("reply_text_snippet")
            or payload.get("email_text")
            or ""
        ).strip()
        body = strip_email_quotes(raw_body)
        subject = str(payload.get("reply_subject") or payload.get("email_subject") or "").strip()
        provider_email_id = str(payload.get("email_id") or "").strip() or None
        eaccount = str(payload.get("email_account") or "").strip()

        conversation = self._get_or_create_conversation(link)
        conversation.latest_incoming_snippet = body[:500]
        conversation.last_provider_email_id = provider_email_id
        conversation.provider_thread_hint = eaccount or conversation.provider_thread_hint

        inbound = ReplyMessage(
            conversation_id=conversation.id,
            direction=ReplyMessageDirection.INBOUND,
            subject=subject or None,
            body=body or "(empty reply)",
            provider_email_id=provider_email_id,
        )
        self.db.add(inbound)
        self.db.flush()

        if STOP_PATTERNS.search(body):
            conversation.status = ReplyAIStatus.STOPPED
            link.outreach_status = OutreachStatus.STOPPED
            self.db.add(conversation)
            self.db.add(link)
            self.db.commit()
            if link.campaign:
                try:
                    stop_instantly_lead_outreach(
                        self._resolve_client(), campaign=link.campaign, link=link
                    )
                except InstantlyError:
                    logger.warning("Could not stop Instantly lead after unsubscribe reply")
            return

        if OOO_PATTERNS.search(body):
            conversation.status = ReplyAIStatus.SKIPPED_AUTO_REPLY
            self.db.add(conversation)
            self.db.commit()
            return

        if self._is_calendar_notification(subject=subject, body=body):
            conversation.status = ReplyAIStatus.SKIPPED_AUTO_REPLY
            self.db.add(conversation)
            self.db.commit()
            return

        ai_settings = self._ai_settings()
        if not ai_settings.auto_reply_enabled:
            conversation.status = ReplyAIStatus.NEEDS_REVIEW
            self.db.add(conversation)
            self.db.commit()
            return

        draft = self._generate_reply(conversation, link, ai_settings)
        if not draft:
            conversation.status = ReplyAIStatus.NEEDS_REVIEW
            self.db.add(conversation)
            self.db.commit()
            return

        outbound = ReplyMessage(
            conversation_id=conversation.id,
            direction=ReplyMessageDirection.OUTBOUND,
            subject=subject if subject.lower().startswith("re:") else f"Re: {subject or 'your note'}",
            body=draft,
            provider_email_id=None,
        )
        self.db.add(outbound)
        self.db.flush()

        if not provider_email_id or not eaccount:
            conversation.status = ReplyAIStatus.NEEDS_REVIEW
            self.db.add(conversation)
            self.db.commit()
            return

        try:
            self._resolve_client().reply_to_email(
                {
                    "reply_to_uuid": provider_email_id,
                    "eaccount": eaccount,
                    "subject": outbound.subject or f"Re: {subject or 'your note'}",
                    "body": {
                        "html": plain_text_to_email_html(draft),
                        "text": draft,
                    },
                }
            )
        except InstantlyError as exc:
            logger.warning("Instantly reply failed: %s", redact_secrets(exc.message, api_key=None))
            conversation.status = ReplyAIStatus.NEEDS_REVIEW
            self.db.add(conversation)
            self.db.commit()
            return

        conversation.status = ReplyAIStatus.AUTO_REPLIED
        conversation.updated_at = datetime.now(timezone.utc)
        self.db.add(conversation)
        self.db.commit()

    def _generate_reply(
        self,
        conversation: ReplyConversation,
        link: CampaignProspect,
        ai_settings: AIReplySettings,
    ) -> str | None:
        """Return the model’s full email text with no content post-processing."""
        openai = OpenAIService(db=self.db, settings=self.settings)
        api_key, model = openai.resolve_credentials()
        if not api_key:
            return None

        messages = self._build_thread_messages(conversation, link, ai_settings)
        try:
            client = self._resolve_openai_client(api_key)
            response = client.responses.create(
                model=model or "gpt-4o-mini",
                input=messages,
            )
            text = (getattr(response, "output_text", None) or "").strip()
            if not text:
                text = self._extract_output_text(response)
            if not text:
                return None
            # Layout only (blank lines between sentences) — does not rewrite copy.
            return break_long_answer_paragraphs(text)
        except Exception as exc:  # noqa: BLE001 — provider failure → needs review
            logger.warning("OpenAI reply generation failed: %s", type(exc).__name__)
            return None

    @staticmethod
    def _is_calendar_notification(*, subject: str, body: str) -> bool:
        hay = f"{subject}\n{body}"
        if CALENDAR_NOTIFY_PATTERNS.search(hay):
            return True
        subj = (subject or "").strip().lower()
        if " between " in subj and ("consultation" in subj or "meeting" in subj):
            return True
        return False

    @staticmethod
    def _first_name(link: CampaignProspect) -> str:
        prospect = link.prospect
        full = (prospect.executive_name if prospect else "") or ""
        parts = full.strip().split()
        return parts[0] if parts else ""

    def _build_thread_messages(
        self,
        conversation: ReplyConversation,
        link: CampaignProspect,
        ai_settings: AIReplySettings,
    ) -> list[dict[str, str]]:
        """System prompt = admin AI prompt + runtime context variables only."""
        assessment_url = ""
        if link.assessment_token:
            assessment_url = build_assessment_url(link.assessment_token, settings=self.settings)
        prospect = link.prospect
        company = prospect.company if prospect else ""
        name = prospect.executive_name if prospect else ""
        first_name = self._first_name(link) or "there"
        sender_name = self._sender_name(link)
        admin_prompt = (ai_settings.system_prompt or "").strip() or DEFAULT_AI_SYSTEM_PROMPT

        # Fill simple {placeholders} from the prompt; leave unknown braces alone.
        filled = (
            admin_prompt.replace("{first_name}", first_name)
            .replace("{name}", name or first_name)
            .replace("{company}", company or "")
            .replace("{assessment_url}", assessment_url or "(none yet)")
            .replace("{sender_name}", sender_name)
        )

        system_content = (
            f"{filled}\n\n"
            f"Runtime context for this thread:\n"
            f"- Prospect full name: {name or '(unknown)'}\n"
            f"- Prospect first name: {first_name}\n"
            f"- Company: {company or '(unknown)'}\n"
            f"- Assessment URL: {assessment_url or '(none yet)'}\n"
            f"- Sender name for signature: {sender_name}\n"
            "Write the complete email reply now."
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

        history = list(
            self.db.scalars(
                select(ReplyMessage)
                .where(ReplyMessage.conversation_id == conversation.id)
                .order_by(ReplyMessage.created_at.asc(), ReplyMessage.id.asc())
            ).all()
        )
        for msg in history:
            if msg.direction == ReplyMessageDirection.INBOUND:
                messages.append({"role": "user", "content": msg.body})
            else:
                messages.append({"role": "assistant", "content": msg.body})
        return messages

    def _sender_name(self, link: CampaignProspect) -> str:
        campaign = link.campaign
        if campaign is not None and (campaign.sender_name or "").strip():
            return campaign.sender_name.strip()
        return (self.settings.default_sender_name or "JeanneCAIO / FreedomBot Team").strip()

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        chunks: list[str] = []
        for item in getattr(response, "output", None) or []:
            for part in getattr(item, "content", None) or []:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks).strip()

    def _get_or_create_conversation(self, link: CampaignProspect) -> ReplyConversation:
        existing = self.db.scalar(
            select(ReplyConversation).where(ReplyConversation.campaign_prospect_id == link.id)
        )
        if existing:
            return existing
        conversation = ReplyConversation(campaign_prospect_id=link.id, status=ReplyAIStatus.PENDING)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def _ai_settings(self) -> AIReplySettings:
        row = self.db.get(AIReplySettings, 1)
        if row is None:
            row = AIReplySettings(
                id=1,
                auto_reply_enabled=False,
                system_prompt=DEFAULT_AI_SYSTEM_PROMPT,
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
        return row

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        return self.instantly_service.get_api_client()

    def _resolve_openai_client(self, api_key: str) -> Any:
        if self._openai_client is not None:
            return self._openai_client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai package is not installed. Run: pip install openai"
            ) from exc
        return OpenAI(api_key=api_key)
