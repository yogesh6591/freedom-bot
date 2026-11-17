'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { PricingCards } from '@/components/PricingCards'
import { loadOnboarding, saveOnboarding } from '@/lib/onboarding'
import {
  DOMAINS,
  getPlan,
  planCoversDomains,
  recommendPlan,
  type PlanId
} from '@/lib/plans'

export default function PlanStepPage() {
  const router = useRouter()
  const [planId, setPlanId] = useState<PlanId>('growth')
  const [domains, setDomains] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const state = loadOnboarding()
    if (!state.email) {
      router.replace('/signup')
      return
    }
    if (!state.workspaceSlug) {
      router.replace('/onboarding/company')
      return
    }
    if (!state.purchasedDomains?.length) {
      router.replace('/onboarding/domain')
      return
    }
    setDomains(state.purchasedDomains)
    setPlanId(state.planId || recommendPlan(state.purchasedDomains))
  }, [router])

  const recommended = useMemo(() => recommendPlan(domains), [domains])
  const selectedLabels = DOMAINS.filter((d) => domains.includes(d.id)).map(
    (d) => d.label
  )

  function onSelect(id: PlanId) {
    setError(null)
    setPlanId(id)
  }

  function continueNext() {
    if (!planCoversDomains(planId, domains)) {
      setError(
        `The ${getPlan(planId).name} plan does not include all domains you selected (${selectedLabels.join(', ')}). Choose ${getPlan(recommended).name}, or go back and adjust domain access.`
      )
      return
    }
    saveOnboarding({ planId, purchasedDomains: domains })
    router.push('/onboarding/payment')
  }

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mx-auto max-w-2xl text-center">
        <p className="eyebrow">Step 3 of 4</p>
        <h1 className="mt-3 font-display text-3xl font-semibold tracking-[-0.03em] text-fg sm:text-4xl">
          Choose your subscription
        </h1>
        <p className="mt-3 text-fg-muted">
          Based on your domain access, we recommend{' '}
          <strong className="text-fg">{getPlan(recommended).name}</strong>. Your
          FreedomBot workspace will only enable:{' '}
          <strong className="text-fg">{selectedLabels.join(', ')}</strong>.
        </p>
      </div>

      <div className="mt-10">
        <PricingCards selected={planId} onSelect={onSelect} />
      </div>

      {error && (
        <div className="mx-auto mt-6 max-w-2xl rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <div className="mt-8 flex flex-wrap justify-center gap-3">
        <button type="button" className="btn-primary" onClick={continueNext}>
          Continue to payment
        </button>
        <Link href="/onboarding/domain" className="btn-secondary">
          Back to domains
        </Link>
      </div>
    </div>
  )
}
