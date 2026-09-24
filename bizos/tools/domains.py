"""
Domain-oriented tools (FB-035)
==============================

Narrow verbs for strategy / finance / brand / legal packs. They reuse org memory
and existing connectors; licensed judgments always go to approval.
"""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.memory import store as memory
from bizos.tools.base import RunScope, effect
from bizos.types import ApprovalStatus, MemoryCategory, SourceType


def _memory_hits(scope: RunScope, query: str, *, tags: list[str], limit: int = 5) -> list[dict]:
    items = memory.search(
        query,
        categories=None,
        authoritative_only=True,
        limit=max(limit * 3, 15),
        ctx=scope.ctx,
    )
    wanted = {t.casefold() for t in tags}
    # Tagged domain content is the answer even if the wording differs.
    tagged = memory.search("", authoritative_only=True, limit=100, ctx=scope.ctx)
    seen = {i.id for i in items}
    items = items + [
        i for i in tagged
        if i.id not in seen and {t.casefold() for t in (i.tags or [])} & wanted
    ]
    filtered = []
    for i in items:
        item_tags = {t.casefold() for t in (i.tags or [])}
        if wanted and not (item_tags & wanted):
            continue
        filtered.append(
            {
                "id": i.id,
                "key": i.memory_key,
                "title": i.title,
                "content": i.current.content if i.current else "",
                "category": str(i.category),
            }
        )
        if len(filtered) >= limit:
            break
    # Fall back to unfiltered hits when no tagged content exists yet.
    if not filtered:
        filtered = [
            {
                "id": i.id,
                "key": i.memory_key,
                "title": i.title,
                "content": i.current.content if i.current else "",
                "category": str(i.category),
            }
            for i in items[:limit]
        ]
    return filtered


@effect("strategy_list_priorities")
def strategy_list_priorities(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """List recorded strategic priorities / goals from org memory."""
    hits = _memory_hits(scope, str(args.get("query") or "priority goal objective"), tags=["strategy", "goal"])
    return ConnectorResult(ok=True, data=hits, summary=f"{len(hits)} strategy memory item(s)")


@effect("strategy_record_option")
def strategy_record_option(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Record a strategic option or recommendation for human decision (not execution)."""
    title = str(args.get("title") or "Strategic option").strip()
    content = str(args.get("content") or args.get("option") or "").strip()
    if not content:
        return ConnectorResult.failure("content is required")
    item = memory.put(
        category=MemoryCategory.DECISION,
        memory_key=str(args.get("key") or "").strip() or None,
        title=title,
        content=content,
        tags=["strategy", "option"],
        source_type=SourceType.MANUAL,
        attributes={
            "status": "OPTION",
            "pros": args.get("pros"),
            "cons": args.get("cons"),
            "recommended": bool(args.get("recommended", False)),
        },
        approval_status=ApprovalStatus.PENDING,
        settings=scope.settings,
        ctx=scope.ctx,
    )
    return ConnectorResult(
        ok=True,
        data={"id": item.id, "title": item.title},
        summary=f"Recorded strategic option {item.title!r} for human review",
    )


@effect("finance_lookup_policy")
def finance_lookup_policy(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Look up recorded finance policies (discounts, refunds, payment terms)."""
    query = str(args.get("query") or "finance policy pricing payment").strip()
    hits = _memory_hits(scope, query, tags=["finance", "pricing", "payment"])
    return ConnectorResult(
        ok=True,
        data=hits,
        summary=f"{len(hits)} finance policy hit(s) — figures are recorded data, not advice",
    )


@effect("finance_propose_adjustment")
def finance_propose_adjustment(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Propose a financial adjustment for licensed human approval (never auto-runs)."""
    # Effect only runs after approval; here we just acknowledge the payload.
    return ConnectorResult(
        ok=True,
        data={
            "customer": args.get("customer"),
            "amount": args.get("amount"),
            "reason": args.get("reason"),
            "kind": args.get("kind") or "adjustment",
        },
        summary=(
            f"Recorded finance adjustment proposal for {args.get('customer')!r} "
            f"amount={args.get('amount')!r} (human-approved)"
        ),
    )


@effect("brand_get_voice")
def brand_get_voice(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Retrieve brand voice / messaging guidelines from org memory."""
    hits = _memory_hits(scope, str(args.get("query") or "brand voice tone messaging"), tags=["brand", "voice"])
    return ConnectorResult(ok=True, data=hits, summary=f"{len(hits)} brand guideline(s)")


@effect("legal_find_clause")
def legal_find_clause(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Find recorded contract/clause summaries — never a legal conclusion."""
    query = str(args.get("query") or "").strip()
    if not query:
        return ConnectorResult.failure("query is required")
    hits = _memory_hits(scope, query, tags=["legal", "contract", "clause"])
    return ConnectorResult(
        ok=True,
        data=hits,
        summary=f"{len(hits)} clause/summary hit(s) — hand judgment to counsel",
    )


@effect("legal_flag_for_counsel")
def legal_flag_for_counsel(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Escalate a legal question to human counsel (always requires approval)."""
    question = str(args.get("question") or "").strip()
    if not question:
        return ConnectorResult.failure("question is required")
    item = memory.put(
        category=MemoryCategory.DECISION,
        title=f"Legal escalation: {question[:80]}",
        content=question,
        tags=["legal", "escalation"],
        source_type=SourceType.MANUAL,
        attributes={
            "status": "NEEDS_COUNSEL",
            "context": args.get("context"),
            "urgency": args.get("urgency") or "normal",
        },
        settings=scope.settings,
        ctx=scope.ctx,
    )
    return ConnectorResult(
        ok=True,
        data={"id": item.id, "question": question},
        summary="Flagged for human counsel",
    )


@effect("n8n_trigger_webhook")
def n8n_trigger_webhook(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Call the client's n8n webhook after policy/approval (FB-040)."""
    event = str(args.get("event") or args.get("workflow") or "bizos.event").strip()
    payload = args.get("payload") if isinstance(args.get("payload"), dict) else {
        k: v for k, v in args.items() if k not in {"event", "workflow", "payload"}
    }
    connector = get_connector("n8n", ctx=scope.ctx)
    return connector.trigger(  # type: ignore[attr-defined]
        event=event,
        payload=payload or {},
        workflow=str(args.get("workflow") or ""),
    )
