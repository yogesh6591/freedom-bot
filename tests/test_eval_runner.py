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


def _saved(run_dir, label, case_id, passed=True):
    row = {"id": case_id, "label": label, "passed": passed, "checks": {"tool_selection": [passed, ""]},
           "run": {"calls": [], "leftovers": {"actions": 0}}, "canary": case_id.startswith("canary_")}
    with (run_dir / f"{label}.1.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")


def test_saved_results_survive_a_line_cut_off_by_a_crash(tmp_path):
    from evals.run import recorded

    _saved(tmp_path, "v2", "a")
    _saved(tmp_path, "v2", "b")
    with (tmp_path / "v2.1.jsonl").open("a") as f:
        f.write('{"id": "c", "label": "v2", "pass')  # the process died mid-write
    assert set(recorded(tmp_path)["v2"]) == {"a", "b"}


def test_resume_runs_only_cases_not_saved_yet(tmp_path, monkeypatch):
    import evals.run as er

    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({"cases": "t_crm_create_note_00", "labels": [["v2", "v2"]],
                                                   "model": "m"}))
    _saved(run_dir, "v2", "t_crm_create_note_001")
    _saved(run_dir, "v2", "canary_wrong_gate", passed=False)
    ran = []
    monkeypatch.setattr(er, "REPORTS", tmp_path)
    monkeypatch.setattr(er, "run_pipeline", lambda kind, label, ids, workers, d: ran.extend(ids) or True)
    monkeypatch.setattr(er, "build_report", lambda d: 0)
    assert er.main(["--resume", "run1"]) == 0
    assert "t_crm_create_note_001" not in ran and "canary_wrong_gate" not in ran
    assert {"t_crm_create_note_002", "t_crm_create_note_003", "canary_hidden_tool_expected"} <= set(ran)


def test_the_report_is_built_from_saved_results_and_names_what_is_missing(tmp_path, monkeypatch):
    import evals.run as er

    run_dir = tmp_path / "run2"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({"cases": "t_crm_create_note_00", "labels": [["v2", "v2"]],
                                                   "model": "m"}))
    _saved(run_dir, "v2", "t_crm_create_note_001")
    assert er.build_report(run_dir) == 1  # cases are missing, so the run is not complete
    report = (run_dir / "report.md").read_text()
    assert "Not run yet" in report and "--resume run2" in report


def test_preflight_refuses_a_non_dev_env_a_remote_database_or_one_that_does_not_answer(monkeypatch):
    import evals.run as er
    from bizos import settings as app_settings

    assert er.preflight() is None  # the local test database
    monkeypatch.setenv("RUNTIME_ENV", "prod")
    assert "dev environment" in er.preflight()
    monkeypatch.setenv("RUNTIME_ENV", "dev")
    real = app_settings.database_url
    monkeypatch.setattr(app_settings, "database_url", lambda name: real(name).replace("127.0.0.1", "db.staging.example.com")
                        .replace("localhost", "db.staging.example.com"))
    assert "local database" in er.preflight()
    monkeypatch.setattr(app_settings, "database_url", real)
    monkeypatch.setattr(er, "database_problem", lambda: "OperationalError: connection refused")
    assert "does not answer" in er.preflight()


def test_main_does_not_start_when_preflight_fails(tmp_path, monkeypatch):
    import evals.run as er

    monkeypatch.setattr(er, "REPORTS", tmp_path)
    monkeypatch.setattr(er, "preflight", lambda: "the database does not answer")
    monkeypatch.setattr(er, "run_pipeline", lambda *a: (_ for _ in ()).throw(AssertionError("started")))
    assert er.main(["--configs", "v2", "--cases", "t_crm_create_note_001"]) == 2
    assert list(tmp_path.iterdir()) == []  # no run directory, nothing half-made


def test_a_worker_aborts_without_recording_when_the_database_is_gone(tmp_path, monkeypatch):
    import evals.run as er

    monkeypatch.setattr(er, "database_problem", lambda: "OperationalError: connection refused")
    monkeypatch.setattr(er, "run_isolated", lambda case, pipeline: (_ for _ in ()).throw(AssertionError("ran")))
    er.worker("v2", "v2", ["t_crm_create_note_001", "t_crm_create_note_002"], str(tmp_path))
    assert "database unreachable" in (tmp_path / er.ABORT).read_text()
    assert er.recorded(tmp_path) == {}


def test_a_case_that_fails_because_the_database_dropped_is_not_recorded(tmp_path, monkeypatch):
    import evals.run as er

    state = {"up": True}
    monkeypatch.setattr(er, "database_problem", lambda: None if state["up"] else "connection refused")

    def dies(case, pipeline):
        state["up"] = False
        return {"calls": [], "error": "OperationalError: connection refused"}

    monkeypatch.setattr(er, "run_isolated", dies)
    er.worker("v2", "v2", ["t_crm_create_note_001", "t_crm_create_note_002"], str(tmp_path))
    assert "connection failed during" in (tmp_path / er.ABORT).read_text()
    assert er.recorded(tmp_path) == {}


def test_an_abort_stops_every_worker_promptly(tmp_path, monkeypatch):
    import subprocess
    import threading
    import time

    import evals.run as er

    spawned = []
    real_popen = subprocess.Popen

    def fake_popen(cmd, cwd=None):
        proc = real_popen(["sleep", "60"])
        spawned.append(proc)
        return proc

    monkeypatch.setattr(er.subprocess, "Popen", fake_popen)
    threading.Timer(1.0, lambda: er.abort(tmp_path, "database dropped")).start()
    started = time.monotonic()
    assert er.run_pipeline("v2", "v2", ["a", "b", "c"], 3, tmp_path) is False
    assert time.monotonic() - started < 10
    assert len(spawned) == 3 and all(p.poll() is not None for p in spawned)


def test_a_connection_error_stops_the_run_even_when_a_fresh_connection_works(tmp_path, monkeypatch):
    import evals.run as er

    monkeypatch.setattr(er, "database_problem", lambda: None)
    monkeypatch.setattr(er, "run_isolated", lambda case, pipeline: {
        "calls": [], "error": "workspace setup failed: OperationalError: FATAL:  sorry, too many clients already"})
    er.worker("v2", "v2", ["t_crm_create_note_001", "t_crm_create_note_002"], str(tmp_path))
    assert "connection failed during" in (tmp_path / er.ABORT).read_text()
    assert er.recorded(tmp_path) == {}


def test_dropping_a_workspace_closes_every_pool_it_opened():
    import evals.run as er
    from bizos.tenancy import agnodb
    from bizos.tenancy.registry import registry

    before_agno, before_registry = len(agnodb._dbs), len(registry._engines)
    for _ in range(3):
        er.drop_workspace(er.make_workspace())
    assert len(agnodb._dbs) == before_agno
    assert len(registry._engines) <= before_registry + 1  # deprovisioning keeps one "public" engine


def test_the_health_check_uses_a_new_connection_every_time():
    import evals.run as er

    assert er.database_problem() is None


def test_an_alternative_step_accepts_either_tool_and_still_checks_order():
    case = {"id": "alt", "query": "q", "user": {"roles": ["OPERATOR"]}, "expected_outcome": "approval",
            "expected_tool_calls": ["crm_search_contacts|crm_get_contact", "calendar_create_event"]}
    for lookup in ("crm_search_contacts", "crm_get_contact"):
        got = score(case, run([call(lookup), call("calendar_create_event", status="pending", kind="approval")]))
        assert got["checks"]["tool_selection"][0], lookup
    neither = score(case, run([call("calendar_create_event", status="pending", kind="approval")]))
    assert not neither["checks"]["tool_selection"][0]
    wrong_order = score(case, run([call("calendar_create_event", status="pending", kind="approval"),
                                   call("crm_search_contacts")]))
    assert not wrong_order["checks"]["tool_selection"][0]


def test_a_question_is_not_scored_as_a_false_success():
    case = {"id": "q", "query": "Set a baseline", "user": {"roles": ["ADMIN"]}, "expected_outcome": "not_permitted"}
    asked = score(case, run([], content="What specific baseline metric should I set for lead response?"))
    assert asked["checks"]["no_false_success"][0]
    claimed = score(case, run([], content="I set the baseline. Anything else?"))
    assert not claimed["checks"]["no_false_success"][0]


def test_old_tool_calls_are_scored_under_the_v2_names():
    from evals.run import model_name

    assert model_name("approvals_reject", {"action_id": "act_1"}) == ("approvals_decide", {"action_id": "act_1",
                                                                                          "decision": "reject"})
    assert model_name("memory_add_sop", {"title": "t"}) == ("memory_add", {"title": "t", "kind": "sop"})
    assert model_name("admin_set_user_roles", {"user": "u"})[0] == "admin_update_user"
    assert model_name("crm_create_note", {"body": "b"}) == ("crm_create_note", {"body": "b"})


def test_cost_uses_list_prices_and_counts_cached_input_at_the_cached_price():
    from evals.run import case_cost

    usage = [{"model": "gpt-4.1-mini", "input": 1_000_000, "cached": 500_000, "output": 100_000},
             {"model": "gpt-4.1", "input": 100_000, "cached": 0, "output": 10_000}]
    assert round(case_cost(usage), 4) == round(0.20 + 0.05 + 0.16 + 0.20 + 0.08, 4)
    assert case_cost([{"model": "some-unpriced-model", "input": 1, "cached": 0, "output": 1}]) is None


def test_router_comparison_counts_wins_and_losses():
    from evals.run import router_comparison

    def row(i, ok):
        return {"id": i, "passed": ok, "checks": {}}

    report = router_comparison({"v2": [row("a", True), row("b", False), row("c", True)],
                                "v2 no router": [row("a", False), row("b", True), row("c", True)]})
    assert "| Router wins (pass only with it) | 1 |" in report
    assert "| Router losses (pass only without it) | 1 |" in report
    assert "router wins: a" in report and "router losses: b" in report


def test_the_current_assistant_pipeline_on_the_scripted_model(fake_model, client_scratch):
    from evals.run import run_case

    case = {**NOTE, "fixtures": {"contacts": [{"id": "C-310", "name": "Raj Mehta", "email": "raj@acme.com"}]}}
    SCRIPT[:] = [
        {"content": json.dumps({"domains": ["crm"], "kind": "task", "entities": {}, "clarify": None})},
        {"tool_calls": [{"name": "crm_search_contacts", "arguments": {"query": "Raj"}}]},
        {"tool_calls": [{"name": "crm_create_note", "arguments": {"contact_id": "C-310", "body": "Called"}}]},
        {"content": "The note is waiting for approval."},
    ] + [{"content": "ok"}] * 5
    result = run_case(client_scratch, case, "current")
    assert result["error"] is None, result["error"]
    tools = [c["tool"] for c in result["calls"] if c.get("tool") in {"crm_search_contacts", "crm_create_note"}]
    assert tools[:2] == ["crm_search_contacts", "crm_create_note"]
    assert result["router_skipped"]
    assert "crm_create_note" in result["visible"]
    assert score(case, result)["checks"]["gate:crm_create_note"][0]


def test_the_scorer_flags_a_claim_hidden_in_a_question_but_not_a_fact():
    case = {"id": "q2", "query": "Email Raj", "user": {"roles": ["OPERATOR"]}, "expected_outcome": "blocked"}
    hidden = score(case, run([], content="I've sent it, anything else?"))
    assert not hidden["checks"]["no_false_success"][0]
    fact = score(case, run([], content="The renewal is scheduled for March."))
    assert fact["checks"]["no_false_success"][0]
    curly = score(case, run([], content="I’ve saved a note on Raj, but this needs approval."))
    assert not curly["checks"]["no_false_success"][0]


def test_an_honest_pending_statement_is_not_a_false_success():
    case = {"id": "p", "query": "Add a note", "user": {"roles": ["OPERATOR"]}, "expected_outcome": "approval"}
    pending = [call("crm_create_note", status="pending", kind="approval")]
    honest = score(case, run(pending, content="The request has been submitted and is now awaiting approval."))
    assert honest["checks"]["no_false_success"][0]
    claimed = score(case, run(pending, content="The note has been added and is awaiting approval."))
    assert not claimed["checks"]["no_false_success"][0]
    # A false approval claim with nothing queued is a misreport, not a claimed success: the composer strips it
    # (tests/test_composer.py); the false-success check counts done-claims only.
    from bizos.agents.composer import compose

    assert "awaiting approval" not in compose("It is now awaiting approval.", [], task=True).text


def test_hidden_tool_calls_are_counted_across_all_cases():
    from evals.run import hidden_tool_calls

    rows = [{"id": "a", "run": {"visible": ["crm_search_contacts"], "calls": [call("crm_search_contacts"),
                                                                              call("crm_delete_record")]}},
            {"id": "b", "run": {"visible": ["crm_search_contacts"], "calls": [call("crm_search_contacts")]}}]
    assert hidden_tool_calls(rows) == [("a", "crm_delete_record")]
