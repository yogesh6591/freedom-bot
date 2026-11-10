"""
Reply Composer (v2 pipeline)
============================

The user-facing reply is the model's own words. Tool results stay in the tool log for the model; they
are never pasted into the reply (no check marks, tool titles, or instructions meant for the model).

If no write succeeded, the lead-in never claims that something was done, sent, created or saved, and
it only mentions approval when something is actually waiting for approval. Done-claims are _DONE
below: the assistant's own actions ("I've saved", "has been added", "your call is scheduled"), never
plain facts ("the renewal is scheduled for March"). Approval claims are the platform's
(bizos/agents/claims.py). A question keeps only its asking part ("I've sent it, anything else?" ->
"Anything else?").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

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


def _is_write(tool: str) -> bool:
    return bool(getattr(get_spec(tool), "write", False))


def compose(lead_in: str, results: Iterable[ToolResult], *, task: bool = True) -> Acknowledgement:
    """The reply: the model's own words, cleaned of false claims when a write did not succeed.

    Write results are not appended. ``task`` is False for a question: an answer may truthfully say a
    record "was created" in March, so only a task's lead-in is cleaned of done-claims.
    A successful show_assessment result is the labelled report the user asked to see; keep it.
    """
    results = list(results)
    writes = [r for r in results if _is_write(r.tool)]
    wrote = any(r.status == "success" for r in writes)
    pending_approval = any(r.status == "pending" and r.pending_kind == "approval" for r in writes)
    reports = []
    for r in results:
        if r.tool != "show_assessment" or r.status != "success":
            continue
        body = r.data if isinstance(r.data, str) else r.summary
        if (body or "").strip():
            reports.append(body)
    if reports and not wrote:
        return Acknowledgement("\n\n".join(reports), False, pending_approval)

    text = (lead_in or "").strip()
    if not wrote and (task or writes):
        text = _drop(text, _DONE, pending=pending_approval)
        if not pending_approval:
            text = _drop(text, _APPROVAL_CLAIM)
        if not text and writes:
            text = NOTHING_DONE if not any(r.status == "pending" for r in writes) else ""

    return Acknowledgement(text or NOTHING_DONE, wrote, pending_approval)


#: Words that may start a claim (done or approval). A sentence holding one is held back while it streams.
_RISKY = re.compile(
    rf"\b{_WROTE}\b|\b(?:done|all set|taken care|completed|finished|sorted|ho gaya|kar diya|approv\w*|queued|"
    r"pending|submitted)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n\s*\n")


class StreamGate:
    """The model's text as it streams, word by word, with the same claim rule as :func:`compose`.

    Text goes out as it arrives until the current sentence holds a word that could start a claim ("sent",
    "deleted", "approval"...). That sentence is then held until it ends and goes out only if compose() would
    keep it, judged on the tool results at that moment. ``state`` returns (wrote, pending_approval, any_write)
    for the request so far. The final reply is still compose()'s: the UI replaces the streamed text with it.
    """

    def __init__(self, state: Callable[[], tuple[bool, bool, bool]], *, task: bool = True) -> None:
        self._state = state
        self._task = task
        self._sentence = ""  # the current, unfinished sentence
        self._shown = 0  # characters of it already sent

    def _filtering(self) -> tuple[bool, bool]:
        wrote, pending, any_write = self._state()
        return (not wrote and (self._task or any_write)), pending

    def _finish(self, sentence: str, gap: str) -> str:
        """The rest of one complete sentence (plus the gap after it), or "" when it must not be shown."""
        filtering, pending = self._filtering()
        rest = sentence[self._shown:]
        self._sentence, self._shown = "", 0
        if not filtering or not _RISKY.search(sentence):
            return rest + gap
        patterns = (_DONE,) if pending else (_DONE, _APPROVAL_CLAIM)
        return rest + gap if _clean_sentence(sentence, patterns, pending=pending) == sentence else ""

    def feed(self, delta: str) -> str:
        """Text that is safe to show now."""
        self._sentence += str(delta or "")
        out = []
        while True:
            end = _SENTENCE_END.search(self._sentence)
            if not end:
                break
            sentence, gap = self._sentence[:end.start()], self._sentence[end.start():end.end()]
            remainder = self._sentence[end.end():]
            out.append(self._finish(sentence, gap))
            self._sentence = remainder
        filtering, _ = self._filtering()
        if not (filtering and _RISKY.search(self._sentence)):
            # Whole words only: the word still arriving might be the one that starts a claim.
            cut = max(self._sentence.rfind(" "), self._sentence.rfind("\n")) + 1
            if cut > self._shown:
                out.append(self._sentence[self._shown:cut])
                self._shown = cut
        return "".join(out)

    def flush(self) -> str:
        """Whatever is left once the model stops."""
        return self._finish(self._sentence, "") if self._sentence else ""
