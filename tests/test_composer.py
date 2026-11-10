"""The v2 reply composer builds acknowledgements from tool results and never claims what did not happen."""

from __future__ import annotations

from bizos.agents.composer import NOTHING_DONE, compose
from bizos.agents.tool_result import ToolResult


def r(tool, status, summary="", kind=None, ref=None):
    return ToolResult(tool=tool, status=status, summary=summary, pending_kind=kind, ref=ref)


def test_writes_are_not_pasted_into_the_reply():
    ack = compose("Here you go.", [
        r("crm_search_deals", "success", "1 deal(s)"),
        r("crm_update_deal_stage", "success", "stage set to won", ref="D-2231"),
        r("email_send_message", "pending", "needs approval", "approval", "act_118"),
        r("calendar_create_event", "pending", "", "draft", "act_119"),
        r("finance_draft_invoice", "error", "No pending billables for 'Acme'"),
    ])
    assert ack.text == "Here you go."
    assert "✅" not in ack.text and "⏳" not in ack.text and "📝" not in ack.text and "❌" not in ack.text
    assert "Update deal stage" not in ack.text and "deal(s)" not in ack.text
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
    assert "awaiting approval" in pending.text
    assert "✅" not in pending.text and "⏳" not in pending.text
    draft_only = compose("It is now awaiting approval.", [r("email_send_message", "pending", "", "draft", "act_2")])
    assert "awaiting approval" not in draft_only.text
    assert "📝" not in draft_only.text


def test_a_failed_or_denied_write_says_nothing_changed():
    ack = compose("I've drafted the invoice.", [r("finance_draft_invoice", "error", "No pending billables")])
    assert ack.text.startswith(NOTHING_DONE) and "❌" not in ack.text and "drafted" not in ack.text


def test_answers_to_questions_keep_their_facts():
    ack = compose("The Acme deal was created in March and was moved to negotiation last week.", [], task=False)
    assert ack.text == "The Acme deal was created in March and was moved to negotiation last week."


def test_questions_are_never_claims():
    for lead in ("What specific baseline metric should I set for lead response?",
                 "Should I send it now?", "Do you want me to update Raj's phone?", "Shall I book the meeting?"):
        assert compose(lead, [], task=True).text == lead
    mixed = compose("Done, I've updated it. Should I also email Raj?", [], task=True).text
    assert mixed == "Should I also email Raj?"


def test_a_question_that_also_claims_keeps_only_the_question():
    assert compose("I've sent it, anything else?", [], task=True).text == "Anything else?"
    assert compose("Done - should I email Priya too?", [], task=True).text == "Should I email Priya too?"
    assert compose("Is there anything else, now that I've booked it?", [], task=True).text == "Is there anything else"
    # A real question keeps its own verbs.
    assert compose("Should I set the baseline to 6 hours?", [], task=True).text == "Should I set the baseline to 6 hours?"


def test_facts_are_not_claims_but_the_assistants_own_actions_are():
    for fact in ("The renewal is scheduled for March.", "The launch checklist is not fully completed yet.", "You have a meeting scheduled tomorrow at 10:00.",
                 "The workspace timezone is set to Asia/Kolkata.", "The Acme deal was moved to negotiation last week.",
                 "There is already a draft invoice created for Globex."):
        assert compose(fact, [], task=True).text == fact, fact
    for claim in ("Your call with Raj is scheduled.", "I’ve saved a note on Raj.", "I’ve set up a call with her.",
                  "I found the priorities and have added a note on the Acme deal.",
                  "The conflict has been flagged and is awaiting approval.", "Raj was updated successfully.",
                  "Meeting with Priya booked for Tuesday.", "Invoice drafted!", "Saved the note for you."):
        assert compose(claim, [], task=True).text == "Nothing was changed." or claim not in compose(claim, [], task=True).text, claim


def test_asking_clauses_future_wording_and_pending_requests_are_not_claims():
    pending = [r("crm_tag_lead", "pending", "", "approval", "act_9")]
    for lead in ("Which deal or pipeline stage should I set, and to what value?",
                 "Would you like me to mark these as done, or waive them?"):
        assert compose(lead, [], task=True).text == lead
    assert compose("I'll keep you updated once it's approved and completed.", pending).text.startswith(
        "I'll keep you updated once it's approved and completed.")
    assert compose("I have created the request to tag Raj Mehta as hot.", pending).text.startswith(
        "I have created the request")
    # Still a claim while pending: the note itself was not added.
    claimed = compose("The note has been added and is awaiting approval before it can be recorded.",
                      [r("crm_create_note", "pending", "", "approval", "act_8")]).text
    assert "has been added" not in claimed


def test_assessment_shaped_lead_in_is_kept():
    """compose no longer replaces a shaped assessment reply with the raw tool report."""
    shaped = "Qualified.\n- Why: strong fit\n- Booking: confirmed"
    report = (
        "Qualified, assessment finished Oct 28\n"
        "Shubham <a@b.com>\n"
        "- Why: strong fit and more detail the user did not ask for"
    )
    ack = compose(
        shaped,
        [ToolResult(tool="show_assessment", status="success", summary=report.splitlines()[0], data=report)],
        task=False,
    )
    assert ack.text == shaped
