"""The whole assistant in ONE file: tools, helpers, prompt and agent.

Read this file top to bottom. Order:

  PART 1  Company tools   what each ops tool DOES (crm_*, email_*, calendar_*, memory_*, support_*,
                          approvals_*, admin_*, finance/hr/... domain tools, youtube_*).
  PART 2  Campaign helpers  load people/campaigns, "who is already in a campaign", send via Instantly.
  PART 3  Campaign tools   the six tools the model uses for outbound campaigns + the saved form.
  PART 4  The prompt       APPROACH, CAMPAIGN_SECTION, build_prompt().
  PART 5  The agent        reply(): runs one chat turn (Agno agent, self-check, saving, trace).

NOT in this file on purpose (it is the engine, shared by everything, and has its own tests):
  bizos/rbac/registry.py   WHO may use each tool, its risk level and mode (the ToolSpec list).
  bizos/tools/base.py      guarded_call: the policy engine every ops tool call goes through.
  bizos/tools/executor.py  runs an action after an approver releases it.
  bizos/agents/surface.py  turns the ToolSpec list into Agno tools for the model.
  bizos/agents/instructions.py  the platform-rules part of the prompt (role, mode, hard rules).

To add or change an ops tool: its behaviour is in PART 1, its permissions are in registry.py.
To change campaign behaviour: PART 2-3. To change what the model is told: PART 4.
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
    return get_connector("crm", ctx=scope.ctx)


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



def _email(scope: RunScope):
    return get_connector("email", ctx=scope.ctx, owner_user_id=scope.on_behalf_of)


def _recipients_email(args: dict[str, Any]) -> list[str]:
    raw = args.get("recipients") or args.get("to") or []
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return [str(item).strip() for item in raw if str(item).strip()]


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
        from bizos.tools.domains import finance_list_pending_billables

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
    # Every action in these statuses, not only the newest page: a phrase that also matches an older request
    # is ambiguous. Stop once there are more matches than the refusal lists.
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
    "risk_policy", "memory_policy", "retention_policy",
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


####################################################################################################
# PART 2 - CAMPAIGN HELPERS (load people/campaigns, who is already in a campaign, send through Instantly)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# context  (app/assistant/context.py) - Facts the assistant may use: saved campaigns, people, and the initial email.
# ------------------------------------------------------------------------------------------------
import json


def _clock_context(value: str) -> str:
    hour_text, minute = (value or "00:00").split(":", 1)
    hour = int(hour_text)
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute} {suffix}"


def _sender_name(db, ctx) -> str:
    from acquisition.config import get_settings
    from acquisition.models.campaign import Campaign

    current = (getattr(ctx, "display_name", None) or "").strip()
    if current:
        return current
    latest = db.query(Campaign).order_by(Campaign.created_at.desc()).first()
    if latest and (latest.sender_name or "").strip():
        return latest.sender_name.strip()
    return get_settings().default_sender_name


def known_records(ctx=None) -> dict:
    """Current acquisition rows. Empty lists mean nothing is saved, not that the model may invent rows."""
    from sqlalchemy.orm import selectinload

    from acquisition.config import get_settings
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import (
        Campaign,
        CampaignProspect,
        MessageChannel,
        MessageType,
    )
    from acquisition.models.prospect import Prospect
    from acquisition.services.outreach_conflict_service import is_active_outreach_link
    from acquisition.services.assessment_tokens import split_executive_name
    from acquisition.services.campaign_service import initial_email_text

    db = SessionLocal()
    try:
        campaigns = []
        rows = db.query(Campaign).options(selectinload(Campaign.messages)).all()
        for campaign in rows:
            initial = next(
                (
                    message
                    for message in campaign.messages
                    if message.is_active
                    and message.channel == MessageChannel.EMAIL
                    and message.message_type == MessageType.INITIAL
                ),
                None,
            )
            start = _clock_context(campaign.send_window_start)
            end = _clock_context(campaign.send_window_end)
            zone = (campaign.send_timezone or "").strip()
            campaigns.append(
                {
                    "campaign_id": str(campaign.id),
                    "campaign_name": campaign.name,
                    "schedule_start": start,
                    "schedule_end": end,
                    "timezone": zone,
                    "schedule_label": f"{start}–{end}",
                    "initial_email": None
                    if initial is None
                    else {
                        "subject": initial.subject or "",
                        "body": initial.body or "",
                    },
                }
            )
        sender_name = _sender_name(db, ctx)
        assessment_url = (get_settings().public_base_url or "").strip()
        no_company_subject, no_company_body = initial_email_text(
            sender_name=sender_name,
            first_name="",
            company="",
            assessment_url=assessment_url,
        )
        active_by_prospect: dict = {}
        for link in db.query(CampaignProspect).options(selectinload(CampaignProspect.campaign)).all():
            camp = link.campaign
            # Same rule the lead sync uses, so "already in a campaign" means one thing everywhere.
            if camp is None or not is_active_outreach_link(link, campaign=camp):
                continue
            active_by_prospect.setdefault(link.prospect_id, [])
            cid = str(camp.id)
            if any(item["campaign_id"] == cid for item in active_by_prospect[link.prospect_id]):
                continue
            active_by_prospect[link.prospect_id].append(
                {"campaign_id": cid, "campaign_name": camp.name}
            )
        people = []
        for person in db.query(Prospect).order_by(Prospect.executive_name).all():
            first_name, _last = split_executive_name(person.executive_name)
            company = (person.company or "").strip()
            subject, body = initial_email_text(
                sender_name=sender_name,
                first_name=first_name,
                company=company,
                assessment_url=assessment_url,
            )
            people.append(
                {
                    "name": person.executive_name,
                    "first_name": first_name,
                    "email": person.email,
                    "company": company,
                    "approval_status": person.approval_status.value,
                    "active_campaigns": active_by_prospect.get(person.id, []),
                    "email_to_show": {"subject": subject, "body": body},
                }
            )
        return {
            "sender_name": sender_name,
            "sender_accounts": sending_account_emails(db),
            "assessment_url": assessment_url,
            "email_without_company": {"subject": no_company_subject, "body": no_company_body},
            "campaigns": campaigns,
            "people": people,
        }
    finally:
        db.close()


def sending_account_emails(db=None) -> list[str]:
    """Mailbox addresses Instantly can send from. Empty when Instantly is not connected."""
    from acquisition.db.session import SessionLocal
    from acquisition.services.campaign_activation_service import CampaignActivationService

    own_session = db is None
    session = db or SessionLocal()
    try:
        accounts = CampaignActivationService(session).list_sending_accounts()
    except Exception:
        return []
    finally:
        if own_session:
            session.close()
    emails: list[str] = []
    for account in accounts:
        email = (account.email or "").strip()
        if email and email not in emails:
            emails.append(email)
    return emails


def known_records_text(ctx=None) -> str:
    try:
        records = known_records(ctx)
    except Exception:
        records = {"campaigns": [], "people": [], "unavailable": True}
    return json.dumps(records, ensure_ascii=False)

# ------------------------------------------------------------------------------------------------
# lead_status  (app/assistant/lead_status.py) - Who is already in a campaign, and what the assistant may do about it.
# ------------------------------------------------------------------------------------------------
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from acquisition.integrations.instantly.errors import InstantlyError
from acquisition.models.campaign import (
    AssessmentStatus,
    CampaignProspect,
    JourneyStage,
    OutreachStatus,
)
from acquisition.models.prospect import Prospect
from acquisition.models.replies import ReplyConversation
from acquisition.services.instantly_lead_control import stop_instantly_lead_outreach
from acquisition.services.outreach_conflict_service import (
    is_active_outreach_link,
    normalize_outreach_email,
)

FREE = "free"
MOVE_NOT_CONTACTED = "move_not_contacted"
MOVE_CONTACTED = "move_contacted"
REFUSE_CONVERSATION = "refuse_conversation"
REFUSE_OPTED_OUT = "refuse_opted_out"

_RANK = {
    FREE: 0,
    MOVE_NOT_CONTACTED: 1,
    MOVE_CONTACTED: 2,
    REFUSE_CONVERSATION: 3,
    REFUSE_OPTED_OUT: 4,
}
_OPTED_OUT = {OutreachStatus.UNSUBSCRIBED, OutreachStatus.BOUNCED}


class StopError(Exception):
    """Stopping a lead in its old campaign failed. Nothing was changed locally."""


@dataclass(slots=True)
class LinkInfo:
    link_id: object
    campaign_id: str
    campaign_name: str
    runtime_status: str
    outreach_status: str
    sync_status: str
    active: bool
    contacted: bool
    conversation: bool
    opted_out: bool

    def as_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id,
            "campaign_name": self.campaign_name,
            "campaign_runtime": self.runtime_status,
            "outreach_status": self.outreach_status,
            "already_emailed": self.contacted,
            "in_conversation": self.conversation,
        }


@dataclass(slots=True)
class PersonStatus:
    email: str
    name: str = ""
    known: bool = False
    tier: str = FREE
    links: list[LinkInfo] = field(default_factory=list)

    @property
    def label(self) -> str:
        return (self.name or self.email).strip()

    @property
    def blocking(self) -> list[LinkInfo]:
        return [link for link in self.links if link.active]

    @property
    def blocking_names(self) -> str:
        names = []
        for link in self.blocking:
            if link.campaign_name and link.campaign_name not in names:
                names.append(link.campaign_name)
        return " and ".join(names) or "another campaign"

    def as_dict(self) -> dict:
        return {
            "email": self.email,
            "name": self.name,
            "tier": self.tier,
            "what_to_do": _GUIDANCE[self.tier],
            "active_in": [link.as_dict() for link in self.blocking],
        }


_GUIDANCE = {
    FREE: "Not in any campaign. Add normally.",
    MOVE_NOT_CONTACTED: (
        "Already in another campaign but never emailed. Tell the user where, then ask one yes/no to stop them "
        "there and add them to the new campaign. If yes, call approve_move."
    ),
    MOVE_CONTACTED: (
        "Already emailed in another campaign. Tell the user where and that moving stops their follow-ups there. "
        "Ask for a clear yes before approve_move."
    ),
    REFUSE_CONVERSATION: (
        "They replied, started the assessment, or booked. Do NOT move or add them. Tell the user plainly and "
        "suggest handling them personally."
    ),
    REFUSE_OPTED_OUT: (
        "They unsubscribed or bounced. Never add them. Tell the user plainly; this is not negotiable."
    ),
}


def _in_conversation(link: CampaignProspect, has_reply_row: bool) -> bool:
    return bool(
        has_reply_row
        or link.outreach_status in {OutreachStatus.REPLIED, OutreachStatus.ASSESSMENT_STARTED}
        or link.last_reply_at is not None
        or link.assessment_started_at is not None
        or link.assessment_status != AssessmentStatus.NOT_STARTED
        or link.booked_at is not None
        or (link.booking_status or "").strip().lower() == "confirmed"
        or link.journey_stage not in {JourneyStage.OUTREACH}
    )


def describe_people(db: Session, emails: list[str]) -> list[PersonStatus]:
    """Read-only. One PersonStatus per email, in the order given."""
    out: list[PersonStatus] = []
    seen: set[str] = set()
    for raw in emails:
        email = normalize_outreach_email(raw)
        if not email or email in seen:
            continue
        seen.add(email)
        person = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email))
        status = PersonStatus(
            email=str(raw).strip(),
            name=(person.executive_name or "").strip() if person else "",
            known=person is not None,
        )
        links = list(
            db.scalars(
                select(CampaignProspect)
                .options(selectinload(CampaignProspect.campaign))
                .join(Prospect, Prospect.id == CampaignProspect.prospect_id)
                .where(func.lower(Prospect.email) == email)
            ).all()
        )
        reply_link_ids = set()
        if links:
            reply_link_ids = set(
                db.scalars(
                    select(ReplyConversation.campaign_prospect_id).where(
                        ReplyConversation.campaign_prospect_id.in_([link.id for link in links])
                    )
                ).all()
            )
        tier = FREE
        for link in links:
            campaign = link.campaign
            active = is_active_outreach_link(link, campaign=campaign)
            opted_out = link.outreach_status in _OPTED_OUT
            info = LinkInfo(
                link_id=link.id,
                campaign_id=str(link.campaign_id),
                campaign_name=(campaign.name if campaign else "") or "",
                runtime_status=(campaign.instantly_runtime_status.value if campaign else ""),
                outreach_status=link.outreach_status.value,
                sync_status=link.sync_status.value,
                active=active,
                contacted=link.outreach_status == OutreachStatus.ACTIVE,
                conversation=active and _in_conversation(link, link.id in reply_link_ids),
                opted_out=opted_out,
            )
            status.links.append(info)
            if opted_out:
                candidate = REFUSE_OPTED_OUT
            elif not active:
                continue
            elif info.conversation:
                candidate = REFUSE_CONVERSATION
            elif info.contacted:
                candidate = MOVE_CONTACTED
            else:
                candidate = MOVE_NOT_CONTACTED
            if _RANK[candidate] > _RANK[tier]:
                tier = candidate
        status.tier = tier
        out.append(status)
    return out


def check_people(emails: list[str]) -> list[PersonStatus]:
    """Same as describe_people but opens (and closes) its own session."""
    from acquisition.db.session import SessionLocal

    db = SessionLocal()
    try:
        return describe_people(db, emails)
    finally:
        db.close()


def stop_for_move(db: Session, client, link_id) -> None:
    """Stop one person in their old campaign. Keeps history, deletes nothing.

    Instantly first, then local: if Instantly fails, local state is untouched and the person
    is still (correctly) shown as active there.
    """
    link = db.scalar(
        select(CampaignProspect)
        .options(selectinload(CampaignProspect.campaign), selectinload(CampaignProspect.prospect))
        .where(CampaignProspect.id == link_id)
    )
    if link is None:
        return
    try:
        stop_instantly_lead_outreach(client, campaign=link.campaign, link=link)
    except InstantlyError as exc:
        raise StopError(str(exc) or "Instantly did not accept the stop request.") from exc
    link.outreach_status = OutreachStatus.STOPPED
    db.add(link)
    db.commit()

# ------------------------------------------------------------------------------------------------
# fulfill  (app/assistant/fulfill.py) - Create the approved campaign and hand the email to Instantly.
# ------------------------------------------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import ValidationError

from acquisition.db.session import SessionLocal
from acquisition.integrations.instantly.timezones import normalize_instantly_timezone
from acquisition.models.campaign import Campaign, CampaignProspect, InstantlyRuntimeStatus, MessageType
from acquisition.models.prospect import ApprovalStatus as ProspectApprovalStatus, Prospect
from acquisition.schemas.campaign import CampaignCreate, CampaignMessageUpdate, CampaignUpdate
from acquisition.schemas.prospect import ProspectCreate
from acquisition.services.assessment_tokens import split_executive_name
from acquisition.services.campaign_activation_service import ActivationError, CampaignActivationService
from acquisition.services.campaign_service import (
    CampaignNotFoundError,
    CampaignService,
    CampaignValidationError,
)
from acquisition.services.instantly_campaign_sync_service import (
    InstantlyCampaignSyncService,
    InstantlySyncError,
)
from acquisition.services.lead_sync_service import LeadSyncError, LeadSyncService
from acquisition.services.instantly_service import InstantlyService
from acquisition.services.prospect_service import ProspectService


class FulfillError(Exception):
    def __init__(self, message: str, *, campaign_id: str = "") -> None:
        self.campaign_id = campaign_id
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class FulfillResult:
    campaign_id: str
    message: str


@dataclass(slots=True)
class _Progress:
    """What this call changed, so a failure can put things back."""

    campaign_id: str = ""
    created_here: bool = False
    attached_now: list = field(default_factory=list)  # prospect ids attached during this call
    stopped: list[str] = field(default_factory=list)  # people stopped in their old campaign


def fulfill_approved_email(ctx, parsed: dict) -> FulfillResult:
    """Use the approved fields to create or update the campaign, then sync and activate it."""
    name = str(parsed.get("campaign_name") or "").strip()
    if not name:
        raise FulfillError("The campaign needs a name before it can be created.")
    subject, body = _subject_body(parsed)
    start, end, zone = _window(parsed.get("campaign_schedule_timing") or [])
    recipients = _recipients_fulfill(parsed)
    if not recipients:
        raise FulfillError("The email needs a recipient before it can be sent.")
    accounts = sending_account_emails()
    if not accounts:
        raise FulfillError("Instantly has no sending account, so the email cannot be sent.")
    move_from = {str(item or "").strip().lower() for item in parsed.get("move_from") or []}

    sender = (getattr(ctx, "display_name", None) or "").strip()
    db = SessionLocal()
    progress = _Progress()
    try:
        campaigns = CampaignService(db)
        if not sender:
            sender = (campaigns.settings.default_sender_name or "").strip()
        requested = str(parsed.get("campaign_id") or "").strip()

        # Preflight: decide everything that can be decided BEFORE writing anything.
        to_stop = _preflight(db, recipients, requested, move_from)

        campaign, progress.created_here = _campaign(db, campaigns, requested, name=name, sender=sender)
        campaign_id = progress.campaign_id = str(campaign.id)
        stated_name = str(parsed.get("person_name") or "").strip()
        already_active = (
            requested
            and campaign_id == requested
            and campaign.instantly_runtime_status == InstantlyRuntimeStatus.ACTIVE
        )
        if not already_active:
            campaigns.update_campaign(
                campaign.id,
                CampaignUpdate(
                    name=name,
                    sender_name=sender or campaign.sender_name,
                    send_timezone=zone,
                    send_window_start=start,
                    send_window_end=end,
                ),
            )
            campaign = campaigns.get_campaign(campaign.id)
        CampaignActivationService(db).save_sending_accounts(campaign.id, accounts)
        prospects = ProspectService(db)
        people = []
        for email in recipients:
            person = _person(db, prospects, email, stated_name)
            people.append(person)
            if person.approval_status != ProspectApprovalStatus.APPROVED:
                prospects.bulk_approve([person.id])
            try:
                campaigns.attach_prospect(campaign.id, person.id)
                progress.attached_now.append(person.id)
            except CampaignValidationError as exc:
                if "already attached" in exc.message:
                    continue
                raise FulfillError(exc.message, campaign_id=campaign_id) from exc
        template_subject, template_body = _template_copy(
            subject,
            body,
            people,
            assessment_url=(campaigns.settings.public_base_url or "").strip(),
            placeholder=campaigns.settings.assessment_url_placeholder,
        )
        _write_initial(campaigns, campaign, template_subject, template_body)
        if people and all(not (person.company or "").strip() for person in people):
            _drop_company_placeholder(db, campaign.id)
        InstantlyCampaignSyncService(db).sync(campaign.id)

        # Stop the old outreach as late as possible: everything above can fail without touching it.
        _stop_old_outreach(db, to_stop, progress)

        leads = LeadSyncService(db).sync_campaign_leads(campaign.id)
        if leads.synced < 1:
            raise FulfillError(
                leads.message or "The recipient was not added to the campaign.",
                campaign_id=campaign_id,
            )
        moved_note = _moved_note(to_stop)
        if already_active:
            return FulfillResult(
                campaign_id=campaign_id,
                message=f"The {name} campaign is already created and this email is already set to send.{moved_note}",
            )
        CampaignActivationService(db).activate(campaign.id, confirm=True)
    except FulfillError as exc:
        _undo(db, progress)
        raise FulfillError(
            _with_note(str(exc), progress), campaign_id=_surviving_id(db, progress, exc.campaign_id)
        ) from exc
    except ValidationError as exc:
        _undo(db, progress)
        raise FulfillError(
            _with_note("The campaign or recipient could not be saved.", progress),
            campaign_id=_surviving_id(db, progress, progress.campaign_id),
        ) from exc
    except (CampaignValidationError, InstantlySyncError, LeadSyncError, ActivationError) as exc:
        detail = getattr(exc, "message", None) or str(exc)
        missing = getattr(exc, "missing", None) or []
        if missing:
            detail = f"{detail} {' '.join(missing)}"
        _undo(db, progress)
        raise FulfillError(
            _with_note(detail.strip(), progress),
            campaign_id=_surviving_id(db, progress, progress.campaign_id),
        ) from exc
    finally:
        db.close()

    return FulfillResult(
        campaign_id=campaign_id,
        message=f"Created the {name} campaign and sent this email.{moved_note}",
    )


def _preflight(db, recipients: list[str], requested: str, move_from: set[str]) -> list:
    """Refuse or plan every recipient before anything is written. Returns the old links to stop."""
    to_stop: list = []
    for status in describe_people(db, recipients):
        label = status.label
        if any(link.opted_out for link in status.links):
            raise FulfillError(
                f"{label} unsubscribed or bounced earlier, so they cannot be added to any campaign."
            )
        elsewhere = [link for link in status.blocking if link.campaign_id != requested]
        if any(link.conversation for link in elsewhere):
            raise FulfillError(
                f"{label} already replied or started the assessment in {status.blocking_names}, "
                "so I will not move them. That one is best handled personally."
            )
        if not elsewhere:
            continue
        if status.email.strip().lower() not in move_from:
            raise FulfillError(
                f"{label} is already in {status.blocking_names}. Nothing was changed. "
                "Do you want me to stop them there and add them to this campaign?"
            )
        to_stop.extend(elsewhere)
    return to_stop


def _stop_old_outreach(db, to_stop: list, progress: _Progress) -> None:
    if not to_stop:
        return
    try:
        client = InstantlyService(db).get_api_client()
    except Exception as exc:  # noqa: BLE001
        raise FulfillError("Could not reach Instantly to stop the old outreach. Nothing was moved.") from exc
    for link in to_stop:
        try:
            stop_for_move(db, client, link.link_id)
        except StopError as exc:
            raise FulfillError(
                f"Instantly would not stop the outreach in {link.campaign_name}: {exc}"
            ) from exc
        progress.stopped.append(link.campaign_name or "the old campaign")


def _moved_note(to_stop: list) -> str:
    names = []
    for link in to_stop:
        if link.campaign_name and link.campaign_name not in names:
            names.append(link.campaign_name)
    return f" Their outreach in {' and '.join(names)} was stopped." if names else ""


def _with_note(message: str, progress: _Progress) -> str:
    if progress.stopped:
        return (
            f"{message} Note: outreach was already stopped in {' and '.join(dict.fromkeys(progress.stopped))}, "
            "so they are not in an active campaign right now. Ask me to try again."
        )
    return message


def _undo(db, progress: _Progress) -> None:
    """Best effort: leave no empty campaign or half-attached people behind."""
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass
    if not progress.campaign_id:
        return
    try:
        campaigns = CampaignService(db)
        cid = uuid.UUID(progress.campaign_id)
        campaign = db.get(Campaign, cid)
        if campaign is None:
            return
        if campaign.instantly_runtime_status == InstantlyRuntimeStatus.ACTIVE:
            return
        if progress.created_here:
            campaigns.delete_campaign(cid)
            progress.campaign_id = ""
            return
        for prospect_id in progress.attached_now:
            link = db.scalar(
                select(CampaignProspect).where(
                    CampaignProspect.campaign_id == cid,
                    CampaignProspect.prospect_id == prospect_id,
                )
            )
            if link is not None and not link.instantly_lead_id:
                campaigns.detach_prospect(cid, prospect_id)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _surviving_id(db, progress: _Progress, fallback: str) -> str:
    """Do not hand back the id of a campaign that was just deleted."""
    if not progress.campaign_id:
        return ""
    return fallback or progress.campaign_id


def _template_copy(
    subject: str,
    body: str,
    people: list,
    *,
    assessment_url: str,
    placeholder: str,
) -> tuple[str, str]:
    """Keep the approved wording. Swap the preview's name and company for Instantly fields."""
    text_subject = subject
    text_body = body
    for person in people:
        first, _last = split_executive_name(person.executive_name or "")
        company = (person.company or "").strip()
        if first:
            text_body = text_body.replace(f"Hi {first},", "Hi {{firstName}},")
        if company:
            text_subject = text_subject.replace(company, "{{companyName}}")
            text_body = text_body.replace(company, "{{companyName}}")
    if assessment_url:
        text_subject = text_subject.replace(assessment_url, placeholder)
        text_body = text_body.replace(assessment_url, placeholder)
    if "Hi there," in text_body:
        text_body = text_body.replace("Hi there,", "Hi {{firstName}},", 1)
    return text_subject, text_body


def _write_initial(campaigns: CampaignService, campaign, subject: str, body: str) -> None:
    current = campaigns.get_campaign(campaign.id)
    initial = next(
        (
            message
            for message in current.messages
            if message.is_active and message.message_type == MessageType.INITIAL
        ),
        None,
    )
    if initial is None:
        raise FulfillError("The campaign has no initial email to send.", campaign_id=str(campaign.id))
    if (initial.subject or "") == subject and (initial.body or "") == body:
        return
    campaigns.update_message(
        current.id,
        initial.id,
        CampaignMessageUpdate(subject=subject, body=body, delay_after_previous_hours=0),
    )


def _campaign(db, campaigns: CampaignService, raw_id, *, name: str, sender: str):
    """Return (campaign, created_here). A bad id is an error, never a silent new campaign."""
    text = str(raw_id or "").strip()
    if text:
        try:
            return campaigns.get_campaign(uuid.UUID(text)), False
        except (ValueError, CampaignNotFoundError) as exc:
            raise FulfillError(
                "I could not find the campaign you picked. Nothing was created. Please pick it again."
            ) from exc
    clash = db.scalar(select(Campaign).where(func.lower(Campaign.name) == name.strip().lower()))
    if clash is not None:
        raise FulfillError(
            f"A campaign called {clash.name} already exists. Do you want to use it, or pick a different name?"
        )
    created = campaigns.create_campaign(
        CampaignCreate(name=name, sender_name=sender or None, seed_default_sequence=True)
    )
    return created, True


def _person(db, prospects: ProspectService, email: str, person_name: str) -> Prospect:
    stated = person_name.strip()
    found = db.scalar(select(Prospect).where(func.lower(Prospect.email) == email.lower()))
    if found is not None:
        _keep_stated_name(db, found, stated)
        _clear_mailbox_company(db, found)
        return found
    local, _, _domain = email.partition("@")
    created = prospects.create_prospect(
        ProspectCreate(
            company=local or email,
            executive_name=stated or (local or email).strip(),
            email=email,
            source="assistant",
        )
    )
    created.company = ""
    db.add(created)
    db.commit()
    db.refresh(created)
    return created


def _drop_company_placeholder(db, campaign_id) -> None:
    """Follow-ups must not address a company that was never identified."""
    from acquisition.models.campaign import CampaignMessage

    rows = list(
        db.scalars(select(CampaignMessage).where(CampaignMessage.campaign_id == campaign_id)).all()
    )
    changed = False
    for message in rows:
        subject = (message.subject or "").replace(" for {{companyName}}", "").replace(" {{companyName}}", "")
        body = (message.body or "").replace(" for {{companyName}}", "").replace("{{companyName}}", "")
        if subject != (message.subject or "") or body != (message.body or ""):
            message.subject = subject
            message.body = body
            db.add(message)
            changed = True
    if changed:
        db.commit()


def _clear_mailbox_company(db, prospect: Prospect) -> None:
    """An email host is not a company. Leave company empty when none was identified."""
    company = (prospect.company or "").strip()
    host = (prospect.email or "").partition("@")[2].strip()
    if not company or not host or company.casefold() != host.casefold():
        return
    prospect.company = ""
    db.add(prospect)
    db.commit()


def _keep_stated_name(db, prospect: Prospect, stated: str) -> None:
    """Replace a mailbox stored in the name field with the name the user gave."""
    if not stated:
        return
    current = (prospect.executive_name or "").strip()
    local = prospect.email.split("@", 1)[0].strip()
    if current.casefold() not in {local.casefold(), prospect.email.casefold(), ""}:
        return
    if current == stated:
        return
    prospect.executive_name = stated
    db.add(prospect)
    db.commit()
    first_name, last_name = split_executive_name(stated)
    lead_ids = [
        lead_id
        for lead_id in db.scalars(
            select(CampaignProspect.instantly_lead_id).where(
                CampaignProspect.prospect_id == prospect.id,
                CampaignProspect.instantly_lead_id.is_not(None),
            )
        ).all()
        if lead_id
    ]
    if not lead_ids:
        return
    try:
        client = InstantlyService(db).get_api_client()
    except Exception:
        return
    for lead_id in lead_ids:
        try:
            client.update_lead(
                lead_id,
                {"first_name": first_name or None, "last_name": last_name or None},
            )
        except Exception:
            continue


def _recipients_fulfill(parsed: dict) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    single = str(parsed.get("email_address") or "").strip()
    values = [single] if single else []
    values.extend(str(item or "").strip() for item in parsed.get("email_address_list") or [])
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            found.append(value)
    return found


def _subject_body(parsed: dict) -> tuple[str, str]:
    draft = str(parsed.get("email_draft") or "").strip()
    if draft.lower().startswith("subject:"):
        first, _, rest = draft.partition("\n")
        subject = first.split(":", 1)[1].strip()
        body = rest.strip()
        if subject and body:
            return subject, body
    subject = str(parsed.get("email_subject") or "").strip()
    body = str(parsed.get("email_body") or "").strip()
    if not subject or not body:
        raise FulfillError("The approved email is missing a subject or a body.")
    return subject, body


def _window(timing: list) -> tuple[str, str, str]:
    if len(timing) < 2:
        raise FulfillError("The campaign needs a start time and an end time.")
    start, start_zone = _clock_fulfill(str(timing[0]))
    end, end_zone = _clock_fulfill(str(timing[1]))
    if start >= end:
        raise FulfillError("The send window start has to be earlier than the end.")
    zone = normalize_instantly_timezone(start_zone or end_zone or "America/Chicago")
    if zone not in _allowed_zones():
        raise FulfillError("The schedule timezone is not one Instantly can use.")
    return start, end, zone


def _clock_fulfill(value: str) -> tuple[str, str | None]:
    parts = value.replace(".", " ").split()
    zone = None
    if parts and parts[-1].lower() not in {"am", "pm"} and parts[-1].isalpha():
        zone = parts[-1]
        parts = parts[:-1]
    clock = " ".join(parts).upper()
    for layout in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            parsed = datetime.strptime(clock, layout)
        except ValueError:
            continue
        return parsed.strftime("%H:%M"), zone
    raise FulfillError(f"Could not read the send time {value.strip() or '(blank)'}.")


def _allowed_zones() -> set[str]:
    from acquisition.integrations.instantly.timezones import INSTANTLY_TIMEZONE_SET

    return INSTANTLY_TIMEZONE_SET


####################################################################################################
# PART 3 - CAMPAIGN TOOLS (update_selection, show_contacts, show_campaigns, propose_email, approve_move, send_approved)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# campaign_tools  (app/assistant/campaign_tools.py) - Campaign tools for the unified assistant.
# ------------------------------------------------------------------------------------------------
import difflib
from typing import Any, Callable

from agno.tools import tool



def _audit(event: str, ctx, payload: dict, *, status: str = "", **extra) -> None:
    """Record the send in the platform audit log. Never lets logging break the send."""
    try:
        from bizos.audit import events as audit
        from bizos.types import AuditEventType

        audit.log(
            AuditEventType(event),
            ctx=ctx,
            tool="campaign_send_approved",
            status=status or event,
            request={
                "campaign": payload.get("campaign_name"),
                "recipients": payload.get("email_address_list"),
                "moved_from_other_campaigns": payload.get("move_from") or [],
                "subject": payload.get("email_subject"),
            },
            result={k: v for k, v in extra.items() if v},
        )
    except Exception:  # noqa: BLE001
        pass


_WHY = {"type": "string", "description": "1-2 specific sentences: what you understood and why this call is right now."}

CAMPAIGN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_selection",
            "description": (
                "Commit what the user decided: recipients, campaign, and/or daily send window. Merges into the "
                "current selection. Existing campaign: pass campaign_id (its saved window is used). New campaign: "
                "pass campaign_name plus window_start and window_end. Call this as soon as the user's meaning gives you any of these."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recipients": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"email": {"type": "string"}, "name": {"type": "string"}},
                            "required": ["email"],
                        },
                    },
                    "recipients_mode": {
                        "type": "string",
                        "enum": ["replace", "add"],
                        "description": "add = keep current recipients and add these; replace = these become the only recipients.",
                    },
                    "campaign_id": {"type": "string"},
                    "campaign_name": {"type": "string"},
                    "confirmed_new_campaign": {
                        "type": "boolean",
                        "description": "true only after the user confirmed they want a brand-new campaign despite a similar existing name.",
                    },
                    "window_start": {"type": "string", "description": "clock time like 9:00 am"},
                    "window_end": {"type": "string", "description": "clock time like 5:00 pm"},
                    "window_evidence": {
                        "type": "string",
                        "description": "REQUIRED with window_start/window_end: copy the user's own words that gave the hours (e.g. '9 am to 5 pm'). Never invent or default hours; if the user has not said them, do not pass a window, ask instead.",
                    },
                    "why": _WHY,
                },
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_contacts",
            "description": "Show the saved contacts table so the user can pick. Refused if recipients are already committed (including this turn). Use change_recipients=true ONLY when the user explicitly asks to see the list again; to switch people, call update_selection instead.",
            "parameters": {
                "type": "object",
                "properties": {"change_recipients": {"type": "boolean"}, "why": _WHY},
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_campaigns",
            "description": "Show the saved campaigns table (only ones the committed recipients are not already active in). Requires recipients to be committed. Refused if a campaign is already committed unless change_campaign=true.",
            "parameters": {
                "type": "object",
                "properties": {"change_campaign": {"type": "boolean"}, "why": _WHY},
                "required": ["why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_email",
            "description": "Build the draft from the committed selection and show it for approval. Requires recipients, campaign and window to be committed.",
            "parameters": {
                "type": "object",
                "properties": {"subject": {"type": "string"}, "body": {"type": "string"}, "why": _WHY},
                "required": ["subject", "body", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_move",
            "description": (
                "Record that the user said yes to stopping these people in their other campaign and adding them to the new one. "
                "Only after you explained it and the user's LATEST message clearly agrees. Refused if you had not told them yet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "emails": {"type": "array", "items": {"type": "string"}},
                    "approval_evidence": {"type": "string", "description": "the user's words that count as yes"},
                    "why": _WHY,
                },
                "required": ["emails", "approval_evidence", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_approved",
            "description": "Create the campaign and send the pending draft. Only after the user's latest message approves it.",
            "parameters": {
                "type": "object",
                "properties": {"approval_evidence": {"type": "string", "description": "the user's words that count as approval"}, "why": _WHY},
                "required": ["approval_evidence", "why"],
            },
        },
    },
]




# ---------- helpers (validation only, no wording) ----------

def _clock_campaign(value: str) -> str | None:
    text = " ".join((value or "").upper().replace(".", "").split())
    for candidate in (text, text.replace(" ", "")):
        for layout in ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p", "%H:%M"):
            try:
                t = datetime.strptime(candidate, layout)
            except ValueError:
                continue
            return f"{t.hour % 12 or 12}:{t:%M} {'am' if t.hour < 12 else 'pm'}"
    return None


def _people(records: dict) -> list[dict]:
    return records.get("people") or []


def _find_person(records: dict, email: str) -> dict | None:
    key = (email or "").strip().casefold()
    return next((p for p in _people(records) if str(p.get("email") or "").casefold() == key), None)


def _find_campaign(records: dict, cid: str = "", name: str = "") -> dict | None:
    for c in records.get("campaigns") or []:
        if cid and str(c.get("campaign_id") or "") == cid:
            return c
        if name and str(c.get("campaign_name") or "").strip().casefold() == name.strip().casefold():
            return c
    return None


def _active_ids(person: dict | None) -> set[str]:
    return {str(c.get("campaign_id") or "") for c in (person or {}).get("active_campaigns") or []}


def _campaign_timing(c: dict) -> str:
    label = str(c.get("schedule_label") or "").strip()
    return label or f"{c.get('schedule_start') or ''}–{c.get('schedule_end') or ''}".strip("–")



def _missing(state: dict) -> list[str]:
    out = []
    if not state.get("recipients"):
        out.append("recipients")
    if not state.get("campaign_name"):
        out.append("campaign")
    if len(state.get("window") or []) < 2:
        out.append("daily send window")
    return out


def campaign_state_text(state: dict, people_status: list | None = None) -> str:
    view = {
        "people_status": people_status if people_status is not None else "unavailable",
        "moves_user_approved": state.get("moves") or [],
        "committed_selection": {
            "recipients": state.get("recipients") or [],
            "campaign_id": state.get("campaign_id") or "",
            "campaign_name": state.get("campaign_name") or "",
            "daily_window": state.get("window") or [],
        },
        "still_missing": _missing(state),
        "last_shown_to_user_in_tables_in_order": state.get("shown") or {"people": [], "campaigns": []},
        "pending_draft": state.get("draft", {}).get("email_draft") if state.get("draft") else None,
        "your_previous_turn_explanation": state.get("last_explanation") or "",
    }
    return "Current state:\n" + json.dumps(view, indent=1, default=str)




# ---------- tool execution ----------

import re

_SENT_CLAIM = re.compile(
    r"send_approved|\b(?:email|message|draft)\b[^.\n]{0,40}\b(?:was|has been|is|been) sent\b"
    r"|\bcampaign\b[^.\n]{0,40}\b(?:was|has been) created\b|\b(?:i(?:'ve| have)? )?sent (?:the|this|your) email\b",
    re.I,
)

NOT_SENT_TEXT = (
    "Nothing has been sent yet. The email draft is still waiting. Say \"send it\" and I will send it."
)


class CampaignSession:
    """One turn of the campaign flow: the committed selection plus the tools that change it.

    Code validates and persists state; it never writes user-facing words. Every result goes back to the
    model, which decides what to say."""

    def __init__(self, ctx, records: dict, state: dict, *, send_block: str = ""):
        self.ctx, self.records = ctx, records
        #: Non-empty when this workspace's mode forbids sending (Advise). The model is told why.
        self.send_block = send_block
        self.state = json.loads(json.dumps(state)) if state else {}
        self.people_to_show: list[dict] = []
        self.campaigns_to_show: list[dict] = []
        self.sent = False
        self.log: list[dict] = []
        self.self_check: list[str] = []
        self.errors = 0
        self.asked_similar = False  # the similar-name question is the table the user needs
        self.user_message = ""  # set by the agent; lets the self-check see what the user asked
        self.user_texts: list[str] = []  # everything the user has written in this chat (for evidence checks)
        self.recipients_committed = False  # recipients committed during this turn
        self.started_with_recipients = bool(self.state.get("recipients"))
        # People already explained to the user in an earlier turn. A move can only be approved for these.
        self.offered_before = set(self.state.get("move_offered") or [])

    def call(self, name: str, args: dict) -> str:
        why = str(args.pop("why", "") or "")
        try:
            logged_args = json.dumps(args, ensure_ascii=False, default=str)[:600]
        except Exception:  # noqa: BLE001
            logged_args = "unserialisable"
        try:
            result = getattr(self, f"_{name}")(**args)
        except TypeError:
            result = "error: bad arguments"
        if result.startswith("error") or '"error"' in result:
            self.errors += 1
        self.log.append({"tool": name, "args": logged_args, "why": why, "result": result[:300]})
        return result

    def _in_latest_message(self, quote: str) -> bool:
        """True when ``quote`` is the user's own words in their latest message (case and spacing ignored)."""
        norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
        q = norm(quote)
        return bool(q) and re.search(rf"(?<!\w){re.escape(q)}(?!\w)", norm(self.user_message)) is not None

    def _user_said(self, quote: str) -> bool:
        """True when ``quote`` really is something the user wrote (this turn or earlier)."""
        norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
        q = norm(quote)
        return bool(q) and any(q in norm(text) for text in self.user_texts)

    def claims_unsent(self, reply: str = "", explanation: str = "") -> bool:
        """True when the model says the email was sent/the campaign created, but send_approved never succeeded."""
        if self.sent or not self.state.get("draft"):
            return False
        return bool(_SENT_CLAIM.search(f"{reply}\n{explanation}"))

    def problems(self, reply: str = "", explanation: str = "") -> list[str]:
        """Consistency check between the model's turn and the state it just built.
        Returned as notes for the model to act on; code never rewrites the reply."""
        s, out = self.state, []
        if self.claims_unsent(reply, explanation):
            out.append(
                "Your reply says the email was sent or the campaign was created, but send_approved has NOT "
                "succeeded this turn, so nothing was sent. If the user's latest message approves the pending "
                "draft, call send_approved now (approval_evidence = their words) and report only what it "
                "returns. If it does not clearly approve, do not claim a send; ask one short question."
            )
        miss = _missing(s)
        if (
            not self.log
            and not s.get("recipients")
            and not s.get("campaign_name")
            and not s.get("campaign_id")
            and re.search(r"\bcampaigns?\b", self.user_message or "", re.I)
        ):
            out.append(
                "The user's message mentions a campaign but you committed nothing. If they gave you any "
                "recipient, campaign name or send hours, call update_selection with it now, BEFORE you ask "
                "your question. If this message is not about emailing people in a campaign, keep your answer "
                "and say so in the explanation."
            )
        if self.people_to_show and (self.recipients_committed or self.started_with_recipients):
            out.append(
                "You showed the contacts table although recipients were already chosen. Unless the user explicitly "
                "asked to see the list again, commit their choice with update_selection and show the campaigns table."
            )
        if not miss and not s.get("draft") and not self.sent and not self.errors:
            out.append(
                "Recipients, campaign and window are all committed but there is no draft. Call propose_email now "
                "and do not ask the user anything."
            )
        if self.recipients_committed and miss[:1] == ["campaign"] and not self.campaigns_to_show and not self.asked_similar:
            out.append(
                "Recipients are committed and a campaign is still needed, but no campaigns table is shown. If the user "
                "has not said they want a new campaign, call show_campaigns so they can see the options you ask about."
            )
        return out

    def _assess(self):
        """Fresh per-person status from the database (same rule as the lead sync). None if it cannot be read."""
        emails = [r["email"] for r in self.state.get("recipients") or []]
        if not emails:
            return []
        try:
            return check_people(emails)
        except Exception:  # noqa: BLE001
            return None

    def status_view(self) -> list | None:
        found = self._assess()
        return None if found is None else [st.as_dict() for st in found]

    def _gate(self, statuses) -> tuple[list[str], list]:
        """(refusals, people who still need a move approval) for the committed campaign."""
        cid = self.state.get("campaign_id") or ""
        approved = {str(e).lower() for e in self.state.get("moves") or []}
        refusals: list[str] = []
        needs: list = []
        for st in statuses:
            if any(link.opted_out for link in st.links):
                refusals.append(f"{st.label} unsubscribed or bounced earlier. Never add them to any campaign.")
                continue
            elsewhere = [link for link in st.blocking if link.campaign_id != cid]
            if any(link.conversation for link in elsewhere):
                refusals.append(
                    f"{st.label} already replied, started the assessment or booked in {st.blocking_names}. "
                    "Do not move or add them; suggest handling them personally."
                )
            elif elsewhere and st.email.lower() not in approved:
                needs.append(st)
        return refusals, needs

    def _blocked(self) -> str:
        """Empty string when the selection may go forward, else a JSON error for the model."""
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        refusals, needs = self._gate(statuses)
        if refusals:
            return json.dumps(
                {
                    "error": "refused_person",
                    "reasons": refusals,
                    "instruction": "Tell the user plainly. Offer to continue without these people (update_selection with recipients_mode replace).",
                }
            )
        if needs:
            self._mark_offered(needs)
            return json.dumps(
                {
                    "error": "needs_move_approval",
                    "people": [st.as_dict() for st in needs],
                    "instruction": (
                        "Tell the user where each person is. Adding them here stops their outreach there (history stays). "
                        "Ask one yes/no. Call approve_move only after their NEXT message says yes."
                    ),
                }
            )
        return ""

    def _mark_offered(self, people) -> None:
        offered = set(self.state.get("move_offered") or [])
        offered.update(st.email.lower() for st in people)
        self.state["move_offered"] = sorted(offered)

    def _invalidate_draft(self) -> None:
        self.state.pop("draft", None)

    def _update_selection(self, recipients=None, recipients_mode="replace", campaign_id="", campaign_name="", confirmed_new_campaign=False, window_start="", window_end="", window_evidence="") -> str:
        s = self.state
        notes: list[str] = []

        if recipients:
            rows, seen = [], set()
            for r in recipients:
                email = str(r.get("email") or "").strip()
                if "@" not in email or email.casefold() in seen:
                    continue
                seen.add(email.casefold())
                known = _find_person(self.records, email)
                rows.append(
                    {
                        "email": str((known or {}).get("email") or email),
                        "name": str((known or {}).get("name") or r.get("name") or "").strip(),
                    }
                )
            if not rows:
                return "error: none of the recipients had a valid email"
            if recipients_mode == "add":
                have = {r["email"].casefold() for r in s.get("recipients") or []}
                rows = (s.get("recipients") or []) + [r for r in rows if r["email"].casefold() not in have]
            if rows != s.get("recipients"):
                self._invalidate_draft()
            s["recipients"] = rows
            keep = {r["email"].lower() for r in rows}
            s["moves"] = [e for e in s.get("moves") or [] if e in keep]
            self.recipients_committed = True
            self.people_to_show = []
            s.setdefault("shown", {})["people"] = []

        if campaign_id or campaign_name:
            existing = _find_campaign(self.records, campaign_id, campaign_name)
            if existing:
                new_id_, new_name = str(existing.get("campaign_id") or ""), str(existing.get("campaign_name") or "")
                start = _clock_campaign(str(existing.get("schedule_start") or "")) or str(existing.get("schedule_start") or "")
                end = _clock_campaign(str(existing.get("schedule_end") or "")) or str(existing.get("schedule_end") or "")
                window = [start, end]
            else:
                typed = (campaign_name or "").strip()
                similar = [] if confirmed_new_campaign else self._similar_names(typed)
                if similar:
                    self.asked_similar = True
                    return json.dumps(
                        {
                            "error": "similar_campaign_name",
                            "typed": typed,
                            "similar_to": similar,
                            "instruction": (
                                "Ask the user whether they mean the existing campaign or a new one with the typed name. "
                                "Existing: update_selection with its campaign_id. New: update_selection again with "
                                "confirmed_new_campaign=true."
                            ),
                            "selection": self._sel(),
                        }
                    )
                new_id_, new_name = "", typed
                window = s.get("window") if new_name == s.get("campaign_name") else []
            if (new_id_, new_name, window) != (s.get("campaign_id"), s.get("campaign_name"), s.get("window")):
                self._invalidate_draft()
            s["campaign_id"], s["campaign_name"], s["window"] = new_id_, new_name, window or []
            self.campaigns_to_show = []
            s.setdefault("shown", {})["campaigns"] = []

        if window_start or window_end:
            if _find_campaign(self.records, s.get("campaign_id", ""), s.get("campaign_name", "")):
                notes.append("ignored window: an existing campaign keeps its saved window")
            else:
                if not self._user_said(window_evidence):
                    return json.dumps(
                        {
                            "error": "window_not_given_by_user",
                            "instruction": (
                                "The user has not told you the daily send hours, so I did not save any. Never invent or "
                                "default them. Ask: 'What daily send hours should it use, for example 9 am to 5 pm?'. "
                                "When they answer, call update_selection with the hours and window_evidence set to their words."
                            ),
                            "selection": self._sel(),
                        }
                    )
                a, b = _clock_campaign(window_start), _clock_campaign(window_end)
                if not (a and b):
                    return "error: need both window_start and window_end as clock times"
                if [a, b] != s.get("window"):
                    self._invalidate_draft()
                s["window"] = [a, b]

        conflict = self._conflict()
        if conflict:
            s["campaign_id"], s["campaign_name"], s["window"] = "", "", []
            self._invalidate_draft()
            return json.dumps({"error": conflict, "selection": self._sel(), "still_missing": _missing(s), "people_status": self._status_for_result()})
        return json.dumps(
            {
                "ok": True,
                "selection": self._sel(),
                "still_missing": _missing(s),
                "notes": notes,
                "people_status": self._status_for_result(),
            }
        )

    def _status_for_result(self):
        statuses = self._assess()
        if statuses is None:
            return "unavailable: tell the user you could not check existing campaigns"
        self._mark_offered([st for st in statuses if st.tier.startswith("move_")])
        return [st.as_dict() for st in statuses]

    def _similar_names(self, typed: str) -> list[str]:
        key = typed.casefold()
        if not key:
            return []
        names = [str(c.get("campaign_name") or "") for c in self.records.get("campaigns") or []]
        by_key = {n.casefold(): n for n in names if n}
        return [by_key[k] for k in difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.8) if k != key]

    def _sel(self) -> dict:
        s = self.state
        return {
            "recipients": s.get("recipients") or [],
            "campaign": s.get("campaign_name") or "",
            "window": s.get("window") or [],
        }

    def _conflict(self) -> str:
        s = self.state
        cid = s.get("campaign_id") or ""
        if not cid:
            return ""
        clash = [
            r["email"] for r in s.get("recipients") or []
            if cid in _active_ids(_find_person(self.records, r["email"]))
        ]
        if clash:
            return f"{', '.join(clash)} already active in campaign '{s.get('campaign_name')}'. Campaign choice cleared. Offer another existing campaign or a new one."
        return ""

    def _show_contacts(self, change_recipients: bool = False) -> str:
        s = self.state
        if self.recipients_committed:
            return "error: you just committed recipients this turn. Do not show contacts; show the campaigns table or continue."
        if s.get("recipients") and not change_recipients:
            names = ", ".join(r["email"] for r in s["recipients"])
            return f"error: recipients already committed ({names}). Do not show the contacts table; to switch people call update_selection."
        rows = [
            {"name": str(p.get("name") or ""), "email": str(p.get("email") or "")}
            for p in _people(self.records)
            if p.get("email")
        ]
        if not rows:
            return "error: no saved contacts"
        self.people_to_show = rows
        s.setdefault("shown", {})["people"] = rows
        return json.dumps({"table_shown_in_order": rows})

    def _show_campaigns(self, change_campaign: bool = False) -> str:
        s = self.state
        if not s.get("recipients"):
            return "error: commit recipients first so I can exclude campaigns they are already active in"
        if s.get("campaign_name") and not change_campaign:
            return f"error: campaign '{s['campaign_name']}' is already committed. Do not show the table again; continue to the email."
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        refusals, _needs = self._gate(statuses)
        if refusals:
            return json.dumps({"error": "refused_person", "reasons": refusals})
        blocked: set[str] = set()
        for st in statuses:
            blocked |= {link.campaign_id for link in st.blocking}
        for r in s["recipients"]:
            blocked |= _active_ids(_find_person(self.records, r["email"]))
        rows = [
            {"name": str(c.get("campaign_name") or ""), "timing": _campaign_timing(c)}
            for c in self.records.get("campaigns") or []
            if c.get("campaign_name") and str(c.get("campaign_id") or "") not in blocked
        ]
        self.campaigns_to_show = rows
        s.setdefault("shown", {})["campaigns"] = rows
        return json.dumps(
            {
                "table_shown_in_order": rows,
                "excluded_because_already_active_ids": sorted(blocked),
                "note": "empty table means no other saved campaign is available; ask for a new campaign name and daily window",
            }
        )

    def _propose_email(self, subject: str, body: str) -> str:
        s = self.state
        miss = _missing(s)
        if miss:
            return f"error: cannot propose yet, still missing: {', '.join(miss)}"
        conflict = self._conflict()
        if conflict:
            return f"error: {conflict}"
        blocked = self._blocked()
        if blocked:
            return blocked
        if not (subject or "").strip() or not (body or "").strip():
            return "error: subject and body required"
        subject, body = subject.strip(), body.strip()
        s["draft"] = {
            "email_subject": subject,
            "email_body": body,
            "email_draft": f"Subject: {subject}\n\n{body}",
            "email_from": ", ".join(sending_account_emails()),
        }
        self.people_to_show, self.campaigns_to_show = [], []
        s["shown"] = {"people": [], "campaigns": []}
        return "ok: draft is shown to the user as a card. Ask one short question about it."

    def _payload(self) -> dict:
        s, d = self.state, self.state["draft"]
        emails = [r["email"] for r in s["recipients"]]
        return {
            "email_address": emails[0] if len(emails) == 1 else "",
            "email_address_list": emails,
            "person_name": " and ".join(r["name"] for r in s["recipients"] if r.get("name")),
            "campaign_id": s.get("campaign_id") or "",
            "campaign_name": s["campaign_name"],
            "campaign_schedule_timing": s["window"],
            "move_from": list(s.get("moves") or []),
            "email_from": d["email_from"],
            "email_subject": d["email_subject"],
            "email_body": d["email_body"],
            "email_draft": d["email_draft"],
            "tool": "execute",
        }

    def _approve_move(self, emails=None, approval_evidence: str = "") -> str:
        s = self.state
        statuses = self._assess()
        if statuses is None:
            return json.dumps({"error": "Could not check who is already in a campaign. Tell the user and try again."})
        by_email = {st.email.lower(): st for st in statuses}
        accepted, problems = [], []
        for raw in emails or []:
            key = str(raw or "").strip().lower()
            st = by_email.get(key)
            if st is None:
                problems.append(f"{raw}: not one of the chosen recipients")
            elif st.tier in ("refuse_opted_out", "refuse_conversation"):
                problems.append(f"{st.label}: cannot be moved ({st.tier})")
            elif not st.tier.startswith("move_"):
                problems.append(f"{st.label}: nothing to move, they are not active in another campaign")
            elif key not in self.offered_before:
                problems.append(
                    f"{st.label}: you have not explained this to the user in an earlier turn. Explain it, ask, and wait for their answer."
                )
            else:
                accepted.append(key)
        if problems:
            return json.dumps({"error": "move_not_approved", "problems": problems, "recorded": []})
        s["moves"] = sorted(set(s.get("moves") or []) | set(accepted))
        self._invalidate_draft()
        return json.dumps({"ok": True, "moves_approved_for": s["moves"], "approval_evidence": (approval_evidence or "")[:200]})

    def _send_approved(self, approval_evidence: str = "") -> str:
        if not self.state.get("draft"):
            return "error: no draft pending, nothing to send"
        if not self._in_latest_message(approval_evidence):
            _audit(
                "ACTION_FAILED", self.ctx, self._payload(), status="APPROVAL_EVIDENCE_NOT_FOUND",
                evidence=(approval_evidence or "")[:200],
            )
            return (
                "error: approval_not_found: approval_evidence must be the user's own words copied from their "
                "latest message. Nothing was sent. Ask the user whether to send the email as shown."
            )
        if self.send_block:
            return f"error: {self.send_block}"
        conflict = self._conflict()
        if conflict:
            return f"error: {conflict}"
        blocked = self._blocked()
        if blocked:
            return blocked
        payload = self._payload()
        try:
            outcome = fulfill_approved_email(self.ctx, payload)
        except FulfillError as exc:
            if exc.campaign_id:
                self.state["campaign_id"] = exc.campaign_id
            _audit("ACTION_FAILED", self.ctx, payload, error=str(exc)[:300], evidence=approval_evidence)
            return f"error: {exc}"
        self.sent = True
        self.state["campaign_id"] = outcome.campaign_id
        _audit("ACTION_EXECUTED", self.ctx, payload, result=outcome.message[:300], evidence=approval_evidence)
        return f"sent: {outcome.message}"




# ---------- policy check ----------
# Every campaign call goes through guarded_call like any other tool (ToolSpecs in
# bizos/rbac/registry.py). The effect hands the call to the turn's CampaignSession.


def _campaign_effect(tool_name: str) -> Callable:
    def run(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
        session = scope.campaign_session
        if session is None:
            return ConnectorResult.failure("The campaign tools only work inside an admin's chat turn.")
        text = session.call(tool_name, dict(args))
        failed = text.startswith("error") or '"error"' in text
        return ConnectorResult(ok=not failed, summary=text, error=text if failed else None)

    return run


for _campaign_tool in CAMPAIGN_TOOLS:
    effect(_campaign_tool["function"]["name"])(_campaign_effect(_campaign_tool["function"]["name"]))


# ---------- Agno wiring ----------

def build_campaign_tools(session: CampaignSession, scope: RunScope) -> list[Callable]:
    """Agno callables for the campaign tools, all bound to one ``CampaignSession``.

    Each call goes through the policy check. All six stay offered (as before), so a refusal, such as
    sending in Advise mode, comes back to the model as an explanation it can pass on.
    """
    from bizos.tools.base import guarded_call

    bound_scope = dataclasses.replace(scope, campaign_session=session)
    tools: list[Callable] = []
    for spec in CAMPAIGN_TOOLS:
        fn = spec["function"]
        name = fn["name"]

        def _make(tool_name: str, description: str, schema: dict) -> Callable:
            @tool(name=tool_name, description=description)
            def _invoke(**kwargs: Any) -> str:
                logged = len(session.log)
                outcome = guarded_call(bound_scope, tool_name, dict(kwargs))
                if len(session.log) > logged:
                    return outcome.message  # the session ran: its own result, exactly as before
                # Stopped before the session ran (policy or a failure); keep the trace complete.
                text = outcome.agent_text()
                session.errors += 1
                session.log.append(
                    {"tool": tool_name, "args": "", "why": str(kwargs.get("why") or ""), "result": text[:300]}
                )
                return text

            # Replace Agno's **kwargs schema with the real one.
            _invoke.parameters = schema
            _invoke.strict = False
            _invoke.skip_entrypoint_processing = True
            return _invoke

        tools.append(_make(name, fn["description"], fn["parameters"]))
    return tools


####################################################################################################
# PART 4 - THE PROMPT
####################################################################################################

# ------------------------------------------------------------------------------------------------
# prompts  (app/assistant/prompts.py) - THE prompt. Everything the model is told lives in this one file.
# ------------------------------------------------------------------------------------------------

from bizos.agents import instructions as agent_instructions

MAX_PEOPLE_IN_PROMPT = 200

APPROACH = """
# HOW YOU WORK (every task, every turn)

You are one assistant for the whole workspace. You decide, from the conversation, the records and your
tool results, which tool to use, in which order, and what to say. The platform never writes your words:
it validates, returns facts (including why something was refused), and records what happened.

- Your answer is a JSON object with two fields. `message` is the only text the user sees: natural,
  specific, varied; never repeat the same sentence twice in a row. `explanation` is for the developer
  trace and is never shown to the user.
- EVERY tool call needs `why`: one or two specific sentences on what you understood and why this call
  is right now.
- Call a tool only when its requirements are met. If something required is missing, ask for that one
  thing. If a tool returns an error, fix it or tell the user honestly. Never claim something was saved,
  sent, queued or scheduled unless a tool result says so.
- Before answering, check your own draft against the tool results of this turn: does it claim anything
  no tool did? Does it mention anything the user is not allowed to see? Does it ask something the user
  already told you?
- If a "Self-check" note follows your draft, a platform check flagged it. Fix the reply in your own
  words, using tools if needed, then answer again.
- `explanation` content (5 to 9 sentences): (a) what you understood the user to mean, including how you
  resolved references like "first 2" or "that one"; (b) what is decided vs still missing; (c) which tools
  you called, or deliberately did not, and why; (d) what you are showing or not showing and why, and what
  the user should do next; (e) why you took this action rather than another.
- Greetings, small talk, thanks, typos: answer like a person. Do not start any flow or call any tool.
"""

CAMPAIGN_SECTION = """
# OUTBOUND CAMPAIGNS (admin only; the campaign tools are in your tool list)

Use the campaign tools when the user wants to email prospects as an outbound campaign: pick people, pick or
create a campaign, review one email, send it once they approve. These are different from the email_* tools,
which send one message from the user's own mailbox (a reply, a one-off note). "Add them to a campaign",
"campaign", "my people / contacts / prospects" (the list below) point at the campaign tools. "Email John
about the invoice" or "reply to this thread" points at email_*. If it is genuinely unclear, ask one short
question. The "people" below are Acquisition prospects; CRM contacts are separate.

Every turn you get a "Current state" block. It is the truth: the committed selection (recipients, campaign,
daily send window), what is still missing, the exact rows you last showed the user in tables (in order), the
pending draft, and people_status for the chosen recipients.

1. Read the latest message, the conversation and Current state. Resolve references ("first 2", "both",
   "that one", "Craig", "same as before") against "last shown" or the committed selection.
2. The moment the user's meaning gives you a fact (recipients, campaign, hours), COMMIT it with
   update_selection in this same turn. Do not wait, do not re-ask, do not show the table again.
   COMMIT FIRST, ASK SECOND: even when something is still missing, call update_selection with everything
   you already know BEFORE you write your question. Asking a question without committing what the user
   already told you is wrong, because the next turn starts from what was committed.
   Example: "email Ethan in a new campaign called snowflake" gives you the recipient (Ethan) and the
   campaign name (snowflake). Call update_selection with both now. Then ask only for the missing daily
   send window. (If the name is close to an existing campaign you will get similar_campaign_name; then ask
   the existing-or-new question.)
3. Then do the next useful thing: ask for the one missing item, show the table that item needs, or propose
   the email.

TABLES (UI cards)
- The contacts table appears only if you call show_contacts; the campaigns table only if you call
  show_campaigns. Show one only when the user must choose and you do not yet have the answer. Never show
  contacts after recipients are committed, and never "just in case".
- Right after you commit recipients, the next table is the CAMPAIGNS table (unless they already named a
  campaign). Asking "which campaign?" without showing it leaves the user blind.
- After the user picks a campaign, commit it; do not show the table again.
- "add last 2", "also Debbie", "both": resolve against the known people order or the last shown rows and
  commit with update_selection (recipients_mode "add" to keep current ones, "replace" to switch).
- Never list names, emails or campaigns in your message. The table is the list; after a table your message
  is one short question.

MOVING FORWARD
- Once recipients, campaign and window are all committed, call propose_email in that same turn. Never ask
  "do you want to proceed" and never re-confirm a choice they already made. The email card is the only
  confirmation step. Never raise a caveat that did not come from a tool result.

WHAT AN EMAIL NEEDS (all committed, then propose_email)
1. recipients from the known people, matched loosely; an address the user types that isn't known is fine;
2. a campaign: an existing one (show_campaigns) or a new one (needs a name);
3. a daily send window: two clock times (e.g. 9:00 am to 5:00 pm), never calendar dates. An existing
   campaign brings its own saved window. For a NEW campaign the hours must come from the user's own words:
   never invent, guess or default them. If they have not said the hours, ask for them, and pass their words
   as window_evidence when you commit.

PEOPLE ALREADY IN A CAMPAIGN (one person = one active campaign)
- Current state and every update_selection result carry people_status: a tier per person and a what_to_do
  line. Follow it. Only say what people_status says.
- free: add normally, say nothing about it.
- move_not_contacted / move_contacted: the person is active in another campaign (active_in names it). Say
  plainly where they are and that adding them here means stopping them there (their history stays). Ask ONE
  clear yes/no. Only after the user's NEXT message says yes, call approve_move. Never in the same turn you
  first ask, never on a vague answer. If they say no, offer to drop that person or choose differently.
- refuse_conversation: they replied, started the assessment or booked. Do not move or add them; say so and
  suggest handling them personally. Offer to continue without them.
- refuse_opted_out: they unsubscribed or bounced. Never add them. Say so plainly; offer to continue without.
- If a tool returns needs_move_approval or a refusal, explain it in plain words, then wait for the user.

SIMILAR CAMPAIGN NAMES
- If update_selection returns similar_campaign_name, do not guess. Ask: "Do you mean the existing <similar
  name>, or a new campaign called <typed name>?" Then commit the existing one by campaign_id, or call
  update_selection again with confirmed_new_campaign=true. Do this even if the user already said "new".

EMAIL AND SENDING
- Use the template from the records (email_to_show for a known person, otherwise email_without_company)
  exactly, except change "Hi there," to "Hi {first name},". Apply edits the user asks for, then propose_email
  again. After propose_email succeeds the UI shows the email card; your message is one short question about
  it (approve as is, or change something). Do not paste the subject or body.
- Call send_approved only when a draft is pending AND the user's latest message clearly approves it, in any
  wording. Changes requested or ambiguous means do not send; ask. After it returns, confirm in one natural
  sentence using only what the tool returned.
- Never invent people, addresses, campaigns or drafts. The records below are the only truth.
"""


def _records_text(records: dict) -> str:
    """The records block for the prompt. Capped so a large prospect list cannot blow up every turn."""
    people = records.get("people") or []
    shown = dict(records)
    if len(people) > MAX_PEOPLE_IN_PROMPT:
        shown["people"] = people[:MAX_PEOPLE_IN_PROMPT]
        shown["people_note"] = (
            f"{len(people) - MAX_PEOPLE_IN_PROMPT} more people exist but are not listed. If the user names "
            "someone who is not listed, ask for their email address."
        )
    return json.dumps(shown, ensure_ascii=False)



def build_prompt(*, ctx, settings, scope, packs, grounded_text: str, session, records: dict) -> str:
    """The full system prompt for one turn. ``session`` is None when campaigns are not available."""
    text = agent_instructions.render(
        ctx=ctx,
        settings=settings,
        mode=scope.mode,
        domain="any — you choose the tools",
        packs=packs,
        grounding=grounded_text,
    )
    text += "\n" + APPROACH
    if session is not None:
        text += "\n" + CAMPAIGN_SECTION
        text += "\n## Campaign records (the only truth for people and campaigns)\n" + _records_text(records)
        text += "\nSending accounts: " + ", ".join(sending_account_emails()) + "\n"
        text += "\n" + campaign_state_text(session.state, session.status_view())
    return text


####################################################################################################
# PART 5 - THE AGENT (runs one chat turn)
####################################################################################################

# ------------------------------------------------------------------------------------------------
# agent  (app/assistant/agent.py) - One Agno agent for the whole workspace: company tasks and outbound campaigns.
# ------------------------------------------------------------------------------------------------
import logging
import os
import re

from agno.agent import Agent
from agno.models.message import Message
from agno.run.base import RunStatus
from fastapi import HTTPException
from pydantic import BaseModel, Field

from bizos import chat_history
from bizos import settings as app_settings
from bizos.agents import grounding
from bizos.agents.claims import enforce_claims
from bizos.agents.persona import AUTO, enforce_persona
from bizos.agents.surface import build_unified_tools
from bizos.domains.packs import DomainPack, enabled_packs, get_pack
from bizos.tenancy.agnodb import workspace_db
from bizos.types import ActionStatus
from bizos.util.ids import new_id

log = logging.getLogger(__name__)

#: How many times the model is asked to rewrite a reply that failed a check.
MAX_NUDGES = 2
#: Upper bound on tool calls inside one model run.
TOOL_CALL_LIMIT = 16
#: People listed in the prompt. The rest are still found by exact address.
#: Tool names that are campaign state or display only; they never count as "doing" something.
_CAMPAIGN_READ_ONLY = {"show_contacts", "show_campaigns"}
_CAMPAIGN_NAMES = {spec["function"]["name"] for spec in CAMPAIGN_TOOLS}

_SECTION_MARKER = re.compile(r"\[\[/?area(:\s*[a-z_]+\s*)?\]\]\n?", re.I)


class AssistantTurn(BaseModel):
    """What the model returns at the end of every turn."""

    message: str = Field(description="What the user sees. Natural, specific, no raw JSON.")
    explanation: str = Field(
        description=(
            "Developer trace, never shown to the user. 5-9 specific, honest sentences: what you understood, "
            "what is decided vs missing, which tools you called or deliberately did not and why, what you "
            "are showing and why, and what the user should do next."
        )
    )


# --------------------------------------------------------------------------- prompt

# --------------------------------------------------------------------------- model

_effort = {"tried": []}  # effort settings already rejected by the provider, so we stop retrying them


def _effort_candidates() -> list[Optional[str]]:
    """Reasoning-effort settings to try, in order. ``None`` means do not send the parameter."""
    configured = (os.environ.get("MODEL_REASONING_EFFORT") or "").strip().lower() or None
    chain: list[Optional[str]] = [configured]
    # Some chat models accept function tools only with effort "none"; others reject the parameter.
    for fallback in ("none", None):
        if fallback not in chain:
            chain.append(fallback)
    return [e for e in chain if e not in _effort["tried"]] or [None]


def _model(effort: Optional[str]):
    mid = app_settings.model_id()
    provider, _, name = mid.partition(":")
    if provider == "openai" and name:
        from agno.models.openai import OpenAIChat

        kwargs: dict[str, Any] = {"id": name}
        if effort:
            kwargs["reasoning_effort"] = effort
        return OpenAIChat(**kwargs)
    return mid  # other providers: let Agno resolve "provider:model"


# --------------------------------------------------------------------------- turn bookkeeping

class _Turn:
    """Everything one chat turn accumulates across model runs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []  # ordered tool log (ops and campaign)
        self.tool_runs: list[Any] = []  # Agno ToolExecution records
        self.self_check: list[str] = []
        self.guardrail_events: list[str] = []
        self.nudges = 0

    def activity(self, session: Optional[CampaignSession]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for call in self.tool_runs:
            name = getattr(call, "tool_name", None)
            if not name:
                continue
            ok = not getattr(call, "tool_call_error", False)
            row: dict[str, Any] = {"tool": name, "ok": ok}
            if name in _CAMPAIGN_NAMES:
                # Campaign tools have no registry spec and report failure in their result text.
                result = str(getattr(call, "result", "") or "")
                if result.startswith("error") or '"error"' in result[:40]:
                    ok = row["ok"] = False
                # ...so say here whether they changed anything.
                row["wrote"] = bool(ok and name not in _CAMPAIGN_READ_ONLY)
            out.append(row)
        return out


def _final(output: Any) -> tuple[str, str, str]:
    """(message, explanation, raw) from an Agno run."""
    content = getattr(output, "content", None)
    if isinstance(content, AssistantTurn):
        raw = content.model_dump_json()
        return content.message.strip(), content.explanation.strip(), raw
    if isinstance(content, BaseModel):
        data = content.model_dump()
    elif isinstance(content, dict):
        data = content
    else:
        raw = str(content or "")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw.strip(), "", raw
    raw = json.dumps(data, default=str)
    return str(data.get("message") or "").strip(), str(data.get("explanation") or "").strip(), raw


def _clean(text: str) -> str:
    """Storage syntax from memory is never something to show."""
    return _SECTION_MARKER.sub("", text or "").strip()


def _platform_checks(
    content: str,
    *,
    grounded: Any,
    settings: Any,
    message: str,
    activity: list[dict[str, Any]],
    new_actions: list[dict[str, Any]],
    ran_match: bool,
) -> tuple[str, list[str]]:
    """The platform's deterministic checks. Returns (compliant wording, names of checks that fired)."""
    fired: list[str] = []
    name = agent_instructions.assistant_name(settings)

    content, changed = enforce_persona(content, name)
    if changed:
        fired.append("PERSONA_ENFORCED")
    content, changed = grounding.enforce_restrictions(
        content, grounded.hidden, answered_visible=bool(grounded.visible_keys)
    )
    if changed:
        fired.append("RESTRICTION_NOTED")
    content, changed = grounding.enforce_conflicts(content, grounded.conflicts)
    if changed:
        fired.append("CONFLICT_ENFORCED")
    content, changed = grounding.enforce_package(
        content,
        grounded.unpurchased,
        company=getattr(settings, "company_name", "") or "",
        asked_for_action=grounded.unpurchased_action,
        answered_visible=bool(grounded.visible_keys),
    )
    if changed:
        fired.append("PACKAGE_SCOPE_NOTED")
    content, changed = enforce_claims(content, message=message, activity=activity, new_actions=new_actions)
    if changed:
        fired.append("FALSE_ACTION_CLAIM_CORRECTED")
    if ran_match and not re.search(r"\bhuman\b", content, re.I):
        content = content.rstrip() + (
            "\n\nThis comparison is assistive only — the hiring decision stays with a human."
        )
        fired.append("HIRING_DECISION_NOTE")
    return content, fired


# --------------------------------------------------------------------------- the turn

def _pending_state(saved: dict) -> dict:
    for m in reversed(saved.get("messages") or []):
        if m["role"] == "assistant":
            state = (m.get("meta") or {}).get("state")
            return state if isinstance(state, dict) else {}
    return {}


def _history(saved: dict) -> list[Message]:
    return [
        Message(role=m["role"], content=m["content"])
        for m in saved.get("messages") or []
        if m["role"] in ("user", "assistant") and m.get("content")
    ]


def _packs_for(settings: Any, pinned: str) -> list[DomainPack]:
    general = get_pack("general")
    if pinned and pinned != AUTO:
        picked = [p for p in (get_pack(pinned), general) if p is not None]
        if picked:
            return list(dict.fromkeys(picked))
    packs = enabled_packs(settings.enabled_domains)
    if general is not None and general not in packs:
        packs.append(general)
    return packs


def _response_domain(turn: _Turn, tool_domains: dict[str, str]) -> str:
    """The domain the turn ended up working in, from the tools the model actually used."""
    for call in turn.tool_runs:
        name = getattr(call, "tool_name", "")
        if name in _CAMPAIGN_NAMES:
            return "acquisition"
        domain = tool_domains.get(name)
        if domain and domain != "general":
            return domain
    return "general"


def _guardrail_audit(ctx: Any, scope: Any, session_id: str, status: str, domain: str = "") -> None:
    try:
        from bizos.audit.events import log
        from bizos.types import AuditEventType

        fields: dict[str, Any] = {"session_id": session_id, "status": status, "execution_mode": str(scope.mode)}
        if domain:
            fields["domain"] = domain
        log(AuditEventType.GUARDRAIL_TRIGGERED, ctx=ctx, **fields)
    except Exception:  # noqa: BLE001 - auditing must not break the reply
        pass


def reply(body: Any, ctx: Any, settings: Any) -> dict[str, Any]:
    """One chat turn. Must be called with the tenant bound (``bound(ctx)``)."""
    from app.api.chat import _regulated_data_refusal as regulated_data_refusal
    from app.api.deps import run_scope

    session_id = body.session_id or new_id("ses")
    saved: dict = {"messages": []}
    if body.session_id:
        try:
            chat_history.assert_can_continue(ctx, body.session_id)
            saved = chat_history.get_session(ctx, body.session_id)
        except chat_history.SessionNotFound:
            # assert_can_continue only refuses someone else's conversation; an unknown id starts fresh.
            if chat_history.owner_of(ctx, body.session_id) is not None:
                raise HTTPException(status_code=404, detail="Conversation not found") from None
    prior_state = _pending_state(saved)

    if not app_settings.llm_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Chat requires a model. Set OPENAI_API_KEY (and MODEL_ID) to enable it. "
                "Memory, approvals, audit and the connectors work without one."
            ),
        )

    scope = run_scope(
        ctx,
        settings,
        domain="general",
        requested_mode=getattr(body, "execution_mode", None),
        session_id=session_id,
    )
    message = body.message
    before = {a.id for a in actions.list_actions(limit=200, ctx=ctx)}

    # ---- before the model: regulated data never reaches the vendor, and restricted candidates are not discussed
    grounded = grounding.analyze(ctx, message, settings.enabled_domains)
    refusal = regulated_data_refusal(message, settings)
    canned = (refusal[1] if refusal else None) or grounding.restricted_candidate_reply(grounded, message)
    if canned is not None:
        if refusal:
            _guardrail_audit(ctx, scope, session_id, refusal[0])
        return _finish(
            ctx,
            scope,
            settings,
            body,
            session_id,
            text=canned,
            explanation="Answered by the platform before any model call (regulated or restricted data).",
            turn=_Turn(),
            session=None,
            tool_domains={},
            new_actions=[],
            final_state=prior_state,
        )

    packs = _packs_for(settings, getattr(body, "domain", AUTO))
    turn = _Turn()

    # The hiring comparison is computed by the platform when the message names a resume and an opening.
    ran_match = False
    keys = grounding.candidate_match_keys(grounded, message)
    grounded_text = grounded.text
    if keys:
        from bizos.tools.base import guarded_call

        outcome = guarded_call(scope, "hr_match_candidate", {"resume_key": keys[0], "opening_key": keys[1]})
        if outcome.ok:
            ran_match = True
            grounded_text += (
                "\n\n## Structured candidate comparison (computed by the platform)\n"
                f"{outcome.message}\nResult: {outcome.data}\n"
                "Present this comparison starting with the verdict in the computed words "
                "('Likely fit' or 'Weak fit'), then must-haves met and what is missing. "
                "State that it is assistive only and the hiring decision stays with a human."
            )

    # ---- tools: every enabled domain, plus the campaign flow for admins
    tools, tool_domains = build_unified_tools(scope, packs, on_call=turn.calls.append)

    session: Optional[CampaignSession] = None
    records: dict = {}
    campaign_allowed = ctx.is_admin and getattr(body, "domain", AUTO) in (AUTO, "acquisition", None)
    if campaign_allowed:
        try:
            records = known_records(ctx)
        except Exception as exc:  # noqa: BLE001 - acquisition database unreachable: no campaign tools this turn
            log.warning("campaign records unavailable: %s: %s", type(exc).__name__, exc)
            records = {}
        if records:
            from bizos.types import ExecutionMode

            block = ""
            if scope.mode == ExecutionMode.ADVISE:
                block = (
                    "this workspace is in Advise mode, so emails can be drafted here but not sent. "
                    "An admin can change the mode."
                )
            session = CampaignSession(ctx, records, prior_state, send_block=block)
            session.log = turn.calls  # one ordered log for ops and campaign calls
            session.user_message = message
            session.user_texts = [m["content"] for m in saved.get("messages") or [] if m["role"] == "user"] + [message]
            tools = tools + _described(build_campaign_tools(session, scope))

    def prompt_now() -> str:
        return build_prompt(
            ctx=ctx,
            settings=settings,
            scope=scope,
            packs=packs,
            grounded_text=grounded_text,
            session=session,
            records=records,
        )

    history = _history(saved)
    next_input: list[Message] = history + [Message(role="user", content=message)]
    text = explanation = ""
    raw = ""
    activity: list[dict[str, Any]] = []
    fixed_text = ""
    fired: list[str] = []

    for attempt in range(MAX_NUDGES + 1):
        output = _run_agent(ctx, settings, session_id, tools, prompt_now(), next_input, turn)
        text, explanation, raw = _final(output)
        text = _clean(text)
        activity = turn.activity(session)
        new_actions = [
            a.to_dict() for a in actions.list_actions(limit=200, ctx=ctx) if a.id not in before
        ]
        fixed_text, fired = _platform_checks(
            text,
            grounded=grounded,
            settings=settings,
            message=message,
            activity=activity,
            new_actions=new_actions,
            ran_match=ran_match,
        )
        issues: list[str] = []
        if fired:
            issues.append(
                "Platform checks flagged your draft ("
                + ", ".join(fired)
                + "). A compliant version would read:\n"
                + fixed_text
                + "\nRewrite your reply in your own words so these checks pass."
            )
        if session is not None:
            issues.extend(session.problems(text, explanation))
        if issues and attempt < MAX_NUDGES:
            turn.nudges += 1
            turn.self_check.extend(issues)
            # Carry the whole run forward (tool calls and results included), so nothing is repeated.
            carried = [m for m in (output.messages or []) if m.role not in ("system", "developer")]
            next_input = carried + [
                Message(
                    role="system",
                    content="Self-check before replying:\n- " + "\n- ".join(issues)
                    + "\nFix this with tools if needed, then give your final answer in the same JSON format. "
                    "If a point truly does not apply, keep your answer and say why in the explanation.",
                )
            ]
            continue
        break

    # The deterministic wording is the last line of defence, not the first.
    if fired:
        text = fixed_text
        turn.guardrail_events.extend(fired)
        for status in fired:
            _guardrail_audit(ctx, scope, session_id, status, domain=",".join(sorted(grounded.unpurchased)) if status == "PACKAGE_SCOPE_NOTED" else "")
    if session is not None and session.claims_unsent(text, explanation):
        # Last line of defence: never tell the user something was sent when it was not.
        text = NOT_SENT_TEXT
        turn.guardrail_events.append("FALSE_SEND_CLAIM_CORRECTED")
    if not text:
        raise HTTPException(status_code=502, detail="The assistant returned an empty message")

    new_actions = [a.to_dict() for a in actions.list_actions(limit=200, ctx=ctx) if a.id not in before]
    final_state = prior_state
    if session is not None:
        final_state = {} if session.sent else {**session.state, "last_explanation": explanation}
    return _finish(
        ctx,
        scope,
        settings,
        body,
        session_id,
        text=text,
        explanation=explanation,
        turn=turn,
        session=session,
        tool_domains=tool_domains,
        new_actions=new_actions,
        final_state=final_state,
    )


def _described(functions: list[Any]) -> list[Any]:
    """Mark campaign tools so the model can tell them from the email_* tools."""
    for fn in functions:
        fn.description = "[Outbound campaign flow] " + (fn.description or "")
    return functions


def _run_agent(
    ctx: Any, settings: Any, session_id: str, tools: list[Any], prompt: str, messages: list[Message], turn: _Turn
) -> Any:
    """Run the Agno agent once. Retries with other reasoning-effort settings only if nothing ran yet."""
    last_error = ""
    for effort in _effort_candidates():
        agent = Agent(
            name=agent_instructions.assistant_name(settings),
            id="bizos-assistant",
            model=_model(effort),
            user_id=ctx.user_id,
            session_id=session_id,
            db=workspace_db(ctx),
            instructions=prompt,
            tools=tools,
            output_schema=AssistantTurn,
            tool_call_limit=TOOL_CALL_LIMIT,
            # Conversation context comes from our own per-user chat history (passed in `messages`),
            # which is also what the user sees; Agno still records the run in the workspace database.
            add_history_to_context=False,
            # Tools are built per request and must not be cached across tenants.
            cache_callables=False,
            enable_agentic_memory=False,
            add_memories_to_context=True,
            telemetry=False,
            store_events=False,
        )
        try:
            output = agent.run(input=messages)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Model call failed: {exc}") from exc
        turn.tool_runs.extend(output.tools or [])
        if output.status != RunStatus.error:
            return output
        last_error = str(output.content or "model error")
        log.warning("model run failed (reasoning_effort=%s): %s", effort, last_error)
        if output.tools:  # something already ran; never replay it
            break
        _effort["tried"].append(effort)
    raise HTTPException(status_code=502, detail=f"Model call failed: {last_error}")


# --------------------------------------------------------------------------- response

def _finish(
    ctx: Any,
    scope: Any,
    settings: Any,
    body: Any,
    session_id: str,
    *,
    text: str,
    explanation: str,
    turn: _Turn,
    session: Optional[CampaignSession],
    tool_domains: dict[str, str],
    new_actions: list[dict[str, Any]],
    final_state: dict,
) -> dict[str, Any]:
    activity = [{"tool": a["tool"], "ok": a["ok"]} for a in turn.activity(session)]
    s = session.state if session is not None else {}
    d = s.get("draft") or {}
    recipients = [r["email"] for r in s.get("recipients") or []]
    domain = _response_domain(turn, tool_domains)
    awaiting = [a for a in new_actions if a["status"] == str(ActionStatus.PENDING_APPROVAL)]
    drafts = [a for a in new_actions if a["status"] == str(ActionStatus.DRAFT)]

    trace = {
        "engine": "agno",
        "tool": "execute" if session is not None and session.sent else "",
        "explanation": explanation,
        "tool_log": turn.calls,
        "self_check": turn.self_check,
        "guardrails": turn.guardrail_events,
        "campaign_id": s.get("campaign_id") or "",
        "campaign_name": s.get("campaign_name") or "",
        "campaign_schedule_timing": s.get("window") or [],
        "email_address": recipients[0] if len(recipients) == 1 else "",
        "email_address_list": recipients,
        "email_from": d.get("email_from") or "",
        "email_subject": d.get("email_subject") or "",
        "email_body": d.get("email_body") or "",
        "email_draft": d.get("email_draft") or "",
        "reply_note": text,
        "campaigns_to_show": session.campaigns_to_show if session is not None else [],
        "people_to_show": session.people_to_show if session is not None else [],
    }
    if not ctx.is_admin:
        # Ordinary users get the answer, not the machinery (tool arguments, reasoning, checks).
        trace = {"engine": "agno"}
    response = {
        "session_id": session_id,
        "content": text,
        "response": text,  # same text under the key the campaign UI reads
        "mode": str(scope.mode),
        "domain": domain,
        "routed": getattr(body, "domain", AUTO) == AUTO,
        "tool_activity": activity,
        "actions": new_actions,
        "awaiting_approval": awaiting,
        "drafts": drafts,
        "email_draft": trace.get("email_draft", ""),
        "trace": trace,
    }
    _remember(ctx, body.message, response, final_state)
    return response


def _action_summary(action: dict[str, Any]) -> dict[str, Any]:
    return {k: action.get(k) for k in ("id", "title", "tool", "status", "risk_level", "policy_reason")}


def _remember(ctx: Any, message: str, response: dict[str, Any], state: dict) -> None:
    """Save the turn as shown. A storage failure must not lose the reply."""
    try:
        chat_history.record_turn(
            ctx,
            session_id=response["session_id"],
            user_message=message,
            reply=response["content"],
            domain=response["domain"],
            meta={
                "engine": "agno",
                "mode": response["mode"],
                "tool_activity": response["tool_activity"],
                "awaiting_approval": [_action_summary(a) for a in response["awaiting_approval"]],
                "drafts": [_action_summary(a) for a in response["drafts"]],
                "state": state,
                "json": response["trace"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error("chat history write failed: %s: %s", type(exc).__name__, exc)