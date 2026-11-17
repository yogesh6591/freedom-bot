'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { DEMO_FILLER } from '@/lib/demo-filler'
import { loadOnboarding, saveOnboarding } from '@/lib/onboarding'
import {
  ALWAYS_ON_DOMAINS,
  DOMAINS,
  recommendPlan
} from '@/lib/plans'
import { cn } from '@/lib/utils'

export default function DomainStepPage() {
  const router = useRouter()
  const [selected, setSelected] = useState<Set<string>>(
    new Set(DEMO_FILLER.purchasedDomains)
  )
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const state = loadOnboarding()
    if (!state.email) {
      router.replace('/signup')
      return
    }
    if (!state.companyName || !state.workspaceSlug) {
      router.replace('/onboarding/company')
      return
    }
    const source =
      state.purchasedDomains?.length > 0
        ? state.purchasedDomains
        : DEMO_FILLER.purchasedDomains
    const next = new Set(source)
    for (const id of ALWAYS_ON_DOMAINS) next.add(id)
    setSelected(next)
  }, [router])

  function toggle(id: string) {
    if ((ALWAYS_ON_DOMAINS as readonly string[]).includes(id)) return
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      for (const always of ALWAYS_ON_DOMAINS) next.add(always)
      return next
    })
  }

  function continueNext() {
    const domains = Array.from(selected)
    if (domains.length === 0) {
      setError('Select at least one domain to continue.')
      return
    }
    saveOnboarding({
      purchasedDomains: domains,
      planId: recommendPlan(domains)
    })
    router.push('/onboarding/plan')
  }

  return (
    <div className="mx-auto max-w-3xl">
      <p className="eyebrow">Step 2 of 4</p>
      <h1 className="mt-3 font-display text-3xl font-semibold tracking-[-0.03em] text-fg sm:text-4xl">
        Choose domain access
      </h1>
      <p className="mt-3 max-w-2xl text-fg-muted">
        Pick the department domains this workspace can use. After onboarding,
        FreedomBot will only unlock the packs you select here.
      </p>

      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        {DOMAINS.map((domain) => {
          const alwaysOn = (ALWAYS_ON_DOMAINS as readonly string[]).includes(
            domain.id
          )
          const active = selected.has(domain.id)
          return (
            <button
              key={domain.id}
              type="button"
              onClick={() => toggle(domain.id)}
              disabled={alwaysOn}
              className={cn(
                'panel flex items-start gap-3 p-5 text-left transition',
                active
                  ? 'border-brand/45 bg-brand/10 shadow-glow'
                  : 'hover:border-[color:var(--line-strong)]',
                alwaysOn && 'cursor-default opacity-95'
              )}
            >
              <span
                className={cn(
                  'mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md border',
                  active
                    ? 'border-brand bg-brand text-white'
                    : 'border-line bg-surface-2'
                )}
              >
                {active && <Check size={12} strokeWidth={3} />}
              </span>
              <span>
                <span className="flex items-center gap-2">
                  <span className="font-semibold text-fg">{domain.label}</span>
                  {alwaysOn && (
                    <span className="rounded-full border border-line px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
                      Included
                    </span>
                  )}
                </span>
                <span className="mt-1 block text-sm leading-6 text-fg-muted">
                  {domain.description}
                </span>
              </span>
            </button>
          )
        })}
      </div>

      {error && (
        <div className="mt-5 rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </div>
      )}

      <div className="mt-8 flex flex-wrap items-center gap-3">
        <button type="button" className="btn-primary" onClick={continueNext}>
          Continue to plan
        </button>
        <Link href="/onboarding/company" className="btn-secondary">
          Back
        </Link>
        <p className="text-sm text-fg-muted">
          {selected.size} domain{selected.size === 1 ? '' : 's'} selected
        </p>
      </div>
    </div>
  )
}
