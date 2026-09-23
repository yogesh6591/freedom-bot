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
  workflows: string[]
  mode_ceiling: string | null
  enabled?: boolean
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

export interface ReviewItem {
  id: string
  question: string
  context: string
  choices: string[]
  recommended_option: string | null
  confidence: number | null
  status: 'OPEN' | 'RESOLVED' | 'DISMISSED'
  workflow_id: string | null
  created_at: string | null
  resolution: string | null
  resolved_by: string | null
}

export interface AuditRow {
  seq: number
  id: string
  event_type: string
  user_id: string | null
  actor_role: string | null
  tool: string | null
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
  role: Role
  tools: { name: string; title: string; write: boolean; risk: string; category: string }[]
  pending_approvals: number
  open_reviews: number
}

export interface ChatResponse {
  session_id: string
  content: string
  mode: ExecutionMode
  tool_activity: { tool: string; ok: boolean }[]
  actions: ActionRecord[]
  awaiting_approval: ActionRecord[]
  drafts: ActionRecord[]
  reviews: ReviewItem[]
}
