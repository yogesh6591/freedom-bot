'use client'

/**
 * Memory: Facts, SOPs, Decisions and Corrections.
 *
 * The correction flow is the point of this screen. Editing a value does not
 * overwrite it — it supersedes it, and the history panel shows the previous
 * value, who changed it, when and why.
 */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { MemoryItem } from '@/lib/types'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorNote,
  Field,
  Panel,
  inputClass,
  timestamp
} from '@/components/common/primitives'

const TABS = ['FACT', 'SOP', 'DECISION', 'CORRECTION'] as const
const TAB_LABEL: Record<string, string> = {
  FACT: 'Facts',
  SOP: 'SOPs',
  DECISION: 'Decisions',
  CORRECTION: 'Corrections'
}

export default function MemoryPage() {
  return (
    <AppShell>
      <MemoryView />
    </AppShell>
  )
}

function MemoryView() {
  const { hasRole } = useSession()
  const [tab, setTab] = useState<(typeof TABS)[number]>('FACT')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [selected, setSelected] = useState<MemoryItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const canWrite = hasRole('DRAFTER', 'APPROVER', 'OPERATOR')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<{ items: MemoryItem[] }>(`/api/memory?category=${tab}`)
      setItems(data.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load memory')
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => {
    void load()
  }, [load])

  async function open(item: MemoryItem) {
    setSelected(await api.get<MemoryItem>(`/api/memory/${item.id}`))
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Organizational memory"
        description="What this organization has told the assistant. Values are versioned: a correction supersedes the old value and keeps it in history."
      >
        <div className="flex gap-2">
          {TABS.map((name) => (
            <button
              key={name}
              onClick={() => {
                setTab(name)
                setSelected(null)
              }}
              className={`rounded-lg px-3 py-1.5 font-geist text-xs transition ${
                tab === name
                  ? 'bg-background-secondary text-primary'
                  : 'text-muted hover:text-primary'
              }`}
            >
              {TAB_LABEL[name]}
            </button>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      <Panel>
        {loading ? (
          <EmptyState>Loading…</EmptyState>
        ) : (
          <DataTable
            rows={items}
            onRowClick={(item) => void open(item)}
            empty={`No ${TAB_LABEL[tab].toLowerCase()} recorded yet.`}
            columns={[
              {
                key: 'title',
                header: 'Title',
                render: (item) => (
                  <div>
                    <div className="font-geist text-primary">{item.title}</div>
                    <div className="font-dmmono text-[10px] text-muted">{item.memory_key}</div>
                  </div>
                )
              },
              {
                key: 'value',
                header: 'Current value',
                render: (item) => (
                  <span className="text-muted">{item.current?.content ?? '—'}</span>
                )
              },
              {
                key: 'source',
                header: 'Source',
                render: (item) => (
                  <Badge tone="neutral">{item.current?.source_type ?? '—'}</Badge>
                )
              },
              {
                key: 'approval',
                header: 'Status',
                render: (item) => (
                  <div className="flex gap-1">
                    <Badge tone={item.current?.is_active ? 'good' : 'neutral'}>
                      {item.current?.status ?? '—'}
                    </Badge>
                    <Badge
                      tone={
                        item.current?.approval_status === 'APPROVED' ? 'good' : 'warn'
                      }
                    >
                      {item.current?.approval_status ?? '—'}
                    </Badge>
                  </div>
                )
              },
              {
                key: 'updated',
                header: 'Last updated',
                render: (item) => (
                  <span className="text-muted">{timestamp(item.updated_at)}</span>
                )
              },
              {
                key: 'v',
                header: 'Ver',
                render: (item) => <span className="text-muted">v{item.current?.version_no ?? 1}</span>
              }
            ]}
          />
        )}
      </Panel>

      {selected && (
        <MemoryDetail
          item={selected}
          canWrite={canWrite}
          onClose={() => setSelected(null)}
          onChanged={async () => {
            await load()
            setSelected(await api.get<MemoryItem>(`/api/memory/${selected.id}`))
          }}
        />
      )}
    </div>
  )
}

function MemoryDetail({
  item,
  canWrite,
  onClose,
  onChanged
}: {
  item: MemoryItem
  canWrite: boolean
  onClose: () => void
  onChanged: () => Promise<void>
}) {
  const [newContent, setNewContent] = useState(item.current?.content ?? '')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function correct(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post(`/api/memory/${item.id}/correct`, {
        new_content: newContent,
        reason
      })
      setReason('')
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Correction failed')
    } finally {
      setBusy(false)
    }
  }

  async function approve(versionId: string) {
    await api.post(`/api/memory/versions/${versionId}/approve`)
    await onChanged()
  }

  return (
    <Panel
      title={item.title}
      description={`${item.category} · ${item.memory_key}`}
      actions={
        <button onClick={onClose} className="text-xs text-muted hover:text-primary">
          Close
        </button>
      }
    >
      <div className="space-y-4">
        <div>
          <h3 className="mb-2 font-geist text-xs uppercase tracking-wide text-muted">
            Version history
          </h3>
          <ul className="space-y-2">
            {(item.versions ?? []).map((version) => (
              <li
                key={version.id}
                className={`rounded-lg border px-3 py-2 text-xs ${
                  version.is_active
                    ? 'border-positive/30 bg-positive/5'
                    : 'border-border bg-background-secondary/30'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={version.is_active ? 'good' : 'neutral'}>
                    v{version.version_no} {version.status}
                  </Badge>
                  <Badge
                    tone={version.approval_status === 'APPROVED' ? 'good' : 'warn'}
                  >
                    {version.approval_status}
                  </Badge>
                  <Badge tone="neutral">{version.source_type}</Badge>
                  <span className="text-muted">
                    by {version.created_by} · {timestamp(version.updated_at)}
                  </span>
                  {version.approval_status === 'PENDING' && canWrite && (
                    <button
                      onClick={() => void approve(version.id)}
                      className="ml-auto rounded border border-positive/40 px-2 py-0.5 text-positive"
                    >
                      Approve
                    </button>
                  )}
                </div>
                <p className="mt-1.5 font-geist text-primary">{version.content}</p>
                {version.correction_reason && (
                  <p className="mt-1 text-muted">
                    Correction reason: {version.correction_reason}
                    {version.corrected_by ? ` (by ${version.corrected_by})` : ''}
                  </p>
                )}
                {version.effective_until && (
                  <p className="mt-1 text-muted">
                    Superseded {timestamp(version.effective_until)}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>

        {canWrite ? (
          <form onSubmit={correct} className="space-y-2 border-t border-border pt-4">
            <h3 className="font-geist text-xs uppercase tracking-wide text-muted">
              Correct this value
            </h3>
            <p className="text-[11px] text-muted">
              The current value is kept in history as superseded. Nothing is overwritten.
            </p>
            {error && <ErrorNote>{error}</ErrorNote>}
            <Field label="New value">
              <textarea
                className={`${inputClass} min-h-[70px]`}
                value={newContent}
                onChange={(event) => setNewContent(event.target.value)}
                required
              />
            </Field>
            <Field label="Why is it changing?">
              <input
                className={inputClass}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                required
              />
            </Field>
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-white disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Record correction'}
            </button>
          </form>
        ) : (
          <p className="border-t border-border pt-4 text-xs text-muted">
            Your role can view memory but not correct it.
          </p>
        )}
      </div>
    </Panel>
  )
}
