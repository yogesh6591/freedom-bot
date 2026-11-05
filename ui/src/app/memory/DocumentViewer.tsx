'use client'

import { useEffect, useState } from 'react'
import { Download } from 'lucide-react'
import { Btn, ErrorNote } from '@/components/common/primitives'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { api } from '@/lib/api'

export interface ViewableDocument {
  document_id: string
  title: string
  filename: string | null
  has_file?: boolean
}

function saveFile(url: string, filename: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
}

/**
 * Shows an uploaded document. A PDF opens as the original file. Browsers cannot display Word files, and files
 * uploaded before originals were kept have only their text, so those show as text.
 */
export function DocumentViewer({
  doc,
  onClose
}: {
  doc: ViewableDocument | null
  onClose: () => void
}) {
  const [fileUrl, setFileUrl] = useState<string | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isPdf = Boolean(doc?.filename?.toLowerCase().endsWith('.pdf'))

  useEffect(() => {
    setFileUrl(null)
    setText(null)
    setError(null)
    if (!doc) return
    let url: string | null = null
    let gone = false
    const base = `/api/memory/documents/${doc.document_id}`
    const fail = (err: unknown) =>
      !gone &&
      setError(err instanceof Error ? err.message : 'Could not open document')
    if (doc.has_file) {
      api
        .blob(`${base}/file`)
        .then((blob) => {
          if (gone) return
          url = URL.createObjectURL(blob)
          setFileUrl(url)
        })
        .catch(fail)
    }
    if (!doc.has_file || !doc.filename?.toLowerCase().endsWith('.pdf')) {
      api
        .get<{ text: string }>(`${base}/text`)
        .then((data) => !gone && setText(data.text))
        .catch(fail)
    }
    return () => {
      gone = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [doc])

  return (
    <Dialog open={doc !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-4xl">
        <DialogHeader>
          <DialogTitle className="pr-8">{doc?.title}</DialogTitle>
          <DialogDescription className="text-muted">
            {doc?.filename}
          </DialogDescription>
        </DialogHeader>

        <div className="pt-3">
          {error && <ErrorNote>{error}</ErrorNote>}
          {isPdf && doc?.has_file && fileUrl ? (
            <iframe
              src={fileUrl}
              title={doc.title}
              className="h-[70vh] w-full rounded-lg border border-border bg-white"
            />
          ) : text !== null ? (
            <>
              {!doc?.has_file && (
                <p className="mb-3 rounded-lg border border-border bg-background-elevated px-3.5 py-2.5 text-xs text-secondary">
                  The original file was not kept for this upload, so this is its
                  text. Upload it again to see the original.
                </p>
              )}
              <div className="max-h-[65vh] overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-background-elevated/40 p-4 text-sm leading-relaxed text-primary">
                {text}
              </div>
            </>
          ) : (
            !error && (
              <p className="py-10 text-center text-sm text-muted">Loading…</p>
            )
          )}
          {fileUrl && doc?.filename && (
            <div className="mt-3 flex justify-end">
              <Btn
                variant="secondary"
                size="sm"
                onClick={() => saveFile(fileUrl, doc.filename ?? 'document')}
              >
                <Download size={15} aria-hidden /> Download
              </Btn>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
