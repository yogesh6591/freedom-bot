"""Company/ops tool effects (CRM, email, calendar, memory, admin, packs).

Imported at startup so the policy engine can run them. Not the campaign assistant.
"""

from __future__ import annotations

####################################################################################################
# PART 1 - COMPANY TOOLS (CRM, email, calendar, memory, support, approvals, admin, domain packs, YouTube)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# crm  (bizos/tools/crm.py) - CRM Tool Effects
# ------------------------------------------------------------------------------------------------
from typing import Any, Optional

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect, precheck, risk_inputs


def _crm(scope: RunScope):
    # An approved change runs on the requester's own CRM login, like email and calendar, so the
    # record in HubSpot shows the person who asked for it rather than the approver.
    return get_connector("crm", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


# ------------------------------------------------------------------- reads


@effect("crm_search_contacts")
def crm_search_contacts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_contacts(
        args.get("query", ""), lifecycle=args.get("lifecycle"), limit=int(args.get("limit", 20))
    )


@effect("crm_get_contact")
def crm_get_contact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).get_contact(str(args.get("contact_id", "")))


@effect("crm_search_companies")
def crm_search_companies(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_companies(args.get("query", ""), limit=int(args.get("limit", 20)))


@effect("crm_search_deals")
def crm_search_deals(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).search_deals(
        stage=args.get("stage"), owner=args.get("owner"), limit=int(args.get("limit", 50))
    )


@effect("crm_pipeline_summary")
def crm_pipeline_summary(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).pipeline_summary()


# ------------------------------------------------------------------ writes


@effect("crm_create_note")
def crm_create_note(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_note(
        body=str(args.get("body", "")),
        contact_id=args.get("contact_id"),
        deal_id=args.get("deal_id"),
    )


@effect("crm_create_task")
def crm_create_task(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_task(
        title=str(args.get("title", "")),
        body=str(args.get("body", "")),
        contact_id=args.get("contact_id"),
        deal_id=args.get("deal_id"),
        assignee=args.get("assignee"),
        due_at=args.get("due_at"),
    )


@effect("crm_tag_lead")
def crm_tag_lead(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).tag_lead(
        str(args.get("contact_id", "")),
        add_tags=list(args.get("add_tags") or []),
        remove_tags=list(args.get("remove_tags") or []),
        lifecycle=args.get("lifecycle"),
    )


@risk_inputs("crm_tag_lead")
def _tag_lead_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Tagging touches ``tags`` and, when a lifecycle is given, ``lifecycle``."""
    fields = ["tags"]
    if args.get("lifecycle"):
        fields.append("lifecycle")
    return {"fields": tuple(fields), "record_count": 1}


@effect("crm_update_contact_field")
def crm_update_contact_field(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).update_contact_field(
        str(args.get("contact_id", "")), field=str(args.get("field", "")), value=args.get("value")
    )


@risk_inputs("crm_update_contact_field")
def _update_field_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"fields": (str(args.get("field", "")),), "record_count": 1}


@effect("crm_create_contact")
def crm_create_contact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).create_contact(
        first_name=str(args.get("first_name", "")),
        last_name=str(args.get("last_name", "")),
        email=args.get("email"),
        company=args.get("company"),
        title=args.get("title"),
        lifecycle=str(args.get("lifecycle", "lead")),
        tags=list(args.get("tags") or []),
        properties=dict(args.get("properties") or {}),
    )


@effect("crm_update_deal_stage")
def crm_update_deal_stage(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).update_deal_stage(str(args.get("deal_id", "")), stage=str(args.get("stage", "")))


@risk_inputs("crm_update_deal_stage")
def _deal_stage_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"fields": ("stage",), "record_count": 1}


_EVERY_CONTACT = "ALL"


def _bulk_scope(args: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """(scope given?, lifecycle filter). ``lifecycle_stage="ALL"`` means every contact.

    ``lifecycle_filter`` is the older argument, still accepted for actions queued before the rename.
    """
    stage = str(args.get("lifecycle_stage") or "").strip()
    if stage:
        return True, None if stage.upper() == _EVERY_CONTACT else stage
    legacy = str(args.get("lifecycle_filter") or "").strip()
    return bool(legacy), legacy or None


@effect("crm_bulk_update_contacts")
def crm_bulk_update_contacts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).bulk_update_contacts(
        lifecycle_filter=_bulk_scope(args)[1],
        field=str(args.get("field", "")),
        value=args.get("value"),
    )


@risk_inputs("crm_bulk_update_contacts")
def _bulk_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Count the rows the filter actually matches, before updating any of them.

    This is what makes the §30 bulk-action scenario real: the agent asking to
    change 1000 leads is measured against the workspace, not trusted.
    """
    count = _crm(scope).count_contacts(lifecycle=_bulk_scope(args)[1])
    return {"record_count": count, "fields": (str(args.get("field", "")),)}


@precheck("crm_bulk_update_contacts")
def _bulk_needs_a_scope(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    """Changing every contact must be asked for explicitly, never the result of a missing argument."""
    if not _bulk_scope(args)[0]:
        return (
            "lifecycle_stage is required: pass one lifecycle stage such as lead, or ALL only when the "
            "user clearly means every contact. Nothing was changed."
        )
    return None


@effect("crm_delete_record")
def crm_delete_record(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _crm(scope).delete_record(
        record_type=str(args.get("record_type", "contact")), record_id=str(args.get("record_id", ""))
    )

# ------------------------------------------------------------------------------------------------
# email  (bizos/tools/email.py) - Email Tool Effects
# ------------------------------------------------------------------------------------------------
import re

from bizos.agents.tool_args import EMAIL_PATTERN


def _email(scope: RunScope):
    return get_connector("email", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


def _recipients_email(args: dict[str, Any]) -> list[str]:
    raw = args.get("recipients") or args.get("to") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


_ADDRESS = re.compile(EMAIL_PATTERN)


def _addresses_only(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    """Recipients must be email addresses: a name ("Raj") is refused before anything is queued or sent."""
    cc = args.get("cc") or []
    cc = [p.strip() for p in cc.split(",")] if isinstance(cc, str) else [str(p).strip() for p in cc]
    bad = [r for r in _recipients_email(args) + [c for c in cc if c] if not _ADDRESS.match(r)]
    if not _recipients_email(args):
        return "recipients is required: give at least one email address. Nothing was sent."
    if bad:
        return (
            f"{', '.join(repr(b) for b in bad)} {'is' if len(bad) == 1 else 'are'} not an email address. "
            "Look up the address with crm_search_contacts (customers and leads) or admin_list_users "
            "(colleagues), or ask the user for it. Nothing was sent."
        )
    return None


precheck("email_send_message")(_addresses_only)
precheck("email_create_draft")(_addresses_only)


@effect("email_search_threads")
def email_search_threads(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).search_threads(args.get("query", ""), limit=int(args.get("limit", 20)))


@effect("email_read_thread")
def email_read_thread(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).read_thread(str(args.get("thread_id", "")))


@effect("email_create_draft")
def email_create_draft(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).create_draft(
        recipients=_recipients_email(args),
        subject=str(args.get("subject", "")),
        body=str(args.get("body", "")),
        cc=list(args.get("cc") or []),
        thread_id=args.get("thread_id"),
        action_id=args.get("action_id"),
    )


@risk_inputs("email_create_draft")
def _draft_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {"recipients": tuple(_recipients_email(args))}


@effect("email_send_message")
def email_send_message(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _email(scope).send_message(
        recipients=_recipients_email(args),
        subject=str(args.get("subject", "")),
        body=str(args.get("body", "")),
        cc=list(args.get("cc") or []),
        thread_id=args.get("thread_id"),
        draft_id=args.get("draft_id"),
    )


@risk_inputs("email_send_message")
def _send_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Recipients drive the domain allowlist; the count drives the record limit."""
    recipients = _recipients_email(args)
    return {"recipients": tuple(recipients), "record_count": max(1, len(recipients))}

# ------------------------------------------------------------------------------------------------
# calendar  (bizos/tools/calendar.py) - Calendar tool effects.
# ------------------------------------------------------------------------------------------------



def _calendar(scope: RunScope):
    return get_connector("calendar", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


def _attendees(args: dict[str, Any]) -> list[str]:
    raw = args.get("attendees") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


@effect("calendar_list_events")
def calendar_list_events(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).list_events(
        start=args.get("start"), end=args.get("end"), limit=int(args.get("limit", 50))
    )


@effect("calendar_check_availability")
def calendar_check_availability(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).check_availability(
        start=str(args.get("start")),
        end=str(args.get("end")),
        slot_minutes=int(args.get("slot_minutes", 30)),
    )


@effect("calendar_create_event")
def calendar_create_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).create_event(
        title=str(args.get("title", "")),
        starts_at=str(args.get("starts_at")),
        ends_at=str(args.get("ends_at")),
        attendees=_attendees(args),
        description=str(args.get("description", "")),
        location=args.get("location"),
    )


@risk_inputs("calendar_create_event")
def _create_event_inputs(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    """Attendees are external recipients for the domain allowlist."""
    return {"recipients": tuple(_attendees(args))}


@effect("calendar_reschedule_event")
def calendar_reschedule_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).reschedule_event(
        str(args.get("event_id", "")),
        starts_at=str(args.get("starts_at")),
        ends_at=str(args.get("ends_at")),
    )


@effect("calendar_cancel_event")
def calendar_cancel_event(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _calendar(scope).cancel_event(str(args.get("event_id", "")))

# ------------------------------------------------------------------------------------------------
# memory  (bizos/tools/memory.py) - Memory and Knowledge Tool Effects
# ------------------------------------------------------------------------------------------------
from typing import Any, Optional

from bizos.memory import store as memory_store
from bizos.tools.base import RunScope, effect
from bizos.types import ApprovalStatus, MemoryCategory, SourceType


def _render_memory(item: Any) -> dict[str, Any]:
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
        # FB-036: how the agent must present this value — "fact" or "estimate".
        "label": item.label,
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
    rendered = [_render_memory(i) for i in items]
    ids = {i.id for i in items}
    conflicts = [
        c for c in memory_store.find_conflicts(ctx=scope.ctx)
        if any(entry["item_id"] in ids for entry in c["items"])
    ]
    summary = f"{len(items)} memory item(s) matched {args.get('query','')!r}"
    if not items:
        hidden = memory_store.restricted_areas_matching(str(args.get("query", "")), ctx=scope.ctx)
        if hidden:
            summary += (
                f". Matching records exist in the restricted {', '.join(hidden)} area(s), which "
                "this user cannot access. Tell them it is restricted; do not say it is missing "
                "and do not offer to create it"
            )
    if items:
        summary += (
            ". When you answer, write 'Fact:' before values labelled fact and "
            "'Estimate:' before values labelled estimate"
        )
    if conflicts:
        topics = ", ".join(c["topic"] for c in conflicts)
        summary += (
            f". CONFLICT on {topics}: recorded policies disagree. Do not choose between "
            "them — tell the user and call memory_escalate_conflict so a person decides."
        )
    data: Any = rendered
    if conflicts:
        data = {"items": rendered, "conflicts": conflicts}
    return ConnectorResult(ok=True, data=data, summary=summary)


@effect("synced_search")
def synced_search(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """The caller's own synced Gmail / Calendar / YouTube / HubSpot records (bizos/sync), newest first."""
    from bizos.sync import store as sync_store

    raw = args.get("sources")
    sources = [raw] if isinstance(raw, str) else [str(s) for s in (raw or [])]
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    rows = sync_store.search(scope.ctx, str(args.get("query") or ""), sources=sources,
                             since=str(args.get("since") or "") or None, limit=limit)
    if not rows:
        return ConnectorResult(
            ok=True, data=[],
            summary="No synced records match. Nothing may be synced yet for this user: connect accounts on "
                    "Integrations, or use the live tools (email_search_threads, calendar_list_events).",
        )
    return ConnectorResult(ok=True, data=rows, summary=f"{len(rows)} synced record(s), newest first")


@effect("memory_get_fact")
def memory_get_fact(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    category = MemoryCategory.parse(args.get("category"), MemoryCategory.FACT)
    key = str(args.get("key") or args.get("memory_key") or "").strip()
    if not key:
        return ConnectorResult.failure("key is required — pass the fact key to look up.")
    item = memory_store.get_by_key(category, key, ctx=scope.ctx)  # type: ignore[arg-type]
    if item is None:
        # The model often guesses a key ("salary_band_engineer_ii"); search the
        # words of it before concluding nothing is recorded.
        phrase = key.replace("_", " ").replace("-", " ")
        hits = memory_store.search(phrase, limit=5, ctx=scope.ctx)
        if hits:
            return ConnectorResult(
                ok=True,
                data=[_render_memory(i) for i in hits],
                summary=(
                    f"no item under key {key!r}, but {len(hits)} related item(s) matched; answer "
                    "from the one that fits, prefixed with its label (Fact:/Estimate:)"
                ),
            )
        hidden = memory_store.restricted_areas_matching(phrase, ctx=scope.ctx)
        if hidden:
            return ConnectorResult(
                ok=True,
                data=None,
                summary=(
                    f"a matching record exists in the restricted {', '.join(hidden)} area(s), which "
                    "this user cannot access. Tell them it is restricted; do not say it is missing."
                ),
            )
        return ConnectorResult(ok=True, data=None, summary=f"no {category} recorded for that key")
    value = item.current.content if item.current else ""
    return ConnectorResult(
        ok=True,
        data=_render_memory(item),
        summary=f"{item.label.title()}: {value} (state it to the user prefixed with '{item.label.title()}:')",
    )


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


#: The restricted area a department domain's writes belong to.
_DOMAIN_AREAS = {"operations": "ops", "finance": "finance", "legal": "legal"}


def _write_area(scope: RunScope, args: dict[str, Any], title: str, content: str) -> Optional[str]:
    """The access area a new memory item must carry.

    Content derived from a restricted record (a checklist made from the ops
    payroll SOP) stays in that record's area; otherwise a department domain's
    write lands in that department. Without this, a restricted SOP rewritten
    as a "team checklist" would become visible to everyone.
    """
    ctx = scope.ctx
    requested = str(args.get("access_area") or "").strip() or None
    if requested and ctx.can_see_area(requested):
        return requested
    text_ = f"{title}\n{content}"
    sources = [
        i for i in memory_store.search(text_[:400], authoritative_only=False, limit=10, ctx=ctx)
        if i.access_area
    ]
    for item in sources:
        body = item.current.content if item.current else ""
        overlap = memory_store.word_hits(f"{item.title} {item.memory_key} {body}", text_[:400])
        if len(overlap) >= 3:
            return item.access_area
    area = _DOMAIN_AREAS.get(scope.domain)
    if area and ctx.can_see_area(area) and not ctx.is_admin:
        return area
    return None


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
    attributes = dict(args.get("attributes") or {})
    if args.get("topic"):
        attributes.setdefault("topic", str(args["topic"]))
    item = memory_store.put(
        category=category,
        title=title,
        content=content,
        memory_key=key,
        domain=args.get("domain") or scope.domain,
        tags=list(args.get("tags") or []),
        attributes=attributes,
        access_area=_write_area(scope, args, title, content),
        source_type=source,  # type: ignore[arg-type]
        source_id=args.get("source_id"),
        confidence=float(args.get("confidence", 1.0)),
        settings=scope.settings,
        ctx=scope.ctx,
    )
    pending = item.current is not None and item.current.approval_status == ApprovalStatus.PENDING
    note = " (recorded as PENDING because it was inferred rather than stated)" if pending else ""
    if item.current is not None and item.current.attributes.get("conflict_with"):
        note = (
            " (held as PENDING: it conflicts with an existing approved policy, so a person "
            "must choose which one stands)"
        )
    return ConnectorResult(
        ok=True,
        data=_render_memory(item),
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
        data=_render_memory(item),
        summary=(
            f"corrected to {item.current.content!r} (version {item.current.version_no}); "
            f"previous value {previous!r} kept in history as superseded"
        ),
    )


@effect("memory_escalate_conflict")
def memory_escalate_conflict(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Hand a policy conflict to a person. Runs only after an approver accepts it.

    The approver's resolution is recorded as a DECISION so the next retrieval
    has an authoritative answer; the agent itself never picks a side.
    """
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return ConnectorResult.failure("topic is required — name the conflicting policy.")
    item = memory_store.put(
        category=MemoryCategory.DECISION,
        title=f"Conflict escalated: {topic}",
        content=str(args.get("question") or f"Which {topic} policy applies?"),
        memory_key=f"conflict_{topic.replace(' ', '_')}"[:80],
        attributes={
            "status": "escalated",
            "conflict_topic": topic,
            "item_ids": list(args.get("item_ids") or []),
        },
        settings=scope.settings,
        ctx=scope.ctx,
    )
    return ConnectorResult(
        ok=True,
        data=_render_memory(item),
        summary=f"conflict on {topic!r} escalated to a person; recorded as {item.memory_key}",
    )

# ------------------------------------------------------------------------------------------------
# support  (bizos/tools/support.py) - Support Tool Effects (FB-046)
# ------------------------------------------------------------------------------------------------

from bizos import support


def _render_support(ticket: dict[str, Any]) -> dict[str, Any]:
    history = ticket.get("history") or []
    return {
        "id": ticket["id"],
        "subject": ticket["subject"],
        "severity": ticket["severity"],
        "status": ticket["status"],
        "handled_by": "Cepoch support",
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "latest_update": (history[-1].get("note") if history else "") or "",
    }


@effect("support_create_ticket")
def support_create_ticket(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    subject = str(args.get("subject") or "").strip()
    if not subject:
        return ConnectorResult.failure("subject is required — summarize the problem in one line.")
    body = str(args.get("body") or "").strip()
    if scope.session_id:
        body = f"{body}\n\nRaised from FreedomBot chat (session {scope.session_id}).".strip()
    ticket, merged = support.submit_ticket(
        client_id=scope.ctx.client_id,
        subject=subject,
        body=body,
        severity=str(args.get("severity") or support.SEVERITY_NORMAL),
        created_by=scope.ctx.user_id,
    )
    return ConnectorResult(
        ok=True,
        data=_render_support(ticket),
        summary=(
            f"added to the open Cepoch support ticket {ticket['id']} with the same subject"
            if merged
            else f"opened Cepoch support ticket {ticket['id']} ({ticket['severity']})"
        ),
    )


@effect("support_my_tickets")
def support_my_tickets(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    mine = [
        t for t in support.list_tickets(client_id=scope.ctx.client_id)
        if t.get("created_by") == scope.ctx.user_id
    ]
    if not mine:
        return ConnectorResult(ok=True, data=[], summary="this user has not raised any support tickets")
    return ConnectorResult(
        ok=True,
        data=[_render_support(t) for t in mine[:20]],
        summary=f"{len(mine)} support ticket(s) raised by this user",
    )

# ------------------------------------------------------------------------------------------------
# approvals  (bizos/tools/approvals.py) - Approval Tool Effects
# ------------------------------------------------------------------------------------------------
import dataclasses

from bizos.actions import store as actions
from bizos.actions.models import Action
from bizos.rbac.registry import get_spec
from bizos.types import ActionStatus, Role

_STATUS_WORDS: dict[str, tuple[ActionStatus, ...]] = {
    "pending": (ActionStatus.PENDING_APPROVAL,),
    "pending_approval": (ActionStatus.PENDING_APPROVAL,),
    "waiting": (ActionStatus.PENDING_APPROVAL,),
    "queue": (ActionStatus.PENDING_APPROVAL,),
    "draft": (ActionStatus.DRAFT,),
    "drafts": (ActionStatus.DRAFT,),
    "changes_requested": (ActionStatus.DRAFT,),
    "approved": (ActionStatus.APPROVED,),
    "executing": (ActionStatus.EXECUTING,),
    "completed": (ActionStatus.COMPLETED,),
    "complete": (ActionStatus.COMPLETED,),
    "done": (ActionStatus.COMPLETED,),
    "executed": (ActionStatus.COMPLETED,),
    "failed": (ActionStatus.FAILED,),
    "rejected": (ActionStatus.REJECTED,),
    "declined": (ActionStatus.REJECTED,),
    "cancelled": (ActionStatus.CANCELLED,),
    "canceled": (ActionStatus.CANCELLED,),
}

_ALL_WORDS = {"all", "any", "everything", "history"}


def _statuses(raw: Any) -> Optional[list[ActionStatus]]:
    """Status words from the model -> statuses. ``None`` means every status."""
    words = raw if isinstance(raw, list) else str(raw or "pending").replace(",", " ").split()
    picked: list[ActionStatus] = []
    for word in words:
        key = str(word).strip().casefold().replace(" ", "_").replace("-", "_")
        if key in _ALL_WORDS:
            return None
        for status in _STATUS_WORDS.get(key, (ActionStatus.parse(key),) if ActionStatus.parse(key) else ()):
            if status not in picked:
                picked.append(status)
    return picked or [ActionStatus.PENDING_APPROVAL]


def _names(scope: RunScope) -> dict[str, str]:
    from bizos.control import store as control

    try:
        return {u.id: u.display_name or u.email for u in control.list_users(scope.ctx.client_id)}
    except Exception:
        return {}


def _approver_role(scope: RunScope) -> Role:
    return Role.parse(scope.settings.approval_policy.approver_role, Role.APPROVER)


def _can_decide(scope: RunScope) -> bool:
    return scope.ctx.is_admin or scope.ctx.has_role(_approver_role(scope))


def _summary_of(action: Action, scope: RunScope) -> str:
    from bizos.actions.preview import content_for

    try:
        content = content_for(action, ctx=scope.ctx)
        return str(content.get("summary") or content.get("headline") or action.title)
    except Exception:
        return action.description or action.title


def _render_approvals(action: Action, scope: RunScope, names: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "action_id": action.id,
        "title": action.title,
        "status": str(action.status),
        "tool": action.tool,
        "what": _summary_of(action, scope),
        "requested_by": names.get(action.requested_by, action.requested_by),
        "requested_by_role": action.requested_by_role,
        "requested_by_you": action.requested_by == scope.ctx.user_id,
        "why_it_needs_review": action.policy_reason,
        "created_at": action.created_at.isoformat() if action.created_at else None,
        "updated_at": action.updated_at.isoformat() if action.updated_at else None,
    }
    if action.approval and action.approval.decided_by:
        entry["decided_by"] = names.get(action.approval.decided_by, action.approval.decided_by)
        entry["decided_at"] = action.approval.decided_at.isoformat() if action.approval.decided_at else None
        entry["decision_comment"] = action.approval.comment
    if action.executed_by:
        entry["executed_by"] = names.get(action.executed_by, action.executed_by)
        entry["executed_at"] = action.executed_at.isoformat() if action.executed_at else None
    if action.status == ActionStatus.FAILED and action.error:
        entry["error"] = action.error
    if action.status == ActionStatus.COMPLETED and action.result:
        entry["result"] = action.result.get("summary") if isinstance(action.result, dict) else action.result
    customer = (action.payload or {}).get("customer")
    if action.tool == "finance_draft_invoice" and customer:
        entry["customer"] = customer
        if action.status == ActionStatus.PENDING_APPROVAL:
            listed = finance_list_pending_billables(scope, {"customer": customer})
            entry["amount"] = (listed.data or {}).get("total")
            entry["line_items"] = (listed.data or {}).get("items")
    return entry


def _visible(action: Action, scope: RunScope) -> bool:
    area = getattr(get_spec(action.tool), "data_area", None)
    return not area or scope.ctx.can_see_area(area)


_MATCH_PAGE = 200
_MATCH_LISTED = 8


def _resolve(
    scope: RunScope, args: dict[str, Any], statuses: Optional[list[ActionStatus]] = None
) -> tuple[Optional[Action], Optional[str]]:
    """Find the one action the user means, by id or by a phrase from its title."""
    ref = str(args.get("action_id") or args.get("id") or "").strip()
    if ref:
        try:
            action = actions.get_action(ref, include_events=True, ctx=scope.ctx)
            if _visible(action, scope):
                return action, None
        except actions.ActionNotFound:
            pass
    phrase = str(args.get("match") or ("" if ref.startswith("act") else ref)).strip().casefold()
    if not phrase:
        return None, (
            f"No action {ref!r} that you can see." if ref
            else "Say which action — give its id, or call approvals_list to find it."
        )
    candidates: list[Action] = []
    offset = 0
    while len(candidates) <= _MATCH_LISTED:
        page = actions.list_actions(statuses=statuses, limit=_MATCH_PAGE, offset=offset, ctx=scope.ctx)
        candidates += [
            a for a in page
            if _visible(a, scope)
            and (phrase in a.title.casefold() or phrase in (a.description or "").casefold())
        ]
        if len(page) < _MATCH_PAGE:
            break
        offset += _MATCH_PAGE
    if len(candidates) == 1:
        return actions.get_action(candidates[0].id, include_events=True, ctx=scope.ctx), None
    if not candidates:
        return None, f"No action matching {phrase!r} that you can see."
    listed = "; ".join(f"{a.id} — {a.title} ({a.status})" for a in candidates[:_MATCH_LISTED])
    return None, f"More than one action matches {phrase!r}; ask the user which one: {listed}"


def _decision_error(exc: Exception, action: Action, scope: RunScope, verb: str) -> ConnectorResult:
    if isinstance(exc, actions.NotAnApprover):
        roles = ", ".join(sorted(str(r) for r in scope.ctx.roles)) or "no role"
        return ConnectorResult.failure(
            f"Not permitted: you cannot {verb} {action.id}. Deciding approvals needs the "
            f"{_approver_role(scope)} role and you hold {roles}. Nothing was changed."
        )
    if isinstance(exc, actions.SelfApprovalRejected):
        return ConnectorResult.failure(
            f"Not permitted: you requested {action.id} yourself, and this workspace does not "
            "allow self-approval. Another approver must decide it. Nothing was changed."
        )
    if isinstance(exc, actions.IllegalTransition):
        return ConnectorResult.failure(
            f"{action.id} is {action.status}, so it cannot be {verb}d now. "
            "Only an action that is PENDING_APPROVAL can be decided."
        )
    if isinstance(exc, actions.ActionNotFound):
        return ConnectorResult.failure(f"No action {action.id} that you can see.")
    raise exc


@effect("approvals_list")
def approvals_list(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Actions in the requested statuses, as the caller may see them."""
    statuses = _statuses(args.get("status"))
    mine = bool(args.get("mine"))
    try:
        limit = max(1, min(int(args.get("limit") or 25), 100))
    except (TypeError, ValueError):
        limit = 25
    names = _names(scope)
    found = [
        a for a in actions.list_actions(
            statuses=statuses,
            requested_by=scope.ctx.user_id if mine else None,
            limit=limit,
            ctx=scope.ctx,
        )
        if _visible(a, scope)
    ]
    label = "all statuses" if statuses is None else ", ".join(str(s) for s in statuses)
    can_decide = _can_decide(scope)
    return ConnectorResult(
        ok=True,
        data={
            "statuses": label,
            "only_mine": mine,
            "actions": [_render_approvals(a, scope, names) for a in found],
            "you_can_approve": can_decide,
            "approver_role": str(_approver_role(scope)),
            "self_approval_allowed": bool(scope.settings.approval_policy.allow_self_approval),
        },
        summary=(
            f"{len(found)} action(s) in {label}"
            + ("; this user can approve or reject pending ones" if can_decide
               else f"; this user cannot approve (needs {_approver_role(scope)})")
        ),
    )


@effect("approvals_get")
def approvals_get(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """One action with its full history of who did what."""
    action, problem = _resolve(scope, args)
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    names = _names(scope)
    data = _render_approvals(action, scope, names)
    data["payload"] = action.payload
    data["history"] = [
        {
            "event": e.event,
            "from": e.from_status,
            "to": e.to_status,
            "by": names.get(e.actor_id, e.actor_id),
            "role": e.actor_role,
            "comment": e.comment,
            "at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in action.events
    ]
    return ConnectorResult(ok=True, data=data, summary=f"{action.id} ({action.status}): {action.title}")


@effect("approvals_approve")
def approvals_approve(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Approve a pending action, then run it when this user may execute it."""
    from bizos.tools.executor import can_execute, execute_approved_action

    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or "").strip() or None
    try:
        approved = actions.approve(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "approve")

    run = dataclasses.replace(scope, domain=approved.domain or scope.domain, counters={})
    if not can_execute(run, approved):
        return ConnectorResult(
            ok=True,
            data={"action_id": approved.id, "status": str(approved.status), "executed": False},
            summary=(
                f"approved {approved.id} ({approved.title}). This role may approve "
                f"{approved.tool} but not carry it out, so it is waiting for an operator to run it."
            ),
        )
    outcome = execute_approved_action(run, approved.id)
    final = actions.get_action(approved.id, ctx=scope.ctx)
    return ConnectorResult(
        ok=True,
        data={
            "action_id": final.id,
            "status": str(final.status),
            "executed": outcome.ok,
            "result": outcome.message,
        },
        summary=(
            f"approved and carried out {final.id} ({final.title}): {outcome.message}"
            if outcome.ok
            else f"approved {final.id} ({final.title}), but running it failed: {outcome.message}"
        ),
    )


@effect("approvals_reject")
def approvals_reject(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or args.get("reason") or "").strip() or None
    try:
        rejected = actions.reject(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "reject")
    return ConnectorResult(
        ok=True,
        data={"action_id": rejected.id, "status": str(rejected.status)},
        summary=f"rejected {rejected.id} ({rejected.title}); it will not run",
    )


@effect("approvals_request_changes")
def approvals_request_changes(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    comment = str(args.get("comment") or args.get("reason") or "").strip()
    if not comment:
        return ConnectorResult.failure("Say what needs to change — the requester sees this comment.")
    action, problem = _resolve(scope, args, [ActionStatus.PENDING_APPROVAL])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    try:
        returned = actions.request_changes(action.id, settings=scope.settings, comment=comment, ctx=scope.ctx)
    except Exception as exc:
        return _decision_error(exc, action, scope, "send back")
    return ConnectorResult(
        ok=True,
        data={"action_id": returned.id, "status": str(returned.status)},
        summary=f"sent {returned.id} ({returned.title}) back to the requester as a draft with the requested changes",
    )


@effect("approvals_submit")
def approvals_submit(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Put a saved draft into the approval queue."""
    action, problem = _resolve(scope, args, [ActionStatus.DRAFT])
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    if action.requested_by != scope.ctx.user_id and not scope.ctx.is_admin:
        return ConnectorResult.failure(
            f"Not permitted: {action.id} is someone else's draft. Only its requester or an admin can submit it."
        )
    if action.status != ActionStatus.DRAFT:
        return ConnectorResult.failure(f"{action.id} is {action.status}; only a DRAFT can be submitted for approval.")
    try:
        submitted = actions.submit_for_approval(action.id, settings=scope.settings, ctx=scope.ctx)
    except actions.IllegalTransition as exc:
        return ConnectorResult.failure(str(exc))
    return ConnectorResult(
        ok=True,
        data={"action_id": submitted.id, "status": str(submitted.status)},
        summary=(
            f"submitted {submitted.id} ({submitted.title}) for approval; it is now pending approval "
            f"by a {_approver_role(scope)}"
        ),
    )


@effect("approvals_cancel")
def approvals_cancel(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    action, problem = _resolve(
        scope, args, [ActionStatus.DRAFT, ActionStatus.PENDING_APPROVAL, ActionStatus.APPROVED, ActionStatus.FAILED]
    )
    if action is None:
        return ConnectorResult.failure(problem or "Action not found.")
    comment = str(args.get("comment") or args.get("reason") or "").strip() or None
    try:
        cancelled = actions.cancel(action.id, comment=comment, ctx=scope.ctx)
    except actions.NotAnApprover:
        return ConnectorResult.failure(
            f"Not permitted: only the requester of {action.id} or an admin can cancel it. Nothing was changed."
        )
    except actions.IllegalTransition:
        return ConnectorResult.failure(f"{action.id} is {action.status} and can no longer be cancelled.")
    return ConnectorResult(
        ok=True,
        data={"action_id": cancelled.id, "status": str(cancelled.status)},
        summary=f"cancelled {cancelled.id} ({cancelled.title})",
    )

# ------------------------------------------------------------------------------------------------
# admin  (bizos/tools/admin.py) - Admin Tool Effects
# ------------------------------------------------------------------------------------------------
import secrets

from bizos import admin_ops as ops
from bizos.control import store as control
from bizos.types import Role


def _failure(exc: ops.AdminError) -> ConnectorResult:
    return ConnectorResult.failure(f"Not changed: {exc.message}")


def _settings(scope: RunScope):
    return ops.current_settings(scope.ctx)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.replace("\n", ",").split(",") if p.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _user_row(user) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "name": user.display_name,
        "email": user.email,
        "roles": sorted(str(r) for r in user.roles),
        "data_access": sorted(user.data_scopes),
        "status": user.status,
    }


def _find_user(scope: RunScope, ref: Any):
    """A user in this client by id, email, email name or display name."""
    wanted = str(ref or "").strip().casefold()
    if not wanted:
        raise ops.AdminError("Say which user — their email or name.")
    users = control.list_users(scope.ctx.client_id)
    for match in (
        lambda u: u.id.casefold() == wanted,
        lambda u: u.email.casefold() == wanted,
        lambda u: u.email.split("@")[0].casefold() == wanted,
        lambda u: (u.display_name or "").casefold() == wanted,
        lambda u: wanted in (u.display_name or "").casefold() or wanted in u.email.casefold(),
    ):
        found = [u for u in users if match(u)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = "; ".join(f"{u.display_name} <{u.email}>" for u in found[:8])
            raise ops.AdminError(f"More than one user matches {ref!r}; ask which: {names}")
    raise ops.AdminNotFound(f"No user matching {ref!r} in this workspace")


def _temporary_password() -> str:
    return secrets.token_urlsafe(12)


# --------------------------------------------------------------------- reads


@effect("admin_workspace_overview")
def admin_workspace_overview(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import open_alerts
    from bizos.launch import checklist_summary
    from bizos.onboarding import get_profile
    from bizos.pricing import get_rules

    settings = _settings(scope)
    users = control.list_users(scope.ctx.client_id)
    launch = checklist_summary(settings)
    rules = get_rules(settings)
    profile = get_profile(settings)
    data = {
        "company": settings.company_name,
        "workspace_mode": str(settings.mode),
        "purchased_domains": sorted(settings.purchased_domains),
        "enabled_domains": sorted(settings.enabled_domains),
        "order_dpa_allows": sorted(settings.order_dpa_allows),
        "llm_baa_covered": settings.llm_baa_covered,
        "approval_policy": dataclasses.asdict(settings.approval_policy),
        "risk_policy": dataclasses.asdict(settings.risk_policy),
        "tool_modes": dict(settings.tool_modes),
        "users": {"total": len(users), "active": sum(1 for u in users if u.status == "ACTIVE")},
        "setup": {
            "configured_at": profile.configured_at,
            "goals": len(profile.goals),
            "systems": len(profile.systems),
            "open_questions": profile.open_questions,
        },
        "launch": {"ready": launch["ready"], "pending_items": launch["pending_count"]},
        "open_alerts": len(open_alerts(settings, scope.ctx)),
        "pricing": {
            "configured": rules.get("configured"),
            "quote_status": rules.get("quote_status"),
            "customer_price_usd": rules.get("customer_price_usd"),
        },
    }
    return ConnectorResult(ok=True, data=data, summary=f"workspace overview for {settings.company_name}")


@effect("admin_list_users")
def admin_list_users(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.types import DATA_AREAS

    users = control.list_users(scope.ctx.client_id)
    return ConnectorResult(
        ok=True,
        data={
            "users": [_user_row(u) for u in users],
            "roles": [str(r) for r in Role],
            "data_areas": list(DATA_AREAS),
        },
        summary=f"{len(users)} user(s) in this workspace",
    )


@effect("admin_get_setup")
def admin_get_setup(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.onboarding import get_profile

    settings = _settings(scope)
    profile = get_profile(settings).to_dict()
    blob = settings.onboarding or {}
    profile["handoff_at"] = blob.get("handoff_at")
    profile["handoff_source"] = blob.get("handoff_source")
    profile["change_orders"] = blob.get("change_orders") or []
    return ConnectorResult(ok=True, data=profile, summary="first-client setup profile")


@effect("admin_list_alerts")
def admin_list_alerts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import last_scanned, list_alerts, list_baselines, visible_to

    settings = _settings(scope)
    alerts = [a for a in list_alerts(settings) if visible_to(a, scope.ctx)]
    return ConnectorResult(
        ok=True,
        data={
            "alerts": alerts,
            "scanned_at": last_scanned(settings),
            "baselines": list_baselines(settings),
            "note": "Directional only — not a full always-on waste engine. No guaranteed ROI.",
        },
        summary=f"{len(alerts)} alert(s); {sum(1 for a in alerts if a.get('status', 'open') == 'open')} open",
    )


@effect("admin_launch_checklist")
def admin_launch_checklist(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.launch import checklist_summary

    summary = checklist_summary(_settings(scope))
    return ConnectorResult(
        ok=True,
        data=summary,
        summary=(
            "launch checklist ready for sign-off"
            if summary["ready"]
            else f"launch checklist: {summary['pending_count']} item(s) pending"
        ),
    )


@effect("admin_pricing")
def admin_pricing(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.pricing import face_scope_config, get_rules, list_consult_quotes

    settings = _settings(scope)
    return ConnectorResult(
        ok=True,
        data={
            "resale_rules": get_rules(settings),
            "scope_weights": face_scope_config(settings),
            "saved_quotes": list_consult_quotes(settings)[:10],
        },
        summary="resale rules, consult scope weights and saved quotes",
    )


@effect("admin_list_domains")
def admin_list_domains(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.domains.packs import PACKS

    settings = _settings(scope)
    items = [
        {
            "domain": p.name,
            "title": p.title,
            "purchased": settings.domain_purchased(p.name),
            "enabled": p.name in settings.enabled_domains,
            "mode_ceiling": str(p.mode_ceiling) if p.mode_ceiling else None,
        }
        for p in PACKS
    ]
    return ConnectorResult(ok=True, data=items, summary=f"{len(items)} domain pack(s)")


@effect("admin_tool_permissions")
def admin_tool_permissions(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.rbac import permissions as perms
    from bizos.rbac.registry import TOOL_SPECS

    query = str(args.get("tool") or args.get("query") or "").strip().casefold()
    settings = _settings(scope)
    overrides = perms.load_overrides(scope.ctx, refresh=True)
    rows = []
    for spec in TOOL_SPECS:
        if query and query not in spec.name.casefold() and query not in spec.title.casefold() \
                and query != spec.category:
            continue
        rows.append(
            {
                "tool": spec.name,
                "title": spec.title,
                "category": spec.category,
                "write": spec.write,
                "permissions": {
                    str(r): str(perms.permission_for_role(spec, r, overrides)) for r in Role
                },
                "overridden_roles": sorted(str(r) for r in Role if (spec.name, str(r)) in overrides),
                "tool_mode": settings.tool_modes.get(spec.name),
            }
        )
    if not query and len(rows) > 40:
        rows = [
            {"tool": r["tool"], "write": r["write"], "overridden_roles": r["overridden_roles"],
             "tool_mode": r["tool_mode"]}
            for r in rows
        ]
    return ConnectorResult(
        ok=True,
        data={"tools": rows, "permission_values": ["DENIED", "DRAFT_ONLY", "APPROVE_ONLY",
                                                     "EXECUTE_AFTER_APPROVAL", "ALLOWED"]},
        summary=f"{len(rows)} tool(s)" + (f" matching {query!r}" if query else ""),
    )


# --------------------------------------------------------------------- setup


_SETUP_FIELDS = (
    "goals", "systems", "sops", "roles_notes", "access_notes", "autonomy_mode",
    "enabled_domains", "review_points", "exclusions", "baselines", "assessment",
)


@effect("admin_apply_setup")
def admin_apply_setup(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Apply first-client setup. Fields left out keep their current values."""
    if not any(args.get(k) for k in _SETUP_FIELDS):
        return ConnectorResult.failure("Nothing to apply. Tell me what to set up.")
    from bizos.onboarding import get_profile

    settings = _settings(scope)
    current = get_profile(settings).to_dict()
    fields = {k: current.get(k) for k in _SETUP_FIELDS}
    fields["baselines"] = None
    # Applying setup also sets the live mode and packs; leaving them out must
    # not revert a change made since on the Policies or Domains tab.
    fields["autonomy_mode"] = settings.default_execution_mode
    fields["enabled_domains"] = list(settings.enabled_domains)
    for key in ("goals", "systems", "review_points", "exclusions", "enabled_domains"):
        if args.get(key) is not None:
            fields[key] = _list(args[key])
    for key in ("roles_notes", "access_notes", "autonomy_mode"):
        if args.get(key) is not None:
            fields[key] = str(args[key])
    if args.get("sops") is not None:
        fields["sops"] = [
            {"title": str(s.get("title") or ""), "content": str(s.get("content") or "")}
            for s in args["sops"] if isinstance(s, dict) and s.get("title")
        ]
    else:
        # Already-stored SOPs live in memory; re-applying must not write them again.
        fields["sops"] = []
    try:
        result = ops.apply_onboarding(scope.ctx, settings, **fields)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="applied first-client setup")


@effect("admin_handoff_assessment")
def admin_handoff_assessment(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    assessment = args.get("assessment")
    if not isinstance(assessment, dict):
        return ConnectorResult.failure("assessment must be a JSON object of the sale/assessment details")
    try:
        result = ops.handoff_assessment(scope.ctx, _settings(scope), assessment)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="prefilled setup from the assessment")


# ------------------------------------------------------------------ insights


@effect("admin_scan_alerts")
def admin_scan_alerts(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.insights import scan_alerts

    items = scan_alerts(client_id=scope.ctx.client_id, settings=_settings(scope), ctx=scope.ctx)
    open_items = [a for a in items if a.get("status", "open") == "open"]
    return ConnectorResult(
        ok=True,
        data=items,
        summary=f"scanned: {len(open_items)} open alert(s) of {len(items)} detected (directional only)",
    )


@effect("admin_dismiss_alert")
def admin_dismiss_alert(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    alert_id = str(args.get("alert_id") or "").strip()
    if not alert_id:
        return ConnectorResult.failure("Say which alert — call admin_list_alerts for ids.")
    try:
        ops.dismiss_alert(scope.ctx, _settings(scope), alert_id)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data={"alert_id": alert_id}, summary=f"dismissed alert {alert_id}")


@effect("admin_set_baselines")
def admin_set_baselines(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Add or update before/after baselines; others are kept unless removed."""
    from bizos.insights import list_baselines

    settings = _settings(scope)
    keep = ("id", "workflow", "metric", "before_value", "after_value", "unit", "notes")
    current = [{k: b.get(k) for k in keep if b.get(k) is not None} for b in list_baselines(settings)]
    remove = {str(r).casefold() for r in _list(args.get("remove"))}

    def key(row: dict[str, Any]) -> str:
        return str(row.get("id") or f"{row.get('workflow')}|{row.get('metric')}").casefold()

    def label(row: dict[str, Any]) -> str:
        return str(row.get("workflow") or "").casefold()

    merged = {key(b): b for b in current if key(b) not in remove and label(b) not in remove}
    for row in args.get("items") or []:
        if not isinstance(row, dict):
            continue
        clean = {k: row[k] for k in keep if row.get(k) not in (None, "")}
        existing = next(
            (k for k, b in merged.items() if clean.get("id") and b.get("id") == clean["id"]
             or (b.get("workflow"), b.get("metric")) == (clean.get("workflow"), clean.get("metric"))),
            None,
        )
        if existing:
            merged[existing] = {**merged[existing], **clean}
        else:
            merged[key(clean)] = clean
    try:
        saved = ops.save_baselines(scope.ctx, settings, list(merged.values()))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True, data=saved, summary=f"{len(saved)} baseline(s) saved (directional before/after only)"
    )


# -------------------------------------------------------------------- launch


@effect("admin_mark_launch_item")
def admin_mark_launch_item(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    item_id = str(args.get("item_id") or "").strip()
    if not item_id:
        return ConnectorResult.failure("Say which checklist item — call admin_launch_checklist for ids.")
    try:
        result = ops.mark_launch_item(
            scope.ctx,
            _settings(scope),
            item_id=item_id,
            status=str(args.get("status") or "done"),
            waive_reason=str(args.get("waive_reason") or ""),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary=f"launch item {item_id} marked {args.get('status') or 'done'}")


@effect("admin_launch_sign_off")
def admin_launch_sign_off(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        result = ops.launch_sign_off(scope.ctx, _settings(scope), force=bool(args.get("force")))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=result, summary="launch signed off")


# ------------------------------------------------------------------- pricing


_RULE_FIELDS = ("customer_price_usd", "cepoch_cost_usd", "margin_percent", "quote_status", "notes", "scope_rules")


@effect("admin_set_resale_rules")
def admin_set_resale_rules(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    rules = {k: args[k] for k in _RULE_FIELDS if args.get(k) is not None}
    if not rules:
        return ConnectorResult.failure("Give at least one value to change (price, cost, margin, status, notes or scope weights).")
    try:
        saved = ops.save_resale_rules(scope.ctx, _settings(scope), rules)
    except ops.AdminError as exc:
        return _failure(exc)
    note = " — numbers changed after confirmation, so the quote needs reconfirming" if saved.get("reconfirm_needed") else ""
    return ConnectorResult(ok=True, data=saved, summary=f"resale rules saved ({saved.get('quote_status')}){note}")


@effect("admin_consult_quote")
def admin_consult_quote(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        result = ops.consult_quote(
            scope.ctx,
            _settings(scope),
            domains=_list(args.get("domains")),
            autonomy=str(args.get("autonomy") or "WAIT_FOR_APPROVAL"),
            integrations=int(args.get("integrations") or 0),
            discount_percent=float(args.get("discount_percent") or 0),
            label=str(args.get("label") or ""),
            save=bool(args.get("save")),
        )
    except (TypeError, ValueError):
        return ConnectorResult.failure("integrations must be a whole number and discount_percent a number")
    except ops.AdminError as exc:
        return _failure(exc)
    quote = result["quote"]
    return ConnectorResult(
        ok=True,
        data=result,
        summary=(
            f"consult quote {quote.get('customer_price_usd')} USD"
            + (f", saved as {result['saved']['id']}" if result.get("saved") else ", not saved")
        ),
    )


# --------------------------------------------------------------------- users


@effect("admin_create_user")
def admin_create_user(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    password = _temporary_password()
    try:
        user = ops.create_user(
            scope.ctx,
            email=str(args.get("email") or ""),
            password=password,
            display_name=str(args.get("display_name") or args.get("name") or ""),
            roles=_list(args.get("roles")) or ["VIEWER"],
            data_scopes=_list(args.get("data_access") or args.get("data_scopes")),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={**_user_row(user), "temporary_password": password},
        summary=f"created {user.email} as {', '.join(sorted(str(r) for r in user.roles))}",
    )


@effect("admin_set_user_roles")
def admin_set_user_roles(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    try:
        user = _find_user(scope, args.get("user"))
        roles = ops.parse_roles(_list(args.get("roles")))
        if user.id == scope.ctx.user_id and Role.ADMIN not in roles:
            return ConnectorResult.failure(
                "Not changed: you cannot remove your own Admin role from chat. Ask another admin."
            )
        updated = ops.set_roles(scope.ctx, user.id, [str(r) for r in roles])
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data=_user_row(updated),
        summary=f"{updated.email} now holds {', '.join(sorted(str(r) for r in updated.roles))}",
    )


@effect("admin_set_user_access")
def admin_set_user_access(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Grant or revoke restricted data areas; ``data_access`` replaces, add/remove edit."""
    try:
        user = _find_user(scope, args.get("user"))
        if args.get("data_access") is not None:
            wanted = set(_list(args.get("data_access")))
        else:
            wanted = set(user.data_scopes)
        wanted |= set(_list(args.get("add")))
        wanted -= set(_list(args.get("remove")))
        updated = ops.set_scopes(scope.ctx, user.id, sorted(s.casefold() for s in wanted))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data=_user_row(updated),
        summary=f"{updated.email} data access: {', '.join(sorted(updated.data_scopes)) or 'general only'}",
    )


@effect("admin_set_user_status")
def admin_set_user_status(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    raw = str(args.get("status") or "").strip().upper()
    status = {"DISABLE": "SUSPENDED", "DISABLED": "SUSPENDED", "SUSPEND": "SUSPENDED",
              "ENABLE": "ACTIVE", "ENABLED": "ACTIVE", "REACTIVATE": "ACTIVE"}.get(raw, raw)
    try:
        user = _find_user(scope, args.get("user"))
        if user.id == scope.ctx.user_id and status != "ACTIVE":
            return ConnectorResult.failure("Not changed: you cannot suspend your own account from chat.")
        updated = ops.set_status(scope.ctx, user.id, status)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(ok=True, data=_user_row(updated), summary=f"{updated.email} is now {updated.status}")


@effect("admin_reset_password")
def admin_reset_password(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    password = _temporary_password()
    try:
        user = _find_user(scope, args.get("user"))
        ops.set_password(scope.ctx, user.id, password)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"email": user.email, "temporary_password": password},
        summary=f"reset the password for {user.email}",
    )


# ------------------------------------------------------------------ policies


_POLICY_FIELDS = (
    "default_execution_mode", "order_dpa_allows", "llm_baa_covered", "approval_policy",
    "risk_policy", "memory_policy", "retention_policy", "timezone",
)


@effect("admin_update_settings")
def admin_update_settings(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    changes: dict[str, Any] = {k: args[k] for k in _POLICY_FIELDS if args.get(k) is not None}
    if "order_dpa_allows" in changes:
        changes["order_dpa_allows"] = [s.casefold() for s in _list(changes["order_dpa_allows"])]
    for key in ("approval_policy", "risk_policy", "memory_policy", "retention_policy"):
        if key in changes and not isinstance(changes[key], dict):
            return ConnectorResult.failure(f"{key} must be an object of the fields to change")
    if not changes:
        return ConnectorResult.failure("Say which setting to change.")
    try:
        updated = ops.update_settings(scope.ctx, _settings(scope), changes)
    except ops.AdminError as exc:
        return _failure(exc)
    shown = {k: updated.to_dict().get(k) for k in changes}
    return ConnectorResult(ok=True, data=shown, summary=f"updated {', '.join(sorted(changes))}")


@effect("admin_toggle_domain")
def admin_toggle_domain(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    domain = str(args.get("domain") or "").strip().casefold()
    enabled = bool(args.get("enabled", True))
    try:
        updated = ops.toggle_domain(scope.ctx, _settings(scope), domain, enabled)
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"domain": domain, "enabled": enabled, "enabled_domains": updated.enabled_domains},
        summary=f"{domain} pack {'enabled' if enabled else 'disabled'}",
    )


@effect("admin_record_change_order")
def admin_record_change_order(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    domain = str(args.get("domain") or "").strip().casefold()
    try:
        updated = ops.record_change_order(
            scope.ctx,
            _settings(scope),
            domain,
            reference=str(args.get("reference") or ""),
            note=str(args.get("note") or ""),
            enable=bool(args.get("enable", True)),
        )
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True,
        data={"purchased_domains": updated.purchased_domains, "enabled_domains": updated.enabled_domains},
        summary=f"recorded change order {args.get('reference')} adding the {domain} pack",
    )


# ---------------------------------------------------------------------- tools


def _is_default(value: Optional[str]) -> bool:
    return str(value or "").strip().casefold() in {"", "default", "none", "clear", "reset", "inherit"}


@effect("admin_set_tool_permission")
def admin_set_tool_permission(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    tool = str(args.get("tool") or "").strip()
    role = str(args.get("role") or "").strip()
    permission = args.get("permission")
    try:
        if _is_default(permission):
            ops.clear_tool_permission(scope.ctx, tool=tool, role=role)
            return ConnectorResult(
                ok=True, data={"tool": tool, "role": role.upper(), "permission": "default"},
                summary=f"{tool} for {role.upper()} reset to the registry default",
            )
        result = ops.set_tool_permission(scope.ctx, tool=tool, role=role, permission=str(permission))
    except ops.AdminError as exc:
        return _failure(exc)
    return ConnectorResult(
        ok=True, data=result, summary=f"{tool} for {result['role']} is now {result['permission']}"
    )


@effect("admin_set_tool_mode")
def admin_set_tool_mode(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    tool = str(args.get("tool") or "").strip()
    mode = args.get("mode")
    try:
        updated = ops.update_settings(
            scope.ctx, _settings(scope), {"tool_modes": {tool: None if _is_default(mode) else str(mode)}}
        )
    except ops.AdminError as exc:
        return _failure(exc)
    current = updated.tool_modes.get(tool)
    return ConnectorResult(
        ok=True,
        data={"tool": tool, "mode": current},
        summary=f"{tool} now {'runs in ' + current + ' mode' if current else 'follows the workspace mode'}",
    )

# ------------------------------------------------------------------------------------------------
# domains  (bizos/tools/domains.py) - Domain-oriented tools (FB-035)
# ------------------------------------------------------------------------------------------------

from bizos.memory import store as memory
from bizos.tools.base import RunScope, effect, precheck


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

# ------------------------------------------------------------------------------------------------
# youtube  (bizos/tools/youtube.py) - YouTube tool effects (read-only, the user's own Composio connection).
# ------------------------------------------------------------------------------------------------



def _youtube(scope: RunScope):
    return get_connector("youtube", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


def _ids(args: dict[str, Any]) -> list[str]:
    raw = args.get("video_ids") or args.get("video_id") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


@effect("youtube_search_videos")
def youtube_search_videos(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).search_videos(
        str(args.get("query", "")), limit=int(args.get("limit", 5)), order=args.get("order")
    )


@effect("youtube_my_channel")
def youtube_my_channel(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).my_channel()


@effect("youtube_my_videos")
def youtube_my_videos(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).my_videos(limit=int(args.get("limit", 10)))


@effect("youtube_video_details")
def youtube_video_details(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).video_details(_ids(args))


@effect("youtube_video_comments")
def youtube_video_comments(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _youtube(scope).video_comments(
        str(args.get("video_id", "")),
        limit=int(args.get("limit", 20)),
        order=str(args.get("order", "relevance")),
    )


@effect("youtube_transcript")
def youtube_transcript(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.connectors import composio as composio_conn
    from bizos.connectors import youtube_transcript as transcripts
    from bizos.connectors.base import ConnectorError

    try:
        data = transcripts.fetch(composio_conn.as_user(scope.ctx, scope.on_behalf_of), str(args.get("video") or ""),
                                 language=str(args.get("language") or "") or None)
    except ConnectorError as exc:
        return ConnectorResult.failure(str(exc))
    note = " (cut to the first part; it is long)" if data["truncated"] else ""
    return ConnectorResult(ok=True, data=data,
                           summary=f"transcript of {data['video_id']} from {data['source']}, "
                                   f"{data['characters']} characters{note}")


# ------------------------------------------------------------------------------------------------
# integrations  (bizos/connectors/composio_actions.py) - any action of the user's connected apps.
# ------------------------------------------------------------------------------------------------

from bizos.connectors import composio_actions as app_actions

#: The dedicated tool a generic action of each app and tier may never be looser than. Reads compare
#: with the app's read tool; writes and destructive actions with its write tool.
_ACTION_REFERENCE = {
    ("gmail", "read"): "email_search_threads",
    ("gmail", "write"): "email_send_message",
    ("googlecalendar", "read"): "calendar_list_events",
    ("googlecalendar", "write"): "calendar_create_event",
    ("hubspot", "read"): "crm_search_contacts",
    ("hubspot", "write"): "crm_update_contact_field",
    ("youtube", "read"): "youtube_my_videos",
}


def _app_owner(scope: RunScope):
    from bizos.connectors import composio as composio_conn

    return composio_conn.as_user(scope.ctx, scope.on_behalf_of)


def _app_action_problem(scope: RunScope, args: dict[str, Any], tier: str) -> Optional[str]:
    """Why this action may not run with this tool, or None.

    The tier must match the tool, and the caller must be allowed the app's dedicated tool for that
    tier in this workspace (so a generic action never reaches what a dedicated tool would refuse).
    An approver executing an approved action was not the requester; the requester was checked when
    the request was made.
    """
    from bizos.connectors.base import ConnectorError
    from bizos.domains.packs import enabled_packs
    from bizos.tools.dry_run import is_visible

    try:
        toolkit, problem = app_actions.check(str(args.get("action") or ""), tier)
    except ConnectorError as exc:
        return str(exc)
    if problem or scope.on_behalf_of:
        return problem
    reference = _ACTION_REFERENCE.get((toolkit, app_actions.READ if tier == app_actions.READ else app_actions.WRITE))
    if reference and not is_visible(scope, reference, enabled_packs(scope.settings.enabled_domains)):
        return (f"Not permitted: your role or this workspace's settings do not allow "
                f"{'reading' if tier == app_actions.READ else 'changing'} this app's data. Nothing was run.")
    return None


def _run_app_action(scope: RunScope, args: dict[str, Any], tier: str) -> ConnectorResult:
    from bizos.connectors.base import ConnectorError

    slug = str(args.get("action") or "").strip().upper()
    try:
        data, truncated = app_actions.run(_app_owner(scope), slug, args.get("arguments") or {}, tier=tier)
    except ConnectorError as exc:
        return ConnectorResult.failure(f"{exc} If the arguments were wrong, check them with "
                                       f"integration_find_action and try again.")
    done = "ran" if tier == app_actions.READ else "done"
    return ConnectorResult(ok=True, data={"action": slug, "result": data, "_untrusted_source": f"app_{slug.lower()}"},
                           summary=f"{slug} {done}" + (" (result cut; narrow the request)" if truncated else ""))


@effect("integration_find_action")
def integration_find_action(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.connectors.base import ConnectorError

    try:
        data = app_actions.find(_app_owner(scope), str(args.get("query") or ""),
                                toolkit=str(args.get("app") or "") or None, limit=int(args.get("limit") or 8))
    except ConnectorError as exc:
        return ConnectorResult.failure(str(exc))
    missing = (f" Not connected: {', '.join(data['not_connected'])}; the user can connect it on the Integrations "
               "page." if data.get("not_connected") else "")
    if not data["actions"]:
        return ConnectorResult(ok=True, data=data, summary="No matching actions; try other words, or web_search." + missing)
    return ConnectorResult(ok=True, data=data, summary=f"{len(data['actions'])} matching action(s); each says which "
                                                       "tool runs it and the arguments it takes." + missing)


@precheck("integration_read_action")
def _read_action_precheck(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    return _app_action_problem(scope, args, app_actions.READ)


@effect("integration_read_action")
def integration_read_action(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _run_app_action(scope, args, app_actions.READ)


@precheck("integration_write_action")
def _write_action_precheck(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    return _app_action_problem(scope, args, app_actions.WRITE)


@effect("integration_write_action")
def integration_write_action(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _run_app_action(scope, args, app_actions.WRITE)


@precheck("integration_destructive_action")
def _destructive_action_precheck(scope: RunScope, args: dict[str, Any]) -> Optional[str]:
    return _app_action_problem(scope, args, app_actions.DESTRUCTIVE)


@effect("integration_destructive_action")
def integration_destructive_action(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _run_app_action(scope, args, app_actions.DESTRUCTIVE)


# ------------------------------------------------------------------------------------------------
# research  (bizos/connectors/web.py) - public web search and page reading.
# ------------------------------------------------------------------------------------------------


@effect("web_search")
def web_search(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.connectors import web
    from bizos.connectors.base import ConnectorError

    try:
        data = web.search(str(args.get("query") or ""), limit=int(args.get("limit") or 6))
    except ConnectorError as exc:
        return ConnectorResult.failure(str(exc))
    return ConnectorResult(ok=True, data=data, summary=f"{len(data['results'])} web result(s); read the useful "
                                                       "ones with web_read")


@effect("web_read")
def web_read(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos.connectors import web
    from bizos.connectors.base import ConnectorError

    try:
        data = web.read(str(args.get("url") or ""))
    except ConnectorError as exc:
        return ConnectorResult.failure(str(exc))
    return ConnectorResult(ok=True, data=data, summary=f"read {data['url']} ({data['characters']} characters"
                                                       + ("; cut" if data["truncated"] else "") + ")")


# ------------------------------------------------------------------------------------------------
# files  (bizos/chat_attachments.py) - files the user attached in the chat.
# ------------------------------------------------------------------------------------------------


@effect("attachment_read")
def attachment_read(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    from bizos import chat_attachments

    try:
        data = chat_attachments.read(scope.ctx, str(args.get("attachment_id") or ""),
                                     query=str(args.get("query") or ""), start=int(args.get("start") or 0))
    except chat_attachments.AttachmentNotFound:
        return ConnectorResult.failure("No attached file with that attachment_id; use one from the Attached files list")
    more = " (more: read again with start=next_start)" if data.get("next_start") is not None else ""
    if "rows" in data:
        summary = f"read {len(data['rows'])} of {data['rows_total']} rows of {data['name']}{more}"
    elif "passages" in data:
        summary = f"found {len(data['passages'])} matching part(s) of {data['name']}"
    else:
        summary = f"read {data['name']}{more}"
    return ConnectorResult(ok=True, data=data, summary=summary)


import app.outreach.effects  # noqa: F401

