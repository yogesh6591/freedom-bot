'use client'

/**
 * Raising a support ticket from the chat: flag one assistant answer, or escalate an issue. The server
 * attaches the question, the answer and the recent conversation, so the person never repeats themselves.
 */

import { useState } from 'react'
import { Flag, LifeBuoy, X } from 'lucide-react'
import { api } from '@/lib/api'

interface Raised {
  message: string
}

/** A small flag under an assistant answer. Flagging the same answer again does not open a second ticket. */
export function FlagAnswer({ sessionId, messageId }: { sessionId: string; messageId: string }) {
  const [open, setOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function report() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<Raised>('/api/support/flag', {
        session_id: sessionId,
        message_id: messageId,
        comment
      })
      setDone(result.message)
      setOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The answer was not reported')
    } finally {
      setBusy(false)
    }
  }

  if (done) return <p className="text-[11px] text-success">{done}</p>
  return (
    <div className="flex flex-col gap-1.5">
      {!open ? (
        <button
          type="button"
          aria-label="Report this answer"
          title="Report this answer"
          onClick={() => setOpen(true)}
          className="flex h-6 w-6 items-center justify-center rounded-full text-muted hover:bg-background-elevated hover:text-primary"
        >
          <Flag size={12} aria-hidden />
        </button>
      ) : (
        <div className="flex w-full max-w-md flex-col gap-1.5 rounded-xl border border-border bg-background-secondary p-2.5">
          <label className="text-[11px] text-muted" htmlFor={`flag-${messageId}`}>
            What was wrong with this answer? (optional)
          </label>
          <textarea
            id={`flag-${messageId}`}
            className="min-h-[56px] rounded-lg border border-border bg-background px-2 py-1.5 text-[13px] text-primary"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          {error && <p className="text-[11px] text-error">{error}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void report()}
              className="rounded-md bg-brand px-2.5 py-1 text-[12px] font-semibold text-white disabled:opacity-50"
            >
              Report to support
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-md px-2.5 py-1 text-[12px] text-muted hover:text-primary"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** "Escalate issue": describe the problem; the current conversation goes with it. */
export function EscalateButton({ sessionId }: { sessionId: string | null }) {
  const [open, setOpen] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function send() {
    setBusy(true)
    setError(null)
    try {
      const result = await api.post<Raised>('/api/support/tickets', {
        subject,
        body,
        session_id: sessionId || undefined
      })
      setDone(result.message)
      setSubject('')
      setBody('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The issue was not raised')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        type="button"
        aria-label="Escalate issue"
        title="Escalate an issue to support"
        onClick={() => {
          setDone(null)
          setOpen(true)
        }}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-[12px] font-semibold text-muted hover:text-primary"
      >
        <LifeBuoy size={14} aria-hidden />
        <span className="hidden sm:inline">Escalate issue</span>
      </button>
      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Escalate issue"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="w-full max-w-md rounded-2xl border border-border bg-background p-4 shadow-xl">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-primary">Escalate an issue</h2>
              <button type="button" aria-label="Close" onClick={() => setOpen(false)} className="text-muted hover:text-primary">
                <X size={16} aria-hidden />
              </button>
            </div>
            {done ? (
              <p role="status" className="text-sm text-success">
                {done}
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                <input
                  aria-label="Title"
                  placeholder="In one line, what is the problem?"
                  className="rounded-lg border border-border bg-background-secondary px-3 py-2 text-[13px] text-primary"
                  value={subject}
                  onChange={(e) => setSubject(e.target.value)}
                />
                <textarea
                  aria-label="Details"
                  placeholder="What happened, and what did you expect?"
                  className="min-h-[96px] rounded-lg border border-border bg-background-secondary px-3 py-2 text-[13px] text-primary"
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                />
                <p className="text-[11px] text-muted">
                  {sessionId ? 'This conversation is attached for support.' : 'Your account and workspace are attached for support.'}
                </p>
                {error && <p className="text-[11px] text-error">{error}</p>}
                <button
                  type="button"
                  disabled={busy || !subject.trim()}
                  onClick={() => void send()}
                  className="self-start rounded-md bg-brand px-3 py-1.5 text-[13px] font-semibold text-white disabled:opacity-50"
                >
                  Send to support
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}
