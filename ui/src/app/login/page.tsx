'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import { ErrorNote, Field, inputClass } from '@/components/common/primitives'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [clientSlug, setClientSlug] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const { refresh } = useSession()

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
      router.replace('/chat')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex min-h-dvh overflow-hidden bg-login-atmosphere">
      <div className="relative z-10 flex w-full flex-col justify-between p-8 md:w-[46%] md:p-12 lg:p-16">
        <div>
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand text-base font-bold text-brand-ink shadow-glow">
              FB
            </span>
            <span className="font-geist text-xl font-bold tracking-tight text-primary">
              FreedomBot
            </span>
          </div>
        </div>

        <div className="my-10 max-w-md space-y-4 md:my-0">
          <p className="font-dmmono text-[11px] uppercase tracking-[0.2em] text-brand-deep">
            Company AI workspace
          </p>
          <h1 className="font-geist text-4xl font-bold leading-[1.1] tracking-tight text-primary text-balance md:text-5xl">
            Your team&apos;s memory, approvals, and actions — in one place.
          </h1>
          <p className="text-base leading-relaxed text-muted">
            Sign in to the workspace your account belongs to. Role and permissions are set by
            your admin, not by this form.
          </p>
        </div>

        <p className="hidden text-xs text-muted md:block">
          Policy-governed · Isolated per client · Human approval when it matters
        </p>
      </div>

      <div className="relative z-10 flex flex-1 items-center justify-center p-6 md:p-10">
        <form
          onSubmit={submit}
          className="w-full max-w-md space-y-5 rounded-2xl border border-border bg-white p-8 shadow-lift animate-fade-up"
        >
          <div>
            <h2 className="font-geist text-2xl font-bold tracking-tight text-primary">
              Welcome back
            </h2>
            <p className="mt-1 text-sm text-muted">Enter your work email to continue.</p>
          </div>

          {error && <ErrorNote>{error}</ErrorNote>}

          <Field label="Email">
            <input
              className={inputClass}
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </Field>
          <Field label="Password">
            <input
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
            hint="Only needed if the same email exists at more than one company."
          >
            <input
              className={inputClass}
              placeholder="optional — e.g. acme"
              value={clientSlug}
              onChange={(event) => setClientSlug(event.target.value)}
            />
          </Field>

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-full bg-brand px-4 py-3.5 font-geist text-sm font-bold text-brand-ink shadow-glow transition hover:bg-brand-soft disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
