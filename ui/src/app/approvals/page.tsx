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
  Btn,
  Chip,
  EmptyState,
  ErrorNote,
  LoadingBlock,
  PageStack,
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
    <PageStack>
      <Panel
        title="Approvals"
        description="Actions the assistant proposed that need a human decision before anything happens."
      >
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
          {FILTERS.map((option) => (
            <Chip
              key={option.value}
              active={filter === option.value}
              onClick={() => setFilter(option.value)}
            >
              {option.label}
            </Chip>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && <LoadingBlock rows={4} />}
      {!loading && items.length === 0 && (
        <EmptyState>Nothing here. The queue is clear.</EmptyState>
      )}

      <div className="space-y-4">
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
    </PageStack>
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
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(action.status)}>{action.status}</Badge>
          <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
          <span className="font-geist text-sm font-semibold text-primary">{action.title}</span>
        </div>

        <div className="rounded-xl border border-border bg-background-elevated px-4 py-3">
          <p className="type-caption">Requested by</p>
          <p className="mt-1 font-geist text-sm text-primary">
            {action.requested_by_name
              ? `${action.requested_by_name}${action.requested_by_role ? ` (${action.requested_by_role})` : ''}`
              : action.requested_by_role ?? action.requested_by}
          </p>
          {action.requested_by_email && (
            <p className="mt-0.5 text-xs text-muted">{action.requested_by_email}</p>
          )}
        </div>

        {action.content && (
          <div className="space-y-3 rounded-xl border border-sky-500/25 bg-sky-500/5 px-4 py-3">
            <p className="type-caption text-sky-300/80">What needs approval</p>
            <p className="font-geist text-sm text-primary">{action.content.headline}</p>
            {action.content.fields.length > 0 && (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs md:grid-cols-4">
                {action.content.fields.map((field) => (
                  <Meta key={field.label} label={field.label} value={field.value} />
                ))}
              </dl>
            )}
            {action.content.lines.length > 0 && (
              <ul className="space-y-1 border-t border-sky-500/15 pt-3 text-xs text-primary">
                {action.content.lines.map((line) => (
                  <li key={line}>• {line}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs md:grid-cols-3">
          <Meta label="Tool" value={action.tool} />
          <Meta label="Domain" value={action.domain ?? '—'} />
          <Meta label="Created" value={timestamp(action.created_at)} />
        </dl>

        <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 px-4 py-3 text-sm">
          <span className="font-geist text-amber-300">Why this needs a decision: </span>
          <span className="text-muted">{action.policy_reason}</span>
        </div>

        <details className="text-sm">
          <summary className="cursor-pointer text-muted ui-transition hover:text-primary">
            Technical payload (tool arguments)
          </summary>
          <pre className="mt-3 overflow-x-auto rounded-xl bg-background p-4 font-dmmono text-xs text-muted">
            {JSON.stringify(action.payload, null, 2)}
          </pre>
        </details>

        {action.error && <ErrorNote>{action.error}</ErrorNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
        {note && (
          <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-sm text-sky-200">
            {note}
          </div>
        )}

        {(pending || approved) && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
            {pending && (
              <input
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Comment (optional)"
                aria-label="Optional comment"
                className="min-h-11 min-w-[200px] flex-1 rounded-xl border border-border bg-background px-3.5 font-geist text-sm outline-none ui-transition focus:border-brand-soft focus:shadow-glow"
              />
            )}
            {pending && canApprove && (
              <>
                <Btn
                  variant="positive"
                  size="sm"
                  disabled={busy}
                  onClick={() => void act('approve', { comment: comment || null })}
                >
                  Approve
                </Btn>
                <Btn
                  variant="danger"
                  size="sm"
                  disabled={busy}
                  onClick={() => void act('reject', { comment: comment || null })}
                >
                  Reject
                </Btn>
              </>
            )}
            {approved && canExecute && (
              <Btn
                size="sm"
                disabled={busy}
                onClick={() => void act('execute')}
              >
                Execute now
              </Btn>
            )}
            {pending && !canApprove && (
              <span className="type-muted">
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
      <dt className="type-caption">{label}</dt>
      <dd className="mt-0.5 font-geist text-primary break-all">{value}</dd>
    </div>
  )
}
