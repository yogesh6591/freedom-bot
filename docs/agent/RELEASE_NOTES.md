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
