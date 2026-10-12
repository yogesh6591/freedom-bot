"""
First-client launch checklist (FB-045)
======================================

Operational sign-off before go-live. Automated tests cover isolation; this
checklist is the remaining human verification on the real first-client
deployment.
"""

from __future__ import annotations

from typing import Any, Optional

from bizos.control.models import ClientSettings
from bizos.onboarding import get_profile
from bizos.util.timeutil import utcnow

#: Items that must be true (or explicitly waived) before launch sign-off.
CHECKLIST: tuple[dict[str, str], ...] = (
    {
        "id": "isolation_tests",
        "title": "Automated isolation tests pass",
        "detail": "M01 / tenant-wall suite green in CI or local pytest.",
    },
    {
        "id": "assessment_handoff",
        "title": "Assessment handoff complete",
        "detail": "Checkout/contract or manual handoff has prefilled First-client setup.",
    },
    {
        "id": "baselines",
        "title": "Baseline metrics recorded",
        "detail": "At least one sold-workflow baseline is on the setup screen.",
    },
    {
        "id": "exclusions",
        "title": "Exclusions confirmed",
        "detail": "Out-of-scope items are written so the bot does not invent coverage.",
    },
    {
        "id": "order_dpa",
        "title": "Order/DPA regulated-data setting reviewed",
        "detail": "PHI / card / child / biometrics match the signed Order/DPA; OpenAI has no BAA.",
    },
    {
        "id": "roles_approvers",
        "title": "Roles and approvers live",
        "detail": "Client admin, operator, and approver can sign in on the real deployment.",
    },
    {
        "id": "smoke_chat",
        "title": "Smoke chat on production-like stack",
        "detail": "One grounded Q&A and one approval path exercised against the client DB.",
    },
    {
        "id": "sign_off",
        "title": "Owner sign-off",
        "detail": "Jeanne (or delegate) records launch approval for this client.",
    },
)


def _blob(settings: ClientSettings) -> dict[str, Any]:
    return dict((settings.onboarding or {}).get("launch") or {})


def evaluate_checklist(settings: ClientSettings) -> list[dict[str, Any]]:
    """Compute pass/pending for each checklist item from workspace state."""
    profile = get_profile(settings)
    onboarding = settings.onboarding or {}
    launch = _blob(settings)
    signed = bool(launch.get("signed_off_at"))
    waived = {str(x) for x in (launch.get("waived") or [])}

    def status(item_id: str, auto_ok: bool) -> str:
        if item_id in waived:
            return "waived"
        if item_id == "sign_off":
            return "done" if signed else "pending"
        if launch.get("items", {}).get(item_id) == "done":
            return "done"
        return "done" if auto_ok else "pending"

    handoff_ok = bool(onboarding.get("handoff_at") or profile.assessment)
    baselines = profile.baselines or list(onboarding.get("baselines") or [])
    baselines_ok = any(
        isinstance(b, dict) and (b.get("before_value") or b.get("workflow"))
        for b in baselines
    )
    exclusions_ok = bool(profile.exclusions)
    # Order/DPA is a human confirmation (BAA / regulated categories reviewed).
    order_auto = bool(launch.get("order_dpa_reviewed")) or bool(
        launch.get("items", {}).get("order_dpa") == "done"
    )

    auto = {
        "isolation_tests": bool(launch.get("items", {}).get("isolation_tests") == "done"),
        "assessment_handoff": handoff_ok,
        "baselines": baselines_ok,
        "exclusions": exclusions_ok,
        "order_dpa": order_auto,
        "roles_approvers": bool(launch.get("items", {}).get("roles_approvers") == "done"),
        "smoke_chat": bool(launch.get("items", {}).get("smoke_chat") == "done"),
        "sign_off": signed,
    }

    items: list[dict[str, Any]] = []
    for spec in CHECKLIST:
        item_id = spec["id"]
        st = status(item_id, auto.get(item_id, False))
        items.append({**spec, "status": st})
    return items


def checklist_summary(settings: ClientSettings) -> dict[str, Any]:
    items = evaluate_checklist(settings)
    launch = _blob(settings)
    # sign_off is the action, not a prerequisite — ready means everything else is done.
    pending = [i for i in items if i["status"] == "pending" and i["id"] != "sign_off"]
    return {
        "items": items,
        "ready": len(pending) == 0,
        "pending_count": len(pending),
        "signed_off_at": launch.get("signed_off_at"),
        "signed_off_by": launch.get("signed_off_by"),
        "note": (
            "Automated isolation tests are green in-repo. Remaining items need "
            "verification on the real first-client deployment before sign-off."
        ),
    }


def mark_item(
    *,
    client_id: str,
    settings: ClientSettings,
    item_id: str,
    status: str,
    actor: str,
    waive_reason: str = "",
) -> dict[str, Any]:
    """Mark a checklist item done/pending/waived."""
    from bizos.control import store as control
    from bizos.control.models import ClientSettings as CS

    known = {c["id"] for c in CHECKLIST}
    if item_id not in known:
        raise ValueError(f"Unknown checklist item: {item_id}")
    status = status.strip().casefold()
    if status not in {"done", "pending", "waived"}:
        raise ValueError("status must be done, pending, or waived")

    payload = settings.to_dict()
    onboarding = dict(payload.get("onboarding") or {})
    launch = dict(onboarding.get("launch") or {})
    items = dict(launch.get("items") or {})
    waived = [str(x) for x in (launch.get("waived") or [])]

    if status == "waived":
        if item_id not in waived:
            waived.append(item_id)
        items[item_id] = "waived"
        if waive_reason:
            notes = dict(launch.get("waive_notes") or {})
            notes[item_id] = waive_reason
            launch["waive_notes"] = notes
    else:
        waived = [x for x in waived if x != item_id]
        items[item_id] = status

    if item_id == "order_dpa" and status == "done":
        launch["order_dpa_reviewed"] = True

    launch["items"] = items
    launch["waived"] = waived
    launch["updated_at"] = utcnow().isoformat()
    launch["updated_by"] = actor
    onboarding["launch"] = launch
    payload["onboarding"] = onboarding
    updated = control.update_client_settings(
        client_id, CS.from_dict(payload), updated_by=actor
    )
    return checklist_summary(updated.settings)


def sign_off(
    *,
    client_id: str,
    settings: ClientSettings,
    actor: str,
    force: bool = False,
) -> dict[str, Any]:
    """Record owner launch sign-off when the checklist is ready (or force)."""
    from bizos.control import store as control
    from bizos.control.models import ClientSettings as CS

    summary = checklist_summary(settings)
    if not summary["ready"] and not force:
        raise ValueError(
            f"Checklist not ready ({summary['pending_count']} pending). "
            "Mark remaining items or pass force=true to record a conditional sign-off."
        )
    payload = settings.to_dict()
    onboarding = dict(payload.get("onboarding") or {})
    launch = dict(onboarding.get("launch") or {})
    items = dict(launch.get("items") or {})
    items["sign_off"] = "done"
    launch["items"] = items
    launch["signed_off_at"] = utcnow().isoformat()
    launch["signed_off_by"] = actor
    if force and not summary["ready"]:
        launch["conditional"] = True
    onboarding["launch"] = launch
    payload["onboarding"] = onboarding
    updated = control.update_client_settings(
        client_id, CS.from_dict(payload), updated_by=actor
    )
    return checklist_summary(updated.settings)
