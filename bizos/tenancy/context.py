"""
Tenant Context
==============

The resolved identity of "who is asking, on behalf of which client, in what
role". Every workspace read or write requires one, so there is no way to reach a
client's data without first having named the client.

The context is carried in a :mod:`contextvars` variable rather than passed
implicitly through globals, so it is correct under asyncio concurrency: two
requests for two different clients handled on the same event loop each see their
own value.

The context is **derived from verified credentials only** (a validated JWT, or an
explicit server-side call such as the scheduler). It is never built from a
request body or a query parameter.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Iterator, Optional

from bizos.types import DeploymentType, ExecutionMode, Role, role_rank

#: Principal used by scheduled/automated runs that have no human caller.
SYSTEM_USER_ID = "__system__"

#: Principal AgentOS's auth layer assigns to scheduler-triggered runs.
SCHEDULER_USER_ID = "__scheduler__"

#: Sentinel for an unauthenticated caller. Never grants any role.
ANON_USER_ID = "anon"


@dataclass(frozen=True)
class TenantContext:
    """Everything the governance layer needs to decide about one request.

    Frozen: a tool or hook downstream must not be able to widen its own
    authority by mutating the context it was handed.
    """

    client_id: str
    client_slug: str
    user_id: str
    roles: frozenset[Role]
    deployment_type: DeploymentType = DeploymentType.DEDICATED_DB
    #: Physical location of the workspace — database name, and schema for
    #: DEDICATED_SCHEMA deployments.
    db_name: str = ""
    db_schema: str = "public"
    #: Workspace default mode; the effective mode for a run is resolved from
    #: this plus any (narrowing-only) per-request override.
    default_execution_mode: ExecutionMode = ExecutionMode.WAIT_FOR_APPROVAL
    email: Optional[str] = None
    display_name: Optional[str] = None
    #: Restricted data areas this user may see (FB-037), from the user record.
    data_scopes: frozenset[str] = frozenset()
    #: Non-authoritative extras (session id, run id) useful for audit.
    attributes: dict = field(default_factory=dict, compare=False)

    # -- role helpers -------------------------------------------------------

    def has_role(self, role: Role) -> bool:
        """Whether the caller holds ``role`` exactly."""
        return role in self.roles

    def at_least(self, role: Role) -> bool:
        """Whether any held role ranks at or above ``role``.

        ADMIN implies every capability; the other roles are compared by rank.
        """
        if Role.ADMIN in self.roles:
            return True
        target = role_rank(role)
        return any(role_rank(held) >= target for held in self.roles)

    @property
    def primary_role(self) -> Role:
        """The highest-ranked role held, for display and for audit records."""
        if not self.roles:
            return Role.VIEWER
        return max(self.roles, key=role_rank)

    @property
    def is_admin(self) -> bool:
        return Role.ADMIN in self.roles

    @property
    def visible_areas(self) -> frozenset[str]:
        """Restricted areas whose content may be returned to this caller."""
        from bizos.types import DATA_AREAS

        if self.is_admin or self.is_system:
            return frozenset(DATA_AREAS)
        return frozenset(a for a in self.data_scopes if a in DATA_AREAS)

    def can_see_area(self, area: Optional[str]) -> bool:
        return not area or area in self.visible_areas

    @property
    def is_system(self) -> bool:
        """Whether this is an automated (scheduler/system) principal."""
        return self.user_id in {SYSTEM_USER_ID, SCHEDULER_USER_ID}

    def audit_fields(self) -> dict:
        """The subset of the context that belongs on every audit record."""
        return {
            "client_id": self.client_id,
            "user_id": self.user_id,
            "role": str(self.primary_role),
        }


_current: ContextVar[Optional[TenantContext]] = ContextVar("bizos_tenant_context", default=None)


class NoTenantContextError(RuntimeError):
    """Raised when workspace data is reached without a resolved tenant.

    This is a bug-catcher, not a user-facing error: it means some code path
    tried to touch customer data without establishing which customer.
    """


def current_context() -> Optional[TenantContext]:
    """The tenant context for the current task, or ``None``."""
    return _current.get()


def require_context() -> TenantContext:
    """The tenant context, or raise. Use this at every workspace entry point."""
    ctx = _current.get()
    if ctx is None:
        raise NoTenantContextError(
            "No tenant context is bound to this task. Workspace data cannot be "
            "accessed without a resolved client."
        )
    return ctx


def set_context(ctx: TenantContext) -> Token:
    """Bind ``ctx`` to the current task. Prefer :func:`tenant_scope`."""
    return _current.set(ctx)


def reset_context(token: Token) -> None:
    """Unbind, restoring whatever was bound before."""
    _current.reset(token)


@contextmanager
def tenant_scope(ctx: TenantContext) -> Iterator[TenantContext]:
    """Bind ``ctx`` for the duration of the block, then restore.

    Always restores on the way out, including on exception — a failed request
    must not leave another client's context bound to a pooled worker.
    """
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
