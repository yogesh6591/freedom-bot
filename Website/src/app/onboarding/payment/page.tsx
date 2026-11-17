'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { FormEvent, useEffect, useMemo, useState } from 'react'
import { CreditCard, Lock } from 'lucide-react'
import { DEMO_FILLER } from '@/lib/demo-filler'
import {
  loadOnboarding,
  saveOnboarding,
  type OnboardingState
} from '@/lib/onboarding'
import { DOMAINS, formatUsd, getPlan } from '@/lib/plans'

export default function PaymentStepPage() {
  const router = useRouter()
  const [state, setState] = useState<OnboardingState | null>(null)
  const [cardName, setCardName] = useState(DEMO_FILLER.cardName)
  const [cardNumber, setCardNumber] = useState(DEMO_FILLER.cardNumber)
  const [expiry, setExpiry] = useState(DEMO_FILLER.expiry)
  const [cvc, setCvc] = useState(DEMO_FILLER.cvc)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [slugTaken, setSlugTaken] = useState(false)

  useEffect(() => {
    const current = loadOnboarding()
    if (!current.email) {
      router.replace('/signup')
      return
    }
    if (!current.workspaceSlug) {
      router.replace('/onboarding/company')
      return
    }
    if (!current.purchasedDomains?.length) {
      router.replace('/onboarding/domain')
      return
    }
    setState(current)
    setCardName(current.fullName || DEMO_FILLER.cardName)
    setCardNumber(DEMO_FILLER.cardNumber)
    setExpiry(DEMO_FILLER.expiry)
    setCvc(DEMO_FILLER.cvc)
  }, [router])

  const plan = useMemo(
    () => getPlan(state?.planId),
    [state?.planId]
  )
  const domains = DOMAINS.filter((d) =>
    (state?.purchasedDomains ?? []).includes(d.id)
  )

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!state) return
    setError(null)

    if (cardNumber.replace(/\s/g, '').length < 12 || expiry.length < 4 || cvc.length < 3) {
      setError('Enter valid card details to continue. Demo mode accepts any plausible values.')
      return
    }

    setBusy(true)
    setSlugTaken(false)
    // Demo checkout: replace with Stripe Checkout / Billing when wiring production payments.
    await new Promise((resolve) => setTimeout(resolve, 900))

    // Payment done: FreedomBot makes the workspace, the departments and the admin account.
    let created: { workspace?: string; detail?: unknown } = {}
    let ok = false
    try {
      const response = await fetch('/api/provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...state, paymentReference: 'demo' })
      })
      created = await response.json().catch(() => ({}))
      ok = response.ok
      setSlugTaken(response.status === 409)
    } catch {
      created = { detail: 'FreedomBot could not be reached. Try again in a minute.' }
    }
    if (!ok || !created.workspace) {
      const detail = created.detail
      setError(
        Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join(' ')
          : String(detail || 'The workspace could not be created. Try again.')
      )
      setBusy(false)
      return
    }

    saveOnboarding({
      paid: true,
      provisioned: true,
      workspaceSlug: created.workspace,
      completedAt: new Date().toISOString()
    })
    setBusy(false)
    router.push('/onboarding/complete')
  }

  if (!state) {
    return <div className="mx-auto max-w-xl text-sm text-fg-muted">Loading…</div>
  }

  return (
    <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <div>
        <p className="eyebrow">Step 4 of 4</p>
        <h1 className="mt-3 font-display text-3xl font-semibold tracking-[-0.03em] text-fg sm:text-4xl">
          Review & pay
        </h1>
        <p className="mt-3 text-fg-muted">
          Confirm your subscription, then complete payment to provision the
          workspace.
        </p>

        <form className="panel mt-8 space-y-4 p-6 sm:p-8" onSubmit={onSubmit}>
          {error && (
            <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
              {error}
              {slugTaken && (
                <Link href="/onboarding/company" className="link ml-1">
                  Change the workspace identifier
                </Link>
              )}
            </div>
          )}
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <Lock size={14} className="text-accent" />
            Secure checkout · demo card capture for local onboarding
          </div>
          <div>
            <label className="label" htmlFor="cardName">
              Name on card
            </label>
            <input
              id="cardName"
              className="field"
              value={cardName}
              onChange={(e) => setCardName(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="cardNumber">
              Card number
            </label>
            <div className="relative">
              <input
                id="cardNumber"
                className="field pr-11"
                inputMode="numeric"
                placeholder="4242 4242 4242 4242"
                value={cardNumber}
                onChange={(e) => setCardNumber(e.target.value)}
              />
              <CreditCard className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-muted" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="expiry">
                Expiry
              </label>
              <input
                id="expiry"
                className="field"
                placeholder="MM/YY"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="cvc">
                CVC
              </label>
              <input
                id="cvc"
                className="field"
                placeholder="123"
                value={cvc}
                onChange={(e) => setCvc(e.target.value)}
              />
            </div>
          </div>
          <button type="submit" className="btn-primary w-full" disabled={busy}>
            {busy
              ? 'Processing…'
              : `Pay ${plan.priceLabel}/mo and create workspace`}
          </button>
          <Link href="/onboarding/plan" className="btn-secondary w-full">
            Back to plans
          </Link>
        </form>
      </div>

      <aside className="panel h-fit p-6 sm:p-7">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
          Order summary
        </p>
        <h2 className="mt-2 text-xl font-semibold text-fg">{plan.name}</h2>
        <p className="mt-1 text-sm text-fg-muted">{plan.tagline}</p>
        <p className="mt-5 font-display text-3xl font-semibold text-fg">
          {plan.priceMonthly ? formatUsd(plan.priceMonthly) : plan.priceLabel}
          <span className="ml-1 text-sm font-sans font-normal text-fg-muted">
            / month
          </span>
        </p>

        <dl className="mt-6 space-y-3 border-t border-line pt-5 text-sm">
          <div className="flex justify-between gap-4">
            <dt className="text-fg-muted">Company</dt>
            <dd className="text-right text-fg">{state.companyName}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-fg-muted">Workspace</dt>
            <dd className="font-mono text-right text-fg">
              {state.workspaceSlug}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-fg-muted">Admin</dt>
            <dd className="text-right text-fg">{state.email}</dd>
          </div>
        </dl>

        <div className="mt-5 rounded-xl border border-line bg-surface-2 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
            Domain access in FreedomBot
          </p>
          <p className="mt-2 text-xs leading-5 text-fg-muted">
            {domains.map((d) => d.label).join(' · ')}
          </p>
          <p className="mt-2 text-xs text-fg-muted">
            Only these department domains will be enabled after sign-in.
          </p>
        </div>
      </aside>
    </div>
  )
}
