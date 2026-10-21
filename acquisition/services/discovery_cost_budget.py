"""Thread-safe run cost budget with in-flight reservation.

Never treat unknown/unpriced calls as $0. Reservations prevent launching more
parallel paid calls than remaining budget can cover.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any


class DiscoveryCostBudget:
    """Tracks known actual spend + reserved estimates for in-flight / queued calls."""

    def __init__(self, max_actual_usd: float | None) -> None:
        self.max_actual_usd = (
            float(max_actual_usd) if max_actual_usd is not None and float(max_actual_usd) > 0 else None
        )
        self._lock = threading.Lock()
        self.actual_cost_usd = 0.0
        self.reserved_usd = 0.0
        self._reservations: dict[str, float] = {}
        self.in_flight = 0
        self.peak_in_flight = 0
        self.blocked_launches = 0
        self.stop_reason: str | None = None
        self.pricing_unknown = False
        self.cost_complete = True

    def remaining_budget(self) -> float | None:
        """Budget left for NEW reservations (actual + already reserved)."""
        if self.max_actual_usd is None:
            return None
        with self._lock:
            return float(self.max_actual_usd) - self.actual_cost_usd - self.reserved_usd

    def max_new_calls(self, estimated_per_call_usd: float) -> int:
        """How many additional calls fit under remaining budget (floor division)."""
        with self._lock:
            if self.pricing_unknown:
                return 0
            if self.max_actual_usd is None:
                return 10**9
            est = max(0.000001, float(estimated_per_call_usd or 0.0))
            remaining = float(self.max_actual_usd) - self.actual_cost_usd - self.reserved_usd
            if remaining <= 0:
                return 0
            return int(remaining // est)

    def try_reserve(self, estimated_usd: float, *, reservation_id: str | None = None) -> str | None:
        """Reserve estimated cost for one upcoming paid call. Returns reservation id or None."""
        rid = reservation_id or str(uuid.uuid4())
        est = max(0.0, float(estimated_usd or 0.0))
        with self._lock:
            if self.pricing_unknown:
                self.blocked_launches += 1
                self.stop_reason = "pricing_unknown"
                return None
            if self.max_actual_usd is None:
                self._reservations[rid] = est
                self.reserved_usd += est
                self.in_flight += 1
                self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
                return rid
            remaining = float(self.max_actual_usd) - self.actual_cost_usd - self.reserved_usd
            if est <= remaining + 1e-12:
                self._reservations[rid] = est
                self.reserved_usd += est
                self.in_flight += 1
                self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
                return rid
            self.blocked_launches += 1
            self.stop_reason = "cost_budget_reached"
            return None

    def settle(
        self,
        reservation_id: str | None,
        actual_usd: float | None,
        *,
        cost_complete: bool = True,
        pricing_status: str | None = None,
    ) -> None:
        """Release reservation and apply actual cost (or mark pricing unknown)."""
        with self._lock:
            if reservation_id and reservation_id in self._reservations:
                reserved = float(self._reservations.pop(reservation_id))
                self.reserved_usd = max(0.0, self.reserved_usd - reserved)
                self.in_flight = max(0, self.in_flight - 1)
            if pricing_status == "unknown_long_context_rate" or not cost_complete:
                self.pricing_unknown = True
                self.cost_complete = False
                self.stop_reason = "pricing_unknown"
                return
            self.actual_cost_usd += max(0.0, float(actual_usd or 0.0))

    def release_if_open(self, reservation_id: str | None) -> None:
        """Drop an unused reservation without recording actual spend."""
        if not reservation_id:
            return
        with self._lock:
            if reservation_id in self._reservations:
                reserved = float(self._reservations.pop(reservation_id))
                self.reserved_usd = max(0.0, self.reserved_usd - reserved)
                self.in_flight = max(0, self.in_flight - 1)

    def mark_pricing_unknown(self, *, detail: str | None = None) -> None:
        del detail
        with self._lock:
            self.pricing_unknown = True
            self.cost_complete = False
            self.stop_reason = "pricing_unknown"

    def can_launch(self, estimated_next_usd: float) -> bool:
        """True if one more call could be reserved (does not reserve)."""
        return self.max_new_calls(estimated_next_usd) >= 1

    def add_actual(self, cost_usd: float) -> None:
        """Add known complete cost with no prior reservation (e.g. Stage A already finished)."""
        with self._lock:
            if self.pricing_unknown:
                return
            self.actual_cost_usd += max(0.0, float(cost_usd or 0.0))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            remaining = None
            if self.max_actual_usd is not None:
                remaining = round(
                    float(self.max_actual_usd) - self.actual_cost_usd - self.reserved_usd, 6
                )
            return {
                "budget_limit": self.max_actual_usd,
                "actual_cost": round(self.actual_cost_usd, 6),
                "reserved_usd": round(self.reserved_usd, 6),
                "remaining_budget": remaining,
                "in_flight_calls_at_stop": self.in_flight,
                "peak_in_flight": self.peak_in_flight,
                "blocked_launches": self.blocked_launches,
                "budget_stop_reason": self.stop_reason,
                "pricing_unknown": self.pricing_unknown,
                "cost_complete": self.cost_complete,
                "note": (
                    "In-flight reservations hold estimated cost until each call settles "
                    "with actual usage. Unknown long-context pricing blocks new paid calls."
                ),
            }
