'use client'

import { useEffect, useState } from 'react'
import { Check, CheckCircle2, Mail } from 'lucide-react'
import { useSession } from '@/components/SessionProvider'
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
import {
  DATA_TAGS,
  accessEmailLink,
  tagLabel,
  type AccessOverview
} from '@/lib/access'
import { cn } from '@/lib/utils'

/** Lets a person ask an admin for data they cannot see. Nothing changes until an admin grants it. */
export function RequestAccessDialog({
  open,
  onClose
}: {
  open: boolean
  onClose: () => void
}) {
  const { me } = useSession()
  const [access, setAccess] = useState<AccessOverview | null>(null)
  const [picked, setPicked] = useState<string[]>([])
  const [reason, setReason] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setPicked([])
    setReason('')
    setSent(false)
    setError(null)
    api
      .get<AccessOverview>('/api/access')
      .then(setAccess)
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'Could not load access')
      )
  }, [open])

  const available = DATA_TAGS.filter(
    (tag) => !access?.granted.includes(tag.value)
  )

  function toggle(tag: string) {
    setPicked((current) =>
      current.includes(tag)
        ? current.filter((item) => item !== tag)
        : [...current, tag]
    )
  }

  async function send() {
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/access/requests', { areas: picked, reason })
      setSent(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not send request')
    } finally {
      setBusy(false)
    }
  }

  const emailLink = accessEmailLink(
    access?.admin_emails ?? [],
    picked,
    reason.trim(),
    me?.user.display_name ?? ''
  )

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Request access</DialogTitle>
          <DialogDescription className="text-muted">
            Choose the data you need. An admin reviews your request.
          </DialogDescription>
        </DialogHeader>

        {error && <ErrorNote>{error}</ErrorNote>}

        {sent ? (
          <div className="space-y-4 pt-2">
            <p className="flex items-start gap-2 text-sm text-primary">
              <CheckCircle2
                size={18}
                className="mt-0.5 shrink-0 text-success"
                aria-hidden
              />
              <span>
                Request saved for {picked.map(tagLabel).join(', ')}. Your admin
                can grant it from the Users page.
              </span>
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <Btn variant="secondary" size="sm" onClick={onClose}>
                Done
              </Btn>
              {(access?.admin_emails.length ?? 0) > 0 && (
                <Btn
                  size="sm"
                  onClick={() => {
                    window.location.href = emailLink
                  }}
                >
                  <Mail size={15} aria-hidden /> Email your admin
                </Btn>
              )}
            </div>
          </div>
        ) : !access ? (
          !error && <p className="pt-2 text-sm text-muted">Loading…</p>
        ) : available.length === 0 ? (
          <p className="pt-2 text-sm text-muted">
            You already have access to every area.
          </p>
        ) : (
          <div className="space-y-4 pt-2">
            <div className="flex flex-wrap gap-2">
              {available.map((tag) => {
                const waiting = access.pending.includes(tag.value)
                const on = picked.includes(tag.value)
                return (
                  <button
                    key={tag.value}
                    type="button"
                    disabled={waiting}
                    aria-pressed={on}
                    onClick={() => toggle(tag.value)}
                    className={cn(
                      'ui-transition inline-flex min-h-10 items-center gap-1.5 rounded-full border px-3.5 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-60',
                      on
                        ? 'border-brand bg-brand/15 text-primary'
                        : 'border-border text-secondary hover:border-brand/50'
                    )}
                  >
                    {on && <Check size={13} aria-hidden />}
                    {tag.label}
                    {waiting ? ' · requested' : ''}
                  </button>
                )
              })}
            </div>
            <Field label="Why do you need it?" hint="Optional.">
              <textarea
                className={cn(inputClass, 'min-h-20 resize-none')}
                maxLength={500}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </Field>
            <div className="flex justify-end">
              <Btn
                size="sm"
                loading={busy}
                disabled={picked.length === 0}
                onClick={() => void send()}
              >
                Send request
              </Btn>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
