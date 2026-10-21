"""The unified Agno assistant, end to end.

The real Agno agent runs against a scripted OpenAI-compatible server, so these tests exercise the
actual tool schemas, the structured reply, the self-check loop, persistence and the campaign rules,
without a live model.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# SAFETY: the `acq` fixture below deletes every prospect and campaign in the acquisition database.
# tests/conftest.py therefore points DATABASE_URL at the throwaway `acquisition_test` database
# (never the real one) before anything is imported, and the fixture refuses to run otherwise.
TEST_DB_NAME = "acquisition_test"

from app.api.chat import ChatRequest  # noqa: E402
from app.api.deps import bound  # noqa: E402
from bizos import chat_history  # noqa: E402

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
    return {"content": json.dumps({"message": message, "explanation": explanation})}


def call(name: str, **args) -> dict:
    args.setdefault("why", "test")
    return {"tool_calls": [{"name": name, "arguments": args}]}


@pytest.fixture(scope="module")
def fake_model():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = {k: os.environ.get(k) for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "MODEL_REASONING_EFFORT")}
    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    os.environ["OPENAI_API_KEY"] = "test-key"
    os.environ.pop("MODEL_REASONING_EFFORT", None)
    from bizos import settings as app_settings

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
        CampaignProspect,
        InstantlyRuntimeStatus,
        OutreachStatus,
    )
    from acquisition.models.prospect import ApprovalStatus, Prospect

    # Hard stop: never wipe a database that is not the throwaway test one.
    assert engine.url.database == TEST_DB_NAME, f"refusing to wipe {engine.url.database!r}"
    db = SessionLocal()
    for model in (CampaignProspect, Campaign, Prospect):
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
    from app.assistant.assistant import reply

    with bound(ctx):
        return reply(ChatRequest(message=message, session_id=session_id, **extra), ctx, settings)


# ------------------------------------------------------------------------------------------ ops


def test_ops_task_runs_a_tool_with_why_and_structured_reply(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("memory_search", query="onboarding checklist", why="User asks what we know about the onboarding checklist."),
        final("I found nothing saved about the onboarding checklist yet.", "Searched memory for the onboarding checklist; nothing found."),
    ]
    out = ask(ctx, client_scratch.settings, "what do we know about the onboarding checklist?")
    assert out["content"] == "I found nothing saved about the onboarding checklist yet."
    assert [t["tool"] for t in out["tool_activity"]] == ["memory_search"]
    log = out["trace"]["tool_log"]
    assert log[0]["tool"] == "memory_search" and log[0]["why"].startswith("User asks")
    assert out["trace"]["explanation"].startswith("Searched memory")
    assert out["trace"]["engine"] == "agno"

    # What the model was offered: every tool needs `why`, optional fields stay optional, no strict mode.
    tools = {t["function"]["name"]: t["function"] for t in REQUESTS[0]["tools"]}
    assert "why" in tools["memory_search"]["parameters"]["required"]
    assert "query" in tools["memory_search"]["parameters"]["required"]
    assert "limit" not in tools["memory_search"]["parameters"]["required"]
    assert not tools["memory_search"].get("strict")
    assert {"update_selection", "propose_email", "send_approved", "approve_move"} <= tool_names(REQUESTS[0])
    # The conversation was saved with the trace.
    saved = chat_history.get_session(ctx, out["session_id"])
    assert saved["messages"][-1]["meta"]["json"]["tool_log"][0]["why"].startswith("User asks")


def test_campaign_tools_are_admin_only(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "viewer")
    SCRIPT[:] = [final("Hi there!")]
    out = ask(ctx, client_scratch.settings, "hello")
    assert out["content"] == "Hi there!"
    assert not ({"update_selection", "send_approved"} & tool_names(REQUESTS[0]))


def test_model_failure_is_a_readable_502(fake_model, acq, client_scratch, ctx_factory):
    from fastapi import HTTPException

    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [{"status": 500, "message": "upstream boom"}] * 30
    with pytest.raises(HTTPException) as exc:
        ask(ctx, client_scratch.settings, "hello")
    assert exc.value.status_code == 502 and "Model call failed" in exc.value.detail


def test_reasoning_effort_falls_back_when_the_model_rejects_it(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    os.environ["MODEL_REASONING_EFFORT"] = "low"
    try:
        SCRIPT[:] = [{"status": 400, "message": "Function tools with reasoning_effort are not supported"}, final("ok")]
        out = ask(ctx, client_scratch.settings, "hello")
    finally:
        os.environ.pop("MODEL_REASONING_EFFORT", None)
    assert out["content"] == "ok"
    assert REQUESTS[0].get("reasoning_effort") == "low"
    assert REQUESTS[1].get("reasoning_effort") == "none"


# ------------------------------------------------------------------------------------------ self-check


def test_false_action_claim_is_fixed_by_the_model_itself(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        final("I've sent the email to Bob."),
        final("I haven't sent anything yet. Who should it go to, and what should it say?"),
    ]
    out = ask(ctx, client_scratch.settings, "email Bob that the meeting moved to Friday")
    assert len(REQUESTS) == 2
    assert out["content"].startswith("I haven't sent anything")
    assert out["trace"]["self_check"], "the nudge should be recorded"
    # The second request carried the self-check note.
    assert "Self-check" in json.dumps(REQUESTS[1]["messages"])


def test_stubborn_model_gets_the_platform_wording(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [final("I've sent the email to Bob.")] * 3
    out = ask(ctx, client_scratch.settings, "email Bob that the meeting moved to Friday")
    assert "I've sent" not in out["content"]
    assert "nothing was actually" in out["content"].lower()
    assert "FALSE_ACTION_CLAIM_CORRECTED" in out["trace"]["guardrails"]


# ------------------------------------------------------------------------------------------ campaign flow


def test_ethan_move_needs_a_yes_in_a_later_turn(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings

    # Turn 1: pick Ethan + a name close to an existing campaign. Ask, don't guess.
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "ethan@acme.example.com"}], why="User named Ethan."),
        call("update_selection", campaign_name="snowflake", why="User wants a new campaign called snowflake."),
        final("Do you mean the existing snowflak, or a new campaign called snowflake?"),
    ]
    t1 = ask(ctx, st, "send Ethan an email in a new campaign snowflake")
    sid = t1["session_id"]
    log = t1["trace"]["tool_log"]
    assert [c["tool"] for c in log] == ["update_selection", "update_selection"]
    assert "similar_campaign_name" in log[1]["result"]
    assert t1["content"].startswith("Do you mean")

    # Turn 2: the user says new. Commit with confirmation, then try to approve the move in the same turn
    # in which the person was first explained (it was explained in turn 1's state? No: only an earlier
    # assistant turn that raised it counts).
    SCRIPT[:] = [
        call(
            "update_selection",
            campaign_name="snowflake",
            confirmed_new_campaign=True,
            window_start="9 am",
            window_end="5 pm",
            window_evidence="9 am to 5 pm",
            why="They said new.",
        ),
        call("propose_email", subject="Hi", body="Hi Ethan", why="All set."),
        final("Ethan is active in snowflak and was already emailed. Moving him stops his follow-ups there. Move him?"),
    ]
    t2 = ask(ctx, st, "new, 9 am to 5 pm", session_id=sid)
    tools_t2 = [c["tool"] for c in t2["trace"]["tool_log"]]
    assert "propose_email" in tools_t2
    propose = [c for c in t2["trace"]["tool_log"] if c["tool"] == "propose_email"][0]
    assert "needs_move_approval" in propose["result"]
    assert not t2["trace"]["email_draft"], "no email card until the move is approved"

    # Turn 3: yes. Approve the move, then propose the email.
    SCRIPT[:] = [
        call("approve_move", emails=["ethan@acme.example.com"], approval_evidence="yes", why="User said yes."),
        call("propose_email", subject="Hi", body="Hi Ethan", why="Move approved."),
        final("Here's the email. Approve it as is, or change something?"),
    ]
    t3 = ask(ctx, st, "yes move him", session_id=sid)
    assert "Subject:" in t3["trace"]["email_draft"]
    assert t3["trace"]["campaign_name"] == "snowflake"
    assert t3["trace"]["email_address_list"] == ["ethan@acme.example.com"]

    # The user edits the email in the UI; the edit must be what the next turn sees and sends.
    chat_history.set_latest_email_draft(
        ctx, sid, "Subject: Edited\n\nEdited body", {"email_subject": "Edited", "email_body": "Edited body"}
    )
    state = chat_history.get_session(ctx, sid)["messages"][-1]["meta"]["state"]
    assert state["draft"]["email_subject"] == "Edited" and state["draft"]["email_body"] == "Edited body"


def test_unsubscribed_person_is_always_refused(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "gus@acme.example.com"}], campaign_name="Brand New", confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm", why="x"),
        call("propose_email", subject="s", body="b", why="x"),
        call("approve_move", emails=["gus@acme.example.com"], approval_evidence="y", why="x"),
        final("Gus bounced earlier, so I can't add him. Want to continue without him?"),
    ]
    out = ask(ctx, client_scratch.settings, "email Gus in Brand New campaign, 9 am to 5 pm")
    results = {c["tool"]: c["result"] for c in out["trace"]["tool_log"]}
    assert "refused_person" in results["propose_email"]
    assert "move_not_approved" in results["approve_move"]
    assert not out["trace"]["email_draft"]


def test_sending_is_blocked_in_advise_mode(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh", confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm", why="x"),
        call("propose_email", subject="s", body="b", why="x"),
        final("Here's the email. Approve it?"),
    ]
    t1 = ask(ctx, client_scratch.settings, "email Nina in Fresh, 9 am to 5 pm", execution_mode="ADVISE")
    SCRIPT[:] = [call("send_approved", approval_evidence="yes", why="approved"), final("This workspace is in Advise mode, so I can't send.")]
    t2 = ask(ctx, client_scratch.settings, "yes send", session_id=t1["session_id"], execution_mode="ADVISE")
    send = [c for c in t2["trace"]["tool_log"] if c["tool"] == "send_approved"][0]
    assert "Advise" in send["result"]
    assert t2["trace"]["tool"] != "execute"


def test_conversation_belongs_to_its_owner(fake_model, acq, client_scratch, ctx_factory):
    from fastapi import HTTPException

    admin = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [final("hi")]
    sid = ask(admin, client_scratch.settings, "hello")["session_id"]
    other = ctx_factory(client_scratch, "viewer")
    with pytest.raises(HTTPException) as exc:
        ask(other, client_scratch.settings, "hello again", session_id=sid)
    assert exc.value.status_code == 404




# ------------------------------------------------------------------------------------------ HTTP


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


def test_http_chat_edit_email_and_delete(fake_model, acq, client_scratch, http):
    admin = _token(http, client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh start", confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm", why="x"),
        call("propose_email", subject="Hello", body="Hi Nina", why="x"),
        final("Here's the email. Approve it, or change something?"),
    ]
    r = http.post("/api/chat", json={"message": "email Nina in a new campaign Fresh start, 9 am to 5 pm"}, headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response"] == body["content"] and "Subject: Hello" in body["email_draft"]
    assert body["trace"]["email_address_list"] == ["nina@acme.example.com"]
    sid = body["session_id"]

    e = http.post(
        "/api/chat/email-draft",
        json={"session_id": sid, "email_draft": "Subject: Mine\n\nMy body", "email_subject": "Mine", "email_body": "My body"},
        headers=admin,
    )
    assert e.status_code == 200
    detail = http.get(f"/api/chat/sessions/{sid}", headers=admin).json()
    assert detail["messages"][-1]["meta"]["json"]["email_draft"].startswith("Subject: Mine")
    assert detail["messages"][-1]["meta"]["state"]["draft"]["email_body"] == "My body"

    ctx = http.get("/api/chat/context", headers=admin).json()
    assert "sending_accounts" in ctx

    # Ordinary users do not get the machinery.
    viewer = _token(http, client_scratch, "viewer")
    SCRIPT[:] = [final("Hello!")]
    v = http.post("/api/chat", json={"message": "hi"}, headers=viewer).json()
    assert v["trace"] == {"engine": "agno"}

    assert http.delete(f"/api/chat/sessions/{sid}", headers=admin).status_code == 200
    assert http.get(f"/api/chat/sessions/{sid}", headers=admin).status_code == 404
    assert http.delete("/api/chat/sessions", headers=admin).json()["ok"] is True


def test_hours_the_user_never_gave_are_not_saved(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh", confirmed_new_campaign=True,
             window_start="9 am", window_end="5 pm", window_evidence="default hours", why="guessing"),
        final("What daily send hours should it use, for example 9 am to 5 pm?"),
    ]
    out = ask(ctx, client_scratch.settings, "email Nina in a new campaign Fresh")
    first = out["trace"]["tool_log"][0]
    assert "window_not_given_by_user" in first["result"]
    assert out["trace"]["campaign_schedule_timing"] == []
    assert out["trace"]["campaign_name"] == "Fresh"  # the parts the user DID give are kept
    assert out["trace"]["email_address_list"] == ["nina@acme.example.com"]
