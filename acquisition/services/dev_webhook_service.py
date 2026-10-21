"""DEVELOPMENT helpers for Cloudflare Tunnel + Instantly webhook registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from acquisition.config import Settings, get_settings
from acquisition.integrations.instantly.client import InstantlyClient
from acquisition.integrations.instantly.errors import InstantlyError
from acquisition.services.instantly_service import InstantlyService

logger = logging.getLogger(__name__)

_TUNNEL_URL_CANDIDATES = (
    Path("/tunnel/url.txt"),
    Path("/app/tunnel/url.txt"),
)


class DevWebhookError(Exception):
    def __init__(self, message: str, *, http_status: int = 422) -> None:
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DevWebhookStatus:
    environment: str
    public_webhook_base_url: str
    webhook_url: str
    tunnel_url_file: str
    tunnel_status: str
    tunnel_detail: str
    webhook_registration_status: str
    webhook_registration_detail: str
    registered_webhook_id: str | None
    can_register: bool
    can_test: bool


class DevWebhookService:
    """Dev-only Instantly webhook + tunnel status (no campaign/lead changes)."""

    def __init__(
        self,
        db: Session,
        *,
        settings: Settings | None = None,
        instantly_service: InstantlyService | None = None,
        client: InstantlyClient | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.instantly_service = instantly_service or InstantlyService(db=db)
        self._client = client

    def webhook_base_url(self) -> str:
        configured = (self.settings.public_webhook_base_url or "").strip().rstrip("/")
        if configured:
            return configured
        discovered = self._read_tunnel_url_file()
        if discovered:
            return discovered
        return (self.settings.public_base_url or "").strip().rstrip("/")

    def webhook_url(self) -> str:
        base = self.webhook_base_url()
        if not base:
            return ""
        return f"{base}/api/webhooks/instantly"

    def status(self) -> DevWebhookStatus:
        env = (self.settings.environment or "development").strip().lower()
        base = self.webhook_base_url()
        webhook_url = self.webhook_url()
        tunnel_file = self._read_tunnel_url_file()
        tunnel_status, tunnel_detail = self._probe_tunnel(base)

        reg_status = "unknown"
        reg_detail = "Instantly not connected"
        webhook_id: str | None = None
        can_register = False
        can_test = False

        try:
            client = self._resolve_client()
            can_register = self._can_register_webhook(webhook_url)
            match = self._find_registered_webhook(client, webhook_url)
            if match:
                webhook_id = str(match.get("id") or "").strip() or None
                reg_status = "registered"
                reg_detail = "Matching Instantly webhook found for this URL."
                can_test = bool(webhook_id)
            elif webhook_url:
                reg_status = "not_registered"
                reg_detail = "No Instantly webhook points at this URL yet."
            else:
                reg_status = "missing_url"
                reg_detail = "Set PUBLIC_WEBHOOK_BASE_URL (or start the tunnel)."
        except InstantlyError as exc:
            reg_status = "error"
            reg_detail = str(exc.message if hasattr(exc, "message") else exc)
        except Exception as exc:  # noqa: BLE001
            reg_status = "error"
            reg_detail = "Could not list Instantly webhooks"

        if env not in {"development", "dev", "local"}:
            tunnel_detail = "Using PUBLIC_WEBHOOK_BASE_URL for Instantly webhooks."
        elif tunnel_file and not (self.settings.public_webhook_base_url or "").strip():
            tunnel_detail = f"{tunnel_detail} (from /tunnel/url.txt)"

        return DevWebhookStatus(
            environment=env,
            public_webhook_base_url=base,
            webhook_url=webhook_url,
            tunnel_url_file=tunnel_file or "",
            tunnel_status=tunnel_status,
            tunnel_detail=tunnel_detail,
            webhook_registration_status=reg_status,
            webhook_registration_detail=reg_detail,
            registered_webhook_id=webhook_id,
            can_register=can_register and reg_status in {"not_registered", "error", "unknown"},
            can_test=can_test,
        )

    def register_webhook(self) -> dict[str, Any]:
        webhook_url = self.webhook_url()
        if not self._can_register_webhook(webhook_url):
            raise DevWebhookError(
                "PUBLIC_WEBHOOK_BASE_URL must be a public https:// URL before registering."
            )

        client = self._resolve_client()
        existing = self._find_registered_webhook(client, webhook_url)
        if existing:
            return {
                "created": False,
                "id": existing.get("id"),
                "target_hook_url": existing.get("target_hook_url"),
                "message": "Webhook already registered for this URL",
            }

        payload: dict[str, Any] = {
            "name": "FreedomBot Acquisition",
            "target_hook_url": webhook_url,
            "event_type": "all_events",
        }
        secret = (self.settings.instantly_webhook_secret or "").strip()
        if secret:
            payload["headers"] = {"Authorization": f"Bearer {secret}"}

        created = client.create_webhook(payload)
        return {
            "created": True,
            "id": created.get("id"),
            "target_hook_url": created.get("target_hook_url") or webhook_url,
            "message": "Webhook registered in Instantly",
        }

    def test_webhook(self) -> dict[str, Any]:
        """Safe connectivity checks: public /health + Instantly webhook test if registered."""
        status = self.status()
        results: dict[str, Any] = {
            "health_ok": False,
            "health_status": None,
            "instantly_test": None,
            "webhook_url": status.webhook_url,
        }

        base = status.public_webhook_base_url
        if base:
            try:
                with httpx.Client(timeout=15.0, follow_redirects=True) as http:
                    response = http.get(f"{base}/health")
                results["health_status"] = response.status_code
                results["health_ok"] = response.status_code == 200
            except httpx.HTTPError as exc:
                results["health_error"] = type(exc).__name__

        if status.registered_webhook_id:
            try:
                client = self._resolve_client()
                results["instantly_test"] = client.test_webhook(status.registered_webhook_id)
            except InstantlyError as exc:
                results["instantly_test_error"] = str(
                    getattr(exc, "message", None) or exc
                )

        return results

    def _can_register_webhook(self, webhook_url: str) -> bool:
        """Allow Instantly registration for any public https URL (prod acq or tunnel)."""
        cleaned = (webhook_url or "").strip()
        if not cleaned.startswith("https://"):
            return False
        host = (urlparse(cleaned).hostname or "").lower()
        if not host or host in {"localhost", "127.0.0.1", "0.0.0.0"}:
            return False
        if host.endswith(".local"):
            return False
        return True

    def _resolve_client(self) -> InstantlyClient:
        if self._client is not None:
            return self._client
        return self.instantly_service.get_api_client()

    def _read_tunnel_url_file(self) -> str | None:
        for path in _TUNNEL_URL_CANDIDATES:
            try:
                if path.is_file():
                    value = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
                    if value.startswith("https://"):
                        return value.rstrip("/")
            except OSError:
                continue
        return None

    def _probe_tunnel(self, base_url: str) -> tuple[str, str]:
        if not base_url:
            return "down", "No PUBLIC_WEBHOOK_BASE_URL and no /tunnel/url.txt yet."
        parsed = urlparse(base_url)
        if parsed.scheme != "https":
            return "invalid", "Webhook base URL must be https for Instantly."
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as http:
                response = http.get(f"{base_url.rstrip('/')}/health")
            if response.status_code == 200:
                return "up", "Public /health returned 200."
            return "degraded", f"Public /health returned HTTP {response.status_code}."
        except httpx.HTTPError:
            return "down", "Could not reach public /health through the tunnel."

    def _find_registered_webhook(
        self, client: InstantlyClient, webhook_url: str
    ) -> dict[str, Any] | None:
        wanted = (webhook_url or "").strip().rstrip("/")
        if not wanted:
            return None
        for item in client.list_webhooks():
            remote = str(item.get("target_hook_url") or "").strip().rstrip("/")
            if remote == wanted:
                return item
        return None
