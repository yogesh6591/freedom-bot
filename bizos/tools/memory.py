"""
Memory and Knowledge Tool Effects
=================================

The agent's access to organizational memory. Reads default to
``authoritative_only``, so a value the model itself inferred and that no human
has approved is never returned as an established fact (§3).
"""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.memory import store as memory_store
from bizos.tools.base import RunScope, effect
from bizos.types import ApprovalStatus, MemoryCategory, SourceType


def _render(item: Any) -> dict[str, Any]:
    """The shape a memory item takes when the model sees it.

    Provenance travels with the value, so the agent can say "per the SOP recorded
    by Dana on 3 June" instead of asserting bare facts.
    """
    version = item.current
    return {
        "id": item.id,
        "category": str(item.category),
        "key": item.memory_key,
        "title": item.title,
        "value": version.content if version else None,
        "source": str(version.source_type) if version else None,
        "recorded_by": version.created_by if version else None,
        "confidence": version.confidence if version else None,
        "approval_status": str(version.approval_status) if version else None,
        "version": version.version_no if version else None,
        "last_updated": version.updated_at.isoformat() if version and version.updated_at else None,
        "attributes": version.attributes if version else {},
    }


@effect("memory_search")
def memory_search(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    categories = None
    raw = args.get("categories")
    if raw:
        parsed = [MemoryCategory.parse(c) for c in (raw if isinstance(raw, list) else [raw])]
        categories = [c for c in parsed if c is not None]
    items = memory_store.search(
        str(args.get("query", "")),
        categories=categories,
        domain=args.get("domain"),
        authoritative_only=bool(args.get("authoritative_only", True)),
        limit=int(args.get("limit", 10)),
        ctx=scope.ctx,
    )
    return ConnectorResult(
        ok=True,
        data=[_render(i) for i in items],
        summary=f"{len(items)} memory item(s) matched {args.get('query','')!r}",
    )


@effect("memory_get_fact")
def memory_get_fact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    category = MemoryCategory.parse(args.get("category"), MemoryCategory.FACT)
    key = str(args.get("key") or args.get("memory_key") or "").strip()
    if not key:
        return ConnectorResult.failure("key is required — pass the fact key to look up.")
    item = memory_store.get_by_key(category, key, ctx=scope.ctx)  # type: ignore[arg-type]
    if item is None:
        return ConnectorResult(ok=True, data=None, summary=f"no {category} recorded for that key")
    return ConnectorResult(ok=True, data=_render(item), summary=item.current.content if item.current else "")


@effect("memory_history")
def memory_history(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    item = memory_store.history(str(args.get("item_id", "")), ctx=scope.ctx)
    return ConnectorResult(
        ok=True,
        data=item.to_dict(include_versions=True),
        summary=f"{len(item.versions)} version(s) of {item.title!r}",
    )


def _normalize_write_args(args: dict[str, Any]) -> dict[str, Any]:
    """Accept common aliases the model uses for memory writes."""
    out = dict(args)
    if not str(out.get("key") or "").strip():
        for alt in ("memory_key", "fact_key", "name"):
            if str(out.get(alt) or "").strip():
                out["key"] = str(out[alt]).strip()
                break
    if not str(out.get("content") or "").strip():
        for alt in ("value", "fact", "text", "body", "decision"):
            if str(out.get(alt) or "").strip():
                out["content"] = str(out[alt]).strip()
                break
    if not str(out.get("title") or "").strip():
        if str(out.get("key") or "").strip():
            out["title"] = str(out["key"]).replace("_", " ").strip()
        elif str(out.get("name") or "").strip():
            out["title"] = str(out["name"]).strip()
    return out


def _put(scope: RunScope, args: dict[str, Any], category: MemoryCategory) -> ConnectorResult:
    args = _normalize_write_args(args)
    title = str(args.get("title") or "").strip()
    content = str(args.get("content") or "").strip()
    key = str(args.get("key") or "").strip() or None
    if not content:
        return ConnectorResult.failure(
            "content is required — pass the non-empty fact/SOP text to store "
            "(and a title or key so it can be looked up later)."
        )
    if not title and not key:
        return ConnectorResult.failure(
            "Provide a title and/or key so this memory item can be retrieved later."
        )
    if not title:
        title = key.replace("_", " ")  # type: ignore[union-attr]

    source = SourceType.parse(args.get("source_type"), SourceType.MANUAL)
    item = memory_store.put(
        category=category,
        title=title,
        content=content,
        memory_key=key,
        domain=args.get("domain") or scope.domain,
        tags=list(args.get("tags") or []),
        attributes=dict(args.get("attributes") or {}),
        source_type=source,  # type: ignore[arg-type]
        source_id=args.get("source_id"),
        confidence=float(args.get("confidence", 1.0)),
        settings=scope.settings,
        ctx=scope.ctx,
    )
    pending = item.current is not None and item.current.approval_status == ApprovalStatus.PENDING
    note = " (recorded as PENDING because it was inferred rather than stated)" if pending else ""
    return ConnectorResult(
        ok=True,
        data=_render(item),
        summary=(
            f"{category} '{item.title}' recorded under key '{item.memory_key}' "
            f"as version {item.current.version_no}{note}"
        ),
    )


@effect("memory_add_fact")
def memory_add_fact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _put(scope, args, MemoryCategory.FACT)


@effect("memory_add_sop")
def memory_add_sop(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _put(scope, args, MemoryCategory.SOP)


@effect("memory_record_decision")
def memory_record_decision(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Record a decision with the §2 C structured fields."""
    attributes = {
        "decision": args.get("decision") or args.get("content"),
        "decided_on": args.get("decided_on"),
        "approved_by": args.get("approved_by"),
        "context": args.get("context"),
        "reason": args.get("reason"),
        "status": args.get("status", "active"),
        **dict(args.get("attributes") or {}),
    }
    return _put(scope, {**args, "attributes": attributes}, MemoryCategory.DECISION)


@effect("memory_correct")
def memory_correct(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Supersede a memory value. Never overwrites; history is preserved."""
    item_id = args.get("item_id")
    if not item_id:
        # Allow correcting by key, which is how a user phrases it in chat.
        category = MemoryCategory.parse(args.get("category"), MemoryCategory.FACT)
        existing = memory_store.get_by_key(category, str(args.get("key", "")), ctx=scope.ctx)  # type: ignore[arg-type]
        if existing is None:
            return ConnectorResult.failure(
                f"No {category} is recorded under key {args.get('key')!r}, so there is nothing to "
                "correct. Record it as a new fact instead."
            )
        item_id = existing.id

    item = memory_store.correct(
        item_id=str(item_id),
        new_content=str(args.get("new_content") or args.get("content", "")),
        reason=str(args.get("reason", "user correction")),
        source_type=SourceType.parse(args.get("source_type"), SourceType.MANUAL),  # type: ignore[arg-type]
        source_id=args.get("source_id"),
        confidence=float(args.get("confidence", 1.0)),
        settings=scope.settings,
        ctx=scope.ctx,
    )
    previous = item.current.attributes.get("previous_value") if item.current else None
    return ConnectorResult(
        ok=True,
        data=_render(item),
        summary=(
            f"corrected to {item.current.content!r} (version {item.current.version_no}); "
            f"previous value {previous!r} kept in history as superseded"
        ),
    )


@effect("knowledge_search")
def knowledge_search(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Search the workspace knowledge base.

    Backed by agno's ``Knowledge`` over the client's own ``agno_knowledge`` table
    and vector store, so the isolation boundary is the same as everything else.
    Document text is external content and is neutralized before it reaches the
    model.
    """
    from bizos.knowledge.service import search_knowledge

    return search_knowledge(scope, str(args.get("query", "")), limit=int(args.get("limit", 5)))
