"""
Admin Tool Effects
==================

The Administration page, from chat: setup, alerts, launch, pricing, users,
policies, domain packs and tool permissions. Every change goes through
:mod:`bizos.admin_ops`, the same code the admin API routes run, so chat can do
nothing the page could not. The registry makes every tool here ADMIN-only.

Settings are re-read from the control plane on each call, so several changes in
one chat turn each see the result of the one before.
"""

from __future__ import annotations

import dataclasses
import secrets
from typing import Any, Optional

from bizos import admin_ops as ops
from bizos.connectors.base import ConnectorResult
from bizos.control import store as control
from bizos.tools.base import RunScope, effect
from bizos.types import Role


def _failure(exc: ops.AdminError) -> ConnectorResult:
    return ConnectorResult.failure(f"Not changed: {exc.message}")


def _settings(scope: RunScope):
    return ops.current_settings(scope.ctx)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _user_row(user) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "name": user.display_name,
        "email": user.email,
        "roles": sorted(str(r) for r in user.roles),
        "data_access": sorted(user.data_scopes),
        "status": user.status,
    }


def _find_user(scope: RunScope, ref: Any):
    """A user in this client by id, email, email name or display name."""
    wanted = str(ref or "").strip().casefold()
    if not wanted:
        raise ops.AdminError("Say which user — their email or name.")
    users = control.list_users(scope.ctx.client_id)
    for match in (
        lambda u: u.id.casefold() == wanted,
        lambda u: u.email.casefold() == wanted,
        lambda u: u.email.split("@")[0].casefold() == wanted,
        lambda u: (u.display_name or "").casefold() == wanted,
        lambda u: wanted in (u.display_name or "").casefold() or wanted in u.email.casefold(),
    ):
        found = [u for u in users if match(u)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = "; ".join(f"{u.display_name} <{u.email}>" for u in found[:8])
            raise ops.AdminError(f"More than one user matches {ref!r}; ask which: {names}")
    raise ops.AdminNotFound(f"No user matching {ref!r} in this workspace")


def _temporary_password() -> str:
    return secrets.token_urlsafe(12)


# --------------------------------------------------------------------- reads


@effect("admin_workspace_overview")
def admin_workspace_overview(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import open_alerts
    from bizos.launch import checklist_summary
    from bizos.onboarding import get_profile
    from bizos.pricing import get_rules

    settings = _settings(scope)
    users = control.list_users(scope.ctx.client_id)
    launch = checklist_summary(settings)
    rules = get_rules(settings)
    profile = get_profile(settings)
    data = {
        "company": settings.company_name,
        "workspace_mode": str(settings.mode),
        "purchased_domains": sorted(settings.purchased_domains),
        "enabled_domains": sorted(settings.enabled_domains),
        "order_dpa_allows": sorted(settings.order_dpa_allows),
        "llm_baa_covered": settings.llm_baa_covered,
        "approval_policy": dataclasses.asdict(settings.approval_policy),
        "risk_policy": dataclasses.asdict(settings.risk_policy),
        "tool_modes": dict(settings.tool_modes),
        "users": {"total": len(users), "active": sum(1 for u in users if u.status == "ACTIVE")},
        "setup": {
            "configured_at": profile.configured_at,
            "goals": len(profile.goals),
            "systems": len(profile.systems),
            "open_questions": profile.open_questions,
        },
        "launch": {"ready": launch["ready"], "pending_items": launch["pending_count"]},
        "open_alerts": len(open_alerts(settings, scope.ctx)),
        "pricing": {
            "configured": rules.get("configured"),
            "quote_status": rules.get("quote_status"),
            "customer_price_usd": rules.get("customer_price_usd"),
        },
    }
    return ConnectorResult(ok=True, data=data, summary=f"workspace overview for {settings.company_name}")


@effect("admin_list_users")
def admin_list_users(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.types import DATA_AREAS

    users = control.list_users(scope.ctx.client_id)
    return ConnectorResult(
        ok=True,
        data={
            "users": [_user_row(u) for u in users],
            "roles": [str(r) for r in Role],
            "data_areas": list(DATA_AREAS),
        },
        summary=f"{len(users)} user(s) in this workspace",
    )


@effect("admin_get_setup")
def admin_get_setup(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.onboarding import get_profile

    settings = _settings(scope)
    profile = get_profile(settings).to_dict()
    blob = settings.onboarding or {}
    profile["handoff_at"] = blob.get("handoff_at")
    profile["handoff_source"] = blob.get("handoff_source")
    profile["change_orders"] = blob.get("change_orders") or []
    return ConnectorResult(ok=True, data=profile, summary="first-client setup profile")


@effect("admin_list_alerts")
def admin_list_alerts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import last_scanned, list_alerts, list_baselines, visible_to

    settings = _settings(scope)
    alerts = [a for a in list_alerts(settings) if visible_to(a, scope.ctx)]
    return ConnectorResult(
        ok=True,
        data={
            "alerts": alerts,
            "scanned_at": last_scanned(settings),
            "baselines": list_baselines(settings),
            "note": "Directional only — not a full always-on waste engine. No guaranteed ROI.",
        },
        summary=f"{len(alerts)} alert(s); {sum(1 for a in alerts if a.get('status', 'open') == 'open')} open",
    )


@effect("admin_launch_checklist")
def admin_launch_checklist(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.launch import checklist_summary

    summary = checklist_summary(_settings(scope))
    return ConnectorResult(
        ok=True,
        data=summary,
        summary=(
            "launch checklist ready for sign-off"
            if summary["ready"]
            else f"launch checklist: {summary['pending_count']} item(s) pending"
        ),
    )


@effect("admin_pricing")
def admin_pricing(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.pricing import face_scope_config, get_rules, list_consult_quotes

    settings = _settings(scope)
    return ConnectorResult(
        ok=True,
        data={
            "resale_rules": get_rules(settings),
            "scope_weights": face_scope_config(settings),
            "saved_quotes": list_consult_quotes(settings)[:10],
        },
        summary="resale rules, consult scope weights and saved quotes",
    )


@effect("admin_list_domains")
def admin_list_domains(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.domains.packs import PACKS

    settings = _settings(scope)
    items = [
        {
            "domain": p.name,
            "title": p.title,
            "purchased": settings.domain_purchased(p.name),
            "enabled": p.name in settings.enabled_domains,
            "mode_ceiling": str(p.mode_ceiling) if p.mode_ceiling else None,
        }
        for p in PACKS
    ]
    return ConnectorResult(ok=True, data=items, summary=f"{len(items)} domain pack(s)")


@effect("admin_tool_permissions")
def admin_tool_permissions(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.rbac import permissions as perms
    from bizos.rbac.registry import TOOL_SPECS

    query = str(args.get("tool") or args.get("query") or "").strip().casefold()
    settings = _settings(scope)
    overrides = perms.load_overrides(scope.ctx, refresh=True)
    rows = []
    for spec in TOOL_SPECS:
        if query and query not in spec.name.casefold() and query not in spec.title.casefold() \
                and query != spec.category:
            continue
        rows.append(
            {
                "tool": spec.name,
                "title": spec.title,
                "category": spec.category,
                "write": spec.write,
                "permissions": {
                    str(r): str(perms.permission_for_role(spec, r, overrides)) for r in Role
                },
                "overridden_roles": sorted(str(r) for r in Role if (spec.name, str(r)) in overrides),
                "tool_mode": settings.tool_modes.get(spec.name),
            }
        )
    if not query and len(rows) > 40:
        rows = [
            {"tool": r["tool"], "write": r["write"], "overridden_roles": r["overridden_roles"],
             "tool_mode": r["tool_mode"]}
            for r in rows
        ]
    return ConnectorResult(
        ok=True,
        data={"tools": rows, "permission_values": ["DENIED", "DRAFT_ONLY", "APPROVE_ONLY",
                                                     "EXECUTE_AFTER_APPROVAL", "ALLOWED"]},
        summary=f"{len(rows)} tool(s)" + (f" matching {query!r}" if query else ""),
    )


# --------------------------------------------------------------------- setup


_SETUP_FIELDS = (
    "goals", "systems", "sops", "roles_notes", "access_notes", "autonomy_mode",
    "enabled_domains", "review_points", "exclusions", "baselines", "assessment",
)


@effect("admin_apply_setup")
def admin_apply_setup(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Apply first-client setup. Fields left out keep their current values."""
    from bizos.onboarding import get_profile

    settings = _settings(scope)
    current = get_profile(settings).to_dict()
    fields = {k: current.get(k) for k in _SETUP_FIELDS}
    fields["baselines"] = None
    # Applying setup also sets the live mode and packs; leaving them out must
    # not revert a change made since on the Policies or Domains tab.
    fields["autonomy_mode"] = settings.default_execution_mode
    fields["enabled_domains"] = list(settings.enabled_domains)
    for key in ("goals", "systems", "review_points", "exclusions", "enabled_domains"):
        if args.get(key) is not None:
            fields[key] = _list(args[key])
    for key in ("roles_notes", "access_notes", "autonomy_mode"):
        if args.get(key) is not None:
            fields[key] = str(args[key])
    if args.get("sops") is not None:
        fields["sops"] = [
            {"title": str(s.get("title") or ""), "content": str(s.get("content") or "")}
            for s in args["sops"] if isinstance(s, dict) and s.get("title")
        ]
    else:
        # Already-stored SOPs live in memory; re-applying must not write them again.
        fields["sops"] = []
    try:
        result = ops.apply_onboarding(scope.ctx, settings, **fields)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="applied first-client setup")


@effect("admin_handoff_assessment")
def admin_handoff_assessment(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    assessment = args.get("assessment")
    if not isinstance(assessment, dict):
        return ConnectorResult.failure("assessment must be a JSON object of the sale/assessment details")
    try:
        result = ops.handoff_assessment(scope.ctx, _settings(scope), assessment)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="prefilled setup from the assessment")


# ------------------------------------------------------------------ insights


@effect("admin_scan_alerts")
def admin_scan_alerts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import scan_alerts

    items = scan_alerts(client_id=scope.ctx.client_id, settings=_settings(scope), ctx=scope.ctx)
    open_items = [a for a in items if a.get("status", "open") == "open"]
    return ConnectorResult(
        ok=True,
        data=items,
        summary=f"scanned: {len(open_items)} open alert(s) of {len(items)} detected (directional only)",
    )


@effect("admin_dismiss_alert")
def admin_dismiss_alert(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    alert_id = str(args.get("alert_id") or "").strip()
    if not alert_id:
        return ConnectorResult.failure("Say which alert — call admin_list_alerts for ids.")
    try:
        ops.dismiss_alert(scope.ctx, _settings(scope), alert_id)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data={"alert_id": alert_id}, summary=f"dismissed alert {alert_id}")


@effect("admin_set_baselines")
def admin_set_baselines(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Add or update before/after baselines; others are kept unless removed."""
    from bizos.insights import list_baselines

    settings = _settings(scope)
    keep = ("id", "workflow", "metric", "before_value", "after_value", "unit", "notes")
    current = [{k: b.get(k) for k in keep if b.get(k) is not None} for b in list_baselines(settings)]
    remove = {str(r).casefold() for r in _list(args.get("remove"))}

    def key(row: dict[str, Any]) -> str:
        return str(row.get("id") or f"{row.get('workflow')}|{row.get('metric')}").casefold()

    def label(row: dict[str, Any]) -> str:
        return str(row.get("workflow") or "").casefold()

    merged = {key(b): b for b in current if key(b) not in remove and label(b) not in remove}
    for row in args.get("items") or []:
        if not isinstance(row, dict):
            continue
        clean = {k: row[k] for k in keep if row.get(k) not in (None, "")}
        existing = next(
            (k for k, b in merged.items() if clean.get("id") and b.get("id") == clean["id"]
             or (b.get("workflow"), b.get("metric")) == (clean.get("workflow"), clean.get("metric"))),
            None,
        )
        if existing:
            merged[existing] = {**merged[existing], **clean}
        else:
            merged[key(clean)] = clean
    try:
        saved = ops.save_baselines(scope.ctx, settings, list(merged.values()))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True, data=saved, summary=f"{len(saved)} baseline(s) saved (directional before/after only)"
    )


# -------------------------------------------------------------------- launch


@effect("admin_mark_launch_item")
def admin_mark_launch_item(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        return ConnectorResult.failure("Say which checklist item — call admin_launch_checklist for ids.")
    try:
        result = ops.mark_launch_item(
            scope.ctx,
            _settings(scope),
            item_id=item_id,
            status=str(args.get("status") or "done"),
            waive_reason=str(args.get("waive_reason") or ""),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary=f"launch item {item_id} marked {args.get('status') or 'done'}")


@effect("admin_launch_sign_off")
def admin_launch_sign_off(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        result = ops.launch_sign_off(scope.ctx, _settings(scope), force=bool(args.get("force")))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="launch signed off")


# ------------------------------------------------------------------- pricing


_RULE_FIELDS = ("customer_price_usd", "cepoch_cost_usd", "margin_percent", "quote_status", "notes", "scope_rules")


@effect("admin_set_resale_rules")
def admin_set_resale_rules(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    rules = {k: args[k] for k in _RULE_FIELDS if args.get(k) is not None}
    if not rules:
        return ConnectorResult.failure("Give at least one value to change (price, cost, margin, status, notes or scope weights).")
    try:
        saved = ops.save_resale_rules(scope.ctx, _settings(scope), rules)
    except ops.AdminError as exc:
        return _failure(exc)
    note = " — numbers changed after confirmation, so the quote needs reconfirming" if saved.get("reconfirm_needed") else ""
    return ConnectorResult(ok=True, data=saved, summary=f"resale rules saved ({saved.get('quote_status')}){note}")


@effect("admin_consult_quote")
def admin_consult_quote(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        result = ops.consult_quote(
            scope.ctx,
            _settings(scope),
            domains=_list(args.get("domains")),
            autonomy=str(args.get("autonomy") or "WAIT_FOR_APPROVAL"),
            integrations=int(args.get("integrations") or 0),
            discount_percent=float(args.get("discount_percent") or 0),
            label=str(args.get("label") or ""),
            save=bool(args.get("save")),
        )
    except (TypeError, ValueError):
        return ConnectorResult.failure("integrations must be a whole number and discount_percent a number")
    except ops.AdminError as exc:
        return _failure(exc)
    quote = result["quote"]
    return ConnectorResult(
        ok=True,
        data=result,
        summary=(
            f"consult quote {quote.get('customer_price_usd')} USD"
            + (f", saved as {result['saved']['id']}" if result.get("saved") else ", not saved")
        ),
    )


# --------------------------------------------------------------------- users


@effect("admin_create_user")
def admin_create_user(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    password = _temporary_password()
    try:
        user = ops.create_user(
            scope.ctx,
            email=str(args.get("email") or ""),
            password=password,
            display_name=str(args.get("display_name") or args.get("name") or ""),
            roles=_list(args.get("roles")) or ["VIEWER"],
            data_scopes=_list(args.get("data_access") or args.get("data_scopes")),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={**_user_row(user), "temporary_password": password},
        summary=f"created {user.email} as {', '.join(sorted(str(r) for r in user.roles))}",
    )


@effect("admin_set_user_roles")
def admin_set_user_roles(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        user = _find_user(scope, args.get("user"))
        roles = ops.parse_roles(_list(args.get("roles")))
        if user.id == scope.ctx.user_id and Role.ADMIN not in roles:
            return ConnectorResult.failure(
                "Not changed: you cannot remove your own Admin role from chat. Ask another admin."
            )
        updated = ops.set_roles(scope.ctx, user.id, [str(r) for r in roles])
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data=_user_row(updated),
        summary=f"{updated.email} now holds {', '.join(sorted(str(r) for r in updated.roles))}",
    )


@effect("admin_set_user_access")
def admin_set_user_access(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Grant or revoke restricted data areas; ``data_access`` replaces, add/remove edit."""
    try:
        user = _find_user(scope, args.get("user"))
        if args.get("data_access") is not None:
            wanted = set(_list(args.get("data_access")))
        else:
            wanted = set(user.data_scopes)
        wanted |= set(_list(args.get("add")))
        wanted -= set(_list(args.get("remove")))
        updated = ops.set_scopes(scope.ctx, user.id, sorted(s.casefold() for s in wanted))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data=_user_row(updated),
        summary=f"{updated.email} data access: {', '.join(sorted(updated.data_scopes)) or 'general only'}",
    )


@effect("admin_set_user_status")
def admin_set_user_status(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    raw = str(args.get("status") or "").strip().upper()
    status = {"DISABLE": "SUSPENDED", "DISABLED": "SUSPENDED", "SUSPEND": "SUSPENDED",
              "ENABLE": "ACTIVE", "ENABLED": "ACTIVE", "REACTIVATE": "ACTIVE"}.get(raw, raw)
    try:
        user = _find_user(scope, args.get("user"))
        if user.id == scope.ctx.user_id and status != "ACTIVE":
            return ConnectorResult.failure("Not changed: you cannot suspend your own account from chat.")
        updated = ops.set_status(scope.ctx, user.id, status)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=_user_row(updated), summary=f"{updated.email} is now {updated.status}")


@effect("admin_reset_password")
def admin_reset_password(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    password = _temporary_password()
    try:
        user = _find_user(scope, args.get("user"))
        ops.set_password(scope.ctx, user.id, password)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"email": user.email, "temporary_password": password},
        summary=f"reset the password for {user.email}",
    )


# ------------------------------------------------------------------ policies


_POLICY_FIELDS = (
    "default_execution_mode", "order_dpa_allows", "llm_baa_covered", "approval_policy",
    "risk_policy", "memory_policy", "retention_policy",
)


@effect("admin_update_settings")
def admin_update_settings(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    changes: dict[str, Any] = {k: args[k] for k in _POLICY_FIELDS if args.get(k) is not None}
    if "order_dpa_allows" in changes:
        changes["order_dpa_allows"] = [s.casefold() for s in _list(changes["order_dpa_allows"])]
    for key in ("approval_policy", "risk_policy", "memory_policy", "retention_policy"):
        if key in changes and not isinstance(changes[key], dict):
            return ConnectorResult.failure(f"{key} must be an object of the fields to change")
    if not changes:
        return ConnectorResult.failure("Say which setting to change.")
    try:
        updated = ops.update_settings(scope.ctx, _settings(scope), changes)
    except ops.AdminError as exc:
        return _failure(exc)
    shown = {k: updated.to_dict().get(k) for k in changes}
    return ConnectorResult(ok=True, data=shown, summary=f"updated {', '.join(sorted(changes))}")


@effect("admin_toggle_domain")
def admin_toggle_domain(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    domain = str(args.get("domain") or "").strip().casefold()
    enabled = bool(args.get("enabled", True))
    try:
        updated = ops.toggle_domain(scope.ctx, _settings(scope), domain, enabled)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"domain": domain, "enabled": enabled, "enabled_domains": updated.enabled_domains},
        summary=f"{domain} pack {'enabled' if enabled else 'disabled'}",
    )


@effect("admin_record_change_order")
def admin_record_change_order(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    domain = str(args.get("domain") or "").strip().casefold()
    try:
        updated = ops.record_change_order(
            scope.ctx,
            _settings(scope),
            domain,
            reference=str(args.get("reference") or ""),
            note=str(args.get("note") or ""),
            enable=bool(args.get("enable", True)),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"purchased_domains": updated.purchased_domains, "enabled_domains": updated.enabled_domains},
        summary=f"recorded change order {args.get('reference')} adding the {domain} pack",
    )


# ---------------------------------------------------------------------- tools


def _is_default(value: Optional[str]) -> bool:
    return str(value or "").strip().casefold() in {"", "default", "none", "clear", "reset", "inherit"}


@effect("admin_set_tool_permission")
def admin_set_tool_permission(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    tool = str(args.get("tool") or "").strip()
    role = str(args.get("role") or "").strip()
    permission = args.get("permission")
    try:
        if _is_default(permission):
            ops.clear_tool_permission(scope.ctx, tool=tool, role=role)
            return ConnectorResult(
                ok=True, data={"tool": tool, "role": role.upper(), "permission": "default"},
                summary=f"{tool} for {role.upper()} reset to the registry default",
            )
        result = ops.set_tool_permission(scope.ctx, tool=tool, role=role, permission=str(permission))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True, data=result, summary=f"{tool} for {result['role']} is now {result['permission']}"
    )


@effect("admin_set_tool_mode")
def admin_set_tool_mode(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    tool = str(args.get("tool") or "").strip()
    mode = args.get("mode")
    try:
        updated = ops.update_settings(
            scope.ctx, _settings(scope), {"tool_modes": {tool: None if _is_default(mode) else str(mode)}}
        )
    except ops.AdminError as exc:
        return _failure(exc)
    current = updated.tool_modes.get(tool)
    return ConnectorResult(
        ok=True,
        data={"tool": tool, "mode": current},
        summary=f"{tool} now {'runs in ' + current + ' mode' if current else 'follows the workspace mode'}",
    )
