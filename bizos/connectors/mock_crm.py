"""
Workspace CRM Connector
=======================

A fully working CRM backed by the client's **own workspace tables**
(``crm_contacts``, ``crm_companies``, ``crm_deals``, ``crm_notes``, ``crm_tasks``).

This is FreedomBot's own CRM and the default place CRM records are kept: in the client's own
database, with no outside CRM needed. A person may choose HubSpot or a sheet in Google Drive instead
(bizos/crm_storage.py). It is a real, queryable CRM, which is what makes the four
execution modes, the approval queue and the workflows testable end-to-end without
a vendor account — and what makes the tenant-isolation test meaningful, since the
records genuinely live inside each client's isolated database.

Swapping in a live vendor means implementing the same methods against their API;
nothing above this layer changes.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text

from bizos.connectors.base import Connector, ConnectorResult
from bizos.connectors.untrusted import wrap_many
from bizos.tenancy.registry import readonly_connection, workspace_connection
from bizos.util.ids import new_id

#: Fields a caller may address by name. An allowlist, because the field name is
#: interpolated into the UPDATE statement — and because it is also what the
#: policy engine's ``auto_updatable_crm_fields`` check is expressed against.
UPDATABLE_CONTACT_FIELDS = frozenset(
    {"first_name", "last_name", "email", "phone", "company", "title", "owner", "lifecycle"}
)


def _domain(value: Optional[str]) -> str:
    """A web domain as stored: lower case, no scheme, no www, no path ("https://www.Acme.com/x" -> "acme.com")."""
    site = str(value or "").strip().lower()
    for prefix in ("https://", "http://"):
        if site.startswith(prefix):
            site = site[len(prefix):]
    site = site.split("/", 1)[0]
    return site[4:] if site.startswith("www.") else site


def check_deal_fields(name: Any, amount: Any, close_date: Any) -> tuple[str, Decimal, Optional[Any], Optional[str]]:
    """A new deal's name, amount and close date checked: (name, amount, close date, problem or None).

    Shared by every place a deal can be saved (FreedomBot, HubSpot, Google Drive), so all three accept
    and refuse the same input.
    """
    from datetime import date

    clean = " ".join(str(name or "").split())
    if not clean:
        return "", Decimal(0), None, "name is required: the deal's name, e.g. 'Globex renewal 2027'."
    try:
        value = Decimal(str(amount if amount not in (None, "") else 0).replace(",", "").lstrip("$"))
    except Exception:
        return clean, Decimal(0), None, f"amount must be a number, e.g. 25000 (got {amount!r})."
    if not value.is_finite():
        return clean, Decimal(0), None, f"amount must be a number, e.g. 25000 (got {amount!r})."
    if value < 0:
        return clean, Decimal(0), None, "amount cannot be negative."
    closes = None
    if close_date:
        try:
            closes = date.fromisoformat(str(close_date)[:10])
        except ValueError:
            return clean, value, None, f"close_date must be a date like 2026-12-31 (got {close_date!r})."
    return clean, value, closes, None


class CrmConnector(Connector):
    category = "crm"
    provider = "workspace"

    @property
    def _author(self) -> str:
        """Who a new note or task is from: the requester when an approver runs it for them."""
        return str(self.config.get("owner_user_id") or self.ctx.user_id)

    def health(self) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            count = conn.execute(text("SELECT count(*) FROM crm_contacts")).scalar()
        return ConnectorResult(ok=True, data={"contacts": count}, summary=f"{count} contacts")

    # -- reads --------------------------------------------------------------

    def search_contacts(
        self, query: str = "", *, lifecycle: Optional[str] = None, limit: int = 20
    ) -> ConnectorResult:
        clauses, params = [], {"limit": max(1, min(limit, 100))}
        if query:
            clauses.append(
                "(first_name ILIKE :q OR last_name ILIKE :q OR email ILIKE :q OR company ILIKE :q "
                "OR :q = ANY(tags))"
            )
            params["q"] = f"%{query}%"
        if lifecycle:
            clauses.append("lifecycle = :lifecycle")
            params["lifecycle"] = lifecycle
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(f"SELECT * FROM crm_contacts {where} ORDER BY updated_at DESC LIMIT :limit"),
                params,
            ).fetchall()
        records = [dict(r._mapping) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} contact(s) found")

    def get_contact(self, contact_id: str) -> ConnectorResult:
        """One contact plus its notes. Note bodies are user-authored, so they are
        neutralized as untrusted content before returning."""
        with readonly_connection(self.ctx) as conn:
            row = conn.execute(
                text("SELECT * FROM crm_contacts WHERE id = :i OR lower(email) = lower(:i)"),
                {"i": contact_id},
            ).first()
            if row is None:
                return ConnectorResult.failure(f"No contact matching {contact_id!r}")
            notes = conn.execute(
                text("SELECT * FROM crm_notes WHERE contact_id = :c ORDER BY created_at DESC LIMIT 20"),
                {"c": row.id},
            ).fetchall()
        safe_notes, findings = wrap_many(
            [dict(n._mapping) for n in notes], source="crm_note", text_field="body"
        )
        contact = dict(row._mapping)
        contact["notes"] = safe_notes
        return ConnectorResult(
            ok=True,
            data=contact,
            summary=f"{row.first_name} {row.last_name} ({row.email or 'no email'})",
            untrusted_findings=findings,
        )

    def search_companies(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        params: dict[str, Any] = {"limit": max(1, min(limit, 100))}
        where = ""
        if query:
            where = "WHERE name ILIKE :q OR domain ILIKE :q"
            params["q"] = f"%{query}%"
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(f"SELECT * FROM crm_companies {where} ORDER BY name LIMIT :limit"), params
            ).fetchall()
        records = [dict(r._mapping) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} company/companies")

    def search_deals(
        self, *, stage: Optional[str] = None, owner: Optional[str] = None, limit: int = 50
    ) -> ConnectorResult:
        clauses, params = [], {"limit": max(1, min(limit, 200))}
        if stage:
            clauses.append("stage = :stage")
            params["stage"] = stage
        if owner:
            clauses.append("owner = :owner")
            params["owner"] = owner
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(f"SELECT * FROM crm_deals {where} ORDER BY updated_at DESC LIMIT :limit"), params
            ).fetchall()
        records = [dict(r._mapping) for r in rows]
        return ConnectorResult(ok=True, data=records, summary=f"{len(records)} deal(s)")

    def pipeline_summary(self) -> ConnectorResult:
        with readonly_connection(self.ctx) as conn:
            rows = conn.execute(
                text(
                    "SELECT stage, count(*) AS deals, COALESCE(sum(amount), 0) AS total "
                    "FROM crm_deals GROUP BY stage ORDER BY total DESC"
                )
            ).fetchall()
            recent = conn.execute(
                text(
                    "SELECT count(*) FROM crm_deals WHERE updated_at > NOW() - INTERVAL '7 days'"
                )
            ).scalar()
        stages = [
            {"stage": r.stage, "deals": r.deals, "total": float(r.total)} for r in rows
        ]
        total = sum(s["total"] for s in stages)
        return ConnectorResult(
            ok=True,
            data={"stages": stages, "total_value": total, "changed_last_7_days": recent},
            summary=f"{sum(s['deals'] for s in stages)} open deals worth {total:,.2f} across {len(stages)} stages",
        )

    def count_contacts(self, *, lifecycle: Optional[str] = None) -> int:
        """How many records a bulk operation would touch — the policy engine's
        ``record_count`` input, computed before anything is written."""
        with readonly_connection(self.ctx) as conn:
            if lifecycle:
                return int(
                    conn.execute(
                        text("SELECT count(*) FROM crm_contacts WHERE lifecycle = :l"),
                        {"l": lifecycle},
                    ).scalar()
                    or 0
                )
            return int(conn.execute(text("SELECT count(*) FROM crm_contacts")).scalar() or 0)

    def find_duplicate(self, email: str) -> ConnectorResult:
        """Duplicate check used by the lead-intake workflow."""
        with readonly_connection(self.ctx) as conn:
            row = conn.execute(
                text("SELECT * FROM crm_contacts WHERE lower(email) = lower(:e)"), {"e": email or ""}
            ).first()
        return ConnectorResult(
            ok=True,
            data=dict(row._mapping) if row else None,
            summary="duplicate found" if row else "no duplicate",
        )

    # -- writes -------------------------------------------------------------

    def create_note(self, *, body: str, contact_id: Optional[str] = None, deal_id: Optional[str] = None) -> ConnectorResult:
        note_id = new_id("note")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO crm_notes (id, contact_id, deal_id, body, author) "
                    "VALUES (:i, :c, :d, :b, :a)"
                ),
                {"i": note_id, "c": contact_id, "d": deal_id, "b": body, "a": self._author},
            )
        return ConnectorResult(ok=True, data={"id": note_id}, summary=f"note {note_id} created")

    def create_task(
        self,
        *,
        title: str,
        body: str = "",
        contact_id: Optional[str] = None,
        deal_id: Optional[str] = None,
        assignee: Optional[str] = None,
        due_at: Optional[str] = None,
    ) -> ConnectorResult:
        task_id = new_id("task")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO crm_tasks (id, title, body, contact_id, deal_id, assignee, "
                    "due_at, created_by) VALUES (:i, :t, :b, :c, :d, :a, "
                    "CAST(NULLIF(:du,'') AS TIMESTAMPTZ), :cb)"
                ),
                {
                    "i": task_id,
                    "t": title,
                    "b": body,
                    "c": contact_id,
                    "d": deal_id,
                    "a": assignee,
                    "du": due_at or "",
                    "cb": self._author,
                },
            )
        return ConnectorResult(ok=True, data={"id": task_id}, summary=f"task '{title}' created")

    def tag_lead(
        self,
        contact_id: str,
        *,
        add_tags: Optional[list[str]] = None,
        remove_tags: Optional[list[str]] = None,
        lifecycle: Optional[str] = None,
    ) -> ConnectorResult:
        with workspace_connection(self.ctx) as conn:
            row = conn.execute(
                text("SELECT * FROM crm_contacts WHERE id = :i OR lower(email) = lower(:i)"),
                {"i": contact_id},
            ).first()
            if row is None:
                return ConnectorResult.failure(f"No contact matching {contact_id!r}")
            tags = set(row.tags or [])
            tags.update(add_tags or [])
            tags.difference_update(remove_tags or [])
            conn.execute(
                text(
                    "UPDATE crm_contacts SET tags = :t, lifecycle = COALESCE(:l, lifecycle), "
                    "updated_at = NOW() WHERE id = :i"
                ),
                {"t": sorted(tags), "l": lifecycle, "i": row.id},
            )
        return ConnectorResult(
            ok=True,
            data={"id": row.id, "tags": sorted(tags), "lifecycle": lifecycle or row.lifecycle},
            summary=f"tags now {sorted(tags)}" + (f", lifecycle {lifecycle}" if lifecycle else ""),
        )

    def update_contact_field(self, contact_id: str, *, field: str, value: Any) -> ConnectorResult:
        if field not in UPDATABLE_CONTACT_FIELDS:
            return ConnectorResult.failure(
                f"{field!r} is not an updatable contact field. "
                f"Allowed: {sorted(UPDATABLE_CONTACT_FIELDS)}"
            )
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                # `field` is validated against the allowlist above; the value is
                # always a bind parameter.
                text(f"UPDATE crm_contacts SET {field} = :v, updated_at = NOW() "
                     "WHERE id = :i OR lower(email) = lower(:i)"),
                {"v": value, "i": contact_id},
            )
            if result.rowcount == 0:
                return ConnectorResult.failure(f"No contact matching {contact_id!r}")
        return ConnectorResult(ok=True, data={"id": contact_id, field: value}, summary=f"{field} set to {value!r}")

    def create_contact(
        self,
        *,
        first_name: str,
        last_name: str = "",
        email: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        lifecycle: str = "lead",
        tags: Optional[list[str]] = None,
        properties: Optional[dict[str, Any]] = None,
    ) -> ConnectorResult:
        contact_id = new_id("con")
        with workspace_connection(self.ctx) as conn:
            conn.execute(
                text(
                    "INSERT INTO crm_contacts (id, first_name, last_name, email, company, title, "
                    "lifecycle, tags, properties) VALUES (:i, :f, :l, :e, :c, :t, :lc, :g, "
                    "CAST(:p AS JSONB))"
                ),
                {
                    "i": contact_id,
                    "f": first_name,
                    "l": last_name,
                    "e": (email or "").lower() or None,
                    "c": company,
                    "t": title,
                    "lc": lifecycle,
                    "g": list(tags or []),
                    "p": json.dumps(properties or {}, default=str),
                },
            )
        return ConnectorResult(
            ok=True,
            data={"id": contact_id},
            summary=f"contact {first_name} {last_name} created",
        )

    def create_company(
        self,
        *,
        name: str,
        domain: Optional[str] = None,
        industry: Optional[str] = None,
        size: Optional[str] = None,
        description: Optional[str] = None,
    ) -> ConnectorResult:
        """Add a company, or return the existing one with the same name or web domain (never a duplicate)."""
        name = " ".join(str(name or "").split())
        if not name:
            return ConnectorResult.failure("name is required: the company's name, e.g. 'Globex Corp'.")
        site = _domain(domain)
        with workspace_connection(self.ctx) as conn:
            existing = conn.execute(
                text(
                    "SELECT id, name, domain FROM crm_companies WHERE lower(name) = lower(:n) "
                    "OR (:d <> '' AND lower(domain) = :d) ORDER BY created_at LIMIT 1"
                ),
                {"n": name, "d": site},
            ).first()
            if existing is not None:
                return ConnectorResult(
                    ok=True,
                    data={"id": existing.id, "name": existing.name, "domain": existing.domain, "existing": True},
                    summary=f"company {existing.name} already exists ({existing.id}); nothing new was created",
                )
            company_id = new_id("co")
            conn.execute(
                text(
                    "INSERT INTO crm_companies (id, name, domain, industry, size, properties) "
                    "VALUES (:i, :n, :d, :ind, :s, CAST(:p AS JSONB))"
                ),
                {
                    "i": company_id,
                    "n": name,
                    "d": site or None,
                    "ind": (industry or "").strip() or None,
                    "s": (size or "").strip() or None,
                    "p": json.dumps({"description": (description or "").strip(), "created_by": self._author}),
                },
            )
        return ConnectorResult(
            ok=True,
            data={"id": company_id, "name": name, "domain": site or None, "existing": False},
            summary=f"company {name} created",
        )

    def create_deal(
        self,
        *,
        name: str,
        amount: Any = 0,
        stage: str = "new",
        close_date: Optional[str] = None,
        company_id: Optional[str] = None,
        contact_id: Optional[str] = None,
        currency: str = "USD",
    ) -> ConnectorResult:
        """Add a deal linked to a company and a contact, or return the existing deal of the same name there."""
        name, value, closes, problem = check_deal_fields(name, amount, close_date)
        if problem:
            return ConnectorResult.failure(problem)
        stage = str(stage or "new").strip() or "new"
        with workspace_connection(self.ctx) as conn:
            if company_id and conn.execute(
                text("SELECT 1 FROM crm_companies WHERE id = :i"), {"i": company_id}
            ).first() is None:
                return ConnectorResult.failure(
                    f"No company {company_id!r}. Find it with crm_search_companies or add it with crm_create_company."
                )
            contact = None
            if contact_id:
                contact = conn.execute(
                    text("SELECT id FROM crm_contacts WHERE id = :i OR lower(email) = lower(:i) LIMIT 1"),
                    {"i": contact_id},
                ).scalar()
                if contact is None:
                    return ConnectorResult.failure(
                        f"No contact {contact_id!r}. Find it with crm_search_contacts or add it with crm_create_contact."
                    )
            existing = conn.execute(
                text(
                    "SELECT id, name, stage, amount FROM crm_deals WHERE lower(name) = lower(:n) "
                    "AND company_id IS NOT DISTINCT FROM :c ORDER BY created_at LIMIT 1"
                ),
                {"n": name, "c": company_id or None},
            ).first()
            if existing is not None:
                return ConnectorResult(
                    ok=True,
                    data={"id": existing.id, "name": existing.name, "stage": existing.stage,
                          "amount": str(existing.amount), "existing": True},
                    summary=f"deal {existing.name} already exists ({existing.id}); nothing new was created",
                )
            deal_id = new_id("deal")
            conn.execute(
                text(
                    "INSERT INTO crm_deals (id, name, contact_id, company_id, stage, amount, currency, "
                    "close_date, owner) VALUES (:i, :n, :c, :co, :s, :a, :cur, :cd, :o)"
                ),
                {
                    "i": deal_id,
                    "n": name,
                    "c": contact,
                    "co": company_id or None,
                    "s": stage,
                    "a": value,
                    "cur": (currency or "USD").strip().upper()[:3] or "USD",
                    "cd": closes,
                    "o": self._author,
                },
            )
        return ConnectorResult(
            ok=True,
            data={"id": deal_id, "name": name, "stage": stage, "amount": str(value), "company_id": company_id or None,
                  "contact_id": contact, "existing": False},
            summary=f"deal {name} created ({stage}, {value})",
        )

    def update_deal_stage(self, deal_id: str, *, stage: str) -> ConnectorResult:
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                text("UPDATE crm_deals SET stage = :s, updated_at = NOW() WHERE id = :i"),
                {"s": stage, "i": deal_id},
            )
            if result.rowcount == 0:
                return ConnectorResult.failure(f"No deal {deal_id!r}")
        return ConnectorResult(ok=True, data={"id": deal_id, "stage": stage}, summary=f"deal moved to {stage}")

    def bulk_update_contacts(
        self, *, lifecycle_filter: Optional[str], field: str, value: Any
    ) -> ConnectorResult:
        if field not in UPDATABLE_CONTACT_FIELDS:
            return ConnectorResult.failure(f"{field!r} is not an updatable contact field")
        params: dict[str, Any] = {"v": value}
        where = ""
        if lifecycle_filter:
            where = "WHERE lifecycle = :lf"
            params["lf"] = lifecycle_filter
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(
                text(f"UPDATE crm_contacts SET {field} = :v, updated_at = NOW() {where}"), params
            )
        return ConnectorResult(
            ok=True, data={"updated": result.rowcount}, summary=f"{result.rowcount} contact(s) updated"
        )

    def delete_record(self, *, record_type: str, record_id: str) -> ConnectorResult:
        tables = {"contact": "crm_contacts", "deal": "crm_deals", "company": "crm_companies"}
        table = tables.get(record_type)
        if table is None:
            return ConnectorResult.failure(f"Unknown record type {record_type!r}")
        with workspace_connection(self.ctx) as conn:
            result = conn.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": record_id})
        return ConnectorResult(
            ok=result.rowcount > 0,
            data={"deleted": result.rowcount},
            summary=f"{result.rowcount} {record_type}(s) deleted",
        )
