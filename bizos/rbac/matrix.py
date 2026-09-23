"""
Permission Matrix Templates
===========================

The five reusable role→permission shapes. Declaring templates rather than
repeating a five-entry dict per tool is what makes §7's "avoid hard-coding every
policy across many files" achievable: a tool declares *which shape* it has, and
the shapes are defined once, here.

ADMIN mirrors OPERATOR for **execution**. Admin authority is over configuration
(users, roles, connectors, policies, domains), not a bypass of the approval
controls — an admin who could unilaterally execute a CRITICAL action would make
the approval queue advisory.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from bizos.types import Role, ToolPermission

P = ToolPermission


def _matrix(viewer: P, drafter: P, approver: P, operator: P) -> Mapping[Role, ToolPermission]:
    """Freeze a role→permission mapping; ADMIN always mirrors OPERATOR."""
    return MappingProxyType(
        {
            Role.VIEWER: viewer,
            Role.DRAFTER: drafter,
            Role.APPROVER: approver,
            Role.OPERATOR: operator,
            Role.ADMIN: operator,
        }
    )


#: Read-only lookups. Everyone who can use the workspace can read it, subject to
#: data classification, which the policy engine applies separately.
READ_ALL = _matrix(P.ALLOWED, P.ALLOWED, P.ALLOWED, P.ALLOWED)

#: Reads of CONFIDENTIAL/RESTRICTED material — a Viewer may ask questions but not
#: pull raw sensitive records.
READ_SENSITIVE = _matrix(P.DENIED, P.ALLOWED, P.ALLOWED, P.ALLOWED)

#: Low-risk writes confined to the workspace (an internal note, a task, a tag).
#: Nothing leaves the customer's own database.
WRITE_INTERNAL = _matrix(P.DENIED, P.ALLOWED, P.ALLOWED, P.ALLOWED)

#: Producing a draft. Never reaches an external system by construction.
DRAFT_ONLY_WRITE = _matrix(P.DENIED, P.ALLOWED, P.ALLOWED, P.ALLOWED)

#: Protected external writes (send an email, create a calendar event on a real
#: calendar). A Drafter may only prepare one; an Approver may bless and then
#: execute the queued call; an Operator may carry it out directly when mode allows.
WRITE_PROTECTED = _matrix(P.DENIED, P.DRAFT_ONLY, P.EXECUTE_AFTER_APPROVAL, P.ALLOWED)

#: Destructive or CRITICAL operations. No one originates them unattended; an
#: Approver or Operator may execute only an action already APPROVED.
WRITE_CRITICAL = _matrix(P.DENIED, P.DENIED, P.EXECUTE_AFTER_APPROVAL, P.EXECUTE_AFTER_APPROVAL)

#: Administrative configuration changes.
ADMIN_ONLY = MappingProxyType(
    {
        Role.VIEWER: P.DENIED,
        Role.DRAFTER: P.DENIED,
        Role.APPROVER: P.DENIED,
        Role.OPERATOR: P.DENIED,
        Role.ADMIN: P.ALLOWED,
    }
)

MATRIX_TEMPLATES: Mapping[str, Mapping[Role, ToolPermission]] = MappingProxyType(
    {
        "READ_ALL": READ_ALL,
        "READ_SENSITIVE": READ_SENSITIVE,
        "WRITE_INTERNAL": WRITE_INTERNAL,
        "DRAFT_ONLY_WRITE": DRAFT_ONLY_WRITE,
        "WRITE_PROTECTED": WRITE_PROTECTED,
        "WRITE_CRITICAL": WRITE_CRITICAL,
        "ADMIN_ONLY": ADMIN_ONLY,
    }
)
