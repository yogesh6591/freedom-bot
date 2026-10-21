# Implementation plan (revised after Phase 0)

Global rules
- The code is the source of truth. Discovery findings are in docs/agent/DISCOVERY.md.
- The existing policy check is the only permission and gating system. New code may add checks
  that make things stricter. It must never make any tool, for any role, less strict than today.
- Build the new pipeline behind AGENT_PIPELINE_V2. The current assistant keeps working.
- Exception: the Phase 2 security fixes go into shared code on purpose, so every path is protected.
- Never delete an existing tool function.
- Stop at the end of each phase and wait for approval. Ask when a product decision is needed.

## Phase 0: Discovery
Done. See docs/agent/DISCOVERY.md.

## Phase 1: Spec alignment (docs only, no code changes)
Tasks
- Rename claude.md to CLAUDE.md and commit it.
- Replace bizos/agent/prompts with bizos/agents/prompts everywhere in the docs.
- Update ARCHITECTURE.md:
  - 5.6 Guard: new pre-checks (schema validation, ID provenance, prerequisites), then the existing
    policy check, unchanged.
  - 5.7: remove the tier table as a source of truth. Gates come from the policy check
    (run / approval / draft / deny). The six always-approval tools stay as they are, admins included.
  - 6: merges limited to the approved list in Phase 4.
  - 7: routing domains sit on top of the 9 packs. A tool is visible only if its pack is enabled for the
    workspace, the pack's mode limits allow it, and the user's role allows it.
  - Confirm cards are deferred; v1 uses the existing approval and draft cards.
- Update TOOL_DESCRIPTIONS.md (confusable table), the router prompt, the executor prompt and the
  playbooks to the final tool list.
- Rewrite evals/cases.yaml with real roles (sales → DRAFTER, member → VIEWER) and add role variants
  where outcomes differ.
Done when: docs, prompts and eval cases agree with the code and with each other.
STOP.

## Phase 2: Security fixes (shared code)
Each fix is a separate commit with tests.
- Zero-admins rule in shared admin code: block any demotion, suspension or deactivation that leaves
  zero active admins, and block users from removing their own admin role. This covers the REST API
  and both assistant paths.
- Register the six campaign tools in the registry so every call goes through the policy check.
  Admin-only, as today. Add the prerequisite: send_approved requires approval_evidence that matches
  text in the user's latest message (case- and whitespace-insensitive, non-empty); otherwise deny and
  tell the model to ask the user for approval. Log the evidence with every send. No approved-state
  requirement; admins' workflow is otherwise unchanged.
Done when: tests prove the new rules, and tests prove admins' campaign behaviour is unchanged
apart from the new prerequisite.
STOP.

## Phase 3: Registry metadata and descriptions (no behaviour change)
- Add to every tool: routing domain, description, id_fields, requires_prior.
- Add a side-effect-free policy dry-run: given a tool, a user and arguments, return what the policy
  check would do (run / approval / draft / deny) without executing anything. Reuse the existing
  rules; never duplicate them.
- Rewrite every description per TOOL_DESCRIPTIONS.md from real behaviour. The "Effect:" line must
  match the dry-run, e.g. "always needs approval".
- Add a ToolResult adapter for tools that don't return the contract. Wrap; don't change internals.
Done when
- A test asserts every model-visible tool has a domain and a description with "Use when:" and
  "Don't use:" lines.
- A parity test shows the dry-run matches the real policy check for every tool × role.
- The current assistant is unchanged.
STOP.

## Phase 4: Tool consolidation (approved subset only)
Merge
- approvals_decide (approve, reject, request changes).
- youtube_my_channel(include_videos) and youtube_video(include_comments).
- admin_alerts(rescan): only if list and scan have identical policy rules; otherwise keep both.
- memory_add, memory_get, crm_search: only if they pass the merge test (same object, compatible
  arguments, identical policy rules). Report any that fail and leave them unmerged.
Do not merge
- crm_update_contact_field and crm_bulk_update_contacts. In the bulk tool's model-facing schema,
  lifecycle_stage is required and "ALL" must be passed explicitly.
- admin_set_user_status stays separate. Merge roles + access into admin_update_user only if their
  policy rules are identical.
- admin_set_tool_permission and admin_set_tool_mode stay separate. The permission tool uses an enum
  of the five real levels; the mode tool is described as tighten-only.
Gating of merged tools
- Each call is gated as the specific old tool its arguments map to, by calling guarded_call under
  that old tool's name:
  - approvals_decide: decision=approve → approvals_approve, reject → approvals_reject,
    request_changes → approvals_request_changes.
  - memory_add: kind=fact → memory_add_fact, sop → memory_add_sop, decision → memory_record_decision.
  - admin_update_user: roles_add/roles_remove → admin_set_user_roles; access_grant/access_revoke →
    admin_set_user_access. A call that changes both must pass both, or nothing changes.
  - youtube_my_channel / youtube_video: include_videos / include_comments map to the old video or
    comments tool; the base call maps to the old channel or details tool.
- A merged tool is visible only if at least one mapped old tool is allowed for the user.
- Its enums are narrowed per request to the allowed values (e.g. "reject" is dropped for a role that
  cannot reject; kind "sop" is dropped if memory_add_sop is denied).
Done when: tests cover every merged tool's argument mapping and enum narrowing, and a parity test
shows each call is gated exactly like the old tool it maps to.
STOP.

## Phase 5: Router and tool loading
- Route model; router prompt loaded from bizos/agents/prompts/router.md.
- Domain menu filtered by enabled packs, pack mode limits and the user's role.
- Bundles, ALWAYS_ON (memory_search, load_tools), and load_tools (intersected with visible tools, logged).
- memory_search prefetch in parallel with routing.
- Any tool whose policy dry-run is "deny" for the current user regardless of arguments is hidden from
  the model and from load_tools. Applies to every tool (e.g. strategy_record_option and
  legal_flag_for_counsel while their packs are capped at ADVISE; see DISCOVERY.md §9).
Done when: tests cover bundle ∩ visibility; non-admins never see admin_* or campaign domains;
load_tools can't add a hidden tool; disabled packs hide their tools; a tool that is always denied for
the user is hidden from the model and from load_tools.
STOP.

## Phase 6: Guard integration
- Agno tool hook: pre-checks in order (schema, ID provenance, prerequisites), then the existing
  policy check, then run / existing approval card / existing draft card / deny.
- known_ids collected per session from tool results and user messages.
- Idempotency keys for every write.
- Strictness parity test: for every tool × role, the gate under AGENT_PIPELINE_V2 is equal to or
  stricter than the current path.
Done when: one test per guard branch, plus the parity test, pass.
STOP.

## Phase 7: Executor, reply composer, pipeline
- handle(query, user, session) behind AGENT_PIPELINE_V2; a fresh executor Agent per request;
  tool_call_limit 8.
- Pending actions use the existing approval and draft cards.
- Reply composer per ARCHITECTURE.md 5.8.
Done when: integration tests with mocked tools cover success, pending approval, draft created,
denied, tool failure, and "never says done when nothing succeeded".
STOP.

## Phase 8: Evals
- evals/run.py with mocked tools loaded from fixtures. Use Agno ReliabilityEval for tool names and
  arguments, plus custom checks for domains, outcome (from the policy dry-run) and forbidden tools.
- Grow the cases to 400+: about 3 per tool, 50 confusable-pair cases, 40 multi-step,
  25 permission denials, 25 clarify or ambiguous, 20 Hindi/Hinglish, 15 adversarial, plus role variants.
- Baseline: run a flat agent (all visible tools, no router) and v2, and report both.
- Gates (confirm with me after the baseline): 0 false-success; 100% on permission and
  forbidden-tool cases; at least 95% correct tool selection.
STOP.

## Phase 9: Tracing and rollout
- Turn on AgentOS tracing.
- Shadow mode: v2 plans on real traffic without executing writes, logged next to the current assistant.
- Enable v2 for admins first, then everyone.
- Weekly script: pull misroutes and load_tools calls from traces into new eval cases.
STOP.

## Later (not v1)
- Confirm cards using Agno's requires_confirmation and new UI, for actions the policy currently
  runs without any gate.