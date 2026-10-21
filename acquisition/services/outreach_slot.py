"""Outreach-ready ProspectSlot types for agentic acquisition."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


SLOT_PENDING = "pending"
SLOT_RESEARCHING = "researching"
SLOT_READY = "ready"
SLOT_EXHAUSTED = "exhausted"
SLOT_TECHNICAL_FAILURE = "technical_failure"
SLOT_COLLISION_RETRY = "collision_retry"


@dataclass
class ProspectSlot:
    """One concurrent unit whose job is a single backend-validated outreach-ready prospect."""

    slot_id: str
    seed_company: dict[str, Any] | None
    status: str = SLOT_PENDING
    candidate_attempts: int = 0
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    final_prospect: dict[str, Any] | None = None
    failure_reason: str | None = None
    ai_recommended_ready: bool = False
    cost_usd: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_seed(cls, index: int, seed: dict[str, Any] | None) -> ProspectSlot:
        return cls(
            slot_id=f"slot-{index + 1}-{uuid.uuid4().hex[:8]}",
            seed_company=seed,
            status=SLOT_PENDING,
        )

    def as_trace(self) -> dict[str, Any]:
        seed = self.seed_company or {}
        final = self.final_prospect or {}
        return {
            "slot_id": self.slot_id,
            "status": self.status,
            "seed_company": seed.get("company_name"),
            "seed_domain": seed.get("normalized_domain"),
            "candidate_attempts": self.candidate_attempts,
            "rejected_candidates": self.rejected_candidates[:8],
            "final_company": final.get("company_name"),
            "final_domain": final.get("normalized_domain"),
            "failure_reason": self.failure_reason,
            "ai_recommended_ready": self.ai_recommended_ready,
        }
