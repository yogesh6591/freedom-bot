"""
First-client onboarding / configuration (FB-044)
================================================

Guided post-sale setup: goals, systems, SOPs, access/autonomy, and optional
assessment payload. Applying a profile writes org memory and updates client
settings (domains + execution mode).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from bizos.control import store as control
from bizos.control.models import ClientSettings
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES
from bizos.memory import store as memory
from bizos.tenancy.context import TenantContext
from bizos.types import ExecutionMode, MemoryCategory, SourceType
from bizos.util.timeutil import utcnow


@dataclass
class OnboardingProfile:
    """What was sold / discovered, applied into the workspace."""

    goals: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    sops: list[dict[str, str]] = field(default_factory=list)  # {title, content, key?}
    roles_notes: str = ""
    access_notes: str = ""
    autonomy_mode: str = ExecutionMode.WAIT_FOR_APPROVAL.value
    enabled_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ENABLED_DOMAINS))
    review_points: list[str] = field(default_factory=list)
    assessment: dict[str, Any] = field(default_factory=dict)
    configured_at: Optional[str] = None
    configured_by: Optional[str] = None
    #: M11-08..10: what onboarding still has to ask the client.
    open_questions: list[str] = field(default_factory=list)
    #: M11-13: answers in the assessment that contradict each other.
    inconsistencies: list[str] = field(default_factory=list)
    #: M11-19: client beliefs recorded in the assessment, not verified facts.
    unverified: list[str] = field(default_factory=list)
    #: FB-044: what is explicitly out of scope for this engagement.
    exclusions: list[str] = field(default_factory=list)
    #: FB-044: baseline metrics captured at setup (also mirrored under onboarding.baselines).
    baselines: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "OnboardingProfile":
        data = dict(data or {})
        known = set(cls.__dataclass_fields__)
        kwargs = {k: v for k, v in data.items() if k in known}
        profile = cls(**kwargs)
        profile.enabled_domains = [
            d for d in (profile.enabled_domains or list(DEFAULT_ENABLED_DOMAINS)) if d in DOMAIN_NAMES
        ]
        if "general" not in profile.enabled_domains:
            profile.enabled_domains.insert(0, "general")
        mode = ExecutionMode.parse(profile.autonomy_mode, ExecutionMode.WAIT_FOR_APPROVAL)
        profile.autonomy_mode = str(mode)
        return profile


# ---------------------------------------------------------------------------
# Assessment review (M11-08, 09, 10, 13, 19)
# ---------------------------------------------------------------------------

#: Keys that describe the same quantity; differing numbers are a contradiction.
_SAME_QUANTITY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("employee count", ("headcount", "employees", "employee_count", "num_employees", "staff", "staff_count", "team_size", "fte", "ftes")),
    ("number of locations", ("locations", "sites", "offices", "warehouses", "branches")),
    ("number of users", ("users", "user_count", "seats")),
)

_ASSUMPTION_KEYS = (
    "assumption", "assumptions", "belief", "beliefs", "client_believes", "expectation",
    "expectations", "hypothesis", "hypotheses", "unverified",
)
_AUTHORITY_KEYS = (
    "approvers", "approver", "authority", "approval_authority", "decision_makers",
    "who_approves", "approval_limits", "signing_authority",
)
_PERMISSION_KEYS = ("roles", "roles_notes", "access_notes", "permissions", "access", "data_access")
_SYSTEM_KEYS = ("systems", "stack", "tools", "integrations")
_GOAL_KEYS = ("goals", "priorities", "outcomes")

#: Assessment findings kept on record (client-reported, but stated as findings).
FINDING_FIELDS: tuple[tuple[str, str], ...] = (
    ("pain", "Pain point"),
    ("primary_pain", "Pain point"),
    ("root_cause", "Root cause"),
    ("workflow", "Workflow"),
    ("impact", "Impact"),
    ("context", "Context"),
)


def _present(assessment: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = assessment.get(key)
        if isinstance(value, (list, dict)) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
    return False


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        import re

        m = re.fullmatch(r"\s*~?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*", value)
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def review_assessment(assessment: dict[str, Any]) -> dict[str, list[str]]:
    """What the assessment leaves open, contradicts, or merely assumes.

    Nothing here is filled in with a default: a missing answer becomes a
    question for the client, a contradiction is flagged for them to settle, and
    a belief stays a belief until someone confirms it.
    """
    assessment = dict(assessment or {})
    questions: list[str] = []
    if not _present(assessment, _GOAL_KEYS):
        questions.append("What outcomes should FreedomBot deliver first? (goals)")
    if not _present(assessment, _SYSTEM_KEYS):
        questions.append(
            "Which systems should FreedomBot work with — CRM, email, calendar, accounting, "
            "file storage? (systems)"
        )
    if not _present(assessment, _PERMISSION_KEYS):
        questions.append(
            "Who should see which restricted areas — finance, HR, salary, legal, executive? "
            "List each person and their access. (permissions)"
        )
    if not _present(assessment, _AUTHORITY_KEYS):
        questions.append(
            "Who may approve actions (emails, CRM changes, spend), and up to what limits? "
            "(approval authority)"
        )
    if not _present(assessment, ("autonomy_mode", "mode")):
        questions.append(
            "How much should FreedomBot do on its own — advise, draft, wait for approval, or "
            "act within limits? (autonomy)"
        )

    conflicts: list[str] = []
    lowered = {str(k).casefold(): v for k, v in assessment.items()}
    for label, keys in _SAME_QUANTITY:
        values = {k: _number(lowered[k]) for k in keys if k in lowered}
        numbers = {k: v for k, v in values.items() if v is not None}
        if len(set(numbers.values())) > 1:
            listed = ", ".join(f"{k} = {lowered[k]}" for k in numbers)
            conflicts.append(f"The {label} is given differently ({listed}). Which is right?")

    beliefs: list[str] = []
    for key in _ASSUMPTION_KEYS:
        value = lowered.get(key)
        if isinstance(value, str) and value.strip():
            beliefs.append(value.strip())
        elif isinstance(value, list):
            beliefs.extend(str(v).strip() for v in value if str(v).strip())
    return {"open_questions": questions, "inconsistencies": conflicts, "unverified": beliefs}


def outstanding(questions: list[str], profile: "OnboardingProfile") -> list[str]:
    """Drop questions the setup already answers (entered in the form, not the assessment)."""
    answered = {
        "(goals)": bool([g for g in profile.goals if str(g).strip()]),
        "(systems)": bool([x for x in profile.systems if str(x).strip()]),
        "(permissions)": bool(profile.roles_notes.strip() or profile.access_notes.strip()),
    }
    return [q for q in questions if not any(q.endswith(tag) and done for tag, done in answered.items())]


def get_profile(settings: ClientSettings) -> OnboardingProfile:
    """Load the first-client setup profile from client settings."""
    raw = getattr(settings, "onboarding", None)
    if isinstance(raw, OnboardingProfile):
        return raw
    blob = dict(raw) if isinstance(raw, dict) else {}
    profile = OnboardingProfile.from_dict(blob)
    if not profile.baselines and isinstance(blob.get("baselines"), list):
        profile.baselines = [b for b in blob["baselines"] if isinstance(b, dict)]
    if not profile.exclusions and isinstance(blob.get("exclusions"), list):
        profile.exclusions = [str(x).strip() for x in blob["exclusions"] if str(x).strip()]
    return profile


def apply_profile(
    *,
    client_id: str,
    profile: OnboardingProfile,
    settings: ClientSettings,
    ctx: TenantContext,
) -> dict[str, Any]:
    """Persist onboarding into settings + organizational memory."""
    profile = OnboardingProfile.from_dict(profile.to_dict())
    profile.configured_at = utcnow().isoformat()
    profile.configured_by = ctx.user_id

    written: list[dict[str, str]] = []

    for index, goal in enumerate(profile.goals):
        text = (goal or "").strip()
        if not text:
            continue
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key=f"goal_{index + 1}",
            title=f"Goal {index + 1}",
            content=text,
            tags=["strategy", "goal", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "goal", "id": item.id, "title": item.title})

    for index, system in enumerate(profile.systems):
        text = (system or "").strip()
        if not text:
            continue
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key=f"system_{index + 1}",
            title=f"Connected system: {text}",
            content=f"Client uses {text} as a connected business system.",
            tags=["systems", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "system", "id": item.id, "title": item.title})

    for sop in profile.sops:
        title = str(sop.get("title") or "").strip()
        content = str(sop.get("content") or "").strip()
        if not title or not content:
            continue
        key = str(sop.get("key") or "").strip() or None
        item = memory.put(
            category=MemoryCategory.SOP,
            memory_key=key,
            title=title,
            content=content,
            tags=["sop", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "sop", "id": item.id, "title": item.title})

    if profile.roles_notes.strip():
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="roles_notes",
            title="Roles and access notes",
            content=profile.roles_notes.strip(),
            tags=["access", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "roles", "id": item.id, "title": item.title})

    if profile.review_points:
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="review_points",
            title="Human review points",
            content="\n".join(f"- {p}" for p in profile.review_points if p),
            tags=["governance", "onboarding"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "review_points", "id": item.id, "title": item.title})

    if profile.exclusions:
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="exclusions",
            title="Out of scope / exclusions",
            content="\n".join(f"- {p}" for p in profile.exclusions if p),
            tags=["governance", "onboarding", "exclusions"],
            source_type=SourceType.MANUAL,
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "exclusions", "id": item.id, "title": item.title})

    if profile.assessment:
        import json

        from bizos.types import ApprovalStatus

        review = review_assessment(profile.assessment)
        profile.open_questions = outstanding(review["open_questions"], profile)
        profile.inconsistencies = review["inconsistencies"]
        profile.unverified = review["unverified"]

        # Findings the assessment states (pain, root cause, impact) stay on record
        # as individual facts, so they can be answered and corrected one by one.
        seen_titles: set[str] = set()
        for key, label in FINDING_FIELDS:
            value = profile.assessment.get(key)
            if not isinstance(value, str) or not value.strip() or label in seen_titles:
                continue
            seen_titles.add(label)
            item = memory.put(
                category=MemoryCategory.FACT,
                memory_key=f"assessment_{label.casefold().replace(' ', '_')}",
                title=f"Assessment: {label.casefold()}",
                content=value.strip(),
                tags=["assessment", "onboarding"],
                source_type=SourceType.DOCUMENT,
                settings=settings,
                ctx=ctx,
            )
            written.append({"kind": "finding", "id": item.id, "title": item.title})

        # M11-19: the client's beliefs are not facts. They wait, PENDING, for a
        # person to confirm them, and are never used as authoritative answers.
        if profile.unverified:
            item = memory.put(
                category=MemoryCategory.FACT,
                memory_key="assessment_unverified_assumptions",
                title="Unverified assumptions from assessment",
                content="\n".join(f"- (unverified) {b}" for b in profile.unverified),
                tags=["assessment", "onboarding", "unverified"],
                source_type=SourceType.DOCUMENT,
                approval_status=ApprovalStatus.PENDING,
                settings=settings,
                ctx=ctx,
            )
            written.append({"kind": "unverified", "id": item.id, "title": item.title})

        # The raw handoff is kept for reference but, being unvalidated, is PENDING.
        item = memory.put(
            category=MemoryCategory.FACT,
            memory_key="assessment_snapshot",
            title="Assessment handoff snapshot (unverified)",
            content=json.dumps(profile.assessment, indent=1, default=str)[:8000],
            tags=["assessment", "onboarding"],
            source_type=SourceType.DOCUMENT,
            approval_status=ApprovalStatus.PENDING,
            attributes={"assessment": profile.assessment},
            settings=settings,
            ctx=ctx,
        )
        written.append({"kind": "assessment", "id": item.id, "title": item.title})

    payload = settings.to_dict()
    onboarding = {**(settings.onboarding or {}), **profile.to_dict()}
    # FB-044: baselines live on the profile and under onboarding.baselines for
    # insights. The applied profile is authoritative, so removing a row sticks.
    onboarding["baselines"] = list(profile.baselines)
    payload["onboarding"] = onboarding
    # Only purchased domains can be switched on; the rest wait for a change order.
    payload["enabled_domains"] = [d for d in profile.enabled_domains if settings.domain_purchased(d)]
    payload["default_execution_mode"] = profile.autonomy_mode
    updated = control.update_client_settings(
        client_id,
        ClientSettings.from_dict(payload),
        updated_by=ctx.user_id,
    )
    return {
        "profile": profile.to_dict(),
        "settings": updated.settings.to_dict(),
        "memory_written": written,
        "needs_clarification": {
            "open_questions": profile.open_questions,
            "inconsistencies": profile.inconsistencies,
            "unverified": profile.unverified,
        },
    }
