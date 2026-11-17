'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { FormEvent, useEffect, useState } from 'react'
import { DEMO_FILLER } from '@/lib/demo-filler'
import { loadOnboarding, saveOnboarding } from '@/lib/onboarding'
import { previewSlug } from '@/lib/utils'

export default function CompanyStepPage() {
  const router = useRouter()
  const [companyName, setCompanyName] = useState(DEMO_FILLER.companyName)
  const [slug, setSlug] = useState(DEMO_FILLER.workspaceSlug)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const state = loadOnboarding()
    if (!state.email) {
      router.replace('/signup')
      return
    }
    setCompanyName(state.companyName || DEMO_FILLER.companyName)
    setSlug(
      state.workspaceSlug ||
        previewSlug(state.companyName || DEMO_FILLER.companyName)
    )
  }, [router])

  const normalized = previewSlug(slug || companyName)

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!companyName.trim()) {
      setError('Enter your company name.')
      return
    }
    if (!normalized || normalized === 'client') {
      setError('Choose a more specific workspace identifier.')
      return
    }
    // Ask FreedomBot whether the identifier is free, so it is not found taken after payment.
    try {
      const response = await fetch(`/api/slug?slug=${encodeURIComponent(normalized)}`)
      if (response.ok) {
        const found = (await response.json()) as { available?: boolean }
        if (found.available === false) {
          setError(`The workspace identifier "${normalized}" is already taken. Choose another.`)
          return
        }
      }
    } catch {
      // Not reachable right now: payment checks again before anything is created.
    }
    saveOnboarding({
      companyName: companyName.trim(),
      workspaceSlug: normalized
    })
    router.push('/onboarding/domain')
  }

  return (
    <div className="mx-auto max-w-xl">
      <p className="eyebrow">Step 1 of 4</p>
      <h1 className="mt-3 font-display text-3xl font-semibold tracking-[-0.03em] text-fg sm:text-4xl">
        Tell us about the company
      </h1>
      <p className="mt-3 text-fg-muted">
        Name the workspace, then you&apos;ll choose which department domains the
        team can access in FreedomBot.
      </p>

      <form className="panel mt-8 space-y-5 p-6 sm:p-8" onSubmit={onSubmit}>
        {error && (
          <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </div>
        )}
        <div>
          <label className="label" htmlFor="company">
            Company name
          </label>
          <input
            id="company"
            className="field"
            value={companyName}
            onChange={(e) => {
              const value = e.target.value
              setCompanyName(value)
              if (!slug || slug === previewSlug(companyName)) {
                setSlug(previewSlug(value))
              }
            }}
            placeholder="Globex Ltd"
          />
        </div>
        <div>
          <label className="label" htmlFor="slug">
            Workspace identifier
          </label>
          <input
            id="slug"
            className="field font-mono"
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="globex"
          />
          <p className="mt-2 text-xs text-fg-muted">
            Used for sign-in. Will be:{' '}
            <span className="font-mono text-fg-muted">{normalized}</span>. Can&apos;t
            be changed later.
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <button type="submit" className="btn-primary">
            Continue to domain access
          </button>
          <Link href="/signup" className="btn-secondary">
            Back
          </Link>
        </div>
      </form>
    </div>
  )
}
