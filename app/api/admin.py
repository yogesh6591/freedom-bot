"""
Admin Routes
============

§26 ``/api/users``, ``/api/roles``, ``/api/policies``, ``/api/domains``, plus
client configuration and the tool permission matrix.

Every route here requires ADMIN and every write is audited. User management is
scoped to the caller's own client: an admin at one customer cannot see, create or
modify users at another, because the client id comes from their token.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import bound, client_settings, current_context, require_admin
from bizos.audit import events as audit
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DOMAIN_NAMES
from bizos.domains.packs import PACKS
from bizos.rbac import permissions as perms
from bizos.rbac.matrix import MATRIX_TEMPLATES
from bizos.rbac.registry import TOOL_SPECS
from bizos.tenancy.context import TenantContext
from bizos.types import AuditEventType, Role, ToolPermission

router = APIRouter(prefix="/api", tags=["admin"])


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=512)
    display_name: str = ""
    roles: list[str] = Field(default_factory=lambda: ["VIEWER"])
    data_scopes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    roles: list[str] = Field(min_length=1)


class ScopeUpdate(BaseModel):
    data_scopes: list[str] = Field(default_factory=list)


class StatusUpdate(BaseModel):
    status: str


class PasswordUpdate(BaseModel):
    password: str = Field(min_length=12, max_length=512)


class SettingsUpdate(BaseModel):
    """Partial update of the §21 ClientSettings object."""

    default_execution_mode: Optional[str] = None
    enabled_domains: Optional[list[str]] = None
    allow_phi: Optional[bool] = None
    allow_card_data: Optional[bool] = None
    enabled_integrations: Optional[list[str]] = None
    memory_policy: Optional[dict[str, Any]] = None
    retention_policy: Optional[dict[str, Any]] = None
    approval_policy: Optional[dict[str, Any]] = None
    risk_policy: Optional[dict[str, Any]] = None
    onboarding: Optional[dict[str, Any]] = None
    tool_modes: Optional[dict[str, Optional[str]]] = None


class PermissionOverride(BaseModel):
    tool: str
    role: str
    permission: str


def _roles(names: list[str]) -> frozenset[Role]:
    parsed = {Role.parse(n) for n in names}
    resolved = frozenset(r for r in parsed if r is not None)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"No valid roles in {names}")
    return resolved  # type: ignore[return-value]


# --------------------------------------------------------------------- users


@router.get("/users")
def list_users(ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    return {"items": [u.to_dict() for u in control.list_users(ctx.client_id)]}


@router.post("/users", status_code=201)
def create_user(body: UserCreate, ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    try:
        user = control.create_user(
            client_id=ctx.client_id,
            email=str(body.email),
            password=body.password,
            display_name=body.display_name,
            roles=_roles(body.roles),
            created_by=ctx.user_id,
            data_scopes=body.data_scopes,
        )
    except control.DuplicateUser as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with bound(ctx):
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="USER_CREATED",
            request={"email": str(body.email), "roles": body.roles},
        )
    return user.to_dict()


def _assert_same_client(ctx: TenantContext, user_id: str) -> None:
    """An admin may only act on users in their own workspace."""
    try:
        user = control.get_user(user_id)
    except control.UserNotFound as exc:
        raise HTTPException(status_code=404, detail="No such user") from exc
    if user.client_id != ctx.client_id:
        # Same response as "not found": an admin must not be able to probe
        # whether a user id exists at another customer.
        raise HTTPException(status_code=404, detail="No such user")


@router.patch("/users/{user_id}/roles")
def set_roles(
    user_id: str, body: RoleUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    _assert_same_client(ctx, user_id)
    user = control.set_user_roles(user_id, _roles(body.roles), granted_by=ctx.user_id)
    with bound(ctx):
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="ROLES_CHANGED",
            request={"user_id": user_id, "roles": body.roles},
        )
    return user.to_dict()


@router.patch("/users/{user_id}/scopes")
def set_scopes(
    user_id: str, body: ScopeUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Grant or revoke restricted data areas (exec/hr/salary/finance/legal)."""
    _assert_same_client(ctx, user_id)
    try:
        user = control.set_user_scopes(user_id, body.data_scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with bound(ctx):
        audit.log(
            AuditEventType.DATA_SCOPES_CHANGED,
            ctx=ctx,
            status="DATA_SCOPES_CHANGED",
            request={"user_id": user_id, "data_scopes": sorted(user.data_scopes)},
        )
    return user.to_dict()


@router.get("/data-areas")
def list_data_areas() -> dict[str, Any]:
    from bizos.types import DATA_AREAS

    return {"items": list(DATA_AREAS)}


@router.patch("/users/{user_id}/status")
def set_status(
    user_id: str, body: StatusUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    _assert_same_client(ctx, user_id)
    return control.set_user_status(user_id, body.status).to_dict()


@router.post("/users/{user_id}/password")
def set_password(
    user_id: str, body: PasswordUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, str]:
    _assert_same_client(ctx, user_id)
    control.set_password(user_id, body.password)
    with bound(ctx):
        audit.log(
            AuditEventType.CONFIG_CHANGED, ctx=ctx, status="PASSWORD_RESET",
            request={"user_id": user_id},
        )
    return {"status": "updated"}


# --------------------------------------------------------------------- roles


@router.get("/roles")
def list_roles() -> dict[str, Any]:
    """The role catalogue with the permission-matrix templates behind it."""
    from bizos.control.schema import ROLE_SEED

    return {
        "roles": [
            {"name": name, "rank": rank, "description": description}
            for name, rank, description in ROLE_SEED
        ],
        "matrix_templates": {
            name: {str(role): str(perm) for role, perm in template.items()}
            for name, template in MATRIX_TEMPLATES.items()
        },
    }


# ----------------------------------------------------------------- policies


@router.get("/policies")
def get_policies(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """The client's policy configuration and the effective tool permission matrix."""
    with bound(ctx):
        overrides = perms.load_overrides(ctx, refresh=True)
        return {
            "settings": settings.to_dict(),
            "tools": [
                {
                    **spec.to_dict(),
                    "effective_permissions": {
                        str(role): str(perms.permission_for_role(spec, role, overrides))
                        for role in Role
                    },
                    "overridden": any((spec.name, str(r)) in overrides for r in Role),
                }
                for spec in TOOL_SPECS
            ],
        }


@router.put("/policies/settings")
def update_settings(
    body: SettingsUpdate,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Update the §21 tenant configuration. Values are clamped to platform floors."""
    changes = body.model_dump(exclude_none=True)
    if "enabled_domains" in changes:
        unpaid = [d for d in changes["enabled_domains"] if not settings.domain_purchased(d)]
        if unpaid:
            raise HTTPException(status_code=409, detail=_change_order_detail(unpaid))
    if "tool_modes" in changes:
        changes["tool_modes"] = _validated_tool_modes(settings, changes["tool_modes"])
    payload = settings.to_dict()
    for key, value in changes.items():
        if key == "tool_modes":
            payload[key] = value
            continue
        if isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    updated = ClientSettings.from_dict(payload)
    client = control.update_client_settings(ctx.client_id, updated, updated_by=ctx.user_id)
    with bound(ctx):
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="SETTINGS_UPDATED",
            request={"changes": body.model_dump(exclude_none=True)},
            result={"settings": client.settings.to_dict()},
        )
    return client.settings.to_dict()


@router.put("/policies/tool-permissions")
def set_tool_permission(
    body: PermissionOverride, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Override one role's permission for one tool, within the registry ceiling."""
    role = Role.parse(body.role)
    permission = ToolPermission.parse(body.permission)
    if role is None or permission is None:
        raise HTTPException(status_code=400, detail="Unknown role or permission")
    with bound(ctx):
        try:
            perms.set_override(
                ctx, tool=body.tool, role=role, permission=permission, updated_by=ctx.user_id  # type: ignore[arg-type]
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            tool=body.tool,
            status="TOOL_PERMISSION_OVERRIDE",
            request={"role": body.role, "permission": body.permission},
        )
    return {"tool": body.tool, "role": str(role), "permission": str(permission)}


@router.delete("/policies/tool-permissions")
def clear_tool_permission(
    tool: str, role: str, ctx: TenantContext = Depends(require_admin)
) -> dict[str, str]:
    parsed = Role.parse(role)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Unknown role")
    with bound(ctx):
        perms.clear_override(ctx, tool=tool, role=parsed)  # type: ignore[arg-type]
    return {"status": "cleared"}


# ------------------------------------------------------------------ domains


@router.get("/domains")
def list_domains(
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Domain packs with per-client purchase and enablement."""
    enablement = {d["name"]: d for d in control.list_client_domains(ctx.client_id)}
    return {
        "items": [
            {
                **pack.to_dict(),
                "enabled": enablement.get(pack.name, {}).get("enabled", False),
                "purchased": settings.domain_purchased(pack.name),
            }
            for pack in PACKS
        ]
    }


class DomainToggle(BaseModel):
    enabled: bool


@router.patch("/domains/{domain}")
def toggle_domain(
    domain: str,
    body: DomainToggle,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Enable or disable a domain pack for this client."""
    if domain not in DOMAIN_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown domain {domain}")
    if domain == "general" and not body.enabled:
        raise HTTPException(
            status_code=400,
            detail="The General pack provides organizational memory and cannot be disabled.",
        )
    if body.enabled and not settings.domain_purchased(domain):
        raise HTTPException(status_code=409, detail=_change_order_detail([domain]))
    enabled = set(settings.enabled_domains)
    enabled.add(domain) if body.enabled else enabled.discard(domain)
    settings.enabled_domains = sorted(enabled)
    client = control.update_client_settings(ctx.client_id, settings, updated_by=ctx.user_id)
    with bound(ctx):
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            domain=domain,
            status="DOMAIN_TOGGLED",
            request={"enabled": body.enabled},
        )
    return {"domain": domain, "enabled": body.enabled, "enabled_domains": client.settings.enabled_domains}


def _change_order_detail(domains: list[str]) -> str:
    return (
        f"{', '.join(domains)} is not part of this client's purchased package. "
        "Record a change order first (POST /api/domains/{domain}/change-order)."
    )


def _validated_tool_modes(settings: ClientSettings, raw: dict[str, Optional[str]]) -> dict[str, str]:
    """Merge per-tool mode changes; ``None``/"" clears an entry."""
    from bizos.rbac.registry import TOOLS_BY_NAME
    from bizos.types import ExecutionMode

    merged = dict(settings.tool_modes)
    for tool, mode in raw.items():
        if tool not in TOOLS_BY_NAME:
            raise HTTPException(status_code=404, detail=f"Unknown tool {tool}")
        if not mode:
            merged.pop(tool, None)
            continue
        parsed = ExecutionMode.parse(mode)
        if parsed is None:
            raise HTTPException(status_code=400, detail=f"Unknown execution mode {mode}")
        merged[tool] = parsed.value
    return merged


class ChangeOrder(BaseModel):
    reference: str = Field(min_length=1, max_length=200)
    note: str = ""
    enable: bool = True


@router.post("/domains/{domain}/change-order")
def record_change_order(
    domain: str,
    body: ChangeOrder,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """FB-035: add a domain to the purchased package against a change order."""
    if domain not in DOMAIN_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown domain {domain}")
    purchased = set(settings.purchased_domains)
    purchased.add(domain)
    settings.purchased_domains = sorted(purchased)
    if body.enable:
        settings.enabled_domains = sorted(set(settings.enabled_domains) | {domain})
    orders = list((settings.onboarding or {}).get("change_orders") or [])
    orders.append({"domain": domain, "reference": body.reference, "note": body.note, "by": ctx.user_id})
    settings.onboarding = {**(settings.onboarding or {}), "change_orders": orders}
    client = control.update_client_settings(ctx.client_id, settings, updated_by=ctx.user_id)
    with bound(ctx):
        audit.log(
            AuditEventType.CHANGE_ORDER,
            ctx=ctx,
            domain=domain,
            status="DOMAIN_PURCHASED",
            request={"reference": body.reference, "note": body.note},
        )
    return {
        "domain": domain,
        "purchased_domains": client.settings.purchased_domains,
        "enabled_domains": client.settings.enabled_domains,
    }


# ------------------------------------------------------------------ clients


@router.get("/client")
def get_client(ctx: TenantContext = Depends(current_context)) -> dict[str, Any]:
    """The caller's own client record. Never another client's."""
    return control.get_client(ctx.client_id).to_dict()


# -------------------------------------------------------------- onboarding (FB-044)


@router.get("/onboarding")
def get_onboarding(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Current first-client configuration profile."""
    from bizos.onboarding import get_profile

    return {"profile": get_profile(settings).to_dict()}


class OnboardingApply(BaseModel):
    goals: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    sops: list[dict[str, str]] = Field(default_factory=list)
    roles_notes: str = ""
    access_notes: str = ""
    autonomy_mode: str = "WAIT_FOR_APPROVAL"
    enabled_domains: Optional[list[str]] = None
    review_points: list[str] = Field(default_factory=list)
    assessment: dict[str, Any] = Field(default_factory=dict)


@router.put("/onboarding")
def apply_onboarding(
    body: OnboardingApply,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Apply guided first-client config: memory + domains + autonomy mode."""
    from bizos.onboarding import OnboardingProfile, apply_profile

    unpaid = [d for d in body.enabled_domains or [] if not settings.domain_purchased(d)]
    if unpaid:
        raise HTTPException(status_code=409, detail=_change_order_detail(unpaid))
    profile = OnboardingProfile(
        goals=body.goals,
        systems=body.systems,
        sops=body.sops,
        roles_notes=body.roles_notes,
        access_notes=body.access_notes,
        autonomy_mode=body.autonomy_mode,
        enabled_domains=body.enabled_domains or list(settings.enabled_domains),
        review_points=body.review_points,
        assessment=body.assessment,
    )
    with bound(ctx):
        result = apply_profile(
            client_id=ctx.client_id, profile=profile, settings=settings, ctx=ctx
        )
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="ONBOARDING_APPLIED",
            request={
                "goals": len(body.goals),
                "systems": len(body.systems),
                "sops": len(body.sops),
                "autonomy_mode": body.autonomy_mode,
            },
        )
    return result
