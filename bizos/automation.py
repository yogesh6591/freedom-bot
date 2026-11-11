"""
Scheduled Tasks and Duplicate-Free Posting
==========================================

A person asks the assistant to do something every day, every week, or every few minutes or hours ("every
Monday at 9 post our weekly tip on the Facebook Page", "every day at 6 PM email me today's new leads",
"every 30 minutes post the latest AI news"). The task is saved in the
workspace with the exact instruction, and a background loop runs it when it is due, **as that person,
through the normal chat pipeline**: the same guard, policy check, approvals and audit as if they had typed
it. A scheduled run is never less checked than a typed message; anything that needs approval waits in
Approvals.

* :func:`next_run` works out the next due time (daily, or on chosen weekdays, at HH:MM in a timezone, or
  every N minutes; never more often than every :data:`MIN_EVERY_MINUTES`).
* :func:`claim` takes one due run atomically, so a slot runs once even with several API processes.
* :func:`run_due` is one tick of the loop: every workspace, every due task.
* :func:`already_posted` / :func:`record_post` keep public posts free of duplicates: the same text to the
  same Page is refused, whether it comes from a scheduled run or from chat.

Plain Python; the chat tools (app/assistant/company_tools.py) and the loop (app/main.py) wire it in.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import readonly_connection, workspace_connection, workspace_location
from bizos.util.ids import new_id

DAILY, WEEKLY, INTERVAL = "daily", "weekly", "interval"
FREQUENCIES = (DAILY, WEEKLY, INTERVAL)
#: Shortest and longest repeat for an "every N minutes" schedule. Each run is a full chat turn (model,
#: tools, maybe a public post), so it is never more often than this.
MIN_EVERY_MINUTES, MAX_EVERY_MINUTES = 5, 7 * 24 * 60
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ACTIVE, CANCELLED, PAUSED = "active", "cancelled", "paused"
#: Most active schedules one person may have (keeps a runaway request from filling the queue).
MAX_ACTIVE_PER_USER = 25
MAX_INSTRUCTION = 2000

_WEEKDAY_NAMES = {
    **{d: d for d in WEEKDAYS},
    "monday": "mon", "tuesday": "tue", "tues": "tue", "wednesday": "wed", "thursday": "thu", "thur": "thu",
    "thurs": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
}

_lock = threading.Lock()
_ready: set[tuple[str, str]] = set()


class ScheduleError(ValueError):
    """A schedule request that cannot be saved as given. The message says how to fix it."""


# --------------------------------------------------------------------------- parsing


def parse_time(value: Any) -> str:
    """'09:00', '9:30', '6 pm', '6:30pm', '18:30' -> 'HH:MM' (24-hour)."""
    raw = str(value or "").strip().lower().replace(".", "")
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", raw)
    if not match:
        raise ScheduleError(f"time must look like 09:00 or 6:30 PM (got {value!r}).")
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half:
        if not 1 <= hour <= 12:
            raise ScheduleError(f"{value!r} is not a valid time.")
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ScheduleError(f"{value!r} is not a valid time.")
    return f"{hour:02d}:{minute:02d}"


def parse_weekdays(values: Any) -> tuple[str, ...]:
    """['Monday', 'thu'] -> ('mon', 'thu'), in week order. Unknown names are refused."""
    if isinstance(values, str):
        values = [v for v in re.split(r"[,\s]+", values) if v]
    out: set[str] = set()
    for value in values or []:
        key = _WEEKDAY_NAMES.get(str(value).strip().lower())
        if key is None:
            raise ScheduleError(f"{value!r} is not a weekday. Use mon, tue, wed, thu, fri, sat or sun.")
        out.add(key)
    return tuple(d for d in WEEKDAYS if d in out)


def parse_every(value: Any) -> int:
    """5, '5', '5 min', '50 minutes', '2 hours', '1h', '90m' -> minutes between runs (5 to a week)."""
    if isinstance(value, bool):
        value = None
    if isinstance(value, (int, float)):
        minutes = int(value)
    else:
        raw = str(value or "").strip().lower()
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?", raw)
        if not match:
            raise ScheduleError("every_minutes must be a number of minutes, e.g. 5, 50 or 120 (every 2 hours).")
        number = float(match.group(1))
        minutes = int(round(number * 60)) if (match.group(2) or "m").startswith("h") else int(round(number))
    if minutes < MIN_EVERY_MINUTES:
        raise ScheduleError(f"the shortest repeat is every {MIN_EVERY_MINUTES} minutes (got {minutes}).")
    if minutes > MAX_EVERY_MINUTES:
        raise ScheduleError("for a gap longer than a week, use a weekly schedule.")
    return minutes


def parse_timezone(value: Any, default: str) -> str:
    name = str(value or "").strip() or default
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"{name!r} is not a timezone. Use a name like Asia/Kolkata or America/New_York.") from exc
    return name


def next_run(frequency: str, time_of_day: str, tz: str, *, weekdays: Iterable[str] = (),
             after: Optional[datetime] = None, every_minutes: Optional[int] = None) -> datetime:
    """The first due time strictly after ``after`` (UTC).

    Daily or weekly: the next HH:MM on a matching day. Every N minutes: N minutes after ``after``, or the
    next HH:MM when a start time is given.
    """
    if frequency == INTERVAL and not time_of_day:
        return (after or datetime.now(timezone.utc)).astimezone(timezone.utc) + timedelta(
            minutes=parse_every(every_minutes))
    if frequency == INTERVAL:
        frequency = DAILY
    zone = ZoneInfo(tz)
    now = (after or datetime.now(timezone.utc)).astimezone(zone)
    hour, minute = (int(p) for p in time_of_day.split(":"))
    days = set(weekdays) if frequency == WEEKLY else set(WEEKDAYS)
    if not days:
        raise ScheduleError("a weekly schedule needs at least one weekday.")
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).date()
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if candidate > now and WEEKDAYS[candidate.weekday()] in days:
            return candidate.astimezone(timezone.utc)
    raise ScheduleError("could not work out the next run time.")  # unreachable: 8 days cover every weekday


def _every(minutes: int) -> str:
    if minutes % 60:
        return f"every {minutes} minutes"
    hours = minutes // 60
    return "every hour" if hours == 1 else f"every {hours} hours"


def describe(task: dict[str, Any]) -> str:
    """'every day at 09:00 (Asia/Kolkata)' / 'every Mon, Thu at 18:30 (Asia/Kolkata)' / 'every 5 minutes'."""
    if task["frequency"] == INTERVAL:
        start = f", from {task['time_of_day']} ({task['timezone']})" if task.get("time_of_day") else ""
        return _every(int(task.get("every_minutes") or 0)) + start
    when = "every day" if task["frequency"] == DAILY else "every " + ", ".join(
        d.capitalize() for d in task.get("weekdays") or ())
    return f"{when} at {task['time_of_day']} ({task['timezone']})"


# --------------------------------------------------------------------------- storage


def ensure_tables(ctx: TenantContext) -> None:
    """Create the two tables in this workspace on first use (with the per-client wall)."""
    from bizos.tenancy.schema import client_wall_ddl

    key = workspace_location(ctx)
    if key in _ready:
        return
    with _lock:
        if key in _ready:
            return
        with workspace_connection(ctx) as conn:
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id           TEXT PRIMARY KEY,
                    title        TEXT NOT NULL,
                    instruction  TEXT NOT NULL,
                    frequency    TEXT NOT NULL,
                    weekdays     TEXT[] NOT NULL DEFAULT '{}',
                    time_of_day  TEXT NOT NULL,
                    every_minutes INTEGER,
                    timezone     TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'active',
                    created_by   TEXT NOT NULL,
                    next_run_at  TIMESTAMPTZ,
                    last_run_at  TIMESTAMPTZ,
                    last_status  TEXT,
                    last_result  TEXT NOT NULL DEFAULT '',
                    run_count    INTEGER NOT NULL DEFAULT 0,
                    session_id   TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ))
            # Workspaces that made the table before "every N minutes" existed.
            conn.execute(text("ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS every_minutes INTEGER"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS scheduled_tasks_due_idx ON scheduled_tasks (status, next_run_at)"
            ))
            conn.execute(text(
                """
                CREATE TABLE IF NOT EXISTS published_posts (
                    id            TEXT PRIMARY KEY,
                    app           TEXT NOT NULL,
                    target        TEXT NOT NULL DEFAULT '',
                    content_hash  TEXT NOT NULL,
                    preview       TEXT NOT NULL DEFAULT '',
                    action        TEXT NOT NULL,
                    posted_by     TEXT NOT NULL,
                    posted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (app, target, content_hash)
                )
                """
            ))
            for statement in client_wall_ddl(ctx.client_id, ("scheduled_tasks", "published_posts")):
                conn.execute(text(statement))
        _ready.add(key)


def _row(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    data.pop("client_id", None)
    data["weekdays"] = list(data.get("weekdays") or [])
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    data["schedule"] = describe(data)
    return data


def _same(text_value: str) -> str:
    return " ".join(str(text_value or "").casefold().split())


def create(ctx: TenantContext, *, title: str, instruction: str, frequency: str, time_of_day: Any,
           weekdays: Any = (), tz: Any = None, default_tz: str = "UTC", every_minutes: Any = None,
           now: Optional[datetime] = None, owner_user_id: Optional[str] = None) -> tuple[dict[str, Any], bool]:
    """Save a schedule for ``owner_user_id`` (default: ``ctx``'s user). Returns ``(task, created)``; the same
    instruction on the same schedule returns the existing task (``created`` False) instead of a duplicate.

    When an approver releases someone else's request, ``owner_user_id`` is that requester: the schedule is
    theirs and every run is checked as them, never as the approver.
    """
    owner = owner_user_id or ctx.user_id
    instruction = " ".join(str(instruction or "").split())
    title = " ".join(str(title or "").split())[:120] or instruction[:60]
    if not instruction:
        raise ScheduleError("instruction is required: what to do each time, e.g. 'Post our weekly AI tip on "
                            "the Facebook Page'.")
    if len(instruction) > MAX_INSTRUCTION:
        raise ScheduleError(f"instruction is too long (at most {MAX_INSTRUCTION} characters).")
    frequency = str(frequency or "").strip().lower()
    if frequency not in FREQUENCIES:
        raise ScheduleError("frequency must be daily, weekly or interval (every N minutes).")
    every = parse_every(every_minutes) if frequency == INTERVAL else None
    # Every N minutes may start at a time of day; daily and weekly need one.
    at = parse_time(time_of_day) if (frequency != INTERVAL or str(time_of_day or "").strip()) else ""
    days = parse_weekdays(weekdays) if frequency == WEEKLY else ()
    if frequency == WEEKLY and not days:
        raise ScheduleError("a weekly schedule needs weekdays, e.g. ['mon'] or ['mon', 'thu'].")
    zone = parse_timezone(tz, default_tz)
    due = next_run(frequency, at, zone, weekdays=days, after=now, every_minutes=every)
    ensure_tables(ctx)
    with workspace_connection(ctx) as conn:
        for row in conn.execute(
            text("SELECT * FROM scheduled_tasks WHERE created_by = :u AND status = 'active'"), {"u": owner}
        ).fetchall():
            if (_same(row.instruction) == _same(instruction) and row.frequency == frequency
                    and row.time_of_day == at and tuple(row.weekdays or ()) == days and row.timezone == zone
                    and (row.every_minutes or None) == every):
                return _row(row), False
        active = conn.execute(
            text("SELECT count(*) FROM scheduled_tasks WHERE created_by = :u AND status = 'active'"),
            {"u": owner},
        ).scalar()
        if active >= MAX_ACTIVE_PER_USER:
            raise ScheduleError(f"you already have {active} active schedules (the limit is {MAX_ACTIVE_PER_USER}). "
                                "Cancel one first.")
        task_id = new_id("sch")
        row = conn.execute(
            text(
                "INSERT INTO scheduled_tasks (id, title, instruction, frequency, weekdays, time_of_day, "
                "every_minutes, timezone, created_by, next_run_at, session_id) "
                "VALUES (:i, :t, :ins, :f, :w, :at, :e, :tz, :u, :n, :s) RETURNING *"
            ),
            {"i": task_id, "t": title, "ins": instruction, "f": frequency, "w": list(days), "at": at, "e": every,
             "tz": zone, "u": owner, "n": due, "s": new_id("ses")},
        ).first()
    return _row(row), True


def list_tasks(ctx: TenantContext, *, everyone: bool = False, include_ended: bool = False) -> list[dict[str, Any]]:
    """The caller's schedules (``everyone`` for admins: the whole workspace), soonest first."""
    ensure_tables(ctx)
    clauses, params = [], {}
    if not everyone:
        clauses.append("created_by = :u")
        params["u"] = ctx.user_id
    if not include_ended:
        clauses.append("status = 'active'")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text(f"SELECT * FROM scheduled_tasks {where} ORDER BY next_run_at NULLS LAST, created_at"), params
        ).fetchall()
    return [_row(r) for r in rows]


def lookup(ctx: TenantContext, task_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Several schedules by id in one read (the approval cards show the schedule each request belongs to)."""
    ids = sorted({str(i) for i in task_ids if i})
    if not ids:
        return {}
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        rows = conn.execute(text("SELECT * FROM scheduled_tasks WHERE id = ANY(:ids)"), {"ids": ids}).fetchall()
    return {row.id: _row(row) for row in rows}


def get(ctx: TenantContext, task_id: str) -> Optional[dict[str, Any]]:
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        row = conn.execute(text("SELECT * FROM scheduled_tasks WHERE id = :i"), {"i": task_id}).first()
    return _row(row) if row else None


def cancel(ctx: TenantContext, task_id: str, *, requested_by: Optional[str] = None) -> dict[str, Any]:
    """Stop a schedule. Only the person who set it up, or an admin, may stop it.

    ``requested_by`` is the person whose approved request an approver is releasing: their own schedule.
    """
    task = get(ctx, task_id)
    if task is None or (task["created_by"] not in {ctx.user_id, requested_by} and not ctx.is_admin):
        raise ScheduleError(f"No schedule {task_id!r} that you can change. List them with schedule_list.")
    if task["status"] != ACTIVE:
        return task
    with workspace_connection(ctx) as conn:
        row = conn.execute(
            text("UPDATE scheduled_tasks SET status = 'cancelled', next_run_at = NULL, updated_at = NOW() "
                 "WHERE id = :i RETURNING *"),
            {"i": task_id},
        ).first()
    return _row(row)


def due(ctx: TenantContext, now: datetime) -> list[dict[str, Any]]:
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        rows = conn.execute(
            text("SELECT * FROM scheduled_tasks WHERE status = 'active' AND next_run_at <= :n ORDER BY next_run_at"),
            {"n": now},
        ).fetchall()
    return [_row(r) for r in rows]


def claim(ctx: TenantContext, task: dict[str, Any], now: datetime) -> bool:
    """Take this due run: move next_run_at on, only if no one else already did. True when this caller won.

    A run that was missed (the server was down) runs once now, not once per missed slot. Every-N-minutes
    schedules keep their beat (10:00, 10:05, 10:10...) however late the tick picks them up.
    """
    if task["frequency"] == INTERVAL:
        step = timedelta(minutes=parse_every(task.get("every_minutes")))
        following = datetime.fromisoformat(task["next_run_at"]) + step
        if following <= now:
            following += step * ((now - following) // step + 1)
    else:
        following = next_run(task["frequency"], task["time_of_day"], task["timezone"],
                             weekdays=task.get("weekdays") or (), after=now)
    with workspace_connection(ctx) as conn:
        won = conn.execute(
            text(
                "UPDATE scheduled_tasks SET next_run_at = :n, last_run_at = :now, run_count = run_count + 1, "
                "last_status = 'running', updated_at = NOW() "
                "WHERE id = :i AND status = 'active' AND next_run_at = CAST(:old AS TIMESTAMPTZ) RETURNING id"
            ),
            {"n": following, "now": now, "i": task["id"], "old": task["next_run_at"]},
        ).first()
    return won is not None


def record_result(ctx: TenantContext, task_id: str, status: str, result: str, *, pause: bool = False) -> None:
    with workspace_connection(ctx) as conn:
        conn.execute(
            text(
                "UPDATE scheduled_tasks SET last_status = :s, last_result = :r, updated_at = NOW()"
                + (", status = 'paused', next_run_at = NULL" if pause else "") + " WHERE id = :i"
            ),
            {"s": status, "r": str(result or "")[:2000], "i": task_id},
        )


# --------------------------------------------------------------------------- the loop


#: Runs one task: (the creator's context, workspace settings, task) -> a short result for the task row.
Runner = Callable[[TenantContext, Any, dict[str, Any]], str]

#: Postgres advisory-lock key for one tick at a time across API processes (any constant).
_LOCK_KEY = 4_241_771_002


def _workspace_reader(client: Any) -> TenantContext:
    """A context that only reads and claims schedule rows in one workspace. Runs use the creator's own."""
    from bizos.tenancy.context import SCHEDULER_USER_ID
    from bizos.types import Role

    return TenantContext(
        client_id=client.id, client_slug=client.slug, user_id=SCHEDULER_USER_ID, roles=frozenset({Role.VIEWER}),
        deployment_type=client.deployment_type, db_name=client.db_name, db_schema=client.db_schema,
        default_execution_mode=client.settings.mode,
    )


def run_due(runner: Runner, *, now: Optional[datetime] = None, clients: Optional[list[Any]] = None) -> dict[str, Any]:
    """One tick: run every due task in every workspace once, as the person who set it up."""
    from bizos.control import store as control
    from bizos.tenancy.context import tenant_scope

    now = now or datetime.now(timezone.utc)
    report: dict[str, Any] = {"ran": [], "skipped": [], "failed": []}
    for client in clients if clients is not None else control.list_clients():
        reader = _workspace_reader(client)
        try:
            with tenant_scope(reader):
                tasks = due(reader, now)
        except Exception as exc:  # noqa: BLE001 - one unreachable workspace must not stop the others
            report["failed"].append({"client": client.slug, "error": str(exc)[:200]})
            continue
        for task in tasks:
            with tenant_scope(reader):
                if not claim(reader, task, now):
                    continue
            try:
                user = control.get_user(task["created_by"])
            except Exception:  # noqa: BLE001
                user = None
            if user is None or str(getattr(user, "status", "ACTIVE")) != "ACTIVE":
                with tenant_scope(reader):
                    record_result(reader, task["id"], "skipped",
                                  "Paused: the person who set this up is no longer active.", pause=True)
                report["skipped"].append(task["id"])
                continue
            ctx = control.build_context(user, attributes={"scheduled_task": task["id"]})
            settings = control.get_client(client.id).settings
            try:
                with tenant_scope(ctx):
                    result = runner(ctx, settings, task)
                status = "done"
                report["ran"].append(task["id"])
            except Exception as exc:  # noqa: BLE001 - recorded on the task; the next slot still runs
                result, status = f"{type(exc).__name__}: {exc}", "failed"
                report["failed"].append({"task": task["id"], "error": result[:200]})
            with tenant_scope(reader):
                record_result(reader, task["id"], status, result)
    return report


def tick(runner: Runner) -> dict[str, Any]:
    """:func:`run_due`, unless another API process is already running a tick."""
    from bizos.control.db import control_engine

    with control_engine().connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar():
            return {"skipped": "another tick is running"}
        try:
            return run_due(runner)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})


def start(runner: Runner, *, interval: float = 60.0) -> Callable[[], None]:
    """Start the background loop (a daemon thread); returns a stop function."""
    from agno.utils.log import log_info, log_warning

    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                tick(runner)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                log_warning(f"scheduled tasks: tick failed: {exc}")

    threading.Thread(target=loop, name="scheduled-tasks", daemon=True).start()
    log_info(f"scheduled tasks: checking every {int(interval)}s")
    return stop.set


# --------------------------------------------------------------------------- duplicate-free posts

#: Arguments that hold what a post says, and where it goes (Facebook, YouTube comments...).
_TEXT_ARGS = ("message", "text", "caption", "content", "description", "comment", "text_original",
              "textOriginal", "body", "title")
_LINK_ARGS = ("link", "url", "photo_url", "image_url", "video_url", "file_url")
_TARGET_ARGS = ("page_id", "pageId", "page", "channel_id", "channelId", "video_id", "videoId", "parent_id",
                "parentId", "post_id", "object_id", "group_id")


def post_fingerprint(app: str, arguments: dict[str, Any]) -> Optional[tuple[str, str, str]]:
    """(target, content hash, preview) for a public post, or None when it carries no text or link to compare."""
    args = arguments if isinstance(arguments, dict) else {}
    words = " ".join(str(args[k]) for k in _TEXT_ARGS if args.get(k))
    links = " ".join(str(args[k]).strip().lower().rstrip("/") for k in _LINK_ARGS if args.get(k))
    content = _same(words)
    if not content and not links:
        return None
    target = next((str(args[k]).strip() for k in _TARGET_ARGS if args.get(k)), "")
    digest = hashlib.sha256(json.dumps([app.lower(), content, links]).encode()).hexdigest()
    return target, digest, " ".join(words.split())[:160]


def already_posted(ctx: TenantContext, app: str, arguments: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The earlier post with the same content to the same place, if there is one."""
    found = post_fingerprint(app, arguments)
    if found is None:
        return None
    target, digest, _ = found
    ensure_tables(ctx)
    with readonly_connection(ctx) as conn:
        row = conn.execute(
            text("SELECT action, preview, posted_at FROM published_posts WHERE app = :a AND target = :t "
                 "AND content_hash = :h"),
            {"a": app.lower(), "t": target, "h": digest},
        ).first()
    return {"action": row.action, "preview": row.preview, "posted_at": row.posted_at.isoformat()} if row else None


def record_post(ctx: TenantContext, app: str, action: str, arguments: dict[str, Any], *, posted_by: str) -> None:
    """Remember a post that went out, so the same content is not posted to the same place again."""
    found = post_fingerprint(app, arguments)
    if found is None:
        return
    target, digest, preview = found
    ensure_tables(ctx)
    with workspace_connection(ctx) as conn:
        conn.execute(
            text("INSERT INTO published_posts (id, app, target, content_hash, preview, action, posted_by) "
                 "VALUES (:i, :a, :t, :h, :p, :act, :u) ON CONFLICT (app, target, content_hash) DO NOTHING"),
            {"i": new_id("pub"), "a": app.lower(), "t": target, "h": digest, "p": preview, "act": action,
             "u": posted_by},
        )
