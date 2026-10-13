'use client'

/**
 * Approval queue (§8).
 *
 * List + Approve / Reject with the proposed payload visible so an approver
 * knows what they are agreeing to. UI only — API contract unchanged.
 */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { ActionRecord } from '@/lib/types'
import {
  Badge,
  Btn,
  EmptyState,
  ErrorNote,
  LoadingBlock,
  PageStack,
  riskTone,
  statusTone,
  timestamp
} from '@/components/common/primitives'
import { cn } from '@/lib/utils'

const FILTERS = [
  { value: 'PENDING_APPROVAL', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'DRAFT', label: 'Drafts' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'FAILED', label: 'Failed' },
  { value: '', label: 'All' }
]

const STATUS_LABEL: Record<string, string> = {
  PENDING_APPROVAL: 'Pending',
  APPROVED: 'Approved',
  DRAFT: 'Draft',
  COMPLETED: 'Completed',
  REJECTED: 'Rejected',
  FAILED: 'Failed',
  EXECUTING: 'Executing',
  CANCELLED: 'Cancelled'
}

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

  const pendingCount =
    filter === 'PENDING_APPROVAL' ? items.length : null

  return (
    <PageStack className="gap-5">
      <header className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0 max-w-prose">
            <h1 className="type-title">Approvals</h1>
            <p className="type-muted mt-1.5">
              Review what the assistant proposed before anything runs outside the
              workspace.
            </p>
          </div>
          {pendingCount !== null && !loading && (
            <p className="shrink-0 font-dmmono text-xs uppercase tracking-wide text-muted">
              {pendingCount === 0
                ? 'Queue clear'
                : `${pendingCount} waiting`}
            </p>
          )}
        </div>

        <div
          role="tablist"
          aria-label="Filter by status"
          className="flex flex-wrap gap-1.5 rounded-2xl border border-border bg-background-secondary/80 p-1.5 shadow-panel"
        >
          {FILTERS.map((option) => {
            const active = filter === option.value
            return (
              <button
                key={option.value || 'all'}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(option.value)}
                className={cn(
                  'inline-flex min-h-10 items-center rounded-xl px-3.5 font-geist text-sm font-medium ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80',
                  active
                    ? 'bg-brand text-brand-ink shadow-glow'
                    : 'text-muted hover:bg-background-elevated hover:text-primary'
                )}
              >
                {option.label}
              </button>
            )
          })}
        </div>
      </header>

      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && <LoadingBlock rows={4} />}
      {!loading && items.length === 0 && (
        <EmptyState>
          Nothing in this filter. Switch tabs or ask FreedomBot to propose an
          action.
        </EmptyState>
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
  const [payloadOpen, setPayloadOpen] = useState(false)

  const pending = action.status === 'PENDING_APPROVAL'
  const approved = action.status === 'APPROVED'
  const headline =
    action.content?.headline?.trim() || action.title || action.tool
  const requester =
    action.requested_by_name ||
    action.requested_by_role ||
    action.requested_by

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
    <article className="overflow-hidden rounded-2xl border border-border bg-background-secondary shadow-panel">
      {/* Title band */}
      <div className="space-y-3 border-b border-border px-5 py-4 sm:px-6 sm:py-5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(action.status)}>
            {STATUS_LABEL[action.status] ?? action.status}
          </Badge>
          <Badge tone={riskTone(action.risk_level)}>{action.risk_level} risk</Badge>
          {action.domain && (
            <span className="rounded-full border border-border bg-background-elevated px-2.5 py-1 font-dmmono text-[10px] uppercase tracking-wide text-muted">
              {action.domain}
            </span>
          )}
        </div>
        <h2 className="font-geist text-lg font-semibold leading-snug tracking-tight text-primary text-balance">
          {headline}
        </h2>
        {action.content?.headline &&
          action.title &&
          action.title !== action.content.headline && (
            <p className="text-xs text-muted">{action.title}</p>
          )}
      </div>

      <div className="space-y-5 px-5 py-5 sm:px-6">
        {/* Who + when — single quiet row, not nested boxes */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <div className="flex min-w-0 items-center gap-3">
            <span
              aria-hidden
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand/15 font-geist text-xs font-bold text-brand"
            >
              {initials(requester)}
            </span>
            <div className="min-w-0">
              <p className="font-geist font-medium text-primary">
                {action.requested_by_name
                  ? action.requested_by_name
                  : requester}
                {action.requested_by_role && (
                  <span className="ml-1.5 font-normal text-muted">
                    · {action.requested_by_role}
                  </span>
                )}
              </p>
              {action.requested_by_email && (
                <p className="truncate text-xs text-muted">
                  {action.requested_by_email}
                </p>
              )}
            </div>
          </div>
          <p className="text-xs text-muted sm:ml-auto">
            Requested {timestamp(action.created_at)}
          </p>
        </div>

        {/* What happens if approved */}
        {action.content &&
          (action.content.fields.length > 0 || action.content.lines.length > 0) && (
            <section
              aria-label="What needs approval"
              className="rounded-xl border border-border bg-background-elevated/70 px-4 py-4"
            >
              <p className="mb-3 font-dmmono text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
                If approved, this will
              </p>
              {action.content.fields.length > 0 && (
                <dl className="grid gap-3 sm:grid-cols-2">
                  {action.content.fields.map((field) => (
                    <div
                      key={field.label}
                      className="rounded-lg border border-border/80 bg-background-secondary px-3 py-2.5"
                    >
                      <dt className="font-dmmono text-[10px] uppercase tracking-wide text-muted">
                        {field.label}
                      </dt>
                      <dd className="mt-1 break-all font-geist text-sm font-medium text-primary">
                        {field.value}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
              {action.content.lines.length > 0 && (
                <ul className="mt-3 space-y-1.5 border-t border-border pt-3 text-sm text-secondary">
                  {action.content.lines.map((line) => (
                    <li key={line} className="flex gap-2">
                      <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-brand" aria-hidden />
                      <span>{line}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

        {/* Quiet metadata */}
        <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
          <Meta label="Tool" value={humanTool(action.tool)} mono />
          <Meta label="Domain" value={action.domain ?? '—'} />
          <Meta label="Mode" value={action.execution_mode.replaceAll('_', ' ')} />
        </dl>

        {/* Policy reason — one calm callout */}
        {action.policy_reason && (
          <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm leading-relaxed text-secondary">
            <span className="font-medium text-amber-700 dark:text-amber-300">
              Why a decision is needed
            </span>
            <span className="mt-1 block text-muted">{action.policy_reason}</span>
          </p>
        )}

        {/* Payload — collapsed by default, less visual weight */}
        <div>
          <button
            type="button"
            aria-expanded={payloadOpen}
            onClick={() => setPayloadOpen((open) => !open)}
            className="inline-flex items-center gap-2 font-geist text-xs font-medium text-muted ui-transition hover:text-primary"
          >
            <span
              aria-hidden
              className={cn(
                'inline-block ui-transition',
                payloadOpen ? 'rotate-90' : 'rotate-0'
              )}
            >
              ▸
            </span>
            Technical payload
          </button>
          {payloadOpen && (
            <pre className="mt-2 max-h-56 overflow-auto rounded-xl border border-border bg-background p-4 font-dmmono text-[11px] leading-relaxed text-muted">
              {JSON.stringify(action.payload, null, 2)}
            </pre>
          )}
        </div>

        {action.error && <ErrorNote>{action.error}</ErrorNote>}
        {error && <ErrorNote>{error}</ErrorNote>}
        {note && (
          <div
            role="status"
            className="rounded-xl border border-info/25 bg-info/10 px-4 py-3 text-sm text-info"
          >
            {note}
          </div>
        )}
      </div>

      {/* Decision bar */}
      {(pending || approved) && (
        <footer className="border-t border-border bg-background-elevated/50 px-5 py-4 sm:px-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            {pending && (
              <input
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Add a comment (optional)"
                aria-label="Optional comment"
                className="min-h-11 min-w-0 flex-1 rounded-xl border border-border bg-background-secondary px-3.5 font-geist text-sm text-primary outline-none ui-transition placeholder:text-muted/55 focus:border-brand-soft focus:shadow-glow"
              />
            )}
            <div className="flex shrink-0 flex-wrap items-center gap-2 sm:ml-auto">
              {pending && canApprove && (
                <>
                  <Btn
                    variant="danger"
                    size="md"
                    disabled={busy}
                    onClick={() => void act('reject', { comment: comment || null })}
                  >
                    Reject
                  </Btn>
                  <Btn
                    variant="positive"
                    size="md"
                    disabled={busy}
                    onClick={() => void act('approve', { comment: comment || null })}
                  >
                    Approve
                  </Btn>
                </>
              )}
              {approved && canExecute && (
                <Btn size="md" disabled={busy} onClick={() => void act('execute')}>
                  Execute now
                </Btn>
              )}
              {pending && !canApprove && (
                <p className="type-muted max-w-xs text-right">
                  Your role cannot decide this. An approver must review it.
                </p>
              )}
            </div>
          </div>
        </footer>
      )}
    </article>
  )
}

function Meta({
  label,
  value,
  mono
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="min-w-0">
      <dt className="font-dmmono text-[10px] uppercase tracking-wide text-muted">
        {label}
      </dt>
      <dd
        className={cn(
          'mt-1 truncate text-sm text-primary',
          mono ? 'font-dmmono text-xs' : 'font-geist font-medium'
        )}
        title={value}
      >
        {value}
      </dd>
    </div>
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0] ?? ''}${parts[1][0] ?? ''}`.toUpperCase()
}

function humanTool(tool: string): string {
  return tool.replaceAll('_', ' ')
}
