'use client'

/** Audit (§9, §17) — searchable, append-only, redacted. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { AuditRow } from '@/lib/types'
import {
  Badge,
  DataTable,
  ErrorNote,
  Panel,
  inputClass,
  modeTone,
  riskTone,
  statusTone,
  timestamp
} from '@/components/common/primitives'

export default function AuditPage() {
  return (
    <AppShell>
      <AuditView />
    </AppShell>
  )
}

function AuditView() {
  const [filters, setFilters] = useState({
    event_type: '',
    user_id: '',
    tool: '',
    integration: '',
    workflow_id: '',
    status: '',
    date_from: '',
    date_to: '',
    q: ''
  })
  const [types, setTypes] = useState<string[]>([])
  const [rows, setRows] = useState<AuditRow[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<AuditRow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retention, setRetention] = useState<{ audit_days: number; effective_days: number } | null>(null)
  const { hasRole } = useSession()

  useEffect(() => {
    void api
      .get<{ audit_days: number; effective_days: number }>('/api/audit/retention')
      .then(setRetention)
      .catch(() => undefined)
    void api
      .get<{ event_types: string[] }>('/api/audit/event-types')
      .then((data) => setTypes(data.event_types))
      .catch(() => undefined)
  }, [])

  const load = useCallback(async () => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value)
    })
    params.set('limit', '200')
    try {
      const data = await api.get<{ items: AuditRow[]; total: number }>(
        `/api/audit?${params.toString()}`
      )
      setRows(data.items)
      setTotal(data.total)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the audit log')
    }
  }, [filters])

  useEffect(() => {
    void load()
  }, [load])

  function set(key: keyof typeof filters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Audit"
        description={`${total} events recorded in this workspace — who acted, what, with which tool, in which mode. Append-only: entries cannot be edited, and are deleted only by the retention rule${
          retention
            ? retention.effective_days > 0
              ? ` (kept ${retention.effective_days} days)`
              : ' (kept indefinitely)'
            : ''
        }. This is the business audit log, separate from Agno run tracing.`}
        actions={
          hasRole('ADMIN') && retention && retention.effective_days > 0 ? (
            <button
              onClick={async () => {
                if (!window.confirm(`Delete audit events older than ${retention.effective_days} days?`)) return
                try {
                  await api.post('/api/audit/purge')
                  await load()
                } catch (err) {
                  setError(err instanceof Error ? err.message : 'Purge failed')
                }
              }}
              className="rounded-lg border border-border px-2 py-1 font-geist text-xs text-muted hover:text-primary"
            >
              Apply retention
            </button>
          ) : undefined
        }
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <select
            className={inputClass}
            value={filters.event_type}
            onChange={(event) => set('event_type', event.target.value)}
          >
            <option value="">All events</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
          <input
            className={inputClass}
            placeholder="User id"
            value={filters.user_id}
            onChange={(event) => set('user_id', event.target.value)}
          />
          <input
            className={inputClass}
            placeholder="Tool"
            value={filters.tool}
            onChange={(event) => set('tool', event.target.value)}
          />
          <input
            className={inputClass}
            placeholder="Integration"
            value={filters.integration}
            onChange={(event) => set('integration', event.target.value)}
          />
          <input
            className={inputClass}
            placeholder="Workflow"
            value={filters.workflow_id}
            onChange={(event) => set('workflow_id', event.target.value)}
          />
          <input
            className={inputClass}
            placeholder="Status"
            value={filters.status}
            onChange={(event) => set('status', event.target.value)}
          />
          <input
            className={inputClass}
            type="datetime-local"
            value={filters.date_from}
            onChange={(event) => set('date_from', event.target.value)}
          />
          <input
            className={inputClass}
            type="datetime-local"
            value={filters.date_to}
            onChange={(event) => set('date_to', event.target.value)}
          />
          <input
            className={`${inputClass} col-span-2 md:col-span-4`}
            placeholder="Search text"
            value={filters.q}
            onChange={(event) => set('q', event.target.value)}
          />
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel>
        <DataTable
          rows={rows}
          empty="No matching audit events."
          onRowClick={setSelected}
          columns={[
            { key: 't', header: 'When', render: (r) => <span className="text-muted">{timestamp(r.created_at)}</span> },
            { key: 'e', header: 'Event', render: (r) => <Badge tone="neutral">{r.event_type}</Badge> },
            { key: 'u', header: 'User', render: (r) => <span className="text-muted">{r.user_id ?? '—'}</span> },
            { key: 'tool', header: 'Tool', render: (r) => r.tool ?? '—' },
            {
              key: 'm',
              header: 'Mode',
              render: (r) => (r.execution_mode ? <Badge tone={modeTone(r.execution_mode)}>{r.execution_mode}</Badge> : '—')
            },
            {
              key: 'd',
              header: 'Decision',
              render: (r) => (r.decision ? <Badge tone={statusTone(r.decision === 'ALLOW' ? 'COMPLETED' : 'PENDING_APPROVAL')}>{r.decision}</Badge> : '—')
            },
            { key: 's', header: 'Status', render: (r) => (r.status ? <Badge tone={statusTone(r.status)}>{r.status}</Badge> : '—') },
            { key: 'r', header: 'Risk', render: (r) => (r.risk_level ? <Badge tone={riskTone(r.risk_level)}>{r.risk_level}</Badge> : '—') },
            { key: 'l', header: 'ms', render: (r) => <span className="text-muted">{r.latency_ms ?? '—'}</span> }
          ]}
        />
      </Panel>

      {selected && (
        <Panel
          title={selected.event_type}
          description={timestamp(selected.created_at)}
          actions={
            <button onClick={() => setSelected(null)} className="text-xs text-muted">
              Close
            </button>
          }
        >
          <pre className="overflow-x-auto rounded-lg bg-background p-3 font-dmmono text-[11px] text-muted">
            {JSON.stringify(selected, null, 2)}
          </pre>
        </Panel>
      )}
    </div>
  )
}
