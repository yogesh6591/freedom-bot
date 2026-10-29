"""The yes/no step for changes that touch more than one person or cannot be undone.

How it works, in two calls of the same tool:

1. First call: nothing changes. The tool stores what it would do (``key``) in the chat state and returns the
   question the model must put to the user.
2. The user answers in their next message. The model calls the tool again with the same arguments and
   ``approval_evidence`` copied from that answer. If the key matches, the answer counts as approval and the
   change is made.

The user's yes only counts for the exact action it was asked about (the key covers the people and the new
values) and never in the same message that started the action.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from app.outreach.approval import approval_problem


def action_key(kind: str, payload: Any) -> str:
    """A short stable fingerprint of one action, e.g. ``delete:3f9a1c2b7d10``."""
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return f"{kind}:{hashlib.sha1(blob.encode('utf-8')).hexdigest()[:12]}"


def gate(state: dict, latest: str, key: str, evidence: str, question: str) -> Optional[str]:
    """``None`` when the user has said yes to this exact action; otherwise the text to give back to the model."""
    pending = state.get("pending") or {}
    if pending.get("key") != key or pending.get("asked_on") == latest:
        state["pending"] = {"key": key, "asked_on": latest}
        return (
            f"confirm first: nothing has changed yet. Ask the user exactly this and wait for their answer: "
            f"{question} Then call this tool again with the same arguments and approval_evidence copied from their yes."
        )
    problem = approval_problem(latest, evidence)
    if problem:
        return f"error: not done - {problem}. Nothing changed."
    state.pop("pending", None)
    return None
