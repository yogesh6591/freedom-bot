#!/usr/bin/env python3
"""Watchman check: prospect rows are isolated per client_id on the chat/outreach path."""

from __future__ import annotations

import sys
import uuid

from sqlalchemy import func, select

from acquisition.db.session import SessionLocal
from acquisition.models.prospect import Prospect
from acquisition.tenant import prospect_client_clause, tenant_scope
from app.outreach import datasets, lookup
from app.outreach import toolkit as toolkit_mod
from app.outreach.toolkit import run as run_tool

toolkit_mod.load_all()


def main() -> int:
    client_a, client_b = f"cli_audit_{uuid.uuid4().hex[:6]}", f"cli_audit_{uuid.uuid4().hex[:6]}"
    email_a = f"a-{uuid.uuid4().hex[:8]}@tenant.test"
    email_b = f"b-{uuid.uuid4().hex[:8]}@tenant.test"
    db = SessionLocal()
    failures: list[str] = []
    try:

        def check(label: str, cond: bool) -> None:
            if not cond:
                failures.append(label)

        # seed
        with tenant_scope(client_a):
            db.add(
                Prospect(
                    company="Org A Co",
                    executive_name="Alice Audit",
                    email=email_a,
                    client_id=client_a,
                    discovery_source="audit",
                )
            )
            db.commit()
        with tenant_scope(client_b):
            db.add(
                Prospect(
                    company="Org B Co",
                    executive_name="Bob Audit",
                    email=email_b,
                    client_id=client_b,
                    discovery_source="audit",
                )
            )
            db.commit()

        with tenant_scope(client_a):
            check("count A", db.scalar(select(func.count()).select_from(Prospect).where(prospect_client_clause())) == 1)
            check("datasets A", datasets.build(db, datasets.Spec(dataset="people", count_only=True))["total"] == 1)
            check("lookup A sees Alice", lookup.by_email(db, email_a) is not None)
            check("lookup A hides Bob", lookup.by_email(db, email_b) is None)
            text = run_tool(
                "query_records",
                db_factory=lambda: db,
                state={},
                latest="how many",
                user_id="usr_test",
                client_id=client_a,
                args={"dataset": "people", "count_only": True},
            )
            check("query_records mentions count 1", "1" in text)

        with tenant_scope(client_b):
            check("count B", db.scalar(select(func.count()).select_from(Prospect).where(prospect_client_clause())) == 1)
            check("lookup B hides Alice", lookup.by_email(db, email_a) is None)

        with tenant_scope(client_a):
            text = run_tool(
                "add_prospects",
                db_factory=lambda: db,
                state={},
                latest="add",
                user_id="usr_test",
                client_id=client_a,
                args={"people": [{"executive_name": "Carol Audit", "email": f"c-{uuid.uuid4().hex[:6]}@tenant.test"}]},
            )
            check("add_prospects OK", text.startswith("Added"))

        print("FAILURES:" if failures else "ALL CHECKS PASSED")
        for f in failures:
            print(" -", f)
        return 1 if failures else 0
    finally:
        for p in db.scalars(select(Prospect).where(Prospect.email.like("%@tenant.test"))):
            db.delete(p)
        db.commit()
        db.close()


if __name__ == "__main__":
    sys.exit(main())