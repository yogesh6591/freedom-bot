'use client'

/** Admin (§17) — users, roles, policies, domains, tool permission matrix. */

import { useCallback, useEffect, useState } from 'react'
import {
  Baby,
  Check,
  CreditCard,
  FileCheck2,
  Fingerprint,
  HeartPulse,
  IdCard,
  Scale,
  UserCheck,
  type LucideIcon
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { PricingStudio } from '@/components/admin/Pricing'
import { AllWorkspaces } from '@/components/owner/WorkspaceMembers'
import { NewClientPanel } from '@/app/owner/NewClientPanel'
import { useSession } from '@/components/SessionProvider'
import {
  DEPARTMENTS,
  DEPARTMENT_MODES,
  modeMeaning,
  tagLabel,
  type AccessRequest
} from '@/lib/access'
import { api } from '@/lib/api'
import type {
  DepartmentMode,
  DomainPack,
  Role,
  SessionUser
} from '@/lib/types'
import {
  Badge,
  Btn,
  DataTable,
  ErrorNote,
  Field,
  PageStack,
  PageHeader,
  Panel,
  SegmentedTab,
  SegmentedTabs,
  inputClass,
  modeTone,
  riskTone,
  type Tone
} from '@/components/common/primitives'

const ROLES: Role[] = ['VIEWER', 'DRAFTER', 'APPROVER', 'OPERATOR', 'ADMIN']
const MODES = ['ADVISE', 'DRAFT', 'WAIT_FOR_APPROVAL', 'AUTO_WITHIN_SCOPE']

interface ToolPolicy {
  name: string
  title: string
  category: string
  risk: string
  classification: string
  write: boolean
  implemented: boolean
  effective_permissions: Record<string, string>
  overridden: boolean
  data_area?: string | null
  view?: Record<string, boolean>
  run?: Record<string, boolean>
}

interface RiskPolicy {
  auto_allowed_max_risk: string
  always_approve_at_or_above: string
  max_records_per_action: number
  max_financial_amount: number
  max_emails_per_run: number
  approved_recipient_domains: string[]
  auto_updatable_crm_fields: string[]
}

interface ClientSettings {
  default_execution_mode: string
  enabled_domains: string[]
  purchased_domains?: string[]
  tool_modes?: Record<string, string>
  retention_policy?: { audit_days: number }
  allow_phi: boolean
  allow_card_data: boolean
  allow_child_data?: boolean
  allow_biometrics?: boolean
  order_dpa_allows?: string[]
  llm_vendor?: string
  llm_baa_covered?: boolean
  risk_policy: RiskPolicy
  approval_policy?: {
    allow_self_approval?: boolean
    approver_role?: string
    expires_after_hours?: number
  }
}

interface PoliciesResponse {
  settings: ClientSettings
  tools: ToolPolicy[]
}

export default function AdminPage() {
  return (
    <AppShell>
      <AdminView />
    </AppShell>
  )
}

function AdminView() {
  const [tab, setTab] = useState<
    | 'setup'
    | 'insights'
    | 'launch'
    | 'pricing'
    | 'users'
    | 'policies'
    | 'domains'
    | 'tools'
    | 'workspaces'
  >('setup')
  const { me } = useSession()
  const platformOwner = Boolean(me?.user.is_platform_owner)
  const [workspacesVersion, setWorkspacesVersion] = useState(0)
  // Platform owners open on Workspaces (onboarding new clients); ?tab= picks a tab.
  const [pickedTab, setPickedTab] = useState(false)
  useEffect(() => {
    if (pickedTab || !me) return
    const wanted = new URLSearchParams(window.location.search).get('tab')
    if (wanted === 'workspaces' ? platformOwner : wanted && wanted !== 'workspaces') {
      setTab(wanted as typeof tab)
    } else if (platformOwner) {
      setTab('workspaces')
    }
    setPickedTab(true)
  }, [me, platformOwner, pickedTab])
  return (
    <PageStack>
      <PageHeader
        eyebrow="Workspace governance"
        title="Administration"
        description="Manage access, configure policies, and set how FreedomBot operates in your organization."
      />
      <div className="-mt-2">
        <SegmentedTabs aria-label="Admin sections">
          {(
            [
              'setup',
              'insights',
              'launch',
              'pricing',
              'users',
              'policies',
              'domains',
              'tools',
              ...(platformOwner ? (['workspaces'] as const) : [])
            ] as const
          ).map((name) => (
            <SegmentedTab
              key={name}
              active={tab === name}
              onClick={() => setTab(name)}
              className="capitalize"
            >
              {name}
            </SegmentedTab>
          ))}
        </SegmentedTabs>
      </div>
      {tab === 'setup' && <Setup />}
      {tab === 'insights' && <Insights />}
      {tab === 'launch' && <LaunchChecklist />}
      {tab === 'pricing' && <PricingStudio />}
      {tab === 'users' && <Users />}
      {tab === 'policies' && <Policies />}
      {tab === 'domains' && <Domains />}
      {tab === 'tools' && <Tools />}
      {tab === 'workspaces' && platformOwner && (
        <>
          <NewClientPanel onCreated={async () => setWorkspacesVersion((v) => v + 1)} />
          <AllWorkspaces key={workspacesVersion} />
        </>
      )}
    </PageStack>
  )
}

interface OnboardingProfileView {
  goals?: string[]
  systems?: string[]
  autonomy_mode?: string
  roles_notes?: string
  access_notes?: string
  sops?: { title?: string; content?: string }[]
  review_points?: string[]
  assessment?: Record<string, unknown>
  open_questions?: string[]
  inconsistencies?: string[]
  unverified?: string[]
  exclusions?: string[]
  baselines?: BaselineRow[]
  handoff_at?: string
  handoff_source?: string
}

/** FB-043: who started the handoff — the sale system automatically, or a manual paste. */
function handoffLabel(source?: string, at?: string): string {
  const when = at ? ` · ${new Date(at).toLocaleString()}` : ''
  if (source === 'checkout' || source === 'contract' || source === 'sale') {
    return `Started automatically after ${source}${when}`
  }
  return `Handoff pasted manually${when}`
}

interface BaselineRow {
  id?: string
  workflow: string
  metric: string
  before_value: string
  after_value: string
  unit: string
  notes: string
  comparison?: string
}

interface Clarification {
  open_questions: string[]
  inconsistencies: string[]
  unverified: string[]
}

const lines = (text: string) =>
  text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean)

function Setup() {
  // Nothing is pre-filled with another company's values: a new client starts
  // empty and the placeholders only show the expected format (M11-08).
  const [goals, setGoals] = useState('')
  const [systems, setSystems] = useState('')
  const [sopTitle, setSopTitle] = useState('')
  const [sopBody, setSopBody] = useState('')
  const [rolesNotes, setRolesNotes] = useState('')
  const [reviewPoints, setReviewPoints] = useState('')
  const [exclusions, setExclusions] = useState('')
  const [baselines, setBaselines] = useState<BaselineRow[]>([])
  const [mode, setMode] = useState('WAIT_FOR_APPROVAL')
  const [assessment, setAssessment] = useState('')
  const [handoffMeta, setHandoffMeta] = useState<string | null>(null)
  const [n8nUrl, setN8nUrl] = useState('')
  const [clarify, setClarify] = useState<Clarification | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const fill = useCallback(
    (p: OnboardingProfileView, { replace }: { replace: boolean }) => {
      if (replace || p.goals?.length) setGoals((p.goals ?? []).join('\n'))
      if (replace || p.systems?.length) setSystems((p.systems ?? []).join('\n'))
      if (p.autonomy_mode) setMode(p.autonomy_mode)
      if (replace || p.roles_notes) setRolesNotes(p.roles_notes ?? '')
      if (replace || p.review_points?.length)
        setReviewPoints((p.review_points ?? []).join('\n'))
      if (replace || p.exclusions?.length)
        setExclusions((p.exclusions ?? []).join('\n'))
      if (replace || p.baselines?.length) {
        setBaselines(
          (p.baselines ?? []).map((b) => ({
            id: b.id,
            workflow: b.workflow || '',
            metric: b.metric || '',
            before_value: b.before_value || '',
            after_value: b.after_value || '',
            unit: b.unit || '',
            notes: b.notes || ''
          }))
        )
      }
      if (p.sops?.[0]?.title) setSopTitle(p.sops[0].title)
      if (p.sops?.[0]?.content) setSopBody(p.sops[0].content)
      if (p.assessment && Object.keys(p.assessment).length) {
        setAssessment(JSON.stringify(p.assessment, null, 2))
      }
      if (p.handoff_at || p.handoff_source)
        setHandoffMeta(handoffLabel(p.handoff_source, p.handoff_at))
      setClarify({
        open_questions: p.open_questions ?? [],
        inconsistencies: p.inconsistencies ?? [],
        unverified: p.unverified ?? []
      })
    },
    []
  )

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<{ profile: OnboardingProfileView }>(
          '/api/onboarding'
        )
        fill(data.profile, { replace: false })
      } catch {
        // First run — empty profile is fine.
      }
    })()
  }, [fill])

  function parseAssessment(): Record<string, unknown> {
    try {
      return assessment.trim() ? JSON.parse(assessment) : {}
    } catch {
      throw new Error('Assessment JSON is invalid')
    }
  }

  async function handoff() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const assessmentObj = parseAssessment()
      if (!Object.keys(assessmentObj).length)
        throw new Error('Paste the assessment JSON first')
      const result = await api.post<{
        profile: OnboardingProfileView
        needs_clarification?: Clarification
        message?: string
      }>('/api/onboarding/handoff', { assessment: assessmentObj })
      fill(result.profile, { replace: true })
      if (result.needs_clarification) setClarify(result.needs_clarification)
      setNote(
        result.message ||
          'Assessment handoff saved. Review fields, then Apply configuration.'
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Handoff failed')
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const result = await api.put<{
        memory_written: { kind: string }[]
        needs_clarification?: Clarification
      }>('/api/onboarding', {
        goals: lines(goals),
        systems: lines(systems),
        sops:
          sopTitle.trim() && sopBody.trim()
            ? [
                {
                  title: sopTitle.trim(),
                  content: sopBody.trim(),
                  key: 'onboarding_sop'
                }
              ]
            : [],
        roles_notes: rolesNotes,
        autonomy_mode: mode,
        review_points: lines(reviewPoints),
        exclusions: lines(exclusions),
        baselines: baselines.filter(
          (b) => b.workflow.trim() || b.metric.trim()
        ),
        assessment: parseAssessment()
      })
      if (n8nUrl.trim()) {
        await api.post('/api/integrations', {
          category: 'n8n',
          provider: 'webhook',
          display_name: 'n8n Webhook',
          enabled: true,
          status: 'CONNECTED',
          config: { webhook_url: n8nUrl.trim() }
        })
      }
      if (result.needs_clarification) setClarify(result.needs_clarification)
      setNote(
        `Configured. Wrote ${result.memory_written?.length ?? 0} memory items. Mode=${mode}.`
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setBusy(false)
    }
  }

  const outstanding =
    clarify &&
    (clarify.open_questions.length ||
      clarify.inconsistencies.length ||
      clarify.unverified.length)

  return (
    <Panel
      title="First-client setup"
      description="Load goals, systems, SOPs and autonomy from the sale/assessment (FB-044)."
    >
      <div className="space-y-4">
        {outstanding ? (
          <div
            role="status"
            className="space-y-3 rounded-lg border border-brand/40 bg-brand/10 px-4 py-3 text-sm"
          >
            <p className="font-geist font-semibold text-primary">
              Needs clarification with the client
            </p>
            {clarify.open_questions.length > 0 && (
              <div>
                <p className="type-caption">Still to ask</p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
                  {clarify.open_questions.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
            {clarify.inconsistencies.length > 0 && (
              <div>
                <p className="type-caption">Answers that disagree</p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
                  {clarify.inconsistencies.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
            {clarify.unverified.length > 0 && (
              <div>
                <p className="type-caption">
                  Client assumptions — not recorded as facts
                </p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
                  {clarify.unverified.map((q) => (
                    <li key={q}>{q}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : null}
        <Field label="Goals (one per line)">
          <textarea
            value={goals}
            onChange={(e) => setGoals(e.target.value)}
            placeholder="e.g. Cut invoice cycle time by 30%"
            className={`${inputClass} min-h-[80px]`}
          />
        </Field>
        <Field label="Systems in scope (one per line)">
          <textarea
            value={systems}
            onChange={(e) => setSystems(e.target.value)}
            placeholder="e.g. HubSpot CRM"
            className={`${inputClass} min-h-[80px]`}
          />
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="SOP title">
            <input
              value={sopTitle}
              onChange={(e) => setSopTitle(e.target.value)}
              placeholder="e.g. Inbound lead response"
              className={inputClass}
            />
          </Field>
          <Field label="Autonomy mode">
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              className={inputClass}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="SOP body">
          <textarea
            value={sopBody}
            onChange={(e) => setSopBody(e.target.value)}
            placeholder="The steps, one per line"
            className={`${inputClass} min-h-[80px]`}
          />
        </Field>
        <Field label="Roles / access notes">
          <textarea
            value={rolesNotes}
            onChange={(e) => setRolesNotes(e.target.value)}
            placeholder="Who sees which areas, and who approves what"
            className={`${inputClass} min-h-[60px]`}
          />
        </Field>
        <Field label="Human review points (one per line)">
          <textarea
            value={reviewPoints}
            onChange={(e) => setReviewPoints(e.target.value)}
            placeholder="e.g. External email send"
            className={`${inputClass} min-h-[60px]`}
          />
        </Field>
        <Field label="Exclusions / out of scope (one per line)">
          <textarea
            value={exclusions}
            onChange={(e) => setExclusions(e.target.value)}
            placeholder="e.g. No payroll tax filing; no customer support chat"
            className={`${inputClass} min-h-[60px]`}
          />
        </Field>
        <div className="space-y-2">
          <p className="type-caption">Baseline metrics (FB-044)</p>
          <p className="text-xs text-muted">
            Record before values for sold workflows at setup. After values fill
            once live.
          </p>
          {baselines.map((row, index) => (
            <div key={row.id || index} className="grid gap-2 md:grid-cols-3">
              <input
                className={inputClass}
                placeholder="Workflow"
                value={row.workflow}
                onChange={(e) => {
                  const next = [...baselines]
                  next[index] = { ...row, workflow: e.target.value }
                  setBaselines(next)
                }}
              />
              <input
                className={inputClass}
                placeholder="Metric"
                value={row.metric}
                onChange={(e) => {
                  const next = [...baselines]
                  next[index] = { ...row, metric: e.target.value }
                  setBaselines(next)
                }}
              />
              <div className="flex gap-2">
                <input
                  className={inputClass}
                  placeholder="Before"
                  value={row.before_value}
                  onChange={(e) => {
                    const next = [...baselines]
                    next[index] = { ...row, before_value: e.target.value }
                    setBaselines(next)
                  }}
                />
                <input
                  className={inputClass}
                  placeholder="Unit"
                  value={row.unit}
                  onChange={(e) => {
                    const next = [...baselines]
                    next[index] = { ...row, unit: e.target.value }
                    setBaselines(next)
                  }}
                />
                <Btn
                  type="button"
                  variant="secondary"
                  size="sm"
                  aria-label="Remove baseline"
                  onClick={() =>
                    setBaselines(baselines.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </Btn>
              </div>
            </div>
          ))}
          <Btn
            type="button"
            variant="secondary"
            size="sm"
            onClick={() =>
              setBaselines([
                ...baselines,
                {
                  id: `baseline_${Date.now()}`,
                  workflow: '',
                  metric: '',
                  before_value: '',
                  after_value: '',
                  unit: '',
                  notes: ''
                }
              ])
            }
          >
            Add baseline
          </Btn>
        </div>
        {handoffMeta && <p className="text-xs text-positive">{handoffMeta}</p>}
        <Field label="Assessment JSON (manual fallback — prefer sale webhook)">
          <textarea
            value={assessment}
            onChange={(e) => setAssessment(e.target.value)}
            placeholder='{"goals": ["…"], "pain": "…", "systems": ["…"], "exclusions": ["…"], "baselines": []}'
            className={`${inputClass} min-h-[80px] font-dmmono text-[11px]`}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Btn
            type="button"
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() => void handoff()}
          >
            Prefill from assessment (manual)
          </Btn>
        </div>
        <Field label="n8n webhook URL (optional — leave blank for local delivery log)">
          <input
            value={n8nUrl}
            onChange={(e) => setN8nUrl(e.target.value)}
            placeholder="https://n8n.example.com/webhook/..."
            className={inputClass}
          />
        </Field>
        {error && <ErrorNote>{error}</ErrorNote>}
        {note && <p className="text-xs text-positive">{note}</p>}
        <Btn disabled={busy} onClick={() => void apply()} size="sm">
          {busy ? 'Applying…' : 'Apply configuration'}
        </Btn>
      </div>
    </Panel>
  )
}

interface InsightAlert {
  id: string
  title: string
  detail: string
  directional_cost?: string
  status?: string
  source?: string
  kind?: string
  access_area?: string | null
  last_seen_at?: string
}

const ALERT_KIND_LABEL: Record<string, string> = {
  repeated_question: 'From chat questions not answered by memory',
  pending_memory: 'From memory awaiting approval',
  senior_routine: 'From user actions',
  routine_approvals: 'From the approval queue',
  approval_delay: 'From the approval queue',
  action_failures: 'From user actions'
}

const ALERT_STATUS_LABEL: Record<string, string> = {
  open: 'Current',
  held: 'Held — lower priority, kept quiet',
  resolved: 'Resolved — pattern no longer seen',
  dismissed: 'Dismissed'
}

function Insights() {
  const [alerts, setAlerts] = useState<InsightAlert[]>([])
  const [scannedAt, setScannedAt] = useState<string | null>(null)
  const [baselines, setBaselines] = useState<BaselineRow[]>([])
  const [disclaimer, setDisclaimer] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const a = await api.get<{ items: InsightAlert[]; scanned_at?: string }>(
        '/api/insights/alerts'
      )
      const b = await api.get<{ items: BaselineRow[]; disclaimer?: string }>(
        '/api/insights/baselines'
      )
      setAlerts(a.items)
      setScannedAt(a.scanned_at ?? null)
      setBaselines(b.items)
      setDisclaimer(
        b.disclaimer || 'Directional before/after only. No guaranteed ROI.'
      )
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load insights')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function dismiss(alertId: string) {
    setBusy(true)
    try {
      await api.post('/api/insights/alerts/dismiss', { alert_id: alertId })
      await load()
      setNote('Alert dismissed. It will stay quiet for 30 days.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Dismiss failed')
    } finally {
      setBusy(false)
    }
  }

  async function scan() {
    setBusy(true)
    setNote(null)
    try {
      await api.post('/api/insights/alerts/scan')
      await load()
      setNote('Scanned this workspace for recurring waste.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Scan failed')
    } finally {
      setBusy(false)
    }
  }

  function updateRow(index: number, patch: Partial<BaselineRow>) {
    setBaselines((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row))
    )
  }

  async function saveBaselines() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const result = await api.put<{
        items: BaselineRow[]
        disclaimer?: string
      }>(
        '/api/insights/baselines',
        // The server keeps only the input fields; the computed comparison is ignored.
        { items: baselines }
      )
      setBaselines(result.items)
      setDisclaimer(result.disclaimer || disclaimer)
      setNote('Baselines saved. Directional only — no ROI guarantee.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Panel
        title="Inefficiency alerts (FB-041)"
        description="Recurring, material waste detected in this workspace's own data. At most two are current. Directional $ only — not an always-on engine."
        actions={
          <Btn
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() => void scan()}
          >
            {busy ? 'Scanning…' : 'Scan now'}
          </Btn>
        }
      >
        {error && <ErrorNote>{error}</ErrorNote>}
        {note && <p className="mb-3 text-xs text-positive">{note}</p>}
        {scannedAt && (
          <p className="mb-3 text-[11px] text-muted">
            Last scanned {new Date(scannedAt).toLocaleString()}
          </p>
        )}
        <div className="space-y-3">
          {alerts.length === 0 && (
            <p className="text-xs text-muted">
              No recurring waste found in this workspace&apos;s data.
            </p>
          )}
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className="rounded-lg border border-border bg-background-elevated px-3 py-2.5"
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="font-geist text-sm text-primary">
                    {alert.title}
                  </p>
                  <p className="mt-1 text-xs text-muted">{alert.detail}</p>
                  {alert.directional_cost && (
                    <p className="mt-1 text-[11px] text-brand-soft">
                      {alert.directional_cost}
                    </p>
                  )}
                  <p className="mt-1 text-[10px] uppercase text-muted/70">
                    {ALERT_STATUS_LABEL[alert.status || 'open'] ?? alert.status}
                    {' · '}
                    {ALERT_KIND_LABEL[alert.kind || ''] ?? 'Detected from your data'}
                    {alert.access_area
                      ? ` · visible to ${alert.access_area} area`
                      : ''}
                  </p>
                </div>
                {(alert.status === 'open' ||
                  alert.status === 'held' ||
                  !alert.status) && (
                  <Btn
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => void dismiss(alert.id)}
                  >
                    Dismiss
                  </Btn>
                )}
              </div>
            </div>
          ))}
        </div>
      </Panel>

      <Panel
        title="Baseline vs after (FB-050)"
        description={
          disclaimer ||
          'Directional before/after for one sold workflow. No guaranteed ROI.'
        }
      >
        <div className="space-y-4">
          {baselines.length === 0 && (
            <p className="text-xs text-muted">
              No metrics recorded yet. Add the workflow&apos;s baseline before
              go-live, then the after value once it is measured.
            </p>
          )}
          {baselines.map((row, index) => (
            <div
              key={row.id}
              className="space-y-2 rounded-lg border border-border p-3"
            >
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Workflow">
                  <input
                    className={inputClass}
                    value={row.workflow}
                    onChange={(e) =>
                      updateRow(index, { workflow: e.target.value })
                    }
                  />
                </Field>
                <Field label="Metric">
                  <input
                    className={inputClass}
                    value={row.metric}
                    onChange={(e) =>
                      updateRow(index, { metric: e.target.value })
                    }
                  />
                </Field>
              </div>
              <div className="grid gap-3 md:grid-cols-4">
                <Field label="Before (number)">
                  <input
                    className={inputClass}
                    inputMode="decimal"
                    value={row.before_value}
                    onChange={(e) =>
                      updateRow(index, { before_value: e.target.value })
                    }
                  />
                </Field>
                <Field label="After (number)">
                  <input
                    className={inputClass}
                    inputMode="decimal"
                    value={row.after_value}
                    onChange={(e) =>
                      updateRow(index, { after_value: e.target.value })
                    }
                  />
                </Field>
                <Field label="Unit">
                  <input
                    className={inputClass}
                    value={row.unit}
                    onChange={(e) => updateRow(index, { unit: e.target.value })}
                  />
                </Field>
                <Field label="Change">
                  <input
                    className={inputClass}
                    value={row.comparison ?? 'Save to compare'}
                    readOnly
                    aria-readonly
                  />
                </Field>
              </div>
              <Field label="Notes">
                <textarea
                  className={`${inputClass} min-h-[48px]`}
                  value={row.notes}
                  onChange={(e) => updateRow(index, { notes: e.target.value })}
                />
              </Field>
              <Btn
                variant="ghost"
                size="sm"
                onClick={() =>
                  setBaselines((rows) => rows.filter((_, i) => i !== index))
                }
              >
                Remove metric
              </Btn>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Btn
              variant="secondary"
              size="sm"
              onClick={() =>
                setBaselines((rows) => [
                  ...rows,
                  {
                    id: `baseline_${Date.now()}`,
                    workflow: '',
                    metric: '',
                    before_value: '',
                    after_value: '',
                    unit: '',
                    notes: ''
                  }
                ])
              }
            >
              Add metric
            </Btn>
            <Btn disabled={busy} onClick={() => void saveBaselines()} size="sm">
              {busy ? 'Saving…' : 'Save baselines'}
            </Btn>
          </div>
        </div>
      </Panel>
    </>
  )
}

/** Display-only: show acme.com instead of acme.example.com on this screen. Login emails stay unchanged. */
function displayEmail(email: string) {
  return email.replace(/@([a-z0-9-]+)\.example\.com$/i, '@$1.com')
}

function TagChip({
  on,
  label,
  onClick
}: {
  on: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`inline-flex min-h-7 items-center gap-1 rounded-full border px-2.5 text-[11px] font-medium ui-transition ${
        on
          ? 'border-brand bg-brand/15 text-primary'
          : 'border-border text-muted hover:border-brand/50 hover:text-secondary'
      }`}
    >
      {on && <Check size={11} aria-hidden />}
      {label}
    </button>
  )
}

function AccessRequests({ onDecided }: { onDecided: () => Promise<void> }) {
  const [items, setItems] = useState<AccessRequest[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: AccessRequest[] }>(
        '/api/access/requests'
      )
      setItems(data.items)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not load access requests'
      )
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function decide(id: string, grant: boolean) {
    setBusy(id)
    setError(null)
    try {
      await api.post(`/api/access/requests/${id}/decide`, { grant })
      await Promise.all([load(), onDecided()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save decision')
    } finally {
      setBusy(null)
    }
  }

  if (items.length === 0 && !error) return null

  return (
    <Panel
      title="Access requests"
      description="People asking for more data. Granting adds these tags to their account."
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <ul className="divide-y divide-border">
        {items.map((item) => (
          <li
            key={item.id}
            className="flex flex-wrap items-center gap-x-4 gap-y-3 py-3 first:pt-0 last:pb-0"
          >
            <div className="min-w-0 flex-1 basis-64">
              <p className="text-sm font-semibold text-primary">
                {item.display_name}
                <span className="ml-2 font-normal text-muted">
                  {displayEmail(item.email)}
                </span>
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {item.areas.map((area) => (
                  <Badge key={area} tone="info">
                    {tagLabel(area)}
                  </Badge>
                ))}
              </div>
              {item.reason && (
                <p className="mt-1.5 text-xs text-muted">{item.reason}</p>
              )}
            </div>
            <div className="flex gap-2">
              <Btn
                variant="secondary"
                size="sm"
                disabled={busy !== null}
                onClick={() => void decide(item.id, false)}
              >
                Decline
              </Btn>
              <Btn
                size="sm"
                loading={busy === item.id}
                disabled={busy !== null}
                onClick={() => void decide(item.id, true)}
              >
                Grant
              </Btn>
            </div>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

const selectClass =
  'min-h-9 rounded-lg border border-border bg-background-secondary px-2.5 text-[13px] text-primary'

/** A data access tag on or off. HR also brings salary data. */
function withTag(scopes: string[], tag: string, on: boolean): string[] {
  const out = new Set(scopes)
  const group = tag === 'hr' ? ['hr', 'salary'] : [tag]
  for (const t of group) {
    if (on) out.add(t)
    else out.delete(t)
  }
  return Array.from(out)
}

/** The data access chips: the departments a person works in. CRM, Gmail and Calendar come with every role. */
function DataAccessChips({
  scopes,
  onToggle
}: {
  scopes: string[]
  onToggle: (tag: string, on: boolean) => void
}) {
  return (
    <div className="flex max-w-md flex-wrap gap-1.5">
      {DEPARTMENTS.map((tag) => {
        const on = scopes.includes(tag.value)
        return (
          <TagChip
            key={tag.value}
            label={tag.label}
            on={on}
            onClick={() => onToggle(tag.value, !on)}
          />
        )
      })}
    </div>
  )
}

/** The Role dropdown's choices: the four roles, then Admin. */
function RoleOptions() {
  return (
    <>
      {DEPARTMENT_MODES.map((m) => (
        <option key={m.value} value={m.value} title={m.meaning}>
          {m.label}
        </option>
      ))}
      <option disabled>──────────</option>
      <option value="admin">Admin (every department, full access)</option>
    </>
  )
}

function Users() {
  const [users, setUsers] = useState<SessionUser[]>([])
  const empty = {
    email: '',
    password: '',
    display_name: '',
    access_mode: '' as DepartmentMode | 'admin' | '',
    data_scopes: [] as string[]
  }
  const [form, setForm] = useState(empty)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setUsers((await api.get<{ items: SessionUser[] }>('/api/users')).items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load users')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function create(event: React.FormEvent) {
    event.preventDefault()
    try {
      await api.post('/api/users', {
        email: form.email,
        password: form.password,
        display_name: form.display_name,
        data_scopes: form.data_scopes,
        ...(form.access_mode === 'admin'
          ? { job_role: 'ADMIN' }
          : { access_mode: form.access_mode })
      })
      setForm(empty)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create user')
    }
  }

  async function update(path: string, body: unknown) {
    try {
      await api.patch(path, body)
      await load()
    } catch (err) {
      await load()
      setError(err instanceof Error ? err.message : 'Could not change access')
    }
  }

  return (
    <>
      <AccessRequests onDecided={load} />
      <Panel
        title="Users"
        description="Each person has one role for all their work. Advise can look at CRM and Gmail but not change them; Draft only drafts CRM and Gmail changes; Approve makes changes directly and approves other people's drafts; Auto makes changes directly. Every role can use CRM, Gmail and Calendar. Workspace rules still apply on top. Data access is the departments they work in (pick several). Admin has every department with full access."
      >
        {error && <ErrorNote>{error}</ErrorNote>}
        <DataTable
          rows={users}
          empty="No users."
          columns={[
            { key: 'e', header: 'Email', render: (u) => displayEmail(u.email) },
            { key: 'n', header: 'Name', render: (u) => u.display_name },
            {
              key: 'r',
              header: 'Role',
              render: (u) => {
                const isAdmin = u.roles.includes('ADMIN')
                const value = isAdmin ? 'admin' : (u.access_mode ?? '')
                return (
                  <div className="flex max-w-[14rem] flex-col gap-1">
                    <select
                      aria-label={`Role for ${u.display_name}`}
                      value={value}
                      onChange={(event) => {
                        const next = event.target.value
                        if (next === 'admin') {
                          if (
                            window.confirm(
                              `Make ${u.display_name} an admin with full access to every department?`
                            )
                          )
                            void update(`/api/users/${u.id}/admin`, { admin: true })
                          return
                        }
                        void update(
                          isAdmin ? `/api/users/${u.id}/admin` : `/api/users/${u.id}/access-mode`,
                          isAdmin ? { admin: false, mode: next } : { mode: next }
                        )
                      }}
                      className={selectClass}
                    >
                      {!value && (
                        <option value="" disabled>
                          Choose role
                        </option>
                      )}
                      <RoleOptions />
                    </select>
                    <span className="text-[11px] leading-4 text-muted">
                      {isAdmin ? 'Every department, full access.' : modeMeaning(value)}
                    </span>
                  </div>
                )
              }
            },
            {
              key: 'tags',
              header: 'Data access',
              render: (u) =>
                u.roles.includes('ADMIN') ? (
                  <span className="text-xs text-muted">All departments</span>
                ) : (
                  <DataAccessChips
                    scopes={u.data_scopes ?? []}
                    onToggle={(tag, on) =>
                      void update(`/api/users/${u.id}/scopes`, {
                        data_scopes: withTag(u.data_scopes ?? [], tag, on)
                      })
                    }
                  />
                )
            }
          ]}
        />
      </Panel>

      <Panel title="Add user">
        <form onSubmit={create} className="grid gap-3">
          <div className="grid gap-2 md:grid-cols-4">
            <Field label="Email">
              <input
                className={inputClass}
                type="email"
                required
                value={form.email}
                onChange={(event) => setForm({ ...form, email: event.target.value })}
              />
            </Field>
            <Field label="Name">
              <input
                className={inputClass}
                value={form.display_name}
                onChange={(event) =>
                  setForm({ ...form, display_name: event.target.value })
                }
              />
            </Field>
            <Field label="Password" hint="Minimum 12 characters.">
              <input
                className={inputClass}
                type="password"
                minLength={12}
                required
                value={form.password}
                onChange={(event) => setForm({ ...form, password: event.target.value })}
              />
            </Field>
            <Field
              label="Role"
              hint={
                form.access_mode === 'admin'
                  ? 'Every department, full access.'
                  : form.access_mode
                    ? modeMeaning(form.access_mode)
                    : 'Applies to all their work.'
              }
            >
              <select
                className={inputClass}
                required
                value={form.access_mode}
                onChange={(event) =>
                  setForm({
                    ...form,
                    access_mode: event.target.value as DepartmentMode | 'admin'
                  })
                }
              >
                <option value="" disabled>
                  Choose role
                </option>
                <RoleOptions />
              </select>
            </Field>
          </div>
          {form.access_mode !== 'admin' && (
            <Field label="Data access" hint="The departments they work in. CRM, Gmail and Calendar come with every role.">
              <DataAccessChips
                scopes={form.data_scopes}
                onToggle={(tag, on) =>
                  setForm({ ...form, data_scopes: withTag(form.data_scopes, tag, on) })
                }
              />
            </Field>
          )}
          <div>
            <Btn type="submit" size="sm">
              Create
            </Btn>
          </div>
        </form>
      </Panel>
    </>
  )
}

const REGULATED = [
  {
    key: 'phi',
    icon: HeartPulse,
    title: 'Block PHI / medical',
    description: 'Patient records, diagnoses and prescriptions.'
  },
  {
    key: 'card',
    icon: CreditCard,
    title: 'Block payment card data',
    description: 'Card numbers and CVV codes.'
  },
  {
    key: 'child',
    icon: Baby,
    title: "Block children's personal data",
    description: 'Names, ages and schools of anyone under 18.'
  },
  {
    key: 'biometrics',
    icon: Fingerprint,
    title: 'Block biometrics',
    description: 'Fingerprints and face, voice or iris scans.'
  },
  {
    key: 'ssn',
    icon: IdCard,
    title: 'Block Social Security numbers',
    description: 'SSNs in chat, attachments, memory and uploaded documents.'
  },
  {
    key: 'licensed_advice',
    icon: Scale,
    title: 'Block licensed advice',
    description:
      'Legal, tax, medical and investment advice. Company records can still be looked up.'
  }
]

function PolicyGroup({
  title,
  description,
  aside,
  children
}: {
  title: string
  description: string
  aside?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="border-t border-border pt-7 first:border-t-0 first:pt-0">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-prose">
          <h3 className="font-geist text-[15px] font-semibold text-primary">
            {title}
          </h3>
          <p className="mt-1 text-[13px] leading-relaxed text-muted">
            {description}
          </p>
        </div>
        {aside}
      </div>
      {children}
    </section>
  )
}

function PolicyToggle({
  icon: Icon,
  title,
  description,
  on,
  onChange,
  disabled,
  status,
  tone = 'neutral'
}: {
  icon: LucideIcon
  title: string
  description: string
  on: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
  status?: string
  tone?: Tone
}) {
  return (
    <div
      className={`flex items-start gap-3.5 rounded-xl border p-4 ui-transition ${
        on
          ? 'border-brand/35 bg-brand/5'
          : 'border-border bg-background-elevated/40'
      }`}
    >
      <span
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
          on ? 'bg-brand/15 text-brand' : 'bg-background-elevated text-muted'
        }`}
        aria-hidden
      >
        <Icon size={19} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-primary">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-muted">
          {description}
        </p>
        {status && (
          <Badge tone={tone} className="mt-2.5">
            {status}
          </Badge>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={title}
        disabled={disabled}
        onClick={() => onChange(!on)}
        className={`relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center rounded-full ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/70 disabled:opacity-60 ${
          on ? 'bg-brand' : 'bg-border'
        }`}
      >
        <span
          className={`inline-block h-[18px] w-[18px] rounded-full bg-white shadow-sm transition-transform ${
            on ? 'translate-x-[23px]' : 'translate-x-[3px]'
          }`}
        />
      </button>
    </div>
  )
}

function Policies() {
  const [data, setData] = useState<PoliciesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    try {
      setData(await api.get<PoliciesResponse>('/api/policies'))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load policies')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (!data)
    return <Panel>{error ? <ErrorNote>{error}</ErrorNote> : 'Loading…'}</Panel>

  const settings = data.settings
  const risk = settings.risk_policy
  const allowed = new Set(settings.order_dpa_allows ?? [])
  const blockedCount = REGULATED.filter(({ key }) => !allowed.has(key)).length

  async function save(body: Record<string, unknown>) {
    setSaving(true)
    try {
      await api.put('/api/policies/settings', body)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title="Policies"
      description="Tenant configuration. Values are clamped server-side to the platform floor — CRITICAL actions always require a human, whatever is entered here."
      actions={
        saving ? <span className="text-xs text-muted">Saving…</span> : undefined
      }
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="space-y-8">
        <PolicyGroup
          title="How the assistant acts"
          description="What it may do on its own, and when it must wait for a person."
        >
          <div className="grid gap-4 md:grid-cols-3">
        <Field label="Default execution mode">
          <select
            className={inputClass}
            value={settings.default_execution_mode}
            onChange={(event) =>
              void save({ default_execution_mode: event.target.value })
            }
          >
            {MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Auto ceiling (risk)">
          <select
            className={inputClass}
            value={risk.auto_allowed_max_risk}
            onChange={(event) =>
              void save({
                risk_policy: { auto_allowed_max_risk: event.target.value }
              })
            }
          >
            {['LOW', 'MEDIUM', 'HIGH'].map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Always approve at or above">
          <select
            className={inputClass}
            value={risk.always_approve_at_or_above}
            onChange={(event) =>
              void save({
                risk_policy: { always_approve_at_or_above: event.target.value }
              })
            }
          >
            {['MEDIUM', 'HIGH', 'CRITICAL'].map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </Field>
          </div>
        </PolicyGroup>

        <PolicyGroup
          title="Regulated data"
          description="Blocked items are refused before they reach the AI model. Turn a block off only when the signed Order/DPA allows it."
          aside={
            <Badge tone={blockedCount === REGULATED.length ? 'good' : 'warn'}>
              {blockedCount} of {REGULATED.length} blocked
            </Badge>
          }
        >
          <div className="grid gap-3 md:grid-cols-2">
            {REGULATED.map(({ key, ...item }) => {
              const blocked = !allowed.has(key)
              const heldByBaa =
                key === 'phi' && !blocked && !settings.llm_baa_covered
              return (
                <PolicyToggle
                  key={key}
                  {...item}
                  on={blocked}
                  disabled={saving}
                  status={
                    heldByBaa ? 'Needs BAA' : blocked ? 'Blocked' : 'Allowed'
                  }
                  tone={heldByBaa ? 'info' : blocked ? 'good' : 'warn'}
                  onChange={(block) => {
                    const next = new Set(allowed)
                    if (block) next.delete(key)
                    else next.add(key)
                    void save({ order_dpa_allows: Array.from(next) })
                  }}
                />
              )
            })}
            <div className="md:col-span-2">
              <PolicyToggle
                icon={FileCheck2}
                title="Model vendor has signed a BAA for this client"
                description={`Model vendor: ${settings.llm_vendor || 'openai'} (set by the deployment). PHI also needs a BAA with this vendor, so it stays blocked without one.`}
                on={Boolean(settings.llm_baa_covered)}
                disabled={saving}
                status={settings.llm_baa_covered ? 'BAA in place' : 'No BAA'}
                tone={settings.llm_baa_covered ? 'good' : 'neutral'}
                onChange={(covered) => void save({ llm_baa_covered: covered })}
              />
              {allowed.has('phi') && !settings.llm_baa_covered && (
                <p className="mt-2 rounded-lg border border-warning/35 bg-warning/10 px-3.5 py-2.5 text-xs text-secondary">
                  PHI is unblocked here but stays blocked until a BAA is in
                  place
                </p>
              )}
            </div>
          </div>
        </PolicyGroup>

        <PolicyGroup
          title="Approvals"
          description="Who can get an action through without a second person. Admins always run without waiting; refusals, Advise and Draft modes, data blocks and licensed decisions still apply."
        >
          <div className="grid gap-3 md:grid-cols-2">
            <PolicyToggle
              icon={UserCheck}
              title="Allow self-approval"
              description="People can approve actions they asked for themselves."
              on={Boolean(settings.approval_policy?.allow_self_approval)}
              disabled={saving}
              onChange={(on) =>
                void save({ approval_policy: { allow_self_approval: on } })
              }
            />
          </div>
        </PolicyGroup>

        <PolicyGroup
          title="Limits"
          description="Caps on a single action or run."
        >
          <div className="grid gap-4 md:grid-cols-3">
        <Field label="Max records per action">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_records_per_action}
            onBlur={(event) =>
              void save({
                risk_policy: {
                  max_records_per_action: Number(event.target.value)
                }
              })
            }
          />
        </Field>
        <Field label="Max financial amount">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_financial_amount}
            onBlur={(event) =>
              void save({
                risk_policy: {
                  max_financial_amount: Number(event.target.value)
                }
              })
            }
          />
        </Field>
        <Field label="Max emails per run">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_emails_per_run}
            onBlur={(event) =>
              void save({
                risk_policy: { max_emails_per_run: Number(event.target.value) }
              })
            }
          />
        </Field>
        <Field
          label="Approved recipient domains"
          hint="Comma separated. Empty means no restriction."
        >
          <input
            className={inputClass}
            defaultValue={risk.approved_recipient_domains.join(', ')}
            onBlur={(event) =>
              void save({
                risk_policy: {
                  approved_recipient_domains: event.target.value
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean)
                }
              })
            }
          />
        </Field>
        <Field label="Auto-updatable CRM fields" hint="Comma separated.">
          <input
            className={inputClass}
            defaultValue={risk.auto_updatable_crm_fields.join(', ')}
            onBlur={(event) =>
              void save({
                risk_policy: {
                  auto_updatable_crm_fields: event.target.value
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean)
                }
              })
            }
          />
        </Field>
          </div>
        </PolicyGroup>
      </div>
    </Panel>
  )
}

interface LaunchItem {
  id: string
  title: string
  detail: string
  status: string
}

function LaunchChecklist() {
  const [items, setItems] = useState<LaunchItem[]>([])
  const [ready, setReady] = useState(false)
  const [note, setNote] = useState('')
  const [signedOff, setSignedOff] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.get<{
        items: LaunchItem[]
        ready: boolean
        note?: string
        signed_off_at?: string | null
      }>('/api/launch/checklist')
      setItems(data.items)
      setReady(data.ready)
      setNote(data.note || '')
      setSignedOff(data.signed_off_at ?? null)
      setError(null)
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not load launch checklist'
      )
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function mark(itemId: string, status: string) {
    setBusy(true)
    try {
      const data = await api.post<{
        items: LaunchItem[]
        ready: boolean
        signed_off_at?: string | null
      }>('/api/launch/checklist/items', { item_id: itemId, status })
      setItems(data.items)
      setReady(data.ready)
      setSignedOff(data.signed_off_at ?? null)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update item')
    } finally {
      setBusy(false)
    }
  }

  async function signOff() {
    setBusy(true)
    try {
      const data = await api.post<{
        items: LaunchItem[]
        ready: boolean
        signed_off_at?: string | null
      }>('/api/launch/sign-off', { force: false })
      setItems(data.items)
      setReady(data.ready)
      setSignedOff(data.signed_off_at ?? null)
      setError(null)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Sign-off blocked — finish pending items'
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="First-client launch (FB-045)"
      description="Automated isolation tests pass in-repo. Sign off after the real deployment checklist."
    >
      <div className="space-y-3">
        {note && <p className="text-xs text-muted">{note}</p>}
        {signedOff && (
          <p className="text-xs text-positive">Signed off at {signedOff}</p>
        )}
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-border/60 px-3 py-2"
            >
              <div>
                <p className="text-sm text-primary">{item.title}</p>
                <p className="text-xs text-muted">{item.detail}</p>
                <p className="mt-1 text-[11px] uppercase tracking-wide text-muted">
                  {item.status}
                </p>
              </div>
              {item.id !== 'sign_off' && item.status === 'pending' && (
                <Btn
                  type="button"
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => void mark(item.id, 'done')}
                >
                  Mark done
                </Btn>
              )}
            </li>
          ))}
        </ul>
        {error && <ErrorNote>{error}</ErrorNote>}
        <Btn
          disabled={busy || !ready || Boolean(signedOff)}
          onClick={() => void signOff()}
          size="sm"
        >
          {signedOff
            ? 'Signed off'
            : ready
              ? 'Sign off launch'
              : 'Finish pending items first'}
        </Btn>
      </div>
    </Panel>
  )
}

function Domains() {
  const [items, setItems] = useState<DomainPack[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setItems((await api.get<{ items: DomainPack[] }>('/api/domains')).items)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function toggle(domain: DomainPack) {
    try {
      await api.patch(`/api/domains/${domain.name}`, {
        enabled: !domain.enabled
      })
      setError(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change domain')
    }
  }

  async function changeOrder(domain: DomainPack) {
    const reference = window.prompt(
      `Change order reference for adding ${domain.title}:`
    )
    if (!reference) return
    try {
      await api.post(`/api/domains/${domain.name}/change-order`, { reference })
      setError(null)
      await load()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not record change order'
      )
    }
  }

  return (
    <Panel
      title="Domain packs"
      description="Only domains in the client's purchased package can be switched on. Anything else is added by recording a change order."
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <DataTable
        rows={items}
        empty="No domains."
        columns={[
          {
            key: 'n',
            header: 'Domain',
            render: (d) => (
              <span className="font-geist text-primary">{d.title}</span>
            )
          },
          {
            key: 'd',
            header: 'Description',
            render: (d) => <span className="text-muted">{d.description}</span>
          },
          {
            key: 'p',
            header: 'Depth',
            render: (d) => (
              <Badge tone={d.depth === 'full' ? 'good' : 'neutral'}>
                {d.depth === 'full' ? `phase ${d.phase}` : 'placeholder'}
              </Badge>
            )
          },
          {
            key: 't',
            header: 'Tools',
            render: (d) => <span className="text-muted">{d.tools.length}</span>
          },
          {
            key: 'm',
            header: 'Mode ceiling',
            render: (d) =>
              d.mode_ceiling ? (
                <Badge tone={modeTone(d.mode_ceiling)}>{d.mode_ceiling}</Badge>
              ) : (
                '—'
              )
          },
          {
            key: 'pu',
            header: 'Package',
            render: (d) =>
              d.purchased ? (
                <Badge tone="good">purchased</Badge>
              ) : (
                <Badge tone="neutral">add-on</Badge>
              )
          },
          {
            key: 'e',
            header: 'Enabled',
            render: (d) =>
              d.purchased ? (
                <button
                  onClick={() => void toggle(d)}
                  disabled={d.name === 'general'}
                  className="rounded border border-border px-2 py-0.5 text-[11px] text-muted disabled:opacity-40"
                >
                  {d.enabled ? 'Disable' : 'Enable'}
                </button>
              ) : (
                <button
                  onClick={() => void changeOrder(d)}
                  className="rounded border border-brand/40 px-2 py-0.5 text-[11px] text-brand-soft"
                >
                  Record change order
                </button>
              )
          }
        ]}
      />
    </Panel>
  )
}

function Tools() {
  const [data, setData] = useState<PoliciesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setData(await api.get<PoliciesResponse>('/api/policies'))
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function setToolMode(tool: string, mode: string) {
    try {
      await api.put('/api/policies/settings', {
        tool_modes: { [tool]: mode || null }
      })
      setError(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not set tool mode')
    }
  }

  if (!data) return <Panel>Loading…</Panel>
  const toolModes = data.settings.tool_modes ?? {}

  return (
    <Panel
      title="Tool permission matrix"
      description="Per role: whether they can see a process (view) and whether they can run it (run). Each tool also carries its own execution mode, which can only be stricter than the workspace mode."
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <DataTable
        rows={data.tools}
        empty="No tools."
        columns={[
          {
            key: 'n',
            header: 'Tool',
            render: (t) => (
              <div>
                <div className="font-dmmono text-[11px] text-primary">
                  {t.name}
                </div>
                <div className="text-[10px] text-muted">{t.title}</div>
              </div>
            )
          },
          { key: 'c', header: 'Category', render: (t) => t.category },
          {
            key: 'r',
            header: 'Risk',
            render: (t) => <Badge tone={riskTone(t.risk)}>{t.risk}</Badge>
          },
          {
            key: 'cl',
            header: 'Class',
            render: (t) => <Badge tone="neutral">{t.classification}</Badge>
          },
          {
            key: 'w',
            header: 'Write',
            render: (t) => (t.write ? 'yes' : 'no')
          },
          {
            key: 'a',
            header: 'Data area',
            render: (t) =>
              t.data_area ? <Badge tone="warn">{t.data_area}</Badge> : '—'
          },
          {
            key: 'mode',
            header: 'Tool mode',
            render: (t) => (
              <select
                value={toolModes[t.name] ?? ''}
                onChange={(event) =>
                  void setToolMode(t.name, event.target.value)
                }
                className="rounded border border-border bg-background px-1 py-0.5 text-[10px]"
              >
                <option value="">workspace</option>
                {MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            )
          },
          {
            key: 'i',
            header: 'Built',
            render: (t) =>
              t.implemented ? (
                <Badge tone="good">yes</Badge>
              ) : (
                <Badge tone="warn">declared</Badge>
              )
          },
          ...ROLES.map((role) => ({
            key: role,
            header: role.slice(0, 4),
            render: (t: ToolPolicy) => (
              <div className="leading-tight">
                <span
                  className={
                    t.effective_permissions[role] === 'DENIED'
                      ? 'text-muted/50'
                      : 'text-primary'
                  }
                >
                  {t.effective_permissions[role]?.replace(
                    'EXECUTE_AFTER_APPROVAL',
                    'EXEC_APPR'
                  ) ?? '—'}
                </span>
                <div className="text-[9px] text-muted">
                  {t.view?.[role] ? 'view' : '—'} /{' '}
                  {t.run?.[role] ? 'run' : 'no run'}
                </div>
              </div>
            )
          }))
        ]}
      />
    </Panel>
  )
}
