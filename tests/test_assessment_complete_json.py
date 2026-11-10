import json
from types import SimpleNamespace

import pytest

from acquisition.models.assessment import AssessmentMessageRole
from acquisition.services import assessment_chat_service as svc_mod
from acquisition.services.assessment_chat_service import AssessmentChatService
from acquisition.services.assessment_extraction import (
    REQUIRED_ASSESSMENT_FIELDS,
    answers_sheet,
    build_current_assessment_state,
    close_stalled_fields,
    get_ask_state,
    is_ready_for_completion,
    missing_required_assessment_fields,
    parse_assistant_turn_response,
    record_questions_asked,
)

U = AssessmentMessageRole.USER
A = AssessmentMessageRole.ASSISTANT

MSGS = [
    "We run a print shop with 12 staff, doing about $400k a year. We want steady growth.",
    "Orders are taken by phone and written on paper, then typed into the system by one person. Printing and delivery are the two teams.",
    "I own it and decide everything. No problems really, no delays, nothing holds us up. I am open to AI but not planning any changes right now.",
]


def _field(value, quote, status="known"):
    return {"status": status, "value": value, "evidence_quote": quote}


FIRST = {
    "company": _field("print shop", "We run a print shop"),
    "goals": _field("steady growth", "We want steady growth"),
    "revenue_range": _field("Under $3M", "about $400k a year"),
    "headcount": {**_field(12, "12 staff"), "scope": "company"},
    "functions": _field("printing, delivery", "Printing and delivery are the two teams"),
    "workflows": _field("phone orders typed in by one person", "Orders are taken by phone"),
}
NONE_ANSWERS = {
    "pain_points": _field("No problems reported", "No problems really"),
    "time_cost_impact": _field("No cost reported", "no delays"),
    "bottlenecks": _field("None reported", "nothing holds us up"),
    "change_readiness": _field("Not planning any changes", "not planning any changes right now"),
    "ai_readiness": _field("Open to AI", "I am open to AI"),
    "authority": _field("decision_maker", "I own it and decide everything"),
}


class FakeDB:
    def add(self, *_): pass
    def commit(self): pass
    def refresh(self, *_): pass


def _make(monkeypatch, extractor_replies):
    s = object.__new__(AssessmentChatService)
    s.db = FakeDB()
    s.msgs = []
    replies = list(extractor_replies)

    def ordered(_cid):
        return list(s.msgs)

    s._ordered_messages = ordered
    s._call_responses = lambda _m: json.dumps(replies.pop(0))
    monkeypatch.setattr(svc_mod, "flag_modified", lambda *_: None)
    return s


def _say(s, role, text):
    s.msgs.append(SimpleNamespace(id=f"m{len(s.msgs)}", role=role, content=text))
    return s.msgs[-1]


def _profile():
    return SimpleNamespace(structured_state=None, qualification=None, qualification_status=None)


def _turn(s, profile, text, patch):
    s._call_responses = lambda _m, p=patch: json.dumps(p)
    m = _say(s, U, text)
    s._update_structured_state(None, SimpleNamespace(id="c"), profile, text,
                               user_message_id=m.id, user_message_role="user")


def test_none_answers_recorded_completes(monkeypatch):
    s = _make(monkeypatch, [])
    p = _profile()
    _say(s, A, "Tell me about your business.")
    _turn(s, p, " ".join(MSGS[:2]), FIRST)
    assert "pain_points" in missing_required_assessment_fields(p.structured_state)
    _say(s, A, "What problems or delays do you have, and are you open to change?")
    _turn(s, p, MSGS[2], NONE_ANSWERS)
    assert is_ready_for_completion(p.structured_state)
    sheet = answers_sheet(p.structured_state)
    assert sheet["required_answered"] == 12 and sheet["required_unanswered"] == []
    assert sheet["fields"]["pain_points"]["status"] == "known"
    assert sheet["fields"]["pain_points"]["quote"] == "No problems really"
    assert set(sheet["fields"]) >= set(REQUIRED_ASSESSMENT_FIELDS)


def test_loop_guard_closes_after_two_asks(monkeypatch):
    # The extractor keeps omitting the "none" answers (the old failure). Guard must still finish.
    s = _make(monkeypatch, [])
    p = _profile()
    _say(s, A, "Tell me about your business.")
    _turn(s, p, " ".join(MSGS[:2]), FIRST)
    missing = missing_required_assessment_fields(p.structured_state)
    assert "pain_points" in missing
    # question 1 about the missing items
    p.structured_state = record_questions_asked(p.structured_state, missing)
    assert get_ask_state(p.structured_state)["counts"]["pain_points"] == 1
    _turn(s, p, "No problems really, no delays, nothing holds us up.", {})
    assert "pain_points" in missing_required_assessment_fields(p.structured_state)  # not yet closed
    # question 2, same items
    p.structured_state = record_questions_asked(p.structured_state, missing)
    _turn(s, p, "Like I said, nothing is wrong.", {})
    assert is_ready_for_completion(p.structured_state)
    sheet = answers_sheet(p.structured_state)
    assert sheet["fields"]["pain_points"]["status"] == "unknown"
    assert sheet["fields"]["pain_points"]["quote"] == "Like I said, nothing is wrong."
    assert sheet["fields"]["company"]["status"] == "known"  # known answers untouched


def test_guard_only_closes_fields_the_last_question_covered(monkeypatch):
    s = _make(monkeypatch, [])
    p = _profile()
    _say(s, A, "Hi")
    _turn(s, p, " ".join(MSGS[:2]), FIRST)
    p.structured_state = record_questions_asked(p.structured_state, ["pain_points"])
    p.structured_state = record_questions_asked(p.structured_state, ["pain_points"])
    p.structured_state = record_questions_asked(p.structured_state, ["bottlenecks"])
    state, closed = close_stalled_fields(p.structured_state, latest_user="x y", user_message_id="m9")
    assert closed == []  # last question covered bottlenecks, asked once
    assert "pain_points" in missing_required_assessment_fields(state)


def test_state_hints_and_turn_parse():
    st = record_questions_asked(None, ["pain_points"])
    built = build_current_assessment_state(st)
    assert "pain_points" in built["asked_once_unanswered"]
    assert "pain_points" not in built["not_yet_asked"] and "bottlenecks" in built["not_yet_asked"]
    t = parse_assistant_turn_response(json.dumps({"assistant_message": "Q?", "asked_fields": ["pain_points", 3]}))
    assert t["asked_fields"] == ["pain_points"]
    assert parse_assistant_turn_response("garbage")["assistant_message"]
