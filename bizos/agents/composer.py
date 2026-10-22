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
actually waiting for approval. Done-claims are _DONE below: the assistant's own actions ("I've saved",
"has been added", "your call is scheduled"), never plain facts ("the renewal is scheduled for March").
Approval claims are the platform's (bizos/agents/claims.py). A question keeps only its asking part
("I've sent it, anything else?" -> "Anything else?").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from bizos.agents.claims import _APPROVAL_CLAIM
from bizos.agents.tool_result import ToolResult
from bizos.rbac.registry import get_spec

_WROTE = (
    r"(?:updated|changed|moved|deleted|removed|booked|cancell?ed|rescheduled|approved|rejected|tagged|assigned|"
    r"invited|emailed|drafted|marked|enabled|disabled|granted|revoked|reset|suspended|reactivated|set|"
    r"created|sent|saved|added|recorded|scheduled|forwarded|replied|shared|posted|logged|noted|"
    r"flagged|escalated)"
)
_THING = (
    r"(?:invoice|email|e-mail|mail|note|task|meeting|event|invite|invitation|contact|deal|message|draft|fact|sop|"
    r"decision|ticket|user|request|reply|record|tag|reminder)s?"
)
#: "It's done" phrasings the shared claim pattern does not cover. Each says the assistant (or "your" thing it
#: was asked to change) did something; a plain fact such as "the renewal is scheduled for March" is not one.
_DONE = re.compile(
    r"\b(?:done|all set|all done|taken care of|completed|finished|that's sorted|ho gaya|kar diya)\b"
    # "I've saved", "I set", "we have added", "I just sent"
    rf"|\b(?:i|we)(?:'ve|'d|\s+have|\s+had)?(?:\s+(?:just|also|now|already|successfully))?\s+{_WROTE}\b"
    # "... and have added a note"
    rf"|\band\s+(?:have|has)\s+(?:also\s+|just\s+)?{_WROTE}\b"
    # "has been added", "have been successfully sent"
    rf"|\b(?:has|have|had)\s+been\s+(?:\w+\s+)?{_WROTE}\b"
    # "your call with Raj is scheduled"
    rf"|\byour\s+\w+(?:\s+\w+){{0,5}}?\s+(?:is|are|was|were)\s+(?:now\s+|successfully\s+|all\s+)?{_WROTE}\b"
    # "is now saved", "was successfully sent", "updated successfully"
    rf"|\b(?:is|are|was|were)\s+(?:now|successfully)\s+{_WROTE}\b|\bsuccessfully\s+{_WROTE}\b"
    rf"|\b{_WROTE}\s+successfully\b"
    # a sentence that starts with a write verb ("Saved the note.")
    rf"|^\W*{_WROTE}\b"
    # a sentence that starts with a thing and a write verb, headline style ("Invoice drafted!", "Meeting with
    # Priya booked for Tuesday"); "you have a meeting scheduled" is a fact
    rf"|^\W*(?:the\s+|your\s+)?{_THING}\s+(?:(?!(?:is|are|was|were|be|been)\b)\w+\s+){{0,3}}{_WROTE}\b",
    re.I,
)

NOTHING_DONE = "Nothing was changed."

_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})
#: The question part really asks something ("should I...", "anything else?"), so its own verbs are not a claim.
_ASKS = re.compile(
    r"^\W*(?:should|shall|do|does|did|can|could|would|will|is|are|was|were|what|which|who|whom|whose|when|"
    r"where|why|how|may|might|want|anything|any|need|kya|ok|okay|right)\b",
    re.I,
)
#: A clause that asks ("which stage should I set", "would you like me to mark it done") claims nothing.
_INTERROGATIVE = re.compile(
    rf"{_ASKS.pattern}|\b(?:should|shall|can|could|would|may|will|do|did)\s+(?:i|we)\b|\b(?:do|would)\s+you\b",
    re.I,
)
#: Words just before a verb that make it a future, a condition or a negation ("once it's approved and
#: completed", "before it can be added", "is not fully completed"): not a claim that it happened.
_NOT_YET = re.compile(
    r"(?:\bonce\b|\bwhen\b|\bafter\b|\buntil\b|\bunless\b|\bif\b|\bbefore\s+(?:it|they|this|that)\s+can\b|"
    r"\bwill\b|'ll\b|\bgoing\s+to\b|\bto\s+be\b|\bcan\s+be\b|"
    r"\bnot\b|n't\b|\bnever\b|\bno\b|\bnothing\b|\byet\s+to\b)[^.;,!?]{0,30}$",
    re.I,
)
#: While a request is waiting for approval, saying the request exists is true ("I've created the request to
#: tag Raj", "escalated for approval").
_QUEUED_WORDING = re.compile(r"\b(?:request|requests|proposal|for approval|for review)\b", re.I)
#: Clause breaks inside one sentence: "I've sent it, anything else?"
_CLAUSES = re.compile(r"(?<=[,;:])\s+|\s+[-\u2013\u2014]\s+")


def is_question(sentence: str) -> bool:
    """The sentence ends with a question mark."""
    return sentence.rstrip().rstrip("\"')»”").endswith("?")


def _matches(fragment: str, patterns: tuple[re.Pattern[str], ...], *, pending: bool = False) -> bool:
    text = fragment.translate(_APOSTROPHES)
    if pending and _QUEUED_WORDING.search(text):
        return False
    for pattern in patterns:
        for m in pattern.finditer(text):
            if pattern is _DONE and _NOT_YET.search(text[max(0, m.start() - 40):m.start()]):
                continue
            return True
    return False


def _clean_sentence(sentence: str, patterns: tuple[re.Pattern[str], ...], *, pending: bool = False) -> str:
    """The sentence without its claims: "" for a claim, the question alone for a question that also claims."""
    if not is_question(sentence):
        return "" if _matches(sentence, patterns, pending=pending) else sentence
    parts = _CLAUSES.split(sentence.strip())
    question, before = parts[-1], parts[:-1]
    if not _INTERROGATIVE.search(question) and _matches(question, patterns, pending=pending):
        question = ""  # "now that I've sent it?" is still a claim
    kept = [p for p in before if _INTERROGATIVE.search(p) or not _matches(p, patterns, pending=pending)]
    if question and len(kept) == len(before):
        return sentence
    rebuilt = " ".join(kept + ([question] if question else [])).strip().rstrip(",;:")
    return rebuilt[:1].upper() + rebuilt[1:] if rebuilt else ""


def _drop(text: str, *patterns: re.Pattern[str], pending: bool = False) -> str:
    """Remove the sentences, or clauses of a question, that match any pattern (the platform's sentence split)."""
    paragraphs: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        kept = [c for c in (_clean_sentence(x, patterns, pending=pending) for x in re.split(r"(?<=[.!?])\s+", para)) if c]
        joined = " ".join(kept).strip()
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs).strip()


def claims_in(text: str, *, pending_approval: bool = False) -> list[str]:
    """The sentences or clauses of ``text`` that claim an action was done (what compose() strips as a
    done-claim when no write ran). Queue descriptions and approval wording are not counted here."""
    found: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if sentence.strip() and _clean_sentence(sentence, (_DONE,), pending=pending_approval) != sentence:
                found.append(sentence.strip())
    return found


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
        text = _drop(text, _DONE, pending=pending_approval)
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
