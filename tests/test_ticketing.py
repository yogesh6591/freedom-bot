"""Ticketing and escalation: raising (flag, form, chat), routing, the Cepoch and Jeanne desks,
reassignment, notifications, service records and what clients never see.

Nothing here reaches a model or an external system: the chat path calls the tool the assistant uses,
through the same policy check.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from freedombot import chat_history, support
from freedombot.control import store as control
from freedombot.support_routing import BUSINESS, TECHNICAL, classify
from freedombot.tenancy.context import tenant_scope
from freedombot.tools.base import RunScope, guarded_call
from freedombot.types import PolicyEffect, Role

DEMO_PASSWORD = "bizos-dev-password"
STAFF_PASSWORD = "test-only-password-1"


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _login(app_client, email: str, slug: str, password: str = DEMO_PASSWORD) -> dict[str, str]:
    response = app_client.post("/api/auth/login", json={"email": email, "password": password, "client_slug": slug})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _demo(app_client, client, prefix: str) -> dict[str, str]:
    return _login(app_client, f"{prefix}@{client.slug}.example.com", client.slug)


@pytest.fixture(scope="module")
def staff(app_client, client_scratch):
    """A Cepoch and a Jeanne support account (in their own workspace, as in Cepoch's internal one)."""
    out = {}
    for team in ("CEPOCH", "JEANNE"):
        email = f"{team.lower()}-{uuid.uuid4().hex[:6]}@cepoch.example.com"
        user = control.create_user(client_id=client_scratch.id, email=email, password=STAFF_PASSWORD,
                                   display_name=team.title(), roles=(Role.ADMIN,), created_by="pytest")
        control.set_support_team(user.id, team)
        out[team] = {"user": control.get_user(user.id),
                     "headers": _login(app_client, email, client_scratch.slug, STAFF_PASSWORD)}
    return out


def _subject(text: str) -> str:
    return f"{text} {uuid.uuid4().hex[:6]}"


def _chat(ctx, question: str, answer: str) -> tuple[str, str]:
    """Save one chat turn for ``ctx``; returns (session id, the assistant message id)."""
    session_id = f"s-{uuid.uuid4().hex[:10]}"
    with tenant_scope(ctx):
        chat_history.record_turn(ctx, session_id=session_id, user_message=question, reply=answer,
                                 domain="general", meta={})
        messages = chat_history.get_session(ctx, session_id)["messages"]
    return session_id, next(m["id"] for m in messages if m["role"] == "assistant")


# --------------------------------------------------------------------------- 1 and 13: flag an answer


def test_flagging_an_answer_opens_one_ticket_with_the_chat_attached(app_client, client_a, ctx_factory, staff):
    viewer = _demo(app_client, client_a, "viewer")
    ctx = ctx_factory(client_a, "viewer")
    session_id, message_id = _chat(ctx, "What is our refund window?", "Our refund window is 400 days.")
    first = app_client.post("/api/support/flag", json={"session_id": session_id, "message_id": message_id,
                                                         "comment": "That is wrong"}, headers=viewer)
    assert first.status_code == 200, first.text
    raised = first.json()
    assert raised["merged"] is False and raised["ticket"]["status"] == "OPEN"

    ticket = support.get_ticket(raised["ticket"]["id"])
    assert ticket["source"] == "AI_FLAG" and ticket["category"] == TECHNICAL and ticket["assignee"] == "CEPOCH"
    assert ticket["context"]["question"] == "What is our refund window?"
    assert ticket["context"]["flagged_answer"] == "Our refund window is 400 days."
    assert ticket["context"]["workspace"]["slug"] == client_a.slug
    assert ticket["context"]["user"]["email"] == ctx.email

    # 13: flagging the same answer again (a retry, a double click) is the same ticket.
    again = app_client.post("/api/support/flag", json={"session_id": session_id, "message_id": message_id},
                            headers=viewer).json()
    assert again["merged"] is True and again["ticket"]["id"] == ticket["id"]


def test_nobody_can_flag_someone_elses_conversation(app_client, client_a, ctx_factory):
    session_id, message_id = _chat(ctx_factory(client_a, "viewer"), "Hello", "Hi")
    drafter = _demo(app_client, client_a, "drafter")
    response = app_client.post("/api/support/flag", json={"session_id": session_id, "message_id": message_id},
                               headers=drafter)
    assert response.status_code == 400


# --------------------------------------------------------------------------- 2: the Escalate form


def test_the_escalate_form_opens_a_ticket_with_the_conversation(app_client, client_a, ctx_factory):
    viewer = _demo(app_client, client_a, "viewer")
    session_id, _ = _chat(ctx_factory(client_a, "viewer"), "Connect my Gmail", "Gmail failed to connect.")
    response = app_client.post("/api/support/tickets", json={
        "subject": _subject("Gmail integration keeps failing"), "body": "Tried three times.", "session_id": session_id,
    }, headers=viewer)
    assert response.status_code == 200, response.text
    ticket = support.get_ticket(response.json()["ticket"]["id"])
    assert ticket["source"] == "MANUAL" and ticket["assignee"] == "CEPOCH"
    assert [m["content"] for m in ticket["context"]["conversation"]] == ["Connect my Gmail", "Gmail failed to connect."]


# --------------------------------------------------------------------------- 3: the assistant escalates


def test_the_assistant_raises_a_ticket_through_its_tool(client_a, ctx_factory):
    ctx = ctx_factory(client_a, "viewer")
    session_id, _ = _chat(ctx, "Escalate this to support", "Sure.")
    scope = RunScope(ctx=ctx, settings=client_a.settings, session_id=session_id)
    subject = _subject("Need help changing our subscription plan at renewal")
    with tenant_scope(ctx):
        outcome = guarded_call(scope, "support_create_ticket",
                               {"subject": subject, "body": "Billing wants a cheaper plan.", "category": "business"})
    assert outcome.ok, outcome.message
    assert outcome.data["id"].startswith("sup") and "opened support ticket" in outcome.message
    # The reply the assistant builds from this never names the team.
    assert "Jeanne" not in outcome.message and "Cepoch" not in outcome.message
    ticket = support.get_ticket(outcome.data["id"])
    assert ticket["source"] == "CHAT" and ticket["category"] == BUSINESS and ticket["assignee"] == "JEANNE"


# --------------------------------------------------------------------------- 4, 5: routing


@pytest.mark.parametrize("subject, body, team, triage", [
    ("Login fails with an error", "", "CEPOCH", False),
    ("HubSpot integration API timeout", "", "CEPOCH", False),
    ("Invoice and subscription renewal question", "needs business approval", "JEANNE", False),
    ("Onboarding call and contract for our new department", "", "JEANNE", False),
    ("Quick question", "something about the thing", "CEPOCH", True),
])
def test_technical_goes_to_cepoch_business_to_jeanne_unclear_to_triage(client_a, subject, body, team, triage):
    ticket = support.create_ticket(client_id=client_a.id, subject=_subject(subject), body=body, created_by="pytest")
    assert ticket["assignee"] == team and ticket["triage"] is triage


def test_a_flagged_answer_is_technical_whatever_it_says():
    assert classify("refund invoice billing", source="AI_FLAG").category == TECHNICAL


# --------------------------------------------------------------------------- 6, 7, 8, 10: desks, reassignment, notices


def test_the_two_desks_reassign_one_ticket_and_keep_its_history(app_client, client_a, staff):
    cepoch, jeanne = staff["CEPOCH"]["headers"], staff["JEANNE"]["headers"]
    ticket = support.create_ticket(client_id=client_a.id, subject=_subject("Login error on the approvals page"),
                                   created_by="pytest")
    assert ticket["assignee"] == "CEPOCH"

    # 8: the team it was routed to is told; the other team is not.
    assert any(n["ticket_id"] == ticket["id"] for n in
               app_client.get("/api/support/notifications", headers=cepoch).json()["items"])
    assert all(n["ticket_id"] != ticket["id"] for n in
               app_client.get("/api/support/notifications", headers=jeanne).json()["items"])

    # Cepoch's default view has it; Jeanne's does not.
    assert any(t["id"] == ticket["id"] for t in app_client.get("/api/support/tickets", headers=cepoch).json()["items"])
    assert all(t["id"] != ticket["id"] for t in app_client.get("/api/support/tickets", headers=jeanne).json()["items"])

    note = app_client.post(f"/api/support/tickets/{ticket['id']}/notes", json={"note": "Checked logs"}, headers=cepoch)
    assert note.status_code == 200

    # 6: Cepoch hands it to Jeanne. Same ticket, no copy; Jeanne is told.
    moved = app_client.post(f"/api/support/tickets/{ticket['id']}/reassign",
                            json={"team": "JEANNE", "note": "This is a contract question"}, headers=cepoch)
    assert moved.status_code == 200, moved.text
    assert moved.json()["ticket"]["id"] == ticket["id"] and moved.json()["ticket"]["assignee"] == "JEANNE"
    assert any(n["ticket_id"] == ticket["id"] and n["kind"] == "reassigned"
               for n in app_client.get("/api/support/notifications", headers=jeanne).json()["items"])

    # 7: Jeanne hands it back.
    back = app_client.post(f"/api/support/tickets/{ticket['id']}/reassign",
                           json={"team": "CEPOCH", "note": "Actually a login bug"}, headers=jeanne).json()["ticket"]
    assert back["assignee"] == "CEPOCH"
    assert len(support.list_tickets(client_id=client_a.id, created_by="pytest")) == len(
        {t["id"] for t in support.list_tickets(client_id=client_a.id, created_by="pytest")})

    # 10: everything that happened is still there, in order.
    events = [h["event"] for h in back["history"]]
    assert events[:4] == ["created", "note", "reassigned", "reassigned"]
    assert back["context"] == ticket["context"]

    # Both can see the complete history.
    assert any(t["id"] == ticket["id"] for t in app_client.get("/api/support/tickets?view=all", headers=jeanne).json()["items"])


# --------------------------------------------------------------------------- 9: status


def test_status_moves_through_open_in_progress_resolved_closed(app_client, client_a, staff):
    cepoch = staff["CEPOCH"]["headers"]
    ticket = support.create_ticket(client_id=client_a.id, subject=_subject("Sync error"), created_by="pytest")
    path = f"/api/support/tickets/{ticket['id']}/status"
    assert app_client.post(path, json={"status": "IN_PROGRESS"}, headers=cepoch).json()["ticket"]["status"] == "IN_PROGRESS"
    # Resolving needs a resolution: it is what the client sees.
    assert app_client.post(path, json={"status": "RESOLVED"}, headers=cepoch).status_code == 400
    done = app_client.post(path, json={"status": "RESOLVED", "resolution": "Reconnected Gmail."}, headers=cepoch)
    assert done.json()["ticket"]["status"] == "RESOLVED" and done.json()["ticket"]["resolution"] == "Reconnected Gmail."
    assert app_client.post(path, json={"status": "CLOSED"}, headers=cepoch).json()["ticket"]["status"] == "CLOSED"
    assert app_client.post(path, json={"status": "WAITING"}, headers=cepoch).status_code == 400


# --------------------------------------------------------------------------- 11: service records


def test_jeanne_gets_a_clients_service_records_and_nobody_else_does(client_a, client_scratch, staff, ctx_factory):
    open_one = support.create_ticket(client_id=client_a.id, subject=_subject("Subscription billing question"),
                                     created_by="pytest")
    done = support.create_ticket(client_id=client_a.id, subject=_subject("Login error"), created_by="pytest")
    support.set_status(ticket_id=done["id"], status="RESOLVED", by="pytest", resolution="Password reset sent.")
    support.reassign(ticket_id=open_one["id"], team="CEPOCH", by="pytest", note="billing system bug")

    jeanne = control.build_context(staff["JEANNE"]["user"])
    with tenant_scope(jeanne):
        outcome = guarded_call(RunScope(ctx=jeanne, settings=client_scratch.settings), "support_client_records",
                               {"client": client_a.slug})
    assert outcome.ok, outcome.message
    records = outcome.data
    assert records["client"]["slug"] == client_a.slug
    assert any(t["id"] == open_one["id"] for t in records["open"])
    assert any(t["id"] == done["id"] and t["resolution"] == "Password reset sent." for t in records["resolved"])
    assert any(e["ticket"] == open_one["id"] for e in records["escalations"])

    # A client's own admin cannot read support records, theirs or anyone's.
    admin = ctx_factory(client_a, "admin")
    with tenant_scope(admin):
        refused = guarded_call(RunScope(ctx=admin, settings=client_a.settings), "support_client_records",
                               {"client": client_a.slug})
    assert not refused.ok and refused.effect == PolicyEffect.DENY


# --------------------------------------------------------------------------- 12, 14: isolation and privacy


def test_one_client_cannot_see_another_clients_ticket(app_client, client_a, client_b):
    raised = app_client.post("/api/support/tickets", json={"subject": _subject("Export broken")},
                             headers=_demo(app_client, client_a, "viewer")).json()["ticket"]
    other = _demo(app_client, client_b, "admin")
    assert app_client.get(f"/api/support/tickets/{raised['id']}", headers=other).status_code == 404
    assert all(t["id"] != raised["id"] for t in
               app_client.get("/api/support/tickets?view=all", headers=other).json()["items"])
    assert app_client.post(f"/api/support/tickets/{raised['id']}/reassign", json={"team": "JEANNE"},
                           headers=other).status_code == 403


def test_clients_never_see_internal_notes_routing_or_history(app_client, client_a, staff):
    viewer = _demo(app_client, client_a, "viewer")
    raised = app_client.post("/api/support/tickets", json={"subject": _subject("Invoice renewal question")},
                             headers=viewer).json()["ticket"]
    jeanne = staff["JEANNE"]["headers"]
    secret = f"internal-{uuid.uuid4().hex[:6]}"
    app_client.post(f"/api/support/tickets/{raised['id']}/notes", json={"note": secret}, headers=jeanne)
    app_client.post(f"/api/support/tickets/{raised['id']}/status",
                    json={"status": "RESOLVED", "resolution": "Plan changed at renewal."}, headers=jeanne)

    for response in (app_client.get(f"/api/support/tickets/{raised['id']}", headers=viewer),
                     app_client.get("/api/support/tickets", headers=viewer)):
        text = response.text
        assert secret not in text and "JEANNE" not in text and "Jeanne" not in text
        assert '"history"' not in text and '"category"' not in text and '"context"' not in text
    mine = app_client.get(f"/api/support/tickets/{raised['id']}", headers=viewer).json()["ticket"]
    assert mine["status"] == "RESOLVED" and mine["resolution"] == "Plan changed at renewal."


# --------------------------------------------------------------------------- staff recognised at once; the queue in chat


def test_the_two_support_accounts_are_recognised_without_a_restart():
    from types import SimpleNamespace

    def untagged(email):
        return SimpleNamespace(email=email, support_team=None)

    assert support.team_of(untagged("Yogesh@cepoch.com"), "cepoch") == "CEPOCH"
    assert support.team_of(untagged("jeanne@cepoch.com"), "cepoch") == "JEANNE"
    # The same address in a client's own workspace is nobody special.
    assert support.team_of(untagged("yogesh@cepoch.com"), "acme") is None
    assert support.team_of(untagged("amelia@acme.com"), "cepoch") is None


def test_cepoch_sees_a_flagged_client_answer_in_its_queue_from_chat(client_a, client_scratch, ctx_factory, staff):
    ctx = ctx_factory(client_a, "admin")
    session_id, message_id = _chat(ctx, "add in memory rapido code is 778332", "Saved.")
    scope = RunScope(ctx=ctx, settings=client_a.settings, session_id=session_id)
    with tenant_scope(ctx):
        raised = guarded_call(scope, "support_create_ticket", {"source": "AI_FLAG", "message_id": message_id})
    assert raised.ok, raised.message

    cepoch = control.build_context(staff["CEPOCH"]["user"])
    with tenant_scope(cepoch):
        queue = guarded_call(RunScope(ctx=cepoch, settings=client_scratch.settings), "support_queue", {})
    assert queue.ok and any(row["id"] == raised.data["id"] for row in queue.data)
    # "My tickets" means the same queue for support staff, whichever tool the assistant picks.
    with tenant_scope(cepoch):
        mine = guarded_call(RunScope(ctx=cepoch, settings=client_scratch.settings), "support_my_tickets", {})
    assert mine.ok and any(row["id"] == raised.data["id"] for row in mine.data)

    with tenant_scope(ctx):
        refused = guarded_call(RunScope(ctx=ctx, settings=client_a.settings), "support_queue", {})
    assert not refused.ok and refused.effect == PolicyEffect.DENY
