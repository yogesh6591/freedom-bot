"""
n8n webhook connector (FB-040)
==============================

Thin outbound integration: the agent does not hold vendor credentials. After
policy/approval, we POST a JSON payload to the client's n8n webhook URL.

When no webhook URL is configured, deliveries are recorded locally so demos and
tests still exercise the approve→execute path.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib import error, request

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorResult
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id


class N8nWebhookConnector(Connector):
    category = "n8n"
    provider = "webhook"

    def health(self) -> ConnectorResult:
        url = (self.config or {}).get("webhook_url") or ""
        with readonly_connection(self.ctx) as conn:
            count = conn.execute(text("SELECT count(*) FROM n8n_deliveries")).scalar()
        if not url:
            return ConnectorResult(
                ok=True,
                data={"mode": "local", "deliveries": count},
                summary=f"n8n local mode ({count} deliveries); set webhook_url to call a real workflow",
            )
        return ConnectorResult(
            ok=True,
            data={"mode": "webhook", "webhook_url": url, "deliveries": count},
            summary=f"n8n webhook configured ({count} prior deliveries)",
        )

    def trigger(
        self,
        *,
        event: str,
        payload: dict[str, Any],
        workflow: str = "",
    ) -> ConnectorResult:
        """Deliver one event to n8n (or the local delivery log)."""
        url = str((self.config or {}).get("webhook_url") or "").strip()
        body = {
            "event": event,
            "workflow": workflow or event,
            "client_id": self.ctx.client_id,
            "client_slug": self.ctx.client_slug,
            "payload": payload,
        }
        delivery_id = new_id("n8n")
        status = "queued"
        response_text = ""
        error_text = ""

        if url:
            try:
                data = json.dumps(body, default=str).encode("utf-8")
                req = request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "bizos-n8n/1"},
                    method="POST",
                )
                with request.urlopen(req, timeout=15) as resp:
                    response_text = resp.read(4000).decode("utf-8", errors="replace")
                    status = "delivered" if 200 <= resp.status < 300 else f"http_{resp.status}"
            except error.HTTPError as exc:
                status = f"http_{exc.code}"
                error_text = str(exc)
                response_text = exc.read(2000).decode("utf-8", errors="replace") if exc.fp else ""
            except Exception as exc:
                status = "failed"
                error_text = str(exc)
        else:
            status = "local"

        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO n8n_deliveries "
                    "(id, event, workflow, status, request, response, error, created_by) "
                    "VALUES (:i, :e, :w, :s, CAST(:req AS JSONB), :resp, :err, :u)"
                ),
                {
                    "i": delivery_id,
                    "e": event,
                    "w": workflow or event,
                    "s": status,
                    "req": json.dumps(body, default=str),
                    "resp": response_text[:4000],
                    "err": error_text[:1000] or None,
                    "u": self.ctx.user_id,
                },
            )

        ok = status in {"delivered", "local"}
        return ConnectorResult(
            ok=ok,
            data={"delivery_id": delivery_id, "status": status, "event": event},
            summary=(
                f"n8n delivery {delivery_id} ({status})"
                + (f": {error_text}" if error_text else "")
            ),
            error=error_text or None,
        )
