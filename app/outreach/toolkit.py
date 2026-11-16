"""Shared plumbing for the chat tools that read and change acquisition data.

A tool is a plain function ``fn(call, **arguments) -> str``. ``run`` opens the database session, runs it and
turns every outcome into the text the model reads back:

* normal text      the result (it may start with ``confirm first:`` when a yes is needed)
* ``error: ...``   the tool could not do it, and says why (``ToolError``, or a bug caught here)

``OutreachSession`` keeps only the email card (recipients, draft, send). Everything else lives in the modules
registered below, one file per area:

    prospect_tools     records: show, add, update, delete
    query_tools        read-only questions over any acquisition data
    assessment_tools   assessment status, result, rerun, reset, attendance, handoff
    booking_tools      Cal.com bookings: cancel, reschedule, book, send a link
    payment_tools      one-time payment offers and the payment link email
    journey_tools      journey settings, case studies, video links, publish
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.outreach import confirm

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A problem the user can fix. The message is shown to the model and then to the user."""


@dataclass
class Call:
    """What a tool may use during one call."""

    db: Any  # SQLAlchemy session; the tool commits its own changes
    state: dict  # the chat turn's state (pending yes/no, chosen recipients)
    latest: str  # the user's latest message
    user_id: Optional[str] = None
    client_id: Optional[str] = None

    def confirmed(self, key: str, evidence: str, question: str) -> Optional[str]:
        """``None`` once the user has said yes to this exact action, else the text to return (see ``confirm``)."""
        return confirm.gate(self.state, self.latest, key, evidence, question)


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., str]
    args: tuple[str, ...]  # the argument names the model may pass


TOOLS: dict[str, Tool] = {}


def tool(name: str, *args: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register ``fn`` as chat tool ``name`` taking the listed arguments."""

    def register(fn: Callable[..., str]) -> Callable[..., str]:
        if name in TOOLS:
            raise ValueError(f"tool {name} is registered twice")
        TOOLS[name] = Tool(fn, tuple(args))
        return fn

    return register


def run(name: str, *, db_factory: Callable[[], Any], state: dict, latest: str, user_id: Optional[str],
        client_id: Optional[str], args: dict[str, Any]) -> str:
    """Run one registered tool and return the text for the model."""
    from acquisition.tenant import tenant_scope

    entry = TOOLS[name]
    kwargs = {k: args[k] for k in entry.args if k in args and args[k] is not None}

    def _execute() -> str:
        db = db_factory()
        try:
            return entry.fn(
                Call(db=db, state=state, latest=latest or "", user_id=user_id, client_id=client_id),
                **kwargs,
            )
        except ToolError as exc:
            db.rollback()
            return f"error: {exc}"
        except Exception as exc:  # noqa: BLE001 - a bug in one tool must not break the chat turn
            db.rollback()
            log.exception("chat tool %s failed", name)
            return f"error: {name} failed ({type(exc).__name__}). Nothing was changed."
        finally:
            db.close()

    if (client_id or "").strip():
        with tenant_scope(client_id):
            return _execute()
    return _execute()


#: the tool modules, one per area (see the module docstring)
_TOOL_MODULES = (
    "assessment_tools",
    "booking_tools",
    "finder_tools",
    "journey_tools",
    "payment_tools",
    "prospect_tools",
    "query_tools",
    "research_tools",
)


def load_all() -> None:
    """Import every tool module so ``TOOLS`` is complete."""
    for name in _TOOL_MODULES:
        importlib.import_module(f"app.outreach.{name}")
