"""research_prospects skips already-researched people on a group/"all" filter; a named person is redone.

Code-only: no OpenAI, no worker tick. Test rows are deleted so nothing stays queued.
"""

from __future__ import annotations

from uuid import uuid4

from acquisition.db.session import SessionLocal
from acquisition.models.prospect import ApprovalStatus, Prospect
from app.outreach.research_tools import research_prospects
from app.outreach.toolkit import Call


def test_all_skips_done_named_redoes():
    tag = f"skipdone-{uuid4().hex[:8]}"
    company = f"SkipDone Co {tag}"
    db = SessionLocal()
    rows: list[Prospect] = []
    try:
        # Distinct full names so lookup.many does not fuzzy-collide across the set.
        done_names = ["Ada Quested", "Ben Marked", "Cara Settled"]
        fresh_names = ["Drew Pending", "Eve Waiting"]
        for i, name in enumerate(done_names):
            rows.append(Prospect(
                company=company,
                executive_name=name,
                email=f"done{i}.{tag}@example.com",
                approval_status=ApprovalStatus.APPROVED,
                research_status="done",
            ))
        for i, name in enumerate(fresh_names):
            rows.append(Prospect(
                company=company,
                executive_name=name,
                email=f"fresh{i}.{tag}@example.com",
                approval_status=ApprovalStatus.APPROVED,
                research_status=None,
            ))
        db.add_all(rows)
        db.commit()

        call = Call(db=db, state={}, latest=f"research all at {company}", user_id="test-user")
        out_all = research_prospects(
            call,
            people=None,
            where=[{"field": "company", "op": "equals", "value": company}],
        )
        assert out_all.startswith("Queued 2 for web research:"), out_all
        assert "Already researched, skipped (name them to research again):" in out_all, out_all
        for name in done_names:
            assert name in out_all, out_all
        for name in fresh_names:
            assert name in out_all, out_all

        db.expire_all()
        by_name = {p.executive_name: p for p in rows}
        for name in done_names:
            db.refresh(by_name[name])
            assert by_name[name].research_status == "done", name
        for name in fresh_names:
            db.refresh(by_name[name])
            assert by_name[name].research_status == "queued", name
            # leave nothing for a worker: put them back before the named redo check
            by_name[name].research_status = None
            by_name[name].research_batch = None
        db.commit()

        named = done_names[0]
        call2 = Call(db=db, state={}, latest=f"research {named} again", user_id="test-user")
        out_named = research_prospects(call2, people=[named], where=None)
        assert out_named.startswith("Queued 1 for web research:"), out_named
        assert named in out_named, out_named
        assert "Already researched, skipped" not in out_named, out_named

        db.refresh(by_name[named])
        assert by_name[named].research_status == "queued"
        # clear so nothing stays queued
        by_name[named].research_status = "done"
        by_name[named].research_batch = None
        db.commit()
    finally:
        for p in rows:
            row = db.get(Prospect, p.id)
            if row is not None:
                db.delete(row)
        db.commit()
        db.close()
