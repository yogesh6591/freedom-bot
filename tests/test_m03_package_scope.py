"""M03-09 — the assistant never offers work from a domain pack the client has not bought."""

from __future__ import annotations

from bizos.agents import grounding
from bizos.domains.catalog import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES

BASE = list(DEFAULT_ENABLED_DOMAINS)


def test_detects_unbought_packs_only():
    hits = grounding.unpurchased_hits("Draft an invoice for Initech and review this contract clause", BASE)
    assert set(hits) == {"finance", "legal"}
    assert "invoices" in hits["finance"]
    # A client that bought everything is never flagged.
    assert grounding.unpurchased_hits("Draft an invoice for Initech", list(DOMAIN_NAMES)) == {}
    # Base-package topics are not flagged.
    assert grounding.unpurchased_hits("What are our operating hours?", BASE) == {}


def test_offer_is_removed_and_package_named():
    reply = (
        "There is no recorded fact about our refund period. "
        "Regarding the invoice for Initech for $4,000, I can draft that for you and submit it "
        "for approval. Shall I proceed with drafting the invoice?"
    )
    hits = grounding.unpurchased_hits("What is our refund period, and draft an invoice for Initech", BASE)
    fixed, changed = grounding.enforce_package(
        reply, hits, company="Globex Ltd", asked_for_action=True, answered_visible=False
    )
    assert changed
    assert "I can draft" not in fixed
    assert "Shall I proceed" not in fixed
    assert "Finance" in fixed and "package" in fixed and "change order" in fixed
    assert "Globex Ltd" in fixed
    # The unrelated, truthful sentence survives.
    assert "no recorded fact about our refund period" in fixed


def test_reply_that_already_names_the_package_is_left_alone():
    reply = "Finance isn't part of your current package, so I can't draft invoices. An admin can add it with a change order."
    hits = grounding.unpurchased_hits("Draft an invoice", BASE)
    fixed, changed = grounding.enforce_package(reply, hits, company="Globex Ltd", asked_for_action=True)
    assert not changed
    assert fixed == reply


def test_recorded_fact_answer_is_not_cluttered():
    # A client may still record, say, its refund period in general memory.
    reply = "Fact: Refund period = 14 days."
    hits = grounding.unpurchased_hits("What is our refund period?", BASE)
    fixed, changed = grounding.enforce_package(
        reply, hits, company="Globex Ltd", asked_for_action=False, answered_visible=True
    )
    assert not changed
    assert fixed == reply


def test_offer_only_reply_becomes_the_package_notice():
    reply = "I can draft that invoice for you right away."
    hits = grounding.unpurchased_hits("Draft an invoice for Initech", BASE)
    fixed, changed = grounding.enforce_package(reply, hits, company="Globex Ltd", asked_for_action=True)
    assert changed
    assert fixed.startswith("Finance (")
    assert "I can draft" not in fixed


def test_no_unbought_topic_means_no_change():
    reply = "I can draft that email for you."
    fixed, changed = grounding.enforce_package(reply, {}, company="Acme Corp", asked_for_action=True)
    assert not changed and fixed == reply


def test_reply_naming_the_pack_by_the_users_words_gets_no_duplicate_note():
    reply = "The credit adjustments domain pack is not part of the client's current package. An admin can add it with a change order."
    hits = grounding.unpurchased_hits("Can you prepare a credit adjustment for Umbrella?", BASE)
    fixed, changed = grounding.enforce_package(reply, hits, company="Globex Ltd", asked_for_action=True)
    assert not changed and fixed == reply


def test_offer_to_record_a_fact_is_kept_and_no_dangling_or():
    reply = (
        "There is no recorded fact for our refund period. Would you like me to record it? "
        "I can draft the Initech invoice for you. Or assist with anything else?"
    )
    hits = grounding.unpurchased_hits("What is our refund period, and draft an invoice for Initech", BASE)
    fixed, _ = grounding.enforce_package(reply, hits, company="Globex Ltd", asked_for_action=True)
    assert "Would you like me to record it?" in fixed
    assert "I can draft" not in fixed
    assert "Or assist" not in fixed


def test_generic_follow_up_to_a_removed_offer_is_removed_too():
    reply = (
        "There is no recorded fact about our refund period. I can draft the Initech invoice "
        "for $4,000. Would you like me to proceed?"
    )
    hits = grounding.unpurchased_hits("What is our refund period, and draft an invoice for Initech", BASE)
    fixed, _ = grounding.enforce_package(reply, hits, company="Globex Ltd", asked_for_action=True)
    assert "proceed" not in fixed
    assert fixed.startswith("There is no recorded fact about our refund period.")
    assert "Not in your package" in fixed
