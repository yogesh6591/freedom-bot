"""The ToolResult adapter maps every policy outcome without changing the tool."""

from __future__ import annotations

from freedombot.agents.tool_result import from_outcome
from freedombot.policy.models import PolicyDecision
from freedombot.tools.base import ToolOutcome
from freedombot.types import DataClassification, ExecutionMode, PolicyEffect, RiskLevel


def _outcome(effect, ok=True, message="line one\nline two", data=None, action_id=None):
    decision = PolicyDecision(
        effect=effect, tool="t", reason="r", rule="R", mode=ExecutionMode.WAIT_FOR_APPROVAL,
        risk=RiskLevel.LOW, classification=DataClassification.INTERNAL,
    )
    return ToolOutcome("t", effect, ok, message, decision, data=data, action_id=action_id)


def test_success_carries_the_record_id_and_first_line():
    r = from_outcome(_outcome(PolicyEffect.ALLOW, data={"id": "D-2231", "stage": "won"}))
    assert (r.status, r.summary, r.ref, r.pending_kind) == ("success", "line one", "D-2231", None)


def test_a_failed_run_is_an_error():
    r = from_outcome(_outcome(PolicyEffect.ALLOW, ok=False, message="No contact matching 'x'"))
    assert (r.status, r.summary, r.ref) == ("error", "No contact matching 'x'", None)


def test_approval_and_draft_are_pending_with_the_action_id():
    approval = from_outcome(_outcome(PolicyEffect.REQUIRE_APPROVAL, action_id="act_118"))
    draft = from_outcome(_outcome(PolicyEffect.DRAFT_ONLY, action_id="act_119", data={"draft_id": "dr_1"}))
    assert (approval.status, approval.pending_kind, approval.ref) == ("pending", "approval", "act_118")
    assert (draft.status, draft.pending_kind, draft.ref, draft.data) == ("pending", "draft", "act_119", {"draft_id": "dr_1"})


def test_deny_is_an_error_with_no_data():
    r = from_outcome(_outcome(PolicyEffect.DENY, ok=False, message="Not permitted", data={"x": 1}))
    assert (r.status, r.summary, r.data, r.ref) == ("error", "Not permitted", None, None)
