"""
Client and User Models
======================

Typed views over the control-plane rows. :class:`ClientSettings` is the §21
tenant configuration object; it round-trips through the ``clients.settings``
JSONB column so adding a setting does not need a migration, while still being a
typed object everywhere in Python.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.types import DeploymentType, ExecutionMode, RiskLevel, Role


@dataclass
class RiskPolicy:
    """Risk thresholds and action limits (§22, §23).

    ``auto_allowed_max_risk`` is the ceiling for unattended execution in
    AUTO_WITHIN_SCOPE. ``always_approve_at_or_above`` is a floor that no client
    configuration can lower below HIGH for CRITICAL actions — see
    :func:`normalize`.
    """

    auto_allowed_max_risk: str = RiskLevel.LOW.value
    always_approve_at_or_above: str = RiskLevel.HIGH.value
    max_records_per_action: int = 10
    #: Money the agent may move/commit unattended. 0 means "never unattended".
    max_financial_amount: float = 0.0
    max_emails_per_run: int = 5
    #: Empty list means "no domain restriction"; a non-empty list is an allowlist.
    approved_recipient_domains: list[str] = field(default_factory=list)
    #: CRM fields an AUTO-mode run may change without approval.
    auto_updatable_crm_fields: list[str] = field(
        default_factory=lambda: ["lifecycle", "tags", "owner", "title"]
    )
    #: Calendar types the agent may create unattended.
    permitted_calendar_types: list[str] = field(default_factory=lambda: ["internal"])

    def normalize(self) -> "RiskPolicy":
        """Clamp client-supplied values to what the platform will honor.

        A tenant may make itself *stricter* but not looser than the platform
        floor: CRITICAL actions always require a human (§22), so
        ``auto_allowed_max_risk`` can never be CRITICAL however the row is edited.
        """
        auto = RiskLevel.parse(self.auto_allowed_max_risk, RiskLevel.LOW)
        if auto == RiskLevel.CRITICAL:
            auto = RiskLevel.HIGH
        floor = RiskLevel.parse(self.always_approve_at_or_above, RiskLevel.HIGH)
        return replace(
            self,
            auto_allowed_max_risk=auto.value,
            always_approve_at_or_above=floor.value,
            max_records_per_action=max(0, int(self.max_records_per_action)),
            max_financial_amount=max(0.0, float(self.max_financial_amount)),
            max_emails_per_run=max(0, int(self.max_emails_per_run)),
        )


@dataclass
class ApprovalPolicy:
    """Who approves, and for how long a request stays open (§8)."""

    approver_role: str = Role.APPROVER.value
    expires_after_hours: int = 72
    #: Tools that always require approval regardless of risk or mode.
    always_require_approval: list[str] = field(default_factory=list)
    #: Whether the person who proposed an action may approve their own.
    #: Enabled by default so a single admin/operator demo account can exercise
    #: the full approve→execute path; turn off in production workspaces.
    allow_self_approval: bool = True


@dataclass
class MemoryPolicy:
    """How organizational memory is governed (§2, §3)."""

    #: AI-inferred memory lands as PENDING and is excluded from authoritative
    #: retrieval until a human approves it.
    require_approval_for_inferred: bool = True
    #: Inferred memory below this confidence is not written at all.
    min_confidence_to_store: float = 0.4
    #: Corrections may only be made by a user at or above this role.
    correction_min_role: str = Role.DRAFTER.value


@dataclass
class RetentionPolicy:
    """Data retention windows in days. 0 means keep indefinitely."""

    audit_days: int = 365
    action_days: int = 365
    superseded_memory_days: int = 0


@dataclass
class ClientSettings:
    """The §21 tenant configuration object."""

    client_id: str = ""
    company_name: str = ""
    deployment_type: str = DeploymentType.DEDICATED_DB.value
    enabled_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_DOMAINS))
    default_execution_mode: str = ExecutionMode.WAIT_FOR_APPROVAL.value
    #: PHI and payment-card handling are OFF unless the contract and deployment
    #: explicitly permit them (§14). Defaults are the safe values.
    allow_phi: bool = False
    allow_card_data: bool = False
    enabled_integrations: list[str] = field(default_factory=list)
    memory_policy: MemoryPolicy = field(default_factory=MemoryPolicy)
    retention_policy: RetentionPolicy = field(default_factory=RetentionPolicy)
    approval_policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    risk_policy: RiskPolicy = field(default_factory=RiskPolicy)
    #: Post-sale configuration snapshot (FB-044).
    onboarding: dict = field(default_factory=dict)
    #: Domains the client has paid for (FB-035). A domain outside this list can
    #: be enabled only through a recorded change order.
    purchased_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_DOMAINS))
    #: Per-tool execution mode (FB-038). Narrowing only: an entry can make one
    #: tool stricter than the workspace mode, never looser.
    tool_modes: dict = field(default_factory=dict)

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "ClientSettings":
        """Build from a JSONB row, tolerating missing and unknown keys.

        Unknown keys are dropped rather than raising: a row written by a newer
        version must not break an older process, and a row written by an older
        version must pick up new defaults.
        """
        data = dict(data or {})
        nested = {
            "memory_policy": MemoryPolicy,
            "retention_policy": RetentionPolicy,
            "approval_policy": ApprovalPolicy,
            "risk_policy": RiskPolicy,
        }
        kwargs: dict[str, Any] = {}
        known = {f for f in cls.__dataclass_fields__}
        for key, value in data.items():
            if key not in known:
                continue
            if key in nested and isinstance(value, dict):
                sub_cls = nested[key]
                sub_known = set(sub_cls.__dataclass_fields__)
                kwargs[key] = sub_cls(**{k: v for k, v in value.items() if k in sub_known})
            else:
                kwargs[key] = value
        settings = cls(**kwargs)
        settings.risk_policy = settings.risk_policy.normalize()
        settings.enabled_domains = [d for d in settings.enabled_domains if d in DOMAIN_NAMES]
        # The general pack is the memory surface; it is always on.
        if "general" not in settings.enabled_domains:
            settings.enabled_domains.insert(0, "general")
        settings.purchased_domains = [d for d in settings.purchased_domains if d in DOMAIN_NAMES]
        if "general" not in settings.purchased_domains:
            settings.purchased_domains.insert(0, "general")
        settings.tool_modes = {
            str(k): str(v) for k, v in dict(settings.tool_modes or {}).items()
            if ExecutionMode.parse(v) is not None
        }
        return settings

    # -- typed accessors ----------------------------------------------------

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.parse(self.default_execution_mode, ExecutionMode.WAIT_FOR_APPROVAL)  # type: ignore[return-value]

    @property
    def auto_max_risk(self) -> RiskLevel:
        return RiskLevel.parse(self.risk_policy.auto_allowed_max_risk, RiskLevel.LOW)  # type: ignore[return-value]

    @property
    def approval_floor(self) -> RiskLevel:
        return RiskLevel.parse(self.risk_policy.always_approve_at_or_above, RiskLevel.HIGH)  # type: ignore[return-value]

    def tool_mode(self, tool: str) -> Optional[ExecutionMode]:
        raw = (self.tool_modes or {}).get(tool)
        return ExecutionMode.parse(raw) if raw else None  # type: ignore[return-value]

    def domain_purchased(self, domain: str) -> bool:
        name = (domain or "").strip().casefold()
        return name == "general" or name in self.purchased_domains

    def domain_enabled(self, domain: str) -> bool:
        return (domain or "").strip().casefold() in self.enabled_domains


@dataclass
class Client:
    """A provisioned customer workspace."""

    id: str
    slug: str
    company_name: str
    deployment_type: DeploymentType
    db_name: str
    db_schema: str
    status: str
    settings: ClientSettings
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "company_name": self.company_name,
            "deployment_type": str(self.deployment_type),
            "db_name": self.db_name,
            "db_schema": self.db_schema,
            "status": self.status,
            "settings": self.settings.to_dict(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class User:
    """A person who can sign in to exactly one client workspace."""

    id: str
    client_id: str
    email: str
    display_name: str
    roles: frozenset[Role]
    status: str = "ACTIVE"
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    data_scopes: frozenset[str] = frozenset()

    @property
    def primary_role(self) -> Role:
        from bizos.types import role_rank

        return max(self.roles, key=role_rank) if self.roles else Role.VIEWER

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "client_id": self.client_id,
            "email": self.email,
            "display_name": self.display_name,
            "roles": sorted(str(r) for r in self.roles),
            "primary_role": str(self.primary_role),
            "data_scopes": sorted(self.data_scopes),
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }
