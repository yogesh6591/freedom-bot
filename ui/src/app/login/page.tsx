'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import { announceSessionChange, takeNotice } from '@/lib/sessionSync'
import { Btn, ErrorNote, Field, inputClass } from '@/components/common/primitives'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [clientSlug, setClientSlug] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNoticeText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const { refresh } = useSession()

  useEffect(() => {
    setNoticeText(takeNotice())
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/login', {
        email,
        password,
        client_slug: clientSlug || undefined
      })
      await refresh()
      // Other open tabs must stop showing the previous workspace (M01-06).
      announceSessionChange()
      router.replace('/chat')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden bg-login-atmosphere lg:flex-row">
      <div className="relative z-10 flex w-full flex-col justify-between gap-10 p-6 sm:p-8 md:p-12 lg:w-[46%] lg:p-16">
        <div className="flex items-center gap-3">
          <span
            className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-base font-bold text-brand-ink shadow-glow"
            aria-hidden
          >
            FB
          </span>
          <span className="font-geist text-xl font-bold tracking-tight text-primary">
            FreedomBot
          </span>
        </div>

        <div className="max-w-md space-y-4">
          <p className="type-caption text-brand-soft">One company AI</p>
          <h1 className="type-display text-4xl md:text-5xl">
            Talk to FreedomBot. It handles the rest.
          </h1>
          <p className="type-muted max-w-prose text-base">
            Ask in chat — routing, memory, and approvals stay behind the scenes. Not a
            wall of Campaigns, Finance, or Ops modules.
          </p>
        </div>

        <p className="hidden text-xs text-muted lg:block">
          Chat-first · Policy-governed · Isolated per client
        </p>
      </div>

      <div className="relative z-10 flex flex-1 items-center justify-center p-6 sm:p-8 md:p-10">
        <form
          onSubmit={submit}
          className="w-full max-w-md space-y-6 rounded-xl border border-border bg-background-secondary/95 p-6 shadow-lift animate-fade-up sm:p-8"
          noValidate
        >
          <div className="space-y-1.5">
            <h2 className="type-title text-2xl">Welcome back</h2>
            <p className="type-muted">Enter your work email to continue.</p>
          </div>

          {notice && (
            <p role="status" className="rounded-xl border border-border bg-background-elevated px-4 py-3 text-sm text-muted">
              {notice}
            </p>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}

          <div className="space-y-4">
            <Field label="Email" htmlFor="login-email">
              <input
                id="login-email"
                className={inputClass}
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </Field>
            <Field label="Password" htmlFor="login-password">
              <input
                id="login-password"
                className={inputClass}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </Field>
            <Field
              label="Workspace"
              htmlFor="login-workspace"
              hint="Only needed if the same email exists at more than one company."
            >
              <input
                id="login-workspace"
                className={inputClass}
                placeholder="optional — e.g. acme"
                value={clientSlug}
                onChange={(event) => setClientSlug(event.target.value)}
              />
            </Field>
          </div>

          <Btn type="submit" disabled={busy} className="w-full rounded-full" size="lg">
            {busy ? 'Signing in…' : 'Continue'}
          </Btn>
        </form>
      </div>
    </div>
  )
}
