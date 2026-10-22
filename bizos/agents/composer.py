"""
Reply Composer (v2 pipeline)
============================

The acknowledgement is built from the request's tool results, never from what the model says
happened (docs/agent/ARCHITECTURE.md §5.10). The model's text is only a lead-in or the answer to a
question.

    ✅ Update deal stage: stage set to won (D-2231)
    ⏳ Send email is waiting for approval (act_118)
    📝 Send email was saved as a draft for review (act_119)
    ❌ Draft invoice: No pending billables for 'Acme'

Only writes get a line; reads are part of the answer. If no write succeeded, the lead-in never claims
that something was done, sent, created or saved, and it only mentions approval when something is
actually waiting for approval. The claim patterns are the platform's existing ones (bizos/agents/claims.py);
a sentence ending in "?" is a question, never a claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from bizos.agents.claims import _APPROVAL_CLAIM, _CLAIM
from bizos.agents.tool_result import ToolResult
from bizos.rbac.registry import get_spec

_WROTE = (
    r"(?:updated|changed|moved|deleted|removed|booked|cancell?ed|rescheduled|approved|rejected|tagged|assigned|"
    r"invited|emailed|drafted|marked|enabled|disabled|granted|revoked|reset|suspended|reactivated|set|"
    r"created|sent|saved|added|recorded|scheduled|submitted|forwarded|replied|shared|posted|logged|noted)"
)
_THING = (
    r"(?:invoice|email|e-mail|mail|note|task|meeting|event|invite|invitation|contact|deal|message|draft|fact|sop|"
    r"decision|ticket|user|request|reply|record|tag|reminder)s?"
)
#: "It's done" phrasings the shared claim pattern does not cover: done-words, "I've updated", a sentence that
#: starts with a write verb ("Saved the note."), and a thing plus a write verb ("Invoice drafted!").
_DONE = re.compile(
    r"\b(?:done|all set|all done|taken care of|completed|finished|that's sorted|ho gaya|kar diya)\b"
    rf"|\b(?:i(?:'ve| have)?(?: just)?|(?:has|have) been|(?:was|were|is|are)(?: now| successfully)?|successfully)\s+{_WROTE}\b"
    rf"|^\W*{_WROTE}\b"
    rf"|\b{_THING}\s+(?:\w+\s+){{0,3}}{_WROTE}\b",
    re.I,
)

NOTHING_DONE = "Nothing was changed."


def is_question(sentence: str) -> bool:
    """A sentence that asks something is never a claim, whatever verbs it uses ("Should I set it?")."""
    return sentence.rstrip().rstrip("\"')»”").endswith("?")


def _drop(text: str, pattern: re.Pattern[str]) -> str:
    """Remove the sentences that match ``pattern``, keeping questions (the platform's sentence split)."""
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        sentences = re.split(r"(?<=[.!?])\s+", para)
        kept = [x for x in sentences if is_question(x) or not pattern.search(x)]
        joined = " ".join(kept).strip()
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs).strip()


@dataclass(frozen=True)
class Acknowledgement:
    text: str
    #: True when at least one write ran successfully.
    wrote: bool
    #: True when at least one write is waiting for approval.
    pending_approval: bool


def _title(tool: str) -> str:
    spec = get_spec(tool)
    return spec.title if spec else tool


def _is_write(tool: str) -> bool:
    return bool(getattr(get_spec(tool), "write", False))


def _line(result: ToolResult) -> Optional[str]:
    ref = f" ({result.ref})" if result.ref else ""
    title = _title(result.tool)
    if result.status == "success":
        return f"✅ {title}: {result.summary}{ref}" if result.summary else f"✅ {title}{ref}"
    if result.status == "pending" and result.pending_kind == "approval":
        return f"⏳ {title} is waiting for approval{ref}"
    if result.status == "pending":
        return f"📝 {title} was saved as a draft for review{ref}"
    return f"❌ {title}: {result.summary or 'it did not complete'}"


def compose(lead_in: str, results: Iterable[ToolResult], *, task: bool = True) -> Acknowledgement:
    """The reply: the model's lead-in (cleaned of false claims) plus one line per write result.

    ``task`` is False for a question: an answer may truthfully say a record "was created" in March, so
    only a task's lead-in is cleaned of done-claims.
    """
    writes = [r for r in results if _is_write(r.tool)]
    wrote = any(r.status == "success" for r in writes)
    pending_approval = any(r.status == "pending" and r.pending_kind == "approval" for r in writes)

    text = (lead_in or "").strip()
    if not wrote and (task or writes):
        text = _drop(_drop(text, _CLAIM), _DONE)
        if not pending_approval:
            text = _drop(text, _APPROVAL_CLAIM)
        if not text and writes:
            text = NOTHING_DONE if not any(r.status == "pending" for r in writes) else ""

    lines: list[str] = []
    for result in writes:
        line = _line(result)
        if line and line not in lines:
            lines.append(line)
    body = "\n".join(lines)
    reply = "\n\n".join(part for part in (text, body) if part)
    return Acknowledgement(reply or NOTHING_DONE, wrote, pending_approval)
