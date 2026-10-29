"""The prospect fields the chat can read and change: one catalog, used by search, edit and add.

Everything that knows a field name (the tool schemas, the query engine, the editor) reads it from here,
so adding a field means adding one line to ``FIELDS``.

``first_name`` and ``last_name`` are virtual: the database keeps one ``executive_name``; the two virtual
fields rewrite its first word / the rest of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


@dataclass(frozen=True)
class Field:
    key: str  # the name used in chat tools
    column: Optional[str]  # attribute on ``Prospect``; None for virtual fields
    label: str  # what to call it when talking to the user
    max_len: Optional[int] = None
    kind: str = "text"  # text | email | url
    fuzzy: bool = False  # typo-tolerant matching in searches
    required: bool = False  # cannot be blanked
    description: str = ""


FIELDS: tuple[Field, ...] = (
    Field("executive_name", "executive_name", "name", 255, fuzzy=True, required=True, description="Full name"),
    Field("first_name", None, "first name", 255, description="First word of the name"),
    Field("last_name", None, "last name", 255, description="Rest of the name"),
    Field("email", "email", "email", 255, kind="email", fuzzy=True, required=True),
    Field("company", "company", "company", 255, fuzzy=True),
    Field("executive_title", "executive_title", "title", 255, description="Job title"),
    Field("website", "website", "website", 512, kind="url"),
    Field("linkedin_url", "linkedin_url", "LinkedIn URL", 512, kind="url"),
    Field("revenue_band", "revenue_band", "revenue band", 100),
    Field("headcount_band", "headcount_band", "headcount band", 100),
    Field("source", "source", "source", 100),
    Field("fit_note", "fit_note", "fit note", None),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}
KEYS: tuple[str, ...] = tuple(f.key for f in FIELDS)
#: the fields that exist as database columns (searchable, sortable)
COLUMN_KEYS: tuple[str, ...] = tuple(f.key for f in FIELDS if f.column)


class FieldError(ValueError):
    """A value the user gave cannot be stored. The message is safe to show them."""


def clean_value(key: str, value: object) -> Optional[str]:
    """Validate and normalise one value. ``None`` means 'clear it' (only for optional fields)."""
    field = BY_KEY.get(key)
    if field is None:
        raise FieldError(f"'{key}' is not a prospect field. Fields: {', '.join(KEYS)}.")
    text = " ".join(str(value).split()) if value is not None else ""
    if field.key == "fit_note":
        text = str(value or "").strip()
    if not text:
        if field.required:
            raise FieldError(f"The {field.label} cannot be empty.")
        return None
    if field.kind == "email":
        text = text.lower()
        if not _EMAIL.match(text):
            raise FieldError(f"'{text}' is not a valid email address.")
    elif field.kind == "url":
        if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
            text = "https://" + text
        if " " in text or "." not in text:
            raise FieldError(f"'{text}' is not a valid web address for {field.label}.")
        if field.key == "linkedin_url" and "linkedin.com" not in text.lower():
            raise FieldError("The LinkedIn URL must be a linkedin.com address.")
    if field.max_len and len(text) > field.max_len:
        raise FieldError(f"The {field.label} is too long (max {field.max_len} characters).")
    return text


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def apply_changes(prospect: object, changes: dict[str, Optional[str]]) -> list[str]:
    """Write already-cleaned values onto a ``Prospect``. Returns the keys that actually changed."""
    changed: list[str] = []
    first, last = split_name(getattr(prospect, "executive_name", "") or "")
    name_touched = False
    for key, value in changes.items():
        field = BY_KEY[key]
        if key == "first_name":
            first, name_touched = value or "", True
        elif key == "last_name":
            last, name_touched = value or "", True
        elif key == "executive_name":
            first, last = split_name(value or "")
            name_touched = True
        else:
            if (getattr(prospect, field.column) or None) != value:
                setattr(prospect, field.column, value if value is not None else ("" if key == "company" else None))
                changed.append(key)
    if name_touched:
        new_name = " ".join(x for x in (first, last) if x)
        if not new_name:
            raise FieldError("The name cannot be empty.")
        if new_name != prospect.executive_name:
            prospect.executive_name = new_name
            changed.append("executive_name")
    return changed
