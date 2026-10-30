"""Payment-link email signs with Best, and the signed-in user's name."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from app.outreach.payment_tools import _payment_email, _signer_name
from app.outreach.toolkit import Call


def test_payment_email_ends_with_best_and_signed_in_user_name():
    call = Call(db=None, state={}, latest="", user_id="user-1")
    user = SimpleNamespace(display_name="Amelia", email="admin@acme.example.com")
    with patch("bizos.control.store.get_user", return_value=user):
        assert _signer_name(call) == "Amelia"
    link = SimpleNamespace(checkout_offer_amount=Decimal("1000.00"), assessment_token="tok")
    prospect = SimpleNamespace()
    _subject, body = _payment_email(prospect, link, "Amelia")
    assert body.splitlines()[-2:] == ["Best,", "Amelia"]
    assert not body.endswith("JeanneCAIO / FreedomBot Team")
