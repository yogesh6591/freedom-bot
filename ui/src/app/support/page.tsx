'use client'

/** FB-046 — Cepoch support inbox. Jeanne only via escalate. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  Chip,
  EmptyState,
  ErrorNote,
  Field,
  LoadingBlock,
  PageStack,
  Panel,
  inputClass,
  timestamp
} from '@/components/common/primitives'

interface Ticket {
  id: string
  subject: string
  body: string
  severity: string
  status: string
  assignee: string
  escalate_reason?: string | null
  updated_at?: string | null
  history: { at: string; by: string; event: string; note: string }[]
}

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'CEPOCH', label: 'Cepoch queue' },
  { value: 'JEANNE', label: 'Needs Jeanne' }
]

const ESCALATE = [
  { value: 'CEO_JUDGMENT', label: 'CEO-level judgment' },
  { value: 'RELATIONSHIP_RISK', label: 'Relationship risk' },
  { value: 'AT_RISK', label: 'At-risk account' }
]

export default function SupportPage() {
  return (
    <AppShell>
      <SupportView />
    </AppShell>
  )
}

function SupportView() {
  const [filter, setFilter] = useState('CEPOCH')
  const [items, setItems] = useState<Ticket[]>([])
  const [selected, setSelected] = useState<Ticket | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [note, setNote] = useState('')
  const [escalateReason, setEscalateReason] = useState('CEO_JUDGMENT')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const path =
        filter === 'JEANNE'
          ? '/api/support/tickets?needs_jeanne=true'
          : filter
            ? `/api/support/tickets?assignee=${filter}`
            : '/api/support/tickets'
      const data = await api.get<{ items: Ticket[] }>(path)
      setItems(data.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load support')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    void load()
  }, [load])

  async function createTicket(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await api.post('/api/support/tickets', { subject, body, severity: 'NORMAL' })
      setSubject('')
      setBody('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed')
    } finally {
      setBusy(false)
    }
  }

  async function refreshSelected(id: string) {
    const data = await api.get<{ ticket: Ticket }>(`/api/support/tickets/${id}`)
    setSelected(data.ticket)
    await load()
  }

  return (
    <PageStack>
      <Panel
        title="Support (Cepoch)"
        description="Routine tickets stay with us. Pull Jeanne only for CEO-level judgment, relationship risk, or at-risk accounts — not a sales desk."
      >
        <div className="flex flex-wrap gap-2" role="group" aria-label="Queue filter">
          {FILTERS.map((option) => (
            <Chip
              key={option.value || 'all'}
              active={filter === option.value}
              onClick={() => setFilter(option.value)}
            >
              {option.label}
            </Chip>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel title="Open a routine ticket">
        <form className="space-y-3" onSubmit={(e) => void createTicket(e)}>
          <Field label="Subject">
            <input
              className={inputClass}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              required
            />
          </Field>
          <Field label="Details">
            <textarea
              className={`${inputClass} min-h-[72px]`}
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
          </Field>
          <Btn type="submit" size="sm" disabled={busy || !subject.trim()}>
            Create (routes to Cepoch)
          </Btn>
        </form>
      </Panel>

      {loading ? (
        <LoadingBlock rows={4} />
      ) : items.length === 0 ? (
        <EmptyState>No tickets in this queue.</EmptyState>
      ) : (
        <div className="space-y-3">
          {items.map((ticket) => (
            <button
              key={ticket.id}
              type="button"
              onClick={() => void refreshSelected(ticket.id)}
              className="w-full rounded-xl border border-border bg-background-secondary p-4 text-left shadow-panel ui-transition hover:border-brand-soft"
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={ticket.assignee === 'JEANNE' ? 'warn' : 'neutral'}>
                  {ticket.assignee}
                </Badge>
                <Badge tone="info">{ticket.status}</Badge>
                <Badge tone={ticket.severity === 'CRITICAL' ? 'bad' : 'neutral'}>
                  {ticket.severity}
                </Badge>
                <span className="font-geist text-sm font-semibold text-primary">
                  {ticket.subject}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">{timestamp(ticket.updated_at)}</p>
            </button>
          ))}
        </div>
      )}

      {selected && (
        <Panel
          title={selected.subject}
          description={`${selected.assignee} · ${selected.status}`}
          actions={
            <Btn variant="ghost" size="sm" onClick={() => setSelected(null)}>
              Close
            </Btn>
          }
        >
          <p className="type-muted mb-4 whitespace-pre-wrap">{selected.body || '—'}</p>
          {selected.escalate_reason && (
            <p className="mb-3 text-xs text-brand-soft">
              Escalate reason: {selected.escalate_reason}
            </p>
          )}

          <div className="mb-4 space-y-2 border-t border-border pt-4">
            <p className="type-caption">Shared history</p>
            <ul className="space-y-2 text-xs text-muted">
              {(selected.history || []).map((entry, index) => (
                <li key={`${entry.at}-${index}`}>
                  <span className="text-primary">{entry.event}</span> — {entry.note}{' '}
                  <span className="opacity-70">({timestamp(entry.at)})</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex flex-wrap gap-2 border-t border-border pt-4">
            <Field label="Add note">
              <input
                className={inputClass}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </Field>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Btn
              size="sm"
              variant="secondary"
              disabled={busy || !note.trim()}
              onClick={async () => {
                setBusy(true)
                try {
                  await api.post(`/api/support/tickets/${selected.id}/notes`, { note })
                  setNote('')
                  await refreshSelected(selected.id)
                } finally {
                  setBusy(false)
                }
              }}
            >
              Save note
            </Btn>
            <Btn
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await api.post(`/api/support/tickets/${selected.id}/status`, {
                    status: 'IN_PROGRESS'
                  })
                  await refreshSelected(selected.id)
                } finally {
                  setBusy(false)
                }
              }}
            >
              Mark in progress
            </Btn>
            <Btn
              size="sm"
              variant="positive"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  await api.post(`/api/support/tickets/${selected.id}/status`, {
                    status: 'RESOLVED'
                  })
                  await refreshSelected(selected.id)
                } finally {
                  setBusy(false)
                }
              }}
            >
              Resolve
            </Btn>
          </div>

          {selected.assignee !== 'JEANNE' && (
            <div className="mt-6 space-y-3 rounded-xl border border-brand/30 bg-brand/10 p-4">
              <p className="text-xs text-brand-soft">
                Escalate to Jeanne only for exception reasons — not routine work.
              </p>
              <Field label="Reason">
                <select
                  className={inputClass}
                  value={escalateReason}
                  onChange={(e) => setEscalateReason(e.target.value)}
                >
                  {ESCALATE.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Btn
                size="sm"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    await api.post(`/api/support/tickets/${selected.id}/escalate`, {
                      reason: escalateReason,
                      note: note || undefined
                    })
                    setNote('')
                    await refreshSelected(selected.id)
                  } catch (err) {
                    setError(err instanceof Error ? err.message : 'Escalate failed')
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                Escalate to Jeanne
              </Btn>
            </div>
          )}

          {selected.assignee === 'JEANNE' && (
            <div className="mt-4">
              <Btn
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    await api.post(`/api/support/tickets/${selected.id}/return-to-cepoch`, {
                      note: 'Returned after Jeanne review'
                    })
                    await refreshSelected(selected.id)
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                Return to Cepoch
              </Btn>
            </div>
          )}
        </Panel>
      )}
    </PageStack>
  )
}
