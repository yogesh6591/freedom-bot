"""Encrypt/decrypt integration credentials at rest with Fernet."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from acquisition.config import Settings, get_settings


class EncryptionError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class CredentialEncryptionService:
    """Application-level encryption for provider credentials.

    The Fernet master key lives only in INTEGRATION_ENCRYPTION_KEY.
    Plaintext secrets must never be logged or returned by this service's callers.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def encrypt_mapping(self, payload: dict[str, Any]) -> str:
        token = self._fernet().encrypt(json.dumps(payload).encode("utf-8"))
        return token.decode("utf-8")

    def decrypt_mapping(self, encrypted: str) -> dict[str, Any]:
        try:
            raw = self._fernet().decrypt(encrypted.encode("utf-8"))
        except InvalidToken as exc:
            raise EncryptionError("Unable to decrypt integration credentials") from exc
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise EncryptionError("Decrypted credentials payload is invalid")
        return data

    def _fernet(self) -> Fernet:
        key = (self.settings.integration_encryption_key or "").strip()
        if not key:
            raise EncryptionError("INTEGRATION_ENCRYPTION_KEY is not configured")
        try:
            return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except Exception as exc:  # noqa: BLE001 — normalize cryptography errors
            raise EncryptionError("INTEGRATION_ENCRYPTION_KEY is invalid") from exc
