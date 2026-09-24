"""
Persona and Single-Chat Routing
===============================

FB-034. Two pieces that make one company chat work:

* **Persona rules.** The assistant is the company's FreedomBot — software, not a
  person. It never presents itself as Jeanne (or any named human). The prompt
  says so, and :func:`enforce_persona` checks the reply deterministically so a
  model slip cannot reach the user.
* **Routing.** One chat, no Bronze/Silver/Gold or domain menu. Each turn is
  routed to the enabled domain the message is about, while the conversation
  stays in one session, so context given earlier (a goal, a client, a number)
  carries from strategy to operations to finance without being repeated.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

#: Names the assistant must never present itself as.
PROTECTED_PERSONAS: tuple[str, ...] = ("Jeanne",)

AUTO = "auto"

#: Keyword signals per domain. Deliberately plain: routing chooses which tools
#: and instructions lead this turn, it never grants permissions — every tool
#: call is still checked by the policy engine.
_SIGNALS: dict[str, tuple[str, ...]] = {
    "strategy": (
        "strategy", "strategic", "goal", "goals", "priority", "priorities", "okr",
        "roadmap", "vision", "quarter plan", "positioning", "option", "trade-off", "tradeoff",
    ),
    "finance": (
        "finance", "invoice", "invoices", "refund", "discount", "pricing", "price",
        "payment", "payments", "budget", "cost", "costs", "revenue", "margin", "cash",
        "spend", "net-30", "terms", "adjustment",
    ),
    "operations": (
        "operations", "ops", "process", "sop", "procedure", "workflow", "task", "tasks",
        "handoff", "checklist", "hours", "schedule", "vendor", "fulfilment", "fulfillment",
    ),
    "sales": (
        "sales", "deal", "deals", "pipeline", "prospect", "lead", "leads", "follow-up",
        "follow up", "proposal", "quote", "crm", "contact", "customer",
    ),
    "intake": ("intake", "inbound", "new lead", "qualify", "qualification", "enquiry", "inquiry"),
    "planning": ("plan", "planning", "meeting", "calendar", "agenda", "timeline", "milestone", "week"),
    "brand": ("brand", "voice", "tone", "messaging", "copy", "tagline", "style guide"),
    "legal": ("legal", "contract", "clause", "msa", "nda", "terms of service", "liability", "counsel"),
}


def route(
    message: str,
    enabled: Iterable[str],
    *,
    previous: Optional[str] = None,
) -> str:
    """Pick the enabled domain this message is about.

    Falls back to the previous turn's domain (so "ok, send it" stays where the
    draft was made), then to ``general``.
    """
    allowed = [d for d in enabled if d != "general"]
    text = f" {(message or '').casefold()} "
    best, best_score = None, 0
    for domain in allowed:
        score = 0
        for signal in _SIGNALS.get(domain, ()):
            if re.search(rf"(?<![a-z]){re.escape(signal)}(?![a-z])", text):
                score += 2 if " " in signal else 1
        if score > best_score:
            best, best_score = domain, score
    if best is not None:
        return best
    if previous and (previous in allowed or previous == "general"):
        return previous
    return "general"


_NAMES = "|".join(re.escape(n) for n in PROTECTED_PERSONAS)
_SELF_CLAIM = re.compile(
    rf"\b(?P<lead>(?:I\s*am|I'm|I’m|this\s+is|my\s+name\s+is|it'?s)\s+)(?:{_NAMES})\b",
    re.IGNORECASE,
)
_SIGN_OFF = re.compile(
    rf"^(?P<lead>\s*(?:[-–—]\s*|best,?\s*|regards,?\s*|thanks,?\s*|cheers,?\s*)?)(?:{_NAMES})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def enforce_persona(reply: str, assistant_name: str = "FreedomBot") -> tuple[str, bool]:
    """Remove any self-presentation as a protected person. Returns (text, changed)."""
    if not reply:
        return reply, False
    out = _SELF_CLAIM.sub(lambda m: f"{m.group('lead')}{assistant_name}", reply)
    out = _SIGN_OFF.sub(lambda m: f"{m.group('lead')}{assistant_name}", out)
    return out, out != reply
