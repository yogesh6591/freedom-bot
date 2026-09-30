'use client'

/** FB-047 — JeanneCAIO exception-only owner view (not a CRM). */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import {
  Badge,
  EmptyState,
  ErrorNote,
  LoadingBlock,
  PageStack,
  Panel,
  timestamp
} from '@/components/common/primitives'

interface PortfolioClient {
  client_id: string
  slug: string
  company_name: string
  purchased_domains: string[]
  implementation_status: string
  account_risk: string
  needs_me_count: number
  needs_me: {
    id: string
    subject: string
    escalate_reason?: string | null
    updated_at?: string | null
  }[]
  recent_history: {
    id: string
    subject: string
    status: string
    assignee: string
    updated_at?: string | null
  }[]
  revenue: {
    placeholder?: boolean
    customer_price_usd?: number | null
    margin_percent?: number
    quote_status?: string
  }
}

interface Portfolio {
  disclaimer?: string
  client_count: number
  needs_me_count: number
  at_risk_count: number
  clients: PortfolioClient[]
  needs_me_queue: {
    id: string
    client_id: string
    subject: string
    escalate_reason?: string | null
    updated_at?: string | null
  }[]
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
      <Panel
        title="Owner view (JeanneCAIO)"
        description="Exception-only portfolio — client count, what they bought, implementation, risk, what needs you, history before a call, revenue. Not a CRM."
      >
        {data?.disclaimer && (
          <p className="mb-4 rounded-xl border border-brand/30 bg-brand/10 px-4 py-3 text-xs text-brand-soft">
            {data.disclaimer}
          </p>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        {loading && <LoadingBlock rows={3} />}
        {data && !loading && (
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Clients" value={String(data.client_count)} />
            <Stat label="Needs me" value={String(data.needs_me_count)} tone="warn" />
            <Stat label="At risk" value={String(data.at_risk_count)} tone="bad" />
          </div>
        )}
      </Panel>

      {data && data.needs_me_queue.length > 0 && (
        <Panel title="What needs me" description="Escalations from Cepoch support (FB-046).">
          <ul className="space-y-3">
            {data.needs_me_queue.map((item) => (
              <li
                key={item.id}
                className="rounded-xl border border-border bg-background-elevated px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="warn">{item.escalate_reason || 'ESCALATED'}</Badge>
                  <span className="font-geist text-sm font-semibold text-primary">
                    {item.subject}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted">{timestamp(item.updated_at)}</p>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {data && (
        <Panel title="Clients">
          {data.clients.length === 0 ? (
            <EmptyState>No active clients.</EmptyState>
          ) : (
            <div className="space-y-4">
              {data.clients.map((client) => (
                <div
                  key={client.client_id}
                  className="rounded-xl border border-border bg-background-elevated p-4"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="type-subtitle text-base">{client.company_name}</h3>
                    <Badge tone="neutral">{client.slug}</Badge>
                    <Badge
                      tone={
                        client.account_risk === 'at_risk'
                          ? 'bad'
                          : client.account_risk === 'watch'
                            ? 'warn'
                            : 'good'
                      }
                    >
                      {client.account_risk}
                    </Badge>
                    <Badge tone="info">{client.implementation_status}</Badge>
                  </div>
                  <p className="mt-2 text-xs text-muted">
                    Bought:{' '}
                    {client.purchased_domains.length
                      ? client.purchased_domains.join(', ')
                      : '—'}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    Revenue (her side):{' '}
                    {client.revenue.customer_price_usd != null
                      ? `$${client.revenue.customer_price_usd.toLocaleString()}`
                      : 'Not set'}
                    {client.revenue.margin_percent != null
                      ? ` · margin ${client.revenue.margin_percent}%`
                      : ''}
                    {client.revenue.placeholder ? ' · face quote' : ''}
                    {client.revenue.quote_status
                      ? ` · ${client.revenue.quote_status}`
                      : ''}
                  </p>
                  <p className="mt-2 text-xs text-brand-soft">
                    Needs me: {client.needs_me_count}
                  </p>
                  {client.recent_history.length > 0 && (
                    <div className="mt-3 border-t border-border pt-3">
                      <p className="type-caption mb-2">History before a call</p>
                      <ul className="space-y-1 text-xs text-muted">
                        {client.recent_history.map((h) => (
                          <li key={h.id}>
                            [{h.assignee}/{h.status}] {h.subject}{' '}
                            <span className="opacity-70">{timestamp(h.updated_at)}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Panel>
      )}
    </PageStack>
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
