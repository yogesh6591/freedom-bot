"""
Google Drive CRM (a "FreedomBot CRM" sheet)
===========================================

For people who chose to keep their CRM records in Google Drive (bizos/crm_storage.py). Records live in
one Google Sheet in that person's Drive, named "FreedomBot CRM", with one tab each for Contacts, Companies,
Deals, Notes and Tasks. The sheet is found or made on first use and remembered.

Same methods as the workspace CRM (bizos/connectors/mock_crm.py), so the CRM tools work unchanged:
add and find contacts, companies and deals (never a duplicate), notes, tasks and a pipeline summary.
Edits and deletes of existing rows are made in the sheet itself; those calls say so and change nothing.

Every call runs through ``composio.execute`` on the person's own Google Sheets login, so their per-tool
choices in Integrations apply. Values are written as plain text (``RAW``), so a value such as ``=IMPORTXML(...)``
is stored as text and never runs as a formula. Everything read back from the sheet is outside content
(anyone with access can edit it) and is neutralized before it reaches the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bizos.connectors.base import Connector, ConnectorError, ConnectorResult
from bizos.connectors.untrusted import neutralize
from bizos.util.ids import new_id

TOOLKIT = "googlesheets"
TITLE = "FreedomBot CRM"
#: tab -> columns (the first row of each tab). Rows are read back by position, so a column is never renamed.
TABS: dict[str, tuple[str, ...]] = {
    "Contacts": ("id", "first_name", "last_name", "email", "company", "title", "lifecycle", "tags",
                 "created_by", "created_at"),
    "Companies": ("id", "name", "domain", "industry", "size", "description", "created_by", "created_at"),
    "Deals": ("id", "name", "amount", "currency", "stage", "close_date", "company_id", "contact_id", "owner",
              "created_at"),
    "Notes": ("id", "contact_id", "deal_id", "body", "author", "created_at"),
    "Tasks": ("id", "title", "body", "contact_id", "deal_id", "assignee", "due_at", "created_by", "created_at"),
}
_MAX_ROWS = 5000


def _find(data: Any, *keys: str) -> Optional[str]:
    """The first value under any of ``keys``, searching nested dicts and lists (Composio wraps responses)."""
    if isinstance(data, dict):
        for key in keys:
            if data.get(key) not in (None, ""):
                return str(data[key])
        for value in data.values():
            found = _find(value, *keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find(value, *keys)
            if found:
                return found
    return None


def _files(data: Any) -> list[dict[str, Any]]:
    """Every dict with an id and a name in a search response."""
    out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if data.get("id") and data.get("name"):
            out.append(data)
        for value in data.values():
            out += _files(value)
    elif isinstance(data, list):
        for value in data:
            out += _files(value)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scrub(records: list[dict[str, Any]], source: str) -> tuple[list[dict[str, Any]], int]:
    """Neutralize every text value read from the sheet (anyone with access can edit it)."""
    total, out = 0, []
    for record in records:
        clean: dict[str, Any] = {}
        for key, value in record.items():
            if isinstance(value, str) and value:
                value, findings = neutralize(value)
                total += len(findings)
            clean[key] = value
        clean["_untrusted_source"] = source
        out.append(clean)
    return out, total


def _matches(record: dict[str, Any], query: str, fields: tuple[str, ...]) -> bool:
    words = [w for w in query.casefold().split() if w]
    hay = " ".join(str(record.get(f) or "") for f in fields).casefold()
    return all(w in hay for w in words)


class DriveSheetCrmConnector(Connector):
    """CRM records in the person's "FreedomBot CRM" Google Sheet."""

    category = "crm"
    provider = "google_drive_sheet"

    @property
    def _owner_id(self) -> str:
        return str(self.config.get("owner_user_id") or self.ctx.user_id)

    @property
    def _owner(self):
        from bizos.connectors import composio

        return composio.as_user(self.ctx, self._owner_id)

    def _run(self, action: str, arguments: dict[str, Any]) -> Any:
        from bizos.connectors import composio

        return composio.execute(self._owner, TOOLKIT, action, arguments)

    # -- the sheet ----------------------------------------------------------

    def _sheet(self) -> tuple[str, Optional[str]]:
        """(spreadsheet id, link) of this person's CRM sheet: remembered, found by name, or made now."""
        from bizos import crm_storage

        sheet_id, url = crm_storage.sheet(self.ctx, self._owner_id)
        if sheet_id:
            return sheet_id, url
        found = self._run("GOOGLESHEETS_SEARCH_SPREADSHEETS", {"query": TITLE, "search_type": "name", "max_results": 10})
        mine = next((f for f in _files(found) if str(f.get("name")) == TITLE), None)
        if mine:
            sheet_id = str(mine["id"])
            url = mine.get("webViewLink") or f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        else:
            made = self._run("GOOGLESHEETS_CREATE_GOOGLE_SHEET1", {"title": TITLE})
            sheet_id = _find(made, "spreadsheetId", "spreadsheet_id", "id") or ""
            if not sheet_id:
                raise ConnectorError("Google Sheets did not return the new sheet's id.")
            url = _find(made, "spreadsheetUrl", "spreadsheet_url") or f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            for tab, columns in TABS.items():
                self._run("GOOGLESHEETS_ADD_SHEET", {"spreadsheet_id": sheet_id, "title": tab})
                self._append_to(sheet_id, tab, list(columns))
        crm_storage.remember_sheet(self.ctx, self._owner_id, sheet_id, url)
        return sheet_id, url

    def _append_to(self, sheet_id: str, tab: str, row: list[Any]) -> None:
        self._run("GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND", {
            "spreadsheetId": sheet_id, "range": f"{tab}!A1", "valueInputOption": "RAW",
            "insertDataOption": "INSERT_ROWS", "values": [["" if v is None else str(v) for v in row]],
        })

    def _rows(self, tab: str) -> list[dict[str, Any]]:
        sheet_id, _ = self._sheet()
        data = self._run("GOOGLESHEETS_VALUES_GET", {"spreadsheet_id": sheet_id, "range": f"{tab}!A1:Z{_MAX_ROWS}"})
        values = None
        stack = [data]
        while stack and values is None:  # the values list may be nested in Composio's wrapper
            item = stack.pop()
            if isinstance(item, dict):
                if isinstance(item.get("values"), list):
                    values = item["values"]
                else:
                    stack += list(item.values())
        columns = TABS[tab]
        rows = []
        for raw in (values or [])[1:]:  # the first row is the column names
            cells = list(raw) + [""] * (len(columns) - len(raw))
            record = {col: (str(cells[i]) if cells[i] not in (None, "") else None) for i, col in enumerate(columns)}
            if record.get("id"):
                rows.append(record)
        return rows

    def _add(self, tab: str, record: dict[str, Any]) -> str:
        sheet_id, url = self._sheet()
        self._append_to(sheet_id, tab, [record.get(col) for col in TABS[tab]])
        return url or ""

    def _read(self, tab: str, label: str, records: list[dict[str, Any]], limit: int) -> ConnectorResult:
        safe, findings = _scrub(records[: max(1, limit)], f"google_sheet_{tab.lower()}")
        _, url = self._sheet()
        return ConnectorResult(ok=True, data=safe, summary=f"{len(safe)} {label} in your FreedomBot CRM sheet",
                               untrusted_findings=findings, metadata={"provider": self.provider, "sheet_url": url})

    def _in_sheet(self, what: str) -> ConnectorResult:
        _, url = self._sheet()
        return ConnectorResult.failure(
            f"Your CRM records are kept in Google Drive, so {what} is done in the FreedomBot CRM sheet itself"
            f"{f' ({url})' if url else ''}. Nothing was changed.")

    # -- reads --------------------------------------------------------------

    def health(self) -> ConnectorResult:
        try:
            sheet_id, url = self._sheet()
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"sheet_url": url}, summary="FreedomBot CRM sheet reachable")

    def search_contacts(self, query: str = "", *, lifecycle: Optional[str] = None, limit: int = 20) -> ConnectorResult:
        try:
            rows = [r for r in self._rows("Contacts")
                    if _matches(r, query, ("first_name", "last_name", "email", "company", "tags"))
                    and (not lifecycle or str(r.get("lifecycle") or "").casefold() == lifecycle.casefold())]
            return self._read("Contacts", "contact(s) found", rows[::-1], min(limit, 100))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))

    def get_contact(self, contact_id: str) -> ConnectorResult:
        ref = str(contact_id or "").strip().casefold()
        try:
            contact = next((r for r in self._rows("Contacts")
                            if ref and ref in (str(r["id"]).casefold(), str(r.get("email") or "").casefold())), None)
            if contact is None:
                return ConnectorResult.failure(f"No contact matching {contact_id!r} in your FreedomBot CRM sheet")
            notes = [n for n in self._rows("Notes") if n.get("contact_id") == contact["id"]][-20:][::-1]
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        (safe,), findings = _scrub([contact], "google_sheet_contacts")
        safe_notes, note_findings = _scrub(notes, "crm_note")
        safe["notes"] = safe_notes
        name = f"{safe.get('first_name') or ''} {safe.get('last_name') or ''}".strip()
        return ConnectorResult(ok=True, data=safe, summary=f"{name} ({safe.get('email') or 'no email'})",
                               untrusted_findings=findings + note_findings, metadata={"provider": self.provider})

    def search_companies(self, query: str = "", *, limit: int = 20) -> ConnectorResult:
        try:
            rows = [r for r in self._rows("Companies") if _matches(r, query, ("name", "domain"))]
            return self._read("Companies", "company/companies", sorted(rows, key=lambda r: str(r.get("name") or "")),
                              min(limit, 100))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))

    def search_deals(self, *, stage: Optional[str] = None, owner: Optional[str] = None, limit: int = 50) -> ConnectorResult:
        try:
            rows = [r for r in self._rows("Deals")
                    if (not stage or str(r.get("stage") or "").casefold() == stage.casefold())
                    and (not owner or str(r.get("owner") or "") == str(owner))]
            return self._read("Deals", "deal(s)", rows[::-1], min(limit, 200))
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))

    def pipeline_summary(self) -> ConnectorResult:
        try:
            deals = self._rows("Deals")
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        stages: dict[str, dict[str, Any]] = {}
        for deal in deals:
            try:
                amount = float(str(deal.get("amount") or 0).replace(",", ""))
            except ValueError:
                amount = 0.0
            entry = stages.setdefault(str(deal.get("stage") or "new"), {"stage": str(deal.get("stage") or "new"),
                                                                       "deals": 0, "total": 0.0})
            entry["deals"] += 1
            entry["total"] += amount
        ordered = sorted(stages.values(), key=lambda s: -s["total"])
        total = sum(s["total"] for s in ordered)
        safe, findings = _scrub(ordered, "google_sheet_deals")
        return ConnectorResult(
            ok=True, data={"stages": safe, "total_value": total},
            summary=f"{len(deals)} deals worth {total:,.2f} across {len(ordered)} stages in your FreedomBot CRM sheet",
            untrusted_findings=findings, metadata={"provider": self.provider})

    def count_contacts(self, *, lifecycle: Optional[str] = None) -> int:
        rows = self._rows("Contacts")
        if lifecycle:
            return sum(1 for r in rows if str(r.get("lifecycle") or "").casefold() == lifecycle.casefold())
        return len(rows)

    def find_duplicate(self, email: str) -> ConnectorResult:
        ref = str(email or "").strip().casefold()
        try:
            row = next((r for r in self._rows("Contacts") if ref and str(r.get("email") or "").casefold() == ref), None)
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data=row, summary="duplicate found" if row else "no duplicate")

    # -- writes -------------------------------------------------------------

    def create_contact(self, *, first_name: str, last_name: str = "", email: Optional[str] = None,
                       company: Optional[str] = None, title: Optional[str] = None, lifecycle: str = "lead",
                       tags: Optional[list[str]] = None, properties: Optional[dict[str, Any]] = None) -> ConnectorResult:
        mail = (email or "").strip().lower() or None
        try:
            if mail:
                same = next((r for r in self._rows("Contacts") if str(r.get("email") or "").casefold() == mail), None)
                if same:
                    return ConnectorResult(
                        ok=True, data={"id": same["id"], "existing": True},
                        summary=f"contact {mail} already exists in your FreedomBot CRM sheet ({same['id']}); nothing new was added",
                        metadata={"provider": self.provider})
            contact_id = new_id("con")
            url = self._add("Contacts", {
                "id": contact_id, "first_name": first_name, "last_name": last_name, "email": mail, "company": company,
                "title": title, "lifecycle": lifecycle, "tags": ", ".join(tags or []), "created_by": self._owner_id,
                "created_at": _now()})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": contact_id, "sheet_url": url, "existing": False},
                               summary=f"contact {first_name} {last_name} added to your FreedomBot CRM sheet".replace("  ", " "),
                               metadata={"provider": self.provider})

    def create_company(self, *, name: str, domain: Optional[str] = None, industry: Optional[str] = None,
                       size: Optional[str] = None, description: Optional[str] = None) -> ConnectorResult:
        from bizos.connectors.mock_crm import _domain

        name = " ".join(str(name or "").split())
        if not name:
            return ConnectorResult.failure("name is required: the company's name, e.g. 'Globex Corp'.")
        site = _domain(domain)
        try:
            same = next((r for r in self._rows("Companies")
                         if str(r.get("name") or "").casefold() == name.casefold()
                         or (site and _domain(r.get("domain")) == site)), None)
            if same:
                return ConnectorResult(
                    ok=True, data={"id": same["id"], "name": same.get("name"), "domain": same.get("domain"), "existing": True},
                    summary=f"company {same.get('name')} already exists in your FreedomBot CRM sheet ({same['id']}); "
                            "nothing new was added",
                    metadata={"provider": self.provider})
            company_id = new_id("co")
            url = self._add("Companies", {
                "id": company_id, "name": name, "domain": site or None, "industry": (industry or "").strip() or None,
                "size": (size or "").strip() or None, "description": (description or "").strip() or None,
                "created_by": self._owner_id, "created_at": _now()})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": company_id, "name": name, "domain": site or None, "sheet_url": url,
                                              "existing": False},
                               summary=f"company {name} added to your FreedomBot CRM sheet", metadata={"provider": self.provider})

    def create_deal(self, *, name: str, amount: Any = 0, stage: str = "new", close_date: Optional[str] = None,
                    company_id: Optional[str] = None, contact_id: Optional[str] = None,
                    currency: str = "USD") -> ConnectorResult:
        from bizos.connectors.mock_crm import check_deal_fields

        name, value, closes, problem = check_deal_fields(name, amount, close_date)
        if problem:
            return ConnectorResult.failure(problem)
        stage = str(stage or "new").strip() or "new"
        try:
            if company_id and not any(r["id"] == company_id for r in self._rows("Companies")):
                return ConnectorResult.failure(
                    f"No company {company_id!r} in your FreedomBot CRM sheet. Find it with crm_search_companies or "
                    "add it with crm_create_company.")
            contact = None
            if contact_id:
                ref = str(contact_id).strip().casefold()
                contact = next((r["id"] for r in self._rows("Contacts")
                                if ref in (str(r["id"]).casefold(), str(r.get("email") or "").casefold())), None)
                if contact is None:
                    return ConnectorResult.failure(
                        f"No contact {contact_id!r} in your FreedomBot CRM sheet. Find it with crm_search_contacts or "
                        "add it with crm_create_contact.")
            same = next((r for r in self._rows("Deals") if str(r.get("name") or "").casefold() == name.casefold()
                         and (r.get("company_id") or None) == (company_id or None)), None)
            if same:
                return ConnectorResult(
                    ok=True, data={"id": same["id"], "name": same.get("name"), "stage": same.get("stage"),
                                   "amount": same.get("amount"), "existing": True},
                    summary=f"deal {same.get('name')} already exists in your FreedomBot CRM sheet ({same['id']}); "
                            "nothing new was added",
                    metadata={"provider": self.provider})
            deal_id = new_id("deal")
            url = self._add("Deals", {
                "id": deal_id, "name": name, "amount": str(value), "currency": (currency or "USD").strip().upper()[:3] or "USD",
                "stage": stage, "close_date": closes.isoformat() if closes else None, "company_id": company_id or None,
                "contact_id": contact, "owner": self._owner_id, "created_at": _now()})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(
            ok=True,
            data={"id": deal_id, "name": name, "stage": stage, "amount": str(value), "company_id": company_id or None,
                  "contact_id": contact, "sheet_url": url, "existing": False},
            summary=f"deal {name} added to your FreedomBot CRM sheet ({stage}, {value})", metadata={"provider": self.provider})

    def create_note(self, *, body: str, contact_id: Optional[str] = None, deal_id: Optional[str] = None) -> ConnectorResult:
        note_id = new_id("note")
        try:
            self._add("Notes", {"id": note_id, "contact_id": contact_id, "deal_id": deal_id, "body": body,
                                "author": self._owner_id, "created_at": _now()})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": note_id}, summary=f"note {note_id} added to your FreedomBot CRM sheet",
                               metadata={"provider": self.provider})

    def create_task(self, *, title: str, body: str = "", contact_id: Optional[str] = None, deal_id: Optional[str] = None,
                    assignee: Optional[str] = None, due_at: Optional[str] = None) -> ConnectorResult:
        task_id = new_id("task")
        try:
            self._add("Tasks", {"id": task_id, "title": title, "body": body, "contact_id": contact_id, "deal_id": deal_id,
                                "assignee": assignee, "due_at": due_at, "created_by": self._owner_id, "created_at": _now()})
        except ConnectorError as exc:
            return ConnectorResult.failure(str(exc))
        return ConnectorResult(ok=True, data={"id": task_id}, summary=f"task '{title}' added to your FreedomBot CRM sheet",
                               metadata={"provider": self.provider})

    # Changing or removing rows that already exist is done in the sheet itself.

    def tag_lead(self, contact_id: str, **_: Any) -> ConnectorResult:
        return self._in_sheet("tagging a contact")

    def update_contact_field(self, contact_id: str, *, field: str, value: Any) -> ConnectorResult:
        return self._in_sheet("changing a contact")

    def update_deal_stage(self, deal_id: str, *, stage: str) -> ConnectorResult:
        return self._in_sheet("moving a deal to another stage")

    def bulk_update_contacts(self, **_: Any) -> ConnectorResult:
        return self._in_sheet("changing many contacts at once")

    def delete_record(self, **_: Any) -> ConnectorResult:
        return self._in_sheet("deleting a record")
