"""
FreedomBot Template
===================

FB-033: every client runs a copy of one template bot. The template holds what
is the same for every company — persona, default mode, the base domain package,
recommended per-tool modes, retention, starter SOPs. Provisioning clones it and
applies that client's overrides; nothing a client configures flows back into the
template or into another client.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

from bizos.control.models import ClientSettings
from bizos.domains.catalog import DOMAIN_NAMES
from bizos.tenancy.context import TenantContext, tenant_scope
from bizos.types import ExecutionMode, MemoryCategory, SourceType

TEMPLATE_PATH = Path(__file__).parent / "templates" / "freedombot.json"


def load_template(path: Optional[Path] = None) -> dict[str, Any]:
    """The template bot definition. Returned as a fresh copy each call."""
    with open(path or TEMPLATE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def recommended_tool_modes() -> dict[str, str]:
    """FB-038 per-tool mode assignment.

    Reads and drafts run on their own; every other write — email sends, CRM
    record changes, anything that moves money — waits for approval. Entries only ever narrow the
    workspace mode, so a client in ADVISE stays in ADVISE.
    """
    from bizos.rbac.registry import TOOL_SPECS

    modes: dict[str, str] = {}
    for spec in TOOL_SPECS:
        if not spec.write or spec.draft_safe:
            modes[spec.name] = ExecutionMode.AUTO_WITHIN_SCOPE.value
        else:
            modes[spec.name] = ExecutionMode.WAIT_FOR_APPROVAL.value
    return modes


def merge(template: dict[str, Any], overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Clone the template and apply one client's overrides (dicts merge, lists replace)."""
    out = copy.deepcopy(template)
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = copy.deepcopy(value)
    return out


def settings_from(config: dict[str, Any], *, company_name: str) -> ClientSettings:
    """The ClientSettings a client starts with, from a merged template config."""
    purchased = [d for d in config.get("purchased_domains") or [] if d in DOMAIN_NAMES]
    tool_modes = {**recommended_tool_modes(), **dict(config.get("tool_modes") or {})}
    return ClientSettings.from_dict(
        {
            "company_name": company_name,
            "deployment_type": config.get("deployment_type", "DEDICATED_DB"),
            "default_execution_mode": config.get(
                "default_execution_mode", ExecutionMode.WAIT_FOR_APPROVAL.value
            ),
            "purchased_domains": purchased,
            "enabled_domains": list(config.get("enabled_domains") or purchased),
            "tool_modes": tool_modes,
            "retention_policy": dict(config.get("retention_policy") or {}),
            "approval_policy": dict(config.get("approval_policy") or {}),
            "allow_phi": bool(config.get("allow_phi", False)),
            "allow_card_data": bool(config.get("allow_card_data", False)),
            "onboarding": {
                "template_version": config.get("template_version"),
                "assistant_name": config.get("assistant_name", "FreedomBot"),
            },
        }
    )


def seed_memory(ctx: TenantContext, config: dict[str, Any], settings: ClientSettings) -> int:
    """Write the template's starter SOPs/facts into a new client's own memory."""
    from bizos.memory import store as memory

    written = 0
    with tenant_scope(ctx):
        for entry in config.get("seed_memory") or []:
            category = MemoryCategory.parse(entry.get("category"), MemoryCategory.FACT)
            key = str(entry.get("key") or "").strip()
            if not key or memory.get_by_key(category, key, ctx=ctx) is not None:  # type: ignore[arg-type]
                continue
            memory.put(
                category=category,  # type: ignore[arg-type]
                memory_key=key,
                title=str(entry.get("title") or key),
                content=str(entry["content"]),
                tags=list(entry.get("tags") or []),
                access_area=entry.get("access_area"),
                attributes=dict(entry.get("attributes") or {}),
                source_type=SourceType.MANUAL,
                settings=settings,
                ctx=ctx,
            )
            written += 1
    return written
