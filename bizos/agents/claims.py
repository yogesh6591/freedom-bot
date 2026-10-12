"""
Action-Claim Check (M06-05, M04-03)
===================================

The model sometimes *says* it submitted, queued, sent or recorded something when
no tool ran — "Your email has been submitted for approval", "Decision recorded".
Nothing happened, so the user is misled into waiting for an approval that does
not exist.

What actually happened in a turn is known exactly: the tool calls and the
actions they created. So the platform checks the reply against it. When the
reply claims a write that did not occur, the claiming sentences are removed and
the user is told plainly that nothing was submitted or saved.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

#: First-person or passive claims that something was done or put in the queue.
_CLAIM = re.compile(
    r"\b(?:"
    r"i(?:'ve| have| had)? (?:just )?(?:submitted|queued|placed|sent|recorded|saved|created|scheduled|added|logged|put)"
    r"|i(?:'ll| will| am going to|'m going to) (?:now )?(?:submit|queue|place|send|record|save|create|schedule|add|log|put)"
    r"|(?:has|have) been (?:successfully )?(?:submitted|queued|placed|sent|recorded|saved|created|scheduled|added|logged)"
    r"|(?:is|are) now (?:awaiting|pending|in the approval|queued|in the queue)"
    r"|will be (?:queued|placed|submitted|sent) for (?:approval|review)"
    r"|(?:placed|put|added) (?:it |this |the \w+ )?(?:in|into) the approval queue"
    r"|(?:decision|fact|sop|policy|note|task|email|invoice|event|meeting|request) (?:recorded|saved|created|sent|submitted|queued|scheduled)"
    r"|(?:submitted|queued|sent) (?:it |this |the \w+ )?for approval"
    r")\b",
    re.I,
)

#: The user asked for something to be done (not just asked a question).
_ACTION_REQUEST = re.compile(
    r"\b(send|email|submit|queue|record|save|remember|create|add|tag|update|schedule|book|"
    r"draft|log|note|propose|flag|trigger|cancel|reschedule|delete|approve|invoice)\b",
    re.I,
)

#: Claims that something is in the approval queue. Only a PENDING_APPROVAL
#: action created this turn makes these true — a saved draft does not.
_APPROVAL_CLAIM = re.compile(
    r"\b(?:awaiting (?:your |an? )?approval|pending approval|waiting (?:for|on) approval|"
    r"submitted for approval|queued for approval|in the approval queue|"
    r"(?:needs|requires|is subject to) approval before (?:it is |it's |being )?sen[dt])\b",
    re.I,
)

DRAFT_ONLY_CORRECTION = (
    "**Note:** only a draft was saved — it has not been submitted and nothing is waiting "
    "for approval. Say \"send it\" to submit it to the approval queue."
)

CORRECTION = (
    "**Note:** nothing was actually submitted, sent or saved in this turn — no action was "
    "created and nothing is waiting for approval. Ask again with the details (for example, "
    "who to email, the subject and the message), and check the Approvals page to confirm."
)


def _wrote_something(activity: Iterable[dict[str, Any]], new_actions: list[Any]) -> bool:
    if new_actions:
        return True
    from bizos.rbac.registry import get_spec

    for call in activity:
        if not call.get("ok"):
            continue
        spec = get_spec(call.get("tool") or "")
        if spec is not None and spec.write:
            return True
    return False


def _status(action: Any) -> str:
    if isinstance(action, dict):
        return str(action.get("status") or "")
    return str(getattr(action, "status", "") or "")


def _drop(reply: str, pattern: re.Pattern[str]) -> str:
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", reply):
        sentences = re.split(r"(?<=[.!?])\s+", para)
        kept = [x for x in sentences if not pattern.search(x)]
        text = " ".join(kept).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs).strip()


def enforce_claims(
    reply: str,
    *,
    message: str,
    activity: list[dict[str, Any]],
    new_actions: list[Any],
) -> tuple[str, bool]:
    """Remove claims of writes that did not happen this turn. Returns (text, changed)."""
    if not reply or not _ACTION_REQUEST.search(message or ""):
        return reply, False

    if not _wrote_something(activity, new_actions):
        if not (_CLAIM.search(reply) or _APPROVAL_CLAIM.search(reply)):
            return reply, False
        cleaned = _drop(_drop(reply, _CLAIM), _APPROVAL_CLAIM)
        return ((cleaned + "\n\n" + CORRECTION) if cleaned else CORRECTION), True

    # Something was written. An approval claim still needs an action that is
    # actually pending approval — a saved draft is not in any queue.
    pending = any(_status(a) == "PENDING_APPROVAL" for a in new_actions)
    if pending or not _APPROVAL_CLAIM.search(reply):
        return reply, False
    cleaned = _drop(reply, _APPROVAL_CLAIM)
    drafted = any(c.get("ok") and "draft" in (c.get("tool") or "") for c in activity)
    note = DRAFT_ONLY_CORRECTION if drafted else CORRECTION
    return ((cleaned + "\n\n" + note) if cleaned else note), True
