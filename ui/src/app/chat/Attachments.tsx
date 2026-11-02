'use client'

/**
 * Files attached in the chat: the paperclip, the chips waiting above the message box, and the chips on a
 * sent message. Each file is uploaded as soon as it is picked (POST /api/chat/attachments), where the
 * server reads it and applies the same data rules as a typed message; the message then sends their ids.
 */

import { useCallback, useRef, useState } from 'react'
import { FileSpreadsheet, FileText, Loader2, Paperclip, X } from 'lucide-react'
import { api } from '@/lib/api'
import type { ChatAttachment } from '@/lib/types'

export const ACCEPT = '.pdf,.docx,.csv,.xlsx,.txt,.md'
const EXTENSIONS = ACCEPT.split(',')
const MAX_FILES = 5
const MAX_BYTES = 10 * 1024 * 1024

export interface PendingFile {
  key: string
  name: string
  status: 'uploading' | 'ready' | 'error'
  card?: ChatAttachment
  error?: string
}

function problemWith(file: File): string | null {
  const name = file.name.toLowerCase()
  if (name.endsWith('.doc')) return 'Old .doc files can’t be read. Save it as .docx or PDF.'
  if (!EXTENSIONS.some((ext) => name.endsWith(ext))) return 'Attach a PDF, Word (.docx), CSV, Excel (.xlsx) or text file.'
  if (file.size > MAX_BYTES) return 'The file is larger than 10 MB.'
  return null
}

/** The files waiting to go with the next message. */
export function useAttachments() {
  const [files, setFiles] = useState<PendingFile[]>([])
  const counter = useRef(0)

  const add = useCallback((list: FileList | File[]) => {
    const picked = Array.from(list)
    setFiles((current) => {
      const room = MAX_FILES - current.length
      const next = [...current]
      for (const file of picked.slice(0, Math.max(0, room))) {
        const key = `f${++counter.current}`
        const problem = problemWith(file)
        next.push({ key, name: file.name, status: problem ? 'error' : 'uploading', error: problem ?? undefined })
        if (problem) continue
        const form = new FormData()
        form.append('file', file)
        api
          .upload<ChatAttachment>('/api/chat/attachments', form)
          .then((card) =>
            setFiles((rows) => rows.map((r) => (r.key === key ? { ...r, status: 'ready', card } : r)))
          )
          .catch((err: unknown) =>
            setFiles((rows) =>
              rows.map((r) =>
                r.key === key
                  ? { ...r, status: 'error', error: err instanceof Error ? err.message : 'Upload failed' }
                  : r
              )
            )
          )
      }
      if (picked.length > room) {
        next.push({ key: `f${++counter.current}`, name: `${picked.length - Math.max(0, room)} more`, status: 'error',
                    error: `Attach at most ${MAX_FILES} files to one message.` })
      }
      return next
    })
  }, [])

  const remove = useCallback((key: string) => setFiles((rows) => rows.filter((r) => r.key !== key)), [])
  const ready = files.filter((f) => f.status === 'ready' && f.card).map((f) => f.card as ChatAttachment)
  const uploading = files.some((f) => f.status === 'uploading')

  return {
    files,
    ready,
    uploading,
    add,
    remove,
    /** Clear after sending; returns what was sent so a failed send can put it back. */
    take: () => {
      const sent = files.filter((f) => f.status === 'ready')
      setFiles([])
      return sent
    },
    restore: (rows: PendingFile[]) => setFiles((current) => [...rows, ...current])
  }
}

function FileIcon({ kind, size = 14 }: { kind?: string; size?: number }) {
  return kind === 'csv' || kind === 'xlsx' ? (
    <FileSpreadsheet size={size} aria-hidden className="shrink-0" />
  ) : (
    <FileText size={size} aria-hidden className="shrink-0" />
  )
}

function shape(card: ChatAttachment): string {
  if (card.pages) return `${card.pages} page${card.pages === 1 ? '' : 's'}`
  if (card.rows !== undefined) return `${card.rows} row${card.rows === 1 ? '' : 's'}`
  return card.kind.toUpperCase()
}

/** The paperclip button and its hidden file picker. */
export function AttachButton({ onPick, disabled }: { onPick: (files: FileList) => void; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(event) => {
          if (event.target.files?.length) onPick(event.target.files)
          event.target.value = ''
        }}
      />
      <button
        type="button"
        aria-label="Attach a file"
        title="Attach a PDF, Word, CSV, Excel or text file"
        disabled={disabled}
        onClick={() => input.current?.click()}
        className="mb-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted transition-colors hover:bg-background-elevated hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Paperclip size={18} aria-hidden />
      </button>
    </>
  )
}

/** Chips above the message box for files waiting to be sent. */
export function PendingFiles({ files, onRemove }: { files: PendingFile[]; onRemove: (key: string) => void }) {
  if (!files.length) return null
  return (
    <ul className="flex flex-wrap gap-2 px-1 pb-2 pt-1" aria-label="Attached files">
      {files.map((f) => (
        <li
          key={f.key}
          title={f.error || f.name}
          className={`flex max-w-[16rem] items-center gap-2 rounded-xl border px-2.5 py-1.5 text-[12px] ${
            f.status === 'error'
              ? 'border-destructive/40 bg-destructive/10 text-destructive'
              : 'border-border bg-background-elevated text-primary'
          }`}
        >
          {f.status === 'uploading' ? (
            <Loader2 size={14} aria-hidden className="shrink-0 animate-spin" />
          ) : (
            <FileIcon kind={f.card?.kind} />
          )}
          <span className="min-w-0">
            <span className="block truncate font-medium">{f.name}</span>
            <span className="block truncate text-[11px] opacity-75">
              {f.status === 'uploading' ? 'Reading…' : f.status === 'error' ? f.error : f.card ? shape(f.card) : ''}
            </span>
          </span>
          <button
            type="button"
            aria-label={`Remove ${f.name}`}
            onClick={() => onRemove(f.key)}
            className="ml-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full opacity-70 hover:bg-black/10 hover:opacity-100"
          >
            <X size={12} aria-hidden />
          </button>
        </li>
      ))}
    </ul>
  )
}

/** Chips on a sent message. */
export function FileChips({ files }: { files?: ChatAttachment[] }) {
  if (!files?.length) return null
  return (
    <div className="flex max-w-[min(80%,28rem)] flex-wrap justify-end gap-1.5">
      {files.map((card) => (
        <span
          key={card.id}
          title={card.name}
          className="flex max-w-[14rem] items-center gap-1.5 rounded-xl border border-border bg-background-elevated px-2.5 py-1.5 text-[12px] text-primary"
        >
          <FileIcon kind={card.kind} />
          <span className="truncate font-medium">{card.name}</span>
          <span className="shrink-0 text-[11px] text-muted">{shape(card)}</span>
        </span>
      ))}
    </div>
  )
}
