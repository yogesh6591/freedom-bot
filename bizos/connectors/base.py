"""
Connector Interface
===================

A connector is the boundary between the platform and one business system. It
exposes **narrow capabilities** (§19) — ``search_contacts``, ``create_note``,
``send_message`` — never a general request method, so a permission and an audit
record can bind to exactly one verb.

Every read that returns free text passes the text through
:mod:`bizos.connectors.untrusted` before it leaves the connector, so no call site
can forget to do it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from bizos.tenancy.context import TenantContext


@dataclass
class ConnectorResult:
    """The outcome of one connector call."""

    ok: bool
    data: Any = None
    #: Human-readable summary for the agent and for the audit record.
    summary: str = ""
    error: Optional[str] = None
    #: Count of neutralized injection attempts in returned content.
    untrusted_findings: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self.data,
            "summary": self.summary,
            "error": self.error,
            "untrusted_findings": self.untrusted_findings,
            "metadata": self.metadata,
        }

    @classmethod
    def failure(cls, error: str) -> "ConnectorResult":
        return cls(ok=False, error=error, summary=error)


class ConnectorError(RuntimeError):
    """A connector could not complete a call."""


class ConnectorNotConfigured(ConnectorError):
    """The integration exists in the registry but has no working configuration."""


class Connector(ABC):
    """Base class for every business-system adapter."""

    #: Integration category: "crm", "email", "calendar", "accounting".
    category: str = ""
    #: Provider id as stored in the ``integrations`` table.
    provider: str = ""

    def __init__(self, ctx: TenantContext, config: Optional[dict[str, Any]] = None) -> None:
        self.ctx = ctx
        self.config = config or {}

    @abstractmethod
    def health(self) -> ConnectorResult:
        """Whether this connector can currently reach its system."""

    def describe(self) -> dict[str, Any]:
        return {"category": self.category, "provider": self.provider}
