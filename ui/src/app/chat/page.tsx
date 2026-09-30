'use client'

/**
 * Chat.
 *
 * One company conversation (FB-034): there is no domain menu. The server routes
 * each turn to the domain it is about, inside one session, so context carries
 * from strategy to operations to finance.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '@/components/AppShell'
import { api, ApiError } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import type {
  ActionRecord,
  ChatContext,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionSummary
} from '@/lib/types'
import {
  Badge,
  Btn,
  ErrorNote,
  modeTone,
  riskTone
} from '@/components/common/primitives'
import MarkdownRenderer from '@/components/ui/typography/MarkdownRenderer'

interface Turn {
  role: 'user' | 'assistant'
  content: string
  response?: ChatResponse
}

export default function ChatPage() {
  return (
    <AppShell>
      <ChatView />
    </AppShell>
  )
}

/** Rebuild chat turns from a saved conversation (M01-11). */
function turnsFrom(detail: ChatSessionDetail): Turn[] {
  return detail.messages.map((m) =>
    m.role === 'user'
      ? { role: 'user', content: m.content }
      : {
          role: 'assistant',
          content: m.content,
          response: {
            session_id: detail.session_id,
            content: m.content,
            mode: (m.meta.mode ?? 'WAIT_FOR_APPROVAL') as ChatResponse['mode'],
            domain: m.domain ?? undefined,
            tool_activity: m.meta.tool_activity ?? [],
            actions: [],
            awaiting_approval: (m.meta.awaiting_approval ?? []) as ActionRecord[],
            drafts: (m.meta.drafts ?? []) as ActionRecord[]
          }
        }
  )
}

function whenLabel(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? ''
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function ChatView() {
  const { me } = useSession()
  // The open conversation is remembered per workspace and per person, so a
  // refresh or a visit to another page comes back to it (M01-11).
  const storageKey = me ? `bizos:chat:${me.client.id}:${me.user.id}` : null
  const [history, setHistory] = useState<ChatSessionSummary[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [lastDomain, setLastDomain] = useState<string | undefined>()
  const [context, setContext] = useState<ChatContext | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [alertsOpen, setAlertsOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const refreshContext = useCallback(async () => {
    try {
      const data = await api.get<ChatContext>('/api/chat/context?domain=auto')
      setContext(data)
    } catch {
      // Context is informational; chat still works without it.
    }
  }, [])

  useEffect(() => {
    void refreshContext()
  }, [refreshContext])

  const remember = useCallback(
    (id: string | undefined) => {
      if (!storageKey) return
      try {
        if (id) localStorage.setItem(storageKey, id)
        else localStorage.removeItem(storageKey)
      } catch {
        // Storage blocked: the conversation list still reopens it.
      }
    },
    [storageKey]
  )

  const loadHistory = useCallback(async () => {
    try {
      const data = await api.get<{ items: ChatSessionSummary[] }>('/api/chat/sessions')
      setHistory(data.items)
      setHistoryError(null)
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Could not load conversations')
    }
  }, [])

  const openSession = useCallback(
    async (id: string) => {
      setRestoring(true)
      setError(null)
      try {
        const detail = await api.get<ChatSessionDetail>(
          `/api/chat/sessions/${encodeURIComponent(id)}`
        )
        const restored = turnsFrom(detail)
        setTurns(restored)
        setSessionId(detail.session_id)
        const lastReply = [...restored].reverse().find((t) => t.role === 'assistant')
        setLastDomain(lastReply?.response?.domain)
        remember(detail.session_id)
        setHistoryOpen(false)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          remember(undefined)
        } else {
          setError(err instanceof Error ? err.message : 'Could not open conversation')
        }
      } finally {
        setRestoring(false)
      }
    },
    [remember]
  )

  // Reopen the conversation this person had open in this workspace.
  useEffect(() => {
    if (!storageKey) return
    let saved: string | null = null
    try {
      saved = localStorage.getItem(storageKey)
    } catch {
      saved = null
    }
    if (saved) void openSession(saved)
    void loadHistory()
  }, [storageKey, openSession, loadHistory])

  function startNewConversation() {
    setSessionId(undefined)
    setLastDomain(undefined)
    setTurns([])
    setError(null)
    remember(undefined)
  }

  useEffect(() => {
    const reduce =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    endRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth' })
  }, [turns, busy])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const message = input.trim()
    if (!message || busy) return
    setInput('')
    setBusy(true)
    setError(null)
    setTurns((prev) => [...prev, { role: 'user', content: message }])
    try {
      const response = await api.post<ChatResponse>('/api/chat', {
        message,
        session_id: sessionId,
        domain: 'auto',
        previous_domain: lastDomain
      })
      setSessionId(response.session_id)
      remember(response.session_id)
      setLastDomain(response.domain)
      setTurns((prev) => [
        ...prev,
        { role: 'assistant', content: response.content, response }
      ])
      void refreshContext()
      void loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chat failed')
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  const alerts = context?.inefficiency_alerts ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex shrink-0 flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="type-subtitle">
              {context?.assistant_name ?? 'FreedomBot'}
            </h1>
            {context && <Badge tone={modeTone(context.mode)}>{context.mode}</Badge>}
            {context && context.pending_approvals > 0 && (
              <Link
                href="/approvals"
                className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80"
              >
                <Badge tone="warn">{context.pending_approvals} awaiting approval</Badge>
              </Link>
            )}
          </div>
          <p className="type-muted truncate">
            {context?.mode_description ||
              'Ask anything — routing, memory, and approvals stay behind the scenes.'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Btn
            variant="secondary"
            size="sm"
            aria-expanded={historyOpen}
            onClick={() => {
              setHistoryOpen((open) => !open)
              if (!historyOpen) void loadHistory()
            }}
          >
            Conversations{history.length ? ` (${history.length})` : ''}
          </Btn>
          {sessionId && (
            <Btn variant="secondary" size="sm" onClick={startNewConversation}>
              New conversation
            </Btn>
          )}
        </div>
      </div>

      {historyOpen && (
        <div className="max-h-64 shrink-0 overflow-y-auto rounded-xl border border-border bg-background-secondary p-2 shadow-panel">
          {historyError && <ErrorNote>{historyError}</ErrorNote>}
          {!historyError && history.length === 0 && (
            <p className="px-2 py-3 type-muted">No saved conversations yet.</p>
          )}
          <ul className="space-y-1">
            {history.map((item) => (
              <li key={item.session_id}>
                <button
                  type="button"
                  onClick={() => void openSession(item.session_id)}
                  aria-current={item.session_id === sessionId ? 'true' : undefined}
                  className={`flex min-h-11 w-full flex-col items-start rounded-lg px-3 py-2 text-left ui-transition hover:bg-background-elevated ${
                    item.session_id === sessionId ? 'bg-background-elevated' : ''
                  }`}
                >
                  <span className="w-full truncate font-geist text-sm text-primary">
                    {item.title}
                  </span>
                  <span className="type-caption">
                    {whenLabel(item.updated_at)} · {item.turns}{' '}
                    {item.turns === 1 ? 'message' : 'messages'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {alerts.length > 0 && (
        <div className="shrink-0 rounded-xl border border-brand/30 bg-brand/10 px-4 py-3">
          <button
            type="button"
            onClick={() => setAlertsOpen((v) => !v)}
            aria-expanded={alertsOpen}
            className="flex min-h-11 w-full items-center justify-between gap-2 text-left ui-transition"
          >
            <span className="type-caption text-brand-soft/90">
              Inefficiency alerts · directional only · {alerts.length}
            </span>
            <span className="text-xs text-brand-soft/80">{alertsOpen ? 'Hide' : 'Show'}</span>
          </button>
          {alertsOpen && (
            <div className="mt-3 space-y-3 border-t border-brand/20 pt-3">
              {alerts.slice(0, 2).map((alert) => (
                <div key={alert.id} className="space-y-1 text-sm">
                  <p className="font-geist font-medium text-primary">{alert.title}</p>
                  <p className="type-muted">{alert.detail}</p>
                  {alert.directional_cost && (
                    <p className="text-xs text-brand-soft/80">{alert.directional_cost}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-border bg-background-secondary p-4 shadow-panel sm:p-6"
        aria-live="polite"
      >
        {restoring && turns.length === 0 && (
          <p className="type-muted" role="status">
            Reopening your conversation…
          </p>
        )}
        {!restoring && turns.length === 0 && (
          <div className="flex min-h-[20rem] flex-col items-center justify-center gap-6 px-4 py-12 text-center">
            <span
              className="flex h-16 w-16 items-center justify-center rounded-xl bg-brand text-xl font-bold text-brand-ink shadow-glow"
              aria-hidden
            >
              FB
            </span>
            <div className="max-w-prose space-y-3">
              <h2 className="type-title text-2xl sm:text-[1.75rem]">Ask FreedomBot</h2>
              <p className="type-muted">
                One company AI — strategy, ops, and finance in the same conversation.
                Routing, memory, and human approval happen behind the scenes.
              </p>
              <p className="text-xs text-muted/80">
                Recorded answers show as{' '}
                <strong className="text-positive">Fact</strong>
                {' · '}
                anything unapproved as{' '}
                <strong className="text-brand-soft">Estimate</strong>
              </p>
            </div>
            <div className="flex max-w-prose flex-wrap justify-center gap-2">
              {[
                'What’s our refund period?',
                'Draft an invoice for Acme Logistics',
                'What needs my approval?'
              ].map((prompt) => (
                <Btn
                  key={prompt}
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setInput(prompt)
                    inputRef.current?.focus()
                  }}
                >
                  {prompt}
                </Btn>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-6">
          {turns.map((turn, index) =>
            turn.role === 'user' ? (
              <div key={index} className="flex justify-end animate-fade-up">
                <div className="max-w-[min(85%,70ch)] whitespace-pre-wrap break-words rounded-xl rounded-br-sm bg-brand px-4 py-3 font-geist text-sm leading-relaxed text-brand-ink shadow-panel">
                  {turn.content}
                </div>
              </div>
            ) : (
              <div key={index} className="space-y-2 animate-fade-up">
                <div className="w-full max-w-prose-chat break-words rounded-xl rounded-bl-sm border border-border bg-background-elevated/80 px-4 py-4 text-primary shadow-panel sm:px-5">
                  <MarkdownRenderer classname="!max-w-none gap-y-2 prose-p:my-1 prose-li:my-0.5 text-sm leading-relaxed">
                    {turn.content}
                  </MarkdownRenderer>
                </div>
                {turn.response?.domain && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-background-wash px-2.5 py-1 type-caption">
                    Handled as: {turn.response.domain}
                  </span>
                )}
                <TurnSignals response={turn.response} />
              </div>
            )
          )}
          {busy && (
            <div className="space-y-2" role="status" aria-label="Thinking">
              <div className="h-3 w-24 animate-pulse rounded-full bg-background-elevated" />
              <div className="h-16 max-w-prose animate-pulse rounded-xl bg-background-elevated" />
              <span className="sr-only">Thinking…</span>
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <form
        onSubmit={send}
        className="flex shrink-0 items-center gap-2 rounded-full border border-border bg-background-secondary p-1.5 shadow-lift"
      >
        <label htmlFor="chat-input" className="sr-only">
          Message FreedomBot
        </label>
        <input
          id="chat-input"
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask FreedomBot…"
          disabled={busy}
          className="min-h-11 flex-1 rounded-full border-0 bg-transparent px-4 font-geist text-sm text-primary outline-none placeholder:text-muted/55 disabled:opacity-60"
        />
        <Btn type="submit" disabled={busy || !input.trim()} className="rounded-full px-6">
          Ask
        </Btn>
      </form>
    </div>
  )
}

function TurnSignals({ response }: { response?: ChatResponse }) {
  if (!response) return null
  const { tool_activity, awaiting_approval, drafts } = response
  if (
    tool_activity.length === 0 &&
    awaiting_approval.length === 0 &&
    drafts.length === 0
  )
    return null

  return (
    <div className="space-y-3 pl-0.5">
      {tool_activity.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">Used:</span>
          {tool_activity.map((activity, index) => (
            <Badge key={index} tone={activity.ok ? 'neutral' : 'bad'}>
              {activity.tool}
            </Badge>
          ))}
        </div>
      )}

      {awaiting_approval.map((action) => (
        <div
          key={action.id}
          className="rounded-xl border border-brand/50 bg-brand/15 px-4 py-3 text-sm"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="warn">Awaiting approval</Badge>
            <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
            <span className="font-geist font-semibold text-primary">{action.title}</span>
          </div>
          <p className="type-muted mt-2">{action.policy_reason}</p>
          <Link
            href="/approvals"
            className="mt-3 inline-flex min-h-11 items-center font-semibold text-brand-soft ui-transition hover:text-primary"
          >
            Open the approval queue →
          </Link>
        </div>
      ))}

      {drafts.map((action) => (
        <div
          key={action.id}
          className="rounded-xl border border-info/25 bg-info/5 px-4 py-3 text-sm"
        >
          <Badge tone="info">Draft prepared — nothing sent</Badge>
          <span className="ml-2 font-geist font-semibold text-primary">{action.title}</span>
        </div>
      ))}
    </div>
  )
}
