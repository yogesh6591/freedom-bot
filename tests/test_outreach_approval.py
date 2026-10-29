"""approval_problem maps the model decision; these tests stub that client."""

from types import SimpleNamespace

from app.outreach.approval import approval_problem
from app.outreach.confirm import action_key, gate


def _stub_decision(monkeypatch, decision):
    class _Client:
        def response(self, **kwargs):
            return SimpleNamespace(parsed={"decision": decision}, content=None)

    monkeypatch.setattr("app.assistant.assistant._model", lambda effort: _Client())


def test_approve_decision_is_no_problem(monkeypatch):
    _stub_decision(monkeypatch, "approve")
    assert approval_problem("Yes, cancel it.", "Yes, cancel it.") == ""


def test_decline_decision_holds_back(monkeypatch):
    _stub_decision(monkeypatch, "decline")
    assert "holds back" in approval_problem("no, cancel that", "no, cancel that")


def test_unclear_decision_does_not_approve(monkeypatch):
    _stub_decision(monkeypatch, "unclear")
    assert "does not clearly approve" in approval_problem("wait", "wait")


def test_pending_cancel_accepts_when_model_approves(monkeypatch):
    _stub_decision(monkeypatch, "approve")
    key = action_key("cancel_booking", {"id": "p1", "uid": "uid-1"})
    state = {"pending": {"key": key, "asked_on": "please cancel the booking"}}
    assert gate(state, "Yes, cancel it.", key, "Yes, cancel it.", "Cancel the call?") is None
    assert "pending" not in state
