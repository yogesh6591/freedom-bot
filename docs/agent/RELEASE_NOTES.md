# Release notes

## Changes outside AGENT_PIPELINE_V2 (review together before pushing)

Every commit on this branch since `main` that changes behaviour for the current assistant, the legacy
engine or the REST API. The other commits only add docs, eval cases or v2-only modules, which nothing
outside `AGENT_PIPELINE_V2` loads.

| Commit | What changes |
|---|---|
| `b880ade` | The control store refuses any demotion or suspension that would leave no active admin (locked against races). Admins can no longer remove their own Admin role or suspend themselves over REST, which was previously allowed. |
| `87eff03` | The six campaign tools get admin-only ToolSpecs and every call goes through the policy check. `send_approved` refuses unless `approval_evidence` is the user's own words from their latest message, and logs the evidence on every attempt. |
| `3b14712` | A campaign send that the policy would queue (lowered approval floor, or an override requiring approval) is refused with a clear message instead of creating an approval card that could never run. The last-admin chat message no longer says "Not changed:" twice. |
| `127fafc` | Refactor only, no intended behaviour change: `guarded_call`'s pre-action checks move into the shared `assess()` so the dry-run can reuse them. Listed because it touches the one path every tool call takes. |
| `ffc5dc4` | `crm_bulk_update_contacts` requires `lifecycle_stage` and refuses calls with no scope. `crm_create_task` accepts `deal_id`. The tool-permission and tool-mode schemas offer fixed values (details below). |
| `e1fd027` | Test-only effect: the acquisition migration environment keeps existing loggers enabled (`disable_existing_loggers=False`). The migration CLI output is unchanged; loggers are no longer silenced during the test session's in-process migration. |
| `86152f7` | `finance_lookup_policy`, `legal_find_clause`, `brand_get_voice` and `strategy_list_priorities` drop `general` from their registry domains. The policy check now denies them outside their own pack. When the finance or legal pack is off, users are no longer told "needs Finance/Legal access" for those capabilities. |
| `1bc6210` | `app/api/chat.py`: the chat endpoint calls the v2 pipeline when `AGENT_PIPELINE_V2` is `1`, `true`, `yes` or `on`. With the flag unset or off (the default), the endpoint behaves exactly as before. |
| `4cf0d85` | `n8n_trigger_webhook` drops `general` from its registry domains. The policy check denies it under the general domain (nothing called it there); it still always needs approval in operations, sales, intake and planning. |
| `477b6dc` | Approval decisions and cancellations by `match` phrase consider every request in the relevant statuses, not only the newest 200. A phrase that also matches an older request is refused as ambiguous instead of acting on the newer one. A phrase matching only an older request now finds it (before: "No action matching"). |
| `52140e0` | New workspace setting `timezone` (IANA name, default `Asia/Kolkata`). The control-plane migration adds it to every existing workspace that lacks it (a value already set is kept). Admins change it with `admin_update_settings` (also over REST, via the same settings update); unknown names are refused with "Unknown timezone …". |
| `9fe0e35` | The current assistant's prompt gives the date and time in the workspace timezone ("Current date and time: … (Asia/Kolkata, the workspace timezone)") instead of UTC. Between 00:00 and 05:30 IST, "today" and "tomorrow" now resolve to the IST date. |
| `12a8a5e` | Workspace calendar (not Google): a time without an offset is read in the workspace timezone in app code, and every time is stored timezone-aware. Before, it was read in the database server's timezone (UTC with the compose image, so "3pm" was stored as 15:00 UTC = 20:30 IST). Event times are returned in the workspace zone, and a non-ISO time is refused before anything is written. Google calendars are unchanged (they use the calendar's own zone). |
| `ed775cb` | v2 only (no effect with the flag off): v2 prompts use the workspace timezone and ask for local times without an offset; `load_tools("campaign")` no longer loads the campaign tools and tells the executor to have the user send the campaign part separately. |
| `8ecd1a5` | Deprovisioning a workspace closes its cached agno `PostgresDb` connection pool (and the registry engines for it); a provisioning that fails partway closes the pools it opened; `agnodb.clear_cache()` closes pools instead of only forgetting them. Before, each removed workspace kept its pooled connections open until the process exited. `WorkspaceRegistry.dispose` takes an optional schema. |
| `616338c` | `email_send_message` and `email_create_draft` refuse any recipient or cc that is not an email address (for example "Raj"), before anything is queued or sent, with: "'Raj' is not an email address. Look up the address with crm_search_contacts (customers and leads) or admin_list_users (colleagues), or ask the user for it. Nothing was sent." Applies to the current assistant and v2. A call with no recipients is refused too. The tool schemas carry the address pattern. |
| `c0ff029` | compose.yaml loads `.env` into the api container (optional file) and mounts a writable `composio-cache` volume; the Dockerfile creates `/app/.composio-cache` owned by the app user. No permission change. |
| `d33c8de` | New `ComposioHubSpotConnector`: when a user connects HubSpot in Composio, CRM tools read and write HubSpot instead of the workspace CRM. Every call still goes through the guard and policy check; gates are unchanged. |
| `0b9b089` | Admins can upload PDF, text or markdown documents (`/api/memory/documents`, admin only). Text is stored as approved memory with category DOCUMENT and the chosen access area, so the existing area rules decide who can see it. No tool or gate change. |
| `4ece7d8` | approvals_list no longer fails when a finance_draft_invoice is pending: it imported the deleted bizos/tools/domains.py. Bug fix only; visibility and gates are unchanged. Test: tests/test_approvals_list_invoice.py. |
| `b31df1b` | HubSpot searches follow HubSpot's paging cursor (100 per page, up to 2,000 records), so pipeline counts cover every deal instead of the first 100. Search results report HubSpot's total, a partial read says so, and full stage labels ("Appointment Scheduled") map to HubSpot's stage ids. Reads only; no gate or permission change. Test: tests/test_hubspot_paging.py (mocked). |
| `75cacdf` | Memory search adds vector hits (Agno OpenAIEmbedder + pgvector, table memory_embeddings) to keyword hits, using the same visibility clauses (active, approved, data areas) and redaction. Never embedded: restricted-area, CONFIDENTIAL/RESTRICTED, regulated or card-like text, and restricted sections. Off with MEMORY_VECTOR_SEARCH=off, without an OpenAI key, or where pgvector is unusable (shared-database workspaces); every failure falls back to keyword search. Tests: tests/test_memory_vectors.py (fake embedder). |

Already on `cepoch/club-freedom` before this work, and not part of it: `fe5b089`, `6afd759`.

## Tool schema changes (`ffc5dc4`)

These apply to every assistant path, not only v2.

- **`crm_bulk_update_contacts` requires `lifecycle_stage`.** Pass one lifecycle stage (such as
  `lead`), or `"ALL"` to change every contact. A call that names no scope is refused with
  "lifecycle_stage is required: …" and nothing changes. Before, leaving the filter out updated every
  contact in the CRM. Actions already queued with the older `lifecycle_filter` argument still run as
  approved. `field` now only accepts the CRM's updatable fields.
- **`admin_set_tool_mode` no longer offers `AUTO_WITHIN_SCOPE`.** A per-tool mode can only make a tool
  stricter than the workspace mode, and AUTO is the least strict mode, so setting it could never
  tighten anything. To remove a per-tool override, pass `"default"`. The offered values are
  `ADVISE`, `DRAFT`, `WAIT_FOR_APPROVAL` and `default`. `admin_set_tool_permission` now offers its
  real values as fixed choices: roles `VIEWER`…`ADMIN`, and permissions `DENIED`, `DRAFT_ONLY`,
  `APPROVE_ONLY`, `EXECUTE_AFTER_APPROVAL`, `ALLOWED` or `default`.
- **`crm_create_task` accepts `deal_id`**, so a task can be linked to a deal as well as a contact.
  The tool always supported it; the schema now offers it. The policy check treats the call the same.

## Admin lockout protection (REST API)

The admin user endpoints now refuse three changes that could lock a workspace out of its own
administration. Before this release, the REST API allowed all three. Only the chat assistant blocked
the first two.

| Change | Endpoint | Status | Response body |
|---|---|---|---|
| Removing your own Admin role | `PATCH /api/users/{user_id}/roles` with `user_id` = yourself and `roles` without `ADMIN` | `403 Forbidden` | `{"detail": "You cannot remove your own Admin role. Ask another admin."}` |
| Suspending yourself | `PATCH /api/users/{user_id}/status` with `user_id` = yourself and `status: "SUSPENDED"` | `403 Forbidden` | `{"detail": "You cannot suspend your own account. Ask another admin."}` |
| Removing the last active admin (demotion or suspension) | either endpoint above | `409 Conflict` | `{"detail": "This would leave the workspace with no active admin. Make someone else an admin first."}` |

Nothing is changed when any of these is returned.

### What clients should know
- **To hand over admin rights:** first make the other person an admin, then have *them* remove your
  Admin role or suspend your account.
- **When you will see 409:** the API always acts as an active admin, so a single request from one
  admin cannot leave zero admins. You will see 409 when two admin changes race, for example two
  admins demoting each other at the same moment. Exactly one of them succeeds.
- **Telling the 409s apart:** `409 Conflict` is also returned when a browser tab is signed in to a
  different workspace or user than it expects. That response's `detail` is exactly
  `"workspace_changed"`, so check for that value; any other 409 from these endpoints is the last-admin
  refusal.
- **No machine-readable error codes:** the platform REST API has no error-code convention. Errors are
  `{"detail": "<message for a person>"}`, and `"workspace_changed"` is the only fixed value. So these
  refusals carry no `code` field (such as `last_admin`, `self_demotion` or `self_suspension`), and we
  have not invented one for this release. Until a convention exists:
  - 409 with any `detail` other than `"workspace_changed"` is the last-admin refusal;
  - 403 is shared with other refusals on the same endpoints (the caller is not an admin, or their
    account is not active). The two self-rules can only be told apart from those by the `detail` text.
    `/roles` returns the self-demotion message and `/status` returns the self-suspension message. (The acquisition assessment API's `{"ok", "error", "code"}`
  shape belongs to that separate public service.)
- **Unchanged:** demoting or suspending *another* admin still works, as long as at least one active
  admin remains. Changes to non-admin users are not affected.

### Also applies to
The same rules are enforced in shared code, so the chat assistant (both engines) gives the same
refusals. In chat, the message is prefixed with "Not changed:".
