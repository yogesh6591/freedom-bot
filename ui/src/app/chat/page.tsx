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
import {
  ArrowUp,
  ChevronDown,
  ChevronUp,
  History,
  MessageSquareText,
  Plus,
  ShieldCheck,
  Sparkles
} from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
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
import { Badge, Btn, ErrorNote, riskTone } from '@/components/common/primitives'
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
            awaiting_approval: (m.meta.awaiting_approval ??
              []) as ActionRecord[],
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
    : date.toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short'
      })
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
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

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
    if (!historyOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setHistoryOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [historyOpen])

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
      const data = await api.get<{ items: ChatSessionSummary[] }>(
        '/api/chat/sessions'
      )
      setHistory(data.items)
      setHistoryError(null)
    } catch (err) {
      setHistoryError(
        err instanceof Error ? err.message : 'Could not load conversations'
      )
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
        const lastReply = [...restored]
          .reverse()
          .find((t) => t.role === 'assistant')
        setLastDomain(lastReply?.response?.domain)
        remember(detail.session_id)
        setHistoryOpen(false)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          remember(undefined)
        } else {
          setError(
            err instanceof Error ? err.message : 'Could not open conversation'
          )
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
    const el = listRef.current
    if (!el) return
    const reduce =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches
    // Scroll only the transcript pane — scrollIntoView was jumping the whole shell up.
    el.scrollTo({ top: el.scrollHeight, behavior: reduce ? 'auto' : 'smooth' })
  }, [turns, busy])

  async function send(event: React.FormEvent) {
    event.preventDefault()
    const message = input.trim()
    if (!message || busy) return
    setInput('')
    if (inputRef.current) inputRef.current.style.height = 'auto'
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
  const suggestions = [
    {
      label: 'Company context',
      question: 'Summarize our current priorities',
      icon: MessageSquareText
    },
    {
      label: 'Approvals',
      question: 'Which actions need my approval?',
      icon: ShieldCheck
    },
    {
      label: 'Get things done',
      question: 'Help me draft a customer follow-up',
      icon: Sparkles
    }
  ]

  return (
    <div className="relative flex h-full min-h-0 flex-1 overflow-hidden rounded-[14px] border border-border bg-background-secondary shadow-panel">
      {historyOpen && (
        <button
          type="button"
          aria-label="Close conversations"
          className="absolute inset-0 z-20 bg-[#14272e]/35"
          onClick={() => setHistoryOpen(false)}
        />
      )}
      <aside
        id="chat-conversations-panel"
        aria-label="Conversation history"
        aria-hidden={!historyOpen}
        className={`${
          historyOpen ? 'translate-x-0' : '-translate-x-full pointer-events-none'
        } absolute inset-y-0 left-0 z-30 flex w-[min(320px,88vw)] flex-col border-r border-border bg-[#f9fbfa] shadow-lift transition-transform duration-200 ease-out dark:bg-background-elevated`}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border px-4 py-4">
          <div>
            <p className="text-[13px] font-semibold text-primary">
              All conversations
            </p>
            <p className="mt-0.5 text-[11px] text-muted">Your recent work</p>
          </div>
          <button
            type="button"
            className="flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-background-secondary text-secondary hover:border-purple-300 hover:text-brand"
            title="New conversation"
            aria-label="New conversation"
            onClick={() => {
              startNewConversation()
              setHistoryOpen(false)
              inputRef.current?.focus()
            }}
          >
            <Plus size={17} />
          </button>
        </div>
        <div className="workspace-scroll min-h-0 flex-1 overflow-y-auto p-3">
          {historyError && <ErrorNote>{historyError}</ErrorNote>}
          {!historyError && history.length === 0 && (
            <p className="px-3 py-6 text-center text-[12px] leading-6 text-muted">
              Your conversations will appear here once you begin.
            </p>
          )}
          <div className="space-y-1">
            {history.map((item) => (
              <button
                key={item.session_id}
                type="button"
                onClick={() => void openSession(item.session_id)}
                aria-current={
                  item.session_id === sessionId ? 'true' : undefined
                }
                className={`w-full rounded-lg px-3 py-3 text-left transition-colors focus-visible:outline-brand ${
                  item.session_id === sessionId
                    ? 'border border-purple-200 bg-purple-50'
                    : 'border border-transparent hover:bg-background-elevated'
                }`}
              >
                <span
                  className={`block truncate text-[12px] font-semibold ${item.session_id === sessionId ? 'text-brand' : 'text-primary'}`}
                >
                  {item.title || 'Untitled conversation'}
                </span>
                <span className="mt-1 block text-[11px] text-muted">
                  {whenLabel(item.updated_at)}
                </span>
              </button>
            ))}
          </div>
        </div>
        <div className="border-t border-border px-4 py-3">
          <div className="flex items-start gap-2 text-[11px] leading-[1.65] text-muted">
            <ShieldCheck
              size={15}
              className="mt-0.5 shrink-0 text-brand"
              aria-hidden
            />
            <span>Conversations follow your workspace permissions.</span>
          </div>
        </div>
      </aside>

      <section
        className="flex min-w-0 flex-1 flex-col"
        aria-label="FreedomBot assistant"
      >
        <header className="flex min-h-[76px] shrink-0 items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-6 lg:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <BrandMark className="h-9 w-9 shrink-0 rounded-lg" />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h1 className="truncate text-[15px] font-semibold tracking-tight text-primary">
                  {context?.assistant_name ?? 'FreedomBot'}
                </h1>
              </div>
              <p className="truncate text-[11px] text-muted">
                Your company workspace assistant
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {context && context.pending_approvals > 0 && (
              <Link
                href="/approvals"
                className="hidden rounded-md focus-visible:outline-brand sm:inline-flex"
              >
                <Badge tone="warn">
                  {context.pending_approvals} pending approvals
                </Badge>
              </Link>
            )}
            <Btn
              variant="secondary"
              size="sm"
              className="!px-3"
              aria-expanded={historyOpen}
              aria-controls="chat-conversations-panel"
              onClick={() => {
                setHistoryOpen((open) => {
                  const next = !open
                  if (next) void loadHistory()
                  return next
                })
              }}
            >
              <History size={15} aria-hidden />
              <span className="hidden sm:inline">All conversations</span>
              <span className="sm:hidden">All</span>
              {history.length > 0 && (
                <span className="tabular-nums text-muted">({history.length})</span>
              )}
            </Btn>
            <Btn
              variant="secondary"
              size="sm"
              className="!px-3"
              onClick={startNewConversation}
            >
              <Plus size={15} aria-hidden />
              <span className="hidden sm:inline">New chat</span>
              <span className="sm:hidden">New</span>
            </Btn>
          </div>
        </header>

        {alerts.length > 0 && (
          <div className="shrink-0 border-b border-warning/20 bg-warning/5 px-4 py-2.5 sm:px-7">
            <button
              type="button"
              className="flex min-h-8 w-full items-center justify-between gap-2 text-left"
              onClick={() => setAlertsOpen((v) => !v)}
              aria-expanded={alertsOpen}
            >
              <span className="text-[12px] font-medium text-primary">
                {alerts.length} potential workflow{' '}
                {alerts.length === 1 ? 'issue' : 'issues'} identified
                <span className="ml-2 font-normal text-muted">
                  · Directional insights
                </span>
              </span>
              {alertsOpen ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
            {alertsOpen && (
              <div className="mt-2 grid gap-2 pb-1 sm:grid-cols-2">
                {alerts.slice(0, 2).map((alert) => (
                  <div
                    key={alert.id}
                    className="rounded-lg border border-warning/20 bg-background-secondary p-3"
                  >
                    <p className="text-[13px] font-semibold text-primary">
                      {alert.title}
                    </p>
                    <p className="mt-1 text-[12px] leading-5 text-muted">
                      {alert.detail}
                    </p>
                    {alert.directional_cost && (
                      <p className="mt-2 text-[12px] text-warning">
                        {alert.directional_cost}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div
          ref={listRef}
          aria-live="polite"
          className="workspace-scroll min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8 lg:px-10"
        >
          {restoring && turns.length === 0 && (
            <div
              role="status"
              className="mx-auto max-w-[780px] text-sm text-muted"
            >
              Opening conversation…
            </div>
          )}
          {!restoring && turns.length === 0 && (
            <div className="mx-auto flex min-h-full w-full max-w-[880px] flex-col justify-center py-8 sm:py-12">
              <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-xl bg-purple-50 text-brand">
                <Sparkles size={24} strokeWidth={1.7} aria-hidden />
              </div>
              <p className="page-eyebrow">Your business, connected</p>
              <h2 className="max-w-[560px] text-[27px] font-semibold leading-[1.23] tracking-[-0.045em] text-primary sm:text-[34px]">
                What can we move forward today?
              </h2>
              <p className="mt-3 max-w-[570px] text-[14px] leading-7 text-muted">
                Ask across your company’s operations, knowledge, and decisions.
                FreedomBot responds within your access permissions and approval
                rules.
              </p>
              <div className="mt-9 grid gap-3 sm:grid-cols-3">
                {suggestions.map((suggestion) => {
                  const Icon = suggestion.icon
                  return (
                    <button
                      key={suggestion.question}
                      type="button"
                      onClick={() => {
                        setInput(suggestion.question)
                        inputRef.current?.focus()
                      }}
                      className="group flex min-h-[134px] flex-col items-start gap-3 rounded-xl border border-border bg-background-secondary p-4 text-left transition-colors hover:border-purple-300 hover:bg-purple-50/40 focus-visible:outline-brand"
                    >
                      <Icon
                        className="text-brand"
                        size={19}
                        strokeWidth={1.75}
                        aria-hidden
                      />
                      <span>
                        <span className="block text-[11px] font-medium text-muted">
                          {suggestion.label}
                        </span>
                        <span className="mt-1 block text-[13px] font-semibold leading-5 text-primary group-hover:text-brand">
                          {suggestion.question}
                        </span>
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {turns.length > 0 && (
            <div className="mx-auto flex w-full max-w-[880px] flex-col gap-8 pb-5">
              {turns.map((turn, index) =>
                turn.role === 'user' ? (
                  <div key={index} className="flex animate-fade-up justify-end">
                    <div className="max-w-[min(90%,65ch)] whitespace-pre-wrap break-words rounded-2xl rounded-br-md border border-[#d9e7e3] bg-[#eaf3f0] px-4 py-3 text-[13px] leading-[1.75] text-[#213e3e] dark:border-border dark:bg-background-elevated dark:text-primary sm:text-[14px]">
                      {turn.content}
                    </div>
                  </div>
                ) : (
                  <div
                    key={index}
                    className="flex animate-fade-up items-start gap-2.5 sm:gap-3"
                  >
                    <BrandMark className="mt-1 h-8 w-8 shrink-0 rounded-lg sm:h-9 sm:w-9" />
                    <div className="min-w-0 max-w-[min(100%,72ch)] flex-1 space-y-2">
                      <div className="overflow-hidden rounded-2xl rounded-tl-md border border-border bg-[#f4f8f7] shadow-panel dark:bg-background-elevated">
                        <div className="border-b border-border/70 px-4 py-2 sm:px-5">
                          <p className="text-[12px] font-semibold text-primary">
                            {context?.assistant_name ?? 'FreedomBot'}
                          </p>
                        </div>
                        <div className="break-words px-4 py-3.5 text-[14px] leading-[1.75] text-primary sm:px-5 sm:py-4">
                          <MarkdownRenderer classname="!max-w-none !rounded-none !bg-transparent gap-y-2.5 prose-p:my-0 prose-ul:my-1 prose-ol:my-1 prose-li:my-0.5 prose-headings:mb-1 prose-headings:mt-2 text-[14px] leading-[1.75] [&_ul]:pl-5 [&_ol]:pl-5">
                            {turn.content}
                          </MarkdownRenderer>
                        </div>
                        {turn.response?.domain && (
                          <div className="flex flex-wrap items-center gap-2 border-t border-border/70 px-4 py-2.5 sm:px-5">
                            <span className="text-[11px] text-muted">
                              Handled as
                            </span>
                            <Badge tone="neutral" className="font-normal capitalize">
                              {turn.response.domain}
                            </Badge>
                          </div>
                        )}
                      </div>
                      <TurnSignals response={turn.response} />
                    </div>
                  </div>
                )
              )}
            </div>
          )}

          {busy && (
            <div
              className="mx-auto flex max-w-[880px] items-center gap-3 py-3"
              role="status"
              aria-label="Processing request"
            >
              <BrandMark className="h-8 w-8 animate-pulse rounded-lg" />
              <span className="text-[13px] text-muted">
                Working on your request
              </span>
              <span className="flex gap-1" aria-hidden>
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand"
                    style={{ animationDelay: `${i * 180}ms` }}
                  />
                ))}
              </span>
            </div>
          )}
        </div>

        <div className="shrink-0 border-t border-border bg-background-secondary px-3 py-3 sm:px-7 sm:py-4 lg:px-9">
          <div className="mx-auto max-w-[880px]">
            {error && (
              <div className="mb-3">
                <ErrorNote>{error}</ErrorNote>
              </div>
            )}
            <form
              onSubmit={send}
              className="rounded-xl border border-border bg-background-secondary shadow-panel transition-shadow focus-within:border-purple-400 focus-within:shadow-glow"
            >
              <label htmlFor="chat-input" className="sr-only">
                Message FreedomBot
              </label>
              <textarea
                id="chat-input"
                ref={inputRef}
                rows={2}
                value={input}
                onChange={(event) => {
                  setInput(event.target.value)
                  const el = event.target
                  el.style.height = 'auto'
                  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
                }}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter' &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault()
                    event.currentTarget.form?.requestSubmit()
                  }
                }}
                placeholder="Ask a question or describe what you need…"
                disabled={busy}
                aria-describedby="chat-input-hint"
                className="max-h-[200px] min-h-[64px] w-full resize-none rounded-xl border-0 bg-transparent px-4 pb-1 pt-3.5 text-[13px] leading-6 text-primary outline-none placeholder:text-muted/65 disabled:opacity-60 sm:text-[14px]"
              />
              <div className="flex items-center justify-between gap-3 px-3 pb-2.5 pl-4">
                <span id="chat-input-hint" className="text-[11px] text-muted">
                  Enter to send · Shift + Enter for a new line
                </span>
                <Btn
                  type="submit"
                  loading={busy}
                  disabled={!input.trim()}
                  size="sm"
                  className="!rounded-lg !px-3.5"
                >
                  <span>Send</span>
                  <ArrowUp size={15} aria-hidden />
                </Btn>
              </div>
            </form>
            <p className="mt-2 text-center text-[10px] text-muted">
              Responses may require verification. Actions follow your workspace
              policies.
            </p>
          </div>
        </div>
      </section>
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
    <div className="space-y-2.5 px-0.5">
      {tool_activity.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted">Used</span>
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
          className="rounded-xl border border-purple-300 bg-purple-50 px-4 py-3 text-sm shadow-panel dark:border-purple-700 dark:bg-purple-950/50"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="warn">Awaiting approval</Badge>
            <Badge tone={riskTone(action.risk_level)}>
              {action.risk_level}
            </Badge>
            <span className="font-geist font-semibold text-primary">
              {action.title}
            </span>
          </div>
          <p className="type-muted mt-2">{action.policy_reason}</p>
          <Link
            href="/approvals"
            className="ui-transition mt-3 inline-flex min-h-11 items-center font-semibold text-purple-700 hover:text-purple-900 dark:text-purple-300 dark:hover:text-purple-100"
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
          <span className="ml-2 font-geist font-semibold text-primary">
            {action.title}
          </span>
        </div>
      ))}
    </div>
  )
}
