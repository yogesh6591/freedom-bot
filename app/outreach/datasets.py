"""Read-only questions about acquisition data: the structured query behind ``query_records``.

The model never writes SQL. It picks a dataset, filters, a sort, optional columns and an optional group-by, and
this module turns that into one bounded SELECT. Everything is checked against the catalog below, so a wrong
field or value is refused with a message that says what is allowed.

Datasets
    people    one row per prospect, with their email, assessment, booking and payment status
    emails    one row per email sent or queued
    bookings  people who have booked (or cancelled), newest meeting first
    payments  people who were offered checkout or paid, newest first

Dates are read and shown in Chicago time. A bare date ("2026-10-29") means that whole day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import String, and_, case, cast, func, not_, or_, select
from sqlalchemy.orm import Session

from acquisition.models.assessment import AssessmentConversation, AssessmentProfile
from acquisition.models.campaign import Campaign, CampaignProspect
from acquisition.models.outreach import SentEmail
from acquisition.models.prospect import Prospect
from app.outreach import chicago, matching
from app.outreach.fields import FIELDS

TZ = chicago.TZ
TZ_LABEL = chicago.TZ_LABEL
DIRECT_CAMPAIGN = "Direct outreach"
MAX_ROWS = 50
MAX_COLUMNS = 8
MAX_FUZZY_IDS = 500


class QueryError(ValueError):
    """The question cannot be run as asked. The message tells the model how to fix the call."""


# --------------------------------------------------------------------------------------------- columns
@dataclass(frozen=True)
class Column:
    key: str
    expr: Any  # SQLAlchemy expression
    kind: str  # text | number | money | date | bool | enum
    label: str
    values: tuple[str, ...] = ()  # allowed values for enum columns
    needs: frozenset[str] = frozenset()  # joins required: link, profile, conversation, mail
    fuzzy_on: Any = None  # a Prospect column: 'contains' on this column also finds close spellings of it


def _enum_text(col: Any) -> Any:
    return cast(col, String)


def _best_link_subquery() -> Any:
    """One campaign link per prospect: the 'Direct outreach' one first, else the newest."""
    rank = func.row_number().over(
        partition_by=CampaignProspect.prospect_id,
        order_by=[(Campaign.name == DIRECT_CAMPAIGN).desc(), CampaignProspect.created_at.desc()],
    )
    ranked = (
        select(CampaignProspect.id.label("link_id"), CampaignProspect.prospect_id.label("pid"), rank.label("rn"))
        .join(Campaign, Campaign.id == CampaignProspect.campaign_id)
        .subquery("ranked_links")
    )
    return select(ranked.c.link_id, ranked.c.pid).where(ranked.c.rn == 1).subquery("best_link")


def _mail_subquery() -> Any:
    return (
        select(
            SentEmail.prospect_id.label("pid"),
            func.count().filter(SentEmail.status == "sent").label("sent"),
            func.count().filter(SentEmail.status.in_(("queued", "sending"))).label("queued"),
            func.max(SentEmail.sent_at).label("last_sent"),
        )
        .group_by(SentEmail.prospect_id)
        .subquery("mail_stats")
    )


BEST_LINK = _best_link_subquery()
MAIL = _mail_subquery()
LINK = CampaignProspect
PROFILE = AssessmentProfile
CONVERSATION = AssessmentConversation

_OUTREACH = ("pending", "active", "replied", "assessment_started", "completed", "bounced", "unsubscribed", "cold", "stopped")
_JOURNEY = (
    "outreach", "assessment_started", "assessment_completed", "qualified", "unqualified", "unqualified_exit",
    "pending_review", "proof", "demo", "value", "calendar_unlocked", "booked",
)


def _text_or(col: Any, default: str) -> Any:
    """A status column as text; people with no assessment link yet read as ``default``."""
    return func.coalesce(cast(col, String), default)


_STATUS = (
    "not_emailed", "email_queued", "emailed", "opened_page", "started_assessment", "finished_assessment",
    "booked", "booking_cancelled", "unsubscribed", "bounced",
)


def _overall_status() -> Any:
    """One word for where a person is, in the same order the list view uses."""
    sent, queued = func.coalesce(MAIL.c.sent, 0), func.coalesce(MAIL.c.queued, 0)
    opted_out = cast(LINK.outreach_status, String).in_(("unsubscribed", "bounced"))
    return case(
        (opted_out, cast(LINK.outreach_status, String)),
        (LINK.booking_status == "confirmed", "booked"),
        (LINK.booking_status == "cancelled", "booking_cancelled"),
        (cast(LINK.assessment_status, String) == "completed", "finished_assessment"),
        (cast(LINK.assessment_status, String) == "started", "started_assessment"),
        (CONVERSATION.created_at.is_not(None), "opened_page"),
        (and_(queued > 0, sent == 0), "email_queued"),
        (sent > 0, "emailed"),
        else_="not_emailed",
    )


_LINK, _MAIL = frozenset({"link"}), frozenset({"mail"})


def _people_columns() -> dict[str, Column]:
    cols = [
        # identity (the editable prospect fields)
        *[
            Column(f.key, getattr(Prospect, f.column), "text", f.label,
                   fuzzy_on=getattr(Prospect, f.column) if f.fuzzy else None)
            for f in FIELDS
            if f.column
        ],
        Column("added_at", Prospect.created_at, "date", "added"),
        # where they are overall
        Column("status", _overall_status(), "enum", "status", _STATUS, frozenset({"link", "mail", "conversation"})),
        # emails
        Column("emails_sent", func.coalesce(MAIL.c.sent, 0), "number", "emails sent", needs=_MAIL),
        Column("emails_queued", func.coalesce(MAIL.c.queued, 0), "number", "emails waiting", needs=_MAIL),
        Column("last_emailed_at", MAIL.c.last_sent, "date", "last emailed", needs=_MAIL),
        Column("outreach_status", _text_or(LINK.outreach_status, "pending"), "enum", "outreach status", _OUTREACH, _LINK),
        # assessment
        Column("assessment_page_opened_at", CONVERSATION.created_at, "date", "page opened",
               needs=frozenset({"link", "conversation"})),
        Column("assessment_status", _text_or(LINK.assessment_status, "not_started"), "enum", "assessment",
               ("not_started", "started", "completed"), _LINK),
        Column("assessment_started_at", LINK.assessment_started_at, "date", "assessment started", needs=_LINK),
        Column("assessment_completed_at", LINK.assessment_completed_at, "date", "assessment finished", needs=_LINK),
        Column("qualification", _enum_text(PROFILE.qualification_status), "enum", "qualification",
               ("pending", "qualified", "unqualified"), frozenset({"link", "profile"})),
        Column("journey_stage", _text_or(LINK.journey_stage, "outreach"), "enum", "journey stage", _JOURNEY, _LINK),
        # booking
        Column("booking_status", func.coalesce(LINK.booking_status, "not_booked"), "enum", "booking",
               ("not_booked", "confirmed", "cancelled"), _LINK),
        Column("meeting_start", LINK.meeting_start, "date", "meeting", needs=_LINK),
        Column("consultation_show", _text_or(LINK.consultation_show_status, "not_recorded"), "enum", "attended",
               ("not_recorded", "showed", "no_show"), _LINK),
        # payment
        Column("checkout_offer_amount", LINK.checkout_offer_amount, "money", "offered", needs=_LINK),
        Column("checkout_offered_at", LINK.checkout_offered_at, "date", "offered on", needs=_LINK),
        Column("purchase_outcome", _text_or(LINK.purchase_outcome, "not_recorded"), "enum", "payment",
               ("not_recorded", "purchased", "did_not_purchase"), _LINK),
        # Offering checkout copies the offered amount into revenue_amount as a placeholder, so it only counts
        # as paid once the payment is recorded.
        Column("amount_paid", case((_enum_text(LINK.purchase_outcome) == "purchased", LINK.revenue_amount), else_=None),
               "money", "paid", needs=_LINK),
        Column("purchased_at", LINK.purchased_at, "date", "paid on", needs=_LINK),
        Column("handoff_status", func.coalesce(LINK.handoff_status, "not_started"), "enum", "handoff",
               ("not_started", "ready", "handed_off"), _LINK),
    ]
    return {c.key: c for c in cols}


def _email_columns() -> dict[str, Column]:
    cols = [
        Column("to_email", SentEmail.to_email, "text", "to", fuzzy_on=Prospect.email),
        Column("person", Prospect.executive_name, "text", "person", fuzzy_on=Prospect.executive_name),
        Column("company", Prospect.company, "text", "company", fuzzy_on=Prospect.company),
        Column("subject", SentEmail.subject, "text", "subject"),
        Column("status", SentEmail.status, "enum", "status", ("queued", "sending", "sent", "failed", "skipped")),
        Column("kind", SentEmail.kind, "enum", "kind", ("first", "follow_up")),
        Column("queued_at", SentEmail.queued_at, "date", "queued"),
        Column("sent_at", SentEmail.sent_at, "date", "sent"),
        Column("error", SentEmail.error, "text", "error"),
    ]
    return {c.key: c for c in cols}


# --------------------------------------------------------------------------------------------- datasets
@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    columns: dict[str, Column]
    default_columns: tuple[str, ...]
    default_sort: tuple[str, str]  # column, asc|desc
    person_id: Any  # expression that holds the prospect id (for typo-tolerant person matching)
    base_where: Callable[[], list[Any]] = lambda: []
    kind: str = "people"  # people | emails
    identity_columns: tuple[str, ...] = ("executive_name", "email")


_PEOPLE = _people_columns()
_EMAILS = _email_columns()

DATASETS: dict[str, Dataset] = {
    "people": Dataset(
        "people", "Every prospect with their email, assessment, booking and payment status.", _PEOPLE,
        ("executive_name", "email", "company", "status"),
        ("added_at", "desc"), Prospect.id,
    ),
    "emails": Dataset(
        "emails", "Every email sent or waiting to send, with its status.", _EMAILS,
        ("person", "to_email", "subject", "status", "kind", "sent_at"),
        ("queued_at", "desc"), SentEmail.prospect_id, kind="emails", identity_columns=("person", "to_email"),
    ),
    "bookings": Dataset(
        "bookings", "People who booked a call (or cancelled), newest meeting first.", _PEOPLE,
        ("executive_name", "email", "company", "booking_status", "meeting_start", "consultation_show"),
        ("meeting_start", "desc"), Prospect.id,
        base_where=lambda: [LINK.booking_status.is_not(None), LINK.booking_status != "not_booked"],
    ),
    "payments": Dataset(
        "payments", "People who were offered checkout or paid, newest first.", _PEOPLE,
        ("executive_name", "email", "checkout_offer_amount", "purchase_outcome", "amount_paid", "purchased_at"),
        ("checkout_offered_at", "desc"), Prospect.id,
        base_where=lambda: [
            or_(
                LINK.checkout_offered_at.is_not(None),
                LINK.checkout_offer_amount.is_not(None),
                _enum_text(LINK.purchase_outcome) != "not_recorded",
            )
        ],
    ),
}
#: bookings and payments need the link; make that explicit so the joins are added
_FORCE_NEEDS = {"bookings": {"link"}, "payments": {"link"}}

OPS = ("equals", "not_equals", "contains", "gt", "gte", "lt", "lte", "between", "is_empty", "not_empty")


# --------------------------------------------------------------------------------------------- values
def _parse_date(text: str) -> tuple[datetime, datetime]:
    try:
        return chicago.parse_range(text)
    except ValueError as exc:
        raise QueryError(f"{exc} Use YYYY-MM-DD, a full date and time, today, yesterday or tomorrow.") from None


def _number(text: str) -> Decimal:
    try:
        return Decimal(str(text).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        raise QueryError(f"'{text}' is not a number.")


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


# --------------------------------------------------------------------------------------------- filters
def _fuzzy_person_ids(db: Session, text: str, columns: list[Any]) -> list[Any]:
    """Ids of the people whose ``columns`` closely match ``text`` (handles typos)."""
    return [pid for pid, _ in matching.find_ids(db, text, Prospect.id, columns, limit=MAX_FUZZY_IDS)]


#: A status column's value that means "nothing has happened yet". is_empty / not_empty on these columns use it.
_DEFAULT_VALUE = {
    "booking_status": "not_booked", "assessment_status": "not_started", "outreach_status": "pending",
    "consultation_show": "not_recorded", "purchase_outcome": "not_recorded", "handoff_status": "not_started",
    "journey_stage": "outreach", "status": "not_emailed", "qualification": "pending",
}


def _condition(db: Session, ds: Dataset, f: dict) -> tuple[Any, set[str]]:
    """One filter as a SQL condition, plus the joins it needs."""
    key, op, value = str(f.get("field") or ""), str(f.get("op") or "equals"), f.get("value")
    value2 = f.get("value2")
    if op not in OPS:
        raise QueryError(f"Unknown op '{op}'. Use one of: {', '.join(OPS)}.")

    # 'search' looks across name, email and company with typo tolerance
    if key == "search":
        if not str(value or "").strip():
            raise QueryError("search needs a value")
        ids = _fuzzy_person_ids(db, str(value), [Prospect.executive_name, Prospect.email, Prospect.company])
        return ds.person_id.in_(ids or [None]), set()

    col = ds.columns.get(key)
    if col is None:
        raise QueryError(f"'{key}' is not a column of {ds.name}. Columns: {', '.join(ds.columns)}, or 'search'.")
    needs = set(col.needs)

    if op == "is_empty":
        expr = col.expr
        cond = expr.is_(None) if col.kind in ("date", "number", "money") else or_(expr.is_(None), cast(expr, String) == "")
        if col.kind == "enum" and key in _DEFAULT_VALUE:
            cond = or_(cond, cast(expr, String) == _DEFAULT_VALUE[key])
        return cond, needs
    if op == "not_empty":
        expr = col.expr
        cond = expr.is_not(None) if col.kind in ("date", "number", "money") else and_(expr.is_not(None), cast(expr, String) != "")
        if col.kind == "enum" and key in _DEFAULT_VALUE:
            cond = and_(cond, cast(expr, String) != _DEFAULT_VALUE[key])
        return cond, needs
    if value is None or str(value).strip() == "":
        raise QueryError(f"'{key}' {op} needs a value")
    text = str(value).strip()

    if col.kind == "date":
        lo, hi = _parse_date(text)
        if op == "equals":
            return and_(col.expr >= lo, col.expr < hi), needs
        if op == "not_equals":
            return not_(and_(col.expr >= lo, col.expr < hi)), needs
        if op == "gt":
            return col.expr >= hi, needs
        if op == "gte":
            return col.expr >= lo, needs
        if op == "lt":
            return col.expr < lo, needs
        if op == "lte":
            return col.expr < hi, needs
        if op == "between":
            if not str(value2 or "").strip():
                raise QueryError(f"'{key}' between needs value and value2")
            return and_(col.expr >= lo, col.expr < _parse_date(str(value2))[1]), needs
        raise QueryError(f"'{op}' does not work on the date column '{key}'. Use equals, gt, gte, lt, lte or between.")

    if col.kind in ("number", "money"):
        n = _number(text)
        table = {"equals": col.expr == n, "not_equals": col.expr != n, "gt": col.expr > n, "gte": col.expr >= n,
                 "lt": col.expr < n, "lte": col.expr <= n}
        if op == "between":
            if not str(value2 or "").strip():
                raise QueryError(f"'{key}' between needs value and value2")
            return and_(col.expr >= n, col.expr <= _number(str(value2))), needs
        if op in table:
            return table[op], needs
        raise QueryError(f"'{op}' does not work on the number column '{key}'.")

    if col.kind == "enum":
        wanted = "_".join(text.lower().split())
        if col.values and wanted not in col.values:
            raise QueryError(f"'{text}' is not a value of {key}. Values: {', '.join(col.values)}.")
        if op == "equals":
            return func.lower(cast(col.expr, String)) == wanted, needs
        if op == "not_equals":
            return or_(col.expr.is_(None), func.lower(cast(col.expr, String)) != wanted), needs
        raise QueryError(f"'{op}' does not work on '{key}'. Use equals or not_equals.")

    # text
    if op == "equals":
        return func.lower(col.expr) == text.lower(), needs
    if op == "not_equals":
        return or_(col.expr.is_(None), func.lower(col.expr) != text.lower()), needs
    if op == "contains":
        like = col.expr.ilike(_like(text), escape="\\")
        if col.fuzzy_on is None:
            return like, needs
        ids = _fuzzy_person_ids(db, text, [col.fuzzy_on])
        return or_(like, ds.person_id.in_(ids or [None])), needs
    raise QueryError(f"'{op}' does not work on the text column '{key}'. Use equals, not_equals, contains, is_empty or not_empty.")


# --------------------------------------------------------------------------------------------- the query
@dataclass
class Spec:
    dataset: str = "people"
    filters: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    sort_by: str = ""
    sort_dir: str = ""
    group_by: str = ""
    limit: int = 20
    offset: int = 0
    count_only: bool = False
    ids: list = field(default_factory=list)  # internal: restrict to these people (never set from model input)


def _from(ds: Dataset, needs: set[str]) -> Any:
    """The FROM clause with only the joins this question needs."""
    if ds.kind == "emails":
        return SentEmail.__table__.join(Prospect.__table__, Prospect.id == SentEmail.prospect_id)
    needs = set(needs) | _FORCE_NEEDS.get(ds.name, set())
    sel = Prospect.__table__
    if needs & {"link", "profile", "conversation"}:
        sel = sel.outerjoin(BEST_LINK, BEST_LINK.c.pid == Prospect.id).outerjoin(LINK, LINK.id == BEST_LINK.c.link_id)
    if "profile" in needs:
        sel = sel.outerjoin(PROFILE, PROFILE.campaign_prospect_id == LINK.id)
    if "conversation" in needs:
        sel = sel.outerjoin(CONVERSATION, CONVERSATION.campaign_prospect_id == LINK.id)
    if "mail" in needs:
        sel = sel.outerjoin(MAIL, MAIL.c.pid == Prospect.id)
    return sel


def _needs_for_base(ds: Dataset) -> set[str]:
    return set(_FORCE_NEEDS.get(ds.name, set()))


def build(db: Session, spec: Spec) -> dict[str, Any]:
    """Validate ``spec`` and run it. Returns the rows, the total and what was asked."""
    ds = DATASETS.get(spec.dataset)
    if ds is None:
        raise QueryError(f"Unknown dataset '{spec.dataset}'. Datasets: {', '.join(DATASETS)}.")

    needs = _needs_for_base(ds)
    where = list(ds.base_where())
    if spec.ids:
        where.append(ds.person_id.in_(spec.ids))
    for f in spec.filters or []:
        cond, n = _condition(db, ds, f)
        where.append(cond)
        needs |= n

    limit = max(0, min(int(spec.limit or 20), MAX_ROWS))
    offset = max(0, int(spec.offset or 0))

    def need_of(keys: list[str]) -> set[str]:
        out: set[str] = set()
        for k in keys:
            c = ds.columns.get(k)
            if c is None:
                raise QueryError(f"'{k}' is not a column of {ds.name}. Columns: {', '.join(ds.columns)}.")
            out |= set(c.needs)
        return out

    # grouped counts
    if spec.group_by:
        gcol = ds.columns.get(spec.group_by)
        if gcol is None:
            raise QueryError(f"Cannot group by '{spec.group_by}'. Columns: {', '.join(ds.columns)}.")
        if gcol.kind in ("money", "number"):
            raise QueryError(f"Group by a text, status or date column, not '{spec.group_by}'.")
        needs |= set(gcol.needs)
        gexpr = (
            func.to_char(func.timezone("America/Chicago", gcol.expr), "YYYY-MM-DD") if gcol.kind == "date" else gcol.expr
        )
        count_expr = func.count(func.distinct(ds.person_id)) if ds.kind == "people" else func.count()
        q = select(gexpr.label("grp"), count_expr.label("n")).select_from(_from(ds, needs)).group_by(gexpr)
        if where:
            q = q.where(*where)
        rows = db.execute(q.order_by(func.count().desc()).limit(MAX_ROWS)).all()
        return {"mode": "group", "dataset": ds.name, "group_by": spec.group_by, "groups": [(r[0], int(r[1])) for r in rows]}

    count_q = select(func.count()).select_from(_from(ds, needs))
    if where:
        count_q = count_q.where(*where)
    total = int(db.scalar(count_q) or 0)
    if spec.count_only or limit == 0:
        return {"mode": "count", "dataset": ds.name, "total": total}

    keys = list(spec.columns or ds.default_columns)[:MAX_COLUMNS]
    needs |= need_of(keys)
    sort_key = spec.sort_by or ds.default_sort[0]
    sort_dir = (spec.sort_dir or ds.default_sort[1]).lower()
    scol = ds.columns.get(sort_key)
    if scol is None:
        raise QueryError(f"Cannot sort by '{sort_key}'. Columns: {', '.join(ds.columns)}.")
    needs |= set(scol.needs)
    if sort_dir not in ("asc", "desc"):
        raise QueryError("sort_dir must be asc or desc")
    order = scol.expr.asc().nulls_last() if sort_dir == "asc" else scol.expr.desc().nulls_last()

    id_label = "row_id"
    q = (
        select(ds.person_id.label(id_label), *[ds.columns[k].expr.label(k) for k in keys])
        .select_from(_from(ds, needs))
    )
    if where:
        q = q.where(*where)
    rows = db.execute(q.order_by(order, ds.person_id).limit(limit).offset(offset)).mappings().all()
    return {
        "mode": "rows", "dataset": ds.name, "total": total, "columns": keys, "rows": [dict(r) for r in rows],
        "offset": offset, "limit": limit, "sorted_by": f"{sort_key} {sort_dir}",
    }


def matching_ids(db: Session, filters: list[dict], limit: int) -> tuple[list[Any], int]:
    """Ids of the people matching ``filters`` (at most ``limit``) and how many match in all."""
    ds = DATASETS["people"]
    needs: set[str] = set()
    where = []
    for f in filters or []:
        cond, n = _condition(db, ds, f)
        where.append(cond)
        needs |= n
    frm = _from(ds, needs)
    total_q = select(func.count()).select_from(frm)
    ids_q = select(ds.person_id).select_from(frm).order_by(ds.person_id).limit(limit)
    if where:
        total_q, ids_q = total_q.where(*where), ids_q.where(*where)
    return list(db.scalars(ids_q)), int(db.scalar(total_q) or 0)


# --------------------------------------------------------------------------------------------- text
def fmt_money(value: Any) -> str:
    return "-" if value is None else f"${Decimal(str(value)):,.2f}"


def _fmt(col: Column, value: Any) -> str:
    if value is None or value == "":
        return "-"
    if col.kind == "date" and isinstance(value, datetime):
        return chicago.fmt_time(value)
    if col.kind == "money":
        return fmt_money(value)
    text = " ".join(str(value).split())
    if col.kind == "enum":
        text = text.replace("_", " ")
    return text if len(text) <= 80 else text[:79] + "…"


def render(db: Session, spec: Spec, result: dict[str, Any]) -> str:
    """The result as short text for the model to read back to the user."""
    ds = DATASETS[result["dataset"]]
    if result["mode"] == "count":
        return f"{result['total']} {ds.name} match."
    if result["mode"] == "group":
        if not result["groups"]:
            return "No matches."
        lines = [f"{('-' if g is None else g)}: {n}" for g, n in result["groups"]]
        return f"{ds.name} by {result['group_by']}:\n" + "\n".join(lines)
    rows, total = result["rows"], result["total"]
    if not rows:
        return f"No {ds.name} match."
    cols = [ds.columns[k] for k in result["columns"]]
    lines = []
    for r in rows:
        parts = [f"{c.label}: {_fmt(c, r[c.key])}" for c in cols]
        lines.append("- " + " | ".join(parts))
    head = f"{total} {ds.name} match"
    shown = len(rows)
    if shown < total:
        head += f"; showing {shown} from {result['offset'] + 1}, sorted by {result['sorted_by']}"
    note = f"\nTimes are in {TZ_LABEL}." if any(c.kind == "date" for c in cols) else ""
    return head + ":\n" + "\n".join(lines) + note


def run(db: Session, spec: Spec) -> str:
    return render(db, spec, build(db, spec))


def column_guide() -> str:
    """One line per dataset listing its columns (used in tool schemas)."""
    out = []
    for ds in DATASETS.values():
        out.append(f"{ds.name}: " + ", ".join(ds.columns))
    return " | ".join(out)


def all_column_keys() -> list[str]:
    seen: dict[str, None] = {}
    for ds in DATASETS.values():
        for k in ds.columns:
            seen.setdefault(k, None)
    return list(seen)


__all__ = [
    "DATASETS", "OPS", "QueryError", "Spec", "TZ", "all_column_keys", "build", "column_guide", "fmt_money",
    "matching_ids", "render", "run",
]
