"""
Ticket Routing
==============

Which team a support ticket goes to. Plain Python, no model call, so it is fast, testable and the
same every time.

* **Technical** (Cepoch, the first support layer): wrong AI answers, login and access, integrations
  and APIs, bugs, errors, technical setup.
* **Business** (Jeanne): billing and subscription needing business approval, business onboarding,
  operational requests, client management.

A flagged AI response is always technical. When the assistant raised the ticket it may pass its own
reading of the issue (``hint``); a clear signal in the text still wins over it. When the words do not
decide, a model reads the ticket and says technical or business (:func:`model_category`). Only when that
is unclear too, or no model is available, does the ticket go to Cepoch's triage queue, so Jeanne never
gets a technical problem by mistake.

The routing reason is kept for support staff only. Nothing here is ever shown to the person who
raised the ticket (:func:`freedombot.support.requester_view`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

TECHNICAL = "TECHNICAL"
BUSINESS = "BUSINESS"
CATEGORIES = (TECHNICAL, BUSINESS)

SOURCE_AI_FLAG = "AI_FLAG"
SOURCE_MANUAL = "MANUAL"
SOURCE_CHAT = "CHAT"
SOURCES = (SOURCE_AI_FLAG, SOURCE_MANUAL, SOURCE_CHAT)

_TECHNICAL_TERMS = (
    "error", "bug", "broken", "crash", "not working", "doesn't work", "does not work", "won't load",
    "not loading", "fails", "failed", "failure", "exception", "timeout", "timed out", "500", "404",
    "login", "log in", "sign in", "signin", "password", "locked out", "2fa", "authentication", "access denied",
    "integration", "api", "gmail", "hubspot", "calendar sync", "sync", "connect", "webhook", "composio",
    "wrong answer", "incorrect", "hallucinat", "made up", "ai response", "the bot said", "assistant said",
    "slow", "freez", "blank page", "upload", "import", "export", "setup", "set up", "configure",
)
_BUSINESS_TERMS = (
    "pay", "paying", "paid", "pay in advance", "advance payment", "prepay", "prepayment", "bill", "bills",
    "deposit", "receipt", "credit note", "purchase order", "seats", "licence", "license", "trial",
    "billing", "invoice", "subscription", "plan", "pricing", "price", "upgrade", "downgrade", "renewal",
    "renew", "contract", "refund", "payment", "discount", "quote", "change order", "purchase", "buy",
    "business approval", "onboarding call", "kickoff", "training session", "account manager",
    "client management", "relationship", "meeting with", "operational request", "process change",
    "add a new department", "commercial", "proposal", "cancel our", "cancel the subscription",
)


@dataclass(frozen=True)
class Routing:
    category: str
    #: True when the text did not say clearly enough: the ticket waits in Cepoch's triage queue.
    triage: bool
    #: Why, for support staff only.
    reason: str

    @property
    def team(self) -> str:
        from freedombot.support import ASSIGNEE_CEPOCH, ASSIGNEE_JEANNE

        return ASSIGNEE_JEANNE if self.category == BUSINESS and not self.triage else ASSIGNEE_CEPOCH


def _hits(text: str, terms: tuple[str, ...]) -> list[str]:
    found = []
    for term in terms:
        pattern = r"\b" + re.escape(term) if term[0].isalnum() else re.escape(term)
        if re.search(pattern, text):
            found.append(term)
    return found


def parse_category(value: Optional[str]) -> Optional[str]:
    name = str(value or "").strip().upper()
    aliases = {"TECH": TECHNICAL, "OPERATIONAL": BUSINESS, "OPERATIONS": BUSINESS, "BUSINESS_ISSUE": BUSINESS}
    name = aliases.get(name, name)
    return name if name in CATEGORIES else None


_MODEL_PROMPT = (
    "You sort support tickets for FreedomBot, a business AI assistant product. Answer with one word.\n"
    "technical: something in the product is broken or wrong: errors, bugs, a wrong or bad AI answer, login, "
    "passwords, access, integrations (Gmail, HubSpot, calendar), syncing, uploads, slowness, technical setup.\n"
    "business: anything about money or the relationship: paying, billing, invoices, subscription, plan, "
    "pricing, contracts, renewals, refunds, business onboarding, meetings, training, operational or account "
    "requests, client management.\n"
    "unclear: only when the ticket gives no way to tell.\n"
    "Reply with exactly one of: technical, business, unclear."
)


def _ask_model(text: str) -> Optional[str]:
    """One short model call. ``None`` when no model is configured or the call fails."""
    from freedombot import settings as app_settings

    if not app_settings.llm_available():
        return None
    try:
        from agno.agent import Agent

        from freedombot.agents.router_agent import router_model

        agent = Agent(name="freedombot-ticket-router", model=router_model(), instructions=_MODEL_PROMPT,
                      add_history_to_context=False, enable_agentic_memory=False, telemetry=False,
                      store_events=False)
        reply = str(getattr(agent.run(input=text[:4000]), "content", "") or "").strip().casefold()
    except Exception:  # noqa: BLE001 - routing falls back to triage, it never fails the ticket
        return None
    word = reply.split()[0].strip(".:") if reply else ""
    return {"technical": TECHNICAL, "business": BUSINESS}.get(word)


#: Replaceable for tests, which never call a real model.
model_category = _ask_model


def classify(
    subject: str, body: str = "", *, source: str = SOURCE_MANUAL, hint: Optional[str] = None, use_model: bool = True
) -> Routing:
    """The team for a ticket, and whether it needs triage."""
    if source == SOURCE_AI_FLAG:
        return Routing(TECHNICAL, False, "a flagged AI response is a technical issue")
    text = f"{subject}\n{body}".casefold()
    technical, business = _hits(text, _TECHNICAL_TERMS), _hits(text, _BUSINESS_TERMS)
    suggested = parse_category(hint)
    if len(technical) > len(business):
        return Routing(TECHNICAL, False, f"technical terms: {', '.join(technical[:4])}")
    if len(business) > len(technical):
        return Routing(BUSINESS, False, f"business terms: {', '.join(business[:4])}")
    if suggested and not (technical or business):
        return Routing(suggested, False, f"assistant classified it as {suggested.lower()}")
    if suggested and technical and business:
        return Routing(suggested, False, f"mixed terms; assistant classified it as {suggested.lower()}")
    if use_model:
        read = model_category(f"{subject}\n{body}".strip())
        if read in CATEGORIES:
            return Routing(read, False, f"model classified it as {read.lower()}")
    return Routing(TECHNICAL, True, "not clear enough to route; sent to Cepoch triage")
