# Phase 0 discovery report: BizOS assistant tool selection

Branch: `club-freedom` at `fe5b089`. This phase was read-only: no code was changed.
The §8 decisions were answered and are recorded in docs/agent/plan.md. This file is kept as the
Phase 0 record and is not updated.
The code is the source of truth. Wherever it disagrees with `docs/agent/ARCHITECTURE.md` or
`TOOL_DESCRIPTIONS.md`, this report follows the code and marks the gap **[SPEC≠CODE]**.

## 0. Inputs that were missing or in a different place

| Expected | Found |
|---|---|
| `docs/agent/plan.md` | **Missing at the time.** It was not in the repo or the git history. (Since added.) |
| `bizos/agent/prompts/` | `bizos/agents/prompts/` (plural `agents`). ARCHITECTURE.md §5.2 and §5.5 use the wrong path. |
| `CLAUDE.md` | `claude.md` (lowercase, untracked). It works on macOS because the filesystem ignores case, but on Linux or CI anything that looks for `CLAUDE.md` will not find it. |
| `docs/agent/drafts/` | Missing. TOOL_DESCRIPTIONS.md mentions it. |
| Eval runner | None exists. `evals/cases.yaml` has 9 cases and nothing runs them. |

`docs/agent/`, `evals/`, `bizos/agents/prompts/` and `claude.md` are all untracked.

## 1. How a chat turn runs today

`POST /chat` (`app/api/chat.py:164`) picks an engine from `ASSISTANT_ENGINE`:
- `unified` (the default) → `app/assistant/assistant.py:reply()` (line 4266)
- `legacy` → `_legacy_chat` (per-domain agent, `bizos/agents/agent.py`)

The unified turn:
1. Load the chat session and pending campaign state from `chat_messages.meta.state`.
2. **Pre-model refusals.** Regulated data (PHI, cards, children) and restricted HR candidates get a
   fixed reply. No model call is made.
3. **Platform grounding** (`bizos/agents/grounding.py`). This can run `hr_match_candidate`
   deterministically before the model.
4. **Tool surface.** `build_unified_tools(scope, packs)` (`bizos/agents/surface.py`) exposes every
   tool from every enabled domain pack that the policy engine would not DENY outright. For admins it
   adds the 6 campaign tools. About 60–80 tools are visible on each turn. There is no router.
5. **One Agno `Agent`** per attempt: `output_schema=AssistantTurn {message, explanation}`,
   `tool_call_limit=16`, `cache_callables=False`, and a `why` argument injected into every tool.
6. **Self-check loop** (up to 2 nudges). `_platform_checks` (persona, restricted areas, conflicts,
   package scope, false action claims) and `CampaignSession.problems` run on the draft reply. The
   model is told what fired and rewrites the reply. If it still fails, fixed wording replaces it.
7. `_finish` returns `content`, `tool_activity`, `actions`, `awaiting_approval`, `drafts`, and a
   `trace` that only admins can see.

The reply text is written by the model. The `awaiting_approval` and `drafts` lists are code-built
from the action store. Two safety nets catch false claims: `claims.py`, plus `claims_unsent` for
campaign sends.

## 2. The guard that already exists

Every ops tool call goes through `bizos/tools/base.py:guarded_call`:
build a `PolicyRequest` from verified context → `risk_inputs` extractor (record counts, recipients
and fields measured from the database, not taken from the model's claims) → PHI/card classifier →
`policy.engine.evaluate()` → audit → **ALLOW | DRAFT_ONLY | REQUIRE_APPROVAL | DENY**.
`PRECHECKS` run after policy, for example "nothing to invoice". Approved actions are later released
through `bizos/tools/executor.py`.

`evaluate()` (`bizos/policy/engine.py`) applies 22 deterministic rules, and the most restrictive one
wins. They cover: implemented, domain enabled, integration connected, RBAC, data area, PHI, card,
child, biometrics, restricted classification, licensed judgment, always-requires-approval,
critical risk, risk floor, execution mode, auto risk ceiling, record limit, financial limit, email
volume, recipient domains, CRM field allowlist, and self-approval.

**This already covers spec guard steps 1 (allowed), 4-partial and 6.** The spec's risk tiers would
become a second, parallel permission system unless they are derived from this engine. **[SPEC≠CODE]**
The spec does not mention this engine at all.

Not implemented today, compared with spec §5.6:
- Step 2: Pydantic validation. Tools take `**kwargs`. `bizos/agents/tool_args.py` has JSON schemas
  and normalisation but no validation messages.
- Step 3: "IDs looked up" (`id_fields`). Not tracked anywhere.
- Step 4: `requires_prior`. Not modelled for ops tools. The campaign flow enforces its own order in
  `CampaignSession`.
- Idempotency keys on writes. None.
- Confirm cards. **No confirm-card path exists in the backend or the UI.** The UI (`ui/src/app/chat/page.tsx`)
  renders approval and draft cards only. Agno 3.0.9 does support `requires_confirmation` and
  `continue_run`, so the API exists.

## 3. Roles, permissions, modes: what the code actually has

- **Roles:** `VIEWER, DRAFTER, APPROVER, OPERATOR, ADMIN` (`bizos/types.py`). Users can hold
  several roles. **Data areas** (`exec, hr, salary, finance, legal`) are a separate grant
  (`data_scopes`).
  **[SPEC≠CODE]** `evals/cases.yaml` uses the roles `sales` and `member`, which do not exist.
  "finance access" maps to a data area, not a role.
- **Per-role permission:** `DENIED | ALLOWED | DRAFT_ONLY | APPROVE_ONLY | EXECUTE_AFTER_APPROVAL`,
  from templates in `bizos/rbac/matrix.py`. Admins can override this per tool and role
  (`admin_set_tool_permission`).
- **Execution modes:** `ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE`. There is a workspace
  mode, a per-pack mode ceiling, a per-request requested mode, and a **per-tool mode
  (`tool_modes`) that can only narrow, never widen** (`engine.py:72`).
- **Risk:** `LOW | MEDIUM | HIGH | CRITICAL`, plus `always_requires_approval`, `licensed_judgment`
  and `financial` flags.
- **ADMIN mirrors OPERATOR for execution** (`matrix.py`). Admins get no bypass of approval.
  **[SPEC≠CODE]** Spec §5.7 says that for `high` tools admins get a confirm card instead of the
  approval queue. That contradicts a deliberate design decision written in the code.

## 4. Tool inventory: 83 registry tools + 6 campaign tools = 89

All 83 have a `ToolSpec` in `bizos/rbac/registry.py` and an `@effect` implementation in
`app/assistant/assistant.py` (PART 1). The 6 campaign tools (`update_selection`, `show_contacts`,
`show_campaigns`, `propose_email`, `approve_move`, `send_approved`) are raw OpenAI function
specs in `CAMPAIGN_TOOLS` (`assistant.py:3191`). They are executed by `CampaignSession` and **do
not go through `guarded_call` or the registry.** They are gated only by `ctx.is_admin`.
**[SPEC≠CODE]** CLAUDE.md requires "every tool call goes through the guard". Today the campaign
tools do not.

### 4.1 Spec default tier vs what the code does today

Only the rows where the spec's tier would change behaviour are listed.

| Tool | Spec tier | Code today | Gap |
|---|---|---|---|
| crm_delete_record | high (admins get a confirm card) | CRITICAL, `always_requires_approval`, **approval for every role including ADMIN**; DRAFTER denied | Spec loosens it for admins |
| finance_draft_invoice | confirm | HIGH, `always_requires_approval`, DRAFTER = draft only | Spec loosens it |
| finance_propose_adjustment | confirm | CRITICAL, `licensed_judgment`, always approval, DRAFTER denied | Spec loosens it |
| legal_flag_for_counsel | write | HIGH, `licensed_judgment`, always approval | Spec loosens it |
| n8n_trigger_webhook | confirm | HIGH, external, always approval | Spec loosens it |
| memory_escalate_conflict | write | MEDIUM, always approval | Spec loosens it |
| email_send_message | high | HIGH external. DRAFTER: draft only; APPROVER: approval; OPERATOR/ADMIN: **runs directly** in AUTO mode | Spec tightens it for operators and admins |
| calendar_* writes | confirm | external. DRAFTER: draft, APPROVER: approval, OPERATOR: runs | Mostly fits |
| admin_* writes (17) | mix of write/confirm/high | all LOW, `admin_config` (skips the mode queue), run directly for ADMIN | Spec adds friction, which is reasonable, but it is new behaviour |
| admin_handoff_assessment | read | **write** (`admin_config`; it saves an assessment) | Spec says read |
| admin_scan_alerts → admin_alerts(rescan) | read | scan is a **write** (`admin_config`) | Merged tool would need an argument-dependent tier |
| approvals_* | read/write/confirm | `governs_queue`; the action store enforces approver role and no self-approval | Fits |

### 4.2 Domains: the spec's 15 router domains vs the code's 9 packs

The code has **domain packs**: `general, operations, sales, intake, planning, strategy (ADVISE
ceiling), finance (WAIT_FOR_APPROVAL ceiling), brand (DRAFT ceiling), legal (ADVISE ceiling)`.
Packs are **purchased and enabled per workspace** (`enabled_domains`, `purchased_domains`), and they
set the policy domain and mode ceiling for their tools. The spec's 15 domains (`crm`, `email`,
`admin_users`, ...) are a different axis: they group tools by topic for routing.
**[SPEC≠CODE]** They are not interchangeable. The router domains must be a new grouping layered on
top of packs, and each tool must still be bound to its pack's scope (`pack_scope`), or the mode
ceilings and domain-enabled checks are lost.

## 5. Merge candidates checked against the code (ARCHITECTURE.md §6)

The rule in §6: merge only if the tools act on the same object, take similarly shaped arguments, and
are allowed for the same roles.

| Proposed merge | Same object | Similar args | Same roles | Verdict |
|---|---|---|---|---|
| approvals_approve / reject / request_changes → approvals_decide | ✅ action | ✅ `action_id` or `match`, `comment` (required for request_changes) | ✅ DECIDE_APPROVAL | **Merge OK** |
| youtube_my_channel + my_videos | ✅ | ✅ | ✅ READ_ALL | **Merge OK** |
| youtube_video_details + video_comments | ✅ | ⚠️ details takes `video_ids: list`, comments takes `video_id` | ✅ | OK with care |
| admin_list_alerts + admin_scan_alerts → admin_alerts | ✅ | ✅ | ✅ ADMIN_ONLY | OK, but **scan is a write**, so the tier depends on `rescan` |
| memory_add_fact / add_sop / record_decision → memory_add | ✅ | ⚠️ fact/sop: `title, content, key, tags`; decision: `title, decision, decided_on, approved_by, context` | ✅ | OK with kind-specific required fields |
| memory_get_fact + memory_history → memory_get | ⚠️ | ❌ get_fact takes `key` + `category`; history takes `item_id` (a version-chain id) | ✅ | **Doubtful.** The spec's `fact_id` exists in neither tool |
| crm_search_contacts / companies / deals → crm_search | ✅ | ❌ deals has **no `query`** (only `stage, owner`) | ✅ READ_ALL, but **pack membership differs** (deals not in intake/planning; companies not in planning) | Possible, but the per-object pack rules must be kept |
| crm_update_contact_field + crm_bulk_update_contacts → crm_update_contacts(contact_ids, fields) | ✅ | ❌ **bulk takes no IDs.** It is `field, value, lifecycle_filter` and updates every contact matching a lifecycle (or **all contacts** if the filter is empty). There is no update-by-ID-list in the connector. | ❌ field update: WRITE_INTERNAL, MEDIUM; bulk: WRITE_PROTECTED, HIGH (DRAFTER draft-only, APPROVER approval) | **Do not merge as specified.** Needs a decision (§8 Q3) |
| admin_set_user_roles / access / status → admin_update_user | ✅ user | ⚠️ all take `user` (id, email or **fuzzy name**), not `user_id`. **set_user_roles replaces the whole role list**; access supports add/remove | ✅ ADMIN_ONLY | **Blocked by CLAUDE.md:** "never merge a destructive tool (… deactivate …)". `set_user_status` is deactivate. §8 Q2 |
| admin_set_tool_permission + admin_set_tool_mode → admin_configure_tool | ✅ tool | ❌ permission is `(tool, role, DENIED/ALLOWED/DRAFT_ONLY/APPROVE_ONLY/EXECUTE_AFTER_APPROVAL/default)`. **Mode is an ExecutionMode that only narrows**, not a risk tier | ✅ | **Spec model is wrong.** `roles_add/roles_remove` cannot express the 5 permission levels, and "mode overrides the default tier" is not what the code does |

## 6. Domain rules the spec lists vs the code

| Rule (spec §5.6 step 5) | In code? |
|---|---|
| Cannot remove your own admin role | ✅ in the `admin_set_user_roles` effect (chat only) |
| Cannot deactivate yourself | ✅ in the `admin_set_user_status` effect (chat only) |
| Workspace cannot be left with zero active admins | ❌ **Not implemented anywhere.** Another admin can demote or suspend the last remaining admin |
| admin_configure_tool / admin_update_user cannot be disabled or removed from the admin role | ❌ not implemented |
| Campaign item must be approved before send | ✅ in `CampaignSession` (`approve_move` needs a yes in a later turn; `send_approved` needs approval evidence) |

These rules live inside effect functions, not in a guard, so the admin REST API path does not go
through them.

## 7. Other things the spec does not account for

- **IDs are often not IDs.** `crm_get_contact` accepts an id **or an email**. Approvals accept
  `action_id` **or `match` (title words)**. Admin user tools accept a **name**. A strict
  "IDs looked up" guard (step 3) would block flows that work today unless these fields are excluded
  or the guard treats a user-typed email or name as known.
- **Regulated-data refusal and platform grounding** run before the model. The new pipeline must keep
  them ahead of the router.
- **Self-check / nudge loop and `claims.py`** already prevent false "done" claims. The spec's reply
  composer would replace the main mechanism, so we need to decide whether to keep both in v2.
- **The `why` argument** is injected into every tool and shown in the admin trace. The spec does not
  mention it.
- **The frontend contract** (`content`, `actions`, `awaiting_approval`, `drafts`, `trace`,
  `tool_activity`) must be preserved for v2 behind the flag.
- **Tests run on real Postgres** with two provisioned workspaces (`tests/conftest.py`). The CRM
  connector is a Postgres-backed mock. `test_unified_assistant.py` drives the real Agno agent against
  a scripted OpenAI-compatible server. That pattern is reusable for evals and fits CLAUDE.md's
  "mocked tools only", since no real email, CRM or calendar is called.
- **Agno 3.0.9 is pinned** (`requirements.txt`) and installed. I verified these names exist:
  `Agent(output_schema, tool_hooks, tool_call_limit, pre_hooks)`, `@tool(requires_confirmation=…,
  requires_user_input=…)`, `continue_run`, `agno.eval.reliability.ReliabilityEval`,
  `agno.guardrails.{PromptInjectionGuardrail, PII}`.
- **`assistant.py` is one 4,607-line file**, deliberately. The v2 modules (router, guard, composer)
  should be separate plain-Python modules as CLAUDE.md asks. They should not be added to that file.
- The legacy engine (`ASSISTANT_ENGINE=legacy`) is still there, which means three paths after v2 is
  added.

## 8. Decisions needed before Phase 1

1. **plan.md.** Please add it, or tell me the phases. I can't proceed past Phase 0 without it. (Resolved.)
2. **admin_update_user vs CLAUDE.md.** The spec merges `admin_set_user_status` (deactivate) into
   `admin_update_user`, but CLAUDE.md forbids merging deactivate with another tool. Options: keep a
   separate `admin_set_user_status`, or merge roles and access only.
3. **crm_update_contacts.** Bulk update is filter-based (lifecycle, or every contact), not by ID list.
   Options: (a) the wrapper loops `crm_update_contact_field` over `contact_ids`, keeping the old bulk
   tool hidden but intact; (b) add an update-by-IDs method to the connector; (c) don't merge. This
   also affects `bulk_update_001`, which assumes IDs.
4. **Risk tiers vs the existing policy engine.** Should v2 tiers be *derived* from
   `evaluate()` (ALLOW→run, DRAFT_ONLY/REQUIRE_APPROVAL→approval queue, plus a new "confirm" layer
   only on top), or should they *replace* it? My recommendation: derive, and never loosen. The 6
   tools in §4.1 where the spec is looser than the code (delete, invoice, adjustment, counsel, n8n,
   escalate) keep the code's behaviour.
5. **Confirm cards.** These need new backend paused-run handling and a new UI card. Is UI work in
   scope, or should v2 map "confirm" to the existing approval and draft cards for now?
6. **admin_configure_tool shape.** It has to carry the real 5-level permission per role, plus an
   ExecutionMode that only narrows. OK to change the spec's argument model to
   `(tool, permissions: {role: level|default}, mode)`?
7. **Eval roles.** Map `sales` → `OPERATOR` or `DRAFTER`? `member` → `VIEWER`? The expected outcome
   of `multi_crm_email_001` (approval for "sales") only holds for DRAFTER (gets a draft) or APPROVER
   (gets approval). For OPERATOR the email runs directly.
8. **Campaign tools under the guard.** OK to give them `ToolSpec`s (ADMIN_ONLY) so they go through
   the v2 guard, while keeping `CampaignSession` as the executor?
9. **Zero-active-admins rule.** It doesn't exist today. Should it be added to the shared admin layer
   (protecting the REST API too), or only to the v2 guard?

## 9. Open product questions (after Phase 1)

1. **ADVISE cap on the strategy and legal packs.** `strategy_record_option` and
   `legal_flag_for_counsel` are listed only in the strategy and legal packs, and both packs have an
   ADVISE mode ceiling. So the policy check denies both tools for every role, admins included, and the
   current assistant never shows them. Is that intended? Owner: product. Pack logic is unchanged until
   this is answered. Under v2 both stay hidden by the "always deny → hidden" rule (plan.md Phase 5).
