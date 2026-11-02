"""Small-edit subject changes follow the model's judgement, not a keyword regex."""

from types import SimpleNamespace

from app.outreach.session import OutreachSession


class _FakeDb:
    def close(self):
        return None


def _stub_subject_decision(monkeypatch, decision):
    class _Client:
        def response(self, **kwargs):
            return SimpleNamespace(parsed={"decision": decision}, content=None)

    monkeypatch.setattr("app.assistant.assistant._model", lambda effort: _Client())


def _session(latest: str) -> OutreachSession:
    body = "Hi {{firstName}},\n\nHello there.\n\nBest,"
    session = OutreachSession(
        SimpleNamespace(),
        {
            "recipients": [{"email": "pat@example.com", "name": "Pat"}],
            "draft": {"email_subject": "A quick introduction", "email_body": body},
        },
        latest_message=latest,
        db_factory=_FakeDb,
    )
    session._records = lambda db: {"people": [], "assessment_url": ""}  # type: ignore[method-assign]
    session._mark_threads = lambda db: None  # type: ignore[method-assign]
    return session


def test_subject_change_follows_model_not_subject_keyword(monkeypatch):
    body = "Hi {{firstName}},\n\nHello there.\n\nBest,"
    longer = (
        "Hi {{firstName}},\n\nHello there.\n\nThis is a longer note.\n\nBest,"
    )
    _stub_subject_decision(monkeypatch, "yes")
    allowed = _session("make it longer and change sub ject to Welcome X")
    out = allowed.propose_email(subject="Welcome X", body=longer)
    assert out.startswith("ok:")
    assert allowed.state["draft"]["email_subject"] == "Welcome X"

    _stub_subject_decision(monkeypatch, "no")
    refused = _session("make it shorter")
    out = refused.propose_email(subject="Welcome X", body=body)
    assert "did not ask to change the subject" in out
    assert "A quick introduction" in out
    assert refused.state["draft"]["email_subject"] == "A quick introduction"
