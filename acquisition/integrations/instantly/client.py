"""Instantly API V2 HTTP client.

All Instantly HTTP access must go through this module.
Authentication: Authorization: Bearer <api_key>
Docs: https://developer.instantly.ai/api-reference/campaign/create-campaign
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from acquisition.integrations.instantly.errors import (
    InstantlyAuthError,
    InstantlyConfigError,
    InstantlyProviderError,
    InstantlyTimeoutError,
)
from acquisition.integrations.instantly.redaction import redact_headers, redact_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InstantlyConnectionResult:
    connected: bool
    message: str
    category: str = "ok"


class InstantlyClient:
    """Thin Instantly API V2 client."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.instantly.ai/api/v2",
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))
        self._transport = transport

    def check_connection(self) -> InstantlyConnectionResult:
        """Read-only auth/connectivity probe via GET /campaigns?limit=1."""
        response = self._request("GET", "/campaigns", params={"limit": 1})
        return self._interpret_connection_response(response)

    def list_campaigns(self, *, limit: int = 100, search: str | None = None) -> list[dict[str, Any]]:
        """GET /campaigns — list Instantly campaigns (for idempotent sync lookup)."""
        params: dict[str, Any] = {"limit": limit}
        if search:
            params["search"] = search
        response = self._request("GET", "/campaigns", params=params)
        data = response.json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def find_campaign_id_by_name(self, name: str) -> str | None:
        """Return the first Instantly campaign id whose name matches exactly."""
        wanted = (name or "").strip()
        if not wanted:
            return None
        # Search narrows candidates; exact match is enforced below.
        for item in self.list_campaigns(limit=100, search=wanted):
            remote_name = str(item.get("name") or "").strip()
            remote_id = str(item.get("id") or "").strip()
            if remote_name == wanted and remote_id:
                return remote_id
        return None

    def create_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /campaigns — create Instantly campaign definition + sequence."""
        response = self._request("POST", "/campaigns", json=payload)
        return self._parse_json_object(response, action="create campaign")

    def update_campaign(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH /campaigns/{id} — update existing Instantly campaign."""
        safe_id = (campaign_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly campaign id is required for update")
        response = self._request("PATCH", f"/campaigns/{safe_id}", json=payload)
        return self._parse_json_object(response, action="update campaign")

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        """GET /campaigns/{id}."""
        safe_id = (campaign_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly campaign id is required")
        response = self._request("GET", f"/campaigns/{safe_id}")
        return self._parse_json_object(response, action="get campaign")

    def activate_campaign(self, campaign_id: str) -> dict[str, Any]:
        """POST /campaigns/{id}/activate."""
        safe_id = (campaign_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly campaign id is required for activate")
        response = self._request("POST", f"/campaigns/{safe_id}/activate")
        if response.content:
            return self._parse_json_object(response, action="activate campaign")
        return {"id": safe_id, "status": "activated"}

    def pause_campaign(self, campaign_id: str) -> dict[str, Any]:
        """POST /campaigns/{id}/pause."""
        safe_id = (campaign_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly campaign id is required for pause")
        response = self._request("POST", f"/campaigns/{safe_id}/pause")
        if response.content:
            return self._parse_json_object(response, action="pause campaign")
        return {"id": safe_id, "status": "paused"}

    def delete_campaign(self, campaign_id: str) -> dict[str, Any]:
        """DELETE /campaigns/{id}.

        404 is treated as success (already removed remotely).
        """
        safe_id = (campaign_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly campaign id is required for delete")
        try:
            response = self._request("DELETE", f"/campaigns/{safe_id}")
        except InstantlyProviderError as exc:
            if getattr(exc, "status_code", None) == 404:
                return {"id": safe_id, "status": "already_deleted"}
            raise
        if response.content:
            try:
                return self._parse_json_object(response, action="delete campaign")
            except InstantlyProviderError:
                return {"id": safe_id, "status": "deleted"}
        return {"id": safe_id, "status": "deleted"}

    def add_campaign_variables(self, campaign_id: str, variables: list[str]) -> dict[str, Any]:
        """POST /campaigns/{id}/variables."""
        safe_id = (campaign_id or "").strip()
        response = self._request(
            "POST",
            f"/campaigns/{safe_id}/variables",
            json={"variables": variables},
        )
        if response.content:
            return self._parse_json_object(response, action="add campaign variables")
        return {"ok": True}

    def list_accounts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """GET /accounts."""
        response = self._request("GET", "/accounts", params={"limit": limit})
        data = response.json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def create_lead(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /leads."""
        response = self._request("POST", "/leads", json=payload)
        return self._parse_json_object(response, action="create lead")

    def update_lead(self, lead_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """PATCH /leads/{id}."""
        safe_id = (lead_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly lead id is required for update")
        response = self._request("PATCH", f"/leads/{safe_id}", json=payload)
        return self._parse_json_object(response, action="update lead")

    def list_leads(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /leads/list."""
        response = self._request("POST", "/leads/list", json=payload)
        data = response.json()
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"items": data}
        return {"items": []}

    def update_lead_interest_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /leads/update-interest-status — stop/label a lead without deleting."""
        response = self._request("POST", "/leads/update-interest-status", json=payload)
        if response.content:
            try:
                data = response.json()
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
        return {"ok": True}

    def reply_to_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /emails/reply."""
        response = self._request("POST", "/emails/reply", json=payload)
        return self._parse_json_object(response, action="reply to email")

    def list_emails(
        self,
        *,
        limit: int = 50,
        starting_after: str | None = None,
        email_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /emails — polling fallback for replies."""
        params: dict[str, Any] = {"limit": limit}
        if starting_after:
            params["starting_after"] = starting_after
        if email_type:
            params["email_type"] = email_type
        response = self._request("GET", "/emails", params=params)
        data = response.json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def create_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /webhooks."""
        response = self._request("POST", "/webhooks", json=payload)
        return self._parse_json_object(response, action="create webhook")

    def list_webhooks(self) -> list[dict[str, Any]]:
        """GET /webhooks."""
        response = self._request("GET", "/webhooks")
        data = response.json()
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            items = data.get("items") or data.get("data") or []
            return [item for item in items if isinstance(item, dict)]
        return []

    def test_webhook(self, webhook_id: str) -> dict[str, Any]:
        """POST /webhooks/{id}/test — ask Instantly to deliver a test payload."""
        safe_id = (webhook_id or "").strip()
        if not safe_id:
            raise InstantlyConfigError("Instantly webhook id is required for test")
        response = self._request("POST", f"/webhooks/{safe_id}/test")
        if response.content:
            try:
                data = response.json()
                if isinstance(data, dict):
                    return data
            except ValueError:
                pass
        return {"ok": True, "id": safe_id}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if not self._api_key:
            raise InstantlyConfigError("Instantly API key is not configured")
        if not self._base_url:
            raise InstantlyConfigError("Instantly base URL is not configured")

        try:
            with self._build_client() as client:
                response = client.request(method, path, params=params, json=json)
        except httpx.TimeoutException as exc:
            logger.warning("Instantly %s %s timed out: %s", method, path, type(exc).__name__)
            raise InstantlyTimeoutError("Instantly request timed out") from exc
        except httpx.HTTPError as exc:
            safe = redact_secrets(str(exc), api_key=self._api_key)
            logger.warning("Instantly %s %s network error: %s", method, path, safe)
            raise InstantlyProviderError("Instantly network error") from exc

        self._raise_for_status(response, action=f"{method} {path}")
        return response

    def _build_client(self) -> httpx.Client:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        kwargs: dict[str, Any] = {
            "base_url": self._base_url,
            "headers": headers,
            "timeout": self._timeout,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _interpret_connection_response(self, response: httpx.Response) -> InstantlyConnectionResult:
        status = response.status_code
        logger.info(
            "Instantly connection check completed status=%s headers=%s",
            status,
            redact_headers(dict(response.request.headers)),
        )
        # _request already raises for error statuses; 2xx means success.
        return InstantlyConnectionResult(
            connected=True,
            message="",
            category="ok",
        )

    def _raise_for_status(self, response: httpx.Response, *, action: str) -> None:
        status = response.status_code
        if status in {401, 403}:
            raise InstantlyAuthError("Instantly authentication failed")
        if 500 <= status <= 599:
            raise InstantlyProviderError("Instantly provider error", status_code=status)
        if status >= 400:
            detail = self._safe_error_detail(response)
            raise InstantlyProviderError(
                f"Instantly request failed during {action}: {detail}" if detail else "Instantly request failed",
                status_code=status,
            )

    def _parse_json_object(self, response: httpx.Response, *, action: str) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise InstantlyProviderError(
                f"Instantly returned invalid JSON during {action}",
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise InstantlyProviderError(
                f"Instantly returned unexpected payload during {action}",
                status_code=response.status_code,
            )
        return data

    def _safe_error_detail(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError:
            return ""
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str):
                return redact_secrets(message, api_key=self._api_key)[:300]
        return ""
