"""The one-line summary each approval shows in the chat's approval list (content["line"])."""

from __future__ import annotations

import pytest

from freedombot.actions.models import Action
from freedombot.actions.preview import _LINE_CHARS, content_for
from freedombot.types import ActionStatus, DataClassification, ExecutionMode, RiskLevel


def _line(tool: str, **payload) -> str:
    action = Action(
        id="act_1", title=tool, description="", explanation="", status=ActionStatus.PENDING_APPROVAL, tool=tool,
        payload=payload, risk_level=RiskLevel.MEDIUM, classification=DataClassification.INTERNAL,
        execution_mode=ExecutionMode.WAIT_FOR_APPROVAL, policy_reason="", requested_by="usr_1",
    )
    return content_for(action, ctx=None)["line"]


@pytest.mark.parametrize("tool, payload, line", [
    ("memory_escalate_conflict",
     {"topic": "goal", "item_ids": ["mem_1", "mem_2"], "question": "Which company goal is current?"},
     "Memory conflict on goal: Which company goal is current?"),
    ("memory_add_fact", {"tags": ["deal"], "content": "Averitt Express signed the deal."},
     "Save to memory: Averitt Express signed the deal."),
    ("crm_create_contact", {"first_name": "John", "last_name": "Doe", "email": "john@acme.com", "company": "Acme"},
     "New contact: John Doe (Acme)"),
    ("crm_update_deal_stage", {"deal_id": "d_1", "stage": "closedwon"}, "Move deal to closedwon"),
    ("email_send_message", {"recipients": ["a@x.com", "b@y.com"], "subject": "Hello", "body": "Hi"},
     "Email to a@x.com +1: Hello"),
    ("calendar_create_event", {"title": "Demo", "starts_at": "2026-12-03T10:00:00+05:30", "ends_at": "x"},
     "New event: Demo, 3 Dec 10:00"),
    ("calendar_reschedule_event", {"event_id": "evt_1", "starts_at": "2026-12-04T15:30:00Z", "ends_at": "x"},
     "Move event to 4 Dec 15:30"),
    ("integration_write_action",
     {"action": "GOOGLECALENDAR_CREATE_EVENT", "arguments": {"summary": "Kickoff", "start_datetime": "x"}},
     "Google Calendar: Create event · Kickoff"),
])
def test_each_request_gets_a_line_that_says_what_and_to_whom(tool, payload, line):
    assert _line(tool, **payload) == line


def test_a_tool_without_its_own_line_uses_its_registry_title_and_main_detail():
    assert _line("crm_delete_record", record_type="contact", record_id="c_9") == "Delete contact c_9"
    assert _line("support_create_ticket", subject="Login broken") == "Raise a support ticket: Login broken"


def test_long_lines_are_cut_short():
    line = _line("memory_add_fact", content="word " * 60)
    assert len(line) == _LINE_CHARS and line.endswith("…")
