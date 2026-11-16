"""show_prospect remembers the focused prospect; lookup falls back for follow-up who."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.outreach import lookup
from app.outreach.toolkit import Call, ToolError


def test_person_uses_focus_prospect_when_who_empty(monkeypatch):
    db = MagicMock()
    prospect = MagicMock()
    prospect.id = 1
    call = Call(db=db, state={"focus_prospect": "bob@cfos2go.com"}, latest="", user_id="u", client_id=None)

    def fake_one(_db, text):
        assert text == "bob@cfos2go.com"
        return prospect

    monkeypatch.setattr(lookup, "one", fake_one)
    assert lookup.person(call, "") is prospect


def test_person_without_focus_raises():
    call = Call(db=MagicMock(), state={}, latest="", user_id="u", client_id=None)
    with pytest.raises(ToolError, match="nobody is named"):
        lookup.person(call, "")


def test_people_dataset_exposes_research_score_columns():
    from app.outreach.datasets import _people_columns

    keys = set(_people_columns())
    assert {"fit_score", "timing_score", "reachability_score"} <= keys