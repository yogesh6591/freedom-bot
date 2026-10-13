'use client'

/** Admin (§17) — users, roles, policies, domains, tool permission matrix. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import type { DomainPack, Role, SessionUser } from '@/lib/types'
import {
  Badge,
  Btn,
  Chip,
  DataTable,
  ErrorNote,
  Field,
  PageStack,
  Panel,
  SegmentedTab,
  SegmentedTabs,
  inputClass,
  modeTone,
  riskTone
} from '@/components/common/primitives'

const ROLES: Role[] = ['VIEWER', 'DRAFTER', 'APPROVER', 'OPERATOR', 'ADMIN']
const MODES = ['ADVISE', 'DRAFT', 'WAIT_FOR_APPROVAL', 'AUTO_WITHIN_SCOPE']
const DATA_AREAS = ['exec', 'hr', 'salary', 'finance', 'legal', 'ops']

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
    'setup' | 'insights' | 'launch' | 'pricing' | 'users' | 'policies' | 'domains' | 'tools'
  >('setup')
  return (
    <PageStack>
      <Panel title="Administration" description="Configure the workspace, users, policies and domain packs.">
        <SegmentedTabs aria-label="Admin sections">
          {(
            ['setup', 'insights', 'launch', 'pricing', 'users', 'policies', 'domains', 'tools'] as const
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
      </Panel>
      {tab === 'setup' && <Setup />}
      {tab === 'insights' && <Insights />}
      {tab === 'launch' && <LaunchChecklist />}
      {tab === 'pricing' && (
        <>
          <ResalePricing />
          <ConsultPricing />
        </>
      )}
      {tab === 'users' && <Users />}
      {tab === 'policies' && <Policies />}
      {tab === 'domains' && <Domains />}
      {tab === 'tools' && <Tools />}
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

const lines = (text: string) => text.split('\n').map((s) => s.trim()).filter(Boolean)

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

  const fill = useCallback((p: OnboardingProfileView, { replace }: { replace: boolean }) => {
    if (replace || p.goals?.length) setGoals((p.goals ?? []).join('\n'))
    if (replace || p.systems?.length) setSystems((p.systems ?? []).join('\n'))
    if (p.autonomy_mode) setMode(p.autonomy_mode)
    if (replace || p.roles_notes) setRolesNotes(p.roles_notes ?? '')
    if (replace || p.review_points?.length) setReviewPoints((p.review_points ?? []).join('\n'))
    if (replace || p.exclusions?.length) setExclusions((p.exclusions ?? []).join('\n'))
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
    if (p.handoff_at || p.handoff_source) setHandoffMeta(handoffLabel(p.handoff_source, p.handoff_at))
    setClarify({
      open_questions: p.open_questions ?? [],
      inconsistencies: p.inconsistencies ?? [],
      unverified: p.unverified ?? []
    })
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<{ profile: OnboardingProfileView }>('/api/onboarding')
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
      if (!Object.keys(assessmentObj).length) throw new Error('Paste the assessment JSON first')
      const result = await api.post<{
        profile: OnboardingProfileView
        needs_clarification?: Clarification
        message?: string
      }>('/api/onboarding/handoff', { assessment: assessmentObj })
      fill(result.profile, { replace: true })
      if (result.needs_clarification) setClarify(result.needs_clarification)
      setNote(result.message || 'Assessment handoff saved. Review fields, then Apply configuration.')
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
        sops: sopTitle.trim() && sopBody.trim()
          ? [{ title: sopTitle.trim(), content: sopBody.trim(), key: 'onboarding_sop' }]
          : [],
        roles_notes: rolesNotes,
        autonomy_mode: mode,
        review_points: lines(reviewPoints),
        exclusions: lines(exclusions),
        baselines: baselines.filter((b) => b.workflow.trim() || b.metric.trim()),
        assessment: parseAssessment()
      })
      if (n8nUrl.trim()) {
        await api.post('/api/integrations', {
          category: 'n8n',
          provider: 'webhook',
          display_name: 'n8n Webhook',
          enabled: true,
          status: 'CONNECTED',
          config: { webhook_url: n8nUrl.trim() },
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
    (clarify.open_questions.length || clarify.inconsistencies.length || clarify.unverified.length)

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
            <p className="font-geist font-semibold text-primary">Needs clarification with the client</p>
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
                <p className="type-caption">Client assumptions — not recorded as facts</p>
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
            <select value={mode} onChange={(e) => setMode(e.target.value)} className={inputClass}>
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
            Record before values for sold workflows at setup. After values fill once live.
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
                  onClick={() => setBaselines(baselines.filter((_, i) => i !== index))}
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
        {handoffMeta && (
          <p className="text-xs text-positive">{handoffMeta}</p>
        )}
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
  access_area?: string | null
  last_seen_at?: string
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
      const a = await api.get<{ items: InsightAlert[]; scanned_at?: string }>('/api/insights/alerts')
      const b = await api.get<{ items: BaselineRow[]; disclaimer?: string }>('/api/insights/baselines')
      setAlerts(a.items)
      setScannedAt(a.scanned_at ?? null)
      setBaselines(b.items)
      setDisclaimer(b.disclaimer || 'Directional before/after only. No guaranteed ROI.')
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
    setBaselines((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  async function saveBaselines() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const result = await api.put<{ items: BaselineRow[]; disclaimer?: string }>(
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
          <Btn variant="secondary" size="sm" disabled={busy} onClick={() => void scan()}>
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
                  <p className="font-geist text-sm text-primary">{alert.title}</p>
                  <p className="mt-1 text-xs text-muted">{alert.detail}</p>
                  {alert.directional_cost && (
                    <p className="mt-1 text-[11px] text-brand-soft">{alert.directional_cost}</p>
                  )}
                  <p className="mt-1 text-[10px] uppercase text-muted/70">
                    {ALERT_STATUS_LABEL[alert.status || 'open'] ?? alert.status}
                    {' · '}
                    {alert.source === 'scan' ? 'Detected from your data' : 'Demo example'}
                    {alert.access_area ? ` · visible to ${alert.access_area} area` : ''}
                  </p>
                </div>
                {(alert.status === 'open' || alert.status === 'held' || !alert.status) && (
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
        description={disclaimer || 'Directional before/after for one sold workflow. No guaranteed ROI.'}
      >
        <div className="space-y-4">
          {baselines.length === 0 && (
            <p className="text-xs text-muted">
              No metrics recorded yet. Add the workflow&apos;s baseline before go-live, then the
              after value once it is measured.
            </p>
          )}
          {baselines.map((row, index) => (
            <div key={row.id} className="space-y-2 rounded-lg border border-border p-3">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Workflow">
                  <input
                    className={inputClass}
                    value={row.workflow}
                    onChange={(e) => updateRow(index, { workflow: e.target.value })}
                  />
                </Field>
                <Field label="Metric">
                  <input
                    className={inputClass}
                    value={row.metric}
                    onChange={(e) => updateRow(index, { metric: e.target.value })}
                  />
                </Field>
              </div>
              <div className="grid gap-3 md:grid-cols-4">
                <Field label="Before (number)">
                  <input
                    className={inputClass}
                    inputMode="decimal"
                    value={row.before_value}
                    onChange={(e) => updateRow(index, { before_value: e.target.value })}
                  />
                </Field>
                <Field label="After (number)">
                  <input
                    className={inputClass}
                    inputMode="decimal"
                    value={row.after_value}
                    onChange={(e) => updateRow(index, { after_value: e.target.value })}
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
                onClick={() => setBaselines((rows) => rows.filter((_, i) => i !== index))}
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

function ResalePricing() {
  const [customerPrice, setCustomerPrice] = useState('')
  const [cost, setCost] = useState('')
  const [status, setStatus] = useState('pending_venu_confirm')
  const [notes, setNotes] = useState('')
  const [rules, setRules] = useState<{
    disclaimer?: string
    price_set_by?: string
    confirm_by?: string
    margin_percent?: number
    allow_public_package_price?: boolean
    allow_per_seat_pricing?: boolean
    forbid_legacy_prices?: boolean
    forbidden_legacy_examples?: string[]
    placeholder?: boolean
    configured?: boolean
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.get<{
        rules: {
          customer_price_usd: number | null
          cepoch_cost_usd: number | null
          configured?: boolean
          quote_status: string
          notes: string
          disclaimer?: string
          price_set_by?: string
          confirm_by?: string
          margin_percent?: number
          allow_public_package_price?: boolean
          allow_per_seat_pricing?: boolean
          forbid_legacy_prices?: boolean
          forbidden_legacy_examples?: string[]
          placeholder?: boolean
        }
      }>('/api/pricing/resale-rules')
      const r = data.rules
      setRules(r)
      // No rule stored means no price — never pre-fill a demo number.
      setCustomerPrice(r.customer_price_usd != null ? String(r.customer_price_usd) : '')
      setCost(r.cepoch_cost_usd != null ? String(r.cepoch_cost_usd) : '')
      setStatus(r.configured === false ? 'draft' : r.quote_status || 'pending_venu_confirm')
      setNotes(r.notes || '')
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load resale rules')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function save() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const data = await api.put<{ rules: { margin_percent?: number; disclaimer?: string } }>(
        '/api/pricing/resale-rules',
        {
          customer_price_usd: Number(customerPrice),
          cepoch_cost_usd: Number(cost),
          quote_status: status,
          notes
        }
      )
      setRules((prev) => ({ ...(prev || {}), ...data.rules }))
      setNote(
        `Saved. Margin ${data.rules.margin_percent ?? '—'}%. Placeholder until Jeanne + Venu confirm.`
      )
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setBusy(false)
    }
  }

  const priceN = Number(customerPrice) || 0
  const costN = Number(cost) || 0
  const bothEntered = customerPrice.trim() !== '' && cost.trim() !== ''
  const liveMargin =
    bothEntered && priceN > 0 ? (((priceN - costN) / priceN) * 100).toFixed(1) : '—'

  return (
    <Panel
      title="Resale price rules (FB-048)"
      description="JeanneCAIO sets customer price and margin. Venu confirms. No public package price, no per-seat, no legacy $2K/$75K lists."
    >
      <div className="mb-4 rounded-lg border border-brand/30 bg-brand/10 px-3 py-2 text-xs text-brand-soft">
        {rules?.disclaimer ||
          'Placeholder face values for FB-048. Replace after JeanneCAIO + Venu confirm. Not a public price list.'}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {note && <p className="mb-3 text-xs text-positive">{note}</p>}
      {rules?.configured === false && (
        <p className="mb-3 text-xs text-muted">
          No resale price is set for this client yet. Quotes stay unavailable until you enter one.
        </p>
      )}

      <div className="mb-4 grid gap-2 text-[11px] text-muted md:grid-cols-2">
        <p>
          Price set by: <span className="font-geist text-primary">{rules?.price_set_by || 'JeanneCAIO'}</span>
        </p>
        <p>
          Confirm by: <span className="font-geist text-primary">{rules?.confirm_by || 'Venu'}</span>
        </p>
        <p>Public package price: <strong className="text-primary">forbidden</strong></p>
        <p>Per-seat pricing: <strong className="text-primary">forbidden</strong></p>
        <p className="md:col-span-2">
          Legacy prices blocked:{' '}
          {(rules?.forbidden_legacy_examples || ['$2K', '$75K', 'module package list']).join(' · ')}
        </p>
      </div>

      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Customer price USD (face)">
            <input
              className={inputClass}
              type="number"
              value={customerPrice}
              onChange={(e) => setCustomerPrice(e.target.value)}
            />
          </Field>
          <Field label="Cepoch cost USD (face)">
            <input
              className={inputClass}
              type="number"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
            />
          </Field>
          <Field label="Margin % (auto)">
            <input className={inputClass} value={liveMargin} readOnly />
          </Field>
        </div>
        <Field label="Quote status">
          <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="draft">Draft</option>
            <option value="pending_venu_confirm">Pending Venu confirm</option>
            <option value="venu_confirmed">Venu confirmed</option>
          </select>
        </Field>
        <Field label="Notes">
          <textarea
            className={`${inputClass} min-h-[64px]`}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>
        <Btn disabled={busy || !bothEntered} onClick={() => void save()} size="sm">
          {busy ? 'Saving…' : 'Save face quote'}
        </Btn>
      </div>
    </Panel>
  )
}

const BASE_DOMAINS = ['general', 'operations', 'sales', 'intake', 'planning'] as const
const ADDON_DOMAINS = ['strategy', 'finance', 'brand', 'legal'] as const
const AUTONOMY_MODES = [
  'ADVISE',
  'DRAFT',
  'WAIT_FOR_APPROVAL',
  'AUTO_WITHIN_SCOPE'
] as const

function ConsultPricing() {
  const [addons, setAddons] = useState<string[]>([])
  const [autonomy, setAutonomy] = useState('WAIT_FOR_APPROVAL')
  const [integrations, setIntegrations] = useState('2')
  const [label, setLabel] = useState('Face consult quote')
  const [config, setConfig] = useState<{
    disclaimer?: string
    addon_usd?: Record<string, number>
    autonomy_factor?: Record<string, number>
    integrations_included?: number
    integration_usd?: number
  } | null>(null)
  const [quote, setQuote] = useState<{
    customer_price_usd: number
    cepoch_cost_usd: number
    margin_percent: number
    subtotal_usd: number
    disclaimer?: string
    lines?: { domain: string; customer_usd: number; kind: string }[]
    inputs?: { autonomy_factor?: number; extra_integrations?: number }
  } | null>(null)
  const [savedId, setSavedId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ config: typeof config }>('/api/pricing/consult-config')
      setConfig(data.config)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load consult config')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function toggleAddon(name: string) {
    setAddons((prev) =>
      prev.includes(name) ? prev.filter((d) => d !== name) : [...prev, name]
    )
  }

  async function run(save: boolean) {
    setBusy(true)
    setError(null)
    setNote(null)
    setSavedId(null)
    try {
      const data = await api.post<{
        quote: NonNullable<typeof quote>
        saved?: { id: string } | null
      }>('/api/pricing/consult-quote', {
        domains: [...BASE_DOMAINS, ...addons],
        autonomy,
        integrations: Number(integrations) || 0,
        label,
        save
      })
      setQuote(data.quote)
      if (data.saved?.id) {
        setSavedId(data.saved.id)
        setNote(`Saved consult quote ${data.saved.id}. Replace face prices later in Resale rules.`)
      } else {
        setNote('Calculated from face FB-048 rules + face scope weights.')
      }
    } catch (err) {
      setQuote(null)
      setError(err instanceof Error ? err.message : 'Consult quote failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Consult pricing math (FB-049)"
      description="Pick scope on a call — price/cost/margin calculate from face FB-048 rules. Swap Admin → Pricing numbers after Jeanne + Venu confirm; no rebuild needed."
    >
      <div className="mb-4 rounded-xl border border-brand/30 bg-brand/10 px-4 py-3 text-xs text-brand-soft">
        {config?.disclaimer ||
          'FB-049 face scope weights. Replace after JeanneCAIO + Venu confirm commercials.'}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}
      {note && <p className="mb-3 text-xs text-positive">{note}</p>}

      <div className="space-y-4">
        <div>
          <p className="type-caption mb-2">Base package (included in face base price)</p>
          <div className="flex flex-wrap gap-2">
            {BASE_DOMAINS.map((d) => (
              <Badge key={d} tone="neutral">
                {d}
              </Badge>
            ))}
          </div>
        </div>

        <div>
          <p className="type-caption mb-2">Add-on domains (face USD)</p>
          <div className="flex flex-wrap gap-2">
            {ADDON_DOMAINS.map((d) => {
              const active = addons.includes(d)
              const usd = config?.addon_usd?.[d]
              return (
                <Chip key={d} active={active} onClick={() => toggleAddon(d)}>
                  {d}
                  {usd != null ? ` · $${usd}` : ''}
                </Chip>
              )
            })}
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <Field label="Autonomy">
            <select
              className={inputClass}
              value={autonomy}
              onChange={(e) => setAutonomy(e.target.value)}
            >
              {AUTONOMY_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                  {config?.autonomy_factor?.[m] != null
                    ? ` (×${config.autonomy_factor[m]})`
                    : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Integrations"
            hint={`First ${config?.integrations_included ?? 2} included; then +$${config?.integration_usd ?? 250} each (face).`}
          >
            <input
              className={inputClass}
              type="number"
              min={0}
              max={50}
              value={integrations}
              onChange={(e) => setIntegrations(e.target.value)}
            />
          </Field>
          <Field label="Quote label (when saving)">
            <input
              className={inputClass}
              value={label}
              onChange={(e) => setLabel(e.target.value)}
            />
          </Field>
        </div>

        <div className="flex flex-wrap gap-2">
          <Btn size="sm" disabled={busy} onClick={() => void run(false)}>
            {busy ? 'Calculating…' : 'Calculate quote'}
          </Btn>
          <Btn size="sm" variant="secondary" disabled={busy} onClick={() => void run(true)}>
            Calculate & save
          </Btn>
        </div>

        {quote && (
          <div className="space-y-3 rounded-xl border border-border bg-background-elevated px-4 py-4">
            <p className="text-xs text-muted">{quote.disclaimer}</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <p className="type-caption">Customer price</p>
                <p className="font-geist text-lg font-semibold text-primary">
                  ${quote.customer_price_usd.toLocaleString()}
                </p>
              </div>
              <div>
                <p className="type-caption">Cepoch cost</p>
                <p className="font-geist text-lg font-semibold text-primary">
                  ${quote.cepoch_cost_usd.toLocaleString()}
                </p>
              </div>
              <div>
                <p className="type-caption">Margin</p>
                <p className="font-geist text-lg font-semibold text-primary">
                  {quote.margin_percent}%
                </p>
              </div>
            </div>
            <ul className="space-y-1 text-xs text-muted">
              {(quote.lines || []).map((line) => (
                <li key={`${line.kind}-${line.domain}`}>
                  {line.kind}: {line.domain} — ${line.customer_usd.toLocaleString()}
                </li>
              ))}
              <li>
                Subtotal ${quote.subtotal_usd.toLocaleString()}
                {quote.inputs?.autonomy_factor != null
                  ? ` × autonomy ${quote.inputs.autonomy_factor}`
                  : ''}
              </li>
            </ul>
            {savedId && (
              <p className="text-xs text-positive">Saved as {savedId}</p>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}

function Users() {
  const [users, setUsers] = useState<SessionUser[]>([])
  const [form, setForm] = useState({ email: '', password: '', display_name: '', role: 'VIEWER' })
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
        roles: [form.role]
      })
      setForm({ email: '', password: '', display_name: '', role: 'VIEWER' })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create user')
    }
  }

  async function setRole(userId: string, role: string) {
    await api.patch(`/api/users/${userId}/roles`, { roles: [role] })
    await load()
  }

  async function toggleScope(user: SessionUser, area: string) {
    const current = new Set(user.data_scopes ?? [])
    if (current.has(area)) current.delete(area)
    else current.add(area)
    try {
      await api.patch(`/api/users/${user.id}/scopes`, { data_scopes: Array.from(current) })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change access')
    }
  }

  return (
    <>
      <Panel title="Users">
        {error && <ErrorNote>{error}</ErrorNote>}
        <DataTable
          rows={users}
          empty="No users."
          columns={[
            { key: 'e', header: 'Email', render: (u) => u.email },
            { key: 'n', header: 'Name', render: (u) => u.display_name },
            {
              key: 'r',
              header: 'Roles',
              render: (u) => (
                <div className="flex gap-1">
                  {u.roles.map((role) => (
                    <Badge key={role} tone="neutral">
                      {role}
                    </Badge>
                  ))}
                </div>
              )
            },
            {
              key: 'set',
              header: 'Change role',
              render: (u) => (
                <select
                  defaultValue={u.primary_role}
                  onChange={(event) => void setRole(u.id, event.target.value)}
                  className="rounded border border-border bg-background px-2 py-1 text-[11px]"
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
              )
            },
            {
              key: 'scopes',
              header: 'Restricted data access',
              render: (u) =>
                u.roles.includes('ADMIN') ? (
                  <span className="text-[11px] text-muted">all areas (admin)</span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {DATA_AREAS.map((area) => {
                      const on = (u.data_scopes ?? []).includes(area)
                      return (
                        <button
                          key={area}
                          onClick={() => void toggleScope(u, area)}
                          className={`rounded border px-1.5 py-0.5 text-[10px] ${
                            on ? 'border-brand bg-brand/20 text-primary' : 'border-border text-muted'
                          }`}
                        >
                          {area}
                        </button>
                      )
                    })}
                  </div>
                )
            }
          ]}
        />
      </Panel>

      <Panel title="Add user">
        <form onSubmit={create} className="grid gap-2 md:grid-cols-5">
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
              onChange={(event) => setForm({ ...form, display_name: event.target.value })}
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
          <Field label="Role">
            <select
              className={inputClass}
              value={form.role}
              onChange={(event) => setForm({ ...form, role: event.target.value })}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex items-end">
            <Btn type="submit" size="sm">
              Create
            </Btn>
          </div>
        </form>
      </Panel>
    </>
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

  if (!data) return <Panel>{error ? <ErrorNote>{error}</ErrorNote> : 'Loading…'}</Panel>

  const settings = data.settings
  const risk = settings.risk_policy

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
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Default execution mode">
          <select
            className={inputClass}
            value={settings.default_execution_mode}
            onChange={(event) => void save({ default_execution_mode: event.target.value })}
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
              void save({ risk_policy: { auto_allowed_max_risk: event.target.value } })
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
              void save({ risk_policy: { always_approve_at_or_above: event.target.value } })
            }
          >
            {['MEDIUM', 'HIGH', 'CRITICAL'].map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Max records per action">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_records_per_action}
            onBlur={(event) =>
              void save({ risk_policy: { max_records_per_action: Number(event.target.value) } })
            }
          />
        </Field>
        <Field label="Max financial amount">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_financial_amount}
            onBlur={(event) =>
              void save({ risk_policy: { max_financial_amount: Number(event.target.value) } })
            }
          />
        </Field>
        <Field label="Max emails per run">
          <input
            className={inputClass}
            type="number"
            defaultValue={risk.max_emails_per_run}
            onBlur={(event) =>
              void save({ risk_policy: { max_emails_per_run: Number(event.target.value) } })
            }
          />
        </Field>
        <Field label="Approved recipient domains" hint="Comma separated. Empty means no restriction.">
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
        <div className="space-y-3">
          <div>
            <p className="type-caption mb-1">Order / DPA allows (FB-039)</p>
            <p className="mb-2 text-xs text-muted">
              One setting from the signed Order/DPA — anything unticked is refused. PHI also needs
              a BAA with the model vendor; OpenAI has none, so PHI stays blocked on OpenAI.
            </p>
            {(
              [
                ['phi', 'PHI / medical'],
                ['card', 'Payment card data'],
                ['child', "Children's personal data"],
                ['biometrics', 'Biometrics']
              ] as const
            ).map(([key, label]) => {
              const allowed = new Set(settings.order_dpa_allows ?? [])
              return (
                <label key={key} className="flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={allowed.has(key)}
                    onChange={(event) => {
                      const next = new Set(allowed)
                      if (event.target.checked) next.add(key)
                      else next.delete(key)
                      void save({ order_dpa_allows: Array.from(next) })
                    }}
                  />
                  {label}
                </label>
              )
            })}
            <label className="mt-2 flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={Boolean(settings.llm_baa_covered)}
                onChange={(event) => void save({ llm_baa_covered: event.target.checked })}
              />
              Model vendor has signed a BAA for this client
            </label>
            <p className="mt-1 text-[11px] text-muted">
              Model vendor: {settings.llm_vendor || 'openai'} (set by the deployment)
              {settings.order_dpa_allows?.includes('phi') && !settings.llm_baa_covered
                ? ' · PHI is blocked until a BAA is in place'
                : ''}
            </p>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={Boolean(settings.approval_policy?.allow_self_approval)}
              onChange={(event) =>
                void save({
                  approval_policy: { allow_self_approval: event.target.checked }
                })
              }
            />
            Allow self-approval
          </label>
          {saving && <span className="text-[11px] text-muted">Saving…</span>}
        </div>
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
      setError(err instanceof Error ? err.message : 'Could not load launch checklist')
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
      setError(err instanceof Error ? err.message : 'Sign-off blocked — finish pending items')
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
        {signedOff && <p className="text-xs text-positive">Signed off at {signedOff}</p>}
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-border/60 px-3 py-2"
            >
              <div>
                <p className="text-sm text-primary">{item.title}</p>
                <p className="text-xs text-muted">{item.detail}</p>
                <p className="mt-1 text-[11px] uppercase tracking-wide text-muted">{item.status}</p>
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
        <Btn disabled={busy || !ready || Boolean(signedOff)} onClick={() => void signOff()} size="sm">
          {signedOff ? 'Signed off' : ready ? 'Sign off launch' : 'Finish pending items first'}
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
      await api.patch(`/api/domains/${domain.name}`, { enabled: !domain.enabled })
      setError(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not change domain')
    }
  }

  async function changeOrder(domain: DomainPack) {
    const reference = window.prompt(`Change order reference for adding ${domain.title}:`)
    if (!reference) return
    try {
      await api.post(`/api/domains/${domain.name}/change-order`, { reference })
      setError(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not record change order')
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
          { key: 'n', header: 'Domain', render: (d) => <span className="font-geist text-primary">{d.title}</span> },
          { key: 'd', header: 'Description', render: (d) => <span className="text-muted">{d.description}</span> },
          {
            key: 'p',
            header: 'Depth',
            render: (d) => (
              <Badge tone={d.depth === 'full' ? 'good' : 'neutral'}>
                {d.depth === 'full' ? `phase ${d.phase}` : 'placeholder'}
              </Badge>
            )
          },
          { key: 't', header: 'Tools', render: (d) => <span className="text-muted">{d.tools.length}</span> },
          {
            key: 'm',
            header: 'Mode ceiling',
            render: (d) =>
              d.mode_ceiling ? <Badge tone={modeTone(d.mode_ceiling)}>{d.mode_ceiling}</Badge> : '—'
          },
          {
            key: 'pu',
            header: 'Package',
            render: (d) =>
              d.purchased ? <Badge tone="good">purchased</Badge> : <Badge tone="neutral">add-on</Badge>
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
      await api.put('/api/policies/settings', { tool_modes: { [tool]: mode || null } })
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
                <div className="font-dmmono text-[11px] text-primary">{t.name}</div>
                <div className="text-[10px] text-muted">{t.title}</div>
              </div>
            )
          },
          { key: 'c', header: 'Category', render: (t) => t.category },
          { key: 'r', header: 'Risk', render: (t) => <Badge tone={riskTone(t.risk)}>{t.risk}</Badge> },
          { key: 'cl', header: 'Class', render: (t) => <Badge tone="neutral">{t.classification}</Badge> },
          { key: 'w', header: 'Write', render: (t) => (t.write ? 'yes' : 'no') },
          {
            key: 'a',
            header: 'Data area',
            render: (t) => (t.data_area ? <Badge tone="warn">{t.data_area}</Badge> : '—')
          },
          {
            key: 'mode',
            header: 'Tool mode',
            render: (t) => (
              <select
                value={toolModes[t.name] ?? ''}
                onChange={(event) => void setToolMode(t.name, event.target.value)}
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
            render: (t) => (t.implemented ? <Badge tone="good">yes</Badge> : <Badge tone="warn">declared</Badge>)
          },
          ...ROLES.map((role) => ({
            key: role,
            header: role.slice(0, 4),
            render: (t: ToolPolicy) => (
              <div className="leading-tight">
                <span
                  className={
                    t.effective_permissions[role] === 'DENIED' ? 'text-muted/50' : 'text-primary'
                  }
                >
                  {t.effective_permissions[role]?.replace('EXECUTE_AFTER_APPROVAL', 'EXEC_APPR') ?? '—'}
                </span>
                <div className="text-[9px] text-muted">
                  {t.view?.[role] ? 'view' : '—'} / {t.run?.[role] ? 'run' : 'no run'}
                </div>
              </div>
            )
          }))
        ]}
      />
    </Panel>
  )
}
