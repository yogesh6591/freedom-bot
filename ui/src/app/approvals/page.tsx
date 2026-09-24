'use client'

/**
 * Approval queue (§8).
 *
 * List + Approve / Reject with the proposed payload visible so an approver
 * knows what they are agreeing to.
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
              className={`rounded-full px-3.5 py-1.5 font-geist text-xs font-medium transition ${
                filter === option.value
                  ? 'bg-brand text-brand-ink shadow-glow'
                  : 'bg-background-elevated text-muted hover:text-primary'
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

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

  return (
    <Panel>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(action.status)}>{action.status}</Badge>
          <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
          <span className="font-geist text-sm text-primary">{action.title}</span>
        </div>

        <p className="text-xs text-muted">{action.description || action.explanation}</p>

        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] md:grid-cols-4">
          <Meta label="Requested by" value={action.requested_by_role ?? action.requested_by} />
          <Meta label="Tool" value={action.tool} />
          <Meta label="Domain" value={action.domain ?? '—'} />
          <Meta label="Created" value={timestamp(action.created_at)} />
        </dl>

        <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs">
          <span className="font-geist text-amber-300">Why this needs a decision: </span>
          <span className="text-muted">{action.policy_reason}</span>
        </div>

        <details open className="text-xs">
          <summary className="cursor-pointer text-muted">Proposed payload</summary>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-background p-3 font-dmmono text-[11px] text-muted">
            {JSON.stringify(action.payload, null, 2)}
          </pre>
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
              </>
            )}
            {approved && canExecute && (
              <button
                disabled={busy}
                onClick={() => void act('execute')}
                className="rounded-lg bg-brand px-3 py-1.5 font-geist text-xs text-brand-ink disabled:opacity-50"
              >
                Execute now
              </button>
            )}
            {pending && !canApprove && (
              <span className="text-xs text-muted">
                Your role cannot decide this. An approver must review it.
              </span>
            )}
          </div>
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
