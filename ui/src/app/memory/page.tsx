'use client'

/**
 * Memory: Facts, SOPs, Decisions and Corrections.
 *
 * The correction flow is the point of this screen. Editing a value does not
 * overwrite it — it supersedes it, and the history panel shows the previous
 * value, who changed it, when and why.
 */

import { useCallback, useEffect, useState } from 'react'
import { Eye, Plus } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { MemoryItem } from '@/lib/types'
import {
  Badge,
  Btn,
  DataTable,
  ErrorNote,
  Field,
  LoadingBlock,
  PageStack,
  PageHeader,
  Panel,
  SegmentedTab,
  SegmentedTabs,
  inputClass,
  timestamp
} from '@/components/common/primitives'
import { ADD_LABEL, AddMemoryDialog } from './AddMemoryDialog'
import { DocumentViewer } from './DocumentViewer'

const TABS = ['FACT', 'SOP', 'DECISION', 'CORRECTION', 'DOCUMENT'] as const
const TAB_LABEL: Record<string, string> = {
  FACT: 'Facts',
  SOP: 'SOPs',
  DECISION: 'Decisions',
  CORRECTION: 'Corrections',
  DOCUMENT: 'Documents'
}

const AREAS = ['', 'finance', 'hr', 'salary', 'legal', 'exec', 'ops', 'sales'] as const
const AREA_LABEL: Record<string, string> = {
  '': 'Everyone in the workspace',
  finance: 'Finance access only',
  sales: 'Sales access only',
  hr: 'HR access only',
  salary: 'Salary access only',
  legal: 'Legal access only',
  exec: 'Executive access only',
  ops: 'Operations access only'
}

interface KnowledgeDocument {
  document_id: string
  title: string
  filename: string | null
  parts: number
  access_area: string | null
  uploaded_by: string
  uploaded_at: string | null
  has_file?: boolean
}

/** Uploaded knowledge-base files: admins upload and remove; everyone sees the ones their access allows. */
function DocumentsPanel({
  isAdmin,
  onError
}: {
  isAdmin: boolean
  onError: (message: string | null) => void
}) {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [area, setArea] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [viewing, setViewing] = useState<KnowledgeDocument | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<{ items: KnowledgeDocument[] }>('/api/memory/documents')
      setDocs(data.items)
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not load documents')
    } finally {
      setLoading(false)
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  async function upload() {
    if (!file) return
    setBusy(true)
    setNotice(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('title', title)
      form.append('access_area', area)
      const result = await api.upload<{ title: string; parts: number }>('/api/memory/documents', form)
      setNotice(`Added “${result.title}” (${result.parts} part${result.parts === 1 ? '' : 's'}).`)
      setFile(null)
      setTitle('')
      onError(null)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  async function remove(doc: KnowledgeDocument) {
    if (!window.confirm(`Remove “${doc.title}” from the knowledge base?`)) return
    try {
      await api.post(`/api/memory/documents/${doc.document_id}/archive`)
      await load()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not remove')
    }
  }

  return (
    <>
      {isAdmin && (
        <Panel
          title="Upload a document"
          description="PDF, Word (.docx), .txt or .md, up to 10 MB. The assistant answers from it for everyone who has the access you choose."
        >
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="File">
              <input
                type="file"
                accept=".pdf,.docx,.txt,.md"
                className={inputClass}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </Field>
            <Field label="Title (optional)">
              <input
                className={inputClass}
                value={title}
                placeholder="Defaults to the file name"
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>
            <Field label="Who can see it">
              <select className={inputClass} value={area} onChange={(e) => setArea(e.target.value)}>
                {AREAS.map((a) => (
                  <option key={a} value={a}>
                    {AREA_LABEL[a]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Btn onClick={() => void upload()} loading={busy} disabled={!file || busy}>
              Upload
            </Btn>
            {notice && <span className="text-sm text-muted">{notice}</span>}
          </div>
        </Panel>
      )}
      <Panel>
        {loading ? (
          <LoadingBlock rows={4} />
        ) : (
          <DataTable
            rows={docs}
            empty="No documents uploaded yet."
            columns={[
              {
                key: 'title',
                header: 'Document',
                render: (doc) => (
                  <div>
                    <div className="font-geist text-primary">{doc.title}</div>
                    <div className="font-dmmono text-[10px] text-muted">{doc.filename}</div>
                  </div>
                )
              },
              {
                key: 'access',
                header: 'Who can see it',
                render: (doc) => (
                  <Badge tone={doc.access_area ? 'warn' : 'good'}>
                    {AREA_LABEL[doc.access_area ?? ''] ?? doc.access_area}
                  </Badge>
                )
              },
              {
                key: 'parts',
                header: 'Parts',
                render: (doc) => <span className="text-muted">{doc.parts}</span>
              },
              {
                key: 'uploaded',
                header: 'Uploaded',
                render: (doc) => <span className="text-muted">{timestamp(doc.uploaded_at)}</span>
              },
              {
                key: 'view',
                header: '',
                render: (doc) => (
                  <Btn
                    size="sm"
                    variant="secondary"
                    title="View document"
                    aria-label={`View ${doc.title}`}
                    onClick={() => setViewing(doc)}
                  >
                    <Eye size={16} aria-hidden />
                  </Btn>
                )
              },
              ...(isAdmin
                ? [
                    {
                      key: 'remove',
                      header: '',
                      render: (doc: KnowledgeDocument) => (
                        <Btn size="sm" variant="secondary" onClick={() => void remove(doc)}>
                          Remove
                        </Btn>
                      )
                    }
                  ]
                : [])
            ]}
          />
        )}
      </Panel>
      <DocumentViewer doc={viewing} onClose={() => setViewing(null)} />
    </>
  )
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
  const { hasRole, me } = useSession()
  const [tab, setTab] = useState<(typeof TABS)[number]>('FACT')
  const [items, setItems] = useState<MemoryItem[]>([])
  const [selected, setSelected] = useState<MemoryItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [adding, setAdding] = useState(false)

  const canWrite = hasRole('DRAFTER', 'APPROVER', 'OPERATOR')
  const canRemove = hasRole('APPROVER')
  const areaOptions = AREAS.filter(
    (area) => !area || me?.user.data_scopes?.includes(area)
  ).map((area) => ({ value: area, label: AREA_LABEL[area] ?? area }))

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get<{ items: MemoryItem[] }>(
        `/api/memory?category=${tab}`
      )
      setItems(data.items)
      const found = await api.get<{ items: Conflict[] }>(
        '/api/memory/conflicts'
      )
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
      <PageHeader
        eyebrow="Knowledge base"
        title="Company memory"
        description="Your organization’s facts, procedures, and decisions. Corrections preserve the full version history."
      />
      <div className="-mt-2">
        <SegmentedTabs aria-label="Memory categories">
          {TABS.map((name) => (
            <SegmentedTab
              key={name}
              active={tab === name}
              onClick={() => {
                setTab(name)
                setSelected(null)
              }}
            >
              {TAB_LABEL[name]}
            </SegmentedTab>
          ))}
        </SegmentedTabs>
      </div>

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
                <div className="mb-3 font-geist text-primary">
                  Topic: {conflict.topic}
                </div>
                <ul className="space-y-2">
                  {conflict.items.map((entry) => (
                    <li
                      key={entry.version_id}
                      className="flex flex-wrap items-center gap-2"
                    >
                      <Badge
                        tone={
                          entry.approval_status === 'APPROVED' ? 'good' : 'warn'
                        }
                      >
                        {entry.approval_status}
                      </Badge>
                      <span className="text-primary">{entry.value}</span>
                      <span className="text-muted">
                        — {entry.title} ({entry.recorded_by})
                      </span>
                      {hasRole('APPROVER') &&
                        entry.approval_status !== 'APPROVED' && (
                          <Btn
                            size="sm"
                            className="ml-auto"
                            onClick={async () => {
                              try {
                                await api.post(
                                  `/api/memory/versions/${entry.version_id}/approve`
                                )
                                await load()
                              } catch (err) {
                                setError(
                                  err instanceof Error
                                    ? err.message
                                    : 'Could not resolve'
                                )
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

      {tab === 'DOCUMENT' ? (
        <DocumentsPanel isAdmin={hasRole('ADMIN')} onError={setError} />
      ) : (
      <Panel
        title={TAB_LABEL[tab]}
        actions={
          canWrite ? (
            <Btn size="sm" onClick={() => setAdding(true)}>
              <Plus size={15} aria-hidden /> {ADD_LABEL[tab]}
            </Btn>
          ) : undefined
        }
      >
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
                  <span className="text-muted">
                    {item.current?.content ?? '—'}
                  </span>
                )
              },
              {
                key: 'source',
                header: 'Source',
                render: (item) => (
                  <Badge tone="neutral">
                    {item.current?.source_type ?? '—'}
                  </Badge>
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
                        item.current?.approval_status === 'APPROVED'
                          ? 'good'
                          : 'warn'
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
                  <span className="text-muted">
                    {timestamp(item.updated_at)}
                  </span>
                )
              },
              {
                key: 'v',
                header: 'Ver',
                render: (item) => (
                  <span className="text-muted">
                    v{item.current?.version_no ?? 1}
                  </span>
                )
              }
            ]}
          />
        )}
      </Panel>
      )}

      {tab !== 'DOCUMENT' && (
        <AddMemoryDialog
          tab={tab}
          open={adding}
          areas={areaOptions}
          onClose={() => setAdding(false)}
          onSaved={load}
        />
      )}

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
      setRemoveError(
        err instanceof Error ? err.message : 'Could not remove this item'
      )
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
        <button
          onClick={onClose}
          className="text-xs text-muted hover:text-primary"
        >
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
                    tone={
                      version.approval_status === 'APPROVED' ? 'good' : 'warn'
                    }
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
                <p className="mt-1.5 font-geist text-primary">
                  {version.content}
                </p>
                {version.correction_reason && (
                  <p className="mt-1 text-muted">
                    Correction reason: {version.correction_reason}
                    {version.corrected_by
                      ? ` (by ${version.corrected_by})`
                      : ''}
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
          <form
            onSubmit={correct}
            className="space-y-2 border-t border-border pt-4"
          >
            <h3 className="font-geist text-xs uppercase tracking-wide text-muted">
              Correct this value
            </h3>
            <p className="text-[11px] text-muted">
              The current value is kept in history as superseded. Nothing is
              overwritten.
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
          <form
            onSubmit={remove}
            className="space-y-2 border-t border-border pt-4"
          >
            <h3 className="font-geist text-xs uppercase tracking-wide text-muted">
              Remove from memory
            </h3>
            <p className="text-[11px] text-muted">
              FreedomBot stops using this item in answers, search and lists.
              Nothing is erased — the version history stays for audit.
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
            <Btn
              type="submit"
              variant="danger"
              disabled={removing || !removeReason.trim()}
              size="sm"
            >
              {removing ? 'Removing…' : 'Remove from memory'}
            </Btn>
          </form>
        )}
      </div>
    </Panel>
  )
}
