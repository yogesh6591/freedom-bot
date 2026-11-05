'use client'

import { useEffect, useState } from 'react'
import {
  Btn,
  ErrorNote,
  Field,
  inputClass
} from '@/components/common/primitives'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { api } from '@/lib/api'
import type { MemoryItem } from '@/lib/types'

export type AddableTab = 'FACT' | 'SOP' | 'DECISION' | 'CORRECTION'

export const ADD_LABEL: Record<AddableTab, string> = {
  FACT: 'Add fact',
  SOP: 'Add SOP',
  DECISION: 'Add decision',
  CORRECTION: 'Add correction'
}

const FORM_COPY = {
  FACT: {
    intro: 'A fact the assistant can use in its answers.',
    title: 'Standard discount',
    body: 'The fact',
    placeholder: '10% on annual contracts above $100,000.'
  },
  SOP: {
    intro: 'A procedure your team should follow.',
    title: 'New client onboarding',
    body: 'The steps',
    placeholder: 'Write each step on its own line.'
  },
  DECISION: {
    intro: 'A decision your company has made.',
    title: 'Cloud provider for 2027',
    body: 'The decision',
    placeholder: 'What was decided, who decided and why.'
  }
}

/** Adds a fact, SOP or decision, or corrects one. Corrections keep the old value in the item's history. */
export function AddMemoryDialog({
  tab,
  open,
  areas,
  onClose,
  onSaved
}: {
  tab: AddableTab
  open: boolean
  areas: { value: string; label: string }[]
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [area, setArea] = useState('')
  const [reason, setReason] = useState('')
  const [itemId, setItemId] = useState('')
  const [choices, setChoices] = useState<MemoryItem[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const correcting = tab === 'CORRECTION'

  useEffect(() => {
    if (!open) return
    setTitle('')
    setContent('')
    setArea('')
    setReason('')
    setItemId('')
    setError(null)
    setChoices(null)
    if (tab !== 'CORRECTION') return
    Promise.all(
      ['FACT', 'SOP', 'DECISION'].map((category) =>
        api.get<{ items: MemoryItem[] }>(
          `/api/memory?category=${category}&limit=500`
        )
      )
    )
      .then((lists) =>
        setChoices(
          lists
            .flatMap((list) => list.items)
            .sort((a, b) => a.title.localeCompare(b.title))
        )
      )
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'Could not load memory')
      )
  }, [open, tab])

  const target = choices?.find((item) => item.id === itemId)

  function pick(id: string) {
    setItemId(id)
    setContent(choices?.find((item) => item.id === id)?.current?.content ?? '')
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (correcting) {
        await api.post(`/api/memory/${itemId}/correct`, {
          new_content: content,
          reason
        })
      } else {
        await api.post('/api/memory', {
          category: tab,
          title,
          content,
          access_area: area || null
        })
      }
      await onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  const unchanged = correcting && content === target?.current?.content

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{ADD_LABEL[tab]}</DialogTitle>
          <DialogDescription className="text-muted">
            {correcting
              ? 'Fix a value in company memory. The old value stays in its history.'
              : FORM_COPY[tab].intro}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={save} className="space-y-4 pt-2">
          {error && <ErrorNote>{error}</ErrorNote>}
          {correcting ? (
            choices?.length === 0 ? (
              <p className="text-sm text-muted">
                There is nothing to correct yet. Add a fact, SOP or decision
                first.
              </p>
            ) : (
              <>
                <Field label="What are you correcting?">
                  <select
                    className={inputClass}
                    required
                    value={itemId}
                    onChange={(event) => pick(event.target.value)}
                  >
                    <option value="" disabled>
                      {choices ? 'Choose an item' : 'Loading…'}
                    </option>
                    {choices?.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                  </select>
                </Field>
                {target && (
                  <>
                    <Field label="Correct value">
                      <textarea
                        className={`${inputClass} min-h-28 resize-y`}
                        required
                        value={content}
                        onChange={(event) => setContent(event.target.value)}
                      />
                    </Field>
                    <Field label="Why is it changing?">
                      <input
                        className={inputClass}
                        required
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                      />
                    </Field>
                  </>
                )}
              </>
            )
          ) : (
            <>
              <Field label="Title">
                <input
                  className={inputClass}
                  required
                  value={title}
                  placeholder={FORM_COPY[tab].title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </Field>
              <Field label={FORM_COPY[tab].body}>
                <textarea
                  className={`${inputClass} min-h-28 resize-y`}
                  required
                  value={content}
                  placeholder={FORM_COPY[tab].placeholder}
                  onChange={(event) => setContent(event.target.value)}
                />
              </Field>
              <Field label="Who can see it">
                <select
                  className={inputClass}
                  value={area}
                  onChange={(event) => setArea(event.target.value)}
                >
                  {areas.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
            </>
          )}
          <div className="flex justify-end gap-2">
            <Btn variant="secondary" size="sm" onClick={onClose}>
              Cancel
            </Btn>
            {!(correcting && !target) && (
              <Btn type="submit" size="sm" loading={busy} disabled={unchanged}>
                Save
              </Btn>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
