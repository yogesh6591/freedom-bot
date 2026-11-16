"""Tenant scope for the shared acquisition database.

Prospects (and queries that read them) are isolated by ``client_id`` from the signed-in
BizOS workspace. Set the scope with :func:`tenant_scope` for the duration of an outreach
tool call or admin request.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import or_
from sqlalchemy.sql import ColumnElement

_CTX: ContextVar[str | None] = ContextVar("acquisition_client_id", default=None)


def current_client_id() -> str:
    """Active workspace client id while inside :func:`tenant_scope`."""
    cid = _CTX.get()
    if not cid:
        raise RuntimeError("Acquisition tenant scope is not set (missing client_id).")
    return cid


def client_id_or_none() -> str | None:
    return _CTX.get()


@contextmanager
def tenant_scope(client_id: str) -> Iterator[None]:
    """Bind every prospect query in this block to ``client_id``."""
    cid = (client_id or "").strip()
    if not cid:
        raise ValueError("client_id is required for acquisition tenant scope")
    token = _CTX.set(cid)
    try:
        yield
    finally:
        _CTX.reset(token)


def prospect_client_clause() -> ColumnElement:
    """SQL filter: this workspace's prospects plus legacy rows with no ``client_id`` yet."""
    from acquisition.models.prospect import Prospect

    cid = _CTX.get()
    if not cid:
        return Prospect.id.is_not(None)
    return or_(Prospect.client_id == cid, Prospect.client_id.is_(None))


def stamp_prospect(prospect) -> None:
    """Set ``client_id`` on a new prospect before insert."""
    prospect.client_id = current_client_id()