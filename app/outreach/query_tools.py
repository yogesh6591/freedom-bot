"""Read-only questions over acquisition data. The model describes the question; the database answers it.

Only the matching rows come back (at most 50, one short line each), so the chat never loads a whole list.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from acquisition.models.outreach import STATUS_QUEUED, SentEmail
from acquisition.models.prospect import Prospect
from acquisition.services import outreach_service as svc
from app.outreach import chicago, datasets, lookup
from app.outreach.toolkit import Call, ToolError, tool


@tool("query_records", "dataset", "filters", "columns", "sort_by", "sort_dir", "group_by", "limit", "offset", "count_only")
def query_records(call: Call, dataset: str = "people", filters: Optional[list] = None, columns: Optional[list] = None,
                  sort_by: str = "", sort_dir: str = "", group_by: str = "", limit: int = 20, offset: int = 0,
                  count_only: bool = False) -> str:
    """Count, list or group people, emails, bookings or payments. Read-only."""
    spec = datasets.Spec(
        dataset=(dataset or "people").strip().lower(),
        filters=[f for f in filters or [] if isinstance(f, dict)],
        columns=[str(c) for c in columns or []],
        sort_by=(sort_by or "").strip(),
        sort_dir=(sort_dir or "").strip().lower(),
        group_by=(group_by or "").strip(),
        limit=_whole(limit, 20),
        offset=_whole(offset, 0),
        count_only=bool(count_only),
    )
    _maybe_focus_prospect(call, spec)
    try:
        text = datasets.run(call.db, spec)
        if spec.dataset == "people" and spec.limit == 1 and not spec.count_only and "0 row" not in text.lower():
            hit = _focus_from_filters(spec.filters)
            if hit:
                call.state["focus_prospect"] = hit
        return text
    except datasets.QueryError as exc:
        raise ToolError(str(exc)) from exc


def _focus_from_filters(filters: list[dict]) -> str:
    for item in filters:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip().lower()
        if field not in ("email", "executive_name", "name", "company"):
            continue
        op = str(item.get("op") or "").strip().lower()
        if op not in ("eq", "is", "contains"):
            continue
        value = str(item.get("value") or "").strip()
        if value:
            return value
    return ""


def _maybe_focus_prospect(call: Call, spec: datasets.Spec) -> None:
    if spec.dataset != "people" or spec.count_only:
        return
    hit = _focus_from_filters(spec.filters)
    if hit:
        call.state["focus_prospect"] = hit


def _whole(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return default


@tool("show_sent_emails", "who", "limit")
def show_sent_emails(call: Call, who: str = "", limit: int = 10) -> str:
    """The email log: what is waiting, what went out today, and the latest emails (for one person or everyone)."""
    db = call.db
    q = select(SentEmail, Prospect).join(Prospect, Prospect.id == SentEmail.prospect_id)
    if (who or "").strip():
        ids = [p.id for p, _ in lookup.find(db, who, limit=20)]
        if not ids:
            raise ToolError(f"no saved prospect matches {who!r}.")
        q = q.where(SentEmail.prospect_id.in_(ids))
    rows = db.execute(q.order_by(SentEmail.queued_at.desc()).limit(max(1, min(_whole(limit, 10), 30)))).all()
    waiting = db.scalar(select(func.count()).select_from(SentEmail).where(SentEmail.status == STATUS_QUEUED)) or 0
    lines = [f"{waiting} email(s) waiting to go out; {svc.sent_today(db)} sent today."]
    for e, p in rows:
        problem = f" - {e.error}" if e.error and e.status != "sent" else ""
        lines.append(f"{chicago.fmt_day(e.sent_at or e.queued_at)}: {e.kind} to {p.executive_name} <{e.to_email}> - "
                     f"{e.status} - subject: {e.subject!r}{problem}")
    if not rows:
        lines.append("No emails recorded yet." if not who else f"No emails to {who} yet.")
    return "\n".join(lines)
