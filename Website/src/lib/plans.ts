export type PlanId = 'starter' | 'growth' | 'enterprise'

export interface CapabilityDomain {
  id: string
  label: string
  description: string
}

export const DOMAINS: CapabilityDomain[] = [
  {
    id: 'general',
    label: 'General',
    description: 'Organizational memory and everyday questions.'
  },
  {
    id: 'operations',
    label: 'Operations',
    description: 'Internal tasks, SOP adherence, operational reporting.'
  },
  {
    id: 'sales',
    label: 'Sales',
    description: 'Pipeline, deals, contacts, outreach, reporting.'
  },
  {
    id: 'intake',
    label: 'Intake',
    description: 'Lead capture, enrichment, classification, routing.'
  },
  {
    id: 'planning',
    label: 'Planning',
    description: 'Calendars, meeting prep, scheduling.'
  },
  {
    id: 'strategy',
    label: 'Strategy',
    description: 'Priorities, options memos, strategic analysis.'
  },
  {
    id: 'finance',
    label: 'Finance',
    description: 'Policy lookup and adjustment proposals.'
  },
  {
    id: 'brand',
    label: 'Brand',
    description: 'Voice guidelines and draft messaging.'
  },
  {
    id: 'legal',
    label: 'Legal',
    description: 'Clause lookup and counsel escalation.'
  }
]

export interface Plan {
  id: PlanId
  name: string
  tagline: string
  priceMonthly: number | null
  priceLabel: string
  cta: string
  highlighted?: boolean
  domains: string[]
  features: string[]
}

export const PLANS: Plan[] = [
  {
    id: 'starter',
    name: 'Starter',
    tagline: 'For lean teams that need a governed assistant.',
    priceMonthly: 249,
    priceLabel: '$249',
    cta: 'Start with Starter',
    domains: ['general', 'operations'],
    features: [
      'Company memory with version history',
      'Approval checkpoints before actions',
      '1 workspace · up to 10 seats',
      'Email + calendar integrations',
      'Audit log (90 days)'
    ]
  },
  {
    id: 'growth',
    name: 'Growth',
    tagline: 'For operators running sales, intake, and planning.',
    priceMonthly: 749,
    priceLabel: '$749',
    cta: 'Choose Growth',
    highlighted: true,
    domains: ['general', 'operations', 'sales', 'intake', 'planning'],
    features: [
      'Everything in Starter',
      'Sales pipeline & outreach assist',
      'Lead intake, enrichment & routing',
      'Meeting prep & scheduling',
      'Up to 50 seats · priority support',
      'Audit log (1 year)'
    ]
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    tagline: 'Full capability pack with finance, brand, and legal.',
    priceMonthly: 1999,
    priceLabel: '$1,999',
    cta: 'Go Enterprise',
    domains: DOMAINS.map((d) => d.id),
    features: [
      'Everything in Growth',
      'Strategy, Finance, Brand & Legal packs',
      'Custom policy packs & SSO',
      'Dedicated success engineer',
      'Unlimited seats · custom retention',
      'SLA & security review'
    ]
  }
]

export const ALWAYS_ON_DOMAINS = ['general'] as const

export const DEFAULT_DOMAINS = ['general', 'operations'] as const

const ENTERPRISE_ONLY = new Set(['strategy', 'finance', 'brand', 'legal'])
const GROWTH_PACKS = new Set(['sales', 'intake', 'planning'])

export function getPlan(id: PlanId | string | null | undefined): Plan {
  return PLANS.find((p) => p.id === id) ?? PLANS[1]
}

/** Smallest subscription that covers the selected capability packs. */
export function recommendPlan(domains: string[]): PlanId {
  if (domains.some((d) => ENTERPRISE_ONLY.has(d))) return 'enterprise'
  if (domains.some((d) => GROWTH_PACKS.has(d))) return 'growth'
  return 'starter'
}

export function planCoversDomains(planId: PlanId, domains: string[]): boolean {
  const allowed = new Set(getPlan(planId).domains)
  return domains.every((d) => allowed.has(d))
}

export function formatUsd(amount: number): string {
  return amount.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0
  })
}
