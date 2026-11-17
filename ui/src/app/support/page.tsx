'use client'

/**
 * Support. Cepoch (technical, first layer) and Jeanne (business) work their own queues here; nobody
 * else does. Everyone else, workspace admins included, raises issues and follows the ones they raised.
 */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { SupportDesk, STATUS_LABEL, statusTone, type Team, type Ticket } from '@/components/support/SupportDesk'
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
  inputClass,
  timestamp
} from '@/components/common/primitives'

interface Me {
  is_staff: boolean
  team: Team | null
  team_label?: string
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

