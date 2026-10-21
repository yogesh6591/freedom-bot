# Release notes

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
