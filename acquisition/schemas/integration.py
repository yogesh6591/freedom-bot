"""Schemas for Instantly admin configuration (never include decrypted secrets)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from acquisition.models.integration import ConnectionStatus


class InstantlyConfigCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str = Field(default="Cepoch Development", min_length=1, max_length=255)
    api_key: str = Field(..., min_length=1)


class InstantlyConfigUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = None
    replace_api_key: bool = False

    @field_validator("api_key", mode="before")
    @classmethod
    def blank_api_key_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_replace_rules(self) -> InstantlyConfigUpdate:
        if self.replace_api_key and not self.api_key:
            raise ValueError("api_key is required when replace_api_key is true")
        if self.api_key and not self.replace_api_key:
            raise ValueError("Set replace_api_key=true to replace the Instantly API key")
        return self


class InstantlyConfigSummary(BaseModel):
    """Safe public view — never includes credential material."""

    id: uuid.UUID
    provider: str = "instantly"
    display_name: str
    api_key_configured: bool
    connection_status: ConnectionStatus
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InstantlyStatusPayload(BaseModel):
    connected: bool
    message: str
    display_name: str | None = None
    connection_status: ConnectionStatus | None = None
    api_key_configured: bool = False
    last_checked_at: datetime | None = None
