"""
Effective Permissions
=====================

Resolves "what may *this caller* do with *this tool*" by combining:

1. the tool's declared matrix in :mod:`bizos.rbac.registry`,
2. any per-client override rows in the workspace's ``tool_permissions`` table, and
3. the full set of roles the caller holds.

A caller may hold several roles. They are combined by capability rather than by
rank, because the roles are not a single ladder: someone who is both a DRAFTER
and an APPROVER may draft *and* approve, while ``APPROVE_ONLY`` alone grants no
ability to originate anything.

Overrides are cached per client and invalidated on write. The cache is keyed by
``client_id`` so one client's override can never be served to another.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from agno.utils.log import log_warning
from sqlalchemy import text

from bizos.rbac.registry import ToolSpec, get_spec
from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import workspace_connection
from bizos.types import Role, ToolPermission

#: How much direct-invocation power each permission carries. APPROVE_ONLY ranks
#: with DENIED here because approving someone else's action is not invoking.
_INVOKE_POWER: Mapping[ToolPermission, int] = {
    ToolPermission.DENIED: 0,
    ToolPermission.APPROVE_ONLY: 0,
    ToolPermission.DRAFT_ONLY: 1,
    ToolPermission.EXECUTE_AFTER_APPROVAL: 2,
    ToolPermission.ALLOWED: 3,
}


@dataclass(frozen=True)
class EffectivePermission:
    """What one caller may do with one tool, before mode and policy are applied."""

    tool: str
    permission: ToolPermission
    can_draft: bool
    can_invoke: bool
    can_execute_approved: bool
    can_approve: bool
    #: "registry" or "client_override" — surfaced in the Admin UI and in audit.
    source: str = "registry"

    @property
    def denied(self) -> bool:
        return not (self.can_draft or self.can_invoke or self.can_execute_approved or self.can_approve)


# ---------------------------------------------------------------------------
# Per-client overrides
# ---------------------------------------------------------------------------

_override_cache: dict[str, dict[tuple[str, str], ToolPermission]] = {}
_cache_lock = threading.Lock()


def load_overrides(ctx: TenantContext, *, refresh: bool = False) -> dict[tuple[str, str], ToolPermission]:
    """Per-client ``(tool, role) -> permission`` overrides from the workspace."""
    if not refresh:
        cached = _override_cache.get(ctx.client_id)
        if cached is not None:
            return cached
    rows: dict[tuple[str, str], ToolPermission] = {}
    try:
        with workspace_connection(ctx, readonly=True) as conn:
            for row in conn.execute(
                text("SELECT tool_name, role, permission FROM tool_permissions")
            ).fetchall():
                parsed = ToolPermission.parse(row.permission)
                if parsed is not None:
                    rows[(row.tool_name, str(row.role).upper())] = parsed  # type: ignore[index]
    except Exception as exc:
        # The workspace is unreachable. Serve the last known overrides if we have
        # them, else fall back to the registry defaults. This is not a security
        # relaxation in practice: every tool that could act needs this same
        # workspace, so a database that cannot answer here cannot execute
        # anything either. Denying instead would turn a transient blip into a
        # total outage, including for reads.
        cached = _override_cache.get(ctx.client_id)
        log_warning(
            f"tool_permissions unavailable for client {ctx.client_id}; "
            f"{'using cached overrides' if cached else 'falling back to registry defaults'}: {exc}"
        )
        return cached if cached is not None else {}
    with _cache_lock:
        _override_cache[ctx.client_id] = rows
    return rows


def set_override(
    ctx: TenantContext, *, tool: str, role: Role, permission: ToolPermission, updated_by: str
) -> None:
    """Write a per-client permission override and invalidate the cache.

    A spec's own guardrails are not overridable: an override cannot grant a role
    more than the tool's registry entry allows for the *most* privileged role,
    so a client cannot configure its way past ``WRITE_CRITICAL``.
    """
    spec = get_spec(tool)
    if spec is None:
        raise KeyError(f"Unknown tool: {tool}")
    ceiling = max(
        (_INVOKE_POWER[p] for p in spec.permissions.values()), default=0
    )
    if _INVOKE_POWER[permission] > ceiling:
        raise PermissionError(
            f"{tool}: cannot grant {permission} to {role} — the tool's registry ceiling is "
            f"{max(spec.permissions.values(), key=lambda p: _INVOKE_POWER[p])}"
        )
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO tool_permissions (tool_name, role, permission, updated_by, updated_at) "
                "VALUES (:t, :r, :p, :u, NOW()) "
                "ON CONFLICT (tool_name, role) DO UPDATE SET permission = EXCLUDED.permission, "
                "updated_by = EXCLUDED.updated_by, updated_at = NOW()"
            ),
            {"t": tool, "r": str(role), "p": str(permission), "u": updated_by},
        )
    invalidate(ctx.client_id)


def clear_override(ctx: TenantContext, *, tool: str, role: Role) -> None:
    """Remove an override so the registry default applies again."""
    with workspace_connection(ctx) as conn:
        conn.execute(
            text("DELETE FROM tool_permissions WHERE tool_name = :t AND role = :r"),
            {"t": tool, "r": str(role)},
        )
    invalidate(ctx.client_id)


def invalidate(client_id: Optional[str] = None) -> None:
    """Drop cached overrides for one client, or for all."""
    with _cache_lock:
        if client_id is None:
            _override_cache.clear()
        else:
            _override_cache.pop(client_id, None)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def permission_for_role(
    spec: ToolSpec, role: Role, overrides: Mapping[tuple[str, str], ToolPermission]
) -> ToolPermission:
    """One role's permission for one tool, honoring a client override."""
    return overrides.get((spec.name, str(role)), spec.permission_for(role))


def resolve(
    tool: str,
    roles: Iterable[Role],
    *,
    ctx: Optional[TenantContext] = None,
    overrides: Optional[Mapping[tuple[str, str], ToolPermission]] = None,
) -> EffectivePermission:
    """The effective permission for ``roles`` on ``tool``.

    An unregistered tool resolves to fully denied — there is no metadata to
    reason about, so the safe answer is "no".
    """
    spec = get_spec(tool)
    if spec is None:
        return EffectivePermission(
            tool=tool,
            permission=ToolPermission.DENIED,
            can_draft=False,
            can_invoke=False,
            can_execute_approved=False,
            can_approve=False,
            source="unregistered",
        )

    role_set = frozenset(roles)
    if overrides is None:
        overrides = load_overrides(ctx) if ctx is not None else {}

    per_role = {role: permission_for_role(spec, role, overrides) for role in role_set}
    source = (
        "client_override"
        if any((spec.name, str(r)) in overrides for r in role_set)
        else "registry"
    )

    best = max(per_role.values(), key=lambda p: _INVOKE_POWER[p], default=ToolPermission.DENIED)

    can_invoke = any(p == ToolPermission.ALLOWED for p in per_role.values())
    can_execute_approved = can_invoke or any(
        p == ToolPermission.EXECUTE_AFTER_APPROVAL for p in per_role.values()
    )
    # Drafting is implied by any permission that lets you originate the action.
    can_draft = can_invoke or any(
        p in (ToolPermission.DRAFT_ONLY, ToolPermission.EXECUTE_AFTER_APPROVAL)
        for p in per_role.values()
    )
    # Approving is an APPROVER/ADMIN capability, and only where that role is not
    # denied this tool outright.
    can_approve = any(
        role in (Role.APPROVER, Role.ADMIN) and per_role.get(role) != ToolPermission.DENIED
        for role in role_set
    )

    return EffectivePermission(
        tool=tool,
        permission=best,
        can_draft=can_draft,
        can_invoke=can_invoke,
        can_execute_approved=can_execute_approved,
        can_approve=can_approve,
        source=source,
    )


def permitted_tools(
    roles: Iterable[Role],
    *,
    ctx: Optional[TenantContext] = None,
    overrides: Optional[Mapping[tuple[str, str], ToolPermission]] = None,
) -> list[EffectivePermission]:
    """Every tool the caller has *some* capability on. Used by the Admin UI."""
    from bizos.rbac.registry import TOOL_SPECS

    if overrides is None:
        overrides = load_overrides(ctx) if ctx is not None else {}
    resolved = [resolve(s.name, roles, overrides=overrides) for s in TOOL_SPECS]
    return [p for p in resolved if not p.denied]
