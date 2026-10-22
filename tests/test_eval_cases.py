"""Every eval case is well formed, and its expected gates and hidden tools are what the policy check gives."""

from __future__ import annotations

import pytest

from evals.check_gates import problems
from evals.run import load_cases

CASES = load_cases()
KNOWN_FIELDS = {
    "id", "query", "user", "fixtures", "workspace", "history", "expected_domains", "expected_tool_calls",
    "expected_args", "expected_gates", "expected_outcome", "expected_hidden", "forbidden_tool_calls",
    "forbidden_args", "expected_reply_contains", "expected_local_times", "expected_local_dates",
}
OUTCOMES = {"run", "approval", "draft", "blocked", "clarify", "not_permitted", "answer", "handed_off"}


def test_case_ids_are_unique_and_fields_are_known():
    from bizos.agents.routing import DOMAINS, MODEL_TOOLS

    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    for case in CASES:
        assert set(case) <= KNOWN_FIELDS, (case["id"], set(case) - KNOWN_FIELDS)
        outcomes = case["expected_outcome"] if isinstance(case["expected_outcome"], list) else [case["expected_outcome"]]
        assert set(outcomes) <= OUTCOMES, case["id"]
        named = [str(t).split(" x")[0] for t in case.get("expected_tool_calls") or []]
        named += list(case.get("expected_gates") or {}) + list(case.get("forbidden_tool_calls") or [])
        named += list(case.get("expected_hidden") or []) + list(case.get("expected_args") or {})
        assert set(named) <= MODEL_TOOLS, (case["id"], set(named) - MODEL_TOOLS)
        assert set(case.get("expected_domains") or []) <= set(DOMAINS), case["id"]
        assert {r.upper() for r in case["user"]["roles"]} <= {"VIEWER", "DRAFTER", "APPROVER", "OPERATOR", "ADMIN"}


@pytest.mark.parametrize("case", [c for c in CASES if c.get("expected_gates") or c.get("expected_hidden")],
                         ids=lambda c: c["id"])
def test_expected_gates_come_from_the_policy_check(client_scratch, case):
    assert problems(client_scratch, case) == []
