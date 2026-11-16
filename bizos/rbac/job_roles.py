"""
Job Roles
=========

The business titles an admin picks for a person: HR, Finance, Operations, Sales, Admin, Employee.

A job role is a label over the permission levels in :mod:`bizos.rbac.matrix`. It says which level a
person holds and which data tags come with it. It adds no check of its own: the policy engine still
reads only the level and the tags, so no tool becomes less strict because of a job role.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Optional

from bizos.types import Role


@dataclass(frozen=True)
class JobRole:
    key: str
    label: str
    level: Role
    tags: tuple[str, ...] = ()


JOB_ROLES: Mapping[str, JobRole] = MappingProxyType(
    {
        job.key: job
        for job in (
            JobRole("HR", "HR", Role.OPERATOR, ("hr", "salary")),
            JobRole("FINANCE", "Finance", Role.OPERATOR, ("finance",)),
            JobRole("OPERATIONS", "Operations", Role.OPERATOR, ("ops",)),
            JobRole("SALES", "Sales", Role.OPERATOR, ("sales",)),
            # Departments with no restricted data area of their own: same level, no extra tag.
            JobRole("INTAKE", "Intake", Role.OPERATOR),
            JobRole("PLANNING", "Planning", Role.OPERATOR),
            JobRole("ADMIN", "Admin", Role.ADMIN),
            JobRole("EMPLOYEE", "Employee", Role.VIEWER),
        )
    }
)


def roles_for(job: JobRole, *, can_approve: bool = False) -> frozenset[Role]:
    """The permission levels a person holds in ``job``. Approving is a separate right; admins have it."""
    if can_approve and job.level is not Role.ADMIN:
        return frozenset({job.level, Role.APPROVER})
    return frozenset({job.level})


def tags_after_change(current: Iterable[str], previous: Optional[JobRole], new: JobRole) -> frozenset[str]:
    """Swap the old role's tags for the new role's, keeping any others the admin added."""
    return (frozenset(current) - frozenset(previous.tags if previous else ())) | frozenset(new.tags)


def resolve_job_role(stored: Optional[str], roles: Iterable[Role], tags: Iterable[str]) -> Optional[str]:
    """The job role to show for this person, or ``None`` when none describes their access.

    The stored title is used only while it still matches their level: the level can also change through
    the assistant's admin tools, and the admin screen must not show a title that is no longer true.
    Someone on Drafter or Approver alone has no matching title, so an admin has to choose one.
    """
    held = frozenset(roles)
    if Role.ADMIN in held:
        return "ADMIN"
    job = JOB_ROLES.get(stored or "")
    if job and job.level in held:
        return job.key
    if Role.OPERATOR in held:
        have = frozenset(tags)
        return next(
            (j.key for j in JOB_ROLES.values() if j.level is Role.OPERATOR and j.tags and set(j.tags) <= have), None
        )
    return "EMPLOYEE" if Role.VIEWER in held else None
