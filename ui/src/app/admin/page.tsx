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
  const [tab, setTab] = useState<'users' | 'policies' | 'domains' | 'tools'>('users')
  return (
    <div className="space-y-4">
      <Panel title="Administration" description="Users, roles, policies and domain packs for this workspace.">
        <div className="flex gap-2">
          {(['users', 'policies', 'domains', 'tools'] as const).map((name) => (
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
      {tab === 'users' && <Users />}
      {tab === 'policies' && <Policies />}
      {tab === 'domains' && <Domains />}
      {tab === 'tools' && <Tools />}
    </div>
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
            <button className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-white">
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

  const load = useCallback(async () => {
    setItems((await api.get<{ items: DomainPack[] }>('/api/domains')).items)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function toggle(domain: DomainPack) {
    await api.patch(`/api/domains/${domain.name}`, { enabled: !domain.enabled })
    await load()
  }

  return (
    <Panel title="Domain packs" description="Enable or disable capability domains for this workspace.">
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
            key: 'e',
            header: 'Enabled',
            render: (d) => (
              <button
                onClick={() => void toggle(d)}
                disabled={d.name === 'general'}
                className="rounded border border-border px-2 py-0.5 text-[11px] text-muted disabled:opacity-40"
              >
                {d.enabled ? 'Disable' : 'Enable'}
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

  useEffect(() => {
    void api.get<PoliciesResponse>('/api/policies').then(setData)
  }, [])

  if (!data) return <Panel>Loading…</Panel>

  return (
    <Panel
      title="Tool permission matrix"
      description="The central permission declaration. An override can tighten a permission but never grant more than the tool's registry ceiling."
    >
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
            key: 'i',
            header: 'Built',
            render: (t) => (t.implemented ? <Badge tone="good">yes</Badge> : <Badge tone="warn">declared</Badge>)
          },
          ...ROLES.map((role) => ({
            key: role,
            header: role.slice(0, 4),
            render: (t: ToolPolicy) => (
              <span
                className={
                  t.effective_permissions[role] === 'DENIED' ? 'text-muted/50' : 'text-primary'
                }
              >
                {t.effective_permissions[role]?.replace('EXECUTE_AFTER_APPROVAL', 'EXEC_APPR') ?? '—'}
              </span>
            )
          }))
        ]}
      />
    </Panel>
  )
}
