"""Prospect Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from acquisition.models.prospect import AcquisitionStatus, ApprovalStatus


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


class ProspectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company: str = Field(..., min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    linkedin_url: str | None = Field(default=None, max_length=512)
    executive_name: str = Field(..., min_length=1, max_length=255)
    executive_title: str | None = Field(default=None, max_length=255)
    email: EmailStr
    revenue_band: str | None = Field(default=None, max_length=100)
    headcount_band: str | None = Field(default=None, max_length=100)
    fit_note: str | None = None
    source: str | None = Field(default=None, max_length=100)
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    # Optional autonomous-acquisition fields (manual create / future discovery).
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=255)
    company_description: str | None = None
    authority_type: str | None = Field(default=None, max_length=64)
    is_founder_or_equivalent: bool | None = None
    email_confidence: str | None = Field(default=None, max_length=32)
    company_phone: str | None = Field(default=None, max_length=64)
    other_contact_path: str | None = Field(default=None, max_length=512)
    research_notes: str | None = None
    public_signals: list[Any] | dict[str, Any] | None = None
    acquisition_status: AcquisitionStatus | None = None

    @field_validator("website", "linkedin_url")
    @classmethod
    def validate_optional_urls(cls, value: str | None) -> str | None:
        return _validate_optional_http_url(value)

    @field_validator(
        "executive_title",
        "revenue_band",
        "headcount_band",
        "fit_note",
        "source",
        "city",
        "state",
        "region",
        "industry",
        "company_description",
        "authority_type",
        "email_confidence",
        "company_phone",
        "other_contact_path",
        "research_notes",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_optional_str(value)
        return value


class ProspectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company: str | None = Field(default=None, min_length=1, max_length=255)
    website: str | None = Field(default=None, max_length=512)
    linkedin_url: str | None = Field(default=None, max_length=512)
    executive_name: str | None = Field(default=None, min_length=1, max_length=255)
    executive_title: str | None = Field(default=None, max_length=255)
    email: EmailStr | None = None
    revenue_band: str | None = Field(default=None, max_length=100)
    headcount_band: str | None = Field(default=None, max_length=100)
    fit_note: str | None = None
    source: str | None = Field(default=None, max_length=100)
    approval_status: ApprovalStatus | None = None
    city: str | None = Field(default=None, max_length=128)
    state: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=128)
    industry: str | None = Field(default=None, max_length=255)
    company_description: str | None = None
    authority_type: str | None = Field(default=None, max_length=64)
    is_founder_or_equivalent: bool | None = None
    email_confidence: str | None = Field(default=None, max_length=32)
    company_phone: str | None = Field(default=None, max_length=64)
    other_contact_path: str | None = Field(default=None, max_length=512)
    research_notes: str | None = None
    public_signals: list[Any] | dict[str, Any] | None = None
    acquisition_status: AcquisitionStatus | None = None

    @field_validator("website", "linkedin_url")
    @classmethod
    def validate_optional_urls(cls, value: str | None) -> str | None:
        return _validate_optional_http_url(value)

    @field_validator(
        "executive_title",
        "revenue_band",
        "headcount_band",
        "fit_note",
        "source",
        "city",
        "state",
        "region",
        "industry",
        "company_description",
        "authority_type",
        "email_confidence",
        "company_phone",
        "other_contact_path",
        "research_notes",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            return _normalize_optional_str(value)
        return value


class ProspectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company: str
    website: str | None
    domain: str | None = None
    city: str | None = None
    state: str | None = None
    region: str | None = None
    industry: str | None = None
    company_description: str | None = None
    linkedin_url: str | None
    executive_name: str
    executive_title: str | None
    authority_type: str | None = None
    is_founder_or_equivalent: bool | None = None
    email: str
    email_confidence: str | None = None
    company_phone: str | None = None
    other_contact_path: str | None = None
    revenue_band: str | None
    headcount_band: str | None
    fit_note: str | None
    research_notes: str | None = None
    public_signals: list[Any] | dict[str, Any] | None = None
    fit_score: float | None = None
    fit_reasons: list[Any] | None = None
    timing_score: float | None = None
    timing_reasons: list[Any] | None = None
    reachability_score: float | None = None
    reachability_reasons: list[Any] | None = None
    priority_score: float | None = None
    priority_reasons: list[Any] | None = None
    score_confidence: float | None = None
    scored_at: datetime | None = None
    source: str | None
    approval_status: ApprovalStatus
    acquisition_status: AcquisitionStatus | None = None
    discovered_at: datetime | None = None
    last_researched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProspectListResponse(BaseModel):
    items: list[ProspectRead]
    total: int
    page: int
    page_size: int
