# One Agno assistant for company work and acquisition

There is one chatbot. `app/assistant/v2.py` runs every turn: regulated-data refusal and grounding, the router
(which tool areas the message is about), tool loading, ONE executor Agno `Agent` whose every tool call goes through
the guard, and a reply composed from tool results. There is no separate "Acquisition mode" and no switch in the UI.

For an admin every turn carries an `OutreachSession` (`app/outreach/session.py`): the chosen recipients and the
email draft, saved with the turn. The `outreach` tool area covers acquisition: prospects (count, search, show, add,
edit, delete), emails and follow-ups, the sent-email log, assessments, bookings (read, move, cancel), one-time payment
offers, and the prospect journey page. The router sends a message there when it is about prospects; a pending draft
keeps short replies ("send", "yes") in that area.

Rules that do not change:

* The model decides, code enforces. RBAC (`freedombot/rbac/registry.py`), the policy engine, approvals and audit run
  inside every tool. Outreach tools are admin-only.
* Every tool call carries a `why`; every reply is `{message, explanation}`. Admins see both with the Developer switch.
* A change that touches more than one person, or cannot be undone, asks for one yes (`app/outreach/confirm.py`): the
  first call changes nothing, the second call carries the user's yes in a later message. "Delete everyone" is refused.
* People book their own call after they qualify; the chat cannot book for them or email a booking link. A payment
  offer needs a qualified person with a confirmed booking. Payments are one-time (Stripe Checkout).
* Times are Chicago time (`app/outreach/chicago.py`) and replies say so.
* Regulated data (PHI, cards, children) is refused before any model call.

Settings: `MODEL_ID`, `MODEL_REASONING_EFFORT`, `EXECUTOR_MODEL_ID`. `ASSISTANT_ENGINE=legacy` restores the old
per-domain agent.
