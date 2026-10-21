"""Live pipeline trace — persists each step of a run for the Admin "Live Trace" view.

A run opens a trace context (``live_trace.run(...)``). While it is open, every
step — slice picked, source harvested, candidate filtered, dedupe decision,
research, AI checks, scores, final status — is written as one row to
``acquisition_trace_events`` on its own short-lived session, so the Admin page
can poll and show the run unfolding one step at a time.

It never stores secrets, API keys, or full prompts. Outside an open context,
calls are no-ops, so business logic is unaffected.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Stage catalogue: short label + plain-English explanation for developers.
STAGES: dict[str, dict[str, str]] = {
    "run": {
        "label": "Run",
        "explain": "A discovery cycle started or finished. Totals at the end show what the run achieved.",
    },
    "icp": {
        "label": "ICP rules",
        "explain": "The target-market rules loaded from Admin settings. Every later decision is checked against these.",
    },
    "plan": {
        "label": "Plan",
        "explain": "The market is split into slices (source × category × state × angle). The least-recently-run slices are picked so each run looks somewhere new instead of repeating yesterday.",
    },
    "harvest": {
        "label": "Harvest",
        "explain": "OpenAI web search was queried for companies in one market slice.",
    },
    "filter": {
        "label": "Filter",
        "explain": "Cheap checks before any research money is spent: directory/review/social sites, 'Top 10' list pages, excluded industries, and non-US results are dropped.",
    },
    "dedupe": {
        "label": "Dedupe",
        "explain": "The candidate is matched against the permanent database by root domain, NPI, or normalized name, so one company only ever gets one record.",
    },
    "known": {
        "label": "Known",
        "explain": "The company is already in the database and was researched recently, so it is skipped — no repeat research cost, and it does not count as 'new'.",
    },
    "verify": {
        "label": "Verify",
        "explain": "The website is fetched to confirm the domain is real and belongs to this company. AI suggestions are never trusted without this check.",
    },
    "triage": {
        "label": "Candidate selection",
        "explain": "All surviving candidates from every source are compared against the full ICP. An AI (or deterministic fallback) ranks the pool and only the top requested count continue to deep research.",
    },
    "save": {
        "label": "Save",
        "explain": "A permanent prospect record was created (new) or refreshed (existing).",
    },
    "research": {
        "label": "Research",
        "explain": "Public sources were read to fill in description, location, size, decision-maker, contact path and signals — each fact keeps its source URL.",
    },
    "ai": {
        "label": "AI check",
        "explain": "An AI step judged something (same company? correct decision-maker? industry?). The AI only suggests; hard rules in code decide.",
    },
    "score": {
        "label": "Score",
        "explain": "Fit, timing and reachability scores were computed and combined into the priority score using the weights from Admin settings.",
    },
    "decision": {
        "label": "Decision",
        "explain": "The final state for this company in this run: ready for outreach, scored, needs more research, unreachable, or excluded.",
    },
    "progress": {
        "label": "Progress",
        "explain": "Running totals — how many candidates were inspected and how many usable new prospects have been produced so far.",
    },
    "error": {
        "label": "Error",
        "explain": "Something failed. The run isolates failures per company and continues with the next one.",
    },
}

_LEVELS = {"info", "success", "warning", "error", "muted"}


@dataclass
class _TraceContext:
    run_id: uuid.UUID
    bind: Any
    seq: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    enabled: bool = True


_current: contextvars.ContextVar[_TraceContext | None] = contextvars.ContextVar(
    "freedombot_live_trace", default=None
)


def current_run_id() -> uuid.UUID | None:
    ctx = _current.get()
    return ctx.run_id if ctx else None


def is_active() -> bool:
    ctx = _current.get()
    return bool(ctx and ctx.enabled)


@contextlib.contextmanager
def run(run_id: uuid.UUID, db: Session) -> Iterator[uuid.UUID]:
    """Open a trace context bound to the same database as ``db``."""
    ctx = _TraceContext(run_id=run_id, bind=db.get_bind())
    token = _current.set(ctx)
    try:
        yield run_id
    finally:
        _current.reset(token)


_LIST_LIMIT_DEFAULT = 40
# Pool / triage arrays must keep the full survivor set for replay + global AI.
_LIST_LIMIT_BY_KEY = {
    "surviving_pool": 300,
    "accepted_companies": 300,
    "returned_companies": 300,
    "produced_companies": 300,
    "selected": 120,
    "not_selected": 300,
    "candidates": 300,
    "companies": 120,
    "queries": 80,
    "slices": 80,
    "requests": 80,
}


def _clean(value: Any, depth: int = 0, *, list_limit: int = _LIST_LIMIT_DEFAULT) -> Any:
    if depth > 5:
        return str(value)[:200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in list(value.items())[:80]:
            key = str(k)
            if any(s in key.lower() for s in ("api_key", "token", "secret", "password", "authorization")):
                continue
            child_limit = _LIST_LIMIT_BY_KEY.get(key, _LIST_LIMIT_DEFAULT)
            out[key] = _clean(v, depth + 1, list_limit=child_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_clean(v, depth + 1) for v in list(value)[: max(1, int(list_limit))]]
    text = str(value)
    return text if len(text) <= 1500 else text[:1497] + "…"


def event(
    stage: str,
    title: str,
    *,
    level: str = "info",
    company: str | None = None,
    prospect_id: Any = None,
    **detail: Any,
) -> None:
    """Record one pipeline step. No-op when no trace context is open."""
    ctx = _current.get()
    if ctx is None or not ctx.enabled:
        return
    from acquisition.models.prospect import AcquisitionTraceEvent

    with ctx.lock:
        ctx.seq += 1
        seq = ctx.seq
    pid = None
    if prospect_id:
        try:
            pid = prospect_id if isinstance(prospect_id, uuid.UUID) else uuid.UUID(str(prospect_id))
        except (ValueError, TypeError):
            pid = None
    row = AcquisitionTraceEvent(
        run_id=ctx.run_id,
        seq=seq,
        stage=(stage or "info")[:48],
        level=level if level in _LEVELS else "info",
        title=(title or "")[:512],
        company=(company or None) and str(company)[:255],
        prospect_id=pid,
        detail_json=_clean(detail) if detail else None,
    )
    try:
        with Session(bind=ctx.bind) as s:
            s.add(row)
            s.commit()
    except Exception:  # noqa: BLE001 — tracing must never break a run
        logger.warning("live trace write failed; disabling for this run", exc_info=True)
        ctx.enabled = False


# ---------------------------------------------------------------------------
# Bridge from the existing [ACQ] trace blocks (acquisition_trace.emit_block).
# ---------------------------------------------------------------------------


def _kv(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    loose: list[str] = []
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip().strip('"')
        elif line.strip():
            loose.append(line.strip())
    return fields, loose


# Per-candidate / per-score ACQ blocks stay in application logs only.
# Live Trace records aggregate summaries emitted by multi_source / orchestration.
_ACQ_LIVE_SUPPRESS = frozenset(
    {
        "QUERY_PLAN",
        "SEARCH",
        "SEARCH][RESULT",
        "CANDIDATE",
        "DEDUPE",
        "SAVE",
        "RESEARCH",
        "AI",
        "SCORE][FIT",
        "SCORE][TIMING",
        "SCORE][REACHABILITY",
        "SCORE][PRIORITY",
        "DONE",
        "PROGRESS",
        "SKIP",
        "AI-LEARNING",
    }
)


def from_acq_block(section: str, lines: list[str]) -> None:
    """Translate selected [ACQ] blocks into Live Trace (ICP only by default).

    Routine per-candidate and per-score blocks are suppressed so Live Trace
    stays at ~15–25 meaningful aggregate events. Developer ACQ logs still emit.
    """
    if not is_active():
        return
    sec = section.upper()
    if sec in _ACQ_LIVE_SUPPRESS or sec.startswith("SCORE]["):
        return
    f, loose = _kv(lines)
    company = f.get("Company") or f.get("company") or (loose[0] if loose else None)
    pid = f.get("prospect_id") or f.get("ProspectID")
    for reserved in ("company", "prospect_id", "level", "title", "stage"):
        f.pop(reserved, None)

    if sec == "ICP":
        event("icp", "ICP / settings loaded", **f)
    else:
        # Unknown sections: keep a single compact event (not candidate loops).
        event(sec.lower()[:48], section, company=company, prospect_id=pid, **f)
