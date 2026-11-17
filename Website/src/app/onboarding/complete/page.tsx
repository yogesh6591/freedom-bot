'use client'

import { useEffect, useState } from 'react'
import { ArrowRight, CheckCircle2, Copy, Eye, EyeOff } from 'lucide-react'
import {
  appSignInUrl,
  loadOnboarding,
  saveOnboarding,
  type OnboardingState
} from '@/lib/onboarding'
import { DOMAINS, getPlan } from '@/lib/plans'

export default function CompletePage() {
  const [state, setState] = useState<OnboardingState | null>(null)
  // Shown once on this page, then erased from the browser.
  const [password, setPassword] = useState('')
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)

  useEffect(() => {
    const current = loadOnboarding()
    if (!current.paid || !current.provisioned || !current.workspaceSlug) {
      window.location.href = '/signup'
      return
    }
    setState(current)
    setPassword(current.password)
    if (current.password) saveOnboarding({ password: '' })
  }, [])

  if (!state) {
    return <div className="mx-auto max-w-xl text-sm text-fg-muted">Loading…</div>
  }

  const plan = getPlan(state.planId)
  const domainLabels = DOMAINS.filter((d) =>
    state.purchasedDomains.includes(d.id)
  ).map((d) => d.label)
  const signInUrl = appSignInUrl(state.workspaceSlug)

  async function copy(label: string, value: string) {
    await navigator.clipboard.writeText(value)
    setCopied(label)
    setTimeout(() => setCopied(null), 1600)
  }

  const rows: { label: string; value: string; secret?: boolean }[] = [
    { label: 'Workspace ID', value: state.workspaceSlug },
    { label: 'Admin email', value: state.email },
    ...(password ? [{ label: 'Password', value: password, secret: true }] : [])
  ]

  return (
    <div className="mx-auto max-w-2xl">
      <div className="panel p-8 sm:p-10">
        <CheckCircle2 className="h-10 w-10 text-accent" />
        <p className="eyebrow mt-5">Onboarding complete</p>
        <h1 className="mt-3 font-display text-3xl font-semibold tracking-[-0.03em] text-fg sm:text-4xl">
          {state.companyName} is ready
        </h1>
        <p className="mt-4 text-fg-muted">
          Payment received for the <strong className="text-fg">{plan.name}</strong>{' '}
          plan. Your FreedomBot workspace and admin account are created, with the
          departments you chose.
        </p>

        <div className="mt-8 rounded-xl border border-line bg-surface-2 p-5 text-sm">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
            Your sign-in details
          </p>
          <div className="mt-3 grid gap-3">
            {rows.map((row) => (
              <div key={row.label} className="flex items-center justify-between gap-3">
                <span className="text-fg-muted">{row.label}</span>
                <span className="flex items-center gap-2 font-mono text-fg">
                  {row.secret && !reveal ? '••••••••••••' : row.value}
                  {row.secret && (
                    <button
                      type="button"
                      aria-label={reveal ? 'Hide password' : 'Show password'}
                      onClick={() => setReveal((v) => !v)}
                      className="text-fg-muted hover:text-fg"
                    >
                      {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-label={`Copy ${row.label}`}
                    onClick={() => void copy(row.label, row.value)}
                    className="text-fg-muted hover:text-fg"
                  >
                    <Copy size={14} />
                  </button>
                  {copied === row.label && <span className="text-xs text-accent">Copied</span>}
                </span>
              </div>
            ))}
          </div>
          {password && (
            <p className="mt-4 text-xs leading-5 text-fg-muted">
              Save the password now: it is shown only on this page and is not kept in your browser.
            </p>
          )}
        </div>

        <div className="mt-4 grid gap-3 rounded-xl border border-line bg-surface-2 p-5 text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="text-fg-muted">Plan</span>
            <span className="text-fg">
              {plan.name} · {plan.priceLabel}/mo
            </span>
          </div>
          <div className="border-t border-line pt-3">
            <p className="text-fg-muted">Departments</p>
            <p className="mt-1 text-fg">{domainLabels.join(' · ')}</p>
          </div>
        </div>

        <div className="mt-8">
          <a href={signInUrl} className="btn-primary">
            Sign in to FreedomBot
            <ArrowRight size={16} />
          </a>
        </div>

        <p className="mt-6 text-xs leading-5 text-fg-muted">
          Sign in with workspace ID <span className="font-mono text-fg">{state.workspaceSlug}</span>,
          your admin email and the password above. From Administration you can add your team.
        </p>
      </div>
    </div>
  )
}
