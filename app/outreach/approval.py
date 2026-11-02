"""The send gate: the user's latest message must really approve.

The model decides what counts as approval, but it may not send on a message that plainly is not one.
Moved unchanged from the old campaign assistant.
"""

from __future__ import annotations

import re


def approval_problem(latest: str, evidence: str) -> str:
    """Empty string when the latest user message can count as approval to send, else why it cannot."""
    norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
    if not norm(latest):
        return "there is no user message to take approval from"
    if not norm(evidence) or norm(evidence) not in norm(latest):
        return "approval_evidence must be the user's own words from their LATEST message"
    try:
        import json

        from pydantic import BaseModel

        from agno.models.message import Message
        from app.assistant.assistant import _model

        class _Decision(BaseModel):
            decision: str

        output = _model("low").response(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Decide whether the user's latest message clearly agrees to go ahead with "
                        "the action just proposed to them. approve = clear agreement. decline = "
                        "refusal, holding back, or asking for a change. unclear = a question, a "
                        "topic change, or anything not clearly agreement."
                    ),
                ),
                Message(role="user", content=json.dumps({"latest": latest, "evidence": evidence})),
            ],
            response_format=_Decision,
        )
        parsed = getattr(output, "parsed", None)
        if isinstance(parsed, _Decision):
            decision = parsed.decision
        elif isinstance(parsed, dict):
            decision = parsed.get("decision")
        else:
            content = getattr(output, "content", None)
            data = json.loads(content) if isinstance(content, str) else content
            decision = (data or {}).get("decision") if isinstance(data, dict) else None
        decision = str(decision or "").strip().casefold()
    except Exception:
        decision = "unclear"
    if decision == "approve":
        return ""
    if decision == "decline":
        return "the user's latest message holds back or declines; do not send"
    return "the user's latest message does not clearly approve"


#: The user asked to change the whole email (subject AND body), not just one part.
WHOLE_EMAIL = re.compile(
    r"\b(?:whole|entire|full|complete)\s+(?:email|mail|message|draft)\b"
    r"|\bsubject\s*(?:,|and|&|\+|as well|too|also)\s*(?:the\s+)?body\b"
    r"|\bbody\s*(?:,|and|&|\+|as well|too|also)\s*(?:the\s+)?subject\b"
    r"|\b(?:including|plus|with|and)\s+(?:the\s+)?subject\b"
    r"|\bsubject\s+(?:too|also|as well)\b",
    re.I,
)
CHANGE_VERB = re.compile(r"\b(?:change|make|rewrite|redo|replace|update|turn|convert|switch|edit|write)\b", re.I)


def user_said(latest: str, evidence: str) -> bool:
    """The evidence is the user's own words from their latest message."""
    norm = lambda t: " ".join(str(t or "").casefold().split())  # noqa: E731
    return bool(norm(evidence)) and norm(evidence) in norm(latest)


def asks_to_change_subject(latest: str) -> bool:
    """True when the model says the latest message asks to change the email subject.

    On any failure the answer is no: the current subject must stay.
    """
    try:
        import json

        from pydantic import BaseModel

        from agno.models.message import Message
        from app.assistant.assistant import _model

        class _Decision(BaseModel):
            decision: str

        output = _model("low").response(
            messages=[
                Message(
                    role="system",
                    content="Does the user's latest message ask to change the email's subject line?",
                ),
                Message(role="user", content=json.dumps({"latest": latest})),
            ],
            response_format=_Decision,
        )
        parsed = getattr(output, "parsed", None)
        if isinstance(parsed, _Decision):
            decision = parsed.decision
        elif isinstance(parsed, dict):
            decision = parsed.get("decision")
        else:
            content = getattr(output, "content", None)
            data = json.loads(content) if isinstance(content, str) else content
            decision = (data or {}).get("decision") if isinstance(data, dict) else None
        return str(decision or "").strip().casefold() == "yes"
    except Exception:
        return False
