"""
First-client onboarding / configuration (FB-044)
================================================

Guided post-sale setup: goals, systems, SOPs, access/autonomy, and optional
assessment payload. Applying a profile writes org memory and updates client
settings (domains + execution mode).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.memory import store as memory
from bizos.tenancy.context import TenantContext
from bizos.types import ExecutionMode, MemoryCategory, SourceType
from bizos.util.timeutil import utcnow


@dataclass
class OnboardingProfile:
    """What was sold / discovered, applied into the workspace."""

    goals: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    sops: list[dict[str, str]] = field(default_factory=list)  # {title, content, key?}
    roles_notes: str = ""
    access_notes: str = ""
    autonomy_mode: str = ExecutionMode.WAIT_FOR_APPROVAL.value
    enabled_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_DOMAINS))
    review_points: list[str] = field(default_factory=list)
    assessment: dict[str, Any] = field(default_factory=dict)
    configured_at: Optional[str] = None
    configured_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "OnboardingProfile":
        data = dict(data or {})
        known = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in known}
        profile = cls(**kwargs)
        profile.enabled_domains = [
            d for d in (profile.enabled_domains or list(DEFAULT_ENABLED_DOMAINS)) if d in DOMAIN_NAMES
        ]
        if "general" not in profile.enabled_domains:
            profile.enabled_domains.insert(0, "general")
        mode = ExecutionMode.parse(profile.autonomy_mode, ExecutionMode.WAIT_FOR_APPROVAL)
        profile.autonomy_mode = str(mode)
        return profile


def get_profile(settings: ClientSettings) -> OnboardingProfile:
    raw = getattr(settings, "onboarding", None)
    if isinstance(raw, OnboardingProfile):
        return raw
    if isinstance(raw, dict):
        return OnboardingProfile.from_dict(raw)
    return OnboardingProfile()


def apply_profile(
    *,
    client_id: str,
    profile: OnboardingProfile,
    settings: ClientSettings,
    ctx: TenantContext,
) -> dict[str, Any]:
    """Persist onboarding into settings + organizational memory."""
    profile = OnboardingProfile.from_dict(profile.to_dict())
    profile.configured_at = utcnow().isoformat()
    profile.configured_by = ctx.user_id

    written: list[dict[str, str]] = []

    for index, goal in enumerate(profile.goals):
        text = (goal or "").strip()
        if not text:
            continue
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key=f"goal_{index + 1}",
            title=f"Goal {index + 1}",
            content=text,
            tags=["strategy", "goal", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "goal", "id": item.id, "title": item.title})

    for index, system in enumerate(profile.systems):
        text = (system or "").strip()
        if not text:
            continue
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key=f"system_{index + 1}",
            title=f"Connected system: {text}",
            content=f"Client uses {text} as a connected business system.",
            tags=["systems", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "system", "id": item.id, "title": item.title})

    for sop in profile.sops:
        title = str(sop.get("title") or "").strip()
        content = str(sop.get("content") or "").strip()
        if not title or not content:
            continue
        key = str(sop.get("key") or "").strip() or None
        item = memory.put(
            category=MemoryCategory.SOP,
            memory_key=key,
            title=title,
            content=content,
            tags=["sop", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "sop", "id": item.id, "title": item.title})

    if profile.roles_notes.strip():
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="roles_notes",
            title="Roles and access notes",
            content=profile.roles_notes.strip(),
            tags=["access", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "roles", "id": item.id, "title": item.title})

    if profile.review_points:
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="review_points",
            title="Human review points",
            content="\n".join(f"- {p}" for p in profile.review_points if p),
            tags=["governance", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "review_points", "id": item.id, "title": item.title})

    if profile.assessment:
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="assessment_snapshot",
            title="Assessment handoff snapshot",
            content=str(profile.assessment),
            tags=["assessment", "onboarding"],
            source_type=SourceType.MANUAL,
            attributes={"assessment": profile.assessment},
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "assessment", "id": item.id, "title": item.title})

    payload = settings.to_dict()
    payload["onboarding"] = {**(settings.onboarding or {}), **profile.to_dict()}
    # Only purchased domains can be switched on; the rest wait for a change order.
    payload["enabled_domains"] = [d for d in profile.enabled_domains if settings.domain_purchased(d)]
    payload["default_execution_mode"] = profile.autonomy_mode
    updated = control.update_client_settings(
        client_id,
        ClientSettings.from_dict(payload),
        updated_by=ctx.user_id,
    )
    return {
        "profile": profile.to_dict(),
        "settings": updated.settings.to_dict(),
        "memory_written": written,
    }
