"""
First-client insights (FB-041, FB-043, FB-050)
=============================================

Thin, directional helpers for the 45-day window — not a full analytics engine.

* **FB-041** — rare inefficiency alerts (1–2 material wastes) with directional $.
* **FB-043** — post-sale assessment handoff → prefill onboarding fields.
* **FB-050** — before/after baseline metrics for one sold workflow (no ROI claim).
"""

from __future__ import annotations

from typing import Any, Optional

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.onboarding import OnboardingProfile, get_profile
from bizos.tenancy.context import TenantContext
from bizos.types import ExecutionMode
from bizos.util.timeutil import utcnow

#: Default Acme alerts (FB-041). Rare, directional — not an always-on engine.
DEFAULT_ALERTS: list[dict[str, Any]] = [
    {
        "id": "alert_payroll_delay",
        "title": "Payroll still waiting on attendance",
        "detail": (
            "Month-end payroll draft often sits 2–3 days after attendance upload "
            "while exceptions are cleared by hand."
        ),
        "directional_cost": "~$1.2k/mo in delayed close + overtime chase",
        "status": "open",
    },
    {
        "id": "alert_expensive_on_routine",
        "title": "Senior time on routine SOP replay",
        "detail": (
            "Leads re-explain the payroll-from-attendance steps each month instead "
            "of reusing the recorded process."
        ),
        "directional_cost": "~4–6 senior hrs/mo on work the SOP already covers",
        "status": "open",
    },
]

#: Default Acme baseline for one sold workflow (FB-050). Directional only.
DEFAULT_BASELINES: list[dict[str, Any]] = [
    {
        "id": "baseline_payroll_attendance",
        "workflow": "Monthly payroll from attendance",
        "metric": "Cycle time (attendance uploaded → payslips ready for approver)",
        "before_value": "3.5",
        "after_value": "",
        "unit": "business days",
        "notes": "Baseline from assessment. After = fill once the bot-assisted path is live.",
    },
]


def _blob(settings: ClientSettings) -> dict[str, Any]:
    return dict(settings.onboarding or {})


def _save_onboarding(
    client_id: str, settings: ClientSettings, patch: dict[str, Any], *, actor: str
) -> ClientSettings:
    payload = settings.to_dict()
    onboarding = {**(settings.onboarding or {}), **patch}
    payload["onboarding"] = onboarding
    updated = control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=actor
    )
    return updated.settings


def list_alerts(settings: ClientSettings) -> list[dict[str, Any]]:
    """Open + dismissed alerts recorded for *this* client.

    A client with nothing stored has no alerts. The Acme demo alerts are seeded
    explicitly by :func:`ensure_default_alerts`; they are never a fallback, or
    every new client would show another company's payroll findings.
    """
    raw = _blob(settings).get("inefficiency_alerts")
    if isinstance(raw, list):
        return [dict(a) for a in raw if isinstance(a, dict)]
    return []


def open_alerts(settings: ClientSettings) -> list[dict[str, Any]]:
    return [a for a in list_alerts(settings) if a.get("status", "open") != "dismissed"]


def dismiss_alert(
    *, client_id: str, settings: ClientSettings, alert_id: str, ctx: TenantContext
) -> list[dict[str, Any]]:
    alerts = list_alerts(settings)
    found = False
    for alert in alerts:
        if alert.get("id") == alert_id:
            alert["status"] = "dismissed"
            alert["dismissed_at"] = utcnow().isoformat()
            found = True
    if not found:
        raise KeyError(alert_id)
    _save_onboarding(
        client_id, settings, {"inefficiency_alerts": alerts}, actor=ctx.user_id
    )
    return alerts


def ensure_default_alerts(
    *, client_id: str, settings: ClientSettings, actor: str = "__bootstrap__"
) -> None:
    if _blob(settings).get("inefficiency_alerts"):
        return
    _save_onboarding(
        client_id, settings, {"inefficiency_alerts": list(DEFAULT_ALERTS)}, actor=actor
    )


def list_baselines(settings: ClientSettings) -> list[dict[str, Any]]:
    """Baselines recorded for this client; empty until someone enters one."""
    raw = _blob(settings).get("baselines")
    if isinstance(raw, list):
        return [dict(b) for b in raw if isinstance(b, dict)]
    return []


def save_baselines(
    *,
    client_id: str,
    settings: ClientSettings,
    baselines: list[dict[str, Any]],
    ctx: TenantContext,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in baselines:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            {
                "id": str(row.get("id") or f"baseline_{len(cleaned) + 1}"),
                "workflow": str(row.get("workflow") or "").strip(),
                "metric": str(row.get("metric") or "").strip(),
                "before_value": str(row.get("before_value") or "").strip(),
                "after_value": str(row.get("after_value") or "").strip(),
                "unit": str(row.get("unit") or "").strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
        )
    _save_onboarding(
        client_id, settings, {"baselines": cleaned}, actor=ctx.user_id
    )
    return cleaned


def ensure_default_baselines(
    *, client_id: str, settings: ClientSettings, actor: str = "__bootstrap__"
) -> None:
    if _blob(settings).get("baselines"):
        return
    _save_onboarding(
        client_id, settings, {"baselines": list(DEFAULT_BASELINES)}, actor=actor
    )


def profile_from_assessment(assessment: dict[str, Any]) -> OnboardingProfile:
    """Map a sale/assessment JSON into onboarding fields (FB-043). Does not apply yet."""
    assessment = dict(assessment or {})
    goals: list[str] = []
    for key in ("goals", "priorities", "outcomes"):
        val = assessment.get(key)
        if isinstance(val, list):
            goals.extend(str(g).strip() for g in val if str(g).strip())
        elif isinstance(val, str) and val.strip():
            goals.append(val.strip())
    if not goals:
        for key in ("pain", "priority", "primary_pain", "opportunity"):
            if assessment.get(key):
                goals.append(str(assessment[key]).strip())
    goals = list(dict.fromkeys(goals))[:8]

    systems: list[str] = []
    for key in ("systems", "stack", "tools", "integrations"):
        val = assessment.get(key)
        if isinstance(val, list):
            systems.extend(str(s).strip() for s in val if str(s).strip())
        elif isinstance(val, str) and val.strip():
            systems.append(val.strip())
    systems = list(dict.fromkeys(systems))[:12]

    sops: list[dict[str, str]] = []
    raw_sops = assessment.get("sops") or assessment.get("workflows") or []
    if isinstance(raw_sops, list):
        for item in raw_sops:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("name") or "").strip()
                content = str(item.get("content") or item.get("body") or item.get("steps") or "").strip()
                if title and content:
                    sops.append({"title": title, "content": content, "key": str(item.get("key") or "")})
            elif isinstance(item, str) and item.strip():
                sops.append({"title": item.strip()[:80], "content": item.strip(), "key": ""})

    roles = str(
        assessment.get("roles_notes")
        or assessment.get("access_notes")
        or assessment.get("roles")
        or ""
    ).strip()
    if isinstance(assessment.get("roles"), list):
        roles = roles or "\n".join(str(r) for r in assessment["roles"])

    mode = str(assessment.get("autonomy_mode") or assessment.get("mode") or "").strip()
    parsed_mode = ExecutionMode.parse(mode, ExecutionMode.WAIT_FOR_APPROVAL) if mode else ExecutionMode.WAIT_FOR_APPROVAL
    mode = str(parsed_mode)

    review = assessment.get("review_points") or []
    if not isinstance(review, list):
        review = []

    return OnboardingProfile(
        goals=goals or ["Confirm goals from assessment with the client"],
        systems=systems or ["Workspace CRM", "Workspace Email", "n8n"],
        sops=sops,
        roles_notes=roles,
        access_notes=str(assessment.get("access_notes") or ""),
        autonomy_mode=mode,
        review_points=[str(p) for p in review if p],
        assessment=assessment,
    )


def handoff_assessment(
    *,
    client_id: str,
    settings: ClientSettings,
    assessment: dict[str, Any],
    ctx: TenantContext,
) -> dict[str, Any]:
    """Prefill onboarding from assessment JSON and persist the draft (FB-043)."""
    current = get_profile(settings)
    incoming = profile_from_assessment(assessment)
    # Prefer assessment-derived values; keep existing where assessment is empty.
    merged = OnboardingProfile(
        goals=incoming.goals or current.goals,
        systems=incoming.systems or current.systems,
        sops=incoming.sops or current.sops,
        roles_notes=incoming.roles_notes or current.roles_notes,
        access_notes=incoming.access_notes or current.access_notes,
        autonomy_mode=incoming.autonomy_mode or current.autonomy_mode,
        enabled_domains=current.enabled_domains,
        review_points=incoming.review_points or current.review_points,
        assessment=assessment,
        configured_at=current.configured_at,
        configured_by=current.configured_by,
    )
    payload = settings.to_dict()
    onboarding = {**(settings.onboarding or {}), **merged.to_dict()}
    onboarding["handoff_at"] = utcnow().isoformat()
    onboarding["handoff_by"] = ctx.user_id
    # Keep insights already stored.
    if "inefficiency_alerts" in (settings.onboarding or {}):
        onboarding["inefficiency_alerts"] = settings.onboarding["inefficiency_alerts"]
    if "baselines" in (settings.onboarding or {}):
        onboarding["baselines"] = settings.onboarding["baselines"]
    payload["onboarding"] = onboarding
    updated = control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    return {
        "profile": get_profile(updated.settings).to_dict(),
        "handoff_at": onboarding["handoff_at"],
        "message": "Assessment handoff saved. Review First-client setup and Apply when ready.",
    }
