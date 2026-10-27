"""Framing names into Instantly blanks must not drop the assessment link."""

from __future__ import annotations

import pytest

from acquisition.config import get_settings
from app.assistant.assistant import TOKEN_ASSESS, keep_assessment_link, strip_assessment_link, tokenize

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
