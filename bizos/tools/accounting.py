"""
Accounting Tool Effects
=======================

Workspace accounting reads and the always-approved payment write. No permission
logic lives here — :func:`bizos.tools.base.guarded_call` decides whether the call
may proceed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from bizos.connectors.base import ConnectorResult
from bizos.connectors.registry import get_connector
from bizos.tools.base import RunScope, effect, risk_inputs


def _accounting(scope: RunScope):
    return get_connector("accounting", ctx=scope.ctx)


@effect("invoice_search")
def invoice_search(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _accounting(scope).search_invoices(
        query=str(args.get("query", "")),
        customer=args.get("customer"),
        status=args.get("status"),
        limit=int(args.get("limit", 20)),
    )


@effect("accounting_customer_balance")
def accounting_customer_balance(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _accounting(scope).customer_balance(str(args.get("customer") or args.get("query") or ""))


@effect("accounting_record_payment")
def accounting_record_payment(scope: RunScope, args: dict[str, Any]) -> ConnectorResult:
    return _accounting(scope).record_payment(
        customer=str(args.get("customer", "")),
        amount=float(args.get("amount", 0)),
        invoice_id=args.get("invoice_id"),
        method=str(args.get("method", "ach")),
        note=str(args.get("note", "")),
    )


@risk_inputs("accounting_record_payment")
def _payment_risk(scope: RunScope, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_count": 1,
        "financial_amount": Decimal(str(args.get("amount", 0) or 0)),
    }
