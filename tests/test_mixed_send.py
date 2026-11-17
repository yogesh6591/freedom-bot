"""A mixed recipient list queues a follow-up and a first email in one send."""

from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select

from acquisition.db.session import SessionLocal
from acquisition.models.outreach import KIND_FIRST, KIND_FOLLOW_UP, STATUS_SENT, SentEmail
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.services import outreach_service as svc
from app.outreach.session import OutreachSession


def test_mixed_pair_queues_follow_up_in_old_thread_and_first_email_for_new(monkeypatch):
    monkeypatch.setattr("app.outreach.sender.readiness", lambda *a, **k: (True, ""))
    monkeypatch.setattr("app.outreach.session.approval_problem", lambda latest, evidence: "")

    db = SessionLocal()
    try:
        prior = Prospect(
            company="Acme",
            executive_name="Prior Person",
            email=f"prior.{uuid4().hex[:8]}@example.com",
            approval_status=ApprovalStatus.APPROVED,
        )
        new = Prospect(
            company="Acme",
            executive_name="New Person",
            email=f"new.{uuid4().hex[:8]}@example.com",
            approval_status=ApprovalStatus.APPROVED,
        )
        db.add_all([prior, new])
        db.flush()
        previous = svc.enqueue(
            db,
            prospect_ids=[prior.id],
            subject_template="Hello",
            body_template="Hi {{firstName}}",
        )[0]
        previous.status = STATUS_SENT
        db.commit()
        previous_id = previous.id
        prior_email, new_email = prior.email, new.email

        session = OutreachSession(
            SimpleNamespace(user_id="test-user"),
            {
                "recipients": [
                    {"email": prior_email, "name": prior.executive_name},
                    {"email": new_email, "name": new.executive_name},
                ],
                "draft": {
                    "email_subject": "Checking in",
                    "email_body": "Hi {{firstName}}, just checking in.",
                },
            },
            latest_message="Yes, send it",
        )
        out = session.send_approved("Yes, send it")
        assert "Nothing was queued" not in out
        assert out.startswith("queued 2")
        batch_id = UUID(session.state["last_batch"]["id"])

        db.expire_all()
        rows = list(db.scalars(select(SentEmail).where(SentEmail.batch_id == batch_id)))
        assert len(rows) == 2
        by_email = {row.to_email.lower(): row for row in rows}
        follow = by_email[prior_email.lower()]
        first = by_email[new_email.lower()]
        assert follow.kind == KIND_FOLLOW_UP
        assert follow.follow_up_of_id == previous_id
        assert follow.subject == "Re: Hello"
        assert first.kind == KIND_FIRST
        assert first.follow_up_of_id is None
        assert first.subject == "Checking in"
    finally:
        db.close()
