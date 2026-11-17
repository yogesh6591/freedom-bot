'use client'

import { DEMO_FILLER } from './demo-filler'
import type { PlanId } from './plans'

export interface OnboardingState {
  fullName: string
  email: string
  password: string
  companyName: string
  workspaceSlug: string
  /** Capability packs the workspace may use in FreedomBot. */
  purchasedDomains: string[]
  planId: PlanId
  paid: boolean
  completedAt?: string
  /** True once FreedomBot made the workspace and the admin account. */
  provisioned?: boolean
}

const STORAGE_KEY = 'freedombot.website.onboarding'

export const emptyOnboarding = (): OnboardingState => ({
  fullName: DEMO_FILLER.fullName,
  email: DEMO_FILLER.email,
  password: DEMO_FILLER.password,
  companyName: DEMO_FILLER.companyName,
  workspaceSlug: DEMO_FILLER.workspaceSlug,
  purchasedDomains: [...DEMO_FILLER.purchasedDomains],
  planId: DEMO_FILLER.planId,
  paid: false
})

export function loadOnboarding(): OnboardingState {
  if (typeof window === 'undefined') return emptyOnboarding()
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return emptyOnboarding()
    return { ...emptyOnboarding(), ...JSON.parse(raw) }
  } catch {
    return emptyOnboarding()
  }
}

export function saveOnboarding(patch: Partial<OnboardingState>): OnboardingState {
  const next = { ...loadOnboarding(), ...patch }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  return next
}

export function clearOnboarding() {
  window.localStorage.removeItem(STORAGE_KEY)
}

export { appSignInUrl } from './urls'
