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
    #: Legacy per-category switches; each is applied as an edit to
    #: ``order_dpa_allows``, which is the one stored setting (FB-039).
    allow_phi: Optional[bool] = None
    allow_card_data: Optional[bool] = None
    allow_child_data: Optional[bool] = None
    allow_biometrics: Optional[bool] = None
    #: FB-039: the "Order/DPA allows" setting (phi / card / child / biometrics).
    order_dpa_allows: Optional[list[str]] = None
    #: FB-039: the deployment's model vendor has a signed BAA for this client.
    #: The vendor itself comes from ``MODEL_ID`` and is not editable here.
    llm_baa_covered: Optional[bool] = None
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
    _fold_legacy_dpa_flags(changes, settings)
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


_LEGACY_DPA_FIELDS = {
    "allow_phi": "phi",
    "allow_card_data": "card",
    "allow_child_data": "child",
    "allow_biometrics": "biometrics",
}


def _fold_legacy_dpa_flags(changes: dict[str, Any], settings: ClientSettings) -> None:
    """FB-039: turn legacy ``allow_*`` switches into an ``order_dpa_allows`` edit.

    The Order/DPA list is the only stored setting, so a legacy switch must edit
    it — otherwise the list would silently override the switch on save.
    """
    legacy = {k: changes.pop(k) for k in list(changes) if k in _LEGACY_DPA_FIELDS}
    if not legacy or "order_dpa_allows" in changes:
        return
    allowed = set(settings.order_dpa_allows)
    for key, on in legacy.items():
        (allowed.add if on else allowed.discard)(_LEGACY_DPA_FIELDS[key])
    changes["order_dpa_allows"] = sorted(allowed)


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

    profile = get_profile(settings).to_dict()
    blob = settings.onboarding or {}
    profile["handoff_at"] = blob.get("handoff_at")
    profile["handoff_source"] = blob.get("handoff_source")
    profile["handoff_event"] = blob.get("handoff_event")
    return {"profile": profile}


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
    #: FB-044: out-of-scope items for this engagement.
    exclusions: list[str] = Field(default_factory=list)
    #: FB-044: baseline metrics captured at setup. Omitted keeps the current
    #: baselines; an empty list clears them.
    baselines: Optional[list[dict[str, Any]]] = None


@router.put("/onboarding")
def apply_onboarding(
    body: OnboardingApply,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Apply guided first-client config: memory + domains + autonomy mode."""
    from bizos.onboarding import OnboardingProfile, apply_profile, get_profile

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
        exclusions=body.exclusions,
        baselines=body.baselines if body.baselines is not None else get_profile(settings).baselines,
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


# -------------------------------------------------------------- insights (FB-041 / 043 / 050)


@router.get("/insights/alerts")
def get_alerts(
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Rare inefficiency alerts for this client (FB-041).

    Detected from the client's own workspace data (M09-11), rescanned when the
    last scan is over an hour old (M09-13), and filtered to the areas this
    caller may see (M09-14).
    """
    from datetime import timedelta

    from bizos.insights import last_scanned, list_alerts, open_alerts, scan_alerts, visible_to
    from bizos.util.timeutil import utcnow

    scanned = last_scanned(settings)
    stale = True
    if scanned:
        from datetime import datetime

        stale = datetime.fromisoformat(scanned) < utcnow() - timedelta(hours=1)
    if stale:
        with bound(ctx):
            scan_alerts(client_id=ctx.client_id, settings=settings, ctx=ctx)
        settings = control.get_client(ctx.client_id).settings
    return {
        "items": [a for a in list_alerts(settings) if visible_to(a, ctx)],
        "open": open_alerts(settings, ctx),
        "scanned_at": last_scanned(settings),
        "note": "Directional only — not a full always-on waste engine.",
    }


@router.post("/insights/alerts/scan")
def scan_alerts_route(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Look for recurring waste in this client's data now (M09-13)."""
    from bizos.insights import last_scanned, scan_alerts

    with bound(ctx):
        items = scan_alerts(client_id=ctx.client_id, settings=settings, ctx=ctx)
    return {
        "items": items,
        "scanned_at": last_scanned(control.get_client(ctx.client_id).settings),
        "note": "Directional only — not a full always-on waste engine.",
    }


class AlertDismiss(BaseModel):
    alert_id: str = Field(min_length=1, max_length=80)


@router.post("/insights/alerts/dismiss")
def dismiss_alert_route(
    body: AlertDismiss,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    from bizos.insights import dismiss_alert

    try:
        items = dismiss_alert(
            client_id=ctx.client_id, settings=settings, alert_id=body.alert_id, ctx=ctx
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown alert {exc}") from exc
    return {"items": items}


@router.get("/insights/baselines")
def get_baselines(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Before/after metrics for sold workflows (FB-050). Directional only."""
    from bizos.insights import list_baselines

    return {
        "items": list_baselines(settings),
        "disclaimer": "Directional before/after only. No guaranteed ROI.",
    }


class BaselinesBody(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/insights/baselines")
def put_baselines(
    body: BaselinesBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    from bizos.insights import save_baselines

    try:
        items = save_baselines(
            client_id=ctx.client_id, settings=settings, baselines=body.items, ctx=ctx
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.log(
        AuditEventType.CONFIG_CHANGED,
        ctx=ctx,
        status="BASELINES_UPDATED",
        request={"count": len(items)},
    )
    return {"items": items, "disclaimer": "Directional before/after only. No guaranteed ROI."}


class HandoffBody(BaseModel):
    assessment: dict[str, Any] = Field(default_factory=dict)


@router.post("/onboarding/handoff")
def onboarding_handoff(
    body: HandoffBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Paste sale/assessment JSON → prefill First-client setup (FB-043).

    Prefer ``POST /api/owner/sale-events`` after checkout/contract so this is
    not needed by hand.
    """
    from bizos.insights import handoff_assessment

    if not body.assessment:
        raise HTTPException(status_code=400, detail="assessment JSON object is required")
    result = handoff_assessment(
        client_id=ctx.client_id,
        settings=settings,
        assessment=body.assessment,
        ctx=ctx,
        source="manual",
    )
    audit.log(
        AuditEventType.CONFIG_CHANGED,
        ctx=ctx,
        status="ASSESSMENT_HANDOFF",
        request={"keys": sorted(body.assessment.keys())[:20], "source": "manual"},
    )
    return result


# -------------------------------------------------------------- launch checklist (FB-045)


@router.get("/launch/checklist")
def get_launch_checklist(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """First-client launch checklist status (FB-045)."""
    from bizos.launch import checklist_summary

    _ = ctx
    return checklist_summary(settings)


class LaunchItemBody(BaseModel):
    item_id: str = Field(min_length=1, max_length=80)
    status: str = Field(default="done", min_length=3, max_length=20)
    waive_reason: str = Field(default="", max_length=500)


@router.post("/launch/checklist/items")
def mark_launch_item(
    body: LaunchItemBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    from bizos.launch import mark_item

    try:
        return mark_item(
            client_id=ctx.client_id,
            settings=settings,
            item_id=body.item_id,
            status=body.status,
            actor=ctx.user_id,
            waive_reason=body.waive_reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class LaunchSignOffBody(BaseModel):
    force: bool = False


@router.post("/launch/sign-off")
def launch_sign_off(
    body: LaunchSignOffBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Record go-live sign-off when the checklist is ready (FB-045)."""
    from bizos.launch import sign_off

    try:
        result = sign_off(
            client_id=ctx.client_id,
            settings=settings,
            actor=ctx.user_id,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.log(
        AuditEventType.CONFIG_CHANGED,
        ctx=ctx,
        status="LAUNCH_SIGNED_OFF",
        request={"force": body.force},
    )
    return result


# -------------------------------------------------------------- resale pricing (FB-048)


@router.get("/pricing/resale-rules")
def get_resale_rules(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """JeanneCAIO resale rules + face quote values (FB-048)."""
    from bizos.pricing import get_rules

    return {"rules": get_rules(settings)}


class ResaleRulesBody(BaseModel):
    """Omitted fields keep their current value; nothing falls back to a demo price."""

    customer_price_usd: Optional[float] = None
    cepoch_cost_usd: Optional[float] = None
    quote_status: Optional[str] = None
    notes: Optional[str] = None


@router.put("/pricing/resale-rules")
def put_resale_rules(
    body: ResaleRulesBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    from bizos.pricing import save_rules

    try:
        rules = save_rules(
            client_id=ctx.client_id,
            settings=settings,
            rules=body.model_dump(exclude_none=True),
            ctx=ctx,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.log(
        AuditEventType.CONFIG_CHANGED,
        ctx=ctx,
        status="RESALE_RULES_UPDATED",
        request={
            "customer_price_usd": rules["customer_price_usd"],
            "quote_status": rules["quote_status"],
            "placeholder": True,
        },
    )
    return {"rules": rules}


# -------------------------------------------------------------- consult pricing (FB-049)


@router.get("/pricing/consult-config")
def get_consult_config(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Face scope weights + current FB-048 rules for the consult calculator."""
    from bizos.pricing import face_scope_config, get_rules

    return {"config": face_scope_config(), "rules": get_rules(settings)}


class ConsultQuoteBody(BaseModel):
    domains: list[str] = Field(default_factory=list)
    autonomy: str = "WAIT_FOR_APPROVAL"
    integrations: int = Field(default=0, ge=0, le=50)
    label: str = ""
    save: bool = False


@router.post("/pricing/consult-quote")
def post_consult_quote(
    body: ConsultQuoteBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """FB-049: calculate (and optionally save) a consult quote from face rules."""
    from bizos.pricing import calculate_consult_quote, save_consult_quote

    try:
        quote = calculate_consult_quote(
            settings,
            domains=body.domains,
            autonomy=body.autonomy,
            integrations=body.integrations,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    saved = None
    if body.save:
        saved = save_consult_quote(
            client_id=ctx.client_id,
            settings=settings,
            quote=quote,
            ctx=ctx,
            label=body.label,
        )
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="CONSULT_QUOTE_SAVED",
            request={
                "id": saved["id"],
                "customer_price_usd": saved["customer_price_usd"],
                "domains": saved["inputs"]["domains"],
                "placeholder": True,
            },
        )

    return {"quote": quote, "saved": saved}

