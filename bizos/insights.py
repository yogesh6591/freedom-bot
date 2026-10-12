"""
First-client insights (FB-041, FB-043, FB-050)
=============================================

Thin, directional helpers for the 45-day window — not a full analytics engine.

* **FB-041** — rare inefficiency alerts (1–2 material wastes) with directional $.
* **FB-043** — post-sale assessment handoff → prefill onboarding fields.
* **FB-050** — before/after baseline metrics for one sold workflow (no ROI claim).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text

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
        "source": "demo",
        "access_area": "ops",
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
        "source": "demo",
        "access_area": "ops",
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


_DEMO_AREAS = {a["id"]: a.get("access_area") for a in DEFAULT_ALERTS}


def _with_area(alert: dict[str, Any]) -> dict[str, Any]:
    """Demo alerts stored before areas existed still carry their area."""
    if "access_area" not in alert and alert.get("id") in _DEMO_AREAS:
        alert = {**alert, "access_area": _DEMO_AREAS[alert["id"]], "source": "demo"}
    return alert


def list_alerts(settings: ClientSettings) -> list[dict[str, Any]]:
    """Open + dismissed alerts recorded for *this* client.

    A client with nothing stored has no alerts. The Acme demo alerts are seeded
    explicitly by :func:`ensure_default_alerts`; they are never a fallback, or
    every new client would show another company's payroll findings.
    """
    raw = _blob(settings).get("inefficiency_alerts")
    if isinstance(raw, list):
        return [_with_area(dict(a)) for a in raw if isinstance(a, dict)]
    return []


def visible_to(alert: dict[str, Any], ctx: Optional[TenantContext]) -> bool:
    """M09-14: an alert about a restricted area is shown only to people who hold it."""
    area = alert.get("access_area")
    return ctx is None or not area or ctx.can_see_area(area)


def open_alerts(
    settings: ClientSettings, ctx: Optional[TenantContext] = None
) -> list[dict[str, Any]]:
    """Current alerts this caller may see. Held, resolved and dismissed ones are not current."""
    return [
        a
        for a in list_alerts(settings)
        if a.get("status", "open") == "open" and visible_to(a, ctx)
    ]


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


# ---------------------------------------------------------------------------
# M09-11 / M09-13: alerts detected from this client's own workspace data
# ---------------------------------------------------------------------------

#: At most this many alerts are current at once; the rest wait as "held".
MAX_OPEN_ALERTS = 2
#: An approval is "delayed" when it waited longer than this.
APPROVAL_DELAY_HOURS = 48
#: A pattern must recur this often before it is worth an alert.
MIN_OCCURRENCES = 3
APPROVAL_WINDOW_DAYS = 60
QUESTION_WINDOW_DAYS = 30
FAILURE_WINDOW_DAYS = 14
#: A dismissed alert is not raised again for this long, however often it recurs.
SNOOZE_DAYS = 30

_STOPWORDS = frozenset(
    "a an and are be can do does for from how i in is it me my of on or our please should "
    "the this to us we what when where which who why will with you your".split()
)


def _question_key(text_: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", (text_ or "").casefold()) if w not in _STOPWORDS]
    return " ".join(sorted(set(words))[:8])


def _area_of_text(text_: str) -> Optional[str]:
    from bizos.agents.grounding import _TOPICS

    lowered = (text_ or "").casefold()
    for area, patterns in _TOPICS.items():
        if any(re.search(p, lowered) for p, _ in patterns):
            return area
    return None


def _approval_delay_findings(ctx: TenantContext) -> list[dict[str, Any]]:
    from bizos.rbac.registry import get_spec
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT a.id, a.tool, r.created_at, COALESCE(r.decided_at, NOW()) AS ended, "
                "r.decided_by FROM approval_requests r JOIN actions a ON a.id = r.action_id "
                "WHERE r.created_at >= NOW() - make_interval(days => :d)"
            ),
            {"d": APPROVAL_WINDOW_DAYS},
        ).fetchall()
    delayed: dict[Optional[str], list[tuple[str, str, float]]] = {}
    for r in rows:
        hours = (r.ended - r.created_at).total_seconds() / 3600
        if hours <= APPROVAL_DELAY_HOURS:
            continue
        spec = get_spec(r.tool)
        area = spec.data_area if spec else None
        delayed.setdefault(area, []).append((r.id, r.tool, hours))
    out = []
    for area, items in delayed.items():
        if len(items) < MIN_OCCURRENCES:
            continue
        waits = sorted(h for _, _, h in items)
        median_days = waits[len(waits) // 2] / 24
        excess_days = sum(max(0.0, h - APPROVAL_DELAY_HOURS) for h in waits) / 24
        tools = sorted({t for _, t, _ in items})
        out.append(
            {
                "id": f"scan_approval_delay_{area or 'general'}",
                "kind": "approval_delay",
                "title": "Approvals repeatedly waiting more than 2 days",
                "detail": (
                    f"{len(items)} approval requests in the last {APPROVAL_WINDOW_DAYS} days "
                    f"waited over {APPROVAL_DELAY_HOURS // 24} days (median {median_days:.1f} days). "
                    f"Tools involved: {', '.join(tools)}."
                ),
                "directional_cost": (
                    f"~{excess_days:.0f} extra approval-days of waiting in {APPROVAL_WINDOW_DAYS} "
                    "days (directional estimate, not a saving)"
                ),
                "evidence": {"count": len(items), "action_ids": [i for i, _, _ in items][:10]},
                "score": len(items) * max(1.0, median_days),
                "access_area": area,
            }
        )
    return out


def _repeated_question_findings(ctx: TenantContext) -> list[dict[str, Any]]:
    from bizos import chat_history
    from bizos.tenancy.registry import workspace_connection

    chat_history.ensure_table(ctx)
    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT user_id, content FROM chat_messages WHERE role = 'user' "
                "AND created_at >= NOW() - make_interval(days => :d)"
            ),
            {"d": QUESTION_WINDOW_DAYS},
        ).fetchall()
    groups: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        key = _question_key(r.content)
        if len(key.split()) >= 2:
            groups.setdefault(key, []).append((r.user_id, r.content))
    out = []
    for key, asks in groups.items():
        people = {u for u, _ in asks}
        if len(asks) < MIN_OCCURRENCES or len(people) < 2:
            continue
        example = asks[0][1].strip()
        example = example[:120] + ("…" if len(example) > 120 else "")
        out.append(
            {
                "id": "scan_repeated_question_" + re.sub(r"[^a-z0-9]+", "_", key)[:60],
                "kind": "repeated_question",
                "title": "Same question keeps coming up",
                "detail": (
                    f"Asked {len(asks)} times by {len(people)} people in the last "
                    f"{QUESTION_WINDOW_DAYS} days, e.g. “{example}”. The answer may live in "
                    "someone's head — consider recording it as an SOP."
                ),
                "directional_cost": (
                    f"~{len(asks) * 15} minutes of repeated explaining in {QUESTION_WINDOW_DAYS} "
                    "days (directional estimate)"
                ),
                "evidence": {"count": len(asks), "people": len(people)},
                "score": float(len(asks) * len(people)),
                "access_area": _area_of_text(" ".join(c for _, c in asks)),
            }
        )
    return out


def _failure_findings(ctx: TenantContext) -> list[dict[str, Any]]:
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT id, tool FROM actions WHERE status = 'FAILED' "
                "AND updated_at >= NOW() - make_interval(days => :d)"
            ),
            {"d": FAILURE_WINDOW_DAYS},
        ).fetchall()
    if len(rows) < MIN_OCCURRENCES:
        return []
    tools = sorted({r.tool for r in rows})
    return [
        {
            "id": "scan_action_failures",
            "kind": "action_failures",
            "title": "Approved actions keep failing",
            "detail": (
                f"{len(rows)} approved actions failed in the last {FAILURE_WINDOW_DAYS} days "
                f"({', '.join(tools)}). Each failure means the work is redone by hand."
            ),
            "directional_cost": "Rework on every failed action (directional, not measured)",
            "evidence": {"count": len(rows), "action_ids": [r.id for r in rows][:10]},
            "score": float(len(rows) * 2),
            "access_area": None,
        }
    ]


def scan_alerts(
    *, client_id: str, settings: ClientSettings, ctx: TenantContext
) -> list[dict[str, Any]]:
    """Look for recurring, material waste in this client's own data.

    Low-noise by design: a pattern must recur, only the top ``MAX_OPEN_ALERTS``
    are current (the rest are held), a dismissed alert stays quiet for
    ``SNOOZE_DAYS``, and a detected alert whose pattern has gone away is marked
    resolved instead of lingering. Figures are directional, never promised.
    """
    findings = (
        _approval_delay_findings(ctx) + _repeated_question_findings(ctx) + _failure_findings(ctx)
    )
    now = utcnow()
    found_ids = {f["id"] for f in findings}
    stored = {a["id"]: a for a in list_alerts(settings)}

    for finding in findings:
        existing = stored.get(finding["id"])
        if existing and existing.get("status") == "dismissed":
            dismissed_at = existing.get("dismissed_at")
            if dismissed_at and datetime.fromisoformat(dismissed_at) > now - timedelta(days=SNOOZE_DAYS):
                continue
        stored[finding["id"]] = {
            **(existing or {}),
            **finding,
            "source": "scan",
            "detected_at": (existing or {}).get("detected_at") or now.isoformat(),
            "last_seen_at": now.isoformat(),
            "status": "open",
        }

    # A detected pattern that is no longer there is not current any more.
    for alert_id, alert in stored.items():
        if alert.get("source") == "scan" and alert_id not in found_ids and alert.get("status") in {"open", "held"}:
            alert["status"] = "resolved"
            alert["resolved_at"] = now.isoformat()

    # Keep it rare: only the most material detected alerts are current.
    candidates = sorted(
        (a for a in stored.values() if a.get("source") == "scan" and a.get("status") in {"open", "held"}),
        key=lambda a: -float(a.get("score") or 0),
    )
    demo_open = sum(1 for a in stored.values() if a.get("source") != "scan" and a.get("status", "open") == "open")
    slots = max(0, MAX_OPEN_ALERTS - demo_open)
    for index, alert in enumerate(candidates):
        alert["status"] = "open" if index < slots else "held"

    alerts = list(stored.values())
    _save_onboarding(
        client_id,
        settings,
        {"inefficiency_alerts": alerts, "alerts_scanned_at": now.isoformat()},
        actor=ctx.user_id,
    )
    return alerts


def last_scanned(settings: ClientSettings) -> Optional[str]:
    return _blob(settings).get("alerts_scanned_at")


def ensure_default_alerts(
    *, client_id: str, settings: ClientSettings, actor: str = "__bootstrap__"
) -> None:
    if _blob(settings).get("inefficiency_alerts"):
        return
    _save_onboarding(
        client_id, settings, {"inefficiency_alerts": list(DEFAULT_ALERTS)}, actor=actor
    )


def _metric_number(value: Any) -> Optional[float]:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    return float(text)


def compare(row: dict[str, Any]) -> dict[str, Any]:
    """M18-10/11: the before→after difference, when both values are recorded.

    Nothing is inferred: a missing value gives no comparison. Lower is not assumed
    to be better — the change is reported with its sign and the reader decides.
    """
    try:
        before = _metric_number(row.get("before_value"))
        after = _metric_number(row.get("after_value"))
    except ValueError:
        return {"change": None, "change_percent": None, "comparison": "not comparable"}
    if before is None or after is None:
        missing = "after" if before is not None else "baseline" if after is not None else "both values"
        return {"change": None, "change_percent": None, "comparison": f"waiting for {missing}"}
    change = round(after - before, 4)
    percent = round(change / before * 100, 1) if before else None
    unit = str(row.get("unit") or "").strip()
    sign = "+" if change > 0 else ""
    text_ = f"{sign}{change:g} {unit}".strip()
    if percent is not None:
        text_ += f" ({sign}{percent:g}%)"
    return {"change": change, "change_percent": percent, "comparison": text_}


def list_baselines(settings: ClientSettings) -> list[dict[str, Any]]:
    """Baselines recorded for this client, each with its before→after comparison.

    Empty until someone enters one.
    """
    raw = _blob(settings).get("baselines")
    if isinstance(raw, list):
        return [{**dict(b), **compare(b)} for b in raw if isinstance(b, dict)]
    return []


def _validated_value(label: str, metric: str, value: Any) -> str:
    """M18-16: before/after must be a number of zero or more, or left blank."""
    text_ = str(value or "").strip()
    if not text_:
        return ""
    try:
        number = float(text_.replace(",", ""))
    except ValueError as exc:
        raise ValueError(
            f"{label} for '{metric or 'metric'}' must be a number (put the unit in the unit field)"
        ) from exc
    if number < 0 or number != number:
        raise ValueError(f"{label} for '{metric or 'metric'}' cannot be negative")
    return text_


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
        metric = str(row.get("metric") or "").strip()
        cleaned.append(
            {
                "id": str(row.get("id") or f"baseline_{len(cleaned) + 1}"),
                "workflow": str(row.get("workflow") or "").strip(),
                "metric": metric,
                "before_value": _validated_value("Before", metric, row.get("before_value")),
                "after_value": _validated_value("After", metric, row.get("after_value")),
                "unit": str(row.get("unit") or "").strip(),
                "notes": str(row.get("notes") or "").strip(),
            }
        )
    _save_onboarding(
        client_id, settings, {"baselines": cleaned}, actor=ctx.user_id
    )
    return [{**row, **compare(row)} for row in cleaned]


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

    review_points = assessment.get("review_points") or []
    if not isinstance(review_points, list):
        review_points = []

    exclusions: list[str] = []
    for key in ("exclusions", "out_of_scope", "not_in_scope"):
        val = assessment.get(key)
        if isinstance(val, list):
            exclusions.extend(str(x).strip() for x in val if str(x).strip())
        elif isinstance(val, str) and val.strip():
            exclusions.append(val.strip())
    exclusions = list(dict.fromkeys(exclusions))[:20]

    baselines: list[dict[str, Any]] = []
    raw_baselines = assessment.get("baselines") or assessment.get("baseline_metrics") or []
    if isinstance(raw_baselines, list):
        for item in raw_baselines:
            if isinstance(item, dict) and (item.get("workflow") or item.get("metric")):
                baselines.append(dict(item))

    from bizos.onboarding import review_assessment

    review = review_assessment(assessment)
    # M11-08: nothing is filled in with a default. Missing answers become questions.
    return OnboardingProfile(
        goals=goals,
        systems=systems,
        sops=sops,
        roles_notes=roles,
        access_notes=str(assessment.get("access_notes") or ""),
        autonomy_mode=mode,
        review_points=[str(p) for p in review_points if p],
        assessment=assessment,
        open_questions=review["open_questions"],
        inconsistencies=review["inconsistencies"],
        unverified=review["unverified"],
        exclusions=exclusions,
        baselines=baselines,
    )


def assessment_fingerprint(assessment: dict[str, Any]) -> str:
    """Stable hash of an assessment, so a repeated sale event is recognised (FB-043)."""
    import hashlib
    import json

    canonical = json.dumps(assessment or {}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def handoff_assessment(
    *,
    client_id: str,
    settings: ClientSettings,
    assessment: dict[str, Any],
    ctx: TenantContext,
    source: str = "manual",
    event: str = "",
) -> dict[str, Any]:
    """Prefill onboarding from assessment JSON and persist the draft (FB-043).

    ``source`` is ``manual`` (paste), ``checkout``, or ``contract`` when the
    sale webhook starts handoff automatically.
    """
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
        open_questions=[],
        inconsistencies=incoming.inconsistencies,
        unverified=incoming.unverified,
        exclusions=incoming.exclusions or current.exclusions,
        baselines=incoming.baselines or current.baselines,
    )
    from bizos.onboarding import outstanding

    merged.open_questions = outstanding(incoming.open_questions, merged)
    payload = settings.to_dict()
    onboarding = {**(settings.onboarding or {}), **merged.to_dict()}
    onboarding["handoff_at"] = utcnow().isoformat()
    onboarding["handoff_by"] = ctx.user_id
    onboarding["handoff_source"] = (source or "manual").strip().casefold() or "manual"
    onboarding["handoff_fingerprint"] = assessment_fingerprint(assessment)
    if event:
        onboarding["handoff_event"] = str(event).strip().casefold()
    # Keep insights already stored.
    if "inefficiency_alerts" in (settings.onboarding or {}):
        onboarding["inefficiency_alerts"] = settings.onboarding["inefficiency_alerts"]
    if merged.baselines:
        onboarding["baselines"] = list(merged.baselines)
    elif "baselines" in (settings.onboarding or {}):
        onboarding["baselines"] = settings.onboarding["baselines"]
    payload["onboarding"] = onboarding
    updated = control.update_client_settings(
        client_id, ClientSettings.from_dict(payload), updated_by=ctx.user_id
    )
    auto = onboarding["handoff_source"] in {"checkout", "contract", "sale"}
    profile = get_profile(updated.settings).to_dict()
    profile["handoff_at"] = onboarding["handoff_at"]
    profile["handoff_source"] = onboarding["handoff_source"]
    profile["handoff_event"] = onboarding.get("handoff_event")
    return {
        "profile": profile,
        "handoff_at": onboarding["handoff_at"],
        "handoff_source": onboarding["handoff_source"],
        "needs_clarification": {
            "open_questions": merged.open_questions,
            "inconsistencies": merged.inconsistencies,
            "unverified": merged.unverified,
        },
        "message": (
            (
                "Checkout/contract handoff started automatically. Ask the client the open "
                "questions and settle any contradictions before you Apply."
                if auto
                else "Assessment handoff saved. Ask the client the open questions and settle any "
                "contradictions before you Apply."
            )
            if merged.open_questions or merged.inconsistencies
            else (
                "Checkout/contract handoff started automatically. Review First-client setup "
                "and Apply when ready."
                if auto
                else "Assessment handoff saved. Review First-client setup and Apply when ready."
            )
        ),
    }
