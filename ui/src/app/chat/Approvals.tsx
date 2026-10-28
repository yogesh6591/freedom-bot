'use client'

/**
 * Approvals inside the chat: a bell in the chat header with the number of requests waiting for this user,
 * and a panel that opens as one more message in the conversation (never a separate screen).
 *
 * The bell polls GET /api/approvals/inbox. Approving or rejecting calls the normal approval endpoints, so
 * every server-side check (approver role, no self-approval, execute rights) still applies; the UI only
 * shows the result straight away.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, BellRing, CheckCircle2, Clock, X, XCircle } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

export interface ApprovalItem {
  id: string
  title: string
  status: string
  tool: string
  requested_by_name?: string | null
  created_at?: string | null
  updated_at?: string | null
  error?: string | null
  content?: { headline?: string; summary?: string; fields?: { label: string; value: string }[] }
}

interface Inbox {
  can_decide: boolean
  to_decide: ApprovalItem[]
  mine: ApprovalItem[]
}

/** Outcome shown on a card right after this user decided it. */
type Decided = { kind: 'approved' | 'rejected'; note: string }

const POLL_MS = 10_000
const DONE = new Set(['APPROVED', 'EXECUTING', 'COMPLETED', 'REJECTED', 'FAILED', 'CANCELLED'])

function statusLabel(status: string): { text: string; tone: string } {
  switch (status) {
    case 'PENDING_APPROVAL':
      return { text: 'Waiting for approval', tone: 'bg-amber-500/10 text-amber-700' }
    case 'APPROVED':
      return { text: 'Approved', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'EXECUTING':
      return { text: 'Approved, running', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'COMPLETED':
      return { text: 'Approved and done', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'CANCELLED':
      return { text: 'Cancelled', tone: 'bg-black/5 text-secondary' }
    case 'REJECTED':
      return { text: 'Rejected', tone: 'bg-red-500/10 text-red-600' }
    case 'FAILED':
      return { text: 'Approved, but failed', tone: 'bg-red-500/10 text-red-600' }
    case 'DRAFT':
      return { text: 'Draft', tone: 'bg-black/5 text-secondary' }
    default:
      return { text: status.replace(/_/g, ' ').toLowerCase(), tone: 'bg-black/5 text-secondary' }
  }
}

/**
 * The inbox, polled while the tab is visible. `fresh` counts the user's own requests that were decided
 * since they last opened the panel (so a requester sees "approved" on the bell without asking).
 */
export function useApprovalInbox(enabled = true) {
  const [inbox, setInbox] = useState<Inbox | null>(null)
  const [fresh, setFresh] = useState(0)
  const [flash, setFlash] = useState<Decided['kind'] | null>(null)
  const known = useRef<Map<string, string> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const next = await api.get<Inbox>('/api/approvals/inbox')
      setInbox(next)
      const seen = known.current
      if (seen) {
        const newlyDone = next.mine.filter(
          (a) => seen.get(a.id) === 'PENDING_APPROVAL' && DONE.has(a.status)
        )
        if (newlyDone.length) {
          setFresh((n) => n + newlyDone.length)
          setFlash(newlyDone.some((a) => a.status === 'REJECTED') ? 'rejected' : 'approved')
        }
      }
      known.current = new Map(next.mine.map((a) => [a.id, a.status]))
    } catch {
      // The bell stays as it was; the next poll tries again.
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    void refresh()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [refresh, enabled])

  useEffect(() => {
    if (!flash) return
    const timer = window.setTimeout(() => setFlash(null), 4000)
    return () => window.clearTimeout(timer)
  }, [flash])

  return {
    inbox,
    fresh,
    flash,
    refresh,
    markSeen: () => setFresh(0),
    showFlash: (kind: Decided['kind']) => setFlash(kind)
  }
}

export function ApprovalBell({
  waiting,
  fresh,
  flash,
  onClick
}: {
  waiting: number
  fresh: number
  flash: Decided['kind'] | null
  onClick: () => void
}) {
  const label =
    flash === 'approved'
      ? 'Approved'
      : flash === 'rejected'
        ? 'Rejected'
        : waiting
          ? `${waiting} waiting for your approval`
          : fresh
            ? `${fresh} of your requests decided`
            : 'Approvals'
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className={`relative inline-flex h-9 items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-semibold shadow-sm backdrop-blur transition-colors ${
        flash === 'approved'
          ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
          : flash === 'rejected'
            ? 'border-red-200 bg-red-50 text-red-600'
            : 'border-white/80 bg-white/70 text-secondary hover:border-violet-200 hover:text-primary dark:border-white/10 dark:bg-white/5'
      }`}
    >
      {flash === 'approved' ? (
        <CheckCircle2 size={15} aria-hidden />
      ) : flash === 'rejected' ? (
        <XCircle size={15} aria-hidden />
      ) : waiting ? (
        <BellRing size={15} aria-hidden />
      ) : (
        <Bell size={15} aria-hidden />
      )}
      <span className="hidden sm:inline">{flash ? label : 'Approvals'}</span>
      {!flash && waiting > 0 ? (
        <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-[11px] font-bold text-white">
          {waiting}
        </span>
      ) : !flash && fresh > 0 ? (
        <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white bg-emerald-500" aria-hidden />
      ) : null}
    </button>
  )
}

function Card({
  item,
  decided,
  busy,
  onDecide
}: {
  item: ApprovalItem
  decided?: Decided
  busy?: boolean
  onDecide?: (kind: Decided['kind']) => void
}) {
  const status = statusLabel(item.status)
  const fields = (item.content?.fields ?? []).slice(0, 6)
  return (
    <div className="rounded-xl border border-black/5 bg-white/80 p-3 dark:border-white/10 dark:bg-white/5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[14px] font-semibold text-primary">{item.content?.headline || item.title}</p>
          <p className="mt-0.5 text-[12px] text-muted">
            {item.requested_by_name ? `Requested by ${item.requested_by_name}` : item.tool}
            {item.created_at ? ` · ${new Date(item.created_at).toLocaleString()}` : ''}
          </p>
        </div>
        {decided ? (
          <span
            className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-semibold ${
              decided.kind === 'approved' ? 'bg-emerald-500/10 text-emerald-700' : 'bg-red-500/10 text-red-600'
            }`}
          >
            {decided.kind === 'approved' ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
            {decided.kind === 'approved' ? 'Approved' : 'Rejected'}
          </span>
        ) : !onDecide ? (
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[12px] font-semibold ${status.tone}`}>{status.text}</span>
        ) : null}
      </div>
      {fields.length > 0 && (
        <dl className="mt-2 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-[13px]">
          {fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-muted">{f.label}</dt>
              <dd className="min-w-0 break-words text-primary">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {decided?.note ? <p className="mt-2 text-[12px] text-muted">{decided.note}</p> : null}
      {item.status === 'FAILED' && item.error ? <p className="mt-2 text-[12px] text-red-600">{item.error}</p> : null}
      {onDecide && !decided ? (
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('approved')}
            className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
          >
            <CheckCircle2 size={14} /> Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('rejected')}
            className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-[13px] font-semibold text-secondary hover:border-red-300 hover:text-red-600 disabled:opacity-60"
          >
            <XCircle size={14} /> Reject
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** The bell with its dropdown: what waits for this user (Approve / Reject) and their own requests. */
export function ApprovalsMenu({
  approvals
}: {
  approvals: ReturnType<typeof useApprovalInbox>
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div ref={box} className="relative">
      <ApprovalBell
        waiting={approvals.inbox?.to_decide.length ?? 0}
        fresh={approvals.fresh}
        flash={approvals.flash}
        onClick={() => {
          setOpen((o) => !o)
          approvals.markSeen()
          void approvals.refresh()
        }}
      />
      {open && (
        <ApprovalsPanel
          inbox={approvals.inbox}
          onClose={() => setOpen(false)}
          onChanged={() => void approvals.refresh()}
          onDecided={(kind) => approvals.showFlash(kind)}
        />
      )}
    </div>
  )
}

function ApprovalsPanel({
  inbox,
  onClose,
  onChanged,
  onDecided
}: {
  inbox: Inbox | null
  onClose: () => void
  onChanged: () => void
  onDecided: (kind: Decided['kind']) => void
}) {
  const [decided, setDecided] = useState<Record<string, Decided>>({})
  // Cards decided here stay visible with their outcome after they leave the waiting list.
  const [decidedItems, setDecidedItems] = useState<ApprovalItem[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function decide(item: ApprovalItem, kind: Decided['kind']) {
    setBusyId(item.id)
    setError(null)
    try {
      if (kind === 'approved') {
        const res = await api.post<{ executed?: boolean; message?: string; result?: { message?: string } }>(
          `/api/approvals/${encodeURIComponent(item.id)}/approve`,
          {}
        )
        const note = res.executed ? res.result?.message || 'Approved and carried out.' : res.message || 'Approved.'
        setDecided((d) => ({ ...d, [item.id]: { kind, note } }))
      } else {
        await api.post(`/api/approvals/${encodeURIComponent(item.id)}/reject`, {})
        setDecided((d) => ({ ...d, [item.id]: { kind, note: 'Rejected. Nothing was changed.' } }))
      }
      setDecidedItems((items) => [...items.filter((i) => i.id !== item.id), item])
      onDecided(kind)
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'That did not work. Try again.')
    } finally {
      setBusyId(null)
    }
  }

  const pending = inbox?.to_decide ?? []
  const waiting = [...pending, ...decidedItems.filter((d) => !pending.some((p) => p.id === d.id))]
  const mine = (inbox?.mine ?? []).slice(0, 8)
  return (
    <div
      role="dialog"
      aria-label="Approvals"
      className="absolute right-0 top-full z-40 mt-2 w-[min(26rem,calc(100vw-2rem))] rounded-2xl border border-border bg-background shadow-xl"
    >
      <div className="max-h-[70vh] overflow-y-auto px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-[15px] font-semibold text-primary">Approvals</p>
          <button type="button" onClick={onClose} aria-label="Close approvals" className="rounded p-1 text-muted hover:text-primary">
            <X size={16} />
          </button>
        </div>
        {error ? <p className="mb-2 text-[13px] text-red-600">{error}</p> : null}
        {inbox === null ? (
          <p className="text-[14px] text-muted">Loading…</p>
        ) : (
          <div className="flex flex-col gap-4">
            {inbox.can_decide ? (
              <section>
                <p className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-secondary">
                  <Clock size={13} /> Waiting for you ({waiting.filter((a) => !decided[a.id]).length})
                </p>
                {waiting.length === 0 ? (
                  <p className="text-[14px] text-muted">Nothing is waiting for your approval.</p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {waiting.map((item) => (
                      <Card key={item.id} item={item} decided={decided[item.id]} busy={busyId === item.id}
                            onDecide={(kind) => void decide(item, kind)} />
                    ))}
                  </div>
                )}
              </section>
            ) : null}
            <section>
              <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-secondary">Your requests</p>
              {mine.length === 0 ? (
                <p className="text-[14px] text-muted">You have no recent requests.</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {mine.map((item) => (
                    <Card key={item.id} item={item} />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
