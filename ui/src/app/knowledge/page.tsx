'use client'

/** Knowledge (§17) — upload, ingestion status, search. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorNote,
  Panel,
  inputClass,
  statusTone,
  timestamp
} from '@/components/common/primitives'

interface Content {
  id: string
  name: string
  description: string | null
  type: string | null
  size: number | null
  status: string | null
  status_message: string | null
  created_at: string | null
}

export default function KnowledgePage() {
  return (
    <AppShell>
      <KnowledgeView />
    </AppShell>
  )
}

function KnowledgeView() {
  const { hasRole } = useSession()
  const [items, setItems] = useState<Content[]>([])
  const [available, setAvailable] = useState(true)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Record<string, unknown>[]>([])
  const [findings, setFindings] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const canUpload = hasRole('DRAFTER', 'APPROVER', 'OPERATOR')

  const load = useCallback(async () => {
    try {
      const data = await api.get<{ items: Content[]; vector_search_available: boolean }>(
        '/api/knowledge'
      )
      setItems(data.items)
      setAvailable(data.vector_search_available)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load documents')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('name', file.name)
      await api.upload('/api/knowledge/upload', form)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function search(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      const data = await api.get<{
        items: Record<string, unknown>[]
        untrusted_findings: number
      }>(`/api/knowledge/search?q=${encodeURIComponent(query)}`)
      setResults(data.items)
      setFindings(data.untrusted_findings)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Panel
        title="Knowledge"
        description="Documents this workspace has ingested. Retrieved text is treated as untrusted data — instruction-shaped content inside a document is neutralized before the assistant sees it."
        actions={
          canUpload ? (
            <label className="cursor-pointer rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted">
              {busy ? 'Working…' : 'Upload document'}
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.docx,.txt,.md,.csv,.json"
                onChange={(event) => void upload(event)}
                className="hidden"
              />
            </label>
          ) : null
        }
      >
        {!available && (
          <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
            Vector search is unavailable in this deployment (it needs an embedding model
            and the pgvector extension). Search falls back to keyword matching over
            document titles and descriptions.
          </div>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        <DataTable
          rows={items}
          empty="No documents ingested yet. PDF, DOCX, TXT, MD, CSV and JSON are supported."
          columns={[
            { key: 'n', header: 'Name', render: (row) => <span className="font-geist text-primary">{row.name}</span> },
            { key: 't', header: 'Type', render: (row) => row.type ?? '—' },
            {
              key: 's',
              header: 'Status',
              render: (row) => (
                <Badge tone={statusTone(row.status)}>{row.status ?? 'unknown'}</Badge>
              )
            },
            { key: 'm', header: 'Detail', render: (row) => <span className="text-muted">{row.status_message ?? '—'}</span> },
            { key: 'c', header: 'Added', render: (row) => <span className="text-muted">{timestamp(row.created_at)}</span> }
          ]}
        />
      </Panel>

      <Panel title="Search">
        <form onSubmit={search} className="flex gap-2">
          <input
            className={inputClass}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the knowledge base"
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-white disabled:opacity-50"
          >
            Search
          </button>
        </form>
        {findings > 0 && (
          <p className="mt-2 text-xs text-amber-300">
            {findings} instruction-shaped span(s) were neutralized in these results.
          </p>
        )}
        <div className="mt-3 space-y-2">
          {results.length === 0 ? (
            <EmptyState>No results yet.</EmptyState>
          ) : (
            results.map((result, index) => (
              <div key={index} className="rounded-lg border border-border p-3 text-xs">
                <div className="font-geist text-primary">{String(result.name ?? 'Untitled')}</div>
                <p className="mt-1 whitespace-pre-wrap text-muted">
                  {String(result.content ?? '').slice(0, 600)}
                </p>
              </div>
            ))
          )}
        </div>
      </Panel>
    </div>
  )
}
