"""Streaming replies: the claim gate on streamed words, and the /api/chat/stream event protocol.

No model calls: the gate is fed text directly and the endpoint's turn is faked.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bizos.agents.composer import StreamGate

CLAIM = "Here are the details for Raj. I've sent the email to Raj. Anything else?"


def _stream(text: str, state, *, task: bool = True, chunk: int = 3) -> tuple[str, int]:
    gate = StreamGate(lambda: state, task=task)
    parts = [gate.feed(text[i:i + chunk]) for i in range(0, len(text), chunk)] + [gate.flush()]
    return "".join(parts), sum(1 for p in parts if p)


def test_words_stream_out_while_they_arrive():
    shown, pieces = _stream("The pipeline has 22 deals in Appointment Scheduled and 31 in Qualified.", (False, False, False))
    assert shown == "The pipeline has 22 deals in Appointment Scheduled and 31 in Qualified." and pieces > 5


def test_a_done_claim_never_streams_when_no_write_succeeded():
    shown, _ = _stream(CLAIM, (False, False, True))
    assert "sent the email" not in shown and shown.endswith("Anything else?")


def test_a_true_done_claim_streams_once_the_write_succeeded():
    assert _stream(CLAIM, (True, False, True))[0] == CLAIM


def test_approval_wording_streams_only_when_something_waits_for_approval():
    text = "It is waiting for approval now."
    assert _stream(text, (False, True, True))[0] == text
    assert "approval" not in _stream(text, (False, False, True))[0]


def test_answers_to_questions_are_not_filtered():
    text = "The Acme contract was signed in March. It renews yearly."
    assert _stream(text, (False, False, False), task=False)[0] == text


def test_no_partial_word_leaks_before_a_claim_is_judged():
    shown, _ = _stream("Done.", (False, False, True), chunk=1)
    assert shown == ""


# --------------------------------------------------------------------------- the endpoint


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _token(app_client, client) -> str:
    response = app_client.post("/api/auth/login", json={"email": f"admin@{client.slug}.example.com",
                                                        "password": "bizos-dev-password", "client_slug": client.slug})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _events(response) -> list[dict]:
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


def test_the_stream_sends_progress_then_the_same_reply(app_client, client_a, monkeypatch):
    import app.api.chat as chat

    def fake(body, ctx, settings, emit=None):
        emit({"type": "status", "text": "Search CRM contacts…"})
        emit({"type": "token", "text": "Raj is "})
        emit({"type": "token", "text": "at Acme."})
        return {"session_id": "ses_1", "content": "Raj is at Acme.", "client": ctx.client_id}

    monkeypatch.setattr(chat, "_dispatch", fake)
    response = app_client.post("/api/chat/stream", json={"message": "who is Raj"},
                               headers={"Authorization": f"Bearer {_token(app_client, client_a)}"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert [e["type"] for e in events] == ["status", "token", "token", "done"]
    assert events[-1]["response"] == {"session_id": "ses_1", "content": "Raj is at Acme.", "client": client_a.id}


def test_a_failed_turn_ends_the_stream_with_an_error(app_client, client_a, monkeypatch):
    import app.api.chat as chat

    def fake(body, ctx, settings, emit=None):
        raise HTTPException(status_code=502, detail="Model call failed: boom")

    monkeypatch.setattr(chat, "_dispatch", fake)
    response = app_client.post("/api/chat/stream", json={"message": "hi"},
                               headers={"Authorization": f"Bearer {_token(app_client, client_a)}"})
    assert _events(response) == [{"type": "error", "status": 502, "detail": "Model call failed: boom"}]


def test_the_stream_needs_a_login(app_client):
    app_client.cookies.clear()  # the earlier logins left a session cookie in this client
    assert app_client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401


def test_v2_receives_the_emitter(client_a, ctx_factory, monkeypatch):
    import app.api.chat as chat
    from app.assistant import v2

    seen = {}
    monkeypatch.setattr(v2, "enabled", lambda: True)
    monkeypatch.setattr(v2, "handle", lambda body, ctx, settings, emit=None: seen.setdefault("emit", emit) or {})
    sink = []
    chat._dispatch(chat.ChatRequest(message="hi"), ctx_factory(client_a, "admin"), client_a.settings, sink.append)
    assert seen["emit"] == sink.append


def test_a_long_quiet_turn_gets_keep_alives(app_client, client_a, monkeypatch):
    import time

    import app.api.chat as chat

    def slow(body, ctx, settings, emit=None):
        time.sleep(0.2)
        return {"session_id": "ses_2", "content": "done"}

    monkeypatch.setattr(chat, "KEEPALIVE_SEC", 0.05)
    monkeypatch.setattr(chat, "_dispatch", slow)
    response = app_client.post("/api/chat/stream", json={"message": "long job"},
                               headers={"Authorization": f"Bearer {_token(app_client, client_a)}"})
    assert ": keep-alive" in response.text and _events(response)[-1]["type"] == "done"
