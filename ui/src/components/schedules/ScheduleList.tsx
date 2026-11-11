'use client'

/**
 * Recurring scheduled tasks: what each does, how often, when it runs next, how the last run went, and
 * Edit / Stop. Used in the chat's Approvals panel and on the Approvals page.
 *
 * Reads GET /api/approvals/schedules; Edit is PUT and Stop is DELETE /api/approvals/schedules/{id}. All three
 * go through the same guard and policy check as the chat's schedule tools, so a change that needs approval
 * comes back as "Sent for approval". Hiding a button is a convenience; the server decides.
 */

import { useCallback, useEffect, useState } from 'react'
import { CalendarClock, History, Loader2, Pencil, Trash2 } from 'lucide-react'
import { useSession } from '@/components/SessionProvider'
import { api, ApiError } from '@/lib/api'
import type { ScheduleRow } from '@/lib/types'
import { cn } from '@/lib/utils'

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
const DAY_LABEL: Record<string, string> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun'
}

const STATUS: Record<string, { text: string; tone: string }> = {
  active: {
    text: 'Active',
    tone: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
  },
  paused: {
    text: 'Paused',
    tone: 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  },
  cancelled: {
    text: 'Stopped',
    tone: 'bg-black/5 text-muted dark:bg-white/10'
  },
  not_set_up: {
    text: 'Waiting for approval',
    tone: 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }
}

const LAST_RUN: Record<string, string> = {
  running: 'running now',
  done: 'done',
  failed: 'failed',
  skipped: 'skipped'
}

function when(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const today = new Date()
  const sameDay = date.toDateString() === today.toDateString()
  const time = date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit'
  })
  return sameDay
    ? time
    : `${date.toLocaleDateString([], { day: 'numeric', month: 'short' })} ${time}`
}

function message(err: unknown): string {
  return err instanceof ApiError || err instanceof Error
    ? err.message
    : 'That did not work. Try again.'
}

type Note = { tone: 'ok' | 'wait' | 'bad'; text: string }

export function ScheduleList({
  compact = false,
  onOpenRuns
}: {
  /** The narrow layout for the chat's Approvals panel. */
  compact?: boolean
  /** Open the conversation that holds every run (the chat passes its own opener). */
  onOpenRuns?: (sessionId: string) => void
}) {
  const { me, hasRole } = useSession()
  const [items, setItems] = useState<ScheduleRow[] | null>(null)
  const [waiting, setWaiting] = useState<ScheduleRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, Note>>({})
  const [showStopped, setShowStopped] = useState(false)

  const isAdmin = hasRole('ADMIN')
  const canWrite =
    isAdmin || hasRole('OPERATOR') || hasRole('APPROVER') || hasRole('DRAFTER')

  const load = useCallback(async () => {
    try {
      const data = await api.get<{
        items: ScheduleRow[]
        waiting: ScheduleRow[]
      }>('/api/approvals/schedules')
      setItems(data.items)
      setWaiting(data.waiting)
      setError(null)
    } catch (err) {
      setError(message(err))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function stop(row: ScheduleRow) {
    if (!row.id) return
    if (
      !window.confirm(
        `Stop "${row.title || row.instruction}"? It will not run again.`
      )
    )
      return
    setBusy(row.id)
    try {
      const res = await api.del<{
        pending_approval?: boolean
        message?: string
      }>(`/api/approvals/schedules/${encodeURIComponent(row.id)}`)
      setNotes((n) => ({
        ...n,
        [row.id as string]: res.pending_approval
          ? { tone: 'wait', text: res.message || 'Sent for approval.' }
          : { tone: 'ok', text: 'Stopped. It will not run again.' }
      }))
      await load()
    } catch (err) {
      setNotes((n) => ({
        ...n,
        [row.id as string]: { tone: 'bad', text: message(err) }
      }))
    } finally {
      setBusy(null)
    }
  }

  async function save(row: ScheduleRow, changes: Record<string, unknown>) {
    if (!row.id) return
    setBusy(row.id)
    try {
      const res = await api.put<{
        pending_approval?: boolean
        message?: string
        schedule?: string
      }>(`/api/approvals/schedules/${encodeURIComponent(row.id)}`, changes)
      setNotes((n) => ({
        ...n,
        [row.id as string]: res.pending_approval
          ? { tone: 'wait', text: res.message || 'Sent for approval.' }
          : {
              tone: 'ok',
              text: `Saved${res.schedule ? `: ${res.schedule}` : ''}.`
            }
      }))
      setEditing(null)
      await load()
    } catch (err) {
      setNotes((n) => ({
        ...n,
        [row.id as string]: { tone: 'bad', text: message(err) }
      }))
    } finally {
      setBusy(null)
    }
  }

  if (error) return <p className="text-[13px] text-red-600">{error}</p>
  if (items === null)
    return (
      <p className="flex items-center gap-1.5 text-[13px] text-muted">
        <Loader2 size={13} className="animate-spin" aria-hidden /> Loading
        schedules…
      </p>
    )

  const live = items.filter((r) => r.status !== 'cancelled')
  const stopped = items.filter((r) => r.status === 'cancelled')
  const shown = [...waiting, ...live, ...(showStopped ? stopped : [])]

  if (shown.length === 0 && stopped.length === 0)
    return (
      <p className="text-[13px] leading-relaxed text-muted">
        No scheduled tasks yet. Ask in the chat, for example: “Every Monday at 9
        AM post a short AI tip on our Facebook Page.”
      </p>
    )

  return (
    <div className="flex flex-col gap-1.5">
      {shown.map((row) => {
        const key = row.id || row.action_id || row.title || ''
        const mine = !!me && row.created_by === me.user.id
        const mayChange =
          canWrite && (mine || isAdmin) && row.status === 'active' && !!row.id
        const status = STATUS[row.status] ?? {
          text: row.status,
          tone: 'bg-black/5 text-muted'
        }
        const note = row.id ? notes[row.id] : undefined
        const ran = (row.run_count ?? 0) > 0
        return (
          <div
            key={key}
            className={cn(
              'rounded-lg border border-black/5 bg-white/80 px-2.5 py-2 dark:border-white/10 dark:bg-white/5',
              row.status === 'cancelled' && 'opacity-70'
            )}
          >
            <div className="flex items-start gap-2">
              <CalendarClock
                size={14}
                className="mt-0.5 shrink-0 text-secondary"
                aria-hidden
              />
              <div className="min-w-0 flex-1">
                <p
                  className="truncate text-[13px] font-medium text-primary"
                  title={row.instruction || ''}
                >
                  {row.title || row.instruction || 'Scheduled task'}
                </p>
                <p className="mt-0.5 text-[12px] text-secondary">
                  {row.schedule || '—'}
                  {row.status === 'active' && row.next_run_at
                    ? ` · next ${when(row.next_run_at)}`
                    : ''}
                </p>
                <p
                  className="mt-0.5 text-[12px] text-muted"
                  title={row.last_result || ''}
                >
                  {row.last_run_at
                    ? `Last run ${when(row.last_run_at)}${row.last_status ? ` · ${LAST_RUN[row.last_status] ?? row.last_status}` : ''}`
                    : row.status === 'not_set_up'
                      ? 'Starts once approved'
                      : 'Not run yet'}
                  {(row.awaiting_approval ?? 0) > 0
                    ? ` · ${row.awaiting_approval} waiting for approval`
                    : ''}
                  {!mine && row.created_by_name
                    ? ` · by ${row.created_by_name}`
                    : ''}
                </p>
                {!compact && row.last_result ? (
                  <p className="mt-1 line-clamp-2 text-[12px] text-muted">
                    Last result: {row.last_result}
                  </p>
                ) : null}
              </div>
              <span
                className={cn(
                  'shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold',
                  status.tone
                )}
              >
                {status.text}
              </span>
            </div>
            {(mayChange || (mine && ran && row.session_id)) &&
            editing !== row.id ? (
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-[22px]">
                {mine && ran && row.session_id ? (
                  onOpenRuns ? (
                    <button
                      type="button"
                      onClick={() => onOpenRuns(row.session_id as string)}
                      className={BTN}
                    >
                      <History size={12} aria-hidden /> Runs
                    </button>
                  ) : (
                    <a
                      href={`/chat?session=${encodeURIComponent(row.session_id)}`}
                      className={BTN}
                    >
                      <History size={12} aria-hidden /> Runs
                    </a>
                  )
                ) : null}
                {mayChange ? (
                  <>
                    <button
                      type="button"
                      disabled={busy === row.id}
                      onClick={() => setEditing(row.id)}
                      className={BTN}
                    >
                      <Pencil size={12} aria-hidden /> Edit
                    </button>
                    <button
                      type="button"
                      disabled={busy === row.id}
                      onClick={() => void stop(row)}
                      className={cn(
                        BTN,
                        'hover:border-red-300 hover:text-red-600'
                      )}
                    >
                      {busy === row.id ? (
                        <Loader2
                          size={12}
                          className="animate-spin"
                          aria-hidden
                        />
                      ) : (
                        <Trash2 size={12} aria-hidden />
                      )}
                      Stop
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
            {editing === row.id ? (
              <ScheduleEditor
                row={row}
                busy={busy === row.id}
                onCancel={() => setEditing(null)}
                onSave={(changes) => void save(row, changes)}
              />
            ) : null}
            {note ? (
              <p
                role="status"
                className={cn(
                  'mt-1.5 pl-[22px] text-[12px]',
                  note.tone === 'ok'
                    ? 'text-emerald-700 dark:text-emerald-300'
                    : note.tone === 'wait'
                      ? 'text-amber-700 dark:text-amber-300'
                      : 'text-red-600'
                )}
              >
                {note.text}
              </p>
            ) : null}
          </div>
        )
      })}
      {stopped.length > 0 ? (
        <button
          type="button"
          onClick={() => setShowStopped((s) => !s)}
          className="self-start text-[12px] font-medium text-secondary hover:text-primary"
        >
          {showStopped ? 'Hide stopped' : `Show stopped (${stopped.length})`}
        </button>
      ) : null}
    </div>
  )
}

const BTN =
  'inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[12px] font-medium text-secondary hover:text-primary disabled:opacity-60'

const FIELD =
  'w-full rounded-md border border-border bg-background px-2 py-1 text-[13px] text-primary outline-none focus:border-brand-soft'

/** Change what a schedule does or when it runs. Sends only what changed. */
function ScheduleEditor({
  row,
  busy,
  onCancel,
  onSave
}: {
  row: ScheduleRow
  busy: boolean
  onCancel: () => void
  onSave: (changes: Record<string, unknown>) => void
}) {
  const [title, setTitle] = useState(row.title || '')
  const [instruction, setInstruction] = useState(row.instruction || '')
  const [frequency, setFrequency] = useState(row.frequency || 'daily')
  const [time, setTime] = useState(row.time_of_day || '')
  const [every, setEvery] = useState(String(row.every_minutes || 30))
  const [days, setDays] = useState<string[]>(
    row.weekdays?.length ? row.weekdays : ['mon']
  )
  const [problem, setProblem] = useState<string | null>(null)

  function submit() {
    const changes: Record<string, unknown> = {}
    if (title.trim() && title.trim() !== (row.title || ''))
      changes.title = title.trim()
    if (instruction.trim() && instruction.trim() !== (row.instruction || ''))
      changes.instruction = instruction.trim()
    if (frequency !== row.frequency) changes.frequency = frequency
    if (frequency === 'interval') {
      const minutes = Number(every)
      if (!Number.isInteger(minutes) || minutes < 5) {
        setProblem('Every N minutes must be a whole number, at least 5.')
        return
      }
      if (minutes !== row.every_minutes) changes.every_minutes = minutes
      if (time !== (row.time_of_day || '')) changes.time = time
    } else {
      if (!time) {
        setProblem('Choose a time of day.')
        return
      }
      if (time !== row.time_of_day) changes.time = time
    }
    if (frequency === 'weekly') {
      if (days.length === 0) {
        setProblem('Choose at least one weekday.')
        return
      }
      const before = [...(row.weekdays || [])].sort().join()
      if ([...days].sort().join() !== before || frequency !== row.frequency)
        changes.weekdays = days
    }
    if (Object.keys(changes).length === 0) {
      setProblem('Nothing changed.')
      return
    }
    setProblem(null)
    onSave(changes)
  }

  return (
    <div className="mt-2 flex flex-col gap-2 rounded-md border border-border bg-background-secondary p-2.5">
      <label className="flex flex-col gap-1 text-[11.5px] font-medium text-secondary">
        Name
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          className={FIELD}
        />
      </label>
      <label className="flex flex-col gap-1 text-[11.5px] font-medium text-secondary">
        Each time it will
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={3}
          maxLength={2000}
          className={cn(FIELD, 'resize-y')}
        />
      </label>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-[11.5px] font-medium text-secondary">
          How often
          <select
            value={frequency}
            onChange={(e) => setFrequency(e.target.value)}
            className={FIELD}
          >
            <option value="interval">Every N minutes</option>
            <option value="daily">Every day</option>
            <option value="weekly">Chosen weekdays</option>
          </select>
        </label>
        {frequency === 'interval' ? (
          <label className="flex w-24 flex-col gap-1 text-[11.5px] font-medium text-secondary">
            Minutes
            <input
              type="number"
              min={5}
              step={1}
              value={every}
              onChange={(e) => setEvery(e.target.value)}
              className={FIELD}
            />
          </label>
        ) : null}
        <label className="flex flex-col gap-1 text-[11.5px] font-medium text-secondary">
          {frequency === 'interval' ? 'Start at (optional)' : 'Time'}
          <input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className={FIELD}
          />
        </label>
      </div>
      {frequency === 'weekly' ? (
        <div
          className="flex flex-wrap gap-1"
          role="group"
          aria-label="Weekdays"
        >
          {DAYS.map((d) => {
            const on = days.includes(d)
            return (
              <button
                key={d}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  setDays((list) =>
                    on ? list.filter((x) => x !== d) : [...list, d]
                  )
                }
                className={cn(
                  'rounded-md border px-2 py-0.5 text-[12px] font-medium',
                  on
                    ? 'border-brand bg-brand text-white'
                    : 'border-border text-secondary hover:text-primary'
                )}
              >
                {DAY_LABEL[d]}
              </button>
            )
          })}
        </div>
      ) : null}
      {row.timezone ? (
        <p className="text-[11.5px] text-muted">Times are in {row.timezone}.</p>
      ) : null}
      {problem ? <p className="text-[12px] text-red-600">{problem}</p> : null}
      <div className="flex justify-end gap-1.5">
        <button type="button" onClick={onCancel} className={BTN}>
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={submit}
          className="inline-flex items-center gap-1 rounded-md bg-brand px-2.5 py-0.5 text-[12px] font-semibold text-white hover:opacity-90 disabled:opacity-60"
        >
          {busy ? (
            <Loader2 size={12} className="animate-spin" aria-hidden />
          ) : null}
          Save
        </button>
      </div>
    </div>
  )
}
