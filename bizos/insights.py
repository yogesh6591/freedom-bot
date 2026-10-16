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
    """Alerts detected from *this* client's own data.

    A client with nothing stored has no alerts. Hand-written demo alerts from
    older versions are not shown: every alert must come from a scan.
    """
    raw = _blob(settings).get("inefficiency_alerts")
    if isinstance(raw, list):
        return [dict(a) for a in raw if isinstance(a, dict) and a.get("source") == "scan"]
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
#: Unapproved memory older than this is knowledge nobody can rely on yet.
PENDING_MEMORY_DAYS = 3
ACTION_WINDOW_DAYS = 30
#: Share of a question's key words a memory item must contain to count as answering it.
COVERAGE_RATIO = 0.6
#: Directional blended hourly cost, overridable per client in onboarding.hourly_rates.
DEFAULT_HOURLY_RATES = {"senior": 120.0, "staff": 45.0}
#: Directional minutes of human effort per occurrence.
MINUTES_PER_REPEAT_ANSWER = 15
MINUTES_PER_ROUTINE_ACTION = 10
MINUTES_PER_ROUTINE_APPROVAL = 5
SENIOR_ROLES = ("ADMIN",)

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


def _rates(settings: Optional[ClientSettings]) -> dict[str, float]:
    rates = dict(DEFAULT_HOURLY_RATES)
    custom = _blob(settings).get("hourly_rates") if settings else None
    if isinstance(custom, dict):
        for key in rates:
            try:
                value = float(custom.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                rates[key] = value
    return rates


def _dollars(minutes: float, rate: float) -> str:
    amount = minutes / 60 * rate
    if amount >= 1000:
        return f"~${amount / 1000:.1f}k"
    return f"~${amount:.0f}"


def _words(text_: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text_ or "").casefold()) if w not in _STOPWORDS}


def _approved_memory(ctx: TenantContext) -> list[tuple[str, set[str]]]:
    """Titles and words of every approved, current memory item (facts, SOPs, decisions)."""
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT i.title, v.content FROM memory_items i JOIN memory_versions v "
                "ON v.item_id = i.id WHERE v.status = 'ACTIVE' AND v.approval_status = 'APPROVED'"
            )
        ).fetchall()
    return [(r.title, _words(f"{r.title} {r.content}")) for r in rows]


def _covered_by(key: str, memory: list[tuple[str, set[str]]]) -> Optional[str]:
    words = set(key.split())
    if len(words) < 2:
        return None
    for title, mem_words in memory:
        if len(words & mem_words) / len(words) >= COVERAGE_RATIO:
            return title
    return None


def _repeated_question_findings(
    ctx: TenantContext, settings: Optional[ClientSettings] = None
) -> list[dict[str, Any]]:
    """Trapped knowledge: the same question from several people, with no approved answer in memory."""
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
    memory = _approved_memory(ctx) if groups else []
    rate = _rates(settings)["staff"]
    out = []
    for key, asks in groups.items():
        people = {u for u, _ in asks}
        if len(asks) < MIN_OCCURRENCES or len(people) < 2:
            continue
        # Memory already holds an approved answer: the assistant answers it, no waste.
        if _covered_by(key, memory):
            continue
        example = asks[0][1].strip()
        example = example[:120] + ("…" if len(example) > 120 else "")
        minutes = len(asks) * MINUTES_PER_REPEAT_ANSWER
        out.append(
            {
                "id": "scan_repeated_question_" + re.sub(r"[^a-z0-9]+", "_", key)[:60],
                "kind": "repeated_question",
                "title": "Same question keeps coming up",
                "detail": (
                    f"Asked {len(asks)} times by {len(people)} people in the last "
                    f"{QUESTION_WINDOW_DAYS} days, e.g. “{example}”. No approved fact or SOP "
                    "in memory answers it, so the answer lives in someone's head — consider "
                    "recording it as an SOP."
                ),
                "directional_cost": (
                    f"{_dollars(minutes, rate)} (~{minutes} min of repeated explaining) in "
                    f"{QUESTION_WINDOW_DAYS} days (directional estimate)"
                ),
                "evidence": {"count": len(asks), "people": len(people)},
                "score": float(len(asks) * len(people)),
                "access_area": _area_of_text(" ".join(c for _, c in asks)),
            }
        )
    return out


def _pending_memory_findings(ctx: TenantContext) -> list[dict[str, Any]]:
    """Knowledge recorded but never approved — or stuck in a conflict — so nobody can rely on it."""
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT i.id, i.title, i.access_area, v.created_at, "
                "(v.attributes ? 'conflict_with') AS conflict "
                "FROM memory_items i JOIN memory_versions v ON v.item_id = i.id "
                "WHERE v.status = 'ACTIVE' AND v.approval_status = 'PENDING' "
                "AND v.created_at <= NOW() - make_interval(days => :d)"
            ),
            {"d": PENDING_MEMORY_DAYS},
        ).fetchall()
    if len(rows) < MIN_OCCURRENCES:
        return []
    conflicts = sum(1 for r in rows if r.conflict)
    titles = ", ".join(sorted({r.title for r in rows})[:4])
    areas = {r.access_area for r in rows}
    return [
        {
            "id": "scan_pending_memory",
            "kind": "pending_memory",
            "title": "Recorded knowledge waiting on approval",
            "detail": (
                f"{len(rows)} memory items have waited over {PENDING_MEMORY_DAYS} days for "
                f"approval ({conflicts} of them conflicting policies), e.g. {titles}. Until "
                "someone approves them the assistant can only give them as estimates."
            ),
            "directional_cost": (
                "Answers on these topics stay estimates instead of facts (directional, not measured)"
            ),
            "evidence": {"count": len(rows), "conflicts": conflicts, "item_ids": [r.id for r in rows][:10]},
            "score": float(len(rows) + conflicts * 2),
            "access_area": next(iter(areas)) if len(areas) == 1 else None,
        }
    ]


def _senior_routine_findings(
    ctx: TenantContext, settings: Optional[ClientSettings] = None
) -> list[dict[str, Any]]:
    """Expensive people on routine work: senior roles repeatedly running the same low-risk tool."""
    from bizos.rbac.registry import get_spec
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT tool, COUNT(*) AS n, COUNT(DISTINCT requested_by) AS people "
                "FROM actions WHERE requested_by_role = ANY(:roles) AND risk_level = 'LOW' "
                "AND status <> 'CANCELLED' "
                "AND created_at >= NOW() - make_interval(days => :d) GROUP BY tool"
            ),
            {"roles": list(SENIOR_ROLES), "d": ACTION_WINDOW_DAYS},
        ).fetchall()
    rate = _rates(settings)["senior"]
    out = []
    for r in rows:
        if r.n < MIN_OCCURRENCES:
            continue
        spec = get_spec(r.tool)
        label = spec.title if spec else r.tool
        minutes = r.n * MINUTES_PER_ROUTINE_ACTION
        out.append(
            {
                "id": f"scan_senior_routine_{r.tool}",
                "kind": "senior_routine",
                "title": "Senior time on routine work",
                "detail": (
                    f"Admins ran “{label}” {r.n} times in the last {ACTION_WINDOW_DAYS} days. "
                    "It is low-risk and repeatable — an operator or the assistant within scope "
                    "could take it."
                ),
                "directional_cost": (
                    f"{_dollars(minutes, rate)} of senior time (~{minutes} min) in "
                    f"{ACTION_WINDOW_DAYS} days (directional estimate)"
                ),
                "evidence": {"count": r.n, "people": r.people, "tool": r.tool},
                "score": float(r.n) * 1.5,
                "access_area": spec.data_area if spec else None,
            }
        )
    return out


def _routine_approval_findings(
    ctx: TenantContext, settings: Optional[ClientSettings] = None
) -> list[dict[str, Any]]:
    """Delays from people approving low-risk actions one by one."""
    from bizos.tenancy.registry import workspace_connection

    with workspace_connection(ctx, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT a.tool, COUNT(*) AS n FROM approval_requests r "
                "JOIN actions a ON a.id = r.action_id WHERE a.risk_level = 'LOW' "
                "AND r.status = 'APPROVED' AND r.decided_at IS NOT NULL "
                "AND r.created_at >= NOW() - make_interval(days => :d) GROUP BY a.tool"
            ),
            {"d": ACTION_WINDOW_DAYS},
        ).fetchall()
    total = sum(r.n for r in rows)
    if total < MIN_OCCURRENCES * 2:
        return []
    rate = _rates(settings)["senior"]
    minutes = total * MINUTES_PER_ROUTINE_APPROVAL
    tools = ", ".join(sorted(r.tool for r in rows)[:4])
    return [
        {
            "id": "scan_routine_approvals",
            "kind": "routine_approvals",
            "title": "Low-risk actions all approved by hand",
            "detail": (
                f"{total} low-risk actions were approved one by one in the last "
                f"{ACTION_WINDOW_DAYS} days ({tools}). If these are routinely approved, raising "
                "the auto ceiling for these tools would remove the wait."
            ),
            "directional_cost": (
                f"{_dollars(minutes, rate)} of approver time (~{minutes} min) in "
                f"{ACTION_WINDOW_DAYS} days (directional estimate)"
            ),
            "evidence": {"count": total, "tools": [r.tool for r in rows]},
            "score": float(total),
            "access_area": None,
        }
    ]


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
        _approval_delay_findings(ctx)
        + _repeated_question_findings(ctx, settings)
        + _pending_memory_findings(ctx)
        + _senior_routine_findings(ctx, settings)
        + _routine_approval_findings(ctx, settings)
        + _failure_findings(ctx)
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
        (a for a in stored.values() if a.get("status") in {"open", "held"}),
        key=lambda a: -float(a.get("score") or 0),
    )
    for index, alert in enumerate(candidates):
        alert["status"] = "open" if index < MAX_OPEN_ALERTS else "held"

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


#: Alerts are rescanned on read once the last scan is older than this.
RESCAN_MINUTES = 15


def refresh_if_stale(
    *, client_id: str, settings: ClientSettings, ctx: TenantContext
) -> ClientSettings:
    """Rescan when the stored alerts are older than ``RESCAN_MINUTES``; return current settings."""
    scanned = last_scanned(settings)
    if scanned and datetime.fromisoformat(scanned) >= utcnow() - timedelta(minutes=RESCAN_MINUTES):
        return settings
    scan_alerts(client_id=client_id, settings=settings, ctx=ctx)
    return control.get_client(client_id).settings


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
