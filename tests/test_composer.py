"""The v2 reply composer builds acknowledgements from tool results and never claims what did not happen."""

from __future__ import annotations

from bizos.agents.composer import NOTHING_DONE, compose
from bizos.agents.tool_result import ToolResult
from bizos.rbac.registry import get_spec


def r(tool, status, summary="", kind=None, ref=None):
    return ToolResult(tool=tool, status=status, summary=summary, pending_kind=kind, ref=ref)


def test_one_line_per_write_and_reads_stay_out():
    ack = compose("Here you go.", [
        r("crm_search_deals", "success", "1 deal(s)"),
        r("crm_update_deal_stage", "success", "stage set to won", ref="D-2231"),
        r("email_send_message", "pending", "needs approval", "approval", "act_118"),
        r("calendar_create_event", "pending", "", "draft", "act_119"),
        r("finance_draft_invoice", "error", "No pending billables for 'Acme'"),
    ])
    lines = ack.text.splitlines()
    assert lines[0] == "Here you go."
    assert f"✅ {get_spec('crm_update_deal_stage').title}: stage set to won (D-2231)" in lines
    assert any(l.startswith("⏳ ") and "waiting for approval (act_118)" in l for l in lines)
    assert any(l.startswith("📝 ") and "draft for review (act_119)" in l for l in lines)
    assert any(l.startswith("❌ ") and "No pending billables" in l for l in lines)
    assert not any("deal(s)" in l for l in lines)
    assert ack.wrote and ack.pending_approval


def test_never_says_done_when_nothing_succeeded():
    for lead in ("Done! I've updated Raj's phone.", "All set, the email has been sent.", "I have booked the meeting.",
                 "Ho gaya, maine kar diya.", "Saved the note for you.", "Forwarded.", "Invoice drafted!",
                 "Meeting with Priya booked for Tuesday.", "Your call with Raj is scheduled.",
                 "Raj was updated successfully.", "The invites are now sent."):
        ack = compose(lead, [], task=True)
        assert not ack.wrote
        for word in ("done", "updated", "has been sent", "booked", "ho gaya", "saved", "forwarded", "drafted",
                     "scheduled", "now sent"):
            assert word not in ack.text.casefold(), (lead, ack.text)


def test_pending_work_is_not_called_done_and_approval_is_only_claimed_when_true():
    pending = compose("Done, I've sent it. It is now awaiting approval.",
                      [r("email_send_message", "pending", "", "approval", "act_1")])
    assert "sent it" not in pending.text and "Done" not in pending.text
    assert "waiting for approval (act_1)" in pending.text
    draft_only = compose("It is now awaiting approval.", [r("email_send_message", "pending", "", "draft", "act_2")])
    assert "awaiting approval" not in draft_only.text and "draft for review" in draft_only.text


def test_a_failed_or_denied_write_says_nothing_changed():
    ack = compose("I've drafted the invoice.", [r("finance_draft_invoice", "error", "No pending billables")])
    assert ack.text.startswith(NOTHING_DONE) and "❌" in ack.text and "drafted" not in ack.text


def test_answers_to_questions_keep_their_facts():
    ack = compose("The Acme deal was created in March and was moved to negotiation last week.", [], task=False)
    assert ack.text == "The Acme deal was created in March and was moved to negotiation last week."


def test_questions_are_never_claims():
    for lead in ("What specific baseline metric should I set for lead response?",
                 "Should I send it now?", "Do you want me to update Raj's phone?", "Shall I book the meeting?"):
        assert compose(lead, [], task=True).text == lead
    mixed = compose("Done, I've updated it. Should I also email Raj?", [], task=True).text
    assert mixed == "Should I also email Raj?"
