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
  Btn,
  Chip,
  DataTable,
  ErrorNote,
  Field,
  LoadingBlock,
  PageStack,
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

interface Conflict {
  topic: string
  items: {
    item_id: string
    version_id: string
    title: string
    value: string
    approval_status: string
    recorded_by: string
  }[]
}

function MemoryView() {
  const { hasRole } = useSession()
  const [tab, setTab] = useState<(typeof TABS)[number]>('FACT')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [selected, setSelected] = useState<MemoryItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [conflicts, setConflicts] = useState<Conflict[]>([])

  const canWrite = hasRole('DRAFTER', 'APPROVER', 'OPERATOR')
  const canRemove = hasRole('APPROVER')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<{ items: MemoryItem[] }>(`/api/memory?category=${tab}`)
      setItems(data.items)
      const found = await api.get<{ items: Conflict[] }>('/api/memory/conflicts')
      setConflicts(found.items)
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
    <PageStack>
      <Panel
        title="Organizational memory"
        description="What this organization has told the assistant. Values are versioned: a correction supersedes the old value and keeps it in history."
      >
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Memory categories">
          {TABS.map((name) => (
            <Chip
              key={name}
              active={tab === name}
              onClick={() => {
                setTab(name)
                setSelected(null)
              }}
            >
              {TAB_LABEL[name]}
            </Chip>
          ))}
        </div>
      </Panel>

      {error && <ErrorNote>{error}</ErrorNote>}

      {conflicts.length > 0 && (
        <Panel
          title="Conflicting policies"
          description="These recorded values disagree. The assistant will not pick one — an approver approves the value that stands, and the other is superseded."
        >
          <div className="space-y-3">
            {conflicts.map((conflict) => (
              <div
                key={conflict.topic}
                className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm"
              >
                <div className="mb-3 font-geist text-primary">Topic: {conflict.topic}</div>
                <ul className="space-y-2">
                  {conflict.items.map((entry) => (
                    <li key={entry.version_id} className="flex flex-wrap items-center gap-2">
                      <Badge tone={entry.approval_status === 'APPROVED' ? 'good' : 'warn'}>
                        {entry.approval_status}
                      </Badge>
                      <span className="text-primary">{entry.value}</span>
                      <span className="text-muted">
                        — {entry.title} ({entry.recorded_by})
                      </span>
                      {hasRole('APPROVER') && entry.approval_status !== 'APPROVED' && (
                        <Btn
                          size="sm"
                          className="ml-auto"
                          onClick={async () => {
                            try {
                              await api.post(`/api/memory/versions/${entry.version_id}/approve`)
                              await load()
                            } catch (err) {
                              setError(err instanceof Error ? err.message : 'Could not resolve')
                            }
                          }}
                        >
                          This one stands
                        </Btn>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Panel>
      )}

      <Panel>
        {loading ? (
          <LoadingBlock rows={5} />
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
                    <div className="font-dmmono text-[10px] text-muted">
                      {item.memory_key}
                      {item.access_area && ` · restricted: ${item.access_area}`}
                    </div>
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
                    <Badge tone={item.label === 'fact' ? 'good' : 'warn'}>
                      {item.label === 'fact' ? 'Fact' : 'Estimate'}
                    </Badge>
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
          canRemove={canRemove}
          onClose={() => setSelected(null)}
          onRemoved={async () => {
            setSelected(null)
            await load()
          }}
          onChanged={async () => {
            await load()
            setSelected(await api.get<MemoryItem>(`/api/memory/${selected.id}`))
          }}
        />
      )}
    </PageStack>
  )
}

function MemoryDetail({
  item,
  canWrite,
  canRemove,
  onClose,
  onChanged,
  onRemoved
}: {
  item: MemoryItem
  canWrite: boolean
  canRemove: boolean
  onClose: () => void
  onChanged: () => Promise<void>
  onRemoved: () => Promise<void>
}) {
  const [removeReason, setRemoveReason] = useState('')
  const [removing, setRemoving] = useState(false)
  const [removeError, setRemoveError] = useState<string | null>(null)

  async function remove(event: React.FormEvent) {
    event.preventDefault()
    if (
      !window.confirm(
        `Remove "${item.title}" from memory? FreedomBot will stop using it in answers. ` +
          'Its history stays in the audit trail.'
      )
    )
      return
    setRemoving(true)
    setRemoveError(null)
    try {
      await api.post(`/api/memory/${item.id}/archive`, { reason: removeReason })
      setRemoveReason('')
      await onRemoved()
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : 'Could not remove this item')
    } finally {
      setRemoving(false)
    }
  }

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
            <Btn type="submit" disabled={busy} size="sm">
              {busy ? 'Saving…' : 'Record correction'}
            </Btn>
          </form>
        ) : (
          <p className="border-t border-border pt-4 text-xs text-muted">
            Your role can view memory but not correct it.
          </p>
        )}

        {canRemove && item.current && (
          <form onSubmit={remove} className="space-y-2 border-t border-border pt-4">
            <h3 className="font-geist text-xs uppercase tracking-wide text-muted">
              Remove from memory
            </h3>
            <p className="text-[11px] text-muted">
              FreedomBot stops using this item in answers, search and lists. Nothing is erased —
              the version history stays for audit.
            </p>
            {removeError && <ErrorNote>{removeError}</ErrorNote>}
            <Field label="Reason for removing">
              <input
                className={inputClass}
                value={removeReason}
                onChange={(event) => setRemoveReason(event.target.value)}
                required
              />
            </Field>
            <Btn type="submit" variant="danger" disabled={removing || !removeReason.trim()} size="sm">
              {removing ? 'Removing…' : 'Remove from memory'}
            </Btn>
          </form>
        )}
      </div>
    </Panel>
  )
}
