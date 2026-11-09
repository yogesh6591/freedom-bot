/** Shapes returned by the platform API. */

export type Role = 'VIEWER' | 'DRAFTER' | 'APPROVER' | 'OPERATOR' | 'ADMIN'

export type ExecutionMode =
  | 'ADVISE'
  | 'DRAFT'
  | 'WAIT_FOR_APPROVAL'
  | 'AUTO_WITHIN_SCOPE'

export type ActionStatus =
  | 'DRAFT'
  | 'PENDING_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'EXECUTING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface SessionUser {
  id: string
  email: string
  display_name: string
  roles: Role[]
  primary_role: Role
  /** The title an admin picked. Null when no title describes this person's access yet. */
  job_role?: string | null
  job_role_label?: string | null
  can_approve?: boolean
  data_scopes?: string[]
  /** Per-person app access an admin set: direct, approval or draft (CRM today). */
  app_access?: Record<string, AppAccessLevel>
  /** What a change in each app does today for this person, from the policy check. */
  app_access_result?: Record<string, AppAccessResult>
  /** M15-20: may open the cross-client owner view. */
  is_platform_owner?: boolean
}

export type AppAccessLevel = 'direct' | 'approval' | 'draft'

export interface AppAccessResult {
  level: AppAccessLevel
  effect: 'ALLOW' | 'REQUIRE_APPROVAL' | 'DRAFT_ONLY' | 'DENY' | string
  rule: string
  reason: string
}

export interface SessionClient {
  id: string
  slug: string
  company_name: string
  deployment_type?: string
  default_execution_mode: ExecutionMode
  enabled_domains: string[]
  allow_phi: boolean
  allow_card_data: boolean
}

export interface DomainPack {
  name: string
  title: string
  description: string
  phase: number
  depth: 'full' | 'placeholder'
  tools: string[]
  workflows?: string[]
  mode_ceiling: string | null
  enabled?: boolean
  purchased?: boolean
}

export interface Me {
  user: SessionUser
  client: SessionClient
  domains: DomainPack[]
  permissions: {
    tool: string
    permission: string
    can_draft: boolean
    can_invoke: boolean
    can_approve: boolean
  }[]
}

export interface MemoryVersion {
  id: string
  version_no: number
  content: string
  status: 'ACTIVE' | 'SUPERSEDED' | 'ARCHIVED'
  source_type: string
  created_by: string
  confidence: number
  approval_status: 'APPROVED' | 'PENDING' | 'REJECTED'
  effective_from: string | null
  effective_until: string | null
  superseded_by: string | null
  correction_reason: string | null
  corrected_by: string | null
  updated_at: string | null
  is_active: boolean
  is_authoritative: boolean
  attributes: Record<string, unknown>
}

export interface MemoryItem {
  id: string
  category: 'FACT' | 'SOP' | 'DECISION' | 'CORRECTION'
  memory_key: string
  title: string
  domain: string | null
  tags: string[]
  classification: string
  access_area?: string | null
  label?: 'fact' | 'estimate'
  updated_at: string | null
  current: MemoryVersion | null
  versions?: MemoryVersion[]
}

export interface ApprovalEvent {
  id: number
  event: string
  from_status: string | null
  to_status: string | null
  actor_id: string
  actor_role: string | null
  comment: string | null
  created_at: string | null
}

export interface ActionContent {
  kind: string
  headline: string
  summary: string
  fields: { label: string; value: string }[]
  lines: string[]
}

export interface ActionRecord {
  id: string
  title: string
  description: string
  explanation: string
  status: ActionStatus
  tool: string
  integration: string | null
  domain: string | null
  payload: Record<string, unknown>
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  classification: string
  execution_mode: ExecutionMode
  policy_reason: string
  requested_by: string
  requested_by_role: string | null
  requested_by_name?: string | null
  requested_by_email?: string | null
  content?: ActionContent | null
  record_count: number
  expires_at: string | null
  created_at: string | null
  result: Record<string, unknown> | null
  error: string | null
  approval: {
    status: string
    reason: string
    required_role: string
    decided_by: string | null
    comment: string | null
  } | null
  events?: ApprovalEvent[]
}

export interface AuditRow {
  seq: number
  id: string
  event_type: string
  user_id: string | null
  /** Resolved display name (or email) for the actor, when known. */
  user_name?: string | null
  /** One-line “what they did” for the table. */
  summary?: string | null
  actor_role: string | null
  tool: string | null
  execution_mode?: string | null
  integration: string | null
  workflow_id: string | null
  action_id: string | null
  decision: string | null
  status: string | null
  risk_level: string | null
  error: string | null
  latency_ms: number | null
  created_at: string
  request: Record<string, unknown> | null
  result: Record<string, unknown> | null
}

export interface WorkflowSummary {
  id: string
  name: string
  description: string
  domain: string
  steps: string[]
  cron: string | null
  required_inputs: string[]
  config: {
    enabled?: boolean
    execution_mode?: string | null
    cron?: string | null
    recipients?: string[]
  }
}

export interface WorkflowRunResult {
  id: string
  status: string
  template: string
  error: string | null
  steps: {
    name: string
    ok: boolean
    summary: string
    awaiting_approval: boolean
    action_id: string | null
  }[]
}

export interface Integration {
  id: string | null
  category: string
  provider: string | null
  display_name: string
  status: string
  enabled: boolean
  last_error: string | null
  connected_at?: string | null
}

export interface ChatContext {
  mode: ExecutionMode
  mode_description: string
  domain: string
  enabled_domains?: string[]
  assistant_name?: string
  role: Role
  data_scopes?: string[]
  tools: {
    name: string
    title: string
    write: boolean
    risk: string
    category: string
    can_view: boolean
    can_run: boolean
    tool_mode?: string | null
  }[]
  sending_accounts?: string[]
  pending_approvals: number
  inefficiency_alerts?: {
    id: string
    title: string
    detail: string
    directional_cost?: string
    status?: string
  }[]
}

export interface AssistantTraceCall {
  tool: string
  domain?: string
  why?: string
  args?: string
  ok?: boolean
  effect?: string
  result?: string
}

export type PersonFieldSource = 'typed' | 'database' | 'chat' | 'missing'

export type EmailCardScope = 'everyone' | 'keep' | ''

export interface EmailViewPerson {
  email: string
  first_name: string
  last_name: string
  company: string
  sources: {
    first_name: PersonFieldSource
    last_name: PersonFieldSource
    company: PersonFieldSource
  }
}

export interface EmailViewPreview {
  email: string
  mode?: string
  subject: string
  body: string
}

export interface EmailViewAudience {
  campaign: string
  campaign_is_new: boolean
  will_send_now: string[]
  also_waiting: { name: string; email: string }[]
  template_changed: boolean
  scope: EmailCardScope
  scope_needed: boolean
}

export interface EmailView {
  draft_id?: string
  template?: { subject: string; body: string }
  assessment_url?: string
  people?: EmailViewPerson[]
  previews?: EmailViewPreview[]
  warnings?: string[]
  audience?: EmailViewAudience
}

export interface SentViewRow {
  name: string
  email: string
  kind: 'first' | 'follow_up'
}

export interface SentView {
  count: number
  first: number
  follow_up: number
  minutes: number
  rows: SentViewRow[]
  skipped?: { name: string; email: string; reason: string }[]
}

export interface TemplateView {
  subject: string
  body: string
  stages?: { stage: string; label: string; body: string }[]
  sending_on: boolean
  saved: boolean
}

export interface AssistantTrace {
  engine?: string
  model?: string
  reasoning_effort?: string
  tool?: string
  explanation?: string
  tool_log?: AssistantTraceCall[]
  self_check?: string[]
  guardrails?: string[]
  campaign_id?: string
  campaign_name?: string
  campaign_schedule_timing?: string[]
  email_address?: string
  email_address_list?: string[]
  email_from?: string
  email_subject?: string
  email_body?: string
  email_draft?: string
  email_view?: EmailView
  show_email_card?: boolean
  sent_view?: SentView
  template_view?: TemplateView
  reply_note?: string
  campaigns_to_show?: { name: string; timing: string }[]
  people_to_show?: { name: string; email: string }[]
}

export interface ChatResponse {
  session_id: string
  content: string
  mode: ExecutionMode
  domain?: string
  routed?: boolean
  tool_activity: { tool: string; ok: boolean }[]
  actions: ActionRecord[]
  awaiting_approval: ActionRecord[]
  drafts: ActionRecord[]
  email_draft?: string
  trace?: AssistantTrace
}

/** One of the caller's saved conversations (M01-11). */
export interface ChatSessionSummary {
  session_id: string
  title: string
  turns: number
  started_at: string | null
  updated_at: string | null
}

/** A file attached in the chat (POST /api/chat/attachments): what was read from it, never its text. */
export interface ChatAttachment {
  id: string
  name: string
  kind: 'pdf' | 'docx' | 'csv' | 'xlsx' | 'txt' | 'md'
  size_bytes: number
  pages?: number
  rows?: number
  characters?: number
}

export interface ChatSessionMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  domain: string | null
  meta: {
    attachments?: ChatAttachment[]
    mode?: ExecutionMode
    tool_activity?: { tool: string; ok: boolean }[]
    awaiting_approval?: Partial<ActionRecord>[]
    drafts?: Partial<ActionRecord>[]
    json?: AssistantTrace
  }
  created_at: string | null
}

export interface ChatSessionDetail {
  session_id: string
  messages: ChatSessionMessage[]
}
