"""
Eval world: mocked tools built from a case's fixtures
=====================================================

Every tool effect is replaced by a responder over the case's fixtures, so evals never reach a real CRM,
mailbox, calendar, ledger or campaign database. The policy check, the guard, the approval queue and the
chat history are the real ones, running against a throwaway workspace.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from bizos.connectors.base import ConnectorResult

#: Integrations every eval workspace has connected (evals/cases.yaml header).
CONNECTED = frozenset({"crm", "email", "calendar", "youtube", "n8n"})


def _words(text: Any) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9@._-]+", str(text or "").casefold()) if len(w) > 1]


def _matches(record: dict, query: Any) -> bool:
    words = _words(query)
    if not words:
        return True
    hay = " ".join(str(v) for v in record.values()).casefold()
    return any(w in hay for w in words)


def _contact(raw: dict) -> dict:
    first, _, last = str(raw.get("name") or "").partition(" ")
    return {**raw, "first_name": raw.get("first_name", first), "last_name": raw.get("last_name", last)}


class World:
    """The fixtures of one case, and a mocked effect for every tool."""

    def __init__(self, fixtures: Optional[dict] = None) -> None:
        f = dict(fixtures or {})
        self.contacts = [_contact(c) for c in f.get("contacts") or []]
        self.deals = list(f.get("deals") or [])
        self.users = list(f.get("users") or [])
        self.actions = list(f.get("actions") or [])
        self.billables = list(f.get("billables") or [])
        self.threads = list(f.get("email_threads") or [])
        self.events = list(f.get("events") or [])
        self.prospects = list(f.get("prospects") or [])
        #: Any read tool's result, given directly: {tool: data}.
        self.results = dict(f.get("results") or {})
        self.ran: list[tuple[str, dict]] = []

    # ------------------------------------------------------------------ reads

    def _first(self, rows: list[dict], key: str, value: Any) -> Optional[dict]:
        return next((r for r in rows if str(r.get(key)) == str(value)), None)

    def read(self, name: str, args: dict) -> Optional[ConnectorResult]:
        q = args.get("query") or args.get("name") or args.get("company") or ""
        if name in self.results:
            data = self.results[name]
            return ConnectorResult(ok=True, data=data, summary=f"{len(data) if isinstance(data, list) else 1} result(s)")
        if name == "crm_search_contacts":
            rows = [c for c in self.contacts if _matches(c, q)]
            return ConnectorResult(ok=True, data=rows, summary=f"{len(rows)} contact(s)")
        if name == "crm_get_contact":
            row = self._first(self.contacts, "id", args.get("contact_id"))
            return ConnectorResult(ok=True, data=row, summary="contact") if row else \
                ConnectorResult.failure(f"No contact {args.get('contact_id')!r}")
        if name == "crm_search_companies":
            names = sorted({r.get("company") for r in self.contacts + self.deals if r.get("company")})
            rows = [{"name": n} for n in names if _matches({"name": n}, q)]
            return ConnectorResult(ok=True, data=rows, summary=f"{len(rows)} company(ies)")
        if name == "crm_search_deals":
            rows = [d for d in self.deals if _matches(d, q)]
            if args.get("stage"):
                rows = [d for d in rows if str(d.get("stage")).casefold() == str(args["stage"]).casefold()]
            return ConnectorResult(ok=True, data=rows, summary=f"{len(rows)} deal(s)")
        if name == "crm_pipeline_summary":
            stages: dict[str, int] = {}
            for d in self.deals:
                stages[str(d.get("stage"))] = stages.get(str(d.get("stage")), 0) + 1
            return ConnectorResult(ok=True, data={"by_stage": stages, "open_deals": len(self.deals)}, summary="pipeline")
        if name == "email_search_threads":
            rows = [{"thread_id": t.get("id"), "from": t.get("from"), "subject": t.get("subject", ""),
                     "snippet": str(t.get("body", ""))[:80]} for t in self.threads if _matches(t, q) or not q]
            if not rows and q:
                rows = [{"thread_id": t.get("id"), "from": t.get("from"), "subject": t.get("subject", "")}
                        for t in self.threads]
            return ConnectorResult(ok=True, data=rows, summary=f"{len(rows)} thread(s)")
        if name == "email_read_thread":
            t = self._first(self.threads, "id", args.get("thread_id"))
            if not t:
                return ConnectorResult.failure(f"No thread {args.get('thread_id')!r}")
            data = {"thread_id": t["id"], "subject": t.get("subject", ""),
                    "messages": [{"from": t.get("from"), "to": t.get("to", ["me@ourco.in"]), "body": t.get("body", "")}]}
            return ConnectorResult(ok=True, data=data, summary="thread")
        if name == "admin_list_users":
            return ConnectorResult(ok=True, data=self.users, summary=f"{len(self.users)} user(s)")
        if name == "approvals_list":
            return ConnectorResult(ok=True, data=self.actions, summary=f"{len(self.actions)} request(s)")
        if name == "approvals_get":
            row = self._first(self.actions, "id", args.get("action_id"))
            return ConnectorResult(ok=True, data=row, summary="request") if row else \
                ConnectorResult.failure(f"No action {args.get('action_id')!r}")
        if name == "finance_list_pending_billables":
            return self.billables_for(args)
        if name == "finance_list_invoices":
            return ConnectorResult(ok=True, data={"invoices": []}, summary="0 invoice(s)")
        if name == "calendar_list_events":
            return ConnectorResult(ok=True, data=self.events, summary=f"{len(self.events)} event(s)")
        if name == "calendar_check_availability":
            return ConnectorResult(ok=True, data={"free": [{"start": args.get("start"), "end": args.get("end")}],
                                                  "busy_count": 0}, summary="1 free window(s)")
        if name in ("memory_search", "memory_history"):
            return ConnectorResult(ok=True, data=[], summary="0 item(s)")
        if name in ("show_contacts", "show_campaigns"):
            return ConnectorResult(ok=True, data=self.prospects, summary=f"{len(self.prospects)} row(s)")
        return None

    def billables_for(self, args: dict) -> ConnectorResult:
        customer = str(args.get("customer") or args.get("company") or "")
        items = [b for b in self.billables if not customer or customer.casefold() in str(b.get("customer")).casefold()
                 or str(b.get("customer")).casefold() in customer.casefold()]
        total = sum(float(b.get("amount") or 0) for b in items)
        return ConnectorResult(ok=True, data={"customer": customer or None, "items": items, "total": total},
                               summary=f"{len(items)} pending billable(s) totaling {total:,.2f}")

    # ------------------------------------------------------------------ effects

    #: The status an approval write needs (as the real store enforces), by tool.
    _NEEDS_STATUS = {"approvals_approve": "PENDING_APPROVAL", "approvals_reject": "PENDING_APPROVAL",
                     "approvals_request_changes": "PENDING_APPROVAL", "approvals_submit": "DRAFT",
                     "approvals_cancel": None}

    def _approval_problem(self, name: str, args: dict) -> Optional[str]:
        ref = str(args.get("action_id") or args.get("match") or "")
        row = self._first(self.actions, "id", ref) or next(
            (a for a in self.actions if ref and ref.casefold() in str(a.get("title", "")).casefold()), None)
        if row is None:
            return f"No action {ref!r} that you can see."
        needed = self._NEEDS_STATUS[name]
        if needed and row.get("status") != needed:
            return f"{row['id']} is {row.get('status')}, so this cannot be done now. Nothing was changed."
        return None

    def effect(self, name: str) -> Callable:
        def run(scope: Any, args: dict) -> ConnectorResult:
            self.ran.append((name, dict(args)))
            if name in self._NEEDS_STATUS:
                problem = self._approval_problem(name, dict(args))
                if problem:
                    return ConnectorResult.failure(problem)
            found = self.read(name, dict(args))
            if found is not None:
                return found
            return ConnectorResult(ok=True, data={"id": f"{name}_1", **{k: v for k, v in args.items()
                                                                        if not str(k).startswith("_")}},
                                   summary=f"{name} done")
        return run

    def schedule_lookup(self, real: Callable) -> Callable:
        """``automation.get`` over ``results.schedule_list`` first (the schedule prechecks read it), then the
        workspace. A fixture schedule belongs to the case's user and is active unless it says otherwise."""

        def get(ctx: Any, task_id: str):
            rows = self.results.get("schedule_list")
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("id") == task_id:
                    return {"title": "", "instruction": "", "frequency": "weekly", "time_of_day": "09:00",
                            "weekdays": ["mon"], "every_minutes": None, "timezone": "Asia/Kolkata",
                            "status": "active", "next_run_at": "2026-11-16T03:30:00+00:00", **row,
                            "created_by": ctx.user_id}
            return real(ctx, task_id)

        return get

    def find_user(self, real: Callable) -> Callable:
        """``_find_user`` over the fixture users first (merged admin tools plan against it), then the workspace."""
        from bizos.control.models import User
        from bizos.types import Role

        def find(scope: Any, ref: Any):
            wanted = str(ref or "").strip().casefold()
            for match in (lambda u: str(u.get("id", "")).casefold() == wanted,
                          lambda u: str(u.get("email", "")).casefold() == wanted,
                          lambda u: str(u.get("name", "")).casefold() == wanted,
                          lambda u: bool(wanted) and wanted in str(u.get("name", "")).casefold()):
                found = [u for u in self.users if match(u)]
                if len(found) == 1:
                    u = found[0]
                    return User(id=u["id"], client_id=scope.ctx.client_id, email=u.get("email", ""),
                                display_name=u.get("name", ""), roles=frozenset(Role(r) for r in u.get("roles") or ["VIEWER"]),
                                status=u.get("status", "ACTIVE"), data_scopes=frozenset(u.get("data_scopes") or []),
                                department_modes=dict(u.get("departments") or {}))
            return real(scope, ref)

        return find

    def bulk_inputs(self, scope: Any, args: dict) -> dict:
        stage = str(args.get("lifecycle_stage") or "")
        rows = self.contacts if stage.upper() == "ALL" else [
            c for c in self.contacts if str(c.get("lifecycle_stage", "")).casefold() == stage.casefold()]
        return {"record_count": max(1, len(rows)), "fields": (str(args.get("field", "")),)}

    def campaign_records(self) -> dict:
        people = [{"email": p.get("email"), "name": p.get("name"), "campaigns": []} for p in self.prospects]
        return {"campaigns": [], "people": people}
