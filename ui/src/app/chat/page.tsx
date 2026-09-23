'use client'

/**
 * Chat.
 *
 * Shows what an ordinary user needs — the reply, and anything that now needs a
 * human — without the technical trace (§17). Tool activity is a compact strip of
 * names; the full redacted record lives in the Audit section for those permitted
 * to read it.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
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

interface ChatDraft {
  turns: Turn[]
  sessionId?: string
  domain: string
}

function chatStorageKey(clientId: string) {
  return `bizos.chat.${clientId}`
}

function loadChatDraft(clientId: string): ChatDraft | null {
  try {
    const raw = sessionStorage.getItem(chatStorageKey(clientId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as ChatDraft
    if (!parsed || !Array.isArray(parsed.turns)) return null
    return parsed
  } catch {
    return null
  }
}

function saveChatDraft(clientId: string, draft: ChatDraft) {
  try {
    sessionStorage.setItem(chatStorageKey(clientId), JSON.stringify(draft))
  } catch {
    // Ignore quota / private-mode failures; chat still works for the current page.
  }
}

export default function ChatPage() {
  return (
    <AppShell>
      <ChatView />
    </AppShell>
  )
}

function ChatView() {
  const { me } = useSession()
  const clientId = me?.client.id
  const [domain, setDomain] = useState('general')
  const [context, setContext] = useState<ChatContext | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [hydrated, setHydrated] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  // Restore this workspace's chat when returning from another sidebar page.
  useEffect(() => {
    if (!clientId) return
    const draft = loadChatDraft(clientId)
    if (draft) {
      setTurns(draft.turns)
      setSessionId(draft.sessionId)
      if (draft.domain) setDomain(draft.domain)
    }
    setHydrated(true)
  }, [clientId])

  useEffect(() => {
    if (!clientId || !hydrated) return
    saveChatDraft(clientId, { turns, sessionId, domain })
  }, [clientId, hydrated, turns, sessionId, domain])

  const loadContext = useCallback(async () => {
    try {
      setContext(await api.get<ChatContext>(`/api/chat/context?domain=${domain}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load chat context')
    }
  }, [domain])

  useEffect(() => {
    void loadContext()
  }, [loadContext])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const message = input.trim()
    if (!message || busy) return
    setInput('')
    setError(null)
    setTurns((prev) => [...prev, { role: 'user', content: message }])
    setBusy(true)
    try {
      const response = await api.post<ChatResponse>('/api/chat', {
        message,
        domain,
        session_id: sessionId
      })
      setSessionId(response.session_id)
      setTurns((prev) => [
        ...prev,
        { role: 'assistant', content: response.content, response }
      ])
      void loadContext()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The assistant could not reply')
    } finally {
      setBusy(false)
    }
  }

  const writeTools = context?.tools.filter((tool) => tool.write) ?? []

  return (
    <div className="mx-auto flex h-full max-w-4xl flex-col gap-4">
      <Panel
        title="Assistant"
        description={context?.mode_description}
        actions={
          <div className="flex items-center gap-2">
            {context && <Badge tone={modeTone(context.mode)}>{context.mode}</Badge>}
            <select
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              className="rounded-lg border border-border bg-background px-2 py-1 font-geist text-xs text-primary"
            >
              {(me?.client.enabled_domains ?? ['general']).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <div className="flex flex-wrap gap-2 text-[11px] text-muted">
          <span>
            {context?.tools.length ?? 0} tools available · {writeTools.length} can change
            things
          </span>
          {context && context.pending_approvals > 0 && (
            <Link href="/approvals">
              <Badge tone="warn">{context.pending_approvals} awaiting approval</Badge>
            </Link>
          )}
          {context && context.open_reviews > 0 && (
            <Link href="/reviews">
              <Badge tone="warn">{context.open_reviews} awaiting your answer</Badge>
            </Link>
          )}
        </div>
      </Panel>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {turns.length === 0 && (
          <Panel>
            <p className="text-xs text-muted">
              Ask about your pipeline, your recorded policies, or an upcoming meeting.
              What the assistant may actually <em>do</em> depends on this workspace&apos;s
              mode and your role — it will tell you when something needs approval.
            </p>
          </Panel>
        )}

        {turns.map((turn, index) =>
          turn.role === 'user' ? (
            <div key={index} className="flex justify-end">
              <div className="max-w-[80%] rounded-xl bg-background-secondary px-4 py-2 font-geist text-sm">
                {turn.content}
              </div>
            </div>
          ) : (
            <div key={index} className="space-y-2">
              <div className="rounded-xl border border-border bg-background-secondary/30 px-4 py-3">
                <MarkdownRenderer>{turn.content}</MarkdownRenderer>
              </div>
              <TurnSignals response={turn.response} />
            </div>
          )
        )}
        {busy && <p className="text-xs text-muted">Thinking…</p>}
        <div ref={endRef} />
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <form onSubmit={send} className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Ask a question, or describe what you want done"
          className="flex-1 rounded-xl border border-border bg-background px-4 py-3 font-geist text-sm outline-none placeholder:text-muted/60 focus:border-brand/60"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-brand px-5 py-3 font-geist text-sm text-white disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  )
}

/** Citations, tool activity, approvals and reviews raised by one turn. */
function TurnSignals({ response }: { response?: ChatResponse }) {
  if (!response) return null
  const { tool_activity, awaiting_approval, drafts, reviews } = response
  if (
    tool_activity.length === 0 &&
    awaiting_approval.length === 0 &&
    drafts.length === 0 &&
    reviews.length === 0
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
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <Badge tone="warn">Awaiting approval</Badge>
            <Badge tone={riskTone(action.risk_level)}>{action.risk_level}</Badge>
            <span className="font-geist text-primary">{action.title}</span>
          </div>
          <p className="mt-1 text-muted">{action.policy_reason}</p>
          <Link href="/approvals" className="mt-1 inline-block text-brand">
            Open the approval queue →
          </Link>
        </div>
      ))}

      {drafts.map((action) => (
        <div
          key={action.id}
          className="rounded-lg border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs"
        >
          <Badge tone="info">Draft prepared — nothing sent</Badge>
          <span className="ml-2 font-geist text-primary">{action.title}</span>
        </div>
      ))}

      {reviews.map((review) => (
        <div
          key={review.id}
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs"
        >
          <Badge tone="warn">Needs your answer</Badge>
          <span className="ml-2 font-geist text-primary">{review.question}</span>
          <Link href="/reviews" className="ml-2 text-brand">
            Answer →
          </Link>
        </div>
      ))}
    </div>
  )
}
