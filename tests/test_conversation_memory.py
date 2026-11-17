"""Short-term conversation memory and the read-repeat bound (v2). Stubbed tools only; no model calls."""

from __future__ import annotations

from types import SimpleNamespace

from freedombot.agents import conversation
from freedombot.agents.guard import READ_REPEAT_LIMIT
from freedombot.agents.tool_result import ToolResult
from tests.test_guard import _guard, stubs  # noqa: F401 - stubs is a fixture


# --------------------------------------------------------------------------- read-repeat bound

def test_the_same_read_twice_returns_the_earlier_result(client_scratch, ctx_factory, stubs):  # noqa: F811
    guard, _, _ = _guard(client_scratch, ctx_factory(client_scratch, "drafter"))
    first = guard.call("crm_search_contacts", {"query": "Contoso"})
    again = guard.call("crm_search_contacts", {"query": "Contoso"})
    assert first.stage == "policy"
    assert again.stage == "repeat" and "Already looked up" in again.text
    assert [name for name, _ in stubs] == ["crm_search_contacts"]  # the tool ran once


def test_one_read_tool_stops_after_the_limit(client_scratch, ctx_factory, stubs):  # noqa: F811
    guard, _, _ = _guard(client_scratch, ctx_factory(client_scratch, "drafter"))
    for n in range(READ_REPEAT_LIMIT):
        assert guard.call("crm_search_contacts", {"query": f"name {n}"}).stage == "policy"
    stopped = guard.call("crm_search_contacts", {"query": "one more"})
    assert stopped.stage == "repeat" and "Stop calling crm_search_contacts" in stopped.text
    assert len(stubs) == READ_REPEAT_LIMIT
    # Other tools are not affected.
    assert guard.call("crm_search_deals", {"stage": "proposal"}).stage == "policy"


def test_writes_are_not_bounded_by_the_read_limit(client_scratch, ctx_factory, stubs):  # noqa: F811
    guard, _, _ = _guard(client_scratch, ctx_factory(client_scratch, "drafter"))
    assert guard.check_repeat("crm_create_note", {"contact_id": "C-1", "body": "x"}) is None


# --------------------------------------------------------------------------- work record

def _decision(tool, args, status="success", summary="done", ref=None, pending_kind=None, stage="policy"):
    result = ToolResult(tool=tool, status=status, summary=summary, ref=ref, pending_kind=pending_kind)
    return SimpleNamespace(tool=tool, stage=stage, args=args, results=[result], text=summary)


def test_turn_record_keeps_calls_that_reached_the_policy_step():
    log = [
        _decision("admin_record_change_order", {"number": "CO-2026-031", "description": "Adds the legal pack"},
                  ref="co_1", summary="Change order CO-2026-031 recorded"),
        SimpleNamespace(tool="memory_search", stage="repeat", args={"query": "x"}, results=[], text="Stop"),
        _decision("calendar_cancel_event", {"event_id": "evt_1"}, status="pending", pending_kind="approval",
                  summary="Waiting for approval"),
    ]
    record = conversation.turn_record("record change order CO-2026-031", ["admin"], log)
    assert [c["tool"] for c in record["calls"]] == ["admin_record_change_order", "calendar_cancel_event"]
    assert record["calls"][1]["status"] == "waiting for approval"
    text = conversation.recent_work_text([record])
    assert "admin_record_change_order(number=CO-2026-031" in text and "[co_1]" in text


def test_free_text_arguments_are_cut_short():
    line = conversation.short_args({"to": "maria@contoso.com", "body": "word " * 100})
    assert "to=maria@contoso.com" in line and len(line) < 140


def test_only_the_last_turns_are_kept_and_domains_carry_over():
    turns: list = []
    for n in range(conversation.MAX_TURNS + 3):
        turns = conversation.remember(turns, {"asked": str(n), "domains": ["crm"], "calls": []})
    assert len(turns) == conversation.MAX_TURNS and turns[-1]["asked"] == str(conversation.MAX_TURNS + 2)
    turns[-1]["domains"] = ["admin", "finance"]
    assert conversation.carry_domains(["memory"], turns, menu=["memory", "admin", "crm"]) == ["memory", "admin"]
    assert conversation.carry_domains(["a", "b", "c", "d"], turns, menu=["admin"]) == ["a", "b", "c", "d"]


def test_history_window_keeps_the_latest_messages():
    assert conversation.window(list(range(40))) == list(range(16, 40))
