'use client'

/**
 * Support. Cepoch (technical, first layer) and Jeanne (business) work their own queues here; nobody
 * else does. Everyone else, workspace admins included, raises issues and follows the ones they raised.
 */

import { useCallback, useEffect, useState } from 'react'
import { Bell } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  EmptyState,
  ErrorNote,
  Field,
  LoadingBlock,
  PageStack,
  PageHeader,
  Panel,
  SegmentedTab,
  SegmentedTabs,
  inputClass,
  timestamp,
  type Tone
} from '@/components/common/primitives'

type Team = 'CEPOCH' | 'JEANNE'

interface HistoryEntry {
  at: string
  by: string
  event: string
  note: string
  team?: string
  from_team?: string
  internal?: boolean
}

interface TicketContext {
  workspace?: { name?: string; slug?: string }
  user?: { email?: string; name?: string; role?: string }
  question?: string
  flagged_answer?: string
  conversation?: { role: string; content: string; at?: string }[]
}

/** The full ticket (staff) or what the person who raised it sees (status and resolution). */
interface Ticket {
  id: string
  subject: string
  body?: string
  severity: string
  status: string
  assignee?: Team
  team_label?: string
  category?: string
  source?: string
  triage?: boolean
  resolution?: string
  created_at?: string | null
  updated_at?: string | null
  client?: { name: string; slug: string }
  context?: TicketContext
  history?: HistoryEntry[]
}

interface Me {
  is_staff: boolean
  team: Team | null
  team_label?: string
}

interface Notice {
  id: string
  ticket_id: string
  kind: string
  message: string
  created_at: string
  read_at: string | null
}

const STATUSES = [
  { value: 'OPEN', label: 'Open' },
  { value: 'IN_PROGRESS', label: 'In progress' },
  { value: 'RESOLVED', label: 'Resolved' },
  { value: 'CLOSED', label: 'Closed' }
]
const STATUS_LABEL: Record<string, string> = Object.fromEntries(
  STATUSES.map((s) => [s.value, s.label])
)
const SOURCE_LABEL: Record<string, string> = {
  AI_FLAG: 'Flagged AI answer',
  MANUAL: 'Escalate form',
  CHAT: 'Chat escalation'
}
const OTHER_TEAM: Record<Team, { team: Team; label: string }> = {
  CEPOCH: { team: 'JEANNE', label: 'Reassign to Jeanne' },
  JEANNE: { team: 'CEPOCH', label: 'Reassign to Cepoch' }
}

function statusTone(status: string): Tone {
  if (status === 'RESOLVED' || status === 'CLOSED') return 'good'
  if (status === 'IN_PROGRESS') return 'info'
  return 'warn'
}

function priorityTone(severity: string): Tone {
  return severity === 'CRITICAL' ? 'bad' : severity === 'HIGH' ? 'warn' : 'neutral'
}

export default function SupportPage() {
  return (
    <AppShell>
      <SupportView />
    </AppShell>
  )
}

function SupportView() {
  const [me, setMe] = useState<Me | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<Me>('/api/support/me')
      .then(setMe)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load support'))
  }, [])

  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!me) return <LoadingBlock rows={4} />
  return me.is_staff && me.team ? (
    <SupportDesk team={me.team} teamLabel={me.team_label || me.team} />
  ) : (
    <MyTickets />
  )
}

/* ------------------------------------------------------------------ requester */

function MyTickets() {
  const [items, setItems] = useState<Ticket[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [submitted, setSubmitted] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems((await api.get<{ items: Ticket[] }>('/api/support/tickets')).items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load your tickets')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function raise(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      const result = await api.post<{ message: string }>('/api/support/tickets', {
        subject,
        body
      })
      setSubmitted(result.message)
      setSubject('')
      setBody('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The issue was not raised')
    } finally {
      setBusy(false)
    }
  }

  return (
    <PageStack>
      <PageHeader
        eyebrow="Service desk"
        title="Support"
        description="Tell FreedomBot support about a problem or a request, and follow your tickets here. You can also flag any assistant answer in the chat."
      />
      {error && <ErrorNote>{error}</ErrorNote>}
      {submitted && (
        <p role="status" className="text-sm text-success">
          {submitted}
        </p>
      )}
      <Panel title="Escalate an issue">
        <form className="max-w-[720px] space-y-4" onSubmit={(e) => void raise(e)}>
          <Field label="Title">
            <input
              className={inputClass}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              required
            />
          </Field>
          <Field label="What happened">
            <textarea
              className={`${inputClass} min-h-[96px]`}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
          </Field>
          <Btn type="submit" size="sm" disabled={busy || !subject.trim()}>
            Send to support
          </Btn>
        </form>
      </Panel>
      {loading ? (
        <LoadingBlock rows={3} />
      ) : items.length === 0 ? (
        <EmptyState>You haven&apos;t raised any tickets yet.</EmptyState>
      ) : (
        <Panel title="My tickets">
          <ul className="space-y-3">
            {items.map((ticket) => (
              <li
                key={ticket.id}
                className="rounded-xl border border-border bg-background-secondary p-4"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={statusTone(ticket.status)}>
                    {STATUS_LABEL[ticket.status] ?? ticket.status}
                  </Badge>
                  <span className="font-geist text-sm font-semibold text-primary">
                    {ticket.subject}
                  </span>
                </div>
                {ticket.resolution && (
                  <p className="mt-2 whitespace-pre-wrap text-xs text-secondary">
                    Resolution: {ticket.resolution}
                  </p>
                )}
                <p className="mt-1 text-xs text-muted">
                  {ticket.id} · {timestamp(ticket.updated_at)}
                </p>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </PageStack>
  )
}

/* ------------------------------------------------------------------ staff desk */

function SupportDesk({ team, teamLabel }: { team: Team; teamLabel: string }) {
  const [view, setView] = useState<'team' | 'triage' | 'all'>('team')
  const [status, setStatus] = useState('')
  const [items, setItems] = useState<Ticket[]>([])
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notices, setNotices] = useState<{ unread: number; items: Notice[] }>({
    unread: 0,
    items: []
  })
  const [showNotices, setShowNotices] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const query = new URLSearchParams({ view })
      if (status) query.set('status', status)
      const [list, notes] = await Promise.all([
        api.get<{ items: Ticket[] }>(`/api/support/tickets?${query.toString()}`),
        api.get<{ unread: number; items: Notice[] }>('/api/support/notifications')
      ])
      setItems(list.items)
      setNotices(notes)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the queue')
    } finally {
      setLoading(false)
    }
  }, [view, status])

  useEffect(() => {
    void load()
  }, [load])

  async function open(id: string) {
    try {
      setSelected((await api.get<{ ticket: Ticket }>(`/api/support/tickets/${id}`)).ticket)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not open the ticket')
    }
  }

  async function markRead() {
    await api.post('/api/support/notifications/read')
    await load()
  }

  const views: { value: 'team' | 'triage' | 'all'; label: string }[] = [
    { value: 'team', label: team === 'CEPOCH' ? 'Technical (mine)' : 'Business (mine)' },
    ...(team === 'CEPOCH' ? [{ value: 'triage' as const, label: 'Triage' }] : []),
    { value: 'all', label: 'All tickets' }
  ]

  return (
    <PageStack>
      <PageHeader
        eyebrow="Service desk"
        title={`Support · ${teamLabel}`}
        description={
          team === 'CEPOCH'
            ? 'Technical issues from every client: AI answers, login, integrations, bugs. Hand business issues to Jeanne.'
            : 'Business and operational issues from every client. Hand technical issues to Cepoch.'
        }
        actions={
          <Btn
            variant="secondary"
            size="sm"
            onClick={() => setShowNotices((v) => !v)}
            aria-label={`${notices.unread} unread notifications`}
          >
            <Bell size={14} aria-hidden /> {notices.unread} new
          </Btn>
        }
      />
      {error && <ErrorNote>{error}</ErrorNote>}

      {showNotices && (
        <Panel
          title="Notifications"
          actions={
            notices.unread > 0 ? (
              <Btn variant="ghost" size="sm" onClick={() => void markRead()}>
                Mark all read
              </Btn>
            ) : undefined
          }
        >
          {notices.items.length === 0 ? (
            <p className="text-xs text-muted">Nothing yet.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {notices.items.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className={`text-left hover:underline ${n.read_at ? 'text-muted' : 'font-medium text-primary'}`}
                    onClick={() => void open(n.ticket_id)}
                  >
                    {n.message}
                  </button>{' '}
                  <span className="text-muted">· {timestamp(n.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <Panel title="Queue">
        <div className="flex flex-wrap items-center gap-3">
          <SegmentedTabs aria-label="Queue">
            {views.map((option) => (
              <SegmentedTab
                key={option.value}
                active={view === option.value}
                onClick={() => setView(option.value)}
              >
                {option.label}
              </SegmentedTab>
            ))}
          </SegmentedTabs>
          <select
            aria-label="Status"
            className={`${inputClass} w-auto`}
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">Any status</option>
            {STATUSES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </Panel>

      {loading ? (
        <LoadingBlock rows={4} />
      ) : items.length === 0 ? (
        <EmptyState>No tickets here.</EmptyState>
      ) : (
        <div className="space-y-2">
          {items.map((ticket) => (
            <button
              key={ticket.id}
              type="button"
              onClick={() => void open(ticket.id)}
              className={`ui-transition w-full rounded-xl border bg-background-secondary p-4 text-left shadow-panel hover:border-brand-soft ${
                selected?.id === ticket.id ? 'border-brand' : 'border-border'
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={statusTone(ticket.status)}>
                  {STATUS_LABEL[ticket.status] ?? ticket.status}
                </Badge>
                <Badge tone={priorityTone(ticket.severity)}>{ticket.severity}</Badge>
                {ticket.triage && <Badge tone="warn">Triage</Badge>}
                <span className="font-geist text-sm font-semibold text-primary">
                  {ticket.subject}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">
                {ticket.client?.name} · {ticket.category === 'BUSINESS' ? 'Business' : 'Technical'} ·{' '}
                {ticket.team_label} · {SOURCE_LABEL[ticket.source || ''] ?? ticket.source} ·{' '}
                {ticket.id} · {timestamp(ticket.updated_at)}
              </p>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <TicketDetail
          ticket={selected}
          onClose={() => setSelected(null)}
          onChanged={async (t) => {
            setSelected(t)
            await load()
          }}
          onError={setError}
        />
      )}
    </PageStack>
  )
}

function TicketDetail({
  ticket,
  onClose,
  onChanged,
  onError
}: {
  ticket: Ticket
  onClose: () => void
  onChanged: (ticket: Ticket) => Promise<void>
  onError: (message: string) => void
}) {
  const [status, setStatus] = useState(ticket.status)
  const [resolution, setResolution] = useState(ticket.resolution || '')
  const [note, setNote] = useState('')
  const [handover, setHandover] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setStatus(ticket.status)
    setResolution(ticket.resolution || '')
  }, [ticket])

  async function act(path: string, body: unknown, after?: () => void) {
    setBusy(true)
    try {
      const result = await api.post<{ ticket: Ticket }>(`/api/support/tickets/${ticket.id}/${path}`, body)
      after?.()
      await onChanged(result.ticket)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'The change was not saved')
    } finally {
      setBusy(false)
    }
  }

  const ctx = ticket.context || {}
  const other = ticket.assignee ? OTHER_TEAM[ticket.assignee] : undefined

  return (
    <Panel
      title={ticket.subject}
      description={`${ticket.id} · ${ticket.client?.name ?? ''} · with ${ticket.team_label}`}
      actions={
        <Btn variant="ghost" size="sm" onClick={onClose}>
          Close
        </Btn>
      }
    >
      <div className="grid gap-x-6 gap-y-1 text-xs text-secondary sm:grid-cols-2">
        <p>Category: {ticket.category === 'BUSINESS' ? 'Business' : 'Technical'}{ticket.triage ? ' (triage)' : ''}</p>
        <p>Priority: {ticket.severity}</p>
        <p>Status: {STATUS_LABEL[ticket.status] ?? ticket.status}</p>
        <p>Source: {SOURCE_LABEL[ticket.source || ''] ?? ticket.source}</p>
        <p>
          Raised by: {ctx.user?.name || ctx.user?.email || '—'}
          {ctx.user?.email ? ` (${ctx.user.email})` : ''}
        </p>
        <p>Workspace: {ctx.workspace?.name || ticket.client?.name} ({ctx.workspace?.slug || ticket.client?.slug})</p>
        <p>Opened: {timestamp(ticket.created_at)}</p>
        <p>Updated: {timestamp(ticket.updated_at)}</p>
      </div>

      <p className="type-muted mt-4 whitespace-pre-wrap">{ticket.body || '—'}</p>

      {(ctx.question || ctx.flagged_answer || (ctx.conversation?.length ?? 0) > 0) && (
        <div className="mt-4 space-y-2 border-t border-border pt-4">
          <p className="type-caption">Chat context</p>
          {ctx.question && (
            <p className="text-xs">
              <span className="text-muted">Question: </span>
              <span className="whitespace-pre-wrap text-primary">{ctx.question}</span>
            </p>
          )}
          {ctx.flagged_answer && (
            <p className="text-xs">
              <span className="text-muted">Flagged answer: </span>
              <span className="whitespace-pre-wrap text-primary">{ctx.flagged_answer}</span>
            </p>
          )}
          {(ctx.conversation?.length ?? 0) > 0 && (
            <details className="text-xs">
              <summary className="cursor-pointer text-muted">
                Conversation ({ctx.conversation?.length} messages)
              </summary>
              <ul className="mt-2 space-y-1.5">
                {ctx.conversation?.map((m, i) => (
                  <li key={i} className="whitespace-pre-wrap">
                    <span className="font-medium text-primary">
                      {m.role === 'user' ? 'User' : 'FreedomBot'}:
                    </span>{' '}
                    {m.content}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {ticket.resolution && (
        <p className="mt-4 whitespace-pre-wrap rounded-lg bg-success/10 p-3 text-xs text-primary">
          Resolution (the client sees this): {ticket.resolution}
        </p>
      )}

      <div className="mt-4 space-y-2 border-t border-border pt-4">
        <p className="type-caption">Activity (internal)</p>
        <ul className="space-y-1.5 text-xs text-muted">
          {(ticket.history || []).map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <span className="text-primary">{entry.event === 'note' ? 'Internal note' : entry.event}</span>{' '}
              — {entry.note} <span className="opacity-70">({entry.by}, {timestamp(entry.at)})</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-4 grid gap-4 border-t border-border pt-4 lg:grid-cols-3">
        <div className="space-y-2">
          <Field label="Status">
            <select className={inputClass} value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </Field>
          {(status === 'RESOLVED' || status === 'CLOSED') && (
            <Field label="Resolution (shown to the client)">
              <textarea
                className={`${inputClass} min-h-[64px]`}
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
              />
            </Field>
          )}
          <Btn
            size="sm"
            variant="secondary"
            disabled={busy || (status === ticket.status && resolution === (ticket.resolution || ''))}
            onClick={() => void act('status', { status, resolution })}
          >
            Save status
          </Btn>
        </div>
        <div className="space-y-2">
          <Field label="Internal note (never shown to the client)">
            <textarea
              className={`${inputClass} min-h-[64px]`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </Field>
          <Btn
            size="sm"
            variant="secondary"
            disabled={busy || !note.trim()}
            onClick={() => void act('notes', { note }, () => setNote(''))}
          >
            Add note
          </Btn>
        </div>
        {other && (
          <div className="space-y-2">
            <Field label="Hand-over note">
              <textarea
                className={`${inputClass} min-h-[64px]`}
                value={handover}
                onChange={(e) => setHandover(e.target.value)}
              />
            </Field>
            <Btn
              size="sm"
              disabled={busy}
              onClick={() =>
                void act('reassign', { team: other.team, note: handover }, () => setHandover(''))
              }
            >
              {other.label}
            </Btn>
          </div>
        )}
      </div>
    </Panel>
  )
}
