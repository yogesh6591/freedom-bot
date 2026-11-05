'use client'

/**
 * Approvals inside the chat: a bell in the chat header with the number of requests waiting for this user,
 * and a dropdown under it with one short line per request (the full text is in the line's tooltip).
 *
 * The bell polls GET /api/approvals/inbox. Approving or rejecting calls the normal approval endpoints, so
 * every server-side check (approver role, no self-approval, execute rights) still applies; the UI only
 * shows the result straight away.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Bell, BellRing, CheckCircle2, Clock, Loader2, Search, X, XCircle } from 'lucide-react'
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
  content?: { headline?: string; line?: string; summary?: string; fields?: { label: string; value: string }[] }
}

interface Inbox {
  can_decide: boolean
  to_decide: ApprovalItem[]
  mine: ApprovalItem[]
}

/** Web research started from the chat: one batch of prospects, researched one at a time. */
export interface ResearchInfo {
  batch: string
  total: number
  done: number
  finished: { name: string; verdict: string; priority: number | null }[]
  failed: string[]
  running: string[]
  waiting: string[]
}

function researchActive(r: ResearchInfo | null): boolean {
  return !!r && r.running.length + r.waiting.length > 0
}

/** Outcome shown on a card right after this user decided it. */
type Decided = { kind: 'approved' | 'rejected'; note: string }

const POLL_MS = 10_000
const DONE = new Set(['APPROVED', 'EXECUTING', 'COMPLETED', 'REJECTED', 'FAILED', 'CANCELLED'])

function statusLabel(status: string): { text: string; tone: string } {
  switch (status) {
    case 'PENDING_APPROVAL':
      return { text: 'Waiting', tone: 'bg-amber-500/10 text-amber-700' }
    case 'APPROVED':
      return { text: 'Approved', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'EXECUTING':
      return { text: 'Running', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'COMPLETED':
      return { text: 'Done', tone: 'bg-emerald-500/10 text-emerald-700' }
    case 'CANCELLED':
      return { text: 'Cancelled', tone: 'bg-black/5 text-secondary' }
    case 'REJECTED':
      return { text: 'Rejected', tone: 'bg-red-500/10 text-red-600' }
    case 'FAILED':
      return { text: 'Failed', tone: 'bg-red-500/10 text-red-600' }
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
  const [research, setResearch] = useState<ResearchInfo | null>(null)
  const [researchFresh, setResearchFresh] = useState(false)
  const wasActive = useRef(false)

  const refreshResearch = useCallback(async () => {
    try {
      const next = await api.get<ResearchInfo | null>('/api/approvals/research-progress')
      setResearch(next)
      const active = researchActive(next)
      if (wasActive.current && !active) setResearchFresh(true)
      wasActive.current = active
    } catch {
      // The research card stays as it was; the next poll tries again.
    }
  }, [])

  const refresh = useCallback(async () => {
    void refreshResearch()
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

  // While people are being researched, look more often so the card moves as each one finishes.
  const researching = researchActive(research)
  useEffect(() => {
    if (!enabled || !researching) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshResearch()
    }, 4000)
    return () => window.clearInterval(timer)
  }, [enabled, researching, refreshResearch])

  useEffect(() => {
    if (!flash) return
    const timer = window.setTimeout(() => setFlash(null), 4000)
    return () => window.clearTimeout(timer)
  }, [flash])

  return {
    inbox,
    fresh,
    flash,
    research,
    researchFresh,
    refresh,
    markSeen: () => {
      setFresh(0)
      setResearchFresh(false)
    },
    showFlash: (kind: Decided['kind']) => setFlash(kind)
  }
}

export function ApprovalBell({
  waiting,
  fresh,
  flash,
  research,
  researchFresh,
  onClick
}: {
  waiting: number
  fresh: number
  flash: Decided['kind'] | null
  research?: ResearchInfo | null
  researchFresh?: boolean
  onClick: () => void
}) {
  const busyResearch = researchActive(research ?? null)
  const label =
    flash === 'approved'
      ? 'Approved'
      : flash === 'rejected'
        ? 'Rejected'
        : waiting
          ? `${waiting} waiting for your approval`
          : busyResearch && research
            ? `Research ${research.done} of ${research.total} done`
            : researchFresh && research
              ? `Research done: ${research.done} of ${research.total}`
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
      ) : busyResearch ? (
        <Loader2 size={15} className="animate-spin" aria-hidden />
      ) : (
        <Bell size={15} aria-hidden />
      )}
      <span className="hidden sm:inline">
        {flash || (!waiting && (busyResearch || researchFresh) && research) ? label : 'Approvals'}
      </span>
      {!flash && waiting > 0 ? (
        <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-[11px] font-bold text-white">
          {waiting}
        </span>
      ) : !flash && (fresh > 0 || (researchFresh && !busyResearch)) ? (
        <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white bg-emerald-500" aria-hidden />
      ) : null}
    </button>
  )
}

/** The server's one-line summary ("Memory conflict on goal: Which goal is current?"). */
function oneLine(item: ApprovalItem): string {
  return (item.content?.line || item.content?.headline || item.title || item.tool).trim()
}

/** One short line per request: what it is, its status, and Approve / Reject when it waits for this user. */
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
  const status = decided
    ? decided.kind === 'approved'
      ? { text: 'Approved', tone: 'bg-emerald-500/10 text-emerald-700' }
      : { text: 'Rejected', tone: 'bg-red-500/10 text-red-600' }
    : statusLabel(item.status)
  const full = [
    oneLine(item),
    ...(item.content?.fields ?? []).slice(0, 6).map((f) => `${f.label}: ${f.value}`),
    item.requested_by_name ? `Requested by ${item.requested_by_name}` : '',
    decided?.note || (item.status === 'FAILED' ? item.error : '') || ''
  ].filter(Boolean).join('\n')
  return (
    <div
      title={full}
      className="flex items-center gap-2 rounded-lg border border-black/5 bg-white/80 px-2.5 py-1.5 dark:border-white/10 dark:bg-white/5"
    >
      <p className="min-w-0 flex-1 truncate text-[13px] font-medium text-primary">{oneLine(item)}</p>
      {onDecide && !decided ? (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('approved')}
            aria-label={`Approve: ${item.content?.headline || item.title}`}
            title="Approve"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-60"
          >
            <CheckCircle2 size={15} aria-hidden />
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide('rejected')}
            aria-label={`Reject: ${item.content?.headline || item.title}`}
            title="Reject"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-border text-secondary hover:border-red-300 hover:text-red-600 disabled:opacity-60"
          >
            <XCircle size={15} aria-hidden />
          </button>
        </>
      ) : (
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${status.tone}`}>{status.text}</span>
      )}
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
        research={approvals.research}
        researchFresh={approvals.researchFresh}
        onClick={() => {
          setOpen((o) => !o)
          approvals.markSeen()
          void approvals.refresh()
        }}
      />
      {open && (
        <ApprovalsPanel
          inbox={approvals.inbox}
          research={approvals.research}
          onClose={() => setOpen(false)}
          onChanged={() => void approvals.refresh()}
          onDecided={(kind) => approvals.showFlash(kind)}
        />
      )}
    </div>
  )
}

/** One card for the latest research batch; it changes in place as people finish. */
function ResearchCard({ research }: { research: ResearchInfo }) {
  const active = researchActive(research)
  const percent = research.total ? Math.round((research.done / research.total) * 100) : 0
  const ranked = [...research.finished].sort((a, b) => (b.priority ?? -1) - (a.priority ?? -1))
  return (
    <div className="rounded-lg border border-black/5 bg-white/80 px-2.5 py-2 dark:border-white/10 dark:bg-white/5">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[13px] font-medium text-primary">
          {active ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <CheckCircle2 size={14} aria-hidden />}
          {active ? 'Researching' : 'Research done'}: {research.done} of {research.total}
        </p>
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-black/5 dark:bg-white/10" aria-hidden>
        <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${percent}%` }} />
      </div>
      <ul className="mt-2 flex flex-col gap-1 text-[12.5px] text-secondary">
        {research.running.map((n) => (
          <li key={`r-${n}`} className="flex items-center gap-1.5">
            <Search size={12} aria-hidden /> <span className="truncate">{n}</span>
            <span className="ml-auto shrink-0 text-[11.5px] text-amber-700">Researching now</span>
          </li>
        ))}
        {research.waiting.map((n) => (
          <li key={`w-${n}`} className="flex items-center gap-1.5">
            <Clock size={12} aria-hidden /> <span className="truncate">{n}</span>
            <span className="ml-auto shrink-0 text-[11.5px]">Waiting</span>
          </li>
        ))}
        {research.failed.map((n) => (
          <li key={`f-${n}`} className="flex items-center gap-1.5">
            <XCircle size={12} aria-hidden /> <span className="truncate">{n}</span>
            <span className="ml-auto shrink-0 text-[11.5px] text-red-600">Failed</span>
          </li>
        ))}
        {ranked.map((f) => (
          <li key={`d-${f.name}`} className="flex items-center gap-1.5" title={f.verdict}>
            <CheckCircle2 size={12} aria-hidden /> <span className="truncate">{f.name}</span>
            <span className="ml-auto shrink-0 text-[11.5px] text-emerald-700">
              {f.verdict.startsWith('suitable') ? 'Suitable' : 'Not suitable'}
              {f.priority !== null ? ` · ${Math.round(f.priority)}` : ''}
            </span>
          </li>
        ))}
      </ul>
      {active ? <p className="mt-1.5 text-[11.5px] text-muted">People in research cannot be emailed until they are done.</p> : null}
    </div>
  )
}

function ApprovalsPanel({
  inbox,
  research,
  onClose,
  onChanged,
  onDecided
}: {
  inbox: Inbox | null
  research: ResearchInfo | null
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
  const mine = (inbox?.mine ?? []).slice(0, 5)
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
          <div className="flex flex-col gap-3">
            {research ? (
              <section>
                <p className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-secondary">
                  <Search size={13} /> Research
                </p>
                <ResearchCard research={research} />
              </section>
            ) : null}
            {inbox.can_decide ? (
              <section>
                <p className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-wide text-secondary">
                  <Clock size={13} /> Waiting for you ({waiting.filter((a) => !decided[a.id]).length})
                </p>
                {waiting.length === 0 ? (
                  <p className="text-[14px] text-muted">Nothing is waiting for your approval.</p>
                ) : (
                  <div className="flex flex-col gap-1.5">
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
                <div className="flex flex-col gap-1.5">
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
