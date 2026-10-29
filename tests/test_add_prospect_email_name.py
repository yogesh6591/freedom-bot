"""add_prospects: an email with no name is saved; a row with no email is skipped."""

from __future__ import annotations

import pytest

from app.outreach.fields import FieldError
from app.outreach.prospect_tools import _new_prospect


class _Db:
    def scalar(self, *args, **kwargs):
        return None


def test_email_only_derives_name_and_missing_email_fails():
    db = _Db()
    person = _new_prospect({"email": "jane.doe_smith-lee@acme.com"}, 1, set(), db)
    assert person.email == "jane.doe_smith-lee@acme.com"
    assert person.executive_name == "Jane Doe Smith Lee"

    with pytest.raises(FieldError, match=r"row 2 \(Pat Kane\): no email"):
        _new_prospect({"executive_name": "Pat Kane"}, 2, set(), db)
