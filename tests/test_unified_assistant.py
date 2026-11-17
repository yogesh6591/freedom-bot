"""Scripted-model fixtures shared by evals and v2 tests.

The old campaign assistant that lived in app.assistant.assistant is gone; remaining
tests here cover session ownership and HTTP edit/delete against v2.handle.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# SAFETY: the `acq` fixture below deletes every prospect and campaign in the acquisition database.
# tests/conftest.py therefore points DATABASE_URL at the throwaway `acquisition_test` database
# (never the real one) before anything is imported, and the fixture refuses to run otherwise.
TEST_DB_NAME = "acquisition_test"

from app.api.chat import ChatRequest  # noqa: E402
from app.api.deps import bound  # noqa: E402
from freedombot import chat_history  # noqa: E402

SCRIPT: list = []
REQUESTS: list = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        REQUESTS.append(req)
        item = SCRIPT.pop(0) if SCRIPT else {"content": json.dumps({"message": "(script empty)", "explanation": "x"})}
        if callable(item):
            item = item(req)
        if item.get("status"):
            body = json.dumps({"error": {"message": item.get("message", "bad"), "type": "invalid_request_error"}}).encode()
            self.send_response(item["status"])
        else:
            msg = {"role": "assistant", "content": item.get("content")}
            finish = "stop"
            if item.get("tool_calls"):
                finish = "tool_calls"
                msg["tool_calls"] = [
                    {
                        "id": f"call_{len(REQUESTS)}_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))},
                    }
                    for i, c in enumerate(item["tool_calls"])
                ]
            body = json.dumps(
                {
                    "id": "x",
                    "object": "chat.completion",
                    "created": 0,
                    "model": req.get("model", "x"),
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def final(message: str, explanation: str = "e") -> dict:
    return {"content": message}


def _chitchat() -> dict:
    return {"content": json.dumps({"domains": [], "kind": "chitchat"})}


def call(name: str, **args) -> dict:
    args.setdefault("why", "test")
    return {"tool_calls": [{"name": name, "arguments": args}]}


@pytest.fixture(scope="module")
def fake_model():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = {
        k: os.environ.get(k)
        for k in (
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "MODEL_REASONING_EFFORT",
            "ASSISTANT_REASONING_EFFORT",
        )
    }
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ["ASSISTANT_REASONING_EFFORT"] = "none"
    os.environ.pop("MODEL_REASONING_EFFORT", None)
    from freedombot import settings as app_settings

    app_settings.llm_available.cache_clear()
    yield
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    app_settings.llm_available.cache_clear()
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset():
    SCRIPT.clear()
    REQUESTS.clear()
    from app.assistant import assistant as agent

    agent._effort["tried"].clear()
    yield


@pytest.fixture
def acq():
    """A clean acquisition database with Ethan (active in `snowflak`), Nina (free) and Gus (bounced)."""
    from acquisition.db.session import SessionLocal, engine
    from acquisition.models.campaign import (
        Campaign,
        CampaignMessage,
        CampaignProspect,
        OutreachStatus,
    )
    from acquisition.models.prospect import ApprovalStatus, Prospect
    from acquisition.models.replies import ReplyConversation, ReplyMessage

    # Hard stop: never wipe a database that is not the throwaway test one.
    assert engine.url.database == TEST_DB_NAME, f"refusing to wipe {engine.url.database!r}"
    db = SessionLocal()
    for model in (ReplyMessage, ReplyConversation, CampaignProspect, CampaignMessage, Campaign, Prospect):
        db.query(model).delete()
    db.commit()

    def person(name, email):
        p = Prospect(company="Acme", executive_name=name, email=email, approval_status=ApprovalStatus.APPROVED)
        db.add(p)
        db.commit()
        return p

    ethan, nina, gus = person("Ethan Hunt", "ethan@acme.example.com"), person("Nina Lee", "nina@acme.example.com"), person("Gus Fring", "gus@acme.example.com")
    snow = Campaign(name="snowflak", sender_name="Me")
    db.add(snow)
    db.commit()
    db.add(CampaignProspect(campaign_id=snow.id, prospect_id=ethan.id, outreach_status=OutreachStatus.ACTIVE))
    db.add(CampaignProspect(campaign_id=snow.id, prospect_id=gus.id, outreach_status=OutreachStatus.BOUNCED))
    db.commit()
    yield {"snow": str(snow.id)}
    db.close()


def tool_names(req: dict) -> set[str]:
    return {t["function"]["name"] for t in req.get("tools") or []}


def ask(ctx, settings, message, session_id=None, **extra):
    from app.assistant.v2 import handle

    with bound(ctx):
        return handle(ChatRequest(message=message, session_id=session_id, **extra), ctx, settings)


def test_model_failure_is_a_readable_502(fake_model, acq, client_scratch, ctx_factory):
    from fastapi import HTTPException

    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [{"status": 500, "message": "upstream boom"}] * 30
    with pytest.raises(HTTPException) as exc:
        ask(ctx, client_scratch.settings, "hello")
    assert exc.value.status_code == 502 and "Model call failed" in exc.value.detail


def test_conversation_belongs_to_its_owner(fake_model, acq, client_scratch, ctx_factory):
    from fastapi import HTTPException

    admin = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [_chitchat(), final("hi")]
    sid = ask(admin, client_scratch.settings, "hello")["session_id"]
    other = ctx_factory(client_scratch, "viewer")
    with pytest.raises(HTTPException) as exc:
        ask(other, client_scratch.settings, "hello again", session_id=sid)
    assert exc.value.status_code == 404


@pytest.fixture(scope="module")
def http():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _token(http, client, prefix):
    r = http.post(
        "/api/auth/login",
        json={"email": f"{prefix}@{client.slug}.example.com", "password": "bizos-dev-password", "client_slug": client.slug},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_edit_someone_elses_session_is_404(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [_chitchat(), final("hi")]
    sid = ask(ctx, client_scratch.settings, "hello")["session_id"]
    message_id = chat_history.get_session(ctx, sid)["messages"][0]["id"]
    viewer = _token(http, client_scratch, "viewer")
    SCRIPT[:] = [final("should not run")]
    r = http.post(
        f"/api/chat/sessions/{sid}/edit",
        json={"message_id": message_id, "message": "hello there"},
        headers=viewer,
    )
    assert r.status_code == 404
    assert [m["content"] for m in chat_history.get_session(ctx, sid)["messages"]] == ["hello", "hi"]


def test_edit_assistant_message_is_400(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [_chitchat(), final("hi")]
    sid = ask(ctx, client_scratch.settings, "hello")["session_id"]
    assistant_id = next(m["id"] for m in chat_history.get_session(ctx, sid)["messages"] if m["role"] == "assistant")
    admin = _token(http, client_scratch, "admin")
    SCRIPT[:] = [final("should not run")]
    r = http.post(
        f"/api/chat/sessions/{sid}/edit",
        json={"message_id": assistant_id, "message": "hello there"},
        headers=admin,
    )
    assert r.status_code == 400
    assert [m["content"] for m in chat_history.get_session(ctx, sid)["messages"]] == ["hello", "hi"]


def test_edit_keeps_history_when_the_model_fails(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [_chitchat(), final("hi")]
    sid = ask(ctx, client_scratch.settings, "hello")["session_id"]
    original = chat_history.get_session(ctx, sid)
    user_id = original["messages"][0]["id"]
    SCRIPT[:] = [{"status": 500, "message": "upstream boom"}] * 30
    admin = _token(http, client_scratch, "admin")
    r = http.post(
        f"/api/chat/sessions/{sid}/edit",
        json={"message_id": user_id, "message": "hello edited"},
        headers=admin,
    )
    assert r.status_code == 502 and "Model call failed" in r.text
    after = chat_history.get_session(ctx, sid)
    assert [m["id"] for m in after["messages"]] == [m["id"] for m in original["messages"]]
    assert [m["content"] for m in after["messages"]] == ["hello", "hi"]
