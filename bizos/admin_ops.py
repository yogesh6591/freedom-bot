"""
Workspace Administration
========================

Every change the Administration page can make — setup, alerts, launch,
pricing, users, policies, domain packs and tool permissions — implemented once
and called from both the admin API routes and the admin chat tools, so the two
can never disagree about what is allowed.

Callers must already have established that ``ctx`` is an ADMIN. Every function
is scoped to ``ctx.client_id``; a user id from another client is reported as
not found. Writes are audited here, not by the caller.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from bizos.audit import events as audit
from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.tenancy.context import TenantContext, tenant_scope
from bizos.types import AuditEventType, Role, ToolPermission
from bizos.util.timeutil import utcnow


class AdminError(Exception):
    """An admin change that was refused. ``status`` is the matching HTTP code."""

    status = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AdminNotFound(AdminError):
    status = 404


class AdminConflict(AdminError):
    status = 409


class AdminForbidden(AdminError):
    status = 403


USER_STATUSES = ("ACTIVE", "SUSPENDED")
MIN_PASSWORD_LENGTH = 12
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

#: Settings an admin may change through :func:`update_settings`.
SETTINGS_FIELDS = frozenset(
    {
        "default_execution_mode",
        "enabled_domains",
        "allow_phi",
        "allow_card_data",
        "allow_child_data",
        "allow_biometrics",
        "order_dpa_allows",
        "llm_baa_covered",
        "enabled_integrations",
        "memory_policy",
        "retention_policy",
        "approval_policy",
        "risk_policy",
        "onboarding",
        "tool_modes",
    }
)

_LEGACY_DPA_FIELDS = {
    "allow_phi": "phi",
    "allow_card_data": "card",
    "allow_child_data": "child",
    "allow_biometrics": "biometrics",
}


def current_settings(ctx: TenantContext) -> ClientSettings:
    """The client's settings as stored now (not as cached at request start)."""
    return control.get_client(ctx.client_id).settings


def _log(ctx: TenantContext, event: AuditEventType, status: str, **fields: Any) -> None:
    with tenant_scope(ctx):
        audit.log(event, ctx=ctx, status=status, **fields)


# --------------------------------------------------------------------- users


def parse_roles(names: Iterable[str]) -> frozenset[Role]:
    resolved = frozenset(r for r in (Role.parse(n) for n in names) if r is not None)
    if not resolved:
        raise AdminError(f"No valid roles in {list(names)}")
    return resolved  # type: ignore[return-value]


def user_in_client(ctx: TenantContext, user_id: str):
    """The user, if they belong to the caller's client."""
    try:
        user = control.get_user(user_id)
    except control.UserNotFound as exc:
        raise AdminNotFound("No such user") from exc
    if user.client_id != ctx.client_id:
        # Same answer as "not found": an admin must not be able to probe whether
        # a user id exists at another customer.
        raise AdminNotFound("No such user")
    return user


def create_user(
    ctx: TenantContext,
    *,
    email: str,
    password: str,
    display_name: str = "",
    roles: Iterable[str] = ("VIEWER",),
    data_scopes: Iterable[str] = (),
):
    email = (email or "").strip()
    if not _EMAIL.match(email):
        raise AdminError(f"{email!r} is not a valid email address")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AdminError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    role_names = list(roles)
    try:
        user = control.create_user(
            client_id=ctx.client_id,
            email=email,
            password=password,
            display_name=display_name,
            roles=parse_roles(role_names),
            created_by=ctx.user_id,
            data_scopes=list(data_scopes),
        )
    except control.DuplicateUser as exc:
        raise AdminConflict(str(exc)) from exc
    except ValueError as exc:
        raise AdminError(str(exc)) from exc
    _log(ctx, AuditEventType.CONFIG_CHANGED, "USER_CREATED", request={"email": email, "roles": role_names})
    return user


def set_roles(ctx: TenantContext, user_id: str, roles: Iterable[str]):
    user_in_client(ctx, user_id)
    role_names = list(roles)
    user = control.set_user_roles(user_id, parse_roles(role_names), granted_by=ctx.user_id)
    _log(ctx, AuditEventType.CONFIG_CHANGED, "ROLES_CHANGED", request={"user_id": user_id, "roles": role_names})
    return user


def set_scopes(ctx: TenantContext, user_id: str, data_scopes: Iterable[str]):
    """Grant or revoke restricted data areas (exec/hr/salary/finance/legal)."""
    user_in_client(ctx, user_id)
    try:
        user = control.set_user_scopes(user_id, list(data_scopes))
    except ValueError as exc:
        raise AdminError(str(exc)) from exc
    _log(
        ctx,
        AuditEventType.DATA_SCOPES_CHANGED,
        "DATA_SCOPES_CHANGED",
        request={"user_id": user_id, "data_scopes": sorted(user.data_scopes)},
    )
    return user


def set_status(ctx: TenantContext, user_id: str, status: str):
    user_in_client(ctx, user_id)
    wanted = (status or "").strip().upper()
    if wanted not in USER_STATUSES:
        raise AdminError(f"Status must be one of {', '.join(USER_STATUSES)}")
    user = control.set_user_status(user_id, wanted)
    _log(ctx, AuditEventType.CONFIG_CHANGED, "USER_STATUS_CHANGED", request={"user_id": user_id, "status": wanted})
    return user


def set_password(ctx: TenantContext, user_id: str, password: str) -> None:
    user_in_client(ctx, user_id)
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AdminError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    control.set_password(user_id, password)
    _log(ctx, AuditEventType.CONFIG_CHANGED, "PASSWORD_RESET", request={"user_id": user_id})


# ------------------------------------------------------------------ settings


def change_order_detail(domains: list[str]) -> str:
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
            raise AdminNotFound(f"Unknown tool {tool}")
        if not mode:
            merged.pop(tool, None)
            continue
        parsed = ExecutionMode.parse(mode)
        if parsed is None:
            raise AdminError(f"Unknown execution mode {mode}")
        merged[tool] = parsed.value
    return merged


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


def update_settings(ctx: TenantContext, settings: ClientSettings, changes: dict[str, Any]) -> ClientSettings:
    """Partial update of the §21 tenant configuration, clamped to platform floors."""
    unknown = sorted(set(changes) - SETTINGS_FIELDS)
    if unknown:
        raise AdminError(f"Unknown setting(s): {', '.join(unknown)}")
    requested = dict(changes)
    changes = {k: v for k, v in changes.items() if v is not None}
    if "enabled_domains" in changes:
        unpaid = [d for d in changes["enabled_domains"] if not settings.domain_purchased(d)]
        if unpaid:
            raise AdminConflict(change_order_detail(unpaid))
    if "default_execution_mode" in changes:
        from bizos.types import ExecutionMode

        mode = ExecutionMode.parse(changes["default_execution_mode"])
        if mode is None:
            raise AdminError(f"Unknown execution mode {changes['default_execution_mode']}")
        changes["default_execution_mode"] = mode.value
    if "tool_modes" in changes:
        changes["tool_modes"] = _validated_tool_modes(settings, changes["tool_modes"])
    _fold_legacy_dpa_flags(changes, settings)
    payload = settings.to_dict()
    for key, value in changes.items():
        if key == "tool_modes":
            payload[key] = value
        elif isinstance(value, dict) and isinstance(payload.get(key), dict):
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    client = control.update_client_settings(
        ctx.client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    _log(
        ctx,
        AuditEventType.CONFIG_CHANGED,
        "SETTINGS_UPDATED",
        request={"changes": {k: v for k, v in requested.items() if v is not None}},
        result={"settings": client.settings.to_dict()},
    )
    return client.settings


# ---------------------------------------------------------- tool permissions


def set_tool_permission(ctx: TenantContext, *, tool: str, role: str, permission: str) -> dict[str, str]:
    """Override one role's permission for one tool, within the registry ceiling."""
    from bizos.rbac import permissions as perms

    parsed_role = Role.parse(role)
    parsed_perm = ToolPermission.parse(permission)
    if parsed_role is None or parsed_perm is None:
        raise AdminError("Unknown role or permission")
    with tenant_scope(ctx):
        try:
            perms.set_override(
                ctx, tool=tool, role=parsed_role, permission=parsed_perm, updated_by=ctx.user_id  # type: ignore[arg-type]
            )
        except KeyError as exc:
            raise AdminNotFound(str(exc)) from exc
        except PermissionError as exc:
            raise AdminForbidden(str(exc)) from exc
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            tool=tool,
            status="TOOL_PERMISSION_OVERRIDE",
            request={"role": role, "permission": permission},
        )
    return {"tool": tool, "role": str(parsed_role), "permission": str(parsed_perm)}


def clear_tool_permission(ctx: TenantContext, *, tool: str, role: str) -> None:
    from bizos.rbac import permissions as perms

    parsed = Role.parse(role)
    if parsed is None:
        raise AdminError("Unknown role")
    with tenant_scope(ctx):
        perms.clear_override(ctx, tool=tool, role=parsed)  # type: ignore[arg-type]
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            tool=tool,
            status="TOOL_PERMISSION_CLEARED",
            request={"role": role},
        )


# ------------------------------------------------------------------- domains


def toggle_domain(ctx: TenantContext, settings: ClientSettings, domain: str, enabled: bool) -> ClientSettings:
    """Enable or disable a domain pack for this client."""
    from bizos.domains.catalog import DOMAIN_NAMES

    if domain not in DOMAIN_NAMES:
        raise AdminNotFound(f"Unknown domain {domain}")
    if domain == "general" and not enabled:
        raise AdminError("The General pack provides organizational memory and cannot be disabled.")
    if enabled and not settings.domain_purchased(domain):
        raise AdminConflict(change_order_detail([domain]))
    current = set(settings.enabled_domains)
    current.add(domain) if enabled else current.discard(domain)
    settings.enabled_domains = sorted(current)
    client = control.update_client_settings(ctx.client_id, settings, updated_by=ctx.user_id)
    _log(ctx, AuditEventType.CONFIG_CHANGED, "DOMAIN_TOGGLED", domain=domain, request={"enabled": enabled})
    return client.settings


def record_change_order(
    ctx: TenantContext,
    settings: ClientSettings,
    domain: str,
    *,
    reference: str,
    note: str = "",
    enable: bool = True,
) -> ClientSettings:
    """FB-035: add a domain to the purchased package against a change order."""
    from bizos.domains.catalog import DOMAIN_NAMES

    if domain not in DOMAIN_NAMES:
        raise AdminNotFound(f"Unknown domain {domain}")
    reference = (reference or "").strip()
    if not reference or len(reference) > 200:
        raise AdminError("A change-order reference (1–200 characters) is required")
    settings.purchased_domains = sorted(set(settings.purchased_domains) | {domain})
    if enable:
        settings.enabled_domains = sorted(set(settings.enabled_domains) | {domain})
    orders = list((settings.onboarding or {}).get("change_orders") or [])
    orders.append(
        {"domain": domain, "reference": reference, "note": note, "by": ctx.user_id, "at": utcnow().isoformat()}
    )
    settings.onboarding = {**(settings.onboarding or {}), "change_orders": orders}
    client = control.update_client_settings(ctx.client_id, settings, updated_by=ctx.user_id)
    _log(
        ctx,
        AuditEventType.CHANGE_ORDER,
        "DOMAIN_PURCHASED",
        domain=domain,
        request={"reference": reference, "note": note},
    )
    return client.settings


# ---------------------------------------------------------------- onboarding


def apply_onboarding(ctx: TenantContext, settings: ClientSettings, **fields: Any) -> dict[str, Any]:
    """Apply guided first-client config: memory + domains + autonomy mode.

    ``baselines=None`` keeps the current baselines; an empty list clears them.
    """
    from bizos.onboarding import OnboardingProfile, apply_profile, get_profile

    enabled = fields.get("enabled_domains")
    unpaid = [d for d in enabled or [] if not settings.domain_purchased(d)]
    if unpaid:
        raise AdminConflict(change_order_detail(unpaid))
    baselines = fields.get("baselines")
    profile = OnboardingProfile(
        goals=list(fields.get("goals") or []),
        systems=list(fields.get("systems") or []),
        sops=list(fields.get("sops") or []),
        roles_notes=fields.get("roles_notes") or "",
        access_notes=fields.get("access_notes") or "",
        autonomy_mode=fields.get("autonomy_mode") or "WAIT_FOR_APPROVAL",
        enabled_domains=list(enabled or settings.enabled_domains),
        review_points=list(fields.get("review_points") or []),
        assessment=dict(fields.get("assessment") or {}),
        exclusions=list(fields.get("exclusions") or []),
        baselines=list(baselines) if baselines is not None else get_profile(settings).baselines,
    )
    with tenant_scope(ctx):
        result = apply_profile(client_id=ctx.client_id, profile=profile, settings=settings, ctx=ctx)
        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            status="ONBOARDING_APPLIED",
            request={
                "goals": len(profile.goals),
                "systems": len(profile.systems),
                "sops": len(profile.sops),
                "autonomy_mode": profile.autonomy_mode,
            },
        )
    return result


def handoff_assessment(ctx: TenantContext, settings: ClientSettings, assessment: dict[str, Any]) -> dict[str, Any]:
    """Paste sale/assessment JSON → prefill First-client setup (FB-043)."""
    from bizos.insights import handoff_assessment as handoff

    if not assessment:
        raise AdminError("assessment JSON object is required")
    result = handoff(client_id=ctx.client_id, settings=settings, assessment=assessment, ctx=ctx, source="manual")
    _log(
        ctx,
        AuditEventType.CONFIG_CHANGED,
        "ASSESSMENT_HANDOFF",
        request={"keys": sorted(assessment.keys())[:20], "source": "manual"},
    )
    return result


# ------------------------------------------------------------------ insights


def save_baselines(ctx: TenantContext, settings: ClientSettings, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from bizos.insights import save_baselines as save

    try:
        saved = save(client_id=ctx.client_id, settings=settings, baselines=items, ctx=ctx)
    except ValueError as exc:
        raise AdminError(str(exc)) from exc
    _log(ctx, AuditEventType.CONFIG_CHANGED, "BASELINES_UPDATED", request={"count": len(saved)})
    return saved


def dismiss_alert(ctx: TenantContext, settings: ClientSettings, alert_id: str) -> list[dict[str, Any]]:
    from bizos.insights import dismiss_alert as dismiss

    try:
        return dismiss(client_id=ctx.client_id, settings=settings, alert_id=alert_id, ctx=ctx)
    except KeyError as exc:
        raise AdminNotFound(f"Unknown alert {exc}") from exc


# -------------------------------------------------------------------- launch


def mark_launch_item(
    ctx: TenantContext, settings: ClientSettings, *, item_id: str, status: str = "done", waive_reason: str = ""
) -> dict[str, Any]:
    from bizos.launch import mark_item

    try:
        return mark_item(
            client_id=ctx.client_id,
            settings=settings,
            item_id=item_id,
            status=status,
            actor=ctx.user_id,
            waive_reason=waive_reason,
        )
    except ValueError as exc:
        raise AdminError(str(exc)) from exc


def launch_sign_off(ctx: TenantContext, settings: ClientSettings, *, force: bool = False) -> dict[str, Any]:
    from bizos.launch import sign_off

    try:
        result = sign_off(client_id=ctx.client_id, settings=settings, actor=ctx.user_id, force=force)
    except ValueError as exc:
        raise AdminConflict(str(exc)) from exc
    _log(ctx, AuditEventType.CONFIG_CHANGED, "LAUNCH_SIGNED_OFF", request={"force": force})
    return result


# ------------------------------------------------------------------- pricing


def save_resale_rules(ctx: TenantContext, settings: ClientSettings, rules: dict[str, Any]) -> dict[str, Any]:
    from bizos.pricing import save_rules

    try:
        saved = save_rules(client_id=ctx.client_id, settings=settings, rules=rules, ctx=ctx)
    except ValueError as exc:
        raise AdminError(str(exc)) from exc
    _log(
        ctx,
        AuditEventType.CONFIG_CHANGED,
        "RESALE_RULES_UPDATED",
        request={
            "customer_price_usd": saved["customer_price_usd"],
            "margin_percent": saved["margin_percent"],
            "quote_status": saved["quote_status"],
            "reconfirm_needed": saved.get("reconfirm_needed", False),
        },
    )
    return saved


def consult_quote(
    ctx: TenantContext,
    settings: ClientSettings,
    *,
    domains: list[str],
    autonomy: str = "WAIT_FOR_APPROVAL",
    integrations: int = 0,
    discount_percent: float = 0.0,
    label: str = "",
    save: bool = False,
) -> dict[str, Any]:
    """FB-049: calculate (and optionally save) a consult quote from face rules."""
    from bizos.pricing import calculate_consult_quote, save_consult_quote

    if not 0 <= integrations <= 50:
        raise AdminError("integrations must be between 0 and 50")
    if not 0 <= discount_percent <= 100:
        raise AdminError("discount_percent must be between 0 and 100")
    if len(label or "") > 200:
        raise AdminError("label must be at most 200 characters")
    try:
        quote = calculate_consult_quote(
            settings,
            domains=domains,
            autonomy=autonomy,
            integrations=integrations,
            discount_percent=discount_percent,
        )
    except ValueError as exc:
        raise AdminError(str(exc)) from exc
    saved = None
    if save:
        try:
            saved = save_consult_quote(client_id=ctx.client_id, settings=settings, quote=quote, ctx=ctx, label=label)
        except ValueError as exc:
            raise AdminError(str(exc)) from exc
        _log(
            ctx,
            AuditEventType.CONFIG_CHANGED,
            "CONSULT_QUOTE_SAVED",
            request={
                "id": saved["id"],
                "customer_price_usd": saved["customer_price_usd"],
                "domains": saved["inputs"]["domains"],
                "placeholder": saved["placeholder"],
            },
        )
    return {"quote": quote, "saved": saved}
