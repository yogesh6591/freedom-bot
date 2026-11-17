"""Jeanne's targeting rules. One config per workspace; defaults live in code."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.prospecting import ProspectingRulesRow, ProspectingRulesVersion

logger = logging.getLogger(__name__)

STALE_MESSAGE = "Someone saved newer rules, reload"


@dataclass
class ProspectingCategory:
    name: str
    keywords: list[str] = field(default_factory=list)
    priority: int = 1


@dataclass
class ProspectingRules:
    version: int = 0
    discovery_enabled: bool = False
    outreach_enabled: bool = False
    country: str = "United States"
    categories: list[ProspectingCategory] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    employees_min: int = 25
    employees_max: int = 250
    revenue_min: int = 5_000_000
    revenue_max: int = 50_000_000
    revenue_sweet_min: int = 7_000_000
    revenue_sweet_max: int = 30_000_000
    revenue_is_soft: bool = True
    exclusions: list[str] = field(default_factory=list)
    discovery_daily_target: int = 5
    outreach_daily_cap: int = 5
    require_verified_email: bool = True
    pause_if_bounce_rate_percent: float = 5.0

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("version", None)
        return data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProspectingRulesError(Exception):
    def __init__(self, messages: list[str]) -> None:
        self.messages = [m for m in messages if m]
        super().__init__(self.messages[0] if self.messages else "Invalid prospecting rules")


class ProspectingStaleError(Exception):
    def __init__(self) -> None:
        super().__init__(STALE_MESSAGE)


def default_prospecting_rules(*, version: int = 0) -> ProspectingRules:
    return ProspectingRules(
        version=version,
        categories=[
            ProspectingCategory(name="Fractional CXO businesses", keywords=["fractional CXO", "fractional chief"], priority=1),
            ProspectingCategory(name="Consultants and advisory firms", keywords=["consultant", "advisory"], priority=2),
            ProspectingCategory(name="Agencies", keywords=["agency"], priority=3),
            ProspectingCategory(
                name="Psychology and behavioral-health group practices",
                keywords=["psychology", "behavioral health", "group practice"],
                priority=4,
            ),
        ],
        titles=["Founder", "Owner", "CEO", "President", "Managing Partner"],
        exclusions=[
            "generic small business",
            "trades",
            "home services",
            "contractors and construction",
            "restaurants",
            "retail",
            "law firms",
            "accounting firms",
        ],
    )


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _int(value: Any, default: int, *, field: str, errors: list[str]) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        errors.append(f"{field} must be a number.")
        return default
    if number < 0:
        errors.append(f"{field} must be 0 or more.")
    return number


def _float(value: Any, default: float, *, field: str, errors: list[str]) -> float:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field} must be a number.")
        return default
    if number < 0:
        errors.append(f"{field} must be 0 or more.")
    return number


def parse_and_validate(raw: dict[str, Any] | None, *, version: int = 0) -> ProspectingRules:
    """Merge raw input onto defaults, trim/de-dupe lists, and raise on bad values."""
    defaults = default_prospecting_rules(version=version)
    src = raw or {}
    errors: list[str] = []

    categories: list[ProspectingCategory] = []
    seen_names: set[str] = set()
    for item in src.get("categories") if isinstance(src.get("categories"), list) else defaults.categories:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name") or "").split())
        if not name:
            errors.append("Every category needs a name.")
            continue
        key = name.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        try:
            priority = int(float(item.get("priority") if item.get("priority") not in (None, "") else 1))
        except (TypeError, ValueError):
            errors.append(f"Priority for {name} must be a number.")
            priority = 1
        if priority < 0:
            errors.append(f"Priority for {name} must be 0 or more.")
        keywords = item.get("keywords") if isinstance(item.get("keywords"), list) else []
        categories.append(
            ProspectingCategory(name=name, keywords=_dedupe([str(k) for k in keywords]), priority=priority)
        )

    titles_src = src.get("titles") if isinstance(src.get("titles"), list) else defaults.titles
    exclusions_src = src.get("exclusions") if isinstance(src.get("exclusions"), list) else defaults.exclusions

    rules = ProspectingRules(
        version=version,
        discovery_enabled=_as_bool(src.get("discovery_enabled"), defaults.discovery_enabled),
        outreach_enabled=_as_bool(src.get("outreach_enabled"), defaults.outreach_enabled),
        country=" ".join(str(src.get("country") if src.get("country") is not None else defaults.country).split()),
        categories=categories,
        titles=_dedupe([str(t) for t in titles_src]),
        employees_min=_int(src.get("employees_min"), defaults.employees_min, field="Minimum employees", errors=errors),
        employees_max=_int(src.get("employees_max"), defaults.employees_max, field="Maximum employees", errors=errors),
        revenue_min=_int(src.get("revenue_min"), defaults.revenue_min, field="Minimum revenue", errors=errors),
        revenue_max=_int(src.get("revenue_max"), defaults.revenue_max, field="Maximum revenue", errors=errors),
        revenue_sweet_min=_int(
            src.get("revenue_sweet_min"), defaults.revenue_sweet_min, field="Sweet-spot revenue from", errors=errors
        ),
        revenue_sweet_max=_int(
            src.get("revenue_sweet_max"), defaults.revenue_sweet_max, field="Sweet-spot revenue to", errors=errors
        ),
        revenue_is_soft=_as_bool(src.get("revenue_is_soft"), defaults.revenue_is_soft),
        exclusions=_dedupe([str(x) for x in exclusions_src]),
        discovery_daily_target=_int(
            src.get("discovery_daily_target"), defaults.discovery_daily_target, field="Discovery daily target", errors=errors
        ),
        outreach_daily_cap=_int(
            src.get("outreach_daily_cap"), defaults.outreach_daily_cap, field="Outreach daily cap", errors=errors
        ),
        require_verified_email=_as_bool(src.get("require_verified_email"), defaults.require_verified_email),
        pause_if_bounce_rate_percent=_float(
            src.get("pause_if_bounce_rate_percent"),
            defaults.pause_if_bounce_rate_percent,
            field="Bounce-rate pause",
            errors=errors,
        ),
    )
    if not rules.country:
        errors.append("Country cannot be empty.")
    if rules.employees_min > rules.employees_max:
        errors.append("Minimum employees must be less than or equal to maximum employees.")
    if rules.revenue_min > rules.revenue_max:
        errors.append("Minimum revenue must be less than or equal to maximum revenue.")
    if rules.revenue_sweet_min > rules.revenue_sweet_max:
        errors.append("Sweet-spot revenue from must be less than or equal to sweet-spot revenue to.")
    if rules.pause_if_bounce_rate_percent > 100:
        errors.append("Bounce-rate pause must be 100 or less.")
    if rules.discovery_enabled:
        if not rules.categories:
            errors.append("Turn on discovery only when there is at least one category.")
        if not rules.titles:
            errors.append("Turn on discovery only when there is at least one title.")
    if errors:
        raise ProspectingRulesError(errors)
    return rules


def rules_from_payload(payload: dict[str, Any] | None, *, version: int) -> ProspectingRules:
    merged = default_prospecting_rules(version=version).to_payload()
    merged.update(payload or {})
    return parse_and_validate(merged, version=version)


def get_prospecting_rules(ctx: Any = None) -> ProspectingRules:
    """Typed config for the future discovery engine. Defaults if nothing is saved or DB is down."""
    del ctx  # reserved for workspace scoping; acquisition is one config per deployment today
    try:
        from acquisition.db.session import SessionLocal

        db = SessionLocal()
        try:
            return ProspectingRulesService(db).current()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prospecting rules unavailable, using defaults: %s", exc)
        return default_prospecting_rules(version=0)


def _audit(ctx: Any, before: dict[str, Any], after: dict[str, Any], action: str) -> None:
    if ctx is None:
        return
    try:
        from freedombot.audit import events as audit
        from freedombot.types import AuditEventType

        audit.log(
            AuditEventType.CONFIG_CHANGED,
            ctx=ctx,
            tool="prospecting_rules",
            status=action,
            request={"before": before},
            result={"after": after},
        )
    except Exception:  # noqa: BLE001
        logger.warning("prospecting rules audit write failed", exc_info=True)


class ProspectingRulesService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current(self) -> ProspectingRules:
        row = self.db.get(ProspectingRulesRow, 1)
        if row is None:
            return default_prospecting_rules(version=0)
        try:
            return rules_from_payload(row.payload, version=int(row.version or 0))
        except ProspectingRulesError:
            logger.warning("saved prospecting rules failed validation; using defaults")
            return default_prospecting_rules(version=int(row.version or 0))

    def history(self) -> list[dict[str, Any]]:
        rows = list(
            self.db.scalars(
                select(ProspectingRulesVersion)
                .where(ProspectingRulesVersion.rules_id == 1)
                .order_by(ProspectingRulesVersion.version.desc())
            ).all()
        )
        out = []
        for row in rows:
            out.append(
                {
                    "version": row.version,
                    "action": row.action,
                    "created_by": row.created_by,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "payload": deepcopy(row.payload or {}),
                }
            )
        return out

    def save(
        self,
        raw: dict[str, Any],
        *,
        expected_version: int,
        actor: str = "",
        action: str = "save",
        ctx: Any = None,
        note: str | None = None,
    ) -> ProspectingRules:
        before = self.current().to_dict()
        if int(self.current().version) != int(expected_version):
            raise ProspectingStaleError()
        parsed = parse_and_validate(raw, version=int(expected_version) + 1)
        row = self.db.get(ProspectingRulesRow, 1)
        payload = parsed.to_payload()
        if row is None:
            if int(expected_version) != 0:
                raise ProspectingStaleError()
            row = ProspectingRulesRow(id=1, version=parsed.version, payload=payload, updated_by=actor or None)
            self.db.add(row)
        else:
            if int(row.version) != int(expected_version):
                raise ProspectingStaleError()
            row.version = parsed.version
            row.payload = payload
            row.updated_by = actor or None
            self.db.add(row)
        self.db.flush()
        self.db.add(
            ProspectingRulesVersion(
                rules_id=row.id,
                version=parsed.version,
                action=action,
                payload=payload,
                created_by=actor or None,
                note=note,
            )
        )
        self.db.commit()
        self.db.refresh(row)
        after = rules_from_payload(row.payload, version=row.version).to_dict()
        _audit(ctx, before, after, action)
        return rules_from_payload(row.payload, version=row.version)

    def restore(
        self, version: int, *, expected_version: int, actor: str = "", ctx: Any = None
    ) -> ProspectingRules:
        snap = self.db.scalar(
            select(ProspectingRulesVersion).where(
                ProspectingRulesVersion.rules_id == 1,
                ProspectingRulesVersion.version == int(version),
            )
        )
        if snap is None:
            raise ProspectingRulesError([f"There is no saved version {version}."])
        return self.save(
            snap.payload or {},
            expected_version=expected_version,
            actor=actor,
            action="restore",
            ctx=ctx,
            note=f"Restored version {version}",
        )

    def reset(self, *, expected_version: int, actor: str = "", ctx: Any = None) -> ProspectingRules:
        return self.save(
            default_prospecting_rules().to_payload(),
            expected_version=expected_version,
            actor=actor,
            action="reset",
            ctx=ctx,
        )
