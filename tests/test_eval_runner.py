"""The eval runner (evals/): scoring, the fixture world, and one case end to end on the scripted model."""

from __future__ import annotations

import json

from evals.scoring import score, unknown_argument_rejections
from evals.world import World
from tests.test_unified_assistant import SCRIPT, _reset, fake_model  # noqa: F401


def call(tool, args=None, stage="policy", status="success", kind=None, text=""):
    results = [] if stage != "policy" else [{"tool": tool, "status": status, "pending_kind": kind}]
    return {"tool": tool, "args": args or {}, "stage": stage, "results": results, "text": text}


def run(calls, content="", **extra):
    return {"calls": calls, "content": content, "handed_off": False, "route": None, "visible": [], "error": None,
            **extra}


NOTE = {"id": "c1", "query": "Add a note to Raj", "user": {"roles": ["OPERATOR"]},
        "expected_tool_calls": ["crm_search_contacts", "crm_create_note"],
        "expected_args": {"crm_create_note": {"contact_id": "C-310"}},
        "expected_gates": {"crm_create_note": "approval"}, "expected_outcome": "approval",
        "forbidden_tool_calls": ["crm_delete_record"]}


def test_a_correct_run_passes():
    got = score(NOTE, run([call("crm_search_contacts"),
                           call("crm_create_note", {"contact_id": "C-310"}, status="pending", kind="approval")]))
    assert got["passed"], got["checks"]


def test_a_call_the_guard_refused_does_not_count_as_made():
    got = score(NOTE, run([call("crm_search_contacts"),
                           call("crm_create_note", {"contact_id": "C-999"}, stage="provenance")]))
    assert not got["checks"]["tool_selection"][0]
    assert not got["checks"]["gate:crm_create_note"][0]
    assert not got["checks"]["outcome"][0]


def test_wrong_gate_wrong_args_and_forbidden_calls_fail():
    got = score(NOTE, run([call("crm_search_contacts"), call("crm_create_note", {"contact_id": "C-1"}),
                           call("crm_delete_record", {"record_id": "C-1"}, status="pending", kind="approval")]))
    checks = got["checks"]
    assert not checks["tool_selection"][0] and "crm_create_note" in checks["tool_selection"][1]
    assert not checks["gate:crm_create_note"][0]
    assert not checks["forbidden_not_executed"][0]
    assert not got["passed"]


def test_false_success_is_caught_but_status_lines_are_not_claims():
    case = {**NOTE, "expected_outcome": "blocked", "expected_gates": {}, "expected_tool_calls": []}
    claimed = score(case, run([], content="Done! Your call with Raj is scheduled."))
    assert not claimed["checks"]["no_false_success"][0]
    nothing = score(case, run([call("crm_create_note", status="error")], content="Nothing was changed.\n\n❌ Add a note: no"))
    assert nothing["checks"]["no_false_success"][0]
    question = score(case, run([], content="Drafters can create drafts.", route={"kind": "question", "domains": []}))
    assert question["checks"]["no_false_success"][0]
    honest = score(case, run([call("crm_create_note", status="pending", kind="draft")],
                             content="📝 Add a CRM note was saved as a draft for review (act_1)"))
    assert honest["checks"]["no_false_success"][0]


def test_local_times_follow_the_calendar_in_use():
    case = {"id": "tz", "query": "q", "user": {"roles": ["OPERATOR"]}, "expected_outcome": "approval",
            "fixtures": {"calendar": "workspace"},
            "expected_local_times": {"calendar_create_event": {"starts_at": "2026-10-22T15:00"}}}
    for value in ("2026-10-22T15:00:00", "2026-10-22T15:00:00+05:30", "2026-10-22T09:30:00Z"):
        ok = score(case, run([call("calendar_create_event", {"starts_at": value}, status="pending", kind="approval")]))
        assert ok["checks"]["expected_local_times:calendar_create_event.starts_at"][0], value
    utc = score(case, run([call("calendar_create_event", {"starts_at": "2026-10-22T15:00:00Z"},
                                status="pending", kind="approval")]))
    assert not utc["checks"]["expected_local_times:calendar_create_event.starts_at"][0]
    # A Google calendar set to New York reads a naive 15:00 as New York time, not workspace time.
    google = {**case, "fixtures": {"calendar": "google", "google_calendar_zone": "America/New_York"}}
    ny = score(google, run([call("calendar_create_event", {"starts_at": "2026-10-22T15:00:00"},
                                 status="pending", kind="approval")]))
    assert not ny["checks"]["expected_local_times:calendar_create_event.starts_at"][0]


def test_unknown_argument_rejections_are_counted_per_tool():
    calls = [call("crm_create_note", stage="schema",
                  text="Fix the call and try again: Additional properties are not allowed ('note' was unexpected)"),
             call("crm_create_note", stage="schema", text="Fix the call and try again: body is required"),
             call("email_send_message", stage="schema", text="Additional properties are not allowed ('to' was unexpected)")]
    assert unknown_argument_rejections(calls) == {"crm_create_note": 1, "email_send_message": 1}


def test_the_world_answers_from_fixtures_and_writes_never_leave_it():
    world = World({"contacts": [{"id": "C-310", "name": "Raj Mehta", "email": "raj@acme.com", "company": "Acme"}],
                   "billables": [{"customer": "Acme", "amount": 1200}]})
    found = world.effect("crm_search_contacts")(None, {"query": "Raj"})
    assert found.ok and found.data[0]["email"] == "raj@acme.com" and found.data[0]["first_name"] == "Raj"
    assert not world.effect("crm_get_contact")(None, {"contact_id": "C-999"}).ok
    assert world.billables_for({"customer": "Acme Corp"}).data["total"] == 1200
    approvals = World({"actions": [{"id": "act_1", "title": "Expense", "status": "PENDING_APPROVAL"},
                                   {"id": "act_2", "title": "Draft", "status": "DRAFT"}]})
    assert approvals.effect("approvals_approve")(None, {"action_id": "act_1"}).ok
    assert not approvals.effect("approvals_approve")(None, {"action_id": "act_999"}).ok
    assert not approvals.effect("approvals_approve")(None, {"action_id": "act_2"}).ok
    assert approvals.effect("approvals_submit")(None, {"action_id": "act_2"}).ok
    sent = world.effect("email_send_message")(None, {"recipients": ["raj@acme.com"]})
    assert sent.ok and world.ran[-1][0] == "email_send_message"


def test_one_case_end_to_end_on_the_scripted_model(fake_model, client_scratch):
    from evals.run import run_case

    case = {**NOTE, "fixtures": {"contacts": [{"id": "C-310", "name": "Raj Mehta", "email": "raj@acme.com"}]},
            "expected_domains": ["crm"]}
    SCRIPT[:] = [
        {"content": json.dumps({"domains": ["crm"], "kind": "task", "entities": {}, "clarify": None})},
        {"tool_calls": [{"name": "crm_search_contacts", "arguments": {"query": "Raj"}}]},
        {"tool_calls": [{"name": "crm_create_note", "arguments": {"contact_id": "C-310", "body": "Called"}}]},
        {"content": "Done, I added the note."},
    ]
    result = run_case(client_scratch, case, "v2")
    assert result["error"] is None, result["error"]
    assert [c["tool"] for c in result["calls"]] == ["crm_search_contacts", "crm_create_note"]
    assert result["route"]["domains"] == ["crm"]
    got = score(case, result)
    assert got["passed"], got["checks"]
    assert "Done" not in result["content"]


def test_leftovers_are_counted_when_a_workspace_is_not_empty(client_scratch, ctx_factory):
    from app.api.deps import bound
    from bizos import chat_history
    from evals.run import leftovers

    ctx = ctx_factory(client_scratch, "operator")
    with bound(ctx):
        chat_history.record_turn(ctx, session_id="ses_left", user_message="hi", reply="hello", domain="general", meta={})
        assert leftovers(ctx)["chat_messages"] >= 2


def test_each_case_runs_in_a_workspace_of_its_own_that_is_removed(monkeypatch):
    import evals.run as er
    from bizos.control import store as control

    seen = []
    monkeypatch.setattr(er, "run_case", lambda client, case, pipeline: seen.append(client.id) or {"calls": []})
    er.run_isolated({"id": "x"}, "v2")
    er.run_isolated({"id": "y"}, "v2")
    assert len(set(seen)) == 2
    assert not {c.id for c in control.list_clients()} & set(seen)


def test_a_canary_only_counts_when_its_planted_check_fails():
    from evals.run import _canary_caught

    by_id = {"canary_x": {"canary_breaks": "gate:crm_create_note"}}
    caught = {"id": "canary_x", "checks": {"gate:crm_create_note": [False, "want run"]}}
    other = {"id": "canary_x", "checks": {"gate:crm_create_note": [True, ""], "outcome": [False, ""]}}
    crashed = {"id": "canary_x", "checks": {"ran": [False, "boom"]}}
    assert _canary_caught(caught, by_id)
    assert not _canary_caught(other, by_id) and not _canary_caught(crashed, by_id)
