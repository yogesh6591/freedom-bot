"""Identifier helpers — short, URL-safe, sortable-enough ids."""

from __future__ import annotations

import re
from uuid import uuid4


def new_id(prefix: str = "") -> str:
    """A new random id, optionally prefixed (``act_3f2a...``)."""
    raw = uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = 40) -> str:
    """Lowercase ``a-z0-9_`` slug, safe to embed in a Postgres identifier.

    Used to derive a client's database/schema name from its name, so the
    result must never need quoting and must not start with a digit.
    """
    slug = _SLUG_RE.sub("_", value.strip().casefold()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:max_length].strip("_")
    if not slug:
        slug = "client"
    if slug[0].isdigit():
        slug = f"c_{slug}"
    return slug
