"""Group picks skip researched "not suitable" people; naming them still allows email.

Code-only: no OpenAI. Send path mocks readiness and enqueue so no real mail is queued.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from acquisition.db.session import SessionLocal
from acquisition.models.outreach_settings import OutreachSettings
from acquisition.models.prospect import ApprovalStatus, Prospect
from acquisition.services import outreach_settings
from app.outreach import audience
from app.outreach.session import OutreachSession


def test_group_skips_not_suitable_named_can_email(monkeypatch):
    tag = uuid4().hex[:8]
    db = SessionLocal()
    created: list = []
    settings_backup = None
    try:
        bad = Prospect(
            company=f"Guard Co {tag}",
            executive_name=f"Unsuitable {tag}",
            email=f"bad.{tag}@example.com",
            approval_status=ApprovalStatus.APPROVED,
            research_status="done",
            research_notes="Verdict: not suitable: wrong kind of business. Fit 20, timing 10, reach 10, priority 15.\nSummary: x",
            priority_score=15.0,
        )
        ok = Prospect(
            company=f"Guard Co {tag}",
            executive_name=f"Suitable {tag}",
            email=f"ok.{tag}@example.com",
            approval_status=ApprovalStatus.APPROVED,
            research_status="done",
            research_notes="Verdict: suitable: matches. Fit 90, timing 50, reach 80, priority 80.\nSummary: y",
            priority_score=80.0,
        )
        db.add_all([bad, ok])
        db.commit()
        created.extend([bad.id, ok.id])

        # ---- group pick (nobody named): unsuitable is skipped with the reason
        res = audience.select_people(db, count=10, text=tag)
        emails = {r["email"].lower() for r in res["people"]}
        assert ok.email.lower() in emails
        assert bad.email.lower() not in emails
        hit = next(s for s in res["skipped"] if s["email"].lower() == bad.email.lower())
        assert hit["reason"] == "researched: not suitable (name them to email anyway)", hit

        # ---- named send: unsuitable person can still be emailed (mocked, no real queue left)
        row = outreach_settings.get_row(db)
        if row is None:
            row = OutreachSettings(id=1)
            db.add(row)
            db.flush()
        settings_backup = {
            "sender_user_id": row.sender_user_id,
            "business_address": row.business_address,
            "optout_line": row.optout_line,
            "worker_enabled": bool(row.worker_enabled),
            "auto_send_enabled": bool(row.auto_send_enabled),
            "first_subject": row.first_subject,
            "first_body": row.first_body,
        }
        row.sender_user_id = row.sender_user_id or "guard-sender"
        row.business_address = row.business_address or "1 Test St"
        row.optout_line = row.optout_line or "Reply STOP"
        row.worker_enabled = True
        row.auto_send_enabled = True
        row.first_subject = "Hello {{firstName}}"
        row.first_body = "Hi {{firstName}},\n\nBest,\n{{senderName}}"
        db.commit()

        import acquisition.services.outreach_service as svc
        import app.outreach.sender as sender
        import app.outreach.session as session_mod

        monkeypatch.setattr(sender, "readiness", lambda: (True, ""))
        monkeypatch.setattr(session_mod, "approval_problem", lambda latest, evidence: "")
        enqueued: list = []

        def fake_enqueue(db_sess, **kwargs):
            ids = list(kwargs.get("prospect_ids") or [])
            enqueued.extend(ids)

            class Row:
                def __init__(self, pid):
                    self.prospect_id = pid
                    self.to_email = bad.email
                    self.id = uuid4()
                    self.kind = "first"
                    self.subject = "Hello"
                    self.body = "Hi"

            return [Row(pid) for pid in ids]

        monkeypatch.setattr(svc, "enqueue", fake_enqueue)

        session = OutreachSession(
            SimpleNamespace(user_id="guard-user"),
            {"recipients": [], "draft": {}},
            latest_message=f"send the intro email to {bad.executive_name}",
            db_factory=SessionLocal,
        )
        out = session.send_from_template(
            people=[{"name": bad.executive_name}],
            approval_evidence="yes send it",
        )
        assert not out.startswith("error:"), out
        assert "not suitable" not in out.lower()
        assert bad.id in enqueued
        assert len(enqueued) >= 1
    finally:
        if settings_backup is not None:
            row = outreach_settings.get_row(db)
            if row is not None:
                for k, v in settings_backup.items():
                    setattr(row, k, v)
                db.commit()
        for pid in created:
            row = db.get(Prospect, pid)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()
