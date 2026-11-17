"""
Admin Routes
============

§26 ``/api/users``, ``/api/roles``, ``/api/policies``, ``/api/domains``, plus
client configuration and the tool permission matrix.

Every route here requires ADMIN and every write is audited. User management is
scoped to the caller's own client: an admin at one customer cannot see, create or
modify users at another, because the client id comes from their token.

The changes themselves live in :mod:`bizos.admin_ops`, shared with the admin
chat tools.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import bound, client_settings, current_context, require_admin
from bizos import admin_ops as ops
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.packs import PACKS
from bizos.rbac import permissions as perms
from bizos.rbac.matrix import MATRIX_TEMPLATES
from bizos.rbac.registry import TOOL_SPECS
from bizos.tenancy.context import TenantContext
from bizos.types import Role

router = APIRouter(prefix="/api", tags=["admin"])

T = TypeVar("T")


def _run(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return fn(*args, **kwargs)
    except ops.AdminError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=512)
    display_name: str = ""
    roles: list[str] = Field(default_factory=lambda: ["VIEWER"])
    data_scopes: list[str] = Field(default_factory=list)
    #: When set, this decides the permission level and data tags instead of the two fields above.
    job_role: Optional[str] = None
    #: The person's one role: advise, draft, approve or auto (Approve when left out). Not for admins.
    access_mode: Optional[str] = None


class RoleUpdate(BaseModel):
    roles: list[str] = Field(min_length=1)


class ScopeUpdate(BaseModel):
    data_scopes: list[str] = Field(default_factory=list)


class JobRoleUpdate(BaseModel):
    job_role: str
    access_mode: Optional[str] = None


class AccessModeUpdate(BaseModel):
    #: advise, draft, approve or auto
    mode: str


class AdminUpdate(BaseModel):
    admin: bool


class CanApproveUpdate(BaseModel):
    can_approve: bool


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


# --------------------------------------------------------------------- users


@router.get("/users")
def list_users(ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    return {"items": [u.to_dict() for u in control.list_users(ctx.client_id)]}


@router.post("/users", status_code=201)
def create_user(body: UserCreate, ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    return _run(
        ops.create_user,
        ctx,
        email=str(body.email),
        password=body.password,
        display_name=body.display_name,
        roles=body.roles,
        data_scopes=body.data_scopes,
        job_role=body.job_role,
        access_mode=body.access_mode,
    ).to_dict()


@router.patch("/users/{user_id}/roles")
def set_roles(
    user_id: str, body: RoleUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    return _run(ops.set_roles, ctx, user_id, body.roles).to_dict()


@router.patch("/users/{user_id}/job-role")
def set_job_role(
    user_id: str, body: JobRoleUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    return _run(ops.set_job_role, ctx, user_id, body.job_role, body.access_mode).to_dict()


@router.get("/person-roles")
def list_person_roles(ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    """The four roles with what each means, and the departments a person's data access can include."""
    from bizos.policy.person_role import choices

    return choices()


@router.patch("/users/{user_id}/access-mode")
def set_access_mode(
    user_id: str, body: AccessModeUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Set a person's one role: Advise, Draft, Approve or Auto."""
    return _run(ops.set_access_mode, ctx, user_id, body.mode).to_dict()


@router.patch("/users/{user_id}/admin")
def set_admin(user_id: str, body: AdminUpdate, ctx: TenantContext = Depends(require_admin)) -> dict[str, Any]:
    """Make a person an admin, or turn it off. Nobody can remove their own admin, nor the last one."""
    return _run(ops.set_admin, ctx, user_id, body.admin).to_dict()


@router.patch("/users/{user_id}/can-approve")
def set_can_approve(
    user_id: str, body: CanApproveUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    return _run(ops.set_can_approve, ctx, user_id, body.can_approve).to_dict()


@router.patch("/users/{user_id}/scopes")
def set_scopes(
    user_id: str, body: ScopeUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Grant or revoke restricted data areas (exec/hr/salary/finance/legal)."""
    return _run(ops.set_scopes, ctx, user_id, body.data_scopes).to_dict()


@router.get("/data-areas")
def list_data_areas() -> dict[str, Any]:
    from bizos.types import DATA_AREAS

    return {"items": list(DATA_AREAS)}


@router.patch("/users/{user_id}/status")
def set_status(
    user_id: str, body: StatusUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    return _run(ops.set_status, ctx, user_id, body.status).to_dict()


@router.post("/users/{user_id}/password")
def set_password(
    user_id: str, body: PasswordUpdate, ctx: TenantContext = Depends(require_admin)
) -> dict[str, str]:
    _run(ops.set_password, ctx, user_id, body.password)
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
    return _run(ops.update_settings, ctx, settings, body.model_dump(exclude_none=True)).to_dict()


@router.put("/policies/tool-permissions")
def set_tool_permission(
    body: PermissionOverride, ctx: TenantContext = Depends(require_admin)
) -> dict[str, Any]:
    """Override one role's permission for one tool, within the registry ceiling."""
    return _run(ops.set_tool_permission, ctx, tool=body.tool, role=body.role, permission=body.permission)


@router.delete("/policies/tool-permissions")
def clear_tool_permission(
    tool: str, role: str, ctx: TenantContext = Depends(require_admin)
) -> dict[str, str]:
    _run(ops.clear_tool_permission, ctx, tool=tool, role=role)
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
    updated = _run(ops.toggle_domain, ctx, settings, domain, body.enabled)
    return {"domain": domain, "enabled": body.enabled, "enabled_domains": updated.enabled_domains}


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
    updated = _run(
        ops.record_change_order, ctx, settings, domain,
        reference=body.reference, note=body.note, enable=body.enable,
    )
    return {
        "domain": domain,
        "purchased_domains": updated.purchased_domains,
        "enabled_domains": updated.enabled_domains,
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
    return _run(ops.apply_onboarding, ctx, settings, **body.model_dump())


# -------------------------------------------------------------- insights (FB-041 / 043 / 050)


@router.get("/insights/alerts")
def get_alerts(
    ctx: TenantContext = Depends(current_context),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Rare inefficiency alerts for this client (FB-041).

    Detected from the client's own memory, chat questions and actions (M09-11),
    rescanned when the last scan is stale (M09-13), and filtered to the areas
    this caller may see (M09-14).
    """
    from bizos.insights import last_scanned, list_alerts, open_alerts, refresh_if_stale, visible_to

    with bound(ctx):
        settings = refresh_if_stale(client_id=ctx.client_id, settings=settings, ctx=ctx)
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
    return {"items": _run(ops.dismiss_alert, ctx, settings, body.alert_id)}


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
    items = _run(ops.save_baselines, ctx, settings, body.items)
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
    return _run(ops.handoff_assessment, ctx, settings, body.assessment)


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
    return _run(
        ops.mark_launch_item, ctx, settings,
        item_id=body.item_id, status=body.status, waive_reason=body.waive_reason,
    )


class LaunchSignOffBody(BaseModel):
    force: bool = False


@router.post("/launch/sign-off")
def launch_sign_off(
    body: LaunchSignOffBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """Record go-live sign-off when the checklist is ready (FB-045)."""
    return _run(ops.launch_sign_off, ctx, settings, force=body.force)


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
    #: Target margin; used to derive cost when cepoch_cost_usd is omitted.
    margin_percent: Optional[float] = None
    quote_status: Optional[str] = None
    notes: Optional[str] = None
    scope_rules: Optional[dict[str, Any]] = None


@router.put("/pricing/resale-rules")
def put_resale_rules(
    body: ResaleRulesBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    return {"rules": _run(ops.save_resale_rules, ctx, settings, body.model_dump(exclude_none=True))}


# -------------------------------------------------------------- consult pricing (FB-049)


@router.get("/pricing/consult-config")
def get_consult_config(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """This client's scope weights + FB-048 rules for the consult calculator."""
    from bizos.pricing import face_scope_config, get_rules

    return {"config": face_scope_config(settings), "rules": get_rules(settings)}


@router.get("/pricing/consult-quotes")
def get_consult_quotes(
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    from bizos.pricing import list_consult_quotes

    return {"items": list_consult_quotes(settings)}


class ConsultQuoteBody(BaseModel):
    domains: list[str] = Field(default_factory=list)
    autonomy: str = "WAIT_FOR_APPROVAL"
    integrations: int = Field(default=0, ge=0, le=50)
    discount_percent: float = Field(default=0, ge=0, le=100)
    label: str = Field(default="", max_length=200)
    save: bool = False


@router.post("/pricing/consult-quote")
def post_consult_quote(
    body: ConsultQuoteBody,
    ctx: TenantContext = Depends(require_admin),
    settings: ClientSettings = Depends(client_settings),
) -> dict[str, Any]:
    """FB-049: calculate (and optionally save) a consult quote from face rules."""
    return _run(ops.consult_quote, ctx, settings, **body.model_dump())
