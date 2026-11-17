'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { FormEvent, Suspense, useMemo, useState } from 'react'
import { BrandMark } from '@/components/BrandMark'
import { ThemeToggle } from '@/components/ThemeToggle'
import { DEMO_FILLER } from '@/lib/demo-filler'
import { getPlan, type PlanId } from '@/lib/plans'
import { saveOnboarding } from '@/lib/onboarding'
import { APP_URL } from '@/lib/urls'

function SignupForm() {
  const router = useRouter()
  const params = useSearchParams()
  const planId = (params.get('plan') as PlanId) || DEMO_FILLER.planId
  const plan = useMemo(() => getPlan(planId), [planId])

  const [fullName, setFullName] = useState(DEMO_FILLER.fullName)
  const [email, setEmail] = useState(DEMO_FILLER.email)
  const [password, setPassword] = useState(DEMO_FILLER.password)
  const [agree, setAgree] = useState(DEMO_FILLER.agree)
  const [error, setError] = useState<string | null>(null)

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (!fullName.trim() || !email.trim() || password.length < 12) {
      setError('Enter your name, work email, and a password of at least 12 characters.')
      return
    }
    if (!agree) {
      setError('Please accept the Terms and Privacy Policy to continue.')
      return
    }

    saveOnboarding({
      fullName: fullName.trim(),
      email: email.trim().toLowerCase(),
      password,
      planId: plan.id,
      paid: false
    })
    router.push('/onboarding/company')
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <aside className="relative hidden overflow-hidden border-r border-line bg-surface-2 p-10 lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center justify-between gap-3">
          <BrandMark />
          <ThemeToggle />
        </div>
        <div>
          <p className="eyebrow">Create your workspace</p>
          <h1 className="mt-4 font-display text-4xl font-semibold tracking-[-0.04em] text-fg">
            Start FreedomBot onboarding
          </h1>
          <p className="mt-4 max-w-md text-fg-muted">
            You&apos;re starting on the <strong className="text-fg">{plan.name}</strong>{' '}
            plan ({plan.priceLabel}/mo). You can change it before payment.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-fg-muted">
            <li>1. Create account</li>
            <li>2. Company & workspace</li>
            <li>3. Choose domain access (Sales, Finance, …)</li>
            <li>4. Confirm plan & pay</li>
            <li>5. Sign into FreedomBot with those domains only</li>
          </ul>
        </div>
        <p className="text-xs text-fg-muted">
          After onboarding, use the same credentials on the main FreedomBot app.
        </p>
      </aside>

      <main className="flex items-center justify-center px-5 py-12">
        <div className="w-full max-w-md">
          <div className="mb-8 flex items-center justify-between gap-3 lg:hidden">
            <BrandMark />
            <ThemeToggle />
          </div>
          <h2 className="font-display text-3xl font-semibold tracking-[-0.03em] text-fg">
            Create account
          </h2>
          <p className="mt-2 text-sm text-fg-muted">
            Already have an account?{' '}
            <a href={APP_URL} className="link">
              Sign in
            </a>
          </p>

          <form className="mt-8 space-y-4" onSubmit={onSubmit}>
            {error && (
              <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                {error}
              </div>
            )}
            <div>
              <label className="label" htmlFor="fullName">
                Full name
              </label>
              <input
                id="fullName"
                className="field"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                autoComplete="name"
                placeholder="Alex Rivera"
              />
            </div>
            <div>
              <label className="label" htmlFor="email">
                Work email
              </label>
              <input
                id="email"
                type="email"
                className="field"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                placeholder="alex@company.com"
              />
            </div>
            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                className="field"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="At least 12 characters"
              />
            </div>
            <label className="flex items-start gap-3 text-sm text-fg-muted">
              <input
                type="checkbox"
                className="mt-1"
                checked={agree}
                onChange={(e) => setAgree(e.target.checked)}
              />
              <span>
                I agree to the{' '}
                <Link href="/terms" className="link">
                  Terms
                </Link>{' '}
                and{' '}
                <Link href="/privacy" className="link">
                  Privacy Policy
                </Link>
                .
              </span>
            </label>
            <button type="submit" className="btn-primary w-full">
              Continue to company setup
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}

export default function SignupPage() {
  return (
    <Suspense fallback={<div className="min-h-screen" />}>
      <SignupForm />
    </Suspense>
  )
}
