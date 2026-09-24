# FB-033 – FB-038: how the platform now works

This is the updated approach and process for the existing FreedomBot flow. Each
section says what the rule is, where it is enforced, and what a person does.

## FB-033 — One isolated FreedomBot per client

**Approach.** One template bot, cloned per client. Each client gets its own
database (or schema), its own deployment, and a `client_id` wall inside its data.

| Piece | Where |
| --- | --- |
| Template bot (persona, default mode, base package, starter SOPs, retention) | `bizos/templates/freedombot.json`, `bizos/template.py` |
| Provisioning script (clone → customize → DB → seed → admin → deployment files) | `scripts/provision_client.py` |
| Separate database per client | `bizos/tenancy/provisioning.py` (`DEDICATED_DB` default) |
| `client_id` on every record, pinned by CHECK constraint | `client_wall_ddl()` in `bizos/tenancy/schema.py`; verified by `unwalled_tables()` |
| Separate deployment, refuses other clients | `CLIENT_SLUG` env → `bizos/settings.py::pinned_client_slug`, enforced in login and `app/api/deps.py` |

Agno only separates data per *user*. The client walls are ours: separate
database + pinned deployment + the `client_id` CHECK inside every table.

**Process for a new client.**

```bash
.venv/bin/python -m scripts.provision_client --name "Globex Ltd" --slug globex \
    --admin-email owner@globex.com --admin-password '<strong password>' \
    --config clients/globex.json          # optional overrides on the template
cd deploy/clients/globex && docker compose up -d --build
```

`deploy/clients/` is git-ignored: `client.env` contains generated secrets.

## FB-034 — One company chat, persona rules

- **One chat.** No Bronze/Silver/Gold or domain menu. `POST /api/chat` defaults
  to `domain: "auto"`: each turn is routed (`bizos/agents/persona.py::route`) to
  the enabled domain it is about, in the same session, so context carries from
  strategy to ops to finance. The UI shows "Handled as: …" under each reply.
- **Persona.** The system prompt says the assistant is FreedomBot and never
  Jeanne (`bizos/agents/instructions.py`). `enforce_persona()` also rewrites any
  reply that claims to be or signs as Jeanne, and logs `PERSONA_ENFORCED`.
- **Decision: Agno, not LangGraph.** The spec row asks for a LangGraph
  react-agent. We are deliberately staying on Agno 3.x: tool calls go through
  our own policy engine (`guarded_call`), sessions and history live in the
  client's own database via Agno's `PostgresDb`, and a second agent framework
  would duplicate both. The spec row should read: *"Single-chat agent on Agno
  (react-style tool loop) with server-side domain routing; LangGraph not used."*

## FB-035 — Only paid domains

- `purchased_domains` in client settings is the paid package. The template's
  base package is general, operations, sales, intake, planning.
- Strategy, finance, brand and legal are built (instructions, tools, knowledge
  per domain in `bizos/domains/packs.py`) but are add-ons.
- Enabling an unpaid domain returns **409**. The admin records a change order
  (`POST /api/domains/{domain}/change-order` or "Record change order" in
  Admin → Domains); it is stored on the client and audited as `CHANGE_ORDER`.

## FB-036 — Fact vs estimate, conflicts go to a person

- Every memory version has an approval flag (`APPROVED` / `PENDING`).
- Retrieval ranks approved above unapproved, then by source trust.
- Every result carries `label`: **fact** (approved, not AI-inferred) or
  **estimate**. The agent must prefix values with "Fact:" / "Estimate:".
- Policies carry a `topic`. A new value that disagrees with an approved one on
  the same topic is stored as `PENDING` with `conflict_with`, audited as
  `MEMORY_CONFLICT`, and shown under "Conflicting policies" in Memory.
- The agent never picks: search reports `CONFLICT` and the agent calls
  `memory_escalate_conflict` (always needs approval). An **approver** resolves
  it by approving the value that stands; the other is superseded, kept in history.

## FB-037 — Restricted data and "see" vs "run"

- Data areas: `exec`, `hr`, `salary`, `finance`, `legal`
  (`bizos/types.py::DATA_AREAS`). Users hold `data_scopes`; admins see all.
- Memory items carry `access_area`. Filtering happens **in the SQL query**
  (`_area_filter` in `bizos/memory/store.py`), so restricted rows never come
  back from search, list, get or history — not even as a count.
- Tools can declare `data_area` (finance adjustment, legal tools); the policy
  rule `DATA_AREA_RESTRICTED` denies them to users without that scope.
- Every tool reports `view` (can see the process exists) and `run` (may invoke
  it) per role. Chat context lists visible tools with `can_run`.
- Admin → Users toggles each user's restricted areas (`PATCH /api/users/{id}/scopes`).

## FB-038 — Modes per tool, approval queue, audit log

- **Per-tool modes.** `tool_modes` in settings assigns one of the four modes to
  each tool; it can only narrow the workspace mode. The template assigns
  AUTO to reads and drafts and WAIT_FOR_APPROVAL to every other write (email
  sends, CRM changes, spend). Editable in Admin → Tools.
- **Approval queue.** Unchanged flow: PENDING_APPROVAL → APPROVED/REJECTED →
  EXECUTED, in the Approvals page, with approval events append-only.
- **Audit log.** Every row records who (`user_id`, `actor_role`), what
  (`event_type`, `decision`, `status`), which tool, and which `execution_mode`.
  It is the business audit log in the client's own database, separate from
  Agno run tracing.
- **Retention.** `retention_policy.audit_days` (default 365, floor 30, 0 = keep).
  The append-only trigger allows a DELETE only for rows older than the cutoff
  set inside the purge transaction; updates are never allowed. Run from
  Audit → "Apply retention", `POST /api/audit/purge`, or at bootstrap. Each
  purge is itself audited as `AUDIT_PURGED`.

## Tests

`tests/test_fb033_038.py` covers each item above against real Postgres.
