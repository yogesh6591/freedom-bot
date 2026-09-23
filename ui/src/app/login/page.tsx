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
      // Deliberately not distinguishing "no such user" from "wrong password" —
      // the server does not either.
      setError(err instanceof Error ? err.message : 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-dvh items-center justify-center bg-background px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-xl border border-border bg-background-secondary/40 p-6"
      >
        <div>
          <h1 className="font-geist text-lg text-primary">Sign in</h1>
          <p className="mt-1 text-xs text-muted">
            Your workspace and role are determined by your account, not by this form.
          </p>
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
          hint="Only needed if the same email exists at more than one workspace."
        >
          <input
            className={inputClass}
            placeholder="optional"
            value={clientSlug}
            onChange={(event) => setClientSlug(event.target.value)}
          />
        </Field>

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-brand px-4 py-2 font-geist text-sm text-white transition disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
