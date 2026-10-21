"""Parse prospect CSV / Excel uploads into row dicts."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "company": ("company", "company name", "organization", "org"),
    "executive_name": (
        "executive name",
        "executive",
        "name",
        "full name",
        "contact name",
    ),
    "email": ("email", "email address", "work email"),
    "website": ("website", "company website", "url", "domain"),
    "linkedin_url": ("linkedin url", "linkedin", "linkedin profile"),
    "executive_title": ("executive title", "title", "job title", "role"),
    "revenue_band": ("revenue band", "revenue", "rev band"),
    "headcount_band": ("headcount band", "headcount", "employees", "company size"),
    "source": ("source", "lead source"),
    "fit_note": ("fit note", "notes", "note", "comments"),
}


class ProspectImportError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _normalize_header(value: str) -> str:
    cleaned = re.sub(r"[\s_]+", " ", (value or "").strip().lower())
    return cleaned


def _map_headers(raw_headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    normalized = [_normalize_header(h) for h in raw_headers]
    for field, aliases in HEADER_ALIASES.items():
        for idx, header in enumerate(normalized):
            if header in aliases:
                mapping[field] = idx
                break
    if "company" not in mapping or "executive_name" not in mapping or "email" not in mapping:
        raise ProspectImportError(
            "File must include Company, Executive Name, and Email columns."
        )
    return mapping


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text.replace(".", "", 1).isdigit():
        text = text[:-2]
    return text


def parse_prospect_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise ProspectImportError("CSV file is empty.") from exc
    mapping = _map_headers(headers)
    rows: list[dict[str, str]] = []
    for raw in reader:
        if not any((cell or "").strip() for cell in raw):
            continue
        item: dict[str, str] = {}
        for field, idx in mapping.items():
            item[field] = _cell_str(raw[idx] if idx < len(raw) else "")
        rows.append(item)
    if not rows:
        raise ProspectImportError("No data rows found in the CSV.")
    return rows


def parse_prospect_xlsx(content: bytes) -> list[dict[str, str]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ProspectImportError(
            "Excel support requires openpyxl. Install dependencies and retry."
        ) from exc

    workbook = load_workbook(filename=io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    iterator = sheet.iter_rows(values_only=True)
    try:
        header_row = next(iterator)
    except StopIteration as exc:
        raise ProspectImportError("Excel file is empty.") from exc
    headers = [_cell_str(cell) for cell in header_row]
    mapping = _map_headers(headers)
    rows: list[dict[str, str]] = []
    for raw in iterator:
        values = list(raw or ())
        if not any(_cell_str(cell) for cell in values):
            continue
        item: dict[str, str] = {}
        for field, idx in mapping.items():
            item[field] = _cell_str(values[idx] if idx < len(values) else "")
        rows.append(item)
    if not rows:
        raise ProspectImportError("No data rows found in the Excel file.")
    return rows


def parse_prospect_upload(*, filename: str, content: bytes) -> list[dict[str, str]]:
    name = (filename or "").strip().lower()
    if not content:
        raise ProspectImportError("Uploaded file is empty.")
    if name.endswith(".csv"):
        return parse_prospect_csv(content)
    if name.endswith(".xlsx"):
        return parse_prospect_xlsx(content)
    if name.endswith(".xls"):
        raise ProspectImportError("Legacy .xls is not supported. Save as .xlsx or .csv.")
    raise ProspectImportError("Unsupported file type. Upload a .csv or .xlsx file.")
