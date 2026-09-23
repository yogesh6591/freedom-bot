'use client'

/** Integrations (§17) — connection status per category. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { Integration } from '@/lib/types'
import {
  Badge,
  DataTable,
  ErrorNote,
  Panel,
  statusTone,
  timestamp
} from '@/components/common/primitives'

const CATEGORY_LABEL: Record<string, string> = {
  crm: 'CRM',
  email: 'Email',
  calendar: 'Calendar',
  accounting: 'Accounting',
  storage: 'Document storage',
  communication: 'Slack / Teams'
}

export default function IntegrationsPage() {
  return (
    <AppShell>
      <IntegrationsView />
    </AppShell>
  )
}

function IntegrationsView() {
  const { hasRole } = useSession()
  const [items, setItems] = useState<Integration[]>([])
  const [providers, setProviders] = useState<Record<string, string[]>>({})
  const [health, setHealth] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const isAdmin = hasRole('ADMIN')

  const load = useCallback(async () => {
    try {
      const data = await api.get<{
        items: Integration[]
        available_providers: Record<string, string[]>
      }>('/api/integrations')
      setItems(data.items)
      setProviders(data.available_providers)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load integrations')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function check() {
    setBusy(true)
    try {
      const data = await api.post<{ results: { category: string; ok: boolean; summary: string; error: string | null }[] }>(
        '/api/integrations/health'
      )
      setHealth(
        Object.fromEntries(
          data.results.map((r) => [r.category, r.ok ? r.summary : r.error ?? 'failed'])
        )
      )
    } finally {
      setBusy(false)
    }
  }

  async function toggle(item: Integration) {
    if (!item.provider) return
    await api.post('/api/integrations', {
      category: item.category,
      provider: item.provider,
      display_name: item.display_name,
      enabled: !item.enabled,
      status: item.enabled ? 'DISCONNECTED' : 'CONNECTED'
    })
    await load()
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Integrations"
        description="Business systems this workspace can reach. Credentials are never stored in this table — they live in the encrypted workspace secret store and are never shown to the assistant."
        actions={
          <button
            disabled={busy}
            onClick={() => void check()}
            className="rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted"
          >
            {busy ? 'Checking…' : 'Check connections'}
          </button>
        }
      >
        {error && <ErrorNote>{error}</ErrorNote>}
        <DataTable
          rows={items}
          empty="No integrations."
          columns={[
            {
              key: 'category',
              header: 'Category',
              render: (row) => (
                <span className="font-geist text-primary">
                  {CATEGORY_LABEL[row.category] ?? row.category}
                </span>
              )
            },
            { key: 'provider', header: 'Provider', render: (row) => row.provider ?? '—' },
            {
              key: 'status',
              header: 'Status',
              render: (row) => <Badge tone={statusTone(row.status)}>{row.status}</Badge>
            },
            {
              key: 'health',
              header: 'Last check',
              render: (row) => (
                <span className="text-muted">
                  {health[row.category] ?? row.last_error ?? '—'}
                </span>
              )
            },
            {
              key: 'connected',
              header: 'Connected',
              render: (row) => (
                <span className="text-muted">{timestamp(row.connected_at ?? null)}</span>
              )
            },
            {
              key: 'actions',
              header: '',
              render: (row) =>
                isAdmin && row.provider ? (
                  <button
                    onClick={() => void toggle(row)}
                    className="rounded border border-border px-2 py-0.5 text-[11px] text-muted"
                  >
                    {row.enabled ? 'Disconnect' : 'Connect'}
                  </button>
                ) : null
            }
          ]}
        />
      </Panel>

      <Panel title="Available providers">
        <ul className="space-y-1 text-xs text-muted">
          {Object.entries(providers).map(([category, list]) => (
            <li key={category}>
              <span className="font-geist text-primary">
                {CATEGORY_LABEL[category] ?? category}:
              </span>{' '}
              {list.join(', ')}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  )
}
