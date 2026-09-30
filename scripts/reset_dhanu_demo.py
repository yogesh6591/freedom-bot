"""
Reset the Dhanu real-world demo
===============================

    docker compose exec api python -m scripts.reset_dhanu_demo

Puts the Acme workspace back to the state the Dhanu test document starts from,
without destroying it:

* re-runs the demo bootstrap (users, data scopes, memory tags and areas);
* Acme Logistics billables back to ``pending_invoice`` and draft invoices removed;
* every pending approval / draft action cancelled;
* leftover test memory (``demo_*``, ``fb03*``, ``test_*`` and chat-written ``*checklist*`` keys) retired so it
  cannot answer demo questions.
"""

from __future__ import annotations

import sys

from bizos.util.dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from bizos.actions import store as actions  # noqa: E402
from bizos.bootstrap import admin_context, bootstrap  # noqa: E402
from bizos.control import store as control  # noqa: E402
from bizos.tenancy.context import tenant_scope  # noqa: E402
from bizos.tenancy.registry import workspace_connection  # noqa: E402
from bizos.types import ActionStatus  # noqa: E402

_TEST_KEY_PATTERNS = ("demo\\_%", "fb03%", "test\\_%", "%checklist%")


def main() -> int:
    bootstrap(with_demo=True)
    client = control.get_client_by_slug("acme")
    ctx = admin_context(client)

    with tenant_scope(ctx):
        cancelled = 0
        for action in actions.list_actions(
            statuses=[ActionStatus.PENDING_APPROVAL, ActionStatus.DRAFT], limit=500, ctx=ctx
        ):
            try:
                actions.cancel(action.id, ctx=ctx)
                cancelled += 1
            except Exception as exc:  # an illegal transition is not worth failing the reset
                print(f"  could not cancel {action.id}: {exc}")

    with workspace_connection(ctx) as conn:
        deals = conn.execute(
            text(
                "UPDATE crm_deals SET stage = 'pending_invoice', updated_at = NOW() "
                "WHERE name ILIKE 'Acme Logistics%' AND stage <> 'pending_invoice'"
            )
        ).rowcount
        invoices = conn.execute(
            text("DELETE FROM accounting_invoices WHERE number LIKE 'DRAFT-%'")
        ).rowcount
        retired = 0
        for pattern in _TEST_KEY_PATTERNS:
            retired += conn.execute(
                text(
                    "UPDATE memory_versions SET status = 'SUPERSEDED', effective_until = NOW() "
                    "WHERE status = 'ACTIVE' AND item_id IN "
                    "(SELECT id FROM memory_items WHERE memory_key LIKE :p)"
                ),
                {"p": pattern},
            ).rowcount

    print(
        f"Demo reset: {cancelled} pending action(s) cancelled, {deals} billable(s) back "
        f"to pending_invoice, {invoices} draft invoice(s) removed, {retired} test memory "
        "value(s) retired. Users and data scopes restored."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
