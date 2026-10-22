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


@pytest.mark.skip(reason="Chat is campaign-only; ops tools are not on this agent.")
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
    assert {"update_selection", "propose_email", "send_approved", "approve_move", "remove_from_campaign"} <= tool_names(REQUESTS[0])
    # The conversation was saved with the trace.
    saved = chat_history.get_session(ctx, out["session_id"])
    assert saved["messages"][-1]["meta"]["json"]["tool_log"][0]["why"].startswith("User asks")


def test_campaign_tools_are_admin_only(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "viewer")
    SCRIPT[:] = [final("Hi there!")]
    out = ask(ctx, client_scratch.settings, "hello")
    assert out["content"] == "Hi there!"
    assert not ({"update_selection", "send_approved", "remove_from_campaign"} & tool_names(REQUESTS[0]))


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

    forever = http.post(
        "/api/chat/email-card",
        json={
            "session_id": sid,
            "subject": "Hello",
            "body": "Hi {{firstName}},",
            "name_scope": "forever",
            "people": {
                "nina@acme.example.com": {"first_name": "Boss", "last_name": "Lee", "company": "Acme"}
            },
        },
        headers=admin,
    )
    assert forever.status_code == 200, forever.text
    from acquisition.db.session import SessionLocal
    from acquisition.models.prospect import Prospect

    db = SessionLocal()
    nina = db.query(Prospect).filter(Prospect.email == "nina@acme.example.com").one()
    assert nina.executive_name == "Boss Lee"
    db.close()
    saved = http.get(f"/api/chat/sessions/{sid}", headers=admin).json()["messages"][-1]["meta"]["json"]
    assert saved["email_view"]["people"][0]["first_name"] == "Boss"
    assert "Boss" in saved["email_draft"]

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


def test_a_send_claim_without_a_send_is_never_shown(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh",
             confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm"),
        call("propose_email", subject="Hi", body="Hi Nina"),
        final("Here is the email. Approve it as is, or change something?"),
    ]
    sid = ask(ctx, st, "email Nina in a new campaign Fresh, 9 am to 5 pm")["session_id"]

    # The model lies twice (and keeps lying after the nudge): the platform wording must win.
    SCRIPT[:] = [final("The Fresh campaign was created and the email was sent.")] * 3
    out = ask(ctx, st, "go ahead", session_id=sid)
    assert "was sent" not in out["content"]
    assert out["content"].startswith("Nothing has been sent")
    assert not any(c["tool"] == "send_approved" for c in out["trace"]["tool_log"])


def test_overnight_hours_are_refused_when_given_not_at_send_time(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Night", confirmed_new_campaign=True,
             window_start="5 pm", window_end="6 am", window_evidence="5 pm to 6 am"),
        final("Hours have to fit inside one day, like 9 am to 5 pm. What would you like?"),
    ]
    out = ask(ctx, client_scratch.settings, "email Nina in a new campaign Night, 5 pm to 6 am")
    assert "window_start_not_before_end" in out["trace"]["tool_log"][0]["result"]
    assert out["trace"]["campaign_schedule_timing"] == []
    assert out["content"].startswith("Hours have to fit")


def test_an_honest_reply_that_mentions_send_approved_is_kept(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="Fresh2",
             confirmed_new_campaign=True, window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm"),
        call("propose_email", subject="Hi", body="Hi Nina"),
        final("Here is the email. Approve it as is, or change something?"),
    ]
    sid = ask(ctx, st, "email Nina in a new campaign Fresh2, 9 am to 5 pm")["session_id"]
    SCRIPT[:] = [final("Which of the two changes do you want?", explanation="I did not call send_approved because the user asked a question.")]
    out = ask(ctx, st, "can you make it warmer?", session_id=sid)
    assert out["content"].startswith("Which of the two")


class _FakeInstantly:
    def __init__(self, items=None, fail_with=None):
        self.calls, self.items, self.fail_with = [], items or [], fail_with

    def list_leads(self, payload):
        self.calls.append(("LIST", payload))
        return {"items": self.items}

    def _request(self, method, path, **kw):
        self.calls.append((method, path, kw))
        if self.fail_with:
            raise self.fail_with


def _ethan_link(acq_fixture, lead_id=None):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect
    from acquisition.models.prospect import Prospect

    db = SessionLocal()
    snow = db.get(Campaign, acq_fixture["snow"])
    snow.instantly_campaign_id = "ic-1"
    link = db.query(CampaignProspect).join(Prospect).filter(Prospect.email == "ethan@acme.example.com").one()
    link.instantly_lead_id = lead_id
    db.commit()
    return db, link.id


def test_moving_a_person_deletes_the_lead_in_instantly_and_removes_the_old_link(acq):
    from app.assistant.assistant import stop_for_move
    from acquisition.models.campaign import CampaignProspect
    from acquisition.models.prospect import Prospect

    db, link_id = _ethan_link(acq, lead_id="L1")
    client = _FakeInstantly()
    stop_for_move(db, client, link_id)
    assert ("DELETE", "/leads/L1") in [c[:2] for c in client.calls]
    assert db.get(CampaignProspect, link_id) is None  # gone from the old campaign
    assert db.query(Prospect).filter(Prospect.email == "ethan@acme.example.com").count() == 1  # person kept
    db.close()


def test_move_finds_the_lead_by_email_when_the_id_was_never_saved(acq):
    from app.assistant.assistant import stop_for_move

    db, link_id = _ethan_link(acq, lead_id=None)
    client = _FakeInstantly(items=[{"id": "L9", "email": "ETHAN@acme.example.com"}])
    stop_for_move(db, client, link_id)
    assert ("DELETE", "/leads/L9") in [c[:2] for c in client.calls]
    db.close()


def test_if_instantly_refuses_nothing_changes_locally(acq):
    import pytest as _pt
    from app.assistant.assistant import StopError, stop_for_move
    from acquisition.integrations.instantly.errors import InstantlyProviderError
    from acquisition.models.campaign import CampaignProspect

    db, link_id = _ethan_link(acq, lead_id="L1")
    with _pt.raises(StopError):
        stop_for_move(db, _FakeInstantly(fail_with=InstantlyProviderError("boom", status_code=500)), link_id)
    assert db.get(CampaignProspect, link_id) is not None
    db.close()


def _east_raj(lead_id="L-raj"):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    raj = Prospect(
        company="Acme",
        executive_name="Raj Mehta",
        email="raj@acme.example.com",
        approval_status=ApprovalStatus.APPROVED,
    )
    east = Campaign(name="east", sender_name="Me", instantly_campaign_id="ic-east")
    db.add_all([raj, east])
    db.commit()
    link = CampaignProspect(
        campaign_id=east.id,
        prospect_id=raj.id,
        outreach_status=OutreachStatus.PENDING,
        instantly_lead_id=lead_id,
    )
    db.add(link)
    db.commit()
    ids = {"east": east.id, "raj": raj.id, "link": link.id}
    db.close()
    return ids


def test_remove_from_campaign_drops_a_person_who_has_not_been_emailed(
    fake_model, acq, client_scratch, ctx_factory, monkeypatch
):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import CampaignProspect
    from acquisition.models.prospect import Prospect
    from app.assistant import assistant as agent

    ids = _east_raj()
    client = _FakeInstantly()
    monkeypatch.setattr(agent.InstantlyService, "get_api_client", lambda self: client)
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    SCRIPT[:] = [final("Remove Raj from east? They have not been emailed yet. Yes?")]
    t1 = ask(ctx, st, "remove Raj from east")
    assert "remove_from_campaign" not in [c["tool"] for c in t1["trace"]["tool_log"]]
    SCRIPT[:] = [
        call(
            "remove_from_campaign",
            email="raj@acme.example.com",
            campaign="east",
            approval_evidence="yes",
            why="User said yes.",
        ),
        final("Removed Raj from east."),
    ]
    t2 = ask(ctx, st, "yes", session_id=t1["session_id"])
    result = [c["result"] for c in t2["trace"]["tool_log"] if c["tool"] == "remove_from_campaign"][0]
    assert result == "Removed Raj from east."
    assert t2["content"] == "Removed Raj from east."
    assert ("DELETE", "/leads/L-raj") in [c[:2] for c in client.calls]
    db = SessionLocal()
    assert db.get(CampaignProspect, ids["link"]) is None
    assert db.get(Prospect, ids["raj"]).executive_name == "Raj Mehta"
    db.close()


def test_remove_from_campaign_refuses_someone_already_emailed(fake_model, acq, client_scratch, ctx_factory):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import CampaignProspect
    from acquisition.models.prospect import Prospect

    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call(
            "remove_from_campaign",
            email="ethan@acme.example.com",
            campaign="snowflak",
            approval_evidence="yes",
            why="User said yes.",
        ),
        final("Ethan has already been emailed in snowflak, so they cannot be removed from it."),
    ]
    out = ask(ctx, client_scratch.settings, "yes, remove Ethan from snowflak")
    result = [c["result"] for c in out["trace"]["tool_log"] if c["tool"] == "remove_from_campaign"][0]
    assert "already been emailed" in result
    db = SessionLocal()
    ethan = db.query(Prospect).filter(Prospect.email == "ethan@acme.example.com").one()
    assert db.query(CampaignProspect).filter(CampaignProspect.prospect_id == ethan.id).count() == 1
    db.close()


def test_remove_from_campaign_asks_yes_no_first(fake_model, acq, client_scratch, ctx_factory):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import CampaignProspect

    ids = _east_raj()
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call(
            "remove_from_campaign",
            email="raj@acme.example.com",
            campaign="east",
            approval_evidence="remove Raj from east",
            why="User asked to remove Raj.",
        ),
        final("Remove Raj from east? They have not been emailed yet. Yes?"),
    ]
    out = ask(ctx, client_scratch.settings, "remove Raj from east")
    result = [c["result"] for c in out["trace"]["tool_log"] if c["tool"] == "remove_from_campaign"][0]
    assert "not_approved" in result
    db = SessionLocal()
    assert db.get(CampaignProspect, ids["link"]) is not None
    db.close()


def test_remove_from_campaign_leaves_the_link_if_instantly_fails(
    fake_model, acq, client_scratch, ctx_factory, monkeypatch
):
    from acquisition.db.session import SessionLocal
    from acquisition.integrations.instantly.errors import InstantlyProviderError
    from acquisition.models.campaign import CampaignProspect
    from app.assistant import assistant as agent

    ids = _east_raj()
    client = _FakeInstantly(fail_with=InstantlyProviderError("boom", status_code=500))
    monkeypatch.setattr(agent.InstantlyService, "get_api_client", lambda self: client)
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call(
            "remove_from_campaign",
            email="raj@acme.example.com",
            campaign="east",
            approval_evidence="yes",
            why="User said yes.",
        ),
        final("Instantly did not remove them. Nothing was changed."),
    ]
    out = ask(ctx, client_scratch.settings, "yes")
    result = [c["result"] for c in out["trace"]["tool_log"] if c["tool"] == "remove_from_campaign"][0]
    assert "boom" in result
    db = SessionLocal()
    assert db.get(CampaignProspect, ids["link"]) is not None
    db.close()


def test_stop_for_move_sends_empty_json_and_treats_404_as_success(acq):
    from app.assistant.assistant import stop_for_move
    from acquisition.integrations.instantly.errors import InstantlyProviderError
    from acquisition.models.campaign import CampaignProspect

    db, link_id = _ethan_link(acq, lead_id="L404")
    client = _FakeInstantly(fail_with=InstantlyProviderError("gone", status_code=404))
    stop_for_move(db, client, link_id)
    assert ("DELETE", "/leads/L404", {"json": {}}) in client.calls
    assert db.get(CampaignProspect, link_id) is None
    db.close()


# ------------------------------------------------------------------------------------------ email card: blanks, details, scope

def _add_prospect(name, email):
    from acquisition.db.session import SessionLocal
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    if not db.query(Prospect).filter(Prospect.email == email).count():
        db.add(Prospect(company="Acme", executive_name=name, email=email, approval_status=ApprovalStatus.APPROVED))
        db.commit()
    db.close()


def _draft_for(ctx, st, recipients, campaign="Spring2", message="email them in a new campaign Spring2, 9 am to 5 pm", body="Hi Raj,\n\nQuick note.\n"):
    _add_prospect("Raj Mehta", "raj@acme.example.com")
    SCRIPT[:] = [
        call("update_selection", recipients=recipients, campaign_name=campaign, confirmed_new_campaign=True,
             window_start="9 am", window_end="5 pm", window_evidence="9 am to 5 pm"),
        call("propose_email", subject="Quick question", body=body),
        final("Here is the email. Approve it as is, or change something?"),
    ]
    return ask(ctx, st, message)


def test_draft_is_a_template_with_each_persons_own_details(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    out = _draft_for(ctx, client_scratch.settings, [{"email": "raj@acme.example.com"}, {"email": "nina@acme.example.com"}])
    view = out["trace"]["email_view"]
    assert "{{firstName}}" in view["template"]["body"] and "Raj" not in view["template"]["body"]
    previews = {p["email"]: p["body"] for p in view["previews"]}
    assert previews["raj@acme.example.com"].startswith("Hi Raj,")
    assert previews["nina@acme.example.com"].startswith("Hi Nina,")  # same template, her own name
    assert view["audience"]["campaign_is_new"] and not view["audience"]["scope_needed"]
    assert {p["sources"]["first_name"] for p in view["people"]} == {"database"}


def test_unknown_person_uses_the_chat_name_or_there_and_warns(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    out = _draft_for(ctx, client_scratch.settings, [
        {"email": "shub@new.example.com", "name": "Shubham Kumar"},
        {"email": "zed@new.example.com"},
    ], body="Hi there,\n\nQuick note.\n")
    view = out["trace"]["email_view"]
    by = {p["email"]: p for p in view["people"]}
    assert by["shub@new.example.com"]["first_name"] == "Shubham" and by["shub@new.example.com"]["sources"]["first_name"] == "chat"
    assert by["zed@new.example.com"]["sources"]["first_name"] == "missing"
    previews = {p["email"]: p["body"] for p in view["previews"]}
    assert previews["shub@new.example.com"].startswith("Hi Shubham,")
    assert previews["zed@new.example.com"].startswith("Hi there,")
    assert any("zed@new.example.com" in w for w in view["warnings"])


def test_asking_who_without_showing_the_contacts_is_nudged(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", campaign_name="draft5", confirmed_new_campaign=True),
        final("Campaign name draft5 is set. Who should receive it, and what hours?"),
        call("show_contacts"),
        final("Pick who should receive it, and tell me the daily hours."),
    ]
    out = ask(ctx, client_scratch.settings, "start a campaign called draft5")
    assert [c["tool"] for c in out["trace"]["tool_log"]] == ["update_selection", "show_contacts"]
    assert out["trace"]["self_check"], "the nudge should be recorded"
    assert out["trace"]["people_to_show"], "the contacts table must reach the user"


def test_a_person_who_cannot_be_found_is_not_forced_into_a_table(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"name": "Zed Nobody"}], campaign_name="draft6", confirmed_new_campaign=True),
        final("I can't find Zed Nobody. Who else should receive it?"),
    ]
    out = ask(ctx, client_scratch.settings, "email Zed Nobody in a new campaign called draft6")
    assert "show_contacts" not in [c["tool"] for c in out["trace"]["tool_log"]]
    assert len(REQUESTS) == 2


def test_a_greeting_that_lists_two_people_is_greeted_per_person(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    out = _draft_for(ctx, client_scratch.settings, [{"email": "raj@acme.example.com"}, {"email": "nina@acme.example.com"}],
                     body="Hi Nina and Raj,\n\nQuick note.\n")
    view = out["trace"]["email_view"]
    assert view["template"]["body"].startswith("Hi {{firstName}},")
    previews = {p["email"]: p["body"] for p in view["previews"]}
    assert previews["raj@acme.example.com"].startswith("Hi Raj,")
    assert previews["nina@acme.example.com"].startswith("Hi Nina,")
    for body in previews.values():
        assert " and " not in body.splitlines()[0]


def test_a_lowercase_name_typed_in_chat_still_reads_as_a_name(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    out = _draft_for(ctx, client_scratch.settings, [{"email": "shub@new.example.com", "name": "shubham kumar"}],
                     body="Hi shubham,\n\nQuick note.\n")
    view = out["trace"]["email_view"]
    assert "{{firstName}}" in view["template"]["body"]  # the typed name became a blank
    assert view["previews"][0]["body"].startswith("Hi Shubham,")
    assert view["people"][0]["first_name"] == "Shubham" and view["people"][0]["last_name"] == "Kumar"


def test_a_lowercase_name_typed_in_chat_still_reads_as_a_name(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    out = _draft_for(ctx, client_scratch.settings, [{"email": "shub@new.example.com", "name": "shubham kumar"}],
                     body="Hi shubham,\n\nQuick note.\n")
    view = out["trace"]["email_view"]
    assert "{{firstName}}" in view["template"]["body"]  # the typed name became a blank
    assert view["previews"][0]["body"].startswith("Hi Shubham,")
    assert view["people"][0]["first_name"] == "Shubham" and view["people"][0]["last_name"] == "Kumar"


def test_typing_details_for_one_person_never_reaches_the_other(fake_model, acq, client_scratch, ctx_factory):
    from app.assistant.assistant import edit_draft

    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for(ctx, client_scratch.settings, [{"email": "raj@acme.example.com"}, {"email": "nina@acme.example.com"}])["session_id"]
    holder = {}

    def mutate(meta):
        holder["view"] = edit_draft(ctx, meta, people={"nina@acme.example.com": {"first_name": "Mathew", "last_name": "Brooks"}})
        return holder["view"]

    with bound(ctx):
        chat_history.edit_latest_assistant_meta(ctx, sid, mutate)
    previews = {p["email"]: p["body"] for p in holder["view"]["previews"]}
    assert previews["nina@acme.example.com"].startswith("Hi Mathew,")
    assert previews["raj@acme.example.com"].startswith("Hi Raj,")
    assert "Mathew" not in holder["view"]["template"]["body"]
    nina = [p for p in holder["view"]["people"] if p["email"].startswith("nina")][0]
    assert nina["sources"]["first_name"] == "typed" and nina["last_name"] == "Brooks"

    # Typing the value the database already has is not an override.
    def revert(meta):
        return edit_draft(ctx, meta, people={"nina@acme.example.com": {"first_name": "Nina", "last_name": "Lee"}})

    with bound(ctx):
        view = chat_history.edit_latest_assistant_meta(ctx, sid, revert)
    nina = [p for p in view["people"] if p["email"].startswith("nina")][0]
    assert nina["sources"]["first_name"] == "database"


def test_a_name_typed_into_the_shared_text_is_flagged(fake_model, acq, client_scratch, ctx_factory):
    from app.assistant.assistant import edit_draft

    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for(ctx, client_scratch.settings, [{"email": "raj@acme.example.com"}])["session_id"]
    with bound(ctx):
        view = chat_history.edit_latest_assistant_meta(ctx, sid, lambda m: edit_draft(ctx, m, body="Hi Priya,\n\nQuick note.\n"))
    assert any("Priya" in w for w in view["warnings"])


def test_old_card_edit_turns_names_back_into_blanks(fake_model, acq, client_scratch, ctx_factory):
    from app.assistant.assistant import edit_draft

    ctx = ctx_factory(client_scratch, "admin")
    sid = _draft_for(ctx, client_scratch.settings, [{"email": "raj@acme.example.com"}])["session_id"]
    with bound(ctx):
        view = chat_history.edit_latest_assistant_meta(
            ctx, sid, lambda m: edit_draft(ctx, m, subject="Hello", body="Hi Raj Mehta,\n\nNew words.\n", rendered=True)
        )
    assert view["template"]["body"].startswith("Hi {{firstName}} {{lastName}},")


def test_editing_an_existing_campaign_email_needs_a_decision_when_others_wait(fake_model, acq, client_scratch, ctx_factory):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    snow = db.get(Campaign, acq["snow"])
    snow.send_window_start, snow.send_window_end = "09:00", "17:00"
    wendy = Prospect(company="Acme", executive_name="Wendy Waits", email="wendy@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    db.add(wendy)
    db.commit()
    db.add(CampaignProspect(campaign_id=snow.id, prospect_id=wendy.id, outreach_status=OutreachStatus.PENDING))
    db.commit()
    db.close()

    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_id=acq["snow"]),
        call("propose_email", subject="Changed subject", body="Hi Nina,\n\nBrand new words.\n"),
        final("Here is the email. Approve it as is, or change something?"),
    ]
    t1 = ask(ctx, st, "add Nina to snowflak")
    view = t1["trace"]["email_view"]
    assert view["audience"]["template_changed"] and view["audience"]["scope_needed"]
    assert [p["email"] for p in view["audience"]["also_waiting"]] == ["wendy@acme.example.com"]

    SCRIPT[:] = [call("send_approved", approval_evidence="go ahead"), final("This also changes the email for Wendy Waits. Go ahead?")]
    t2 = ask(ctx, st, "go ahead", session_id=t1["session_id"])
    sent = [c for c in t2["trace"]["tool_log"] if c["tool"] == "send_approved"][0]
    assert "needs_scope" in sent["result"] and "Wendy Waits" in sent["result"]
    assert "or keep" not in sent["result"].lower()

    SCRIPT[:] = [call("choose_email_scope", scope="keep", approval_evidence="keep it"), final("Okay, I will keep the campaign's current email.")]
    t3 = ask(ctx, st, "keep it", session_id=t1["session_id"])
    assert t3["trace"]["email_view"]["audience"]["scope"] == "keep"
    assert not t3["trace"]["email_view"]["audience"]["scope_needed"]


def test_saying_no_after_needs_scope_sends_nothing(fake_model, acq, client_scratch, ctx_factory):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    snow = db.get(Campaign, acq["snow"])
    snow.send_window_start, snow.send_window_end = "09:00", "17:00"
    wendy = Prospect(company="Acme", executive_name="Wendy Waits", email="wendy@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    db.add(wendy)
    db.commit()
    db.add(CampaignProspect(campaign_id=snow.id, prospect_id=wendy.id, outreach_status=OutreachStatus.PENDING))
    db.commit()
    db.close()

    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_id=acq["snow"]),
        call("propose_email", subject="Changed subject", body="Hi Nina,\n\nBrand new words.\n"),
        final("Here is the email. Approve it as is, or change something?"),
    ]
    t1 = ask(ctx, st, "add Nina to snowflak")
    SCRIPT[:] = [call("send_approved", approval_evidence="go ahead"), final("This also changes the email for Wendy Waits. Go ahead?")]
    t2 = ask(ctx, st, "go ahead", session_id=t1["session_id"])
    assert "needs_scope" in [c["result"] for c in t2["trace"]["tool_log"] if c["tool"] == "send_approved"][0]

    SCRIPT[:] = [final("Okay, nothing sent. Want to change the wording back to the campaign's email, or edit it more?")]
    t3 = ask(ctx, st, "no", session_id=t1["session_id"])
    tools = [c["tool"] for c in t3["trace"]["tool_log"]]
    assert "choose_email_scope" not in tools
    assert "send_approved" not in tools
    assert t3["trace"]["tool"] != "execute"
    assert t3["trace"]["email_view"]["audience"]["scope_needed"]
    assert not t3["trace"]["email_view"]["audience"]["scope"]


def test_person_details_must_come_from_the_user(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    t1 = _draft_for(ctx, st, [{"email": "zed@new.example.com"}], body="Hi there,\n\nNote.\n")
    SCRIPT[:] = [
        call("set_person_details", email="zed@new.example.com", first_name="Invented", evidence="made up words"),
        call("set_person_details", email="zed@new.example.com", first_name="Zed", company="Acme", evidence="his name is Zed and he works at Acme"),
        final("Saved."),
    ]
    t2 = ask(ctx, st, "his name is Zed and he works at Acme", session_id=t1["session_id"])
    log = t2["trace"]["tool_log"]
    assert "details_not_given_by_user" in log[0]["result"]
    person = t2["trace"]["email_view"]["people"][0]
    assert person["first_name"] == "Zed" and person["company"] == "Acme" and person["sources"]["first_name"] == "typed"


def test_what_is_typed_for_one_person_is_saved_on_that_persons_link_only(acq):
    from types import SimpleNamespace

    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import CampaignProspect
    from acquisition.models.prospect import Prospect
    from acquisition.services.lead_sync_service import LeadSyncService
    from app.assistant.assistant import _apply_person_fields

    db = SessionLocal()
    nina = db.query(Prospect).filter(Prospect.email == "nina@acme.example.com").one()
    ethan = db.query(Prospect).filter(Prospect.email == "ethan@acme.example.com").one()
    link = db.query(CampaignProspect).filter(CampaignProspect.prospect_id == ethan.id).one()
    _apply_person_fields(db, link.campaign_id, [ethan], [
        {"email": ethan.email, "first_name": "Eth", "last_name": "Hunt", "company": "Acme", "first_is_fallback": False}
    ])
    db.refresh(link)
    assert link.first_name_override == "Eth" and link.last_name_override is None and link.company_override is None
    assert ethan.executive_name == "Ethan Hunt"  # the person record is untouched

    class Fake:
        def __init__(self):
            self.sent = []

        def list_leads(self, payload):
            return {"items": []}

        def create_lead(self, payload):
            self.sent.append(payload)
            return {"id": "L1"}

    fake = Fake()
    campaign = link.campaign
    campaign.instantly_campaign_id = "ic-1"
    LeadSyncService(db, client=fake)._sync_one(fake, campaign, link)
    assert fake.sent[0]["first_name"] == "Eth" and fake.sent[0]["last_name"] == "Hunt"
    db.close()


def test_new_people_keep_their_own_names_when_several_are_sent(acq, monkeypatch, ctx_factory, client_scratch):
    from acquisition.db.session import SessionLocal
    from acquisition.models.prospect import Prospect
    from acquisition.services.lead_sync_service import LeadSyncResult
    from app.assistant import assistant as agent

    monkeypatch.setattr(agent, "sending_account_emails", lambda db=None: ["me@example.com"])
    monkeypatch.setattr(agent.InstantlyCampaignSyncService, "sync", lambda self, cid: None)
    monkeypatch.setattr(
        agent.LeadSyncService,
        "sync_campaign_leads",
        lambda self, cid: LeadSyncResult(campaign_id=cid, synced=2, failed=0, skipped=0, message="ok"),
    )
    monkeypatch.setattr(agent.CampaignActivationService, "activate", lambda self, cid, confirm=True: None)

    ctx = ctx_factory(client_scratch, "admin")
    agent.fulfill_approved_email(
        ctx,
        {
            "campaign_name": "tom-amy",
            "email_address_list": ["tom@new.example.com", "amy@new.example.com"],
            "person_name": "Tom and Amy",
            "people_fields": [
                {"email": "tom@new.example.com", "first_name": "Tom", "last_name": "", "company": "", "first_is_fallback": False},
                {"email": "amy@new.example.com", "first_name": "Amy", "last_name": "", "company": "", "first_is_fallback": False},
            ],
            "email_template": True,
            "email_subject": "Hello",
            "email_body": "Hi {{firstName}},",
            "campaign_schedule_timing": ["9:00 am", "5:00 pm"],
        },
    )
    db = SessionLocal()
    tom = db.query(Prospect).filter(Prospect.email == "tom@new.example.com").one()
    amy = db.query(Prospect).filter(Prospect.email == "amy@new.example.com").one()
    assert tom.executive_name == "Tom"
    assert amy.executive_name == "Amy"
    assert "Tom and Amy" not in {tom.executive_name, amy.executive_name}
    db.close()


def test_the_users_own_edit_is_not_overwritten_and_the_card_is_not_repeated(fake_model, acq, client_scratch, ctx_factory):
    from app.assistant.assistant import edit_draft

    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    t1 = _draft_for(ctx, st, [{"email": "nina@acme.example.com"}], body="Hi Nina,\n\nQuick note.\n")
    sid = t1["session_id"]
    assert t1["trace"]["show_email_card"] is True
    draft_id = t1["trace"]["email_view"]["draft_id"]
    assert draft_id

    # The user rewrites the shared text in the card.
    with bound(ctx):
        chat_history.edit_latest_assistant_meta(ctx, sid, lambda m: edit_draft(ctx, m, body="Hi {{firstName}},\n\nMy own words.\n"))

    # A vague message: the model wrongly tries to regenerate the draft. The platform refuses and keeps the edit.
    SCRIPT[:] = [
        call("propose_email", subject="Quick question", body="Hi there,\n\nQuick note.\n"),
        final("Would you like me to send it as is, or change something?"),
    ]
    t2 = ask(ctx, st, "hmm maybe", session_id=sid)
    assert "user_edited_draft" in t2["trace"]["tool_log"][0]["result"]
    assert t2["trace"]["show_email_card"] is False
    assert t2["trace"]["email_view"]["draft_id"] == draft_id
    assert "My own words" in t2["trace"]["email_view"]["template"]["body"]

    # A question about the draft: no new card, same draft.
    SCRIPT[:] = [final("The subject is Quick question.")]
    t3 = ask(ctx, st, "what is the subject again?", session_id=sid)
    assert t3["trace"]["show_email_card"] is False and t3["trace"]["email_view"]["draft_id"] == draft_id

    # A real change request builds on their text, passes their words, and shows a new card.
    SCRIPT[:] = [
        call("propose_email", subject="Short subject", body="Hi {{firstName}},\n\nMy own words, shorter.\n", change_evidence="make it shorter"),
        final("Here is the shorter version. Approve it, or change something?"),
    ]
    t4 = ask(ctx, st, "make it shorter", session_id=sid)
    assert t4["trace"]["show_email_card"] is True
    assert t4["trace"]["email_view"]["draft_id"] != draft_id
    assert "shorter" in t4["trace"]["email_view"]["template"]["body"]


def test_proposing_the_same_draft_again_does_not_make_a_new_card(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    t1 = _draft_for(ctx, st, [{"email": "nina@acme.example.com"}], body="Hi Nina,\n\nQuick note.\n")
    SCRIPT[:] = [
        call("propose_email", subject="Quick question", body="Hi Nina,\n\nQuick note.\n"),
        final("Approve it as is, or change something?"),
    ]
    t2 = ask(ctx, st, "is that okay?", session_id=t1["session_id"])
    assert t2["trace"]["show_email_card"] is False
    assert t2["trace"]["email_view"]["draft_id"] == t1["trace"]["email_view"]["draft_id"]


def test_new_campaign_with_identical_wording_still_gets_its_own_card(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    t1 = _draft_for(ctx, st, [{"email": "nina@acme.example.com"}], campaign="draftA", body="Hi Nina,\n\nQuick note.\n")
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "nina@acme.example.com"}], campaign_name="draftB", confirmed_new_campaign=True,
             window_start="10 am", window_end="4 pm", window_evidence="10 am and 4 pm"),
        call("propose_email", subject="Quick question", body="Hi Nina,\n\nQuick note.\n"),
        final("Approve it as is, or change something?"),
    ]
    t2 = ask(ctx, st, "email Nina in a new campaign called draftB, send between 10 am and 4 pm", session_id=t1["session_id"])
    assert t2["trace"]["campaign_name"] == "draftB"
    assert t2["trace"]["show_email_card"] is True
    assert t2["trace"]["email_view"]["draft_id"] != t1["trace"]["email_view"]["draft_id"]


def test_a_message_that_is_not_approval_never_sends(fake_model, acq, client_scratch, ctx_factory):
    ctx = ctx_factory(client_scratch, "admin")
    st = client_scratch.settings
    sid = _draft_for(ctx, st, [{"email": "nina@acme.example.com"}], campaign="NoSend")["session_id"]
    for message in ("where it is shown", "show me the email you are using", "is that okay?", "don't send yet", "no", "what is the subject again?"):
        SCRIPT[:] = [call("send_approved", approval_evidence=message), final("Do you want to send it as is, or change something?")]
        out = ask(ctx, st, message, session_id=sid)
        res = [c for c in out["trace"]["tool_log"] if c["tool"] == "send_approved"][0]["result"]
        assert "not_approved" in res, message
        assert not out["trace"]["campaign_id"], message


def test_real_approvals_pass_the_gate():
    from app.assistant.assistant import approval_problem

    for message in ("go ahead", "good", "yes send it", "looks great, ship it", "ok", "approve and send it", "Yes please", "send it", "haan bhej do"):
        assert approval_problem(message, message) == "", message
    assert approval_problem("go ahead", "something the user never wrote")


# ------------------------------------------------------------------------------------------ edit a user message


def _three_turn_campaign(ctx, settings):
    """Ethan / snowflake: pick, commit hours, then approve the move."""
    SCRIPT[:] = [
        call("update_selection", recipients=[{"email": "ethan@acme.example.com"}], why="User named Ethan."),
        call("update_selection", campaign_name="snowflake", why="User wants a new campaign called snowflake."),
        final("Do you mean the existing snowflak, or a new campaign called snowflake?"),
    ]
    t1 = ask(ctx, settings, "send Ethan an email in a new campaign snowflake")
    sid = t1["session_id"]
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
    ask(ctx, settings, "new, 9 am to 5 pm", session_id=sid)
    SCRIPT[:] = [
        call("approve_move", emails=["ethan@acme.example.com"], approval_evidence="yes", why="User said yes."),
        call("propose_email", subject="Hi", body="Hi Ethan", why="Move approved."),
        final("Here's the email. Approve it as is, or change something?"),
    ]
    ask(ctx, settings, "yes move him", session_id=sid)
    return sid


def test_edit_second_user_message_replays_from_earlier_state(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    sid = _three_turn_campaign(ctx, client_scratch.settings)
    saved = chat_history.get_session(ctx, sid)
    users = [m for m in saved["messages"] if m["role"] == "user"]
    assert [m["content"] for m in users] == [
        "send Ethan an email in a new campaign snowflake",
        "new, 9 am to 5 pm",
        "yes move him",
    ]
    second = users[1]
    assert saved["messages"][-1]["meta"]["state"].get("window") == ["9:00 am", "5:00 pm"]

    REQUESTS.clear()
    SCRIPT[:] = [
        call("update_selection", campaign_name="snowflake", confirmed_new_campaign=True, why="They said new."),
        final("What daily send hours should it use?"),
    ]
    admin = _token(http, client_scratch, "admin")
    r = http.post(
        f"/api/chat/sessions/{sid}/edit",
        json={"message_id": second["id"], "message": "new campaign, still deciding hours"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "What daily send hours should it use?"

    after = chat_history.get_session(ctx, sid)
    assert [m["role"] for m in after["messages"]] == ["user", "assistant", "user", "assistant"]
    assert after["messages"][2]["content"] == "new campaign, still deciding hours"
    assert after["messages"][3]["content"] == "What daily send hours should it use?"
    assert all(m["content"] != "yes move him" for m in after["messages"])
    # Hours were committed on the edited turn. update_selection without a window keeps
    # whatever was already in state — so an empty window means we loaded turn 1, not turn 2.
    assert after["messages"][-1]["meta"]["state"].get("window") in ([], None)
    prompt = "\n".join(
        m["content"] if isinstance(m.get("content"), str) else ""
        for m in REQUESTS[0]["messages"]
    )
    state_view = prompt.split("Current state:", 1)[-1]
    assert '"daily_window": []' in state_view
    assert "9:00 am" not in state_view and "5:00 pm" not in state_view


def test_edit_someone_elses_session_is_404(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [final("hi")]
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
    SCRIPT[:] = [final("hi")]
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
    SCRIPT[:] = [final("hi")]
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


def test_edit_first_message_starts_with_empty_state(fake_model, acq, client_scratch, ctx_factory, http):
    ctx = ctx_factory(client_scratch, "admin")
    SCRIPT[:] = [
        call(
            "update_selection",
            recipients=[{"email": "nina@acme.example.com"}],
            campaign_name="Fresh",
            confirmed_new_campaign=True,
            window_start="9 am",
            window_end="5 pm",
            window_evidence="9 am to 5 pm",
            why="x",
        ),
        call("propose_email", subject="s", body="b", why="x"),
        final("Here's the email. Approve it?"),
    ]
    t1 = ask(ctx, client_scratch.settings, "email Nina in Fresh, 9 am to 5 pm")
    SCRIPT[:] = [final("Sure.")]
    ask(ctx, client_scratch.settings, "thanks", session_id=t1["session_id"])
    saved = chat_history.get_session(ctx, t1["session_id"])
    first_id = saved["messages"][0]["id"]
    assert saved["messages"][-1]["meta"]["state"].get("window") == ["9:00 am", "5:00 pm"]

    REQUESTS.clear()
    SCRIPT[:] = [final("Who should this go to?")]
    admin = _token(http, client_scratch, "admin")
    r = http.post(
        f"/api/chat/sessions/{t1['session_id']}/edit",
        json={"message_id": first_id, "message": "I want to email someone"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "Who should this go to?"
    after = chat_history.get_session(ctx, t1["session_id"])
    assert [m["content"] for m in after["messages"]] == ["I want to email someone", "Who should this go to?"]
    state = after["messages"][-1]["meta"]["state"]
    assert not state.get("window")
    assert not state.get("recipients")
    prompt = "\n".join(
        m["content"] if isinstance(m.get("content"), str) else ""
        for m in REQUESTS[0]["messages"]
    )
    state_view = prompt.split("Current state:", 1)[-1]
    assert '"daily_window": []' in state_view
    assert '"recipients": []' in state_view
    assert "9:00 am" not in state_view
