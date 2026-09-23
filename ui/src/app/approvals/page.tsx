'use client'

/**
 * Approval queue (§8).
 *
 * Every card carries the whole basis for the decision — what is proposed, by
 * whom, against which integration, the exact payload, the risk class, and the
 * policy rule that made approval necessary — so an approver never has to guess
 * what they are agreeing to.
 */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { ActionRecord } from '@/lib/types'
import {
  Badge,
  EmptyState,
  ErrorNote,
  Panel,
  inputClass,
  riskTone,
  statusTone,
  timestamp
} from '@/components/common/primitives'

const FILTERS = [
  { value: 'PENDING_APPROVAL', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'DRAFT', label: 'Drafts' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'FAILED', label: 'Failed' },
  { value: '', label: 'All' }
]

export default function ApprovalsPage() {
  return (
    <AppShell>
      <ApprovalsView />
    </AppShell>
  )
}

function ApprovalsView() {
  const { hasRole } = useSession()
  const [filter, setFilter] = useState('PENDING_APPROVAL')
  const [items, setItems] = useState<ActionRecord[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const canApprove = hasRole('APPROVER') || hasRole('ADMIN')
  const canExecute = hasRole('OPERATOR') || hasRole('APPROVER') || hasRole('ADMIN')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const path = filter ? `/api/approvals?status=${filter}` : '/api/approvals/all'
      const data = await api.get<{ items: ActionRecord[] }>(path)
      setItems(data.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the queue')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-4">
      <Panel
        title="Approvals"
        description="Actions the assistant proposed that need a human decision before anything happens."
      >
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((option) => (
            <button
              key={option.value}
              onClick={() => setFilter(option.value)}
              className={`rounded-lg px-3 py-1.5 font-geist text-xs transition ${
                filter === option.value
                  ? 'bg-background-secondary text-primary'
                  : 'text-muted hover:text-primary'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && <EmptyState>Loading…</EmptyState>}
      {!loading && items.length === 0 && (
        <EmptyState>Nothing here. The queue is clear.</EmptyState>
      )}

      <div className="space-y-3">
        {items.map((action) => (
          <ActionCard
            key={action.id}
            action={action}
            canApprove={canApprove}
            canExecute={canExecute}
            onChanged={load}
          />
        ))}
      </div>
    </div>
  )
}

function ActionCard({
  action,
  canApprove,
  canExecute,
  onChanged
}: {
  action: ActionRecord
  canApprove: boolean
  canExecute: boolean
  onChanged: () => Promise<void>
}) {
  const [comment, setComment] = useState('')
  const [payload, setPayload] = useState(JSON.stringify(action.payload, null, 2))
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [detail, setDetail] = useState<ActionRecord | null>(null)

  const pending = action.status === 'PENDING_APPROVAL'
  const approved = action.status === 'APPROVED'

  async function act(path: string, body?: unknown) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      const result = await api.post<{ message?: string }>(
        `/api/approvals/${action.id}/${path}`,
        body
      )
      if (result?.message) setNote(result.message)
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed')
    } finally {
      setBusy(false)
    }
  }

  async function showHistory() {
    setDetail(await api.get<ActionRecord>(`/api/approvals/${action.id}`))
  }

  return (
    <Panel>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(action.status)}>{action.status}</Badge>
          <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
          <Badge tone="neutral">{action.classification}</Badge>
          <Badge tone="neutral">{action.execution_mode}</Badge>
          <span className="font-geist text-sm text-primary">{action.title}</span>
        </div>

        <p className="text-xs text-muted">{action.description || action.explanation}</p>

        <ActionPreview tool={action.tool} payload={action.payload} explanation={action.explanation} />

        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] md:grid-cols-4">
          <Meta label="Requested by" value={action.requested_by_role ?? action.requested_by} />
          <Meta label="Tool" value={action.tool} />
          <Meta label="Integration" value={action.integration ?? '—'} />
          <Meta label="Domain" value={action.domain ?? '—'} />
          <Meta label="Records affected" value={String(action.record_count)} />
          <Meta label="Created" value={timestamp(action.created_at)} />
          <Meta label="Expires" value={timestamp(action.expires_at)} />
          <Meta label="Approver role" value={action.approval?.required_role ?? '—'} />
        </dl>

        <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs">
          <span className="font-geist text-amber-300">Why this needs a decision: </span>
          <span className="text-muted">{action.policy_reason}</span>
        </div>

        <details open className="text-xs">
          <summary className="cursor-pointer text-muted">Full proposed payload</summary>
          {editing ? (
            <textarea
              value={payload}
              onChange={(event) => setPayload(event.target.value)}
              className={`${inputClass} mt-2 min-h-[140px] font-dmmono text-[11px]`}
            />
          ) : (
            <pre className="mt-2 overflow-x-auto rounded-lg bg-background p-3 font-dmmono text-[11px] text-muted">
              {JSON.stringify(action.payload, null, 2)}
            </pre>
          )}
        </details>

        {action.error && <ErrorNote>{action.error}</ErrorNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
        {note && (
          <div className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs text-sky-200">
            {note}
          </div>
        )}

        {(pending || approved) && (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            {pending && (
              <input
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Comment (optional)"
                className="min-w-[200px] flex-1 rounded-lg border border-border bg-background px-3 py-1.5 font-geist text-xs outline-none"
              />
            )}
            {pending && canApprove && (
              <>
                <button
                  disabled={busy}
                  onClick={() => void act('approve', { comment: comment || null })}
                  className="rounded-lg bg-positive px-3 py-1.5 font-geist text-xs text-black disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  disabled={busy}
                  onClick={() => void act('reject', { comment: comment || null })}
                  className="rounded-lg bg-destructive px-3 py-1.5 font-geist text-xs text-white disabled:opacity-50"
                >
                  Reject
                </button>
                <button
                  disabled={busy || !comment}
                  title={comment ? '' : 'A comment is required to request changes'}
                  onClick={() => void act('request-changes', { comment })}
                  className="rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted disabled:opacity-50"
                >
                  Request changes
                </button>
                {editing ? (
                  <button
                    disabled={busy}
                    onClick={() => {
                      try {
                        void act('edit', { payload: JSON.parse(payload), comment })
                        setEditing(false)
                      } catch {
                        setError('The payload is not valid JSON')
                      }
                    }}
                    className="rounded-lg border border-brand px-3 py-1.5 font-geist text-xs text-brand"
                  >
                    Save payload
                  </button>
                ) : (
                  <button
                    onClick={() => setEditing(true)}
                    className="rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted"
                  >
                    Edit before approving
                  </button>
                )}
              </>
            )}
            {approved && canExecute && (
              <button
                disabled={busy}
                onClick={() => void act('execute')}
                className="rounded-lg bg-brand px-3 py-1.5 font-geist text-xs text-white disabled:opacity-50"
              >
                Execute now
              </button>
            )}
            {pending && !canApprove && (
              <span className="text-xs text-muted">
                Your role cannot decide this. An approver must review it.
              </span>
            )}
            <button
              onClick={() => void showHistory()}
              className="ml-auto text-xs text-muted hover:text-primary"
            >
              History
            </button>
          </div>
        )}

        {detail?.events && (
          <ol className="space-y-1 border-t border-border pt-3 text-[11px] text-muted">
            {detail.events.map((event) => (
              <li key={event.id}>
                <span className="font-dmmono text-primary">{event.event}</span>{' '}
                {event.from_status ? `${event.from_status} → ${event.to_status}` : event.to_status}{' '}
                · {event.actor_role} {event.actor_id} · {timestamp(event.created_at)}
                {event.comment ? ` · “${event.comment}”` : ''}
              </li>
            ))}
          </ol>
        )}
      </div>
    </Panel>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted/70">{label}</dt>
      <dd className="font-geist text-primary break-all">{value}</dd>
    </div>
  )
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function str(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(str).filter(Boolean).join(', ')
  return JSON.stringify(value)
}

/** Flatten tool args whether they are top-level or nested under kwargs/arguments. */
function flatPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const nested = {
    ...asRecord(payload.kwargs),
    ...asRecord(payload.arguments),
    ...asRecord(payload.args)
  }
  return { ...nested, ...payload }
}

function ActionPreview({
  tool,
  payload,
  explanation
}: {
  tool: string
  payload: Record<string, unknown>
  explanation: string
}) {
  const data = flatPayload(payload || {})
  const rows: { label: string; value: string }[] = []

  if (tool.startsWith('email_')) {
    rows.push(
      { label: 'To', value: str(data.recipients || data.to) },
      { label: 'Cc', value: str(data.cc) },
      { label: 'Subject', value: str(data.subject) },
      { label: 'Body', value: str(data.body || data.text || data.html) }
    )
  } else if (tool.startsWith('crm_')) {
    rows.push(
      { label: 'Contact', value: [str(data.first_name), str(data.last_name)].filter(Boolean).join(' ') },
      { label: 'Email', value: str(data.email) },
      { label: 'Company', value: str(data.company) },
      { label: 'Note / body', value: str(data.body || data.note || data.title) },
      { label: 'Contact id', value: str(data.contact_id) },
      { label: 'Deal id', value: str(data.deal_id) },
      { label: 'Stage', value: str(data.stage || data.lifecycle) }
    )
  } else if (tool.startsWith('calendar_')) {
    rows.push(
      { label: 'Title', value: str(data.title) },
      { label: 'Starts', value: str(data.starts_at) },
      { label: 'Ends', value: str(data.ends_at) },
      { label: 'Attendees', value: str(data.attendees) },
      { label: 'Location', value: str(data.location) },
      { label: 'Description', value: str(data.description) }
    )
  } else if (tool.startsWith('accounting_') || tool === 'invoice_search') {
    rows.push(
      { label: 'Customer', value: str(data.customer || data.customer_name) },
      { label: 'Amount', value: str(data.amount) },
      { label: 'Invoice', value: str(data.invoice_id || data.number) },
      { label: 'Method', value: str(data.method) },
      { label: 'Note', value: str(data.note) }
    )
  } else {
    for (const [key, value] of Object.entries(data)) {
      if (['kwargs', 'arguments', 'args'].includes(key)) continue
      const rendered = str(value)
      if (rendered) rows.push({ label: key, value: rendered })
    }
  }

  const visible = rows.filter((row) => row.value.trim().length > 0)

  return (
    <div className="rounded-lg border border-border bg-background-secondary/40 px-3 py-3">
      <div className="mb-2 font-geist text-xs text-primary">What you are approving</div>
      {visible.length === 0 ? (
        <p className="text-xs text-muted">
          {explanation || 'No structured fields were provided. See the full payload below.'}
        </p>
      ) : (
        <dl className="space-y-2 text-xs">
          {visible.map((row) => (
            <div key={row.label}>
              <dt className="text-muted/70">{row.label}</dt>
              <dd className="whitespace-pre-wrap font-geist text-primary">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
