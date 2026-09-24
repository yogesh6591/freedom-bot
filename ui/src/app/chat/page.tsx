'use client'

/**
 * Chat.
 *
 * One company conversation (FB-034): there is no domain menu. The server routes
 * each turn to the domain it is about, inside one session, so context carries
 * from strategy to operations to finance.
 *
 * Shows what an ordinary user needs — the reply, and anything that now needs a
 * human — without the technical trace (§17). Tool activity is a compact strip of
 * names; the full redacted record lives in the Audit section for those permitted
 * to read it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '@/components/AppShell'
import { api } from '@/lib/api'
import type { ChatContext, ChatResponse } from '@/lib/types'
import {
  Badge,
  ErrorNote,
  Panel,
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

function ChatView() {
  const [lastDomain, setLastDomain] = useState<string | undefined>()
  const [context, setContext] = useState<ChatContext | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const endRef = useRef<HTMLDivElement>(null)

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

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
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
      setLastDomain(response.domain)
      setTurns((prev) => [
        ...prev,
        { role: 'assistant', content: response.content, response }
      ])
      void refreshContext()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chat failed')
    } finally {
      setBusy(false)
    }
  }

  const runnable = context?.tools.filter((t) => t.can_run) ?? []
  const writeTools = runnable.filter((t) => t.write)
  const viewOnly = (context?.tools.length ?? 0) - runnable.length

  return (
    <div className="flex h-[calc(100dvh-8.5rem)] min-h-[28rem] flex-col gap-4">
      <Panel
        className="border-0 bg-gradient-to-br from-brand/40 via-white to-background-elevated"
        title={context?.assistant_name ?? 'FreedomBot'}
        description={context?.mode_description}
        actions={
          <div className="flex items-center gap-2">
            {context && <Badge tone={modeTone(context.mode)}>{context.mode}</Badge>}
            {sessionId && (
              <button
                type="button"
                onClick={() => {
                  setSessionId(undefined)
                  setLastDomain(undefined)
                  setTurns([])
                }}
                className="rounded-full border border-border bg-white px-3 py-1.5 font-geist text-xs font-medium text-muted transition hover:border-brand-deep hover:bg-brand/20 hover:text-primary"
              >
                New conversation
              </button>
            )}
          </div>
        }
      >
        <div className="flex flex-wrap gap-2 text-xs leading-relaxed text-muted">
          <span>
            {runnable.length} tools you can run · {writeTools.length} can change things
            {viewOnly > 0 && ` · ${viewOnly} visible but not runnable for your role`}
          </span>
          {context && context.pending_approvals > 0 && (
            <Link href="/approvals">
              <Badge tone="warn">{context.pending_approvals} awaiting approval</Badge>
            </Link>
          )}
        </div>
      </Panel>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto rounded-2xl border border-border bg-white p-4 shadow-panel sm:p-5">
        {turns.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-4 py-16 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-brand text-lg font-bold text-brand-ink shadow-glow">
              FB
            </span>
            <p className="max-w-md text-sm leading-relaxed text-muted">
              One conversation for the whole company — move from strategy to operations to
              finance without repeating yourself. Answers mark recorded values as{' '}
              <strong className="text-positive">Fact</strong> and anything unapproved as{' '}
              <strong className="text-brand-deep">Estimate</strong>.
            </p>
          </div>
        )}

        {turns.map((turn, index) =>
          turn.role === 'user' ? (
            <div key={index} className="flex justify-end animate-fade-up">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-brand px-4 py-3 font-geist text-sm leading-relaxed text-brand-ink shadow-panel">
                {turn.content}
              </div>
            </div>
          ) : (
            <div key={index} className="space-y-2 animate-fade-up">
              <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-border bg-background-elevated/80 px-4 py-3.5 text-primary shadow-panel">
                <MarkdownRenderer>{turn.content}</MarkdownRenderer>
              </div>
              {turn.response?.domain && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-background-wash px-2.5 py-0.5 font-dmmono text-[10px] uppercase tracking-wider text-muted">
                  Handled as: {turn.response.domain}
                </span>
              )}
              <TurnSignals response={turn.response} />
            </div>
          )
        )}
        {busy && (
          <p className="animate-pulseSoft font-geist text-sm text-muted">Thinking…</p>
        )}
        <div ref={endRef} />
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <form
        onSubmit={send}
        className="flex gap-2 rounded-full border border-border bg-white p-1.5 shadow-lift"
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask a question, or describe what you want done"
          className="flex-1 rounded-full border-0 bg-transparent px-4 py-2.5 font-geist text-sm text-primary outline-none placeholder:text-muted/55"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-full bg-brand px-6 py-2.5 font-geist text-sm font-bold text-brand-ink shadow-glow transition hover:bg-brand-soft disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}

/** Tool activity, approvals and drafts raised by one turn. */
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
    <div className="space-y-2 pl-1">
      {tool_activity.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted">Used:</span>
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
          className="rounded-xl border border-brand/50 bg-brand/15 px-3.5 py-2.5 text-sm"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="warn">Awaiting approval</Badge>
            <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
            <span className="font-geist font-semibold text-primary">{action.title}</span>
          </div>
          <p className="mt-1.5 leading-relaxed text-muted">{action.policy_reason}</p>
          <Link
            href="/approvals"
            className="mt-2 inline-block font-semibold text-brand-deep transition hover:text-primary"
          >
            Open the approval queue →
          </Link>
        </div>
      ))}

      {drafts.map((action) => (
        <div
          key={action.id}
          className="rounded-xl border border-info/25 bg-info/5 px-3.5 py-2.5 text-sm"
        >
          <Badge tone="info">Draft prepared — nothing sent</Badge>
          <span className="ml-2 font-geist font-semibold text-primary">{action.title}</span>
        </div>
      ))}
    </div>
  )
}
