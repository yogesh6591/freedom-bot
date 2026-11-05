"""query_records can filter people by research_status (empty -> not_researched).

Code-only: no OpenAI. Against the local acquisition DB, not_researched is exactly the
people with an empty research_status (19), and done is exactly the people marked done.
"""

from __future__ import annotations

from sqlalchemy import or_, select

from acquisition.db.session import SessionLocal
from acquisition.models.prospect import Prospect
from app.outreach.datasets import matching_ids


def test_research_status_not_researched_and_done():
    db = SessionLocal()
    try:
        empty = list(db.scalars(select(Prospect).where(
            or_(Prospect.research_status.is_(None), Prospect.research_status == "")
        )))
        done = list(db.scalars(select(Prospect).where(Prospect.research_status == "done")))

        ids_nr, total_nr = matching_ids(
            db, [{"field": "research_status", "op": "equals", "value": "not_researched"}], 500
        )
        assert total_nr == len(empty)
        assert set(ids_nr) == {p.id for p in empty}

        ids_done, total_done = matching_ids(
            db, [{"field": "research_status", "op": "equals", "value": "done"}], 500
        )
        assert total_done == len(done)
        assert set(ids_done) == {p.id for p in done}

        # Local acquisition DB (chat on :8001): 19 never researched; Stephanie is among done.
        from acquisition.config import get_settings

        if get_settings().database_url.rstrip("/").endswith("/acquisition"):
            assert len(empty) == 19, f"expected 19 empty research_status locally, got {len(empty)}"
            assert total_nr == 19
            assert any(p.executive_name == "Stephanie Warlick" for p in done), (
                f"Stephanie Warlick missing from done: {[p.executive_name for p in done]!r}"
            )
    finally:
        db.close()
