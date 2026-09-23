# CURRENT_ARCHITECTURE.md

Inventory of the upstream open-source code this platform is built on, produced **before**
any application code was written (STEP 1 / STEP 2 of the implementation method).

## 0. Starting state

The target working directory `/Users/gaurav/Desktop/yash/freedombot_9sep` was **empty**.
"The existing repository" is therefore the four upstream Agno projects, which were cloned
into `.reference/` (git-ignored, inspection only — never shipped or vendored):

| Repo | URL | State |
|---|---|---|
| `agno` | https://github.com/agno-agi/agno | v3.0.9, monorepo (`libs/agno`, `libs/agno_infra`, `libs/agnoctl`) |
| `context` | https://github.com/agno-agi/context | AgentOS application template (agents/, app/, db/, workflows/, compose) |
| `agent-ui` | https://github.com/agno-agi/agent-ui | Next.js 15 + React 18 chat UI for AgentOS |
| `scout` | https://github.com/agno-agi/scout | AgentOS application, context-provider pattern |

`agno` is consumed as a **pinned PyPI dependency** (`agno==3.0.9`), not forked — forking a
90 MB framework to add an application layer would be the opposite of reuse. `context` is
the **application-shape fork base** (its `app/`, `db/`, `agents/policy.py` layout is
adopted directly). `agent-ui` is the **frontend fork base**.

---

## 1. What Agno 3.0.9 already implements

Verified by reading `.reference/agno/libs/agno/agno/`.

### 1.1 Agent runtime — `agno/agent/agent.py`
Full agent loop with model abstraction, streaming, retries, structured output,
fallback models, reasoning, parser/output models. **No need to build an agent engine.**

Directly relevant constructor parameters (verified in the `__init__` signature):

| Parameter | Why it matters here |
|---|---|
| `tools: Sequence[...] \| Callable[..., List]` | Tools can be a **callable resolved per run** — the hook for per-role, per-mode tool surfaces |
| `cache_callables: bool` | Must be `False` so the tool callable re-runs for every request |
| `pre_hooks` / `post_hooks` | Accepts callables, `BaseGuardrail`, `BaseEval` — the input-guardrail seam |
| `tool_hooks: List[Callable]` | Wraps every tool call — the mandatory policy-enforcement seam |
| `instructions: str \| List[str] \| Callable` | Per-run system prompt, resolved from `RunContext` |
| `knowledge`, `knowledge_filters` | RAG with metadata filtering |
| `db`, `memory_manager`, `enable_agentic_memory` | Sessions + user memory persistence |
| `dependencies`, `metadata` | Arbitrary per-run payload reachable from hooks/tools |

### 1.2 AgentOS — `agno/os/`
FastAPI application factory (`AgentOS(...).get_app()`), with:

- `os/app.py` — accepts `base_app: FastAPI`, so **custom routers mount into the same app**
  (`on_route_conflict="preserve_agentos"`). This is how the platform API is added without
  a second web framework.
- `os/auth.py` — JWT bearer verification, `security_key` mode, service-account tokens
  (`sa:` prefix), internal scheduler token → `__scheduler__` principal, scope checks.
- `os/scopes.py` — route→scope mapping, `get_accessible_resource_ids`, `has_required_scopes`.
- `os/config.py` — `AuthorizationConfig(user_isolation=True)` scopes session/memory/run
  REST endpoints to the verified JWT subject.
- `os/routers/` — ready-made REST for `agents`, `teams`, `workflows`, `sessions`, `memory`,
  `knowledge`, `approvals`, `traces`, `metrics`, `evals`, `schedules`, `job_queue`,
  `learnings`, `media`, `registry`, `service_accounts`, `database`, `health`.
- `os/interfaces/` — `slack`, `agui`, `a2a`, `telegram`, `whatsapp`.
- `os/mcp*.py` — MCP server surface + built-in OAuth 2.1 authorization server.
- Scheduler: `scheduler=True`, `scheduler_poll_interval`, `scheduler_base_url`.
- Tracing: `tracing=True` → `agno_traces` / `agno_spans` + `/traces` router.

### 1.3 Approvals — `agno/approval/` + `agno/db/schemas/approval.py` + `os/routers/approvals/`
- `@approval(type="required"|"audit")` decorator; `required` implies
  `requires_confirmation=True` on the `Function`.
- `Approval` dataclass persisted to `agno_approvals`: `id, run_id, session_id, status
  (pending|approved|rejected|expired|cancelled), source_type, approval_type, pause_type,
  tool_name, tool_args, expires_at, agent_id, team_id, workflow_id, user_id, schedule_id,
  schedule_run_id, source_name, requirements, context, resolution_data, resolved_by,
  resolved_at, created_at, updated_at, run_status`.
- REST: `GET /approvals`, `/approvals/count`, `/approvals/{id}`, `/approvals/{id}/status`,
  `POST /approvals/{id}/resolve`, `DELETE /approvals/{id}`.

**Assessment:** this is a *run-pause* approval primitive keyed to a paused LLM run. It is
excellent for "pause this run until a human clicks approve" but it does **not** model a
business action with a lifecycle that outlives the run (`DRAFT → PENDING_APPROVAL →
APPROVED → EXECUTING → COMPLETED/FAILED`), a risk classification, a policy reason, an
approver comment thread, or a state-transition history. Both are used: agno's for the
in-run pause, ours for the durable business-action record.

### 1.4 Guardrails — `agno/guardrails/`
`BaseGuardrail` (sync + async `check`), `PromptInjectionGuardrail` (18 substring patterns),
`PIIDetectionGuardrail` (SSN / credit-card / email / phone regex, `mask_pii` option),
`OpenAIModerationGuardrail`. Raise `InputCheckError` with a `CheckTrigger`.

**Assessment:** reusable as an *input* pre-hook. Does not cover untrusted **tool output** /
retrieved content, which is where the real injection risk sits (§20 of the brief).

### 1.5 Database — `agno/db/`
Adapters for postgres, async_postgres, mysql, sqlite, mongo, redis, dynamo, firestore,
clickhouse, singlestore, surrealdb, gcs_json, json, in_memory, valkey. `PostgresDb` auto-creates
its tables. Configurable table names and `db_schema` (default `agno`).

Tables agno owns: `agno_sessions`, `agno_runs`, `agno_memories`, `agno_metrics`,
`agno_eval_runs`, `agno_knowledge`, `agno_traces`, `agno_spans`, `agno_versions`,
`agno_components`, `agno_component_configs`, `agno_component_links`, `agno_learnings`,
`agno_schedules`, `agno_schedule_runs`, `agno_jobs`, `agno_approvals`, `agno_auth_tokens`,
`agno_service_accounts`, `agno_mcp_oauth_*`.

### 1.6 Memory — `agno/memory/` + `agno/db/schemas/memory.py`
`UserMemory(memory, memory_id, topics, user_id, input, created_at, updated_at, feedback,
agent_id, team_id)` + `MemoryManager` (agentic add/update/delete).

**Assessment — the biggest gap.** There is **no** category (fact/SOP/decision/correction),
**no** provenance (`source_type`, `source_id`, `created_by`, `confidence`), **no** approval
status, **no** effectivity window, and **no** supersession chain. Requirements §2 and §3 are
almost entirely new work.

### 1.7 Knowledge — `agno/knowledge/`
Readers (PDF/DOCX/CSV/TXT/Markdown/JSON/web/YouTube/S3/GCS), chunking strategies, 20+
vector DBs (incl. `PgVector`), embedders, rerankers, async content pipeline with status
tracking in `agno_knowledge`. **Fully reusable — no custom vector search.**

### 1.8 Workflows — `agno/workflow/`
`Workflow`, `Step`, `Parallel`, `Condition`, `Loop`, `Router`, `StepInput`/`StepOutput`,
persisted runs, `WorkflowSchedule`. Registered on `AgentOS(workflows=[...])` with a REST
surface. **Reusable for playbooks.**

### 1.9 Tools — `agno/tools/` (155 modules)
Includes `googlecalendar`, `gmail`, `slack`, `salesforce`, `jira`, `postgres`, `sql`,
`googledrive`, `github`, plus `Toolkit`, `@tool` decorator and `Function` metadata
(`requires_confirmation`, `external_execution`, `requires_user_input`, `approval_type`).

### 1.10 Other
`agno/tracing`, `agno/scheduler`, `agno/skills` (`LocalSkills` markdown playbooks),
`agno/eval`, `agno/hooks`, `agno/integrations`, `agno/job_queue`, `agno/compression`.

---

## 2. What `context` (the fork base) demonstrates

`.reference/context` is an AgentOS app whose structure this repo adopts.

| File | What it gives us |
|---|---|
| `app/main.py` | AgentOS wiring: `db`, `agents`, `workflows`, `interfaces`, `lifespan`, `authorization=is_prd()`, `AuthorizationConfig(user_isolation=True)`, scheduler, MCP, tracing |
| `app/identity.py` | Deriving a **verified** principal from `RunContext.user_id`; reserved-principal namespaces (`anon`, `__scheduler__`, `__oauth__:`, `sa:`); fail-closed `is_owner()` |
| `agents/policy.py` | **The single most valuable pattern.** Identity-conditioned tool surface (`context_tools`) + identity-conditioned instructions + a `pre_hook` (`normalize_identity`) + a `tool_hook` (`enforce_capture_only`) as defense in depth. Our 5-role RBAC and 4 execution modes are a direct generalization of this. |
| `db/session.py` | Two engines split by role: read/write pinned to a schema via `search_path`, and a **Postgres-level read-only** engine (`default_transaction_read_only=on`) that prompt tricks cannot bypass. Plus a SQLAlchemy `before_cursor_execute` guard rejecting cross-schema writes. Directly reused for ADVISE mode and tenant confinement. |
| `db/schema.py` | Declarative `Table`/`Column` list → generates DDL **and** the agent's schema documentation from one source. Reused for the tenant workspace schema. |
| `app/schedules.py`, `workflows/` | Schedule registration + workflow module layout |
| `compose.yaml`, `Dockerfile`, `scripts/entrypoint.sh` | Local Docker setup |
| `docs/SECURITY.md` | Threat model write-up style |

`scout` contributes the **context-provider** idea (each source exposes exactly two
natural-language tools, `query_<source>` / `update_<source>`) — deliberately **not** adopted,
because requirement §19 demands narrow per-capability tools (`crm_search_contacts`,
`email_send_message`) so permissions and audit can bind to a single verb.

---

## 3. What `agent-ui` gives us

Next.js 15 (App Router) + React 18 + Tailwind + shadcn/Radix + zustand + `nuqs`.
`src/components/chat/` (ChatArea, Messages, ChatInput, Sidebar/Sessions),
`src/components/ui/` (Button, Dialog, Select, Tooltip, Textarea, MarkdownRenderer, icons),
`src/api/`, `src/hooks/` (SSE streaming handler), `src/types/`.

Covers **Chat** only. Memory, Knowledge, Approvals, Workflows, Integrations, Audit and Admin
sections do not exist upstream.

---

## 4. Requirement → status matrix (pre-implementation)

| # | Requirement | Upstream status |
|---|---|---|
| 1 | Client isolation | **GAP.** Agno has `user_id` isolation and configurable `db_schema`, but no client/workspace concept, no provisioning, no per-client engine routing |
| 2 | Organizational memory (facts/SOPs/decisions/corrections) | **GAP.** `UserMemory` is a flat string |
| 3 | Source-aware memory / provenance | **GAP** |
| 4 | Users and roles (5 roles) | **PARTIAL.** JWT auth + scopes exist; no user store, no password login, no role model |
| 5 | Four execution modes | **GAP** (the per-run tool callable is the seam) |
| 6 | Policy engine | **GAP** |
| 7 | Tool permissions matrix | **GAP** |
| 8 | Approval queue | **PARTIAL.** Run-pause approvals + REST exist; business-action lifecycle does not |
| 9 | Audit log | **PARTIAL.** Traces/spans/runs exist (observability); a structured security audit trail does not |
| 10 | Business integrations | **PARTIAL.** Gmail/Calendar/Slack/Salesforce toolkits exist but are unscoped, coarse, and unpermissioned |
| 11 | Domain packs | **GAP** |
| 13 | Workflows | **EXISTING** engine, **GAP** for the three business playbooks |
| 14 | Guardrails (licensed judgment / PHI / card data / secrets) | **PARTIAL.** Prompt-injection + PII input guardrails exist |
| 15 | Data classification | **GAP** |
| 16 | Human review queue | **GAP** |
| 17 | Frontend sections | **PARTIAL.** Chat exists; 7 sections do not |
| 20 | Prompt-injection protection | **PARTIAL.** Input-side only; no untrusted-content wrapping of tool output |
| 21 | Tenant configuration | **GAP** |
| 22 | Action risk levels | **GAP** |
| 23 | Action limits | **GAP** |
| 24 | Observability | **EXISTING** (tracing, metrics, spans) |
| 25 | Database design | **PARTIAL.** ~20 agno tables reused; platform tables are new |
| 26 | API | **PARTIAL.** Agno routers reused; platform routers new |
| 27 | Security requirements | **PARTIAL** |

## 5. Conclusion

Roughly **60% of the runtime surface is reusable as-is** (agent loop, sessions, knowledge,
vector search, workflows, scheduling, tracing, MCP, JWT/service-account auth, REST
scaffolding, chat UI, Docker layout). The platform work is a **governance layer**:
tenancy, RBAC, policy engine, execution modes, organizational memory, business-action
approvals, audit, domain packs, connectors and the seven missing UI sections.

The three seams that make the governance layer enforceable **in code rather than in the
prompt** all already exist in Agno and are proven by `context`:

1. `Agent.tools` as a per-run callable → the model never sees a tool its role/mode forbids.
2. `Agent.tool_hooks` → every tool invocation is intercepted before the function body runs.
3. A Postgres-level read-only connection → ADVISE mode cannot write even if everything above fails.
