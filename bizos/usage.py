"""Token usage per chat turn (Audit → Token usage).

Every model call in one turn (the router, the assistant runs, the assessment
reply) is added to a collector bound to the request. When the turn ends, one
row is saved in the workspace: who asked, the question (secrets redacted),
the tokens per step, and an estimated cost. Saving never fails a chat turn.

Costs are estimates from the list prices below (USD per 1M tokens). A model
that is not listed shows no cost; the tokens are still counted.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from typing import Any, Iterator, Optional

from sqlalchemy import text

from bizos.tenancy.context import TenantContext
from bizos.tenancy.registry import readonly_connection, workspace_connection, workspace_location
from bizos.util.ids import new_id
from bizos.util.redaction import redact_text
from bizos.util.timeutil import utcnow

log = logging.getLogger(__name__)

#: USD per 1M tokens: (input, cached input, output). Longest matching prefix wins.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "o4-mini": (1.10, 0.275, 4.40),
    "o3": (2.00, 0.50, 8.00),
}

QUERY_CHARS = 500


def price_for(model: str) -> Optional[tuple[float, float, float]]:
    name = (model or "").strip().lower()
    for prefix in sorted(PRICES, key=len, reverse=True):
        if name.startswith(prefix):
            return PRICES[prefix]
    return None


def estimate_cost(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> Optional[float]:
    """Estimated USD cost of one call. Cached input is billed at the cached price."""
    price = price_for(model)
    if price is None:
        return None
    fresh = max(input_tokens - cached_tokens, 0)
    return round((fresh * price[0] + cached_tokens * price[1] + output_tokens * price[2]) / 1_000_000, 6)


class Collector:
    """The model calls made in one turn. Shared with worker threads through the context."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, call: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(call)

    def totals(self) -> dict[str, Any]:
        keys = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "total_tokens")
        out: dict[str, Any] = {k: sum(int(c.get(k) or 0) for c in self.calls) for k in keys}
        costs = [c["cost_usd"] for c in self.calls if c.get("cost_usd") is not None]
        out["cost_usd"] = round(sum(costs), 6) if costs else None
        return out


_current: ContextVar[Optional[Collector]] = ContextVar("bizos_usage_collector", default=None)


@contextmanager
def collect() -> Iterator[Collector]:
    """Bind a collector to this turn. Threads started with copy_context() add to the same one."""
    collector = Collector()
    token = _current.set(collector)
    try:
        yield collector
    finally:
        _current.reset(token)


def record(output: Any, *, step: str, model: Any = None) -> None:
    """Add one Agno run's token metrics to the current turn. Silent when nothing is collecting."""
    collector = _current.get()
    if collector is None or output is None:
        return
    try:
        metrics = getattr(output, "metrics", None)
        if metrics is None:
            return
        model_id = str(getattr(output, "model", None) or getattr(model, "id", None) or model or "")
        input_tokens = int(getattr(metrics, "input_tokens", 0) or 0)
        output_tokens = int(getattr(metrics, "output_tokens", 0) or 0)
        cached = int(getattr(metrics, "cache_read_tokens", 0) or 0)
        reasoning = int(getattr(metrics, "reasoning_tokens", 0) or 0)
        total = int(getattr(metrics, "total_tokens", 0) or 0) or input_tokens + output_tokens
        collector.add(
            {
                "step": step,
                "model": model_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached,
                "reasoning_tokens": reasoning,
                "total_tokens": total,
                "cost_usd": estimate_cost(model_id, input_tokens, cached, output_tokens),
            }
        )
    except Exception:  # noqa: BLE001 - counting tokens must never break a turn
        log.exception("usage.record failed")


_ready: set[Any] = set()
_lock = threading.Lock()


def ensure_table(ctx: TenantContext) -> None:
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
                CREATE TABLE IF NOT EXISTS token_usage (
                    id               TEXT PRIMARY KEY,
                    user_id          TEXT NOT NULL,
                    session_id       TEXT,
                    source           TEXT NOT NULL DEFAULT 'chat',
                    query            TEXT NOT NULL DEFAULT '',
                    status           TEXT NOT NULL DEFAULT 'ok',
                    model            TEXT NOT NULL DEFAULT '',
                    calls            INTEGER NOT NULL DEFAULT 0,
                    input_tokens     INTEGER NOT NULL DEFAULT 0,
                    output_tokens    INTEGER NOT NULL DEFAULT 0,
                    cached_tokens    INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens     INTEGER NOT NULL DEFAULT 0,
                    cost_usd         NUMERIC(14, 6),
                    duration_ms      INTEGER,
                    steps            JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            ))
            conn.execute(text("CREATE INDEX IF NOT EXISTS token_usage_created_idx ON token_usage (created_at DESC)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS token_usage_user_idx ON token_usage (user_id, created_at)"))
            for statement in client_wall_ddl(ctx.client_id, ("token_usage",)):
                conn.execute(text(statement))
        _ready.add(key)


def save(
    ctx: TenantContext,
    collector: Collector,
    *,
    query: str,
    session_id: Optional[str],
    source: str = "chat",
    status: str = "ok",
    duration_ms: Optional[int] = None,
) -> Optional[str]:
    """Store one turn's usage. Turns with no model call are skipped. Never raises."""
    if not collector.calls:
        return None
    try:
        ensure_table(ctx)
        totals = collector.totals()
        models = sorted({c["model"] for c in collector.calls if c.get("model")})
        row_id = new_id("tok")
        with workspace_connection(ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO token_usage (id, user_id, session_id, source, query, status, model, calls, "
                    "input_tokens, output_tokens, cached_tokens, reasoning_tokens, total_tokens, cost_usd, "
                    "duration_ms, steps) VALUES (:id, :u, :s, :src, :q, :st, :m, :n, :i, :o, :c, :r, :t, "
                    ":cost, :d, CAST(:steps AS JSONB))"
                ),
                {
                    "id": row_id,
                    "u": ctx.user_id,
                    "s": session_id,
                    "src": source,
                    "q": redact_text(query or "")[:QUERY_CHARS],
                    "st": status,
                    "m": ", ".join(models),
                    "n": len(collector.calls),
                    "i": totals["input_tokens"],
                    "o": totals["output_tokens"],
                    "c": totals["cached_tokens"],
                    "r": totals["reasoning_tokens"],
                    "t": totals["total_tokens"],
                    "cost": totals["cost_usd"],
                    "d": duration_ms,
                    "steps": json.dumps(collector.calls),
                },
            )
        return row_id
    except Exception:  # noqa: BLE001
        log.exception("usage.save failed")
        return None


def _num(value: Any) -> Any:
    return float(value) if value is not None and not isinstance(value, (int, float)) else value


def report(ctx: TenantContext, *, days: int = 30, user_id: Optional[str] = None, limit: int = 200) -> dict[str, Any]:
    """Per-query rows plus totals by person, model and day, for one workspace."""
    ensure_table(ctx)
    since = utcnow() - timedelta(days=days)
    where = "created_at >= :since" + (" AND user_id = :u" if user_id else "")
    params: dict[str, Any] = {"since": since, "u": user_id, "lim": limit}
    sums = ("COUNT(*) AS queries, COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cached_tokens),0) AS cached_tokens, "
            "COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens, COALESCE(SUM(total_tokens),0) AS total_tokens, "
            "SUM(cost_usd) AS cost_usd")
    with readonly_connection(ctx) as conn:
        totals = dict(conn.execute(text(f"SELECT {sums} FROM token_usage WHERE {where}"), params).one()._mapping)
        by_user = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT user_id, {sums} FROM token_usage WHERE {where} GROUP BY user_id ORDER BY total_tokens DESC"
        ), params)]
        by_model = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT model, {sums} FROM token_usage WHERE {where} GROUP BY model ORDER BY total_tokens DESC"
        ), params)]
        by_day = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day, {sums} FROM token_usage "
            f"WHERE {where} GROUP BY 1 ORDER BY 1"
        ), params)]
        items = [dict(r._mapping) for r in conn.execute(text(
            f"SELECT id, user_id, session_id, source, query, status, model, calls, input_tokens, output_tokens, "
            f"cached_tokens, reasoning_tokens, total_tokens, cost_usd, duration_ms, steps, created_at "
            f"FROM token_usage WHERE {where} ORDER BY created_at DESC LIMIT :lim"
        ), params)]
    for group in (totals, *by_user, *by_model, *by_day, *items):
        if "cost_usd" in group:
            group["cost_usd"] = _num(group["cost_usd"])
    return {
        "days": days,
        "totals": totals,
        "by_user": by_user,
        "by_model": by_model,
        "by_day": by_day,
        "items": items,
        "cost_note": "Estimated from list prices per model; check your OpenAI bill for exact charges.",
    }
