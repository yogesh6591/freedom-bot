'use client'

/**
 * Chat.
 *
 * One company conversation (FB-034): the server routes each turn to the domain
 * it is about, inside one session, so context carries from strategy to
 * operations to finance. Admins can pin the turn to Acquisition, which uses
 * the campaign assistant instead of the ops pipeline.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowUp,
  ChevronDown,
  ChevronUp,
  History,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2
} from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { api, ApiError } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import type {
  ActionRecord,
  AssistantTrace,
  ChatContext,
  ChatResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  EmailView
} from '@/lib/types'
import { Badge, Btn, ErrorNote } from '@/components/common/primitives'
import { EmailCard } from './EmailCard'
import { useDebug } from '@/lib/debug'

interface Turn {
  id?: string
  role: 'user' | 'assistant'
  content: string
  emailDraft?: string
  /** Words that stay under the email card after the draft is edited. */
  note?: string
  trace?: AssistantTrace
  response?: ChatResponse
}

function sameEmailLine(line: string, draftLines: string[]): boolean {
  for (const other of draftLines) {
    if (line === other) return true
    const short = line.length <= other.length ? line : other
    const long = line.length <= other.length ? other : line
    if (short.length >= 20 && long.includes(short)) return true
  }
  return false
}

/** The email lives in the card. This is only the question that came with it. */
function spokenNote(content: string, draft?: string, saved?: string): string {
  if (typeof saved === 'string') return saved.trim()
  const text = content.trim()
  const email = (draft || '').trim()
  if (!email) return text
  if (text.includes(email)) return text.replace(email, '').trim()
  const draftLines = email
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !sameEmailLine(line, draftLines))
    .join('\n')
    .trim()
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
  return detail.messages.map((m) => ({
    id: m.id,
    role: m.role === 'user' ? 'user' : 'assistant',
    content: m.content,
    emailDraft: m.meta.json?.email_draft || undefined,
    note: spokenNote(m.content, m.meta.json?.email_draft, m.meta.json?.reply_note),
    trace: m.meta.json,
    response:
      m.role === 'assistant'
        ? {
            session_id: detail.session_id,
            content: m.content,
            mode: (m.meta.mode ?? 'WAIT_FOR_APPROVAL') as ChatResponse['mode'],
            domain: m.domain ?? undefined,
            tool_activity: m.meta.tool_activity ?? [],
            actions: [],
            awaiting_approval: (m.meta.awaiting_approval ?? []) as ActionRecord[],
            drafts: (m.meta.drafts ?? []) as ActionRecord[],
            trace: m.meta.json
          }
        : undefined
  }))
}

const CAMPAIGN_FIELDS: { key: keyof AssistantTrace; label: string }[] = [
  { key: 'campaign_name', label: 'Campaign' },
  { key: 'campaign_id', label: 'Campaign id' },
  { key: 'campaign_schedule_timing', label: 'Schedule' },
  { key: 'campaigns_to_show', label: 'Campaigns' },
  { key: 'people_to_show', label: 'People' },
  { key: 'email_address', label: 'Email address' },
  { key: 'email_address_list', label: 'Email list' },
  { key: 'email_draft', label: 'Email draft' }
]

function parseDraftText(
  trace?: AssistantTrace,
  emailDraft?: string
): { subject: string; body: string } {
  const subject = (trace?.email_subject || '').trim()
  const body = (trace?.email_body || '').trim()
  if (subject || body) return { subject, body }
  const draft = (emailDraft || trace?.email_draft || '').trim()
  if (!draft) return { subject: '', body: '' }
  const lines = draft.split('\n')
  const first = (lines[0] || '').trim()
  if (first.toLowerCase().startsWith('subject:')) {
    return {
      subject: first.slice('subject:'.length).trim(),
      body: lines.slice(1).join('\n').replace(/^\n/, '').trimStart()
    }
  }
  return { subject: '', body: draft }
}

function peopleFromShown(
  shown?: { name: string; email: string }[]
): EmailView['people'] {
  return (shown || [])
    .filter((person) => person.email || person.name)
    .map((person) => {
      const parts = (person.name || '').trim().split(/\s+/).filter(Boolean)
      return {
        email: person.email || '',
        first_name: parts[0] || '',
        last_name: parts.slice(1).join(' '),
        company: '',
        sources: {
          first_name: parts[0] ? 'chat' : 'missing',
          last_name: parts.length > 1 ? 'chat' : 'missing',
          company: 'missing'
        } as const
      }
    })
}

function emailViewFor(trace?: AssistantTrace, emailDraft?: string): EmailView | undefined {
  const view = trace?.email_view
  const shownPeople = peopleFromShown(trace?.people_to_show)
  if (view?.template && (view.template.subject || view.template.body)) {
    if (view.people?.length || !shownPeople?.length) return view
    return { ...view, people: shownPeople }
  }
  const { subject, body } = parseDraftText(trace, emailDraft)
  if (!subject && !body) return undefined
  const emails = [...new Set((trace?.email_address_list || []).filter(Boolean))]
  const single = (trace?.email_address || '').trim()
  const recipients = emails.length ? emails : single ? [single] : []
  return {
    template: { subject, body },
    people: shownPeople || [],
    previews: (recipients.length ? recipients : ['']).map((email) => ({
      email,
      subject,
      body
    })),
    warnings: [],
    audience: {
      campaign: trace?.campaign_name || '',
      campaign_is_new: true,
      will_send_now: recipients,
      also_waiting: [],
      template_changed: false,
      scope: '',
      scope_needed: false
    }
  }
}

function applyEmailView(turn: Turn, view: EmailView): Turn {
  const first = view.previews?.[0]
  const emailDraft = first
    ? `Subject: ${first.subject}\n\n${first.body}`.trim()
    : turn.emailDraft
  return {
    ...turn,
    emailDraft,
    trace: {
      ...(turn.trace || {}),
      email_view: {
        ...view,
        draft_id: view.draft_id || turn.trace?.email_view?.draft_id
      },
      email_subject: view.template?.subject,
      email_body: view.template?.body,
      email_draft: emailDraft
    }
  }
}

function previousAssistantDraft(turns: Turn[], index: number): string {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (turns[i].role !== 'assistant') continue
    return (turns[i].trace?.email_draft || turns[i].emailDraft || '').trim()
  }
  return ''
}

function ownEmailView(turn: Turn, emailDraft?: string): EmailView | undefined {
  const view = turn.trace?.email_view
  if (view?.template && (view.template.subject || view.template.body)) return view
  return emailViewFor(turn.trace, emailDraft)
}

function emailCardForTurn(
  turn: Turn,
  latest: Turn | undefined,
  previousDraft: string,
  emailDraft?: string
): { view: EmailView; editable: boolean } | undefined {
  const flag = turn.trace?.show_email_card
  if (flag === false) return undefined
  const draft = (turn.trace?.email_draft || emailDraft || '').trim()
  if (flag !== true && !draft) return undefined
  if (flag !== true && draft === previousDraft) return undefined
  const own = ownEmailView(turn, emailDraft)
  if (!own?.template) return undefined
  const latestView = latest?.trace?.email_view
  const latestDraft = (latest?.trace?.email_draft || latest?.emailDraft || '').trim()
  const sameDraft = own.draft_id
    ? own.draft_id === latestView?.draft_id
    : Boolean(draft) && draft === latestDraft
  const live = sameDraft && latest?.trace?.tool !== 'execute'
  return {
    view: live && latestView?.template ? latestView : own,
    editable: Boolean(live)
  }
}

function traceValue(value: AssistantTrace[keyof AssistantTrace]): string {
  if (Array.isArray(value)) {
    return value
      .map((item) =>
        item && typeof item === 'object' && 'name' in item
          ? [item.name, 'timing' in item ? String(item.timing || '') : '']
              .filter(Boolean)
              .join(' · ')
          : item
      )
      .filter(Boolean)
      .join(', ')
  }
  return typeof value === 'string' ? value.trim() : ''
}

function actionLine(item: Pick<ActionRecord, 'title' | 'status' | 'risk_level'> | Partial<ActionRecord>): string {
  return [item.title, item.status, item.risk_level].filter(Boolean).join(' - ')
}

function TraceRow({
  label,
  value,
  tool
}: {
  label: string
  value: string
  tool?: boolean
}) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[140px_1fr] sm:gap-3">
      <dt className="text-[11px] font-medium text-muted">{label}</dt>
      <dd className="whitespace-pre-wrap break-words text-[12px] leading-5 text-primary">
        {tool ? (
          <span className="inline-flex rounded-md bg-accent px-1.5 py-0.5 font-semibold">
            {value}
          </span>
        ) : (
          value
        )}
      </dd>
    </div>
  )
}

/** Debug mode (sidebar switch): the tools this reply called, in order, with whether each succeeded. */
function ToolsCalled({ activity }: { activity: { tool: string; ok: boolean }[] }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed border-border bg-background px-2.5 py-1.5 font-dmmono text-[11px] text-muted">
      <span className="font-geist font-semibold uppercase tracking-wide">Tools called</span>
      {activity.length === 0 ? (
        <span>none</span>
      ) : (
        activity.map((item, i) => (
          <span
            key={`${item.tool}-${i}`}
            className={`rounded px-1.5 py-0.5 ${item.ok === false ? 'bg-red-500/10 text-red-600' : 'bg-emerald-500/10 text-emerald-700'}`}
          >
            {item.tool} {item.ok === false ? '✗' : '✓'}
          </span>
        ))
      )}
    </div>
  )
}

function TurnTrace({
  trace,
  response
}: {
  trace: AssistantTrace
  response?: ChatResponse
}) {
  const calls = trace.tool_log ?? []
  const lastTool = calls.length ? (calls[calls.length - 1].tool || '—') : 'Waiting'
  const toolsUsed = (response?.tool_activity ?? [])
    .map((item) => `${item.tool} (${item.ok === false ? 'failed' : 'ok'})`)
    .join(', ')
  const actions = (response?.actions ?? []).map(actionLine).filter(Boolean).join(', ')
  const awaiting = (response?.awaiting_approval ?? [])
    .map((item) => item.title)
    .filter(Boolean)
    .join(', ')
  const drafts = (response?.drafts ?? [])
    .map((item) => item.title)
    .filter(Boolean)
    .join(', ')
  const selfCheck = (trace.self_check ?? []).filter(Boolean).join('\n')
  const guardrails = (trace.guardrails ?? []).filter(Boolean).join('\n')
  return (
    <dl className="grid gap-2 rounded-xl border border-dashed border-border bg-background px-3 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">
        Developer
      </div>
      <TraceRow label="Tool" value={lastTool} tool />
      <TraceRow label="Explanation" value={traceValue(trace.explanation) || '—'} />
      <TraceRow label="Domain" value={(response?.domain || '').trim() || '—'} />
      <TraceRow label="Mode" value={(response?.mode || '').trim() || '—'} />
      <TraceRow label="Tools used" value={toolsUsed || '—'} />
      <TraceRow label="Actions" value={actions || '—'} />
      <TraceRow label="Awaiting approval" value={awaiting || '—'} />
      <TraceRow label="Drafts" value={drafts || '—'} />
      {CAMPAIGN_FIELDS.map((field) => (
        <TraceRow
          key={field.key}
          label={field.label}
          value={traceValue(trace[field.key]) || '—'}
        />
      ))}
      {selfCheck ? <TraceRow label="Self-check" value={selfCheck} /> : null}
      {guardrails ? <TraceRow label="Guardrails" value={guardrails} /> : null}
      {calls.length > 0 && (
        <div className="grid gap-1.5 border-t border-border/70 pt-2">
          <dt className="text-[11px] font-medium text-muted">Tool calls this turn</dt>
          {calls.map((call, i) => (
            <dd
              key={i}
              className="rounded-lg border border-border bg-background-secondary px-2.5 py-2 text-[12px] leading-5"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-accent px-1.5 py-0.5 font-semibold">
                  {call.tool}
                </span>
                {call.ok === false && <Badge tone="bad">failed</Badge>}
              </div>
              {call.why && <p className="mt-1 text-primary">{call.why}</p>}
              {call.args && (
                <p className="mt-1 break-all font-mono text-[11px] text-muted">{call.args}</p>
              )}
              {call.result && (
                <p className="mt-1 break-words text-[11px] text-secondary">→ {call.result}</p>
              )}
            </dd>
          ))}
        </div>
      )}
    </dl>
  )
}

function messageBlocks(text: string): string[] {
  const blocks: string[] = []
  let current = ''
  for (const line of text.split('\n')) {
    if (!line.trim()) {
      if (current.trim()) blocks.push(current.trim())
      current = ''
    } else {
      current = current ? `${current}\n${line.trim()}` : line.trim()
    }
  }
  if (current.trim()) blocks.push(current.trim())
  return blocks
}

function AssistantMessage({
  text,
  campaigns,
  people: peopleRows
}: {
  text: string
  campaigns?: { name: string; timing: string }[]
  people?: { name: string; email: string }[]
}) {
  const blocks = messageBlocks(text)
  const people: { name: string; email: string }[] = (peopleRows || []).filter(
    (row) => row.name && row.email
  )
  const before: string[] = []
  const after: string[] = []
  let seenPeople = false
  for (const block of blocks) {
    const lines = block.split('\n')
    const isPerson = lines.length === 2 && lines[1].includes('@') && !lines[0].includes('@')
    if (isPerson && people.length === 0) {
      seenPeople = true
      people.push({ name: lines[0], email: lines[1] })
    } else if (!seenPeople) {
      before.push(block)
    } else {
      after.push(block)
    }
  }
  const campaignRows = (campaigns || []).filter((row) => row.name)
  const lead = campaignRows.length && before.length ? before.slice(0, 1) : before
  const rest = campaignRows.length && before.length ? [...before.slice(1), ...after] : after
  return (
    <div className="flex items-start gap-2.5">
      <span className="assistant-orb mt-0.5 h-5 w-5 shrink-0 rounded-full" aria-hidden />
      <div className="assistant-bot-bubble w-fit max-w-[min(100%,36rem)] rounded-2xl rounded-tl-md px-4 py-3">
        <div className="flex flex-col gap-3">
          {lead.map((block) => (
            <p key={block} className="whitespace-pre-wrap text-[15px] leading-6 text-primary">
              {block}
            </p>
          ))}
          {people.length > 0 ? (
            <table className="w-full border-collapse text-left text-[14px]">
              <thead>
                <tr className="border-b border-black/10 text-[12px] font-semibold uppercase tracking-wide text-secondary">
                  <th className="py-1.5 pr-4 font-semibold">Name</th>
                  <th className="py-1.5 font-semibold">Email</th>
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <tr key={person.email} className="border-b border-black/5 last:border-0">
                    <td className="py-1.5 pr-4 font-medium text-primary">{person.name}</td>
                    <td className="py-1.5 text-secondary">{person.email}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {campaignRows.length > 0 ? (
            <table className="w-full border-collapse text-left text-[14px]">
              <thead>
                <tr className="border-b border-black/10 text-[12px] font-semibold uppercase tracking-wide text-secondary">
                  <th className="py-1.5 pr-4 font-semibold">Campaign</th>
                  <th className="py-1.5 font-semibold">Timing</th>
                </tr>
              </thead>
              <tbody>
                {campaignRows.map((campaign) => (
                  <tr key={campaign.name} className="border-b border-black/5 last:border-0">
                    <td className="py-1.5 pr-4 font-medium text-primary">{campaign.name}</td>
                    <td className="py-1.5 text-secondary">{campaign.timing}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {rest.map((block) => (
            <p key={block} className="whitespace-pre-wrap text-[15px] leading-6 text-primary">
              {block}
            </p>
          ))}
        </div>
      </div>
    </div>
  )
}

function laterActionHappened(turns: Turn[], fromIndex: number): boolean {
  for (const turn of turns.slice(fromIndex + 1)) {
    if (turn.role !== 'assistant') continue
    const log = turn.trace?.tool_log ?? []
    if (
      log.some(
        (call) =>
          call.tool === 'send_approved' &&
          String(call.result || '').startsWith('sent:')
      )
    ) {
      return true
    }
    const wrote = (turn.response?.tool_activity ?? []).some((item) => item.ok)
    const actions = [
      ...(turn.response?.actions ?? []),
      ...(turn.response?.awaiting_approval ?? []),
      ...(turn.response?.drafts ?? [])
    ]
    if (wrote && actions.length > 0) return true
  }
  return false
}

function assistantTurnFrom(data: ChatResponse & { response?: string }): Turn {
  const content = data.content || data.response || ''
  return {
    role: 'assistant',
    content,
    emailDraft: data.email_draft || undefined,
    note: spokenNote(content, data.email_draft, data.trace?.reply_note),
    trace: data.trace,
    response: { ...data, content }
  }
}

function withMessageIds(turns: Turn[], detail: ChatSessionDetail): Turn[] {
  return turns.map((turn, index) => ({
    ...turn,
    id: detail.messages[index]?.id ?? turn.id
  }))
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
  const { me, hasRole } = useSession()
  const canAcquire = hasRole('ADMIN')
  // The open conversation is remembered per workspace and per person, so a
  // refresh or a visit to another page comes back to it (M01-11).
  const storageKey = me ? `bizos:chat:${me.client.id}:${me.user.id}` : null
  const [history, setHistory] = useState<ChatSessionSummary[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [context, setContext] = useState<ChatContext | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [developer, setDeveloper] = useState(false)
  const [debug] = useDebug()
  const [acquisition, setAcquisition] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const refreshContext = useCallback(async () => {
    try {
      const domain = canAcquire && acquisition ? 'acquisition' : 'auto'
      const data = await api.get<ChatContext>(`/api/chat/context?domain=${domain}`)
      setContext(data)
    } catch {
      // Context is informational; chat still works without it.
    }
  }, [acquisition, canAcquire])

  useEffect(() => {
    void refreshContext()
  }, [refreshContext])

  useEffect(() => {
    try {
      setDeveloper(localStorage.getItem('bizos:chat:developer') === 'on')
      setAcquisition(localStorage.getItem('bizos:chat:acquisition') === 'on')
    } catch {
      setDeveloper(false)
      setAcquisition(false)
    }
  }, [])

  function toggleDeveloper() {
    setDeveloper((current) => {
      const next = !current
      try {
        localStorage.setItem('bizos:chat:developer', next ? 'on' : 'off')
      } catch {
        // The toggle still works for this visit.
      }
      return next
    })
  }

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

  async function deleteConversation(id: string) {
    const item = history.find((row) => row.session_id === id)
    const title = item?.title || 'this conversation'
    if (!window.confirm(`Delete ${title}?`)) return
    try {
      await api.del(`/api/chat/sessions/${encodeURIComponent(id)}`)
      setHistory((rows) => rows.filter((row) => row.session_id !== id))
      if (sessionId === id) startNewConversation()
    } catch (err) {
      setHistoryError(err instanceof Error ? err.message : 'Could not delete the conversation')
    }
  }

  function startNewConversation() {
    setSessionId(undefined)
    setTurns([])
    setError(null)
    setEditingId(null)
    setEditText('')
    remember(undefined)
  }

  function toggleAcquisition() {
    setAcquisition((current) => {
      const next = !current
      try {
        localStorage.setItem('bizos:chat:acquisition', next ? 'on' : 'off')
      } catch {
        // The toggle still works for this visit.
      }
      return next
    })
    startNewConversation()
  }

  function engineDomain(): 'acquisition' | undefined {
    return canAcquire && acquisition ? 'acquisition' : undefined
  }

  async function attachMessageIds(id: string, nextTurns: Turn[]) {
    try {
      const detail = await api.get<ChatSessionDetail>(
        `/api/chat/sessions/${encodeURIComponent(id)}`
      )
      setTurns(withMessageIds(nextTurns, detail))
    } catch {
      setTurns(nextTurns)
    }
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
    setEditingId(null)
    setTurns((prev) => [...prev, { role: 'user', content: message }])
    try {
      const data = await api.post<ChatResponse & { response?: string }>(
        '/api/chat',
        { message, session_id: sessionId, domain: engineDomain() ?? 'auto' }
      )
      setSessionId(data.session_id)
      remember(data.session_id)
      const nextTurns = [...turns, { role: 'user' as const, content: message }, assistantTurnFrom(data)]
      setTurns(nextTurns)
      await attachMessageIds(data.session_id, nextTurns)
      void refreshContext()
      void loadHistory()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Chat failed')
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  async function saveEdit(index: number) {
    if (!sessionId || busy) return
    const turn = turns[index]
    const message = editText.trim()
    if (!turn?.id || turn.role !== 'user' || !message || message === turn.content) return
    if (laterActionHappened(turns, index)) {
      const ok = window.confirm(
        'An action (email sent / record created) already happened after this point. Editing will NOT undo it. Continue?'
      )
      if (!ok) return
    }
    setBusy(true)
    setError(null)
    try {
      const data = await api.post<ChatResponse & { response?: string }>(
        `/api/chat/sessions/${encodeURIComponent(sessionId)}/edit`,
        { message_id: turn.id, message, domain: engineDomain() ?? 'auto' }
      )
      const nextTurns = [
        ...turns.slice(0, index),
        { role: 'user' as const, content: message },
        assistantTurnFrom(data)
      ]
      setSessionId(data.session_id)
      remember(data.session_id)
      setTurns(nextTurns)
      setEditingId(null)
      await attachMessageIds(data.session_id, nextTurns)
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
  const lastAssistantIndex = turns.reduce(
    (found, item, i) => (item.role === 'assistant' ? i : found),
    -1
  )
  const suggestions = [
    {
      question: 'Summarize our current priorities',
      wash: 'linear-gradient(135deg, rgba(124,58,237,0.16), rgba(255,255,255,0.7))'
    },
    {
      question: 'Which actions need my approval?',
      wash: 'linear-gradient(135deg, rgba(37,99,235,0.16), rgba(255,255,255,0.7))'
    },
    {
      question: 'Help me draft a customer follow-up',
      wash: 'linear-gradient(135deg, rgba(219,39,119,0.16), rgba(255,255,255,0.7))'
    }
  ]

  return (
    <div className="assistant-stage relative flex h-full min-h-0 flex-1 overflow-hidden">
      {historyOpen && (
        <button
          type="button"
          aria-label="Close conversations"
          className="absolute inset-0 z-20 bg-[#06070d]/55"
          onClick={() => setHistoryOpen(false)}
        />
      )}
      <aside
        id="chat-conversations-panel"
        aria-label="Conversation history"
        aria-hidden={!historyOpen}
        className={`${
          historyOpen ? 'translate-x-0' : '-translate-x-full pointer-events-none'
        } absolute inset-y-0 left-0 z-30 flex w-[min(320px,88vw)] flex-col border-r border-border bg-background-secondary shadow-lift transition-transform duration-200 ease-out`}
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
              <div
                key={item.session_id}
                className={`flex items-stretch rounded-lg transition-colors ${
                  item.session_id === sessionId
                    ? 'border border-purple-200 bg-purple-50'
                    : 'border border-transparent hover:bg-background-elevated'
                }`}
              >
                <button
                  type="button"
                  onClick={() => void openSession(item.session_id)}
                  aria-current={
                    item.session_id === sessionId ? 'true' : undefined
                  }
                  className="min-w-0 flex-1 rounded-lg px-3 py-3 text-left focus-visible:outline-brand"
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
                <button
                  type="button"
                  className="mr-1 flex w-9 shrink-0 items-center justify-center rounded-lg text-muted hover:text-red-600 focus-visible:outline-brand"
                  aria-label={`Delete ${item.title || 'conversation'}`}
                  title="Delete conversation"
                  onClick={() => void deleteConversation(item.session_id)}
                >
                  <Trash2 size={15} aria-hidden />
                </button>
              </div>
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
        aria-label="Assistant"
      >
        <header className="shrink-0 px-3 pt-3 sm:px-5">
          <div className="assistant-glass flex h-14 w-full items-center justify-between gap-3 rounded-2xl px-3">
            <div className="flex min-w-0 items-center gap-2.5 pl-1">
              <span className="assistant-orb h-8 w-8 shrink-0 rounded-full" aria-hidden />
              <span className="min-w-0">
                <span className="block truncate text-[14px] font-semibold tracking-tight text-primary">
                  Assistant
                </span>
                <span className="flex items-center gap-1.5 text-[11px] text-muted">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden />
                  {canAcquire && acquisition ? 'Acquisition' : 'Ready'}
                </span>
              </span>
            </div>
          <div className="flex shrink-0 items-center gap-2">
            {canAcquire && (
              <button
                type="button"
                role="switch"
                aria-checked={acquisition}
                aria-label="Acquisition"
                onClick={toggleAcquisition}
                className={`inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] font-semibold ${
                  acquisition
                    ? 'border-purple-300 bg-purple-50 text-brand'
                    : 'border-border text-muted hover:text-primary'
                }`}
              >
                <span
                  className={`h-2 w-2 rounded-full ${acquisition ? 'bg-brand' : 'bg-border'}`}
                  aria-hidden
                />
                <span className="hidden sm:inline">Acquisition</span>
              </button>
            )}
            <button
              type="button"
              role="switch"
              aria-checked={developer}
              aria-label="Developer mode"
              onClick={toggleDeveloper}
              className={`inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-[12px] font-semibold ${
                developer
                  ? 'border-purple-300 bg-purple-50 text-brand'
                  : 'border-border text-muted hover:text-primary'
              }`}
            >
              <span
                className={`h-2 w-2 rounded-full ${developer ? 'bg-brand' : 'bg-border'}`}
                aria-hidden
              />
              <span className="hidden sm:inline">Developer</span>
            </button>
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
          className="workspace-scroll min-h-0 flex-1 overflow-y-auto"
        >
          <div
            className={`flex min-h-full w-full flex-col px-4 py-5 sm:px-6 ${
              turns.length === 0 ? 'justify-center' : 'justify-start'
            }`}
          >
            {restoring && turns.length === 0 && (
              <p role="status" className="text-sm text-muted">
                Opening conversation…
              </p>
            )}
            {!restoring && turns.length === 0 && (
              <div className="mx-auto w-full max-w-[640px] pb-6">
                <div className="mb-6 flex flex-col items-center text-center">
                  <span className="assistant-orb mb-4 h-16 w-16 rounded-full" aria-hidden />
                  <h2 className="font-[family-name:var(--font-display)] text-[30px] font-semibold tracking-tight text-primary sm:text-[36px]">
                    What should we send?
                  </h2>
                  <p className="mt-2 max-w-[420px] text-[14px] leading-6 text-secondary">
                    Name a person, a campaign, and a send window. The draft stays here until you approve it.
                  </p>
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  {suggestions.map((item) => (
                    <button
                      key={item.question}
                      type="button"
                      onClick={() => {
                        setInput(item.question)
                        inputRef.current?.focus()
                      }}
                      style={{ background: item.wash }}
                      className="assistant-glass rounded-2xl px-4 py-4 text-left text-[14px] font-medium leading-6 text-primary transition-transform hover:-translate-y-0.5"
                    >
                      {item.question}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.length > 0 && (
              <div className="flex flex-col gap-4 pb-2">
                {turns.map((turn, index) =>
                  turn.role === 'user' ? (
                    <div
                      key={turn.id || index}
                      className="group flex items-end justify-end gap-1.5"
                    >
                      {turn.id && !busy && editingId !== turn.id ? (
                        <button
                          type="button"
                          aria-label="Edit message"
                          title="Edit message"
                          className="mb-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted opacity-0 transition-opacity group-hover:opacity-100 hover:bg-background-elevated hover:text-primary"
                          onClick={() => {
                            setEditingId(turn.id as string)
                            setEditText(turn.content)
                          }}
                        >
                          <Pencil size={14} aria-hidden />
                        </button>
                      ) : null}
                      {editingId === turn.id ? (
                        <div className="w-fit max-w-[min(80%,28rem)]">
                          <textarea
                            value={editText}
                            onChange={(event) => setEditText(event.target.value)}
                            rows={Math.min(8, Math.max(2, editText.split('\n').length))}
                            aria-label="Edit message"
                            className="assistant-user-bubble w-full min-w-[16rem] resize-y rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] leading-7 outline-none"
                          />
                          <div className="mt-1.5 flex justify-end gap-2">
                            <button
                              type="button"
                              className="rounded-full px-3 py-1 text-[12px] font-medium text-secondary hover:text-primary"
                              onClick={() => {
                                setEditingId(null)
                                setEditText('')
                              }}
                            >
                              Cancel
                            </button>
                            <button
                              type="button"
                              className="rounded-full bg-brand px-3 py-1 text-[12px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
                              disabled={
                                !editText.trim() || editText.trim() === turn.content.trim()
                              }
                              onClick={() => void saveEdit(index)}
                            >
                              Save
                            </button>
                          </div>
                        </div>
                      ) : (
                        <p className="assistant-user-bubble w-fit max-w-[min(80%,28rem)] whitespace-pre-wrap break-words rounded-2xl rounded-br-md px-4 py-2.5 text-[15px] leading-7">
                          {turn.content}
                        </p>
                      )}
                    </div>
                  ) : (
                    (() => {
                      const latest =
                        lastAssistantIndex >= 0 ? turns[lastAssistantIndex] : undefined
                      const card = emailCardForTurn(
                        turn,
                        latest,
                        previousAssistantDraft(turns, index),
                        turn.emailDraft
                      )
                      const note = spokenNote(
                        turn.content,
                        turn.emailDraft,
                        turn.note
                      )
                      return (
                    <div key={index} className="flex flex-col items-start gap-2">
                      {card ? (
                        <EmailCard
                          view={card.view}
                          sessionId={sessionId || ''}
                          editable={Boolean(sessionId) && card.editable}
                          onView={(next) =>
                            setTurns((prev) => {
                              const last = prev.reduce(
                                (found, item, i) =>
                                  item.role === 'assistant' ? i : found,
                                -1
                              )
                              const lastTurn = last >= 0 ? prev[last] : undefined
                              const ownId = turn.trace?.email_view?.draft_id
                              const ownDraft = (
                                turn.trace?.email_draft ||
                                turn.emailDraft ||
                                ''
                              ).trim()
                              const lastDraft = (
                                lastTurn?.trace?.email_draft ||
                                lastTurn?.emailDraft ||
                                ''
                              ).trim()
                              const sameDraft = ownId
                                ? ownId === lastTurn?.trace?.email_view?.draft_id
                                : Boolean(ownDraft) && ownDraft === lastDraft
                              const target =
                                sameDraft && lastTurn?.trace?.tool !== 'execute'
                                  ? last
                                  : index
                              return prev.map((item, i) =>
                                i === target ? applyEmailView(item, next) : item
                              )
                            })
                          }
                        />
                      ) : null}
                      {note ? (
                        card ? (
                          <p className="assistant-bot-bubble w-fit max-w-[min(100%,36rem)] whitespace-pre-wrap break-words rounded-2xl rounded-tl-md px-4 py-3 text-[15px] leading-7 text-primary">
                            {note}
                          </p>
                        ) : (
                          <AssistantMessage
                            text={note}
                            campaigns={turn.trace?.campaigns_to_show}
                            people={turn.trace?.people_to_show}
                          />
                        )
                      ) : null}
                      {debug && turn.response ? (
                        <ToolsCalled activity={turn.response.tool_activity ?? []} />
                      ) : null}
                      {developer && turn.trace ? (
                        <TurnTrace trace={turn.trace} response={turn.response} />
                      ) : null}
                    </div>
                      )
                    })()
                  )
                )}
              </div>
            )}

            {busy && (
              <div
                className="mt-3 flex items-start gap-2.5"
                role="status"
                aria-label="Working"
              >
                <span className="assistant-orb h-5 w-5 shrink-0 rounded-full" aria-hidden />
                <span className="assistant-bot-bubble inline-flex items-center gap-1.5 rounded-3xl rounded-bl-md px-4 py-3">
                  {[0, 1, 2].map((i) => (
                    <span
                      key={i}
                      className="h-2 w-2 animate-pulse rounded-full bg-purple-400"
                      style={{ animationDelay: `${i * 160}ms` }}
                    />
                  ))}
                </span>
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 px-3 pb-4 pt-1 sm:px-5">
          <div className="w-full">
            {error && (
              <div className="mb-3">
                <ErrorNote>{error}</ErrorNote>
              </div>
            )}
            <form
              onSubmit={send}
              className="assistant-glass flex items-end gap-2 rounded-3xl px-3 py-2"
            >
              <label htmlFor="chat-input" className="sr-only">
                Message
              </label>
              <textarea
                id="chat-input"
                ref={inputRef}
                rows={1}
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
                placeholder="Message the assistant"
                disabled={busy}
                className="max-h-[200px] min-h-[40px] w-full resize-none border-0 bg-transparent py-2 text-[15px] leading-6 text-primary outline-none placeholder:text-muted/70 disabled:opacity-60"
              />
              <button
                type="submit"
                disabled={busy || !input.trim()}
                aria-label="Send"
                className="assistant-send mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full disabled:cursor-not-allowed"
              >
                {busy ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-r-transparent" />
                ) : (
                  <ArrowUp size={16} aria-hidden />
                )}
              </button>
            </form>
          </div>
        </div>
      </section>
    </div>
  )
}

