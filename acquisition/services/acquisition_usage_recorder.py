"""Persist Acquisition OpenAI usage rows and run cost rollups."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from acquisition.integrations.discovery.openai_discovery_client import DiscoveryModelResult
from acquisition.models.acquisition_ai_usage import AcquisitionAIUsage

logger = logging.getLogger(__name__)


class AcquisitionUsageRecorder:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_call(
        self,
        answer: DiscoveryModelResult,
        *,
        run_id: uuid.UUID | str | None,
        call_type: str,
        attempt_number: int = 1,
        slot_id: str | None = None,
        company: str | None = None,
        domain: str | None = None,
    ) -> AcquisitionAIUsage | None:
        try:
            rid: uuid.UUID | None = None
            if isinstance(run_id, uuid.UUID):
                rid = run_id
            elif run_id:
                rid = uuid.UUID(str(run_id))
        except Exception:  # noqa: BLE001
            rid = None

        status = "ok" if answer.data is not None and not answer.error else "error"
        cost_complete = bool(getattr(answer, "cost_complete", True))
        pricing_status = str(getattr(answer, "pricing_status", None) or (answer.usage or {}).get("pricing_status") or "ok")
        if pricing_status == "unknown_long_context_rate":
            cost_complete = False

        def _cost(value: float | None) -> float | None:
            if not cost_complete:
                return None
            if value is None:
                return None
            return float(value)

        row = AcquisitionAIUsage(
            run_id=rid,
            response_id=answer.response_id,
            call_type=call_type,
            slot_id=slot_id,
            company=company,
            domain=domain,
            attempt_number=int(attempt_number or 1),
            model=answer.model or None,
            reasoning_effort=answer.reasoning_effort or None,
            input_tokens=int(answer.input_tokens or 0),
            cached_input_tokens=int(answer.cached_input_tokens or 0),
            cache_write_tokens=int(answer.cache_write_tokens or 0),
            output_tokens=int(answer.output_tokens or 0),
            reasoning_tokens=int(answer.reasoning_tokens or 0),
            total_tokens=int(answer.total_tokens or 0),
            web_search_calls=int(answer.search_calls or 0),
            input_cost_usd=_cost(answer.input_cost_usd),
            output_cost_usd=_cost(answer.output_cost_usd),
            web_search_cost_usd=_cost(answer.web_search_cost_usd),
            total_cost_usd=_cost(answer.cost_usd),
            pricing_status=pricing_status,
            cost_complete=cost_complete,
            status=status,
            error_type=answer.error,
            detail_json={
                "http_status": answer.http_status,
                "usage": answer.usage or {},
                "pricing_status": pricing_status,
                "cost_complete": cost_complete,
            },
        )
        try:
            from acquisition.db.session import SessionLocal

            with SessionLocal() as session:
                session.add(row)
                session.commit()
                session.refresh(row)
            return row
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist acquisition_ai_usage")
            return None

    def run_totals(self, run_id: uuid.UUID | str | None) -> dict[str, Any]:
        if not run_id:
            return self._empty_totals()
        try:
            rid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
        except Exception:  # noqa: BLE001
            return self._empty_totals()
        rows = list(self.db.scalars(select(AcquisitionAIUsage).where(AcquisitionAIUsage.run_id == rid)))
        return self.summarize_rows(rows)

    @staticmethod
    def _empty_totals() -> dict[str, Any]:
        return {
            "api_requests": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "web_search_calls": 0,
            "input_cost_usd": 0.0,
            "output_cost_usd": 0.0,
            "web_search_cost_usd": 0.0,
            "total_cost_usd": None,
            "cost_complete": True,
            "pricing_status": "ok",
            "by_call_type": {},
            "per_call": [],
        }

    @staticmethod
    def summarize_rows(rows: list[AcquisitionAIUsage]) -> dict[str, Any]:
        totals = AcquisitionUsageRecorder._empty_totals()
        by_type: dict[str, dict[str, Any]] = {}
        known_total = 0.0
        incomplete = False
        for row in rows:
            totals["api_requests"] += 1
            if row.status == "ok":
                totals["successful_calls"] += 1
            else:
                totals["failed_calls"] += 1
            totals["input_tokens"] += int(row.input_tokens or 0)
            totals["cached_input_tokens"] += int(row.cached_input_tokens or 0)
            totals["cache_write_tokens"] += int(row.cache_write_tokens or 0)
            totals["output_tokens"] += int(row.output_tokens or 0)
            totals["reasoning_tokens"] += int(row.reasoning_tokens or 0)
            totals["web_search_calls"] += int(row.web_search_calls or 0)
            row_complete = bool(getattr(row, "cost_complete", True)) and row.total_cost_usd is not None
            if not row_complete:
                incomplete = True
            else:
                totals["input_cost_usd"] += float(row.input_cost_usd or 0.0)
                totals["output_cost_usd"] += float(row.output_cost_usd or 0.0)
                totals["web_search_cost_usd"] += float(row.web_search_cost_usd or 0.0)
                known_total += float(row.total_cost_usd or 0.0)
            bucket = by_type.setdefault(
                row.call_type,
                {"calls": 0, "total_cost_usd": None, "web_search_calls": 0, "cost_complete": True},
            )
            bucket["calls"] += 1
            bucket["web_search_calls"] += int(row.web_search_calls or 0)
            if not row_complete:
                bucket["cost_complete"] = False
                bucket["total_cost_usd"] = None
            elif bucket.get("cost_complete") is not False:
                bucket["total_cost_usd"] = round(
                    float(bucket["total_cost_usd"] or 0.0) + float(row.total_cost_usd or 0.0), 6
                )
            totals["per_call"].append(
                {
                    "call_type": row.call_type,
                    "slot_id": row.slot_id,
                    "company": row.company,
                    "attempt": row.attempt_number,
                    "total_cost_usd": None
                    if not row_complete
                    else round(float(row.total_cost_usd or 0.0), 6),
                    "pricing_status": getattr(row, "pricing_status", None) or "ok",
                    "cost_complete": row_complete,
                    "web_search_calls": int(row.web_search_calls or 0),
                    "status": row.status,
                    "error_type": row.error_type,
                }
            )
        for key in ("input_cost_usd", "output_cost_usd", "web_search_cost_usd"):
            totals[key] = round(float(totals[key]), 6)
        totals["cost_complete"] = not incomplete
        totals["pricing_status"] = "unknown_long_context_rate" if incomplete else "ok"
        totals["total_cost_usd"] = None if incomplete else round(known_total, 6)
        totals["by_call_type"] = by_type
        if incomplete:
            totals["message"] = "Actual cost unavailable — long-context pricing is not configured."
        return totals

    @staticmethod
    def cost_per_ready(total_cost_usd: float | None, ready_count: int) -> float | None:
        if total_cost_usd is None or ready_count <= 0:
            return None
        return round(float(total_cost_usd) / float(ready_count), 6)
