# agentv2 → club-freedom: agent v2 pipeline (behind `AGENT_PIPELINE_V2`, off by default)

## Summary

A rebuilt way for the FreedomBot assistant to pick and run tools, built next to the current assistant:

- **Router:** picks the relevant tool domains for the message.
- **Guard:** checks schema, provenance (IDs and addresses come only from tool results or the user's
  own words) and prerequisites, then runs the existing policy check (`guarded_call`). That check
  remains the only permission system.
- **Executor:** gets only the routed tools.
- **Reply composer:** builds the acknowledgement from tool results, so the reply never claims an
  action that did not succeed.

**With `AGENT_PIPELINE_V2` unset or off (the default), the chat endpoint behaves as before.** The
exceptions are the shared-code changes in the review table below.

This PR adds 27 commits on top of `club-freedom`:
- the eval runner and its 501 cases, checked against the policy dry-run;
- the fix rounds for routing, claims and confusable tools;
- two shared-code fixes: connection pools and email addresses.

Design and decisions: `docs/agent/plan.md`, `ARCHITECTURE.md` and `DISCOVERY.md`.

## Review carefully: changes outside v2

Every commit that changes behaviour for the current assistant, the legacy engine or the REST API
(from `docs/agent/RELEASE_NOTES.md`). **New in this PR:** `8ecd1a5`, `616338c`. The others are already
on `club-freedom` and are listed for completeness.

| Commit | In this PR | What changes |
|---|---|---|
| `b880ade` | already merged | The control store refuses any demotion or suspension that would leave no active admin. Admins can no longer remove their own Admin role or suspend themselves over REST. |
| `87eff03` | already merged | The six campaign tools get admin-only ToolSpecs and go through the policy check. `send_approved` needs `approval_evidence` taken from the user's latest message. |
| `3b14712` | already merged | A campaign send the policy would queue is refused clearly, instead of creating an approval card that could never run. |
| `127fafc` | already merged | Refactor: `guarded_call`'s pre-checks move into the shared `assess()`. No intended behaviour change. |
| `ffc5dc4` | already merged | `crm_bulk_update_contacts` requires `lifecycle_stage` (or `"ALL"`). `crm_create_task` accepts `deal_id`. The permission and mode schemas offer fixed values. |
| `e1fd027` | already merged | Test-only: the acquisition migration env keeps existing loggers enabled. |
| `86152f7` | already merged | `finance_lookup_policy`, `legal_find_clause`, `brand_get_voice` and `strategy_list_priorities` drop the `general` domain. |
| `1bc6210` | already merged | The chat endpoint calls v2 when `AGENT_PIPELINE_V2` is `1`, `true`, `yes` or `on`. Unchanged otherwise. |
| `4cf0d85` | already merged | `n8n_trigger_webhook` drops the `general` domain. It still always needs approval in its packs. |
| `477b6dc` | already merged | Approval `match` phrases consider every open request, not only the newest 200. An ambiguous phrase is refused. |
| `52140e0` | already merged | New workspace setting `timezone` (IANA name, default `Asia/Kolkata`), backfilled for existing workspaces. Changed with `admin_update_settings`. |
| `9fe0e35` | already merged | The current assistant's prompt gives the date and time in the workspace timezone instead of UTC. |
| `12a8a5e` | already merged | Workspace calendar: a time without an offset is read in the workspace timezone in app code and stored timezone-aware. Before, it was read in the DB server's zone (UTC), so "3pm" was stored as 20:30 IST. |
| `ed775cb` | already merged | v2 only: workspace timezone in v2 prompts, and no campaign tools loaded mid-request. |
| **`8ecd1a5`** | **new** | Deprovisioning a workspace, or a provisioning that fails partway, closes its cached agno `PostgresDb` connection pool and its registry engines. `agnodb.clear_cache()` closes pools. Before, each removed workspace kept its connections open until the process exited. |
| **`616338c`** | **new** | `email_send_message` and `email_create_draft` refuse any recipient or cc that is not an email address (for example "Raj"), before anything is queued or sent. The message tells the model to look the address up. Applies to both assistants. |

## Eval results

All runs use `gpt-4.1-mini` with mocked tools: no real CRM, email, calendar or finance calls. Each
case runs in its own throwaway workspace. Every expected gate is checked against the policy dry-run
by a test (`tests/test_eval_cases.py`), and three canary cases must fail on purpose. Each run passed
its checks: 0 crashes, every canary caught, no leftovers between cases.

**Latest finished run** (fix round, 495 cases, `evals/reports/20261022-134401`):

| | v2 (run 1 / run 2) | v2 without router | current assistant |
|---|---|---|---|
| Cases passed | 395 / 391 (80% / 79%) | 400 / 493 (81%) | 269 (54%) |
| Hidden-tool calls | 0 / 0 | 0 | 0 |
| Real false successes (checked by hand) | 1 / 1 | 2 | 33 |
| Tool selection | 84% / 83% | 88% | 69% |
| Permission cases | 83% / 83% | 81% | 87% |
| Forbidden tool executed | 96% / 96% | 96% | 94% |
| Cost per case (list prices) | $0.0022 / $0.0019 | $0.0048 | $0.0057 |
| Latency, median | 3.4 s / 3.3 s | 2.8 s | 3.4 s |

- **Router vs no router:** 34–36 cases pass only with the router (clarifying questions, permission,
  multi-step) and 40–46 only without it (admin, approvals, confusable pairs). The router halves the
  cost. Run-to-run variation is about 7% of cases.
- **Stronger executor (`gpt-4.1`)** was tried: +3 cases for 4× the cost, so it is not the default.
  It stays available as `EXECUTOR_MODEL_ID`.
- **v2's false successes in that run** ("has been flagged", "I’ve saved a note") come from phrasings
  the composer missed then. `aaa8354` fixes them.
- **The current assistant's 33 false successes** are claims such as "the note has been added" while
  the note was only waiting for approval. That is a problem in today's production path; v2 strips
  such claims.

**A re-run is in progress** (v2 twice, v2 without router, current assistant; 501 cases). It covers
the routing fix round (`bfe04dd`) and the final composer (`aaa8354`). Its results will be added here.
In its small pre-run, v2 had 0 false successes and 0 hidden-tool calls.

## Gates (`docs/agent/plan.md`)

**Hard gates, required for any rollout:**

| Gate | Status |
|---|---|
| 0 hidden-tool calls | Met in every run so far. |
| 0 real false successes | Fixed in `aaa8354`; to be confirmed by the re-run in progress. |
| v2 at least as good as the current assistant overall | Met: 395 / 391 against 269 of 495. |

**Accuracy goals, tracked but not blocking shadow mode:**

| Goal | Status |
|---|---|
| Tool selection ≥ 95% | Not yet: 83–84%. |
| Permission and forbidden-tool cases 100% | Not yet: 83% and 96%. Nearly all remaining permission failures are workarounds: `memory_add` or `support_create_ticket` used in place of a missing tool. No user ever saw or called a tool outside their role. |

The next step after merge and confirmation is Phase 9, starting with shadow mode only.

## Known open questions

- **Strategy and legal packs have an Advise mode cap.** `strategy_record_option` and
  `legal_flag_for_counsel` are listed only in packs capped at ADVISE. So the policy check denies them
  for every role, admins included, and v2 hides them. Intended? (DISCOVERY.md §9.1)
- **Billables keep the `general` domain.** `finance_list_pending_billables` and `finance_list_invoices`
  keep it, so a direct `guarded_call` can list billables with the finance pack off. The `operations`
  domain gives the same route. Waiting for a team decision. (DISCOVERY.md §9.3)
- **`alembic check` fails, and Alembic INFO lines are printed twice.** `alembic -c
  acquisition_alembic.ini check` fails on a fresh database: the models and migrations disagree on
  some indexes (for example `ix_after_booking_youtube_enabled_order`, `uq_prospects_npi`). The
  migration CLI also prints every INFO line twice. Both predate this work and are not fixed here, by
  decision. (DISCOVERY.md §9.7)
- **Other gaps:**
  - "Yes, send it" after a campaign turn is not always routed to the campaign hand-off.
  - Strategy options are still sometimes saved with `memory_add`.
  - The claim check cannot see a workaround described as the real action ("Change order CO-9 has
    been logged" when a CRM note was written); the workaround eval cases cover these.
- **Not pushed anywhere:** eval reports (`evals/reports/` is git-ignored).
