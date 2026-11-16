"""Placeholder pending-discovery+…@example.com shows No email yet and cannot be emailed."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from acquisition.db.session import SessionLocal
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.services.prospect_identity import is_placeholder_email, unique_placeholder_email
from acquisition.templating import create_templates
from app.outreach.session import OutreachSession


def test_unique_placeholder_shows_no_email_yet_and_choose_recipients_refuses():
    email = unique_placeholder_email()
    assert email.startswith("pending-discovery+") and email.endswith("@example.com")
    assert is_placeholder_email(email)

    # Prospects page: same Jinja test the list template uses.
    env = create_templates().env
    html = env.from_string(
        "{% if email is placeholder_email %}"
        '<span class="email-missing">No email yet</span>'
        "{% else %}{{ email }}{% endif %}"
    ).render(email=email)
    assert "No email yet" in html
    assert email not in html

    tag = uuid4().hex[:8]
    db = SessionLocal()
    row = Prospect(
        company=f"NoMail Co {tag}",
        executive_name=f"NoMail Person {tag}",
        email=email,
        approval_status=ApprovalStatus.APPROVED,
        email_confidence="placeholder",
    )
    try:
        db.add(row)
        db.commit()
        session = OutreachSession(
            SimpleNamespace(user_id="test-user"),
            {"recipients": []},
            latest_message=f"email {email}",
            db_factory=SessionLocal,
        )
        out = session.choose_recipients(
            people=[{"email": email, "name": row.executive_name}],
            mode="replace",
        )
        assert out.startswith("error:"), out
        assert "no email yet" in out.lower(), out
        assert session.state.get("recipients") == []
    finally:
        db.delete(row)
        db.commit()
        db.close()
