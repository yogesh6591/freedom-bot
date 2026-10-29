"""Campaign Pydantic schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignStatus,
    ColdStatus,
    InstantlyRuntimeStatus,
    InstantlySyncStatus,
    LeadSyncStatus,
    MessageChannel,
    MessageType,
    OutreachStatus,
)
from acquisition.schemas.prospect import ProspectRead

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _normalize_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _validate_optional_http_url(value: str | None) -> str | None:
    value = _normalize_optional_str(value)
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be a valid http or https URL")
    return value


def _validate_required_http_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("must be a valid http or https URL")
    return value.strip()


def _validate_hhmm(value: str) -> str:
    cleaned = value.strip()
    if not _HHMM.match(cleaned):
        raise ValueError("must be HH:MM in 24-hour format")
    return cleaned


class CampaignCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=255)
    sender_name: str | None = Field(default=None, max_length=255)
    assessment_url: str | None = Field(default=None, max_length=512)
    status: CampaignStatus = CampaignStatus.DRAFT
    seed_default_sequence: bool = True

    @field_validator("assessment_url")
    @classmethod
    def validate_assessment_url(cls, value: str | None) -> str | None:
        return _validate_optional_http_url(value)

    @field_validator("sender_name", mode="before")
    @classmethod
    def empty_sender_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_optional_str(value)
        return value


class CampaignUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    sender_name: str | None = Field(default=None, min_length=1, max_length=255)
    assessment_url: str | None = Field(default=None, max_length=512)
    status: CampaignStatus | None = None
    send_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    send_window_start: str | None = None
    send_window_end: str | None = None
    send_monday: bool | None = None
    send_tuesday: bool | None = None
    send_wednesday: bool | None = None
    send_thursday: bool | None = None
    send_friday: bool | None = None
    send_saturday: bool | None = None
    send_sunday: bool | None = None

    @field_validator("assessment_url")
    @classmethod
    def validate_assessment_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return _validate_required_http_url(value)

    @field_validator("send_window_start", "send_window_end")
    @classmethod
    def validate_window(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_hhmm(value)

    @model_validator(mode="after")
    def validate_window_order(self) -> CampaignUpdate:
        if self.send_window_start and self.send_window_end:
            if self.send_window_start >= self.send_window_end:
                raise ValueError("send_window_start must be before send_window_end")
        return self


class CampaignMessageCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sequence_order: int | None = Field(default=None, ge=1)
    message_type: MessageType
    channel: MessageChannel = MessageChannel.EMAIL
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = None
    delay_after_previous_hours: int = Field(default=0, ge=0)
    is_active: bool = True

    @field_validator("subject", "body", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_optional_str(value)
        return value


class CampaignMessageUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sequence_order: int | None = Field(default=None, ge=1)
    message_type: MessageType | None = None
    channel: MessageChannel | None = None
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = None
    delay_after_previous_hours: int | None = Field(default=None, ge=0)
    is_active: bool | None = None

    @field_validator("subject", "body", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_optional_str(value)
        return value


class CampaignProspectAttach(BaseModel):
    prospect_id: uuid.UUID


class CampaignMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    sequence_order: int
    message_type: MessageType
    channel: MessageChannel
    subject: str | None
    body: str | None
    delay_after_previous_hours: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CampaignProspectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    campaign_id: uuid.UUID
    prospect_id: uuid.UUID
    created_at: datetime
    instantly_lead_id: str | None = None
    sync_status: LeadSyncStatus = LeadSyncStatus.NOT_SYNCED
    outreach_status: OutreachStatus = OutreachStatus.PENDING
    last_synced_at: datetime | None = None
    last_activity_at: datetime | None = None
    last_reply_at: datetime | None = None
    assessment_status: AssessmentStatus = AssessmentStatus.NOT_STARTED
    cold_status: ColdStatus = ColdStatus.NOT_COLD
    assessment_token: str | None = None
    sync_error: str | None = None
    assessment_started_at: datetime | None = None
    assessment_completed_at: datetime | None = None
    assessment_url: str | None = None
    prospect: ProspectRead | None = None


class CampaignSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sender_name: str
    assessment_url: str | None
    status: CampaignStatus
    instantly_sync_status: InstantlySyncStatus = InstantlySyncStatus.NOT_SYNCED
    instantly_campaign_id: str | None = None
    instantly_runtime_status: InstantlyRuntimeStatus = InstantlyRuntimeStatus.NOT_STARTED
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    prospect_count: int = 0


class CampaignDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    sender_name: str
    assessment_url: str | None
    status: CampaignStatus
    send_timezone: str
    send_window_start: str
    send_window_end: str
    send_monday: bool
    send_tuesday: bool
    send_wednesday: bool
    send_thursday: bool
    send_friday: bool
    send_saturday: bool
    send_sunday: bool
    instantly_campaign_id: str | None = None
    instantly_sync_status: InstantlySyncStatus = InstantlySyncStatus.NOT_SYNCED
    instantly_last_synced_at: datetime | None = None
    instantly_sync_error: str | None = None
    instantly_email_list: list[str] = Field(default_factory=list)
    instantly_runtime_status: InstantlyRuntimeStatus = InstantlyRuntimeStatus.NOT_STARTED
    created_at: datetime
    updated_at: datetime
    messages: list[CampaignMessageRead] = Field(default_factory=list)
    prospects: list[CampaignProspectRead] = Field(default_factory=list)


class CampaignListResponse(BaseModel):
    items: list[CampaignSummary]
    total: int
    page: int
    page_size: int


class CampaignReadyError(BaseModel):
    missing: list[str]
