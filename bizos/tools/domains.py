"""
Domain-oriented tools (FB-035)
==============================

Narrow verbs for strategy / finance / brand / legal packs. They reuse org memory
and existing connectors; licensed judgments always go to approval.
"""

from __future__ import annotations

from typing import Any, Optional

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.memory import store as memory
from bizos.rbac.registry import get_spec
from bizos.tools.base import RunScope, effect, precheck
from bizos.types import ApprovalStatus, MemoryCategory, Role, SourceType


def _hit(i: Any) -> dict:
    return {
        "id": i.id,
        "key": i.memory_key,
        "title": i.title,
        "content": i.current.content if i.current else "",
        "category": str(i.category),
        "label": i.label,
        "access_area": i.access_area,
    }


def _memory_hits(
    scope: RunScope,
    query: str,
    *,
    tags: list[str],
    areas: tuple[str, ...] = (),
    limit: int = 5,
) -> list[dict]:
    """Domain memory for a query.

    An item belongs to the domain when it carries one of ``tags`` *or* sits in
    one of ``areas`` — seeded or user-recorded facts do not always carry tags,
    and a finance-area fact is finance content whatever it is tagged.
    Query matches rank first; the domain's other content follows.
    """
    wanted = {t.casefold() for t in tags}

    def in_domain(i: Any) -> bool:
        if i.access_area and i.access_area in areas:
            return True
        return bool({t.casefold() for t in (i.tags or [])} & wanted)

    matched = memory.search(query, authoritative_only=True, limit=max(limit * 3, 15), ctx=scope.ctx)
    domain_matched = [i for i in matched if in_domain(i)]
    everything = memory.search("", authoritative_only=True, limit=200, ctx=scope.ctx)
    seen = {i.id for i in domain_matched}
    rest = [i for i in everything if i.id not in seen and in_domain(i)]
    ordered = domain_matched + rest
    if not ordered:
        ordered = matched
    return [_hit(i) for i in ordered[:limit]]


@effect("strategy_list_priorities")
def strategy_list_priorities(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """List recorded strategic priorities / goals from org memory."""
    hits = _memory_hits(
        scope,
        str(args.get("query") or "priority goal objective"),
        tags=["strategy", "goal"],
        areas=("exec",),
    )
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
    hits = _memory_hits(
        scope, query, tags=["finance", "pricing", "payment", "discount"], areas=("finance",)
    )
    return ConnectorResult(
        ok=True,
        data=hits,
        summary=f"{len(hits)} finance policy hit(s) — figures are recorded data, not advice",
    )


@effect("finance_list_pending_billables")
def finance_list_pending_billables(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """List CRM deals staged as pending_invoice for a customer (finance-scoped)."""
    from sqlalchemy import text

    from bizos.tenancy.registry import readonly_connection

    customer = str(args.get("customer") or args.get("company") or "").strip()
    limit = max(1, min(int(args.get("limit") or 50), 100))
    clauses = ["stage = :stage"]
    params: dict[str, Any] = {"stage": "pending_invoice", "limit": limit}
    if customer:
        clauses.append("(d.name ILIKE :q OR co.name ILIKE :q)")
        params["q"] = f"%{customer}%"
    where = " AND ".join(clauses)
    with readonly_connection(scope.ctx) as conn:
        rows = conn.execute(
            text(
                "SELECT d.id, d.name, d.amount, d.currency, d.stage, co.name AS company "
                "FROM crm_deals d "
                "LEFT JOIN crm_companies co ON co.id = d.company_id "
                f"WHERE {where} ORDER BY d.updated_at DESC LIMIT :limit"
            ),
            params,
        ).fetchall()
    items = [
        {
            "deal_id": r.id,
            "name": r.name,
            "company": r.company,
            "amount": float(r.amount or 0),
            "currency": r.currency or "USD",
            "stage": r.stage,
        }
        for r in rows
    ]
    total = sum(i["amount"] for i in items)
    label = customer or "all customers"
    return ConnectorResult(
        ok=True,
        data={"customer": customer or None, "items": items, "total": total},
        summary=f"{len(items)} pending billable(s) for {label} totaling {total:,.2f}",
    )


@precheck("finance_draft_invoice")
def _draft_invoice_precheck(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    """No empty invoices and no duplicate requests reach the approval queue."""
    from bizos.actions import store as actions
    from bizos.types import ActionStatus

    customer = str(args.get("customer") or args.get("company") or "").strip()
    if not customer:
        return "customer is required — name the customer to invoice."
    pending = actions.list_actions(
        statuses=[ActionStatus.PENDING_APPROVAL],
        tool="finance_draft_invoice",
        limit=50,
        ctx=scope.ctx,
    )
    for action in pending:
        queued = str((action.payload or {}).get("customer") or "")
        if queued and (queued.casefold() in customer.casefold() or customer.casefold() in queued.casefold()):
            return (
                f"An invoice for {queued} is already awaiting approval ({action.id}). "
                "Nothing new was queued; an approver needs to act on the existing request."
            )
    listed = finance_list_pending_billables(scope, {"customer": customer})
    if not (listed.data or {}).get("items"):
        return (
            f"There are no pending billables for {customer}, so there is nothing to invoice. "
            "Nothing was queued. Any earlier billables may already be on a draft invoice — "
            "use finance_list_invoices to check."
        )
    # Snapshot what the approver will see, so the card stays accurate even after
    # the billables move off pending_invoice.
    data = listed.data or {}
    args["_content_snapshot"] = {
        "kind": "invoice",
        "customer": customer,
        "total": data.get("total"),
        "line_items": data.get("items") or [],
    }
    return None


@effect("approvals_list_pending")
def approvals_list_pending(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """The approval queue as the caller may see it."""
    from bizos.actions import store as actions
    from bizos.control import store as control
    from bizos.types import ActionStatus

    try:
        names = {u.id: u.display_name or u.email for u in control.list_users(scope.ctx.client_id)}
    except Exception:
        names = {}
    items = []
    for a in actions.list_actions(statuses=[ActionStatus.PENDING_APPROVAL], limit=50, ctx=scope.ctx):
        spec_area = getattr(get_spec(a.tool), "data_area", None)
        if spec_area and not scope.ctx.can_see_area(spec_area):
            continue
        entry: dict[str, Any] = {
            "action_id": a.id,
            "title": a.title,
            "tool": a.tool,
            "requested_by": names.get(a.requested_by, a.requested_by),
            "requested_by_role": a.requested_by_role,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        customer = (a.payload or {}).get("customer")
        if a.tool == "finance_draft_invoice" and customer:
            entry["customer"] = customer
            listed = finance_list_pending_billables(scope, {"customer": customer})
            entry["amount"] = (listed.data or {}).get("total")
            entry["line_items"] = (listed.data or {}).get("items")
        items.append(entry)
    can_approve = scope.ctx.is_admin or scope.ctx.at_least(Role.APPROVER)
    return ConnectorResult(
        ok=True,
        data={"pending": items, "you_can_approve": can_approve},
        summary=(
            f"{len(items)} action(s) awaiting approval"
            + ("; this user can approve them on the Approvals page" if can_approve else "; this user cannot approve")
        ),
    )


@effect("finance_list_invoices")
def finance_list_invoices(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """List invoices (including drafts) for a customer from the accounting ledger."""
    from sqlalchemy import text

    from bizos.tenancy.registry import readonly_connection

    customer = str(args.get("customer") or args.get("company") or "").strip()
    params: dict[str, Any] = {"limit": max(1, min(int(args.get("limit") or 20), 100))}
    where = ""
    if customer:
        where = "WHERE customer_name ILIKE :q"
        params["q"] = f"%{customer}%"
    with readonly_connection(scope.ctx) as conn:
        rows = conn.execute(
            text(
                "SELECT id, number, customer_name, status, currency, total, balance_due "
                f"FROM accounting_invoices {where} ORDER BY number DESC LIMIT :limit"
            ),
            params,
        ).fetchall()
    items = [
        {
            "invoice_id": r.id,
            "number": r.number,
            "customer": r.customer_name,
            "status": r.status,
            "currency": r.currency,
            "total": float(r.total or 0),
            "balance_due": float(r.balance_due or 0),
        }
        for r in rows
    ]
    return ConnectorResult(
        ok=True,
        data=items,
        summary=f"{len(items)} invoice(s) for {customer or 'all customers'}",
    )


@effect("finance_draft_invoice")
def finance_draft_invoice(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Draft an invoice from pending billables (runs only after policy/approval)."""
    from sqlalchemy import text

    from bizos.tenancy.registry import workspace_connection
    from bizos.util.ids import new_id

    customer = str(args.get("customer") or args.get("company") or "").strip()
    if not customer:
        return ConnectorResult.failure("customer is required")

    # Reuse the list effect so the draft always matches what finance could see.
    listed = finance_list_pending_billables(
        scope, {"customer": customer, "limit": int(args.get("limit") or 50)}
    )
    items = list((listed.data or {}).get("items") or [])
    if not items:
        return ConnectorResult.failure(f"No pending billables for {customer!r}")

    total = float(sum(float(i.get("amount") or 0) for i in items))
    invoice_id = new_id("inv")
    number = f"DRAFT-{invoice_id[-8:].upper()}"
    with workspace_connection(scope.ctx) as conn:
        conn.execute(
            text(
                "INSERT INTO accounting_invoices "
                "(id, number, customer_name, status, currency, total, balance_due) "
                "VALUES (:i, :n, :c, 'draft', 'USD', :t, :t)"
            ),
            {"i": invoice_id, "n": number, "c": customer, "t": total},
        )
        for item in items:
            deal_id = item.get("deal_id")
            if not deal_id:
                continue
            conn.execute(
                text(
                    "UPDATE crm_deals SET stage = 'invoiced_draft', updated_at = NOW() "
                    "WHERE id = :i AND stage = 'pending_invoice'"
                ),
                {"i": deal_id},
            )
    return ConnectorResult(
        ok=True,
        data={
            "invoice_id": invoice_id,
            "number": number,
            "customer": customer,
            "status": "draft",
            "total": total,
            "line_items": items,
        },
        summary=f"Draft invoice {number} for {customer} totaling {total:,.2f} USD",
    )


def _find_hr_record(scope: RunScope, *, key: str, name: str, prefix: str, default: str) -> Any:
    """A job opening or resume by exact key, else by name within HR memory."""
    if key:
        item = memory.get_by_key(MemoryCategory.FACT, key, ctx=scope.ctx)
        if item is not None:
            return item
    if name:
        for item in memory.search(name, authoritative_only=True, limit=20, ctx=scope.ctx):
            if (item.memory_key or "").startswith(prefix):
                return item
    return memory.get_by_key(MemoryCategory.FACT, default, ctx=scope.ctx)


@effect("hr_match_candidate")
def hr_match_candidate(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Compare a resume against a recorded job opening (HR-scoped; not a hiring decision)."""
    resume_text = str(args.get("resume_text") or "").strip()
    opening = _find_hr_record(
        scope,
        key=str(args.get("opening_key") or "").strip(),
        name=str(args.get("opening") or args.get("role") or args.get("job") or "").strip(),
        prefix="job_opening",
        default="job_opening_ops_coordinator",
    )
    if opening is None or not opening.current:
        return ConnectorResult.failure("No matching job opening is recorded in HR memory")

    if resume_text:
        resume_body = resume_text
        resume_title = "Uploaded resume"
        resume_key = None
    else:
        resume = _find_hr_record(
            scope,
            key=str(args.get("resume_key") or "").strip(),
            name=str(args.get("candidate_name") or args.get("candidate") or args.get("name") or "").strip(),
            prefix="candidate_resume",
            default="candidate_resume_jordan_lee",
        )
        if resume is None or not resume.current:
            return ConnectorResult.failure("No matching candidate resume is recorded in HR memory")
        resume_body = resume.current.content
        resume_title = resume.title
        resume_key = resume.memory_key

    must, nice = _requirements(opening.current.content)
    must_met = [r for r in must if _requirement_met(r, resume_body)]
    nice_met = [r for r in nice if _requirement_met(r, resume_body)]
    # Must-haves carry the decision; nice-to-haves only nudge it.
    score = round(
        80.0 * len(must_met) / max(len(must), 1) + 20.0 * len(nice_met) / max(len(nice), 1)
        if nice
        else 100.0 * len(must_met) / max(len(must), 1),
        1,
    )
    applicable = len(must_met) == len(must) or score >= 70.0
    missing = [r for r in must if r not in must_met] + [
        f"{r} (nice-to-have)" for r in nice if r not in nice_met
    ]
    verdict = "Likely fit" if applicable else "Weak fit"
    return ConnectorResult(
        ok=True,
        data={
            "opening": {"key": opening.memory_key, "title": opening.title},
            "resume": {"key": resume_key, "title": resume_title},
            "match_score": score,
            "applicable": applicable,
            "must_haves_met": must_met,
            "must_haves_total": len(must),
            "nice_to_haves_met": nice_met,
            "missing": missing,
            "note": "Assistive comparison only — hiring decision stays with a human.",
        },
        summary=(
            f"{verdict} vs {opening.title}: {len(must_met)}/{len(must)} must-haves met, "
            f"{len(nice_met)}/{len(nice)} nice-to-haves (score {score}%). "
            f"Missing: {', '.join(missing) or 'nothing'}. Assistive only — a human decides."
        ),
    )


_REQ_STOP = frozenset(
    "the and for with that this from have must role years year plus basics basic "
    "experience strong good ability".split()
)


def _stem(word: str) -> str:
    """Crude stem so "coordination" matches "coordinator" and "planning" matches "plan"."""
    return word[:6] if len(word) > 6 else word


def _requirements(opening: str) -> tuple[list[str], list[str]]:
    """Must-have and nice-to-have lines from an opening's text."""
    import re

    def section(label: str) -> list[str]:
        m = re.search(label + r"\s*:\s*(.+?)(?:\.\s+[A-Z][\w-]*\s*:|$)", opening, re.I | re.S)
        if not m:
            return []
        return [p.strip(" .") for p in re.split(r",|;", m.group(1)) if p.strip(" .")]

    must = section(r"must[- ]haves?")
    nice = section(r"nice[- ]to[- ]haves?")
    if not must:
        must = [p.strip(" .") for p in re.split(r"[.,;]", opening) if len(p.strip()) > 3]
    return must, nice


def _requirement_met(requirement: str, resume: str) -> bool:
    import re

    text_ = resume.casefold()
    resume_stems = {_stem(w) for w in re.findall(r"[a-z0-9+]+", text_)}
    words = [
        w for w in re.findall(r"[a-z]+", requirement.casefold()) if len(w) > 2 and w not in _REQ_STOP
    ]
    if not words:
        return False
    # "No SQL" in a resume is an explicit gap, not a match.
    for w in words:
        if re.search(rf"\bno {re.escape(w)}", text_):
            return False
    years = re.search(r"(\d+)\+?\s*years?", requirement.casefold())
    if years:
        have = [int(n) for n in re.findall(r"(\d+)\+?\s*years?", text_)]
        if not have or max(have) < int(years.group(1)):
            return False
    hits = sum(1 for w in words if _stem(w) in resume_stems)
    return hits * 2 >= len(words)


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
    hits = _memory_hits(scope, query, tags=["legal", "contract", "clause"], areas=("legal",))
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
