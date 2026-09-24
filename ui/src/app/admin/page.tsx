'use client'

/** Admin (§17) — users, roles, policies, domains, tool permission matrix. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import type { DomainPack, Role, SessionUser } from '@/lib/types'
import {
  Badge,
  DataTable,
  ErrorNote,
  Field,
  Panel,
  inputClass,
  modeTone,
  riskTone
} from '@/components/common/primitives'

const ROLES: Role[] = ['VIEWER', 'DRAFTER', 'APPROVER', 'OPERATOR', 'ADMIN']
const MODES = ['ADVISE', 'DRAFT', 'WAIT_FOR_APPROVAL', 'AUTO_WITHIN_SCOPE']
const DATA_AREAS = ['exec', 'hr', 'salary', 'finance', 'legal']

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
  const [tab, setTab] = useState<'setup' | 'users' | 'policies' | 'domains' | 'tools'>('setup')
  return (
    <div className="space-y-4">
      <Panel title="Administration" description="Configure the workspace, users, policies and domain packs.">
        <div className="flex flex-wrap gap-2">
          {(['setup', 'users', 'policies', 'domains', 'tools'] as const).map((name) => (
            <button
              key={name}
              onClick={() => setTab(name)}
              className={`rounded-lg px-3 py-1.5 font-geist text-xs capitalize transition ${
                tab === name ? 'bg-background-secondary text-primary' : 'text-muted hover:text-primary'
              }`}
            >
              {name}
            </button>
          ))}
        </div>
      </Panel>
      {tab === 'setup' && <Setup />}
      {tab === 'users' && <Users />}
      {tab === 'policies' && <Policies />}
      {tab === 'domains' && <Domains />}
      {tab === 'tools' && <Tools />}
    </div>
  )
}

function Setup() {
  const [goals, setGoals] = useState('Grow enterprise pipeline 25%\nCut lead response time to <1 day')
  const [systems, setSystems] = useState('Workspace CRM\nWorkspace Email\nWorkspace Calendar\nn8n')
  const [sopTitle, setSopTitle] = useState('Inbound lead response')
  const [sopBody, setSopBody] = useState(
    'Respond to every inbound lead within one business day. Qualify BANT, then book or nurture.'
  )
  const [rolesNotes, setRolesNotes] = useState('Admin configures; Approver releases external writes; Operator executes.')
  const [mode, setMode] = useState('WAIT_FOR_APPROVAL')
  const [assessment, setAssessment] = useState('{"pain":"slow lead response","priority":"intake automation"}')
  const [n8nUrl, setN8nUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<{ profile: {
          goals?: string[]
          systems?: string[]
          autonomy_mode?: string
          roles_notes?: string
          assessment?: Record<string, unknown>
        } }>('/api/onboarding')
        const p = data.profile
        if (p.goals?.length) setGoals(p.goals.join('\n'))
        if (p.systems?.length) setSystems(p.systems.join('\n'))
        if (p.autonomy_mode) setMode(p.autonomy_mode)
        if (p.roles_notes) setRolesNotes(p.roles_notes)
        if (p.assessment && Object.keys(p.assessment).length) {
          setAssessment(JSON.stringify(p.assessment, null, 2))
        }
      } catch {
        // First run — empty profile is fine.
      }
    })()
  }, [])

  async function apply() {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      let assessmentObj: Record<string, unknown> = {}
      try {
        assessmentObj = assessment.trim() ? JSON.parse(assessment) : {}
      } catch {
        throw new Error('Assessment JSON is invalid')
      }
      const result = await api.put<{ memory_written: { kind: string }[] }>('/api/onboarding', {
        goals: goals.split('\n').map((s) => s.trim()).filter(Boolean),
        systems: systems.split('\n').map((s) => s.trim()).filter(Boolean),
        sops: sopTitle.trim() && sopBody.trim()
          ? [{ title: sopTitle.trim(), content: sopBody.trim(), key: 'onboarding_sop' }]
          : [],
        roles_notes: rolesNotes,
        autonomy_mode: mode,
        review_points: [
          'External email send',
          'CRM deletes',
          'Finance adjustments',
          'Legal escalations',
          'n8n webhooks',
        ],
        assessment: assessmentObj
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
      setNote(
        `Configured. Wrote ${result.memory_written?.length ?? 0} memory items. Mode=${mode}.`
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="First-client setup"
      description="Load goals, systems, SOPs and autonomy from the sale/assessment (FB-044)."
    >
      <div className="space-y-4">
        <Field label="Goals (one per line)">
          <textarea value={goals} onChange={(e) => setGoals(e.target.value)} className={`${inputClass} min-h-[80px]`} />
        </Field>
        <Field label="Systems in scope (one per line)">
          <textarea value={systems} onChange={(e) => setSystems(e.target.value)} className={`${inputClass} min-h-[80px]`} />
        </Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="SOP title">
            <input value={sopTitle} onChange={(e) => setSopTitle(e.target.value)} className={inputClass} />
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
          <textarea value={sopBody} onChange={(e) => setSopBody(e.target.value)} className={`${inputClass} min-h-[80px]`} />
        </Field>
        <Field label="Roles / access notes">
          <textarea value={rolesNotes} onChange={(e) => setRolesNotes(e.target.value)} className={`${inputClass} min-h-[60px]`} />
        </Field>
        <Field label="Assessment JSON (handoff)">
          <textarea value={assessment} onChange={(e) => setAssessment(e.target.value)} className={`${inputClass} min-h-[80px] font-dmmono text-[11px]`} />
        </Field>
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
        <button
          disabled={busy}
          onClick={() => void apply()}
          className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-brand-ink disabled:opacity-50"
        >
          {busy ? 'Applying…' : 'Apply configuration'}
        </button>
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
            <button className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-brand-ink">
              Create
            </button>
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
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={settings.allow_phi}
              onChange={(event) => void save({ allow_phi: event.target.checked })}
            />
            Allow PHI
          </label>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={settings.allow_card_data}
              onChange={(event) => void save({ allow_card_data: event.target.checked })}
            />
            Allow payment card data
          </label>
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
                  className="rounded border border-amber-500/40 px-2 py-0.5 text-[11px] text-amber-300"
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
