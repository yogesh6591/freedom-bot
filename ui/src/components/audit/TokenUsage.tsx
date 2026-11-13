'use client'

/** Audit → Token usage: tokens and estimated cost for every chat query (admins only). */

import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  DataTable,
  ErrorNote,
  LoadingBlock,
  Panel,
  inputClass,
  timestamp
} from '@/components/common/primitives'

interface Sums {
  queries: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cost_usd: number | null
}

interface Step {
  step: string
  model: string
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cost_usd: number | null
}

interface UsageRow {
  id: string
  user_id: string
  user_name: string
  session_id?: string | null
  source: string
  query: string
  status: string
  model: string
  calls: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  reasoning_tokens: number
  total_tokens: number
  cost_usd: number | null
  duration_ms?: number | null
  steps: Step[]
  created_at: string
}

interface UsageReport {
  days: number
  totals: Sums
  by_user: (Sums & { user_id: string; user_name: string })[]
  by_model: (Sums & { model: string })[]
  by_day: (Sums & { day: string })[]
  items: UsageRow[]
  cost_note: string
}

const STEP_LABEL: Record<string, string> = {
  router: 'Router',
  assistant: 'Assistant',
  assessment_reply: 'Assessment reply'
}

const n = (value?: number | null) => (value ?? 0).toLocaleString()
const usd = (value?: number | null) =>
  value == null ? '—' : value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
const seconds = (ms?: number | null) => (ms == null ? '—' : `${(ms / 1000).toFixed(1)}s`)

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-border bg-background-elevated p-4">
      <p className="type-caption">{label}</p>
      <p className="mt-1 font-dmmono text-xl text-primary">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

export function TokenUsage() {
  const [days, setDays] = useState(30)
  const [userId, setUserId] = useState('')
  const [data, setData] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<UsageRow | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ days: String(days), limit: '300' })
      if (userId) params.set('user_id', userId)
      setData(await api.get<UsageReport>(`/api/audit/token-usage?${params}`))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load token usage')
    } finally {
      setLoading(false)
    }
  }, [days, userId])

  useEffect(() => {
    void load()
  }, [load])

  const t = data?.totals
  const people = data?.by_user ?? []

  return (
    <>
      <Panel
        title="Token usage"
        description="Model tokens for every question asked in this workspace: the router, the assistant and any follow-up passes. Costs are estimates from list prices."
        actions={
          <Btn size="sm" variant="secondary" onClick={() => void load()} loading={loading}>
            Refresh
          </Btn>
        }
      >
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            aria-label="Period"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className={`${inputClass} max-w-[180px]`}
          >
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <select
            aria-label="Person"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            className={`${inputClass} max-w-[240px]`}
          >
            <option value="">Everyone</option>
            {people.map((p) => (
              <option key={p.user_id} value={p.user_id}>
                {p.user_name}
              </option>
            ))}
          </select>
        </div>
        {!data && !error && <LoadingBlock />}
        {t && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat label="Queries" value={n(t.queries)} />
            <Stat label="Total tokens" value={n(t.total_tokens)} />
            <Stat
              label="Input tokens"
              value={n(t.input_tokens)}
              hint={`${n(t.cached_tokens)} cached`}
            />
            <Stat
              label="Output tokens"
              value={n(t.output_tokens)}
              hint={t.reasoning_tokens ? `${n(t.reasoning_tokens)} reasoning` : undefined}
            />
            <Stat
              label="Estimated cost"
              value={usd(t.cost_usd)}
              hint={t.queries ? `${usd((t.cost_usd ?? 0) / t.queries)} per query` : undefined}
            />
          </div>
        )}
        {data && <p className="mt-3 text-xs text-muted">{data.cost_note}</p>}
      </Panel>

      {data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="By person">
            <DataTable
              rows={data.by_user}
              empty="No queries yet."
              onRowClick={(r) => setUserId(r.user_id)}
              columns={[
                { key: 'u', header: 'Person', render: (r) => r.user_name },
                { key: 'q', header: 'Queries', render: (r) => n(r.queries) },
                { key: 't', header: 'Tokens', render: (r) => n(r.total_tokens) },
                { key: 'c', header: 'Est. cost', render: (r) => usd(r.cost_usd) }
              ]}
            />
          </Panel>
          <Panel title="By model">
            <DataTable
              rows={data.by_model}
              empty="No queries yet."
              columns={[
                { key: 'm', header: 'Model', render: (r) => r.model || '—' },
                { key: 'q', header: 'Queries', render: (r) => n(r.queries) },
                { key: 't', header: 'Tokens', render: (r) => n(r.total_tokens) },
                { key: 'c', header: 'Est. cost', render: (r) => usd(r.cost_usd) }
              ]}
            />
          </Panel>
        </div>
      )}

      {data && (
        <Panel
          title="Every query"
          description={`Newest first, up to 300. Click a row to see each model call.`}
        >
          <DataTable
            rows={data.items}
            empty="No queries in this period."
            onRowClick={setSelected}
            columns={[
              {
                key: 'w',
                header: 'When',
                render: (r) => <span className="whitespace-nowrap">{timestamp(r.created_at)}</span>
              },
              { key: 'u', header: 'Who', render: (r) => r.user_name },
              {
                key: 'q',
                header: 'Query',
                render: (r) => (
                  <span className="flex max-w-md flex-wrap items-center gap-1.5">
                    <span className="line-clamp-2">{r.query || '—'}</span>
                    {r.source === 'scheduled' && <Badge tone="info">Scheduled</Badge>}
                    {r.status !== 'ok' && <Badge tone="bad">Failed</Badge>}
                  </span>
                )
              },
              { key: 'in', header: 'Input', render: (r) => n(r.input_tokens) },
              { key: 'ca', header: 'Cached', render: (r) => n(r.cached_tokens) },
              { key: 'out', header: 'Output', render: (r) => n(r.output_tokens) },
              {
                key: 't',
                header: 'Total',
                render: (r) => <span className="font-semibold">{n(r.total_tokens)}</span>
              },
              { key: 'c', header: 'Est. cost', render: (r) => usd(r.cost_usd) },
              { key: 'd', header: 'Time', render: (r) => seconds(r.duration_ms) }
            ]}
          />
        </Panel>
      )}

      {selected && (
        <Panel
          title="Model calls for this query"
          description={`${selected.user_name} · ${timestamp(selected.created_at)} · ${selected.model}`}
          actions={
            <Btn variant="ghost" size="sm" onClick={() => setSelected(null)}>
              Close
            </Btn>
          }
        >
          <p className="mb-3 text-sm text-secondary">{selected.query}</p>
          <DataTable
            rows={selected.steps}
            empty="No model calls."
            columns={[
              { key: 's', header: 'Step', render: (s) => STEP_LABEL[s.step] ?? s.step },
              { key: 'm', header: 'Model', render: (s) => s.model || '—' },
              { key: 'in', header: 'Input', render: (s) => n(s.input_tokens) },
              { key: 'ca', header: 'Cached', render: (s) => n(s.cached_tokens) },
              { key: 'out', header: 'Output', render: (s) => n(s.output_tokens) },
              { key: 're', header: 'Reasoning', render: (s) => n(s.reasoning_tokens) },
              { key: 't', header: 'Total', render: (s) => n(s.total_tokens) },
              { key: 'c', header: 'Est. cost', render: (s) => usd(s.cost_usd) }
            ]}
          />
        </Panel>
      )}
    </>
  )
}
