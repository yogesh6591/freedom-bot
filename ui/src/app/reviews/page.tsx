'use client'

/** Human review queue (§16) — questions the assistant raised instead of guessing. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { ReviewItem } from '@/lib/types'
import {
  Badge,
  EmptyState,
  ErrorNote,
  Panel,
  inputClass,
  statusTone,
  timestamp
} from '@/components/common/primitives'

export default function ReviewsPage() {
  return (
    <AppShell>
      <ReviewsView />
    </AppShell>
  )
}

function ReviewsView() {
  const { hasRole } = useSession()
  const [status, setStatus] = useState('OPEN')
  const [items, setItems] = useState<ReviewItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const canResolve = hasRole('DRAFTER', 'APPROVER', 'OPERATOR')

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: ReviewItem[] }>(`/api/reviews?status=${status}`)
      setItems(data.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load reviews')
    }
  }, [status])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-4">
      <Panel
        title="Human review"
        description="When the assistant could not decide something important, it stopped and asked instead of guessing."
      >
        <div className="flex gap-2">
          {['OPEN', 'RESOLVED', 'DISMISSED'].map((value) => (
            <button
              key={value}
              onClick={() => setStatus(value)}
              className={`rounded-lg px-3 py-1.5 font-geist text-xs transition ${
                status === value
                  ? 'bg-background-secondary text-primary'
                  : 'text-muted hover:text-primary'
              }`}
            >
              {value}
            </button>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}
      {items.length === 0 && <EmptyState>Nothing waiting on a human.</EmptyState>}

      {items.map((item) => (
        <ReviewCard key={item.id} item={item} canResolve={canResolve} onChanged={load} />
      ))}
    </div>
  )
}

function ReviewCard({
  item,
  canResolve,
  onChanged
}: {
  item: ReviewItem
  canResolve: boolean
  onChanged: () => Promise<void>
}) {
  const [answer, setAnswer] = useState(item.recommended_option ?? '')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(path: 'resolve' | 'dismiss') {
    setBusy(true)
    setError(null)
    try {
      await api.post(`/api/reviews/${item.id}/${path}`,
        path === 'resolve' ? { resolution: answer, note } : { note })
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone(item.status)}>{item.status}</Badge>
          {item.confidence !== null && (
            <Badge tone="warn">confidence {Math.round(item.confidence * 100)}%</Badge>
          )}
          {item.workflow_id && <Badge tone="neutral">{item.workflow_id}</Badge>}
          <span className="text-[11px] text-muted">{timestamp(item.created_at)}</span>
        </div>

        <p className="font-geist text-sm text-primary">{item.question}</p>
        {item.context && <p className="text-xs text-muted">{item.context}</p>}

        {item.status === 'OPEN' && canResolve ? (
          <div className="space-y-2 border-t border-border pt-3">
            {item.choices.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {item.choices.map((choice) => (
                  <button
                    key={choice}
                    onClick={() => setAnswer(choice)}
                    className={`rounded-lg border px-3 py-1.5 font-geist text-xs transition ${
                      answer === choice
                        ? 'border-brand text-brand'
                        : 'border-border text-muted hover:text-primary'
                    }`}
                  >
                    {choice}
                    {choice === item.recommended_option ? ' (recommended)' : ''}
                  </button>
                ))}
              </div>
            )}
            <input
              className={inputClass}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="Your answer"
            />
            <input
              className={inputClass}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Note (optional)"
            />
            {error && <ErrorNote>{error}</ErrorNote>}
            <div className="flex gap-2">
              <button
                disabled={busy || !answer}
                onClick={() => void submit('resolve')}
                className="rounded-lg bg-positive px-3 py-1.5 font-geist text-xs text-black disabled:opacity-50"
              >
                Resolve
              </button>
              <button
                disabled={busy}
                onClick={() => void submit('dismiss')}
                className="rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted"
              >
                Dismiss
              </button>
            </div>
          </div>
        ) : (
          item.resolution && (
            <p className="border-t border-border pt-3 text-xs text-muted">
              Answered <span className="text-primary">{item.resolution}</span>
              {item.resolved_by ? ` by ${item.resolved_by}` : ''}
            </p>
          )
        )}
      </div>
    </Panel>
  )
}
