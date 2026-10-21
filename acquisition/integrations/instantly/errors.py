"""Instantly integration errors (never include secrets)."""

from __future__ import annotations


class InstantlyError(Exception):
    """Base Instantly integration error."""

    def __init__(self, message: str, *, category: str = "instantly_error") -> None:
        self.message = message
        self.category = category
        super().__init__(message)


class InstantlyConfigError(InstantlyError):
    def __init__(self, message: str = "Instantly API key is not configured") -> None:
        super().__init__(message, category="config_error")


class InstantlyAuthError(InstantlyError):
    def __init__(self, message: str = "Instantly authentication failed") -> None:
        super().__init__(message, category="auth_error")


class InstantlyTimeoutError(InstantlyError):
    def __init__(self, message: str = "Instantly request timed out") -> None:
        super().__init__(message, category="timeout")


class InstantlyProviderError(InstantlyError):
    def __init__(
        self,
        message: str = "Instantly provider error",
        *,
        status_code: int | None = None,
    ) -> None:
        self.status_code = status_code
        super().__init__(message, category="provider_error")
