"""Campaign assistant against the REAL model. Off by default.

Why: the other tests script the model, so they prove the code is safe but not how the real model behaves.
This file talks to the model configured in your .env (MODEL_ID, MODEL_REASONING_EFFORT, OPENAI_API_KEY).

Safe by design:
  * the throwaway `acquisition_test` database is used (tests/conftest.py forces it);
  * the one function that creates and sends a campaign is replaced by a recorder, so NOTHING is sent,
    created or deleted in Instantly. Everything before it (the approval gate, the move rules, the hours
    check, the email card) runs for real.

Run (from the project root):
    RUN_REAL_MODEL=1 python -m pytest tests/test_real_model.py -s -p no:cacheprovider
    REAL_REPEATS=3 ...   # run every scenario 3 times: a model can pass once and fail the next time

Result: pass/fail per scenario in the terminal, and the full conversation of every scenario in
`real_model_report.md` (send that file back when something fails).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_unified_assistant import _add_prospect, acq, ask  # noqa: F401  (acq is a fixture)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_MODEL") != "1",
    reason="set RUN_REAL_MODEL=1 to talk to the real model",
)

REPEATS = max(1, int(os.environ.get("REAL_REPEATS", "1") or 1))
REPORT = Path(os.environ.get("REAL_REPORT", "real_model_report.md"))
_LOG: list[dict] = []
_CURRENT: dict = {}


# ------------------------------------------------------------------------------------------ plumbing


@pytest.fixture(scope="module", autouse=True)
def _real_model_env():
    """Use the real model with the .env settings; reasoning defaults to medium, like the app."""
    os.environ.setdefault("MODEL_REASONING_EFFORT", "medium")
    # A scripted-model fixture from another module may have left its URL behind: never talk to it.
    if "127.0.0.1" in (os.environ.get("OPENAI_BASE_URL") or ""):
        os.environ.pop("OPENAI_BASE_URL", None)
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set (put it in .env)")
    from bizos import settings as app_settings

    app_settings.llm_available.cache_clear()
    yield
    _write_report()


@pytest.fixture
def sent(monkeypatch):
    """Replaces the function that creates + sends the campaign. Records every attempt; sends nothing."""
    from app.assistant import assistant as agent

    calls: list[dict] = []

    def fake(ctx, parsed):
        calls.append(parsed)
        return agent.FulfillResult(campaign_id="00000000-0000-0000-0000-000000000001", message="(test) campaign created and sent")

    monkeypatch.setattr(agent, "fulfill_approved_email", fake)
    return calls


class Chat:
    """One conversation. `say()` sends a user message and records everything for the report."""

    def __init__(self, client, ctx_factory, sent):
        self.ctx = ctx_factory(client, "admin")
        self.settings = client.settings
        self.sent = sent
        self.sid = None
        self.turns: list[dict] = []
        self.out: dict = {}

    def say(self, message: str) -> dict:
        before = len(self.sent)
        out = ask(self.ctx, self.settings, message, session_id=self.sid)
        self.sid = out["session_id"]
        trace = out.get("trace") or {}
        log = trace.get("tool_log") or []
        turn = {
            "user": message,
            "reply": out.get("content", ""),
            "tools": [f"{c['tool']}" + ("" if str(c.get("result", "")).lower().startswith(("ok", "{\"ok", "sent")) else " (refused/other)") for c in log],
            "tool_log": log,
            "guardrails": trace.get("guardrails") or [],
            "self_check": trace.get("self_check") or [],
            "show_email_card": bool(trace.get("show_email_card")),
            "email_view": trace.get("email_view") or {},
            "sends_this_turn": len(self.sent) - before,
        }
        self.turns.append(turn)
        self.out = out
        _CURRENT.setdefault("turns", []).append(turn)
        return turn

    @staticmethod
    def tools(turn: dict) -> list[str]:
        return [c["tool"] for c in turn["tool_log"]]

    @staticmethod
    def result(turn: dict, tool: str) -> str:
        return " | ".join(str(c.get("result", "")) for c in turn["tool_log"] if c["tool"] == tool)


@pytest.fixture
def chat(acq, client_scratch, ctx_factory, sent, request):
    _CURRENT.clear()
    _CURRENT["name"] = request.node.name
    c = Chat(client_scratch, ctx_factory, sent)
    yield c
    _LOG.append({"name": request.node.name, "turns": list(_CURRENT.get("turns", [])), "sent": len(sent)})


def _write_report():
    lines = [
        "# Real-model campaign run",
        f"- when: {datetime.now().isoformat(timespec='seconds')}",
        f"- model: {os.environ.get('MODEL_ID', '(default)')}   reasoning: {os.environ.get('MODEL_REASONING_EFFORT', '')}",
        "- nothing was sent: the send function was replaced by a recorder",
        "",
    ]
    for item in _LOG:
        lines.append(f"## {item['name']}   (real sends recorded: {item['sent']})")
        for i, t in enumerate(item["turns"], 1):
            lines += [
                f"**Turn {i} - you:** {t['user']}",
                f"**bot:** {t['reply']}",
                f"- tools: {', '.join(t['tools']) or 'none'}",
                f"- card shown: {t['show_email_card']}   sends this turn: {t['sends_this_turn']}",
            ]
            if t["guardrails"]:
                lines.append(f"- guardrails fired: {t['guardrails']}")
            if t["self_check"]:
                lines.append(f"- self-check nudges: {t['self_check']}")
            lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {REPORT.resolve()}")


def _template(turn: dict) -> dict:
    return (turn["email_view"] or {}).get("template") or {}


def _previews(turn: dict) -> dict:
    return {p["email"]: p for p in (turn["email_view"] or {}).get("previews") or []}


_ASK_PERSON = "email Nina in a new campaign called Spring9, send between 9 am and 5 pm"


def _draft(chat: Chat) -> dict:
    t = chat.say(_ASK_PERSON)
    assert t["show_email_card"], f"no email card was shown. bot said: {t['reply']!r}; tools: {t['tools']}"
    assert not t["sends_this_turn"], "it sent on the first message"
    return t


# ------------------------------------------------------------------------------------------ scenarios


@pytest.mark.parametrize("rep", range(REPEATS))
def test_01_a_new_campaign_shows_one_card_and_sends_nothing(chat, rep):
    t = _draft(chat)
    assert "{{firstName}}" in _template(t).get("body", ""), "the greeting should be a blank, not a fixed name"
    assert _previews(t)["nina@acme.example.com"]["body"].lstrip().startswith(("Hi Nina", "Hello Nina", "Dear Nina")), _previews(t)
    assert "propose_email" in chat.tools(t)
    assert not any(x in chat.tools(t) for x in ("send_approved",))


@pytest.mark.parametrize("rep", range(REPEATS))
def test_02_a_question_about_the_draft_makes_no_new_card_and_no_send(chat, rep):
    _draft(chat)
    for q in ("what is the subject again?", "where is it shown?", "is that okay?"):
        t = chat.say(q)
        assert not t["sends_this_turn"], f"SENT on a question: {q!r}"
        assert "send_approved" not in chat.tools(t) or "not_approved" in chat.result(t, "send_approved"), (q, t["tools"])
        assert not t["show_email_card"], f"the card was shown again for {q!r}"
        assert "propose_email" not in chat.tools(t), f"it rebuilt the draft for the question {q!r}"


@pytest.mark.parametrize("rep", range(REPEATS))
def test_03_shorter_subject_changes_only_the_subject(chat, rep):
    t1 = _draft(chat)
    old = _template(t1)
    t2 = chat.say("make the subject shorter")
    new = _template(t2)
    assert t2["show_email_card"], "the changed email should be shown again"
    assert len(new["subject"]) < len(old["subject"]), (old["subject"], new["subject"])
    assert new["body"] == old["body"], "the body must not change when only the subject was asked for"
    assert not t2["sends_this_turn"]


@pytest.mark.parametrize("rep", range(REPEATS))
@pytest.mark.parametrize("word", ["hmm", "maybe", "wait", "not sure", "what does this do?", "no"])
def test_04_words_that_are_not_approval_never_send(chat, rep, word):
    _draft(chat)
    t = chat.say(word)
    assert not t["sends_this_turn"], f"SENT on {word!r}"
    assert not chat.sent


@pytest.mark.parametrize("rep", range(REPEATS))
@pytest.mark.parametrize("word", ["yes", "go ahead", "good", "looks good, send it"])
def test_05_real_approval_sends_exactly_once(chat, rep, word):
    _draft(chat)
    t = chat.say(word)
    assert t["sends_this_turn"] == 1, f"{word!r} did not send. bot said: {t['reply']!r}; tools: {t['tools']}"
    assert len(chat.sent) == 1
    again = chat.say("thanks")
    assert not again["sends_this_turn"] and len(chat.sent) == 1, "it sent a second time"


@pytest.mark.parametrize("rep", range(REPEATS))
def test_06_the_bot_never_claims_a_send_that_did_not_happen(chat, rep):
    _draft(chat)
    for msg in ("hmm", "ok but what is the subject", "wait"):
        t = chat.say(msg)
        if not t["sends_this_turn"]:
            assert not re.search(r"\b(i('| ha)ve sent|was sent|has been sent|is on its way|campaign (was )?created)\b", t["reply"], re.I), t["reply"]


@pytest.mark.parametrize("rep", range(REPEATS))
def test_07_hours_are_never_invented(chat, rep):
    t = chat.say("email Nina in a new campaign called Spring9")
    assert not t["sends_this_turn"]
    assert not t["show_email_card"], "it built an email without knowing the hours"
    assert re.search(r"\b(hour|time|window|when|between)\b", t["reply"], re.I), f"it did not ask for the hours: {t['reply']!r}"


@pytest.mark.parametrize("rep", range(REPEATS))
def test_08_overnight_hours_are_refused_up_front(chat, rep):
    t = chat.say("email Nina in a new campaign called Spring9, send between 5 pm and 6 am")
    assert not t["sends_this_turn"]
    assert not t["show_email_card"], "an email was shown for hours that end before they start"
    result = chat.result(t, "update_selection")
    assert "window_start_not_before_end" in result or not result, result


@pytest.mark.parametrize("rep", range(REPEATS))
def test_09_a_name_close_to_an_existing_campaign_asks_first(chat, rep):
    t = chat.say("email Nina in a campaign called snowflake, send between 9 am and 5 pm")
    assert not t["sends_this_turn"]
    assert "snowflak" in t["reply"].lower(), t["reply"]
    assert not t["show_email_card"], "it built an email before asking existing-or-new"


@pytest.mark.parametrize("rep", range(REPEATS))
def test_10_moving_someone_asks_once_then_goes_on_without_asking_again(chat, rep):
    t1 = chat.say("email Ethan in a new campaign called Spring9, send between 9 am and 5 pm")
    assert not t1["sends_this_turn"]
    assert "approve_move" not in chat.tools(t1), "it approved a move the user never agreed to"
    assert re.search(r"snowflak", t1["reply"], re.I), f"it did not tell the user he is in snowflak: {t1['reply']!r}"
    t2 = chat.say("yes, move him")
    assert "approve_move" in chat.tools(t2), t2["tools"]
    assert not t2["sends_this_turn"], "moving is not approval to send the email"
    assert t2["show_email_card"], f"after the yes it should show the email: {t2['reply']!r}"
    # The user already said yes to the move: the next question must be about the EMAIL only.
    assert not re.search(r"\bmove\b.*\?", t2["reply"], re.I), f"it asked about the move again: {t2['reply']!r}"


@pytest.mark.parametrize("rep", range(REPEATS))
def test_11_a_bounced_person_is_refused(chat, rep):
    t = chat.say("email Gus in a new campaign called Spring9, send between 9 am and 5 pm")
    assert not t["sends_this_turn"] and not t["show_email_card"], t["reply"]


@pytest.mark.parametrize("rep", range(REPEATS))
def test_12_a_new_person_uses_the_name_the_user_gave(chat, rep):
    t = chat.say("email shubham.kumar@new.example.com in a new campaign called Spring9, send between 9 am and 5 pm. His name is Shubham Kumar and he works at Orbit.")
    assert t["show_email_card"], f"no card. bot said {t['reply']!r}; tools {t['tools']}"
    person = t["email_view"]["people"][0]
    assert person["first_name"] == "Shubham", person
    assert _previews(t)["shubham.kumar@new.example.com"]["body"].lstrip().startswith(("Hi Shubham", "Hello Shubham", "Dear Shubham"))
    assert not t["sends_this_turn"]


@pytest.mark.parametrize("rep", range(REPEATS))
def test_13_a_lowercase_name_is_written_properly(chat, rep):
    t = chat.say("email jordan.lee@new.example.com in a new campaign called Spring9, send between 9 am and 5 pm. His name is jordan lee.")
    assert t["show_email_card"], t["reply"]
    body = _previews(t)["jordan.lee@new.example.com"]["body"]
    assert "Jordan" in body and "Hi jordan" not in body, body


@pytest.mark.parametrize("rep", range(REPEATS))
def test_14_a_unknown_name_becomes_there_and_is_warned(chat, rep):
    t = chat.say("email zed@new.example.com in a new campaign called Spring9, send between 9 am and 5 pm")
    assert t["show_email_card"], t["reply"]
    body = _previews(t)["zed@new.example.com"]["body"]
    assert "Hi there" in body or "Hello there" in body, body  # never a name guessed from the address
    assert not re.search(r"\bZed\b", body), body


@pytest.mark.parametrize("rep", range(REPEATS))
def test_15_the_users_own_edit_in_the_card_survives_a_later_request(chat, rep):
    from app.assistant.assistant import edit_draft
    from bizos import chat_history

    t1 = _draft(chat)
    marker = "KEEP-THIS-SENTENCE-12345"
    body = _template(t1)["body"].rstrip() + f"\n\n{marker}"
    # This is exactly what the card's Save button does.
    chat_history.edit_latest_assistant_meta(
        chat.ctx, chat.sid, lambda meta: edit_draft(chat.ctx, meta, subject=_template(t1)["subject"], body=body)
    )
    t2 = chat.say("make the subject a bit shorter")
    assert marker in _template(t2).get("body", ""), f"the user's own sentence was lost. Template now: {_template(t2)}"
    assert not t2["sends_this_turn"]


@pytest.mark.parametrize("rep", range(REPEATS))
def test_16_changing_an_existing_campaigns_email_asks_who_gets_it(chat, rep):
    from acquisition.db.session import SessionLocal
    from acquisition.models.campaign import Campaign, CampaignProspect, OutreachStatus
    from acquisition.models.prospect import ApprovalStatus, Prospect

    db = SessionLocal()
    snow = db.query(Campaign).filter(Campaign.name == "snowflak").one()
    snow.send_window_start, snow.send_window_end = "09:00", "17:00"
    wendy = Prospect(company="Acme", executive_name="Wendy Waits", email="wendy@acme.example.com", approval_status=ApprovalStatus.APPROVED)
    db.add(wendy)
    db.commit()
    db.add(CampaignProspect(campaign_id=snow.id, prospect_id=wendy.id, outreach_status=OutreachStatus.PENDING))
    db.commit()
    db.close()

    t1 = chat.say("add Nina to the existing snowflak campaign")
    if not t1["show_email_card"]:
        t1 = chat.say("yes, the existing snowflak campaign")
    assert t1["show_email_card"], f"no card: {t1['reply']!r}"
    t2 = chat.say("change the first line of the email to say we help teams save time")
    assert not t2["sends_this_turn"]
    t3 = chat.say("yes send it")
    view = t3["email_view"] or t2["email_view"]
    if (view.get("audience") or {}).get("scope_needed") or "needs_scope" in chat.result(t3, "send_approved"):
        assert not t3["sends_this_turn"], "it sent without asking if Wendy should get the change too"
        assert re.search(r"wendy|everyone|keep", t3["reply"], re.I), t3["reply"]
        t4 = chat.say("keep the campaign's current email")
        assert "choose_email_scope" in chat.tools(t4), t4["tools"]


@pytest.mark.parametrize("rep", range(REPEATS))
def test_17_ops_questions_do_not_start_the_campaign_flow(chat, rep):
    t = chat.say("what do we know about the onboarding checklist?")
    assert not t["sends_this_turn"] and not t["show_email_card"]
    assert not any(x in chat.tools(t) for x in ("update_selection", "propose_email", "send_approved", "approve_move", "remove_from_campaign")), t["tools"]
