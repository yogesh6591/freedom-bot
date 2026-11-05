"""Prospect records: show one, add, change and delete.

Rules (the same for every tool here):
* reading is free
* adding is immediate (nothing is lost if it was a mistake)
* changing one person is immediate; changing several, or deleting anyone, asks the user for one yes first
* "everyone" is never accepted: groups are named, or described with a filter, and are capped
"""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.models.prospect import Prospect
from app.outreach import chicago, datasets, fields, lookup, records
from app.outreach.confirm import action_key
from app.outreach.fields import FieldError
from app.outreach.text import proper_case
from app.outreach.toolkit import Call, ToolError, tool

MAX_ADD = 100
MAX_DELETE = 25
MAX_GROUP_UPDATE = 200
#: changes that identify a person; they are only allowed one person at a time
IDENTITY = frozenset({"executive_name", "first_name", "last_name", "email"})
_EVERYONE = re.compile(r"^\s*(\*|all|everyone|everybody|all (the )?(prospects|people|contacts))\s*$", re.I)
BULK_REFUSAL = (
    "I can't delete everyone at once. Tell me which people to delete (up to 25 names) and I'll ask you to confirm."
)


def _clean(changes: Any) -> dict[str, Optional[str]]:
    """Validate a ``{field: value}`` dict from the model; empty values clear optional fields."""
    if not isinstance(changes, dict) or not changes:
        raise ToolError("changes is empty. Say which fields to change and the new values.")
    out: dict[str, Optional[str]] = {}
    for key, value in changes.items():
        try:
            out[key] = fields.clean_value(key, value)
        except FieldError as exc:
            raise ToolError(str(exc)) from exc
    return out


def _email_taken(db: Session, email: str, except_id: Any) -> bool:
    row = lookup.by_email(db, email)
    return row is not None and row.id != except_id


def _snapshot(p: Prospect) -> dict[str, Optional[str]]:
    return {k: getattr(p, fields.BY_KEY[k].column) for k in fields.COLUMN_KEYS}


def _show(label: str, value: Optional[str]) -> str:
    return f"{label}: {value if value else '-'}"


# ------------------------------------------------------------------------------------------------ show
@tool("show_prospect", "who")
def show_prospect(call: Call, who: str = "") -> str:
    """Everything stored about one person, with their email, assessment, booking and payment status."""
    p = lookup.person(call, who)
    db = call.db
    link = records.link_row(db, p.id)
    mail = records.email_state(db, p.id)
    lines = [
        f"{p.executive_name} <{p.email}>",
        " | ".join([_show("Company", p.company), _show("Title", p.executive_title)]),
        " | ".join([_show("Website", p.website), _show("LinkedIn", p.linkedin_url)]),
        " | ".join([_show("Revenue", p.revenue_band), _show("Headcount", p.headcount_band), _show("Source", p.source)]),
        _show("Fit note", p.fit_note),
        f"Added {chicago.fmt_time(p.created_at)} (Chicago time).",
        f"Emails: {mail['sent']} sent, {mail['queued']} waiting" + (", opted out" if mail["opted_out"] else "")
        + (f", last {chicago.fmt_time(mail['last_at'])}" if mail["last_at"] else ""),
    ]
    if p.research_status:
        lines.append(f"Research: {p.research_status}" + (f", priority {p.priority_score:.0f}" if p.priority_score is not None else ""))
    if p.research_notes:
        lines.append("Research notes: " + p.research_notes[:600].replace("\n", " "))
    if link is None or not link.assessment_token:
        lines.append("Assessment: no link yet (never emailed)")
        return "\n".join(lines)
    lines.append(f"Assessment: {link.assessment_status.value}, journey stage {link.journey_stage.value.replace('_', ' ')}")
    booking = (link.booking_status or "not_booked").replace("_", " ")
    when = f", {chicago.fmt_time(link.meeting_start)} Chicago time" if link.meeting_start else ""
    lines.append(f"Booking: {booking}{when}")
    if link.checkout_offer_amount is not None or link.purchase_outcome.value != "not_recorded":
        offered = f"offered {datasets.fmt_money(link.checkout_offer_amount)}" if link.checkout_offer_amount is not None else "no offer"
        paid = (f", paid {datasets.fmt_money(link.revenue_amount)} on {chicago.fmt_time(link.purchased_at)}"
                if link.purchase_outcome.value == "purchased" else "")
        lines.append(f"Payment: {offered}{paid}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ add
def _new_prospect(item: dict, number: int, taken: set[str], db: Session) -> Prospect:
    """A new ``Prospect`` for one pasted/typed person, or a ``FieldError`` saying why the row is skipped."""
    clean = {key: fields.clean_value(key, item[key]) for key in fields.KEYS if item.get(key) not in (None, "")}
    first, last = clean.pop("first_name", None), clean.pop("last_name", None)
    name = clean.pop("executive_name", None) or " ".join(x for x in (first, last) if x)
    email = clean.pop("email", None)
    if not name and email:
        name = " ".join(email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").split())
    if not name:
        raise FieldError(f"row {number}: no name")
    if not email:
        raise FieldError(f"row {number} ({name}): no email")
    if email in taken or lookup.by_email(db, email) is not None:
        raise FieldError(f"{name} <{email}> is already saved")
    taken.add(email)
    prospect = Prospect(company="", executive_name=proper_case(name), email=email, discovery_source="assistant")
    fields.apply_changes(prospect, clean)
    return prospect


@tool("add_prospects", "people")
def add_prospects(call: Call, people: Optional[list] = None) -> str:
    """Save one or many new people. Anyone whose email is already saved is skipped and reported."""
    items = [i for i in people or [] if isinstance(i, dict)]
    if not items:
        raise ToolError("no people given. Each person needs a name and an email.")
    if len(items) > MAX_ADD:
        raise ToolError(f"{len(items)} people is too many for one step (max {MAX_ADD}). Ask the user to split the list.")
    added: list[Prospect] = []
    skipped: list[str] = []
    taken: set[str] = set()
    for number, item in enumerate(items, 1):
        try:
            prospect = _new_prospect(item, number, taken, call.db)
        except FieldError as exc:
            skipped.append(str(exc))
            continue
        call.db.add(prospect)
        added.append(prospect)
    call.db.commit()
    parts = []
    if added:
        more = f"; and {len(added) - 10} more" if len(added) > 10 else ""
        parts.append(f"Added {len(added)}: " + "; ".join(lookup.label(p) for p in added[:10]) + more + ".")
    if skipped:
        more = f" | and {len(skipped) - 10} more" if len(skipped) > 10 else ""
        parts.append(f"Not added ({len(skipped)}): " + " | ".join(skipped[:10]) + more + ".")
    return " ".join(parts)


# ------------------------------------------------------------------------------------------------ update
def _targets(call: Call, people: Optional[list], where: Optional[list]) -> list[Prospect]:
    if bool(people) == bool(where):
        raise ToolError("give either people (names or emails) or where (a filter), not both and not neither. "
                        "Changing everyone at once is not supported.")
    if people:
        if any(_EVERYONE.match(str(x)) for x in people):
            raise ToolError("changing everyone at once is not supported. Name the people or use a filter.")
        return lookup.many(call.db, people, MAX_GROUP_UPDATE)
    try:
        ids, total = datasets.matching_ids(call.db, where, MAX_GROUP_UPDATE)
    except datasets.QueryError as exc:
        raise ToolError(str(exc)) from exc
    if total == 0:
        raise ToolError("nobody matches that filter. Nothing changed.")
    if total > MAX_GROUP_UPDATE:
        raise ToolError(f"{total} people match, which is more than the {MAX_GROUP_UPDATE} allowed in one step. "
                        "Ask the user to narrow the filter.")
    return list(call.db.scalars(select(Prospect).where(Prospect.id.in_(ids)).order_by(Prospect.executive_name)))


def _describe(changes: dict[str, Optional[str]]) -> str:
    return ", ".join(f"{fields.BY_KEY[k].label} to {v if v else 'empty'}" for k, v in changes.items())


def _names(people: list[Prospect], limit: int = 5) -> str:
    shown = ", ".join(p.executive_name for p in people[:limit])
    return shown + (f" and {len(people) - limit} more" if len(people) > limit else "")


@tool("update_prospects", "changes", "people", "where", "approval_evidence")
def update_prospects(call: Call, changes: Optional[dict] = None, people: Optional[list] = None,
                     where: Optional[list] = None, approval_evidence: str = "") -> str:
    """Change fields on one person (immediately) or on a named or filtered group (after one yes)."""
    cleaned = _clean(changes)
    targets = _targets(call, people, where)
    if len(targets) > 1 and IDENTITY & set(cleaned):
        raise ToolError("a name or email can only be changed for one person at a time.")
    if "email" in cleaned and _email_taken(call.db, cleaned["email"], targets[0].id):
        raise ToolError(f"{cleaned['email']} is already saved for someone else.")
    if len(targets) > 1:
        key = action_key("update", {"ids": sorted(str(p.id) for p in targets), "changes": cleaned})
        question = f"Change {_describe(cleaned)} for {len(targets)} people ({_names(targets)})?"
        stop = call.confirmed(key, approval_evidence, question)
        if stop:
            return stop
    report = []
    for p in targets:
        before = _snapshot(p)
        changed = fields.apply_changes(p, cleaned)
        after = _snapshot(p)
        if len(targets) == 1 and changed:
            report.append(f"{lookup.label(p)}: " + "; ".join(
                f"{fields.BY_KEY[k].label} {before[k] or '-'} -> {after[k] or '-'}" for k in changed))
    call.db.commit()
    if len(targets) == 1:
        return report[0] if report else f"{targets[0].executive_name} already had these values. Nothing changed."
    return f"Updated {len(targets)} people ({_names(targets)}): {_describe(cleaned)}."


# ------------------------------------------------------------------------------------------------ delete
@tool("delete_prospects", "people", "approval_evidence")
def delete_prospects(call: Call, people: Optional[list] = None, approval_evidence: str = "") -> str:
    """Delete the named people (max 25), with their email log and assessment, after one yes."""
    if not people:
        raise ToolError("no names given. " + BULK_REFUSAL)
    if any(_EVERYONE.match(str(x)) for x in people):
        return f"Nothing changed. Reply to the user with exactly: {BULK_REFUSAL}"
    targets = lookup.many(call.db, people, MAX_DELETE)
    sent = {p.id: records.email_state(call.db, p.id)["sent"] for p in targets}
    key = action_key("delete", sorted(str(p.id) for p in targets))
    listing = "; ".join(f"{p.executive_name} <{p.email}>" + (f" ({sent[p.id]} email(s) sent)" if sent[p.id] else "")
                        for p in targets)
    question = (f"Delete {len(targets)} {'person' if len(targets) == 1 else 'people'}: {listing}? "
                "This also removes their sent-email log and assessment. Emails already sent cannot be unsent.")
    stop = call.confirmed(key, approval_evidence, question)
    if stop:
        return stop
    gone = {p.email.lower() for p in targets}
    for p in targets:
        call.db.delete(p)  # their emails, links and assessments go with them (database cascade)
    call.db.commit()
    call.state["recipients"] = [r for r in call.state.get("recipients", []) if str(r.get("email", "")).lower() not in gone]
    return f"Deleted {len(targets)}: " + "; ".join(f"{p.executive_name} <{p.email}>" for p in targets) + "."


@tool("refuse_bulk_change")
def refuse_bulk_change(call: Call) -> str:
    return f"Nothing changed. Reply to the user with exactly: {BULK_REFUSAL}"
