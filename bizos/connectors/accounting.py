"""
Workspace Accounting Connector
==============================

A workspace-backed accounting surface (invoices + customer balances + payment
records) so Finance-domain tools are exercisable end-to-end without QuickBooks
or Xero. Same isolation model as the workspace CRM.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorResult
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id


class AccountingConnector(Connector):
    category = "accounting"
    provider = "workspace"

    def health(self) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            invoices = conn.execute(text("SELECT count(*) FROM accounting_invoices")).scalar()
            customers = conn.execute(text("SELECT count(*) FROM accounting_customers")).scalar()
        return ConnectorResult(
            ok=True,
            data={"invoices": invoices, "customers": customers},
            summary=f"{customers} customers, {invoices} invoices",
        )

    def search_invoices(
        self,
        *,
        query: str = "",
        customer: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> ConnectorResult:
        clauses, params = [], {"limit": max(1, min(limit, 100))}
        if query:
            clauses.append(
                "(i.number ILIKE :q OR i.customer_name ILIKE :q OR i.customer_email ILIKE :q)"
            )
            params["q"] = f"%{query}%"
        if customer:
            clauses.append(
                "(i.customer_id = :customer OR i.customer_email ILIKE :customer_q OR i.customer_name ILIKE :customer_q)"
            )
            params["customer"] = customer
            params["customer_q"] = f"%{customer}%"
        if status:
            clauses.append("i.status = :status")
            params["status"] = status
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    f"SELECT i.* FROM accounting_invoices i {where} "
                    "ORDER BY i.issued_at DESC LIMIT :limit"
                ),
                params,
            ).fetchall()
        records = [dict(r._mapping) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} invoice(s)")

    def customer_balance(self, customer: str) -> ConnectorResult:
        key = (customer or "").strip()
        if not key:
            return ConnectorResult(ok=False, error="customer is required", summary="missing customer")
        with readonly_connection(self.ctx) as conn:
            row = conn.execute(
                text(
                    "SELECT * FROM accounting_customers "
                    "WHERE id = :k OR lower(email) = lower(:k) OR lower(name) = lower(:k) "
                    "LIMIT 1"
                ),
                {"k": key},
            ).first()
            if row is None:
                # Derive a balance from open invoices when no customer master row exists.
                open_total = conn.execute(
                    text(
                        "SELECT COALESCE(SUM(balance_due), 0) FROM accounting_invoices "
                        "WHERE status IN ('open','partial','overdue') AND "
                        "(customer_id = :k OR lower(customer_email) = lower(:k) OR lower(customer_name) = lower(:k))"
                    ),
                    {"k": key},
                ).scalar()
                data = {
                    "customer": key,
                    "balance_due": float(open_total or 0),
                    "currency": "USD",
                    "source": "invoices",
                }
                return ConnectorResult(
                    ok=True,
                    data=data,
                    summary=f"balance {data['balance_due']} {data['currency']} for {key}",
                )
            data = dict(row._mapping)
            return ConnectorResult(
                ok=True,
                data=data,
                summary=f"balance {data.get('balance_due')} {data.get('currency', 'USD')} for {data.get('name')}",
            )

    def record_payment(
        self,
        *,
        customer: str,
        amount: float,
        invoice_id: Optional[str] = None,
        method: str = "ach",
        note: str = "",
    ) -> ConnectorResult:
        amt = Decimal(str(amount))
        if amt <= 0:
            return ConnectorResult(ok=False, error="amount must be positive", summary="invalid amount")
        payment_id = new_id("pay")
        with workspace_connection(self.ctx) as conn:
            customer_row = conn.execute(
                text(
                    "SELECT * FROM accounting_customers "
                    "WHERE id = :k OR lower(email) = lower(:k) OR lower(name) = lower(:k) "
                    "LIMIT 1"
                ),
                {"k": customer},
            ).first()
            customer_id = customer_row.id if customer_row else None
            invoice = None
            if invoice_id:
                invoice = conn.execute(
                    text("SELECT * FROM accounting_invoices WHERE id = :i"),
                    {"i": invoice_id},
                ).first()
                if invoice is None:
                    return ConnectorResult(
                        ok=False, error=f"invoice {invoice_id} not found", summary="missing invoice"
                    )
            conn.execute(
                text(
                    "INSERT INTO accounting_payments "
                    "(id, customer_id, customer_key, invoice_id, amount, currency, method, note, created_by) "
                    "VALUES (:i, :c, :k, :inv, :a, :cur, :m, :n, :u)"
                ),
                {
                    "i": payment_id,
                    "c": customer_id,
                    "k": customer,
                    "inv": invoice_id,
                    "a": float(amt),
                    "cur": (invoice.currency if invoice else None)
                    or (customer_row.currency if customer_row else "USD"),
                    "m": method or "ach",
                    "n": note or "",
                    "u": self.ctx.user_id,
                },
            )
            if invoice is not None:
                new_balance = max(Decimal(str(invoice.balance_due)) - amt, Decimal("0"))
                new_status = "paid" if new_balance == 0 else "partial"
                conn.execute(
                    text(
                        "UPDATE accounting_invoices SET balance_due = :b, status = :s, "
                        "updated_at = NOW() WHERE id = :i"
                    ),
                    {"b": float(new_balance), "s": new_status, "i": invoice_id},
                )
            if customer_row is not None:
                new_cust_bal = max(Decimal(str(customer_row.balance_due)) - amt, Decimal("0"))
                conn.execute(
                    text(
                        "UPDATE accounting_customers SET balance_due = :b, updated_at = NOW() "
                        "WHERE id = :i"
                    ),
                    {"b": float(new_cust_bal), "i": customer_row.id},
                )
        return ConnectorResult(
            ok=True,
            data={"payment_id": payment_id, "customer": customer, "amount": float(amt), "invoice_id": invoice_id},
            summary=f"recorded payment {float(amt)} for {customer}",
        )
