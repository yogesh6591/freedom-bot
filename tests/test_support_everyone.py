"""Everyone signed in can raise a support ticket from the Support page, not only from the chat.

Only Cepoch's support staff work the queue (tests/test_ticketing.py). Everyone else, a workspace's
operators and admins included, sees only the tickets they raised, as support_my_tickets shows them.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from freedombot.types import PolicyEffect


@pytest.fixture(scope="module")
def app_client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _auth(app_client, client, prefix: str) -> dict[str, str]:
    response = app_client.post(
        "/api/auth/login",
        json={
            "email": f"{prefix}@{client.slug}.example.com",
            "password": "bizos-dev-password",
            "client_slug": client.slug,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _subject(text: str) -> str:
    return f"{text} {uuid.uuid4().hex[:8]}"


@pytest.mark.parametrize("prefix", ["viewer", "drafter", "approver"])
def test_every_role_can_raise_a_ticket_from_the_support_page(app_client, client_a, prefix):
    headers = _auth(app_client, client_a, prefix)
    subject = _subject(f"Export button does nothing for {prefix}")
    response = app_client.post(
        "/api/support/tickets", json={"subject": subject, "body": "Tried twice."}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["merged"] is False and body["message"] == f"Ticket {body['ticket']['id']} opened with FreedomBot support."
    ticket = body["ticket"]
    # The raiser never learns which team has it.
    assert ticket["subject"] == subject and ticket["handled_by"] == "FreedomBot support"
    # The raiser sees status and the latest update, not the queue fields support works with.
    assert "assignee" not in ticket and "history" not in ticket and "escalate_reason" not in ticket

    mine = app_client.get("/api/support/tickets", headers=headers).json()
    assert mine["can_manage"] is False
    assert any(t["id"] == ticket["id"] for t in mine["items"])


def test_non_staff_see_only_their_own_tickets_and_cannot_work_the_queue(app_client, client_a):
    viewer = _auth(app_client, client_a, "viewer")
    drafter = _auth(app_client, client_a, "drafter")
    raised = app_client.post(
        "/api/support/tickets", json={"subject": _subject("Login page is slow")}, headers=viewer
    ).json()["ticket"]

    # Queue filters are ignored for non-staff: still only their own tickets.
    for path in ("/api/support/tickets", "/api/support/tickets?needs_jeanne=true",
                 "/api/support/tickets?assignee=CEPOCH"):
        listed = app_client.get(path, headers=drafter).json()["items"]
        assert all(t["id"] != raised["id"] for t in listed)

    ticket_id = raised["id"]
    # The raiser may open their own ticket (status only); anyone else in the workspace may not.
    own = app_client.get(f"/api/support/tickets/{ticket_id}", headers=viewer)
    assert own.status_code == 200 and "history" not in own.json()["ticket"]
    assert app_client.get(f"/api/support/tickets/{ticket_id}", headers=drafter).status_code == 404
    assert app_client.post(f"/api/support/tickets/{ticket_id}/notes", json={"note": "hi"},
                           headers=viewer).status_code == 403
    assert app_client.post(f"/api/support/tickets/{ticket_id}/status", json={"status": "RESOLVED"},
                           headers=viewer).status_code == 403
    assert app_client.post(f"/api/support/tickets/{ticket_id}/escalate", json={"reason": "AT_RISK"},
                           headers=viewer).status_code == 403


def test_workspace_operators_and_admins_do_not_work_the_queue(app_client, client_a):
    """Tickets reach only Cepoch and Jeanne, never the workspace's own admins or operators."""
    viewer = _auth(app_client, client_a, "viewer")
    raised = app_client.post(
        "/api/support/tickets", json={"subject": _subject("Chat shows a blank screen"), "severity": "HIGH"},
        headers=viewer,
    ).json()["ticket"]
    for prefix in ("operator", "admin"):
        staff = _auth(app_client, client_a, prefix)
        queue = app_client.get("/api/support/tickets?view=all", headers=staff).json()
        assert queue["can_manage"] is False
        assert all(t["id"] != raised["id"] for t in queue["items"])
        assert app_client.post(f"/api/support/tickets/{raised['id']}/notes", json={"note": "x"},
                               headers=staff).status_code == 403


def test_same_subject_again_is_merged(app_client, client_a):
    viewer = _auth(app_client, client_a, "viewer")
    subject = _subject("CSV export fails")
    first = app_client.post("/api/support/tickets", json={"subject": subject}, headers=viewer).json()
    second = app_client.post("/api/support/tickets", json={"subject": subject, "body": "Still broken."},
                             headers=viewer).json()
    assert second["merged"] is True and second["ticket"]["id"] == first["ticket"]["id"]
    assert "added to it" in second["message"]


def test_the_page_goes_through_the_policy_check(app_client, client_a, monkeypatch):
    import app.api.support as support_api
    from freedombot import support

    calls = []

    def refused(scope, tool, arguments):
        calls.append((scope.ctx.user_id, tool, arguments))
        return SimpleNamespace(ok=False, effect=PolicyEffect.DENY, message="Not permitted here.", data=None)

    monkeypatch.setattr(support_api, "guarded_call", refused)
    viewer = _auth(app_client, client_a, "viewer")
    subject = _subject("Refused by policy")
    response = app_client.post("/api/support/tickets", json={"subject": subject}, headers=viewer)
    assert response.status_code == 403 and response.json()["detail"] == "Not permitted here."
    assert calls and calls[0][1] == "support_create_ticket" and calls[0][2]["subject"] == subject
    assert all(t["subject"] != subject for t in support.list_tickets(client_id=client_a.id))


def test_signed_out_callers_cannot_raise_tickets(client_a):
    from app.main import app

    with TestClient(app) as anonymous:
        response = anonymous.post("/api/support/tickets", json={"subject": "anonymous ticket"})
    assert response.status_code == 401
