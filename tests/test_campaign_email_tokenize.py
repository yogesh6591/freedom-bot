"""Framing names into Instantly blanks must not drop the assessment link."""

from __future__ import annotations

import pytest

from acquisition.config import get_settings
from app.assistant.assistant import (
    TOKEN_ASSESS,
    TOKEN_FIRST,
    draft_view,
    keep_assessment_link,
    person_fields,
    strip_assessment_link,
    tokenize,
)

BASE = "http://localhost:3001/acquire"


@pytest.fixture
def public_base(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    get_settings.cache_clear()
    yield BASE
    get_settings.cache_clear()


def test_tokenize_keeps_assessment_placeholder_when_a_name_is_inside_it(public_base):
    body = "Hi Nina,\n\nTake this:\n{{assessment_url}}\n\nBest,\nJeanne"
    framed = tokenize(
        body,
        [{"first_name": "Nina", "last_name": "url", "company": "Acme", "email": "nina@acme.example.com"}],
    )
    assert TOKEN_ASSESS in framed
    assert "{{firstName}}" in framed
    assert "Nina" not in framed.split(TOKEN_ASSESS)[0]


def test_tokenize_keeps_a_public_assessment_url_and_turns_it_into_the_placeholder(public_base):
    body = (
        "Hi Raj,\n\nWould you be open to a short free assessment for North?\n\n"
        f"{public_base}\n\nBest,\nJeanne"
    )
    framed = tokenize(
        body,
        [{"first_name": "Raj", "last_name": "Lee", "company": "North", "email": "raj@north.example.com"}],
    )
    assert TOKEN_ASSESS in framed
    assert public_base not in framed
    assert "{{companyName}}" in framed
    assert framed.count(TOKEN_ASSESS) == 1


def test_tokenize_does_not_invent_a_link_when_the_draft_never_had_one(public_base):
    framed = tokenize(
        "Hi Nina,\n\nQuick note.\n",
        [{"first_name": "Nina", "last_name": "", "company": "", "email": "nina@acme.example.com"}],
    )
    assert TOKEN_ASSESS not in framed


def test_keep_assessment_link_restores_the_placeholder_if_framing_ate_the_url(public_base):
    eaten = "Hi {{firstName}},\n\nWould you be open to a short free assessment?\n\nBest,\nJeanne"
    restored = keep_assessment_link(eaten)
    assert TOKEN_ASSESS in restored


def test_strip_assessment_link_removes_the_cta_from_a_subject(public_base):
    subject = f"Spring offer for the team {public_base}"
    cleaned = strip_assessment_link(subject)
    assert TOKEN_ASSESS not in cleaned
    assert public_base not in cleaned
    assert cleaned == "Spring offer for the team"


def test_strip_assessment_link_removes_the_placeholder_from_a_subject(public_base):
    cleaned = strip_assessment_link("Quick question {{assessment_url}}")
    assert TOKEN_ASSESS not in cleaned
    assert cleaned == "Quick question"


def test_a_database_given_name_that_matches_the_local_part_is_kept():
    records = {"people": [{"email": "ada@acme.example.com", "name": "Ada"}]}
    fields = person_fields(records, {}, {"email": "ada@acme.example.com"})
    assert fields["first_name"] == "Ada"
    assert fields["sources"]["first_name"] == "database"


def test_a_handle_copied_from_the_mailbox_is_not_a_name():
    records = {"people": [{"email": "ada.lee92@gmail.com", "name": "ada.lee92"}]}
    fields = person_fields(records, {}, {"email": "ada.lee92@gmail.com"})
    assert fields["first_name"] == ""
    assert fields["sources"]["first_name"] == "missing"


def test_the_full_email_is_not_treated_as_a_name():
    records = {"people": [{"email": "ada@acme.example.com", "name": "ada@acme.example.com"}]}
    fields = person_fields(records, {}, {"email": "ada@acme.example.com"})
    assert fields["first_name"] == ""
    assert fields["sources"]["first_name"] == "missing"


def test_missing_name_warning_is_only_when_the_greeting_uses_the_blank():
    state = {
        "recipients": [{"email": "zed@new.example.com"}],
        "draft": {
            "id": "d1",
            "email_template": True,
            "email_subject": "Hello",
            "email_body": "Hi Zed,\n\nNote.\n",
        },
    }
    view = draft_view({}, state)
    assert not any("Hi there" in w for w in view["warnings"])

    state["draft"]["email_body"] = f"Hi {TOKEN_FIRST},\n\nNote.\n"
    view = draft_view({}, state)
    assert any("Hi there" in w for w in view["warnings"])
