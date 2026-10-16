'use client'

/** FB-047 — JeanneCAIO exception-only owner view (not a CRM). */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  EmptyState,
  ErrorNote,
  LoadingBlock,
  PageStack,
  PageHeader,
  Panel,
  inputClass,
  timestamp,
  type Tone
} from '@/components/common/primitives'

interface Derived {
  value: string
  derived: string
  source: 'derived' | 'owner'
  reasons: string[]
  set_by?: string | null
  set_at?: string | null
}

interface NeedsMeItem {
  kind: 'escalation' | 'launch_sign_off' | 'pricing'
  id: string
  subject: string
  escalate_reason?: string | null
  severity?: string
  updated_at?: string | null
}

interface PortfolioClient {
  client_id: string
  slug: string
  company_name: string
  purchased_domains: string[]
  implementation_status: string
  implementation: Derived
  account_risk: string
  risk: Derived
  owner_notes: string
  needs_me_count: number
  needs_me: NeedsMeItem[]
  history: { at: string; kind: string; text: string }[]
  revenue: {
    placeholder?: boolean
    customer_price_usd?: number | null
    margin_percent?: number | null
    quote_status?: string | null
  }
}

interface Portfolio {
  disclaimer?: string
  client_count: number
  needs_me_count: number
  at_risk_count: number
  watch_count: number
  live_count: number
  revenue_total_usd: number | null
  revenue_placeholder: boolean
  clients: PortfolioClient[]
  needs_me_queue: (NeedsMeItem & { client_id: string; company_name: string })[]
}

const STATUS_LABEL: Record<string, string> = {
  not_started: 'Not started',
  setup: 'In setup',
  live: 'Live',
  blocked: 'Blocked'
}

const RISK_LABEL: Record<string, string> = {
  healthy: 'Healthy',
  watch: 'Watch',
  at_risk: 'At risk'
}

const NEEDS_LABEL: Record<NeedsMeItem['kind'], string> = {
  escalation: 'Escalation',
  launch_sign_off: 'Launch sign-off',
  pricing: 'Pricing'
}

const HISTORY_LABEL: Record<string, string> = {
  sale: 'Sale',
  setup: 'Setup',
  launch: 'Launch',
  change_order: 'Change order',
  support: 'Support',
  owner_note: 'Note'
}

function riskBadgeTone(risk: string): Tone {
  return risk === 'at_risk' ? 'bad' : risk === 'watch' ? 'warn' : 'good'
}

function statusBadgeTone(status: string): Tone {
  return status === 'blocked' ? 'bad' : status === 'live' ? 'good' : 'info'
}

function money(value?: number | null) {
  return value != null ? `$${value.toLocaleString()}` : 'Not set'
}

export default function OwnerPage() {
  return (
    <AppShell>
      <OwnerView />
    </AppShell>
  )
}

function OwnerView() {
  const [data, setData] = useState<Portfolio | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<Portfolio>('/api/owner/portfolio'))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load owner view')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <PageStack>
      <PageHeader
        eyebrow="Portfolio oversight"
        title="Owner overview"
        description="Only the exceptions: client health, what needs you, and the context before a call."
      />
      <Panel
        title="Portfolio snapshot"
        description="Status and risk are worked out from each client's setup, launch, support tickets and sign-ins."
      >
        {data?.disclaimer && (
          <p className="mb-4 rounded-xl border border-brand/30 bg-brand/10 px-4 py-3 text-xs text-brand-soft">
            {data.disclaimer}
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        {loading && !data && <LoadingBlock rows={3} />}
        {data && (
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="Clients" value={String(data.client_count)} />
            <Stat label="Live" value={String(data.live_count)} />
            <Stat label="Needs me" value={String(data.needs_me_count)} tone="warn" />
            <Stat
              label="At risk / watch"
              value={`${data.at_risk_count} / ${data.watch_count}`}
              tone="bad"
            />
            <Stat
              label={data.revenue_placeholder ? 'Revenue (incl. face quotes)' : 'Revenue'}
              value={money(data.revenue_total_usd)}
            />
          </div>
        )}
      </Panel>

      {data && (
        <Panel
          title="What needs me"
          description="Escalations from Cepoch support, launches waiting on your sign-off, and prices not yet confirmed."
        >
          {data.needs_me_queue.length === 0 ? (
            <EmptyState>Nothing needs you right now.</EmptyState>
          ) : (
            <ul className="space-y-3">
              {data.needs_me_queue.map((item) => (
                <li
                  key={`${item.client_id}-${item.id}`}
                  className="rounded-xl border border-border bg-background-elevated px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={item.kind === 'escalation' ? 'warn' : 'info'}>
                      {item.kind === 'escalation'
                        ? item.escalate_reason || NEEDS_LABEL.escalation
                        : NEEDS_LABEL[item.kind]}
                    </Badge>
                    <Badge tone="neutral">{item.company_name}</Badge>
                    <span className="font-geist text-sm font-semibold text-primary">
                      {item.subject}
                    </span>
                  </div>
                  {item.updated_at && (
                    <p className="mt-1 text-xs text-muted">{timestamp(item.updated_at)}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      {data && (
        <Panel title="Clients" description="At-risk clients first.">
          {data.clients.length === 0 ? (
            <EmptyState>No active clients.</EmptyState>
          ) : (
            <div className="space-y-4">
              {data.clients.map((client) => (
                <ClientCard key={client.client_id} client={client} onSaved={load} />
              ))}
            </div>
          )}
        </Panel>
      )}
    </PageStack>
  )
}

function ClientCard({
  client,
  onSaved
}: {
  client: PortfolioClient
  onSaved: () => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [status, setStatus] = useState('')
  const [risk, setRisk] = useState('')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startEdit = () => {
    setStatus(client.implementation.source === 'owner' ? client.implementation_status : '')
    setRisk(client.risk.source === 'owner' ? client.account_risk : '')
    setNotes(client.owner_notes)
    setError(null)
    setEditing(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.put(`/api/owner/portfolio/${client.client_id}`, {
        implementation_status: status || null,
        account_risk: risk || null,
        owner_notes: notes
      })
      setEditing(false)
      await onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl border border-border bg-background-elevated p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="type-subtitle text-base">{client.company_name}</h3>
        <Badge tone="neutral">{client.slug}</Badge>
        <Badge tone={riskBadgeTone(client.account_risk)}>
          {RISK_LABEL[client.account_risk] ?? client.account_risk}
          {client.risk.source === 'owner' ? ' · set by you' : ''}
        </Badge>
        <Badge tone={statusBadgeTone(client.implementation_status)}>
          {STATUS_LABEL[client.implementation_status] ?? client.implementation_status}
          {client.implementation.source === 'owner' ? ' · set by you' : ''}
        </Badge>
        {client.needs_me_count > 0 && (
          <Badge tone="warn">Needs me: {client.needs_me_count}</Badge>
        )}
        <Btn variant="ghost" size="sm" className="ml-auto" onClick={startEdit}>
          Override / note
        </Btn>
      </div>

      <div className="mt-3 grid gap-3 text-xs text-muted md:grid-cols-2">
        <div>
          <p className="type-caption mb-1">What they bought</p>
          <p>{client.purchased_domains.length ? client.purchased_domains.join(', ') : '—'}</p>
          <p className="type-caption mb-1 mt-3">Revenue (your side)</p>
          <p>
            {money(client.revenue.customer_price_usd)}
            {client.revenue.margin_percent != null
              ? ` · margin ${client.revenue.margin_percent}%`
              : ''}
            {client.revenue.placeholder ? ' · face quote' : ''}
            {client.revenue.quote_status ? ` · ${client.revenue.quote_status}` : ''}
          </p>
        </div>
        <div>
          <p className="type-caption mb-1">Why this status and risk</p>
          <ReasonList derived={client.implementation} labels={STATUS_LABEL} />
          <ReasonList derived={client.risk} labels={RISK_LABEL} empty="No risk signals." />
          {client.owner_notes && (
            <p className="mt-2 text-brand-soft">Your note: {client.owner_notes}</p>
          )}
        </div>
      </div>

      {client.history.length > 0 && (
        <div className="mt-3 border-t border-border pt-3">
          <p className="type-caption mb-2">History before a call</p>
          <ul className="space-y-1 text-xs text-muted">
            {client.history.map((h, i) => (
              <li key={`${h.at}-${i}`} className="flex flex-wrap gap-2">
                <span className="opacity-70">{timestamp(h.at)}</span>
                <span className="font-semibold text-secondary">
                  {HISTORY_LABEL[h.kind] ?? h.kind}
                </span>
                <span>{h.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {editing && (
        <div className="mt-3 space-y-3 border-t border-border pt-3">
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="grid gap-3 sm:grid-cols-2">
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className={inputClass}
              aria-label="Implementation status"
            >
              <option value="">
                Status: automatic ({STATUS_LABEL[client.implementation.derived] ?? client.implementation.derived})
              </option>
              {Object.entries(STATUS_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <select
              value={risk}
              onChange={(e) => setRisk(e.target.value)}
              className={inputClass}
              aria-label="Account risk"
            >
              <option value="">
                Risk: automatic ({RISK_LABEL[client.risk.derived] ?? client.risk.derived})
              </option>
              {Object.entries(RISK_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Private note for your next call"
            maxLength={2000}
            rows={2}
            className={inputClass}
          />
          <div className="flex gap-2">
            <Btn size="sm" loading={saving} onClick={() => void save()}>
              Save
            </Btn>
            <Btn variant="secondary" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Btn>
          </div>
        </div>
      )}
    </div>
  )
}

function ReasonList({
  derived,
  labels,
  empty
}: {
  derived: Derived
  labels: Record<string, string>
  empty?: string
}) {
  const overridden = derived.source === 'owner' && derived.value !== derived.derived
  if (!derived.reasons.length && !overridden) {
    return empty ? <p>{empty}</p> : null
  }
  return (
    <ul className="mb-1 list-disc space-y-0.5 pl-4">
      {overridden && (
        <li>
          You set {labels[derived.value] ?? derived.value}; the signals say{' '}
          {labels[derived.derived] ?? derived.derived}.
        </li>
      )}
      {derived.reasons.map((r) => (
        <li key={r}>{r}</li>
      ))}
    </ul>
  )
}

function Stat({
  label,
  value,
  tone = 'neutral'
}: {
  label: string
  value: string
  tone?: 'neutral' | 'warn' | 'bad'
}) {
  return (
    <div className="rounded-xl border border-border bg-background-elevated px-4 py-3">
      <p className="type-caption">{label}</p>
      <p
        className={`mt-1 font-geist text-2xl font-semibold ${
          tone === 'warn'
            ? 'text-brand-soft'
            : tone === 'bad'
              ? 'text-destructive'
              : 'text-primary'
        }`}
      >
        {value}
      </p>
    </div>
  )
}
