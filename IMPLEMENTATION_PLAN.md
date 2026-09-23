# IMPLEMENTATION_PLAN.md

Every requirement from the brief mapped to **EXISTING** (use upstream unchanged),
**NEEDS MODIFICATION** (wrap/extend upstream), **NEW** (must be built), or
**NOT IN PHASE 1**.

Written after the inspection in [CURRENT_ARCHITECTURE.md](CURRENT_ARCHITECTURE.md) and
before any application code.

---

## A. Architectural decisions

**A1 — Agno is a dependency, not a fork.** `agno==3.0.9` is pinned in `requirements.txt`.
Forking the framework to add an application layer would guarantee divergence from upstream.
The *application* shape is forked from `context`.

**A2 — Package name `bizos/`.** Not `platform/` — that shadows the Python stdlib `platform`
module and would break imports inside dependencies.

**A3 — Tenancy = workspace registry + per-client engine.** A control-plane database holds
clients/users/roles. Each client gets a **provisioned, physically separate** workspace:

- `deployment_type = "dedicated_db"` (Phase 1 default) → its own Postgres **database**.
- `deployment_type = "dedicated_schema"` → its own Postgres **schema** in a shared database.

Both give table-level physical separation. There is **no code path that queries two clients'
data**: `WorkspaceRegistry.engine_for(client_id)` returns an engine bound to exactly one
database/schema, and the *only* way to get a workspace connection is through a
`TenantContext` carrying a resolved `client_id`. A shared table with a `tenant_id` column is
explicitly **not** used for customer content. This satisfies "configurable for either model"
without ever depending on a `WHERE tenant_id = ?` for confidentiality.

**A4 — Enforcement in three independent layers**, so no single failure is fatal:

1. **Surface** — `Agent.tools` is a per-run callable; a tool the caller's role or the
   workspace's mode forbids is never given to the model (reuses `context/agents/policy.py`).
2. **Interception** — a `tool_hook` calls the policy engine before the function body runs.
   Every tool also calls the engine itself, so a tool invoked outside the agent is still gated.
3. **Substrate** — ADVISE mode and all read tools run on a Postgres connection opened with
   `default_transaction_read_only=on` (reuses `context/db/session.py`).

**A5 — Two approval concepts, both used.** Agno's `agno_approvals` handles the *in-run pause*.
Our `approval_requests` + `actions` + `approval_events` tables model the *business action*
lifecycle that outlives the run (§8 requires `DRAFT…CANCELLED`, risk class, policy reason,
comments, per-transition history). The action record is the source of truth; the executor
only ever runs an action that is `APPROVED`.

---

## B. Requirement mapping

### 1. Client isolation — **NEW** (built on EXISTING pieces)
| Piece | Status | Source |
|---|---|---|
| Control-plane store (clients, users, roles) | NEW | `bizos/control/` |
| Workspace provisioning (`CREATE DATABASE` / `CREATE SCHEMA` + DDL) | NEW | `bizos/tenancy/provisioning.py` |
| Per-client engine cache + read-only engine | NEEDS MODIFICATION | generalizes `context/db/session.py` |
| Tenant context propagation (`contextvars`) | NEW | `bizos/tenancy/context.py` |
| Agno session/memory/knowledge isolation | EXISTING | `PostgresDb(db_url=<client url>)` per workspace |

### 2. Organizational memory — **NEW**
`memory_items` + `memory_versions` in each workspace. Categories `FACT | SOP | DECISION |
CORRECTION`. Supersession: a correction closes the prior version (`superseded_by`,
`effective_until`, `status=SUPERSEDED`) and inserts a new active version in **one
transaction** — never a destructive update. `agno_memories` stays for conversational
per-user memory; org memory is separate and explicit.

### 3. Source-aware memory — **NEW.** Columns on every version: `source_type, source_id,
created_by, updated_by, created_at, updated_at, confidence, approval_status, effective_from,
effective_until, superseded_by`. AI-inferred entries default to
`approval_status=PENDING`; retrieval ranks approved-and-explicit above inferred.

### 4. Users and roles — **NEW** (auth primitives EXISTING)
`VIEWER < DRAFTER < APPROVER < OPERATOR < ADMIN` in `bizos/rbac/`. Password login
(PBKDF2-HMAC-SHA256, stdlib) issuing a JWT whose `sub` is the user id and whose claims carry
`client_id` + `role`. Agno verifies the JWT (`authorization=True`); the platform verifies the
claims. **Roles are never read from a request body.**

### 5. Four execution modes — **NEW**
`ADVISE | DRAFT | WAIT_FOR_APPROVAL | AUTO_WITHIN_SCOPE` resolved per run:
workspace default → domain override → per-request override (only *downward*, never upward).
The mode is an input to the policy engine and to the tool-surface builder.

### 6. Policy engine — **NEW.** `bizos/policy/engine.py`. Single entry point
`evaluate(PolicyRequest) -> PolicyDecision` returning `ALLOW | DENY | DRAFT_ONLY |
REQUIRE_APPROVAL`. Ordered, deterministic rules; **no LLM in the path**. Inputs: client,
user, role, domain, tool, action, mode, risk, data classification, financial amount, record
count, recipient domains, PHI/card flags, tenant policy overrides.

### 7. Tool permissions — **NEW.** Central `ToolSpec` registry (tool name → risk, classification,
write?, per-role permission, approval requirement) + a `tool_permissions` workspace table for
per-client overrides. Declared **once**, consumed by the surface builder, the policy engine
and the UI.

### 8. Approval queue — **NEW** (REST/UI), **EXISTING** primitive.
`actions`, `approval_requests`, `approval_events`. Actions: Approve / Reject / Request Changes /
Edit Before Approval. Every transition appended to `approval_events`.

### 9. Audit log — **NEW.** `audit_events` (append-only: `REVOKE UPDATE, DELETE` on the table)
with a redaction pass that strips secrets before write. Agno tracing stays for latency/token
observability.

### 10. Integrations — **NEW adapters over EXISTING toolkits.**
Phase 1: **Mock CRM** (deterministic, seeded, testable), **Email** (mock + Gmail adapter),
**Calendar** (mock + Google adapter). Each connector exposes narrow capabilities; the tool
layer maps 1:1 to them. Slack / accounting / storage: interface defined, adapter deferred.

### 11 & 12. Domain packs — **NEW.** `bizos/domains/`. Phase 1 built: `operations`, `sales`,
`intake`, `planning`, plus `general`. Configuration-only placeholders: `strategy`, `finance`,
`brand`, `legal` (registered, admin-toggleable, minimal instructions — **NOT IN PHASE 1** for
depth).

### 13. Workflows — **EXISTING engine, NEW playbooks.** `lead_intake`,
`meeting_preparation`, `weekly_sales_summary` as agno `Workflow`s with custom step functions
that route every write through the policy engine.

### 14. Guardrails — **NEEDS MODIFICATION.** Reuse `PromptInjectionGuardrail` /
`PIIDetectionGuardrail` as pre-hooks; add licensed-judgment, PHI and card-data guardrails as
policy rules (code, not prompt) plus a secret-redaction utility.

### 15. Data classification — **NEW.** `PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED` on
tools, connectors and memory items; consumed by the policy engine.

### 16. Human review queue — **NEW.** `human_review_items` (`OPEN | RESOLVED | DISMISSED`).

### 17. Frontend — **NEEDS MODIFICATION.** Fork `agent-ui`; keep its chat components; add
Memory, Knowledge, Approvals, Workflows, Integrations, Audit, Admin sections and a login page.

### 18. Chat behavior — **NEW.** Mode-aware instructions **and** a mode-aware tool surface, so
the refusal is structural; the prompt only explains it.

### 19. Tool design rule — **NEW.** Narrow verbs only. A registry test asserts no tool matches
a generic-execution denylist.

### 20. Prompt-injection protection — **NEEDS MODIFICATION.** All connector/knowledge output is
wrapped in `<untrusted_content>` fences with an explicit non-instruction preamble, and
instruction-shaped patterns in retrieved content are neutralized before the model sees them.

### 21. Tenant configuration — **NEW.** `ClientSettings` (`enabled_domains`,
`default_execution_mode`, `allow_phi`, `allow_card_data`, `enabled_integrations`,
`memory_policy`, `retention_policy`, `approval_policy`, `risk_policy`).

### 22. Risk levels — **NEW.** `LOW | MEDIUM | HIGH | CRITICAL`; CRITICAL always requires a human.

### 23. Action limits — **NEW.** `max_records_per_action`, `max_financial_amount`,
`max_emails_per_run`, `approved_recipient_domains`, `auto_updatable_crm_fields`.

### 24. Observability — **EXISTING** (`tracing=True`) + audit latency fields.

### 25. Database — reuse ~20 agno tables; add 16 platform tables (4 control-plane, 12 workspace).

### 26. API — **NEEDS MODIFICATION.** Platform routers mounted on the **same** FastAPI app via
`AgentOS(base_app=...)`. No second web framework.

### 27. Security — **NEW/PARTIAL** per SECURITY.md.

### 28. Prohibitions — honored; see the "Do-not list compliance" section of the final report.

---

## C. Build order (matches STEP 4 → STEP 16)

| Step | Deliverable |
|---|---|
| 4 | `bizos/tenancy`, `bizos/control`, provisioning, auth, `.env.example`, compose |
| 5 | `bizos/rbac` — roles, tool registry, permission matrix |
| 6 | `bizos/policy` — engine, rules, limits, classification |
| 7 | Execution modes + mode-aware tool surface |
| 8 | `bizos/actions` — actions, approval queue, executor |
| 9 | `bizos/memory` — org memory, versioning, corrections |
| 10 | `bizos/audit` |
| 11 | `bizos/connectors` — mock CRM, email, calendar |
| 12 | `bizos/domains` + `bizos/workflows` |
| 13 | `ui/` — fork of agent-ui + 7 new sections |
| 14 | `tests/` — all §30 scenarios, executed |
| 15 | Security review |
| 16 | Final report with PASS/FAIL/PARTIAL/NOT TESTED |

## D. Explicitly out of Phase 1
Salesforce/HubSpot/Pipedrive live adapters; QuickBooks/Xero; Drive/OneDrive/SharePoint;
Slack/Teams write; deep Strategy/Finance/Brand/Legal packs; per-client Kubernetes
deployment (compose only); external secret manager (interface + env-backed implementation
only); SSO/SCIM.
