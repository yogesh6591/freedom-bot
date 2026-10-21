# One Agno assistant for company work and outbound campaigns

`app/assistant/assistant.py` (ONE file: company tools, campaign tools, prompt, agent) builds ONE Agno `Agent` per request. It sees every tool the user's role,
the workspace mode and the policy engine allow (CRM, email, calendar, memory, approvals, admin, ...)
and, for admins, the campaign tools (`update_selection`, `show_contacts`, `show_campaigns`,
`propose_email`, `approve_move`, `send_approved`). The model decides which to use.

Everything lives in `app/assistant/assistant.py`; its header explains the five parts. Permissions/risk per tool stay in `bizos/rbac/registry.py`; the policy engine in `bizos/tools/base.py`.

Approach (same as the standalone campaign bot, now on Agno):

* The model decides; code enforces. RBAC, the policy engine, approvals and audit run inside every tool.
* Every tool call carries a `why`. Every reply is `{message, explanation}`; the explanation is the
  developer trace (admins see it with the **Developer** switch in the chat header).
* Campaign choices are committed with `update_selection` and kept between turns
  (`chat_messages.meta.state`), so the bot never re-asks.
* One person = one active campaign. Tool results carry a tier per person (`free`, `move_*`, `refuse_*`);
  moving someone needs a yes in a LATER turn (`approve_move`). Unsubscribed/bounced/replied people are
  always refused. Similar campaign names (`snowflak` vs `snowflake`) always trigger the question.
* Self-check: platform checks (persona, restricted areas, conflicts, package scope, false action claims)
  and the campaign checks run on the draft; the model is told what fired and rewrites its own reply
  (max 2 times). Only if it still fails does the platform's fixed wording replace it.
* Regulated data (PHI, cards, children) is refused before any model call.
* Edited emails (pencil on the email card) are saved into the committed state, so what is sent is what was edited.

Settings: `MODEL_ID`, `MODEL_REASONING_EFFORT` (empty = omit; auto-fallback to `none`, then omitted),
`ASSISTANT_ENGINE=legacy` restores the old per-domain agent.

Tests: `tests/test_unified_assistant.py` runs the real Agno agent against a scripted OpenAI-compatible
server. Needs Postgres, `SECRET_ENCRYPTION_KEY`, and the `acquisition` database migrated
(`alembic -c acquisition_alembic.ini upgrade head`, `DATABASE_URL` set).
