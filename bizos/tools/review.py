"""Human review queue tool effect (§16)."""

from __future__ import annotations

from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.review import store as review_store
from bizos.tools.base import RunScope, effect


@effect("request_human_review")
def request_human_review(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    """Raise a question instead of guessing.

    §16: when the agent cannot determine the right answer for something that
    matters, the correct behavior is to stop and ask, not to pick. This tool is
    what "stop and ask" looks like as an action the agent can actually take.
    """
    item = review_store.create(
        question=str(args.get("question", "")),
        context=str(args.get("context", "")),
        choices=list(args.get("choices") or []),
        recommended_option=args.get("recommended_option"),
        confidence=float(args["confidence"]) if args.get("confidence") is not None else None,
        agent_id=scope.agent_id,
        workflow_id=scope.workflow_id,
        workflow_run_id=scope.workflow_run_id,
        session_id=scope.session_id,
        action_id=args.get("action_id"),
        ctx=scope.ctx,
    )
    return ConnectorResult(
        ok=True,
        data={"review_id": item.id, "status": str(item.status)},
        summary=(
            f"Raised review {item.id} for a human to answer: {item.question!r}. "
            "Stop here and tell the user it is waiting on them."
        ),
    )
