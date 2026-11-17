"""Prospects are isolated by FreedomBot client_id inside acquisition tenant scope."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from acquisition.db.session import SessionLocal
from acquisition.models.prospect import Prospect
from acquisition.tenant import prospect_client_clause, tenant_scope
from app.outreach import datasets


def test_prospects_isolated_by_client_id():
    client_a, client_b = "cli_test_a", "cli_test_b"
    email_a = f"alice-{uuid.uuid4().hex[:8]}@acme.test"
    email_b = f"bob-{uuid.uuid4().hex[:8]}@globex.test"
    db = SessionLocal()
    try:
        with tenant_scope(client_a):
            db.add(
                Prospect(
                    company="Acme Co",
                    executive_name="Alice Test",
                    email=email_a,
                    client_id=client_a,
                    discovery_source="test",
                )
            )
            db.commit()
        with tenant_scope(client_b):
            db.add(
                Prospect(
                    company="Globex Co",
                    executive_name="Bob Test",
                    email=email_b,
                    client_id=client_b,
                    discovery_source="test",
                )
            )
            db.commit()

        with tenant_scope(client_a):
            n = db.scalar(select(func.count()).select_from(Prospect).where(prospect_client_clause()))
            assert n == 1
            result = datasets.build(db, datasets.Spec(dataset="people", count_only=True))
            assert result["total"] == 1

        with tenant_scope(client_b):
            result = datasets.build(db, datasets.Spec(dataset="people", count_only=True))
            assert result["total"] == 1

        with tenant_scope(client_a):
            assert (
                db.scalar(select(Prospect).where(prospect_client_clause(), Prospect.email == email_b)) is None
            )
    finally:
        try:
            for p in db.scalars(select(Prospect).where(Prospect.email.in_([email_a, email_b]))):
                db.delete(p)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        db.close()