"""
Organizational Memory Store
===========================

Implements §2 and §3: categorized, source-aware, versioned memory that supports
**corrections without destroying history**.

The central invariant:

    A memory item has at most one ACTIVE version at any time, and a correction
    closes the old version and opens a new one **in a single transaction**.

That invariant is enforced by the database (a partial unique index on
``(item_id) WHERE status = 'ACTIVE'``), not just by this module. If a future code
path forgot to close the old version, the insert would fail rather than leaving
two conflicting "current" values — the silent-overwrite failure §28 forbids,
inverted into a loud one.

Retrieval prefers the latest **active and approved** version, and ranks explicit
human sources above AI inference (``SOURCE_TRUST``), so an inferred guess never
outranks something a person stated.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional, Sequence

from sqlalchemy import text

from bizos.memory.models import MemoryItem, MemoryVersion
from bizos.tenancy.context import TenantContext, current_context, require_context
from bizos.tenancy.registry import workspace_connection
from bizos.types import (
    ApprovalStatus,
    AuditEventType,
    DataClassification,
    MemoryCategory,
    MemoryStatus,
    Role,
    SourceType,
)
from bizos.util.ids import new_id, slugify
from bizos.util.timeutil import utcnow


class MemoryNotFound(LookupError):
    pass


class MemoryConflict(RuntimeError):
    """A write would have violated the one-active-version invariant."""


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _version_from_row(row: Any) -> MemoryVersion:
    return MemoryVersion(
        id=row.id,
        item_id=row.item_id,
        version_no=row.version_no,
        content=row.content,
        status=MemoryStatus.parse(row.status, MemoryStatus.ACTIVE),  # type: ignore[arg-type]
        attributes=row.attributes or {},
        source_type=SourceType.parse(row.source_type, SourceType.MANUAL),  # type: ignore[arg-type]
        source_id=row.source_id,
        created_by=row.created_by,
        updated_by=row.updated_by,
        confidence=float(row.confidence),
        approval_status=ApprovalStatus.parse(row.approval_status, ApprovalStatus.PENDING),  # type: ignore[arg-type]
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        effective_from=row.effective_from,
        effective_until=row.effective_until,
        superseded_by=row.superseded_by,
        supersedes=row.supersedes,
        correction_reason=row.correction_reason,
        corrected_by=row.corrected_by,
        corrected_at=row.corrected_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_from_row(row: Any, current: Optional[MemoryVersion] = None) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        category=MemoryCategory.parse(row.category, MemoryCategory.FACT),  # type: ignore[arg-type]
        memory_key=row.memory_key,
        title=row.title,
        domain=row.domain,
        tags=list(row.tags or []),
        classification=DataClassification.parse(row.classification, DataClassification.INTERNAL),  # type: ignore[arg-type]
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        current=current,
        access_area=getattr(row, "access_area", None),
    )


# ---------------------------------------------------------------------------
# Restricted areas (FB-037)
# ---------------------------------------------------------------------------


def _area_filter(tenant: Optional[TenantContext], alias: str = "i") -> tuple[str, dict[str, Any]]:
    """SQL that keeps restricted rows out of the result set entirely.

    Filtering happens in the query, not after it, so a restricted item cannot
    leak through a count, a ranking or a LIMIT slice.
    """
    areas = sorted(tenant.visible_areas) if tenant is not None else []
    return (
        f"({alias}.access_area IS NULL OR {alias}.access_area = ANY(:visible_areas))",
        {"visible_areas": areas},
    )


def _visible(tenant: Optional[TenantContext], row: Any) -> bool:
    area = getattr(row, "access_area", None)
    return not area or (tenant is not None and tenant.can_see_area(area))


# One record can mix open and restricted parts: a section wrapped in
# ``[[area:finance]] … [[/area]]`` is shown only to people who can see that area.
_SECTION = re.compile(r"\[\[area:\s*([a-z_]+)\s*\]\](.*?)\[\[/area\]\]", re.S | re.I)
_SECTION_TITLES = {
    "finance": "Finance",
    "ops": "Operations",
    "hr": "HR",
    "salary": "Salary / compensation",
    "legal": "Legal",
    "exec": "Executive / board",
}


def _can_see(tenant: Optional[TenantContext], area: str) -> bool:
    return tenant is not None and tenant.can_see_area(area.casefold())


def hidden_sections(content: str, tenant: Optional[TenantContext]) -> list[tuple[str, str]]:
    """``(area, section text)`` for every section of ``content`` the caller cannot see."""
    return [
        (m.group(1).casefold(), m.group(2))
        for m in _SECTION.finditer(content or "")
        if not _can_see(tenant, m.group(1))
    ]


def redact_sections(content: str, tenant: Optional[TenantContext]) -> str:
    """``content`` with restricted sections replaced by a notice naming the area."""
    if not content or "[[area:" not in content.casefold():
        return content

    # Readers who may see a section keep its markers, so an edit made from what
    # they read still carries the restriction.
    def replace(m: "re.Match[str]") -> str:
        area = m.group(1).casefold()
        if _can_see(tenant, area):
            return m.group(0)
        title = _SECTION_TITLES.get(area, area)
        return f"[Section restricted to the {title} area — not shown for your access]"

    return _SECTION.sub(replace, content)


def _check_section_write(
    tenant: TenantContext, new_content: str, current_content: Optional[str]
) -> None:
    """Nobody may overwrite (and so silently drop) a section they cannot see, or
    create a section for an area they cannot see."""
    if current_content and hidden_sections(current_content, tenant):
        raise PermissionError("not permitted to change a record with restricted sections")
    for area, _ in hidden_sections(new_content, tenant):
        raise PermissionError(f"not permitted to write {area} content")


def _redact_version(version: Optional[MemoryVersion], tenant: Optional[TenantContext]) -> None:
    if version is None:
        return
    version.content = redact_sections(version.content, tenant)
    previous = version.attributes.get("previous_value") if version.attributes else None
    if isinstance(previous, str):
        version.attributes["previous_value"] = redact_sections(previous, tenant)


def _redacted(item: MemoryItem, tenant: Optional[TenantContext]) -> MemoryItem:
    _redact_version(item.current, tenant)
    for version in item.versions or []:
        if version is not item.current:
            _redact_version(version, tenant)
    return item


# ---------------------------------------------------------------------------
# Conflicts (FB-036)
# ---------------------------------------------------------------------------


def _conflicting_items(conn: Any, topic: str, exclude_item: Optional[str], content: str) -> list[str]:
    """Approved, active items on the same topic whose value differs."""
    rows = conn.execute(
        text(
            "SELECT i.id FROM memory_items i JOIN memory_versions v ON v.item_id = i.id "
            "WHERE v.status = 'ACTIVE' AND v.approval_status = 'APPROVED' "
            "AND lower(v.attributes->>'topic') = lower(:t) "
            "AND (CAST(:x AS TEXT) IS NULL OR i.id <> :x) "
            "AND btrim(lower(v.content)) <> btrim(lower(:c))"
        ),
        {"t": topic, "x": exclude_item, "c": content},
    ).fetchall()
    return [r.id for r in rows]


def find_conflicts(*, ctx: Optional[TenantContext] = None) -> list[dict[str, Any]]:
    """Topics where active memory holds more than one differing value.

    These are never resolved by the agent: they are listed for a person to pick
    the authoritative value.
    """
    tenant = ctx or current_context()
    area_sql, area_params = _area_filter(tenant)
    with workspace_connection(tenant, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT lower(v.attributes->>'topic') AS topic, i.id, i.title, i.memory_key, "
                "i.category, v.id AS version_id, v.content, v.approval_status, v.created_by "
                "FROM memory_items i JOIN memory_versions v ON v.item_id = i.id "
                "WHERE v.status = 'ACTIVE' AND COALESCE(v.attributes->>'topic', '') <> '' "
                f"AND {area_sql} ORDER BY topic, i.updated_at"
            ),
            area_params,
        ).fetchall()
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(r.topic, []).append(
            {
                "item_id": r.id,
                "version_id": r.version_id,
                "title": r.title,
                "key": r.memory_key,
                "category": r.category,
                "value": r.content,
                "approval_status": r.approval_status,
                "recorded_by": r.created_by,
            }
        )
    out = []
    for topic, items in groups.items():
        values = {i["value"].strip().casefold() for i in items}
        if len(items) > 1 and len(values) > 1:
            out.append({"topic": topic, "items": items})
    return out


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _resolve_approval(
    source_type: SourceType,
    confidence: float,
    explicit: Optional[ApprovalStatus],
    ctx: TenantContext,
    settings: Any,
) -> ApprovalStatus:
    """Decide whether a new version starts APPROVED or PENDING.

    §3: AI-inferred information is not trusted equally with an explicitly
    approved fact. Unless the client turns the requirement off, anything the
    model inferred lands as PENDING and is excluded from authoritative
    retrieval until a human accepts it.
    """
    if explicit is not None:
        return explicit
    if source_type == SourceType.AI_INFERENCE:
        policy = getattr(settings, "memory_policy", None)
        if policy is None or policy.require_approval_for_inferred:
            return ApprovalStatus.PENDING
    return ApprovalStatus.APPROVED


def put(
    *,
    category: MemoryCategory,
    title: str,
    content: str,
    memory_key: Optional[str] = None,
    domain: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    classification: DataClassification = DataClassification.INTERNAL,
    attributes: Optional[dict[str, Any]] = None,
    source_type: SourceType = SourceType.MANUAL,
    source_id: Optional[str] = None,
    confidence: float = 1.0,
    approval_status: Optional[ApprovalStatus] = None,
    access_area: Optional[str] = None,
    settings: Any = None,
    ctx: Optional[TenantContext] = None,
) -> MemoryItem:
    """Create a memory item, or add a **new version** to an existing one.

    Never overwrites: if the ``(category, memory_key)`` already exists, the
    current version is superseded and a new one is opened, with the same
    provenance bookkeeping a correction gets.
    """
    tenant = ctx or require_context()
    title = (title or "").strip()
    content = content if content is not None else ""
    explicit_key = (str(memory_key).strip() if memory_key is not None else "")
    if explicit_key:
        key = explicit_key
    elif title:
        key = slugify(title, max_length=80)
    else:
        raise ValueError("memory items require a key or a non-empty title")
    if not str(content).strip():
        raise ValueError("memory items require non-empty content")
    now = utcnow()
    resolved_approval = _resolve_approval(source_type, confidence, approval_status, tenant, settings)
    area = (access_area or "").strip().casefold() or None
    if area is not None and not tenant.can_see_area(area):
        raise PermissionError(f"not permitted to write {area} content")
    attributes = dict(attributes or {})
    conflict_with: list[str] = []

    with workspace_connection(tenant) as conn:
        existing = conn.execute(
            text("SELECT * FROM memory_items WHERE category = :c AND memory_key = :k"),
            {"c": str(category), "k": key},
        ).first()
        if existing is not None and not _visible(tenant, existing):
            raise PermissionError("not permitted to change this restricted item")
        current_row = _current_version_row(conn, existing.id) if existing is not None else None
        _check_section_write(tenant, str(content), current_row.content if current_row else None)

        topic = str(attributes.get("topic") or "").strip()
        if topic:
            conflict_with = _conflicting_items(
                conn, topic, existing.id if existing is not None else None, str(content)
            )
            if conflict_with:
                # Two policies disagree: neither the agent nor this write gets to
                # pick. The new value waits for a person (FB-036).
                resolved_approval = ApprovalStatus.PENDING
                attributes["conflict_with"] = conflict_with

        if existing is None:
            item_id = new_id("mem")
            conn.execute(
                text(
                    "INSERT INTO memory_items (id, category, memory_key, title, domain, tags, "
                    "classification, access_area, created_by) "
                    "VALUES (:i, :c, :k, :t, :d, :g, :cl, :ar, :b)"
                ),
                {
                    "i": item_id,
                    "c": str(category),
                    "k": key,
                    "t": title,
                    "d": domain,
                    "g": list(tags or []),
                    "cl": str(classification),
                    "ar": area,
                    "b": tenant.user_id,
                },
            )
            version_no = 1
            supersedes = None
        else:
            item_id = existing.id
            current = _current_version_row(conn, item_id)
            # An archived item has no ACTIVE version; number after the highest
            # version ever written so re-adding it never collides.
            version_no = int(
                conn.execute(
                    text("SELECT COALESCE(MAX(version_no), 0) FROM memory_versions WHERE item_id = :i"),
                    {"i": item_id},
                ).scalar()
                or 0
            ) + 1
            supersedes = current.id if current else None
            if current is not None:
                _close_version(conn, current.id, now, superseded_by=None, actor=tenant.user_id)
            conn.execute(
                text("UPDATE memory_items SET title = :t, updated_at = NOW() WHERE id = :i"),
                {"t": title, "i": item_id},
            )

        version_id = new_id("mv")
        conn.execute(
            text(
                "INSERT INTO memory_versions (id, item_id, version_no, content, attributes, status, "
                "source_type, source_id, created_by, confidence, approval_status, approved_by, "
                "approved_at, effective_from, supersedes) VALUES "
                "(:i, :it, :n, :c, CAST(:a AS JSONB), 'ACTIVE', :st, :si, :b, :cf, :ap, :apb, :apa, "
                ":ef, :sup)"
            ),
            {
                "i": version_id,
                "it": item_id,
                "n": version_no,
                "c": content,
                "a": json.dumps(attributes or {}, default=str),
                "st": str(source_type),
                "si": source_id,
                "b": tenant.user_id,
                "cf": confidence,
                "ap": str(resolved_approval),
                "apb": tenant.user_id if resolved_approval == ApprovalStatus.APPROVED else None,
                "apa": now if resolved_approval == ApprovalStatus.APPROVED else None,
                "ef": now,
                "sup": supersedes,
            },
        )
        if supersedes:
            # Link the closed version forward to its replacement.
            conn.execute(
                text("UPDATE memory_versions SET superseded_by = :new WHERE id = :old"),
                {"new": version_id, "old": supersedes},
            )

    _audit(
        AuditEventType.MEMORY_CREATED,
        tenant,
        item_id=item_id,
        category=category,
        key=key,
        source_type=source_type,
        approval_status=resolved_approval,
    )
    if conflict_with:
        _audit(
            AuditEventType.MEMORY_CONFLICT,
            tenant,
            item_id=item_id,
            topic=attributes.get("topic"),
            conflict_with=",".join(conflict_with),
        )
    return get_item(item_id, ctx=tenant)


def correct(
    *,
    item_id: str,
    new_content: str,
    reason: str,
    source_type: SourceType = SourceType.MANUAL,
    source_id: Optional[str] = None,
    confidence: float = 1.0,
    attributes: Optional[dict[str, Any]] = None,
    approval_status: Optional[ApprovalStatus] = None,
    settings: Any = None,
    ctx: Optional[TenantContext] = None,
) -> MemoryItem:
    """Supersede the current value with a corrected one (§2 D).

    The old version becomes ``SUPERSEDED`` with ``effective_until`` set and
    ``superseded_by`` pointing at the replacement; the new version records who
    corrected it, when, why and from what source. Both happen in one
    transaction, so there is never a moment with zero or two active versions.
    """
    tenant = ctx or require_context()
    now = utcnow()
    resolved_approval = _resolve_approval(source_type, confidence, approval_status, tenant, settings)

    with workspace_connection(tenant) as conn:
        item = conn.execute(text("SELECT * FROM memory_items WHERE id = :i"), {"i": item_id}).first()
        if item is None or not _visible(tenant, item):
            raise MemoryNotFound(item_id)
        current = _current_version_row(conn, item_id)
        if current is None:
            raise MemoryConflict(f"{item_id} has no active version to correct")
        _check_section_write(tenant, str(new_content), current.content)

        previous_value = current.content
        _close_version(conn, current.id, now, superseded_by=None, actor=tenant.user_id)

        version_id = new_id("mv")
        merged = dict(attributes or {})
        merged.setdefault("corrects_version_id", current.id)
        merged.setdefault("previous_value", previous_value)
        merged.setdefault("reason", reason)

        conn.execute(
            text(
                "INSERT INTO memory_versions (id, item_id, version_no, content, attributes, status, "
                "source_type, source_id, created_by, updated_by, confidence, approval_status, "
                "approved_by, approved_at, effective_from, supersedes, correction_reason, "
                "corrected_by, corrected_at) VALUES "
                "(:i, :it, :n, :c, CAST(:a AS JSONB), 'ACTIVE', :st, :si, :b, :b, :cf, :ap, :apb, "
                ":apa, :ef, :sup, :cr, :cb, :ca)"
            ),
            {
                "i": version_id,
                "it": item_id,
                "n": current.version_no + 1,
                "c": new_content,
                "a": json.dumps(merged, default=str),
                "st": str(source_type),
                "si": source_id,
                "b": tenant.user_id,
                "cf": confidence,
                "ap": str(resolved_approval),
                "apb": tenant.user_id if resolved_approval == ApprovalStatus.APPROVED else None,
                "apa": now if resolved_approval == ApprovalStatus.APPROVED else None,
                "ef": now,
                "sup": current.id,
                "cr": reason,
                "cb": tenant.user_id,
                "ca": now,
            },
        )
        conn.execute(
            text("UPDATE memory_versions SET superseded_by = :new WHERE id = :old"),
            {"new": version_id, "old": current.id},
        )
        conn.execute(
            text("UPDATE memory_items SET updated_at = NOW() WHERE id = :i"), {"i": item_id}
        )

    _audit(
        AuditEventType.MEMORY_CORRECTED,
        tenant,
        item_id=item_id,
        reason=reason,
        previous_value=previous_value,
        new_value=new_content,
        source_type=source_type,
    )
    return get_item(item_id, ctx=tenant)


def approve_version(version_id: str, *, ctx: Optional[TenantContext] = None) -> MemoryVersion:
    """Mark a PENDING version as human-approved, making it authoritative."""
    tenant = ctx or require_context()
    with workspace_connection(tenant) as conn:
        pending = conn.execute(
            text(
                "SELECT v.attributes, i.access_area FROM memory_versions v "
                "JOIN memory_items i ON i.id = v.item_id WHERE v.id = :i"
            ),
            {"i": version_id},
        ).first()
        if pending is None or not _visible(tenant, pending):
            raise MemoryNotFound(version_id)
        if (pending.attributes or {}).get("conflict_with") and not tenant.at_least(Role.APPROVER):
            raise PermissionError("resolving a policy conflict requires an approver")
        result = conn.execute(
            text(
                "UPDATE memory_versions SET approval_status = 'APPROVED', approved_by = :u, "
                "approved_at = NOW(), updated_at = NOW() WHERE id = :i"
            ),
            {"u": tenant.user_id, "i": version_id},
        )
        if result.rowcount == 0:
            raise MemoryNotFound(version_id)
        row = conn.execute(
            text("SELECT * FROM memory_versions WHERE id = :i"), {"i": version_id}
        ).first()
        # Approving one side of a conflict is the person's resolution: the
        # competing values are superseded (kept in history), not left standing.
        losers = (row.attributes or {}).get("conflict_with") or []
        for item_id in losers:
            current = _current_version_row(conn, item_id)
            if current is not None:
                _close_version(conn, current.id, utcnow(), superseded_by=version_id, actor=tenant.user_id)
    _audit(AuditEventType.MEMORY_CORRECTED, tenant, version_id=version_id, approved=True)
    return _version_from_row(row)


def archive(item_id: str, *, reason: str, ctx: Optional[TenantContext] = None) -> MemoryItem:
    """Retire an item: its active version becomes ARCHIVED and stops being used.

    Nothing is deleted. The version history stays readable for audit, but an
    archived item no longer appears in lists, search, conflicts or answers.
    Recording the same key again later starts a fresh active version.
    """
    tenant = ctx or current_context()
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to remove a memory item")
    now = utcnow()
    with workspace_connection(tenant) as conn:
        row = conn.execute(text("SELECT * FROM memory_items WHERE id = :i"), {"i": item_id}).first()
        if row is None or not _visible(tenant, row):
            raise MemoryNotFound(item_id)
        current = _current_version_row(conn, item_id)
        if current is None:
            raise MemoryNotFound(item_id)
        if hidden_sections(current.content, tenant):
            # Part of the value is in an area this caller cannot see.
            raise PermissionError("not permitted to remove an item with restricted sections")
        conn.execute(
            text(
                "UPDATE memory_versions SET status = 'ARCHIVED', effective_until = :t, "
                "correction_reason = :r, corrected_by = :u, corrected_at = :t, "
                "updated_by = :u, updated_at = NOW() WHERE id = :v"
            ),
            {"t": now, "r": reason[:500], "u": tenant.user_id, "v": current.id},
        )
        conn.execute(text("UPDATE memory_items SET updated_at = NOW() WHERE id = :i"), {"i": item_id})
    _audit(
        AuditEventType.MEMORY_ARCHIVED,
        tenant,
        item_id=item_id,
        category=row.category,
        key=row.memory_key,
        reason=reason[:500],
    )
    return history(item_id, ctx=tenant)


def _current_version_row(conn: Any, item_id: str) -> Any:
    return conn.execute(
        text("SELECT * FROM memory_versions WHERE item_id = :i AND status = 'ACTIVE'"),
        {"i": item_id},
    ).first()


def _close_version(conn: Any, version_id: str, when: Any, *, superseded_by: Optional[str], actor: str) -> None:
    """Close an active version. The value itself is left untouched."""
    conn.execute(
        text(
            "UPDATE memory_versions SET status = 'SUPERSEDED', effective_until = :t, "
            "superseded_by = COALESCE(:sb, superseded_by), updated_by = :u, updated_at = NOW() "
            "WHERE id = :i"
        ),
        {"t": when, "sb": superseded_by, "u": actor, "i": version_id},
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_item(item_id: str, *, ctx: Optional[TenantContext] = None) -> MemoryItem:
    tenant = ctx or current_context()
    with workspace_connection(tenant, readonly=True) as conn:
        row = conn.execute(text("SELECT * FROM memory_items WHERE id = :i"), {"i": item_id}).first()
        if row is None or not _visible(tenant, row):
            raise MemoryNotFound(item_id)
        current = _current_version_row(conn, item_id)
    return _redacted(_item_from_row(row, _version_from_row(current) if current else None), tenant)


def get_by_key(
    category: MemoryCategory,
    memory_key: str,
    *,
    ctx: Optional[TenantContext] = None,
    include_archived: bool = False,
) -> Optional[MemoryItem]:
    """The item under ``memory_key``. An archived item reads as absent unless
    ``include_archived`` (seeding uses it so a removed item is not recreated)."""
    tenant = ctx or current_context()
    with workspace_connection(tenant, readonly=True) as conn:
        row = conn.execute(
            text("SELECT * FROM memory_items WHERE category = :c AND memory_key = :k"),
            {"c": str(category), "k": memory_key},
        ).first()
        if row is None or not _visible(tenant, row):
            return None
        current = _current_version_row(conn, row.id)
    if current is None and not include_archived:
        return None
    return _redacted(_item_from_row(row, _version_from_row(current) if current else None), tenant)


def history(item_id: str, *, ctx: Optional[TenantContext] = None) -> MemoryItem:
    """The item with every version it has ever had, newest first."""
    tenant = ctx or current_context()
    with workspace_connection(tenant, readonly=True) as conn:
        row = conn.execute(text("SELECT * FROM memory_items WHERE id = :i"), {"i": item_id}).first()
        if row is None or not _visible(tenant, row):
            raise MemoryNotFound(item_id)
        versions = conn.execute(
            text("SELECT * FROM memory_versions WHERE item_id = :i ORDER BY version_no DESC"),
            {"i": item_id},
        ).fetchall()
    parsed = [_version_from_row(v) for v in versions]
    item = _item_from_row(row, next((v for v in parsed if v.is_active), None))
    item.versions = parsed
    return _redacted(item, tenant)


def list_items(
    *,
    category: Optional[MemoryCategory] = None,
    domain: Optional[str] = None,
    include_unapproved: bool = True,
    limit: int = 200,
    offset: int = 0,
    ctx: Optional[TenantContext] = None,
) -> list[MemoryItem]:
    """List items with their current version."""
    clauses = ["v.status = 'ACTIVE'"]
    params: dict[str, Any] = {"limit": max(1, min(limit, 500)), "offset": max(0, offset)}
    if category is not None:
        clauses.append("i.category = :category")
        params["category"] = str(category)
    if domain:
        clauses.append("i.domain = :domain")
        params["domain"] = domain
    if not include_unapproved:
        clauses.append("v.approval_status = 'APPROVED'")
    tenant = ctx or current_context()
    area_sql, area_params = _area_filter(tenant)
    clauses.append(area_sql)
    params.update(area_params)

    with workspace_connection(tenant, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT i.*, v.id AS v_id FROM memory_items i "
                "JOIN memory_versions v ON v.item_id = i.id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY i.updated_at DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).fetchall()
        out: list[MemoryItem] = []
        for row in rows:
            version = conn.execute(
                text("SELECT * FROM memory_versions WHERE id = :i"), {"i": row.v_id}
            ).first()
            out.append(_redacted(_item_from_row(row, _version_from_row(version)), tenant))
    return out


def search(
    query: str,
    *,
    categories: Optional[Sequence[MemoryCategory]] = None,
    domain: Optional[str] = None,
    authoritative_only: bool = True,
    limit: int = 10,
    ctx: Optional[TenantContext] = None,
) -> list[MemoryItem]:
    """Find memory items matching ``query``.

    Only ACTIVE versions are candidates, so a superseded value can never come
    back as the answer. When ``authoritative_only`` (the default for anything the
    agent will state as fact), PENDING versions are excluded too, which is how
    §3's "do not treat AI-inferred information as equally trusted" is enforced at
    retrieval rather than merely recorded at write time.

    Ranking is exact-key first, then title, then body, then by source trust so a
    human-stated value outranks an inferred one, then by recency.
    """
    tenant = ctx or current_context()
    clauses = ["v.status = 'ACTIVE'"]
    params: dict[str, Any] = {"q": f"%{(query or '').strip()}%", "limit": max(1, min(limit, 100))}
    if authoritative_only:
        clauses.append("v.approval_status = 'APPROVED'")
    if categories:
        clauses.append("i.category = ANY(:categories)")
        params["categories"] = [str(c) for c in categories]
    if domain:
        clauses.append("(i.domain = :domain OR i.domain IS NULL)")
        params["domain"] = domain
    # The whole phrase, or any meaningful word in it: "Q3 goals and priorities"
    # must find a fact titled "Goal 1". Words also match tags.
    word_clauses = ["i.title ILIKE :q", "i.memory_key ILIKE :q", "v.content ILIKE :q", ":q = '%%'"]
    hit_terms: list[str] = []
    for n, word in enumerate(_search_words(query)):
        match = _word_match_sql(n, word, params)
        word_clauses.append(match)
        hit_terms.append(f"(CASE WHEN {match} THEN 1 ELSE 0 END)")
    clauses.append(f"({' OR '.join(word_clauses)})")
    # More of the question's words matched ranks higher, so "salary band engineer"
    # prefers the salary item over one that merely mentions an engineer.
    word_rank = f"({' + '.join(hit_terms)}) DESC, " if hit_terms else ""
    area_sql, area_params = _area_filter(tenant)
    clauses.append(area_sql)
    params.update(area_params)

    # Source trust as SQL so ordering happens in the database rather than after
    # a LIMIT, which would rank only an arbitrary slice.
    trust = (
        "CASE v.source_type WHEN 'MANUAL' THEN 1.0 WHEN 'DOCUMENT' THEN 0.9 "
        "WHEN 'CRM' THEN 0.8 WHEN 'ACCOUNTING' THEN 0.8 WHEN 'CALENDAR' THEN 0.7 "
        "WHEN 'GMAIL' THEN 0.6 WHEN 'SLACK' THEN 0.6 WHEN 'WORKFLOW' THEN 0.6 "
        "WHEN 'AI_INFERENCE' THEN 0.3 ELSE 0.5 END"
    )
    with workspace_connection(tenant, readonly=True) as conn:
        rows = conn.execute(
            text(
                f"SELECT i.*, v.id AS v_id FROM memory_items i "
                "JOIN memory_versions v ON v.item_id = i.id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY (CASE WHEN v.approval_status = 'APPROVED' THEN 0 ELSE 1 END), "
                "(CASE WHEN i.memory_key ILIKE :q THEN 0 WHEN i.title ILIKE :q THEN 1 "
                f"ELSE 2 END), {word_rank}({trust} * v.confidence) DESC, "
                "i.updated_at DESC LIMIT :limit"
            ),
            params,
        ).fetchall()
        out: list[MemoryItem] = []
        for row in rows:
            version = conn.execute(
                text("SELECT * FROM memory_versions WHERE id = :i"), {"i": row.v_id}
            ).first()
            out.append(_redacted(_item_from_row(row, _version_from_row(version)), tenant))
    if out and tenant is not None:
        _audit(AuditEventType.MEMORY_READ, tenant, query=query, hits=len(out))
    return out


def restricted_areas_matching(query: str, *, ctx: Optional[TenantContext] = None) -> list[str]:
    """Restricted areas holding approved items that match ``query`` but that the
    caller cannot see.

    Returns area names only — never a title, key or value — so the assistant can
    say "that is in the finance area, which you don't have access to" instead of
    implying the information does not exist.
    """
    return sorted(restricted_area_hits(query, ctx=ctx))


def restricted_area_hits(
    query: str,
    *,
    ctx: Optional[TenantContext] = None,
    common: Optional[set[str]] = None,
) -> dict[str, list[str]]:
    """``{area: [the user's own words that matched there]}`` for restricted areas
    the caller cannot see. Only the caller's words are returned, never content.

    ``common`` are words that already match records the caller *can* see
    ("draft", "approval"); like names, they count half and cannot flag an area
    on their own.
    """
    tenant = ctx or current_context()
    words = _search_words(query)
    if not words or tenant is None:
        return {}
    entities = _entity_words(query) | set(common or ())
    params: dict[str, Any] = {"visible_areas": sorted(tenant.visible_areas)}
    matches = [_word_match_sql(n, w, params) for n, w in enumerate(words)]
    with workspace_connection(tenant, readonly=True) as conn:
        rows = conn.execute(
            text(
                "SELECT i.access_area, i.title, i.memory_key, v.content, "
                "array_to_string(i.tags, ' ') AS tags FROM memory_items i "
                "JOIN memory_versions v ON v.item_id = i.id "
                "WHERE v.status = 'ACTIVE' AND v.approval_status = 'APPROVED' "
                "AND i.access_area IS NOT NULL "
                "AND NOT (i.access_area = ANY(:visible_areas)) "
                f"AND ({' OR '.join(matches)}) LIMIT 500"
            ),
            params,
        ).fetchall()
        area_sql, area_params = _area_filter(tenant)
        mixed = conn.execute(
            text(
                "SELECT i.title, i.memory_key, v.content FROM memory_items i "
                "JOIN memory_versions v ON v.item_id = i.id "
                "WHERE v.status = 'ACTIVE' AND v.approval_status = 'APPROVED' "
                f"AND v.content ILIKE '%%[[area:%%' AND {area_sql} LIMIT 200"
            ),
            area_params,
        ).fetchall()
    candidates = [
        (r.access_area, " ".join(str(x or "") for x in (r.title, r.memory_key, r.content, r.tags)))
        for r in rows
    ]
    # A hidden section counts by its own text only: the open part of the record
    # is not what makes a question restricted.
    candidates += [
        (area, body)
        for r in mixed
        for area, body in hidden_sections(r.content, tenant)
    ]
    # Two matching words (or one, for a one-word question) keeps a stray common
    # word from flagging an unrelated area; and a customer or person name alone
    # ("Acme Logistics") is not what the question is about.
    needed = 1.0 if len(words) == 1 else 2.0
    out: dict[str, list[str]] = {}
    for area, haystack in candidates:
        hit = [w for w in words if _hits(w, haystack)]
        weight = sum(0.5 if w in entities else 1.0 for w in hit)
        if weight < needed or not [w for w in hit if w not in entities]:
            continue
        bucket = out.setdefault(area, [])
        bucket.extend(w for w in hit if w not in bucket)
    return out


def word_hits(text_: str, query: str) -> list[str]:
    """Which of the question's words (or their synonyms) appear in ``text_``."""
    return [w for w in _search_words(query) if _hits(w, text_ or "")]


def _hits(word: str, text_: str) -> bool:
    """The word as a prefix anywhere ("payroll" in "payrolls"), or a synonym as a
    whole word ("plan" but not inside "planning")."""
    import re

    haystack = text_.casefold().replace("_", " ")
    if word in haystack:
        return True
    return any(re.search(rf"\b{re.escape(v)}s?\b", haystack) for v in _SYNONYMS.get(word, ()))


#: Words people use for a topic that the record words differently. Kept small
#: and business-generic; each variant counts as a hit for the original word.
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "staffing": ("staff", "hiring", "headcount"),
    "headcount": ("hiring", "staff"),
    "direction": ("plan", "decision"),
    "responsibility": ("scope", "your part", "duty"),
    "duty": ("scope", "your part"),
    "compensation": ("salary",),
    "termination": ("terminate",),
    "payslip": ("payroll",),
}


def _variants(word: str) -> tuple[str, ...]:
    return (word, *_SYNONYMS.get(word, ()))


def _word_match_sql(n: int, word: str, params: dict[str, Any]) -> str:
    """SQL matching one question word (or a synonym) anywhere in an item."""
    parts = []
    for m, variant in enumerate(_variants(word)):
        key = f"w{n}_{m}"
        params[key] = f"%{variant}%"
        parts.append(
            f"i.title ILIKE :{key} OR i.memory_key ILIKE :{key} OR v.content ILIKE :{key} "
            f"OR array_to_string(i.tags, ' ') ILIKE :{key}"
        )
    return "(" + " OR ".join(parts) + ")"


def _entity_words(query: str) -> set[str]:
    """Normalized words that are capitalized mid-sentence — names of customers,
    people and roles rather than the subject being asked about."""
    import re

    out: set[str] = set()
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]*", query or ""):
        token = m.group(0)
        before = (query[: m.start()]).rstrip()
        sentence_start = not before or before[-1] in ".?!:"
        if token[0].isupper() and not sentence_start and token != "I":
            out.add(_normalize(token.casefold()))
    return out


def _normalize(raw: str) -> str:
    word = raw[:-3] + "y" if raw.endswith("ies") and len(raw) > 4 else raw
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    return word


_STOPWORDS = frozenset(
    "the and for are our what which who how when where why this that with from have has "
    "any all can you your about into does did was were will would should could there their "
    "them they tell show give list me please also need want help its it's than then "
    "answer one know just exactly remind get like thing things".split()
)


def _search_words(query: str) -> list[str]:
    """Meaningful words from a question, singularized crudely ("goals" -> "goal")."""
    import re

    if not re.search(r"\s", (query or "").strip()):
        return []  # a single token such as a memory key is matched exactly
    words: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", (query or "").casefold()):
        # Quarters ("q3") are short but meaningful.
        if (len(raw) < 3 and not re.fullmatch(r"q[1-4]", raw)) or raw in _STOPWORDS or raw.isdigit():
            continue
        word = _normalize(raw)
        if word not in words:
            words.append(word)
    return words[:10]


def _audit(event_type: AuditEventType, tenant: TenantContext, **payload: Any) -> None:
    from bizos.audit.events import log

    log(event_type, ctx=tenant, request={k: str(v) for k, v in payload.items()})
