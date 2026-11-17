/** Self-serve signup at /signup: the departments a company can buy and the plans that cover them. */

export type PlanId = 'starter' | 'growth' | 'enterprise'

export interface Department {
  id: string
  label: string
  description: string
}

/** The domain packs, by the same ids the server uses. General is always included. */
export const DEPARTMENTS: Department[] = [
  { id: 'general', label: 'General', description: 'Company memory and everyday questions.' },
  { id: 'operations', label: 'Operations', description: 'Internal tasks, SOPs, operational reporting.' },
  { id: 'sales', label: 'Sales', description: 'Pipeline, deals, contacts, outreach, reporting.' },
  { id: 'intake', label: 'Intake', description: 'Lead capture, enrichment, classification, routing.' },
  { id: 'planning', label: 'Planning', description: 'Calendars, meeting prep, scheduling.' },
  { id: 'strategy', label: 'Strategy', description: 'Priorities, options memos, strategic analysis.' },
  { id: 'finance', label: 'Finance', description: 'Policy lookup and adjustment proposals.' },
  { id: 'brand', label: 'Brand', description: 'Voice guidelines and draft messaging.' },
  { id: 'legal', label: 'Legal', description: 'Clause lookup and counsel escalation.' }
]

export interface Plan {
  id: PlanId
  name: string
  tagline: string
  priceMonthly: number
  departments: string[]
  features: string[]
}

export const PLANS: Plan[] = [
  {
    id: 'starter',
    name: 'Starter',
    tagline: 'For lean teams that need a governed assistant.',
    priceMonthly: 249,
    departments: ['general', 'operations'],
    features: ['Company memory', 'Approvals before actions', 'Up to 10 seats', 'Email and calendar']
  },
  {
    id: 'growth',
    name: 'Growth',
    tagline: 'For teams running sales, intake and planning.',
    priceMonthly: 749,
    departments: ['general', 'operations', 'sales', 'intake', 'planning'],
    features: ['Everything in Starter', 'Sales and outreach', 'Lead intake and routing', 'Up to 50 seats']
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    tagline: 'Every department, including finance, brand and legal.',
    priceMonthly: 1999,
    departments: DEPARTMENTS.map((d) => d.id),
    features: ['Everything in Growth', 'Strategy, Finance, Brand, Legal', 'Unlimited seats', 'SLA and security review']
  }
]

export function getPlan(id: PlanId): Plan {
  return PLANS.find((p) => p.id === id) ?? PLANS[1]
}

/** The smallest plan that covers the chosen departments. */
export function recommendPlan(departments: string[]): PlanId {
  return PLANS.find((p) => departments.every((d) => p.departments.includes(d)))?.id ?? 'enterprise'
}

export function planCovers(planId: PlanId, departments: string[]): boolean {
  const allowed = new Set(getPlan(planId).departments)
  return departments.every((d) => allowed.has(d))
}

export function usd(amount: number): string {
  return amount.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

/** The workspace ID the server will make from a business name (mirrors freedombot.util.ids.slugify). */
export function workspaceIdFrom(value: string): string {
  let slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
    .replace(/_+$/g, '')
  if (!slug) slug = 'client'
  if (/^[0-9]/.test(slug)) slug = `c_${slug}`
  return slug
}
