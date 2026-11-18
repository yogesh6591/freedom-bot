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

/** For the Departments step's "What each department does" dialog: the team's usual work, and what
 * FreedomBot does there (from the server's domain packs in freedombot/domains). */
export interface DepartmentGuide {
  team: string
  helps: string[]
  example: string
}

export const DEPARTMENT_GUIDE: Record<string, DepartmentGuide> = {
  general: {
    team: 'Everyone in the company: everyday questions about how things are done.',
    helps: ['Answers from your company memory: documents, facts, SOPs and decisions', 'Searches the web and your connected apps', 'Sends anything that changes data for approval first'],
    example: 'What is our refund policy?'
  },
  operations: {
    team: 'Runs the day-to-day: internal tasks, recurring processes and reporting on how work is going.',
    helps: ['Follows your recorded SOPs step by step', 'Creates internal tasks and notes', 'Says so when no SOP covers a request, instead of guessing'],
    example: 'Walk me through our month-end payroll steps.'
  },
  sales: {
    team: 'Finds and closes deals: pipeline, customer contacts, follow-ups and sales numbers.',
    helps: ['Pipeline and deal summaries from your CRM', 'Contact history before a call', 'Drafts outreach emails; sending waits for approval'],
    example: 'Give me a pipeline summary by stage.'
  },
  intake: {
    team: 'Handles everything coming in: new leads, enquiries and requests, and where each one should go.',
    helps: ['Captures and enriches new leads', 'Checks for an existing record before adding one', 'Classifies and routes using your own criteria, and asks when unsure'],
    example: 'Sort this week\'s new enquiries by priority.'
  },
  planning: {
    team: 'Keeps time organised: calendars, meetings and the week ahead.',
    helps: ['Checks availability before suggesting a time', 'Meeting briefs: who is coming, recent history, what is still open', 'Weekly plans'],
    example: 'What meetings do I have in the next 7 days?'
  },
  strategy: {
    team: 'Sets direction: company priorities, big choices and the reasoning behind them.',
    helps: ['Lists your recorded priorities', 'Writes options memos with trade-offs', 'Advises only: people make the decision'],
    example: 'What are our priorities this quarter?'
  },
  finance: {
    team: 'Manages money: invoices, billing, payments and finance policy.',
    helps: ['Looks up finance policy', 'Lists invoices and work not yet billed', 'Drafts invoices and adjustments, always sent for approval'],
    example: 'Which invoices are still unpaid?'
  },
  brand: {
    team: 'Protects how the company sounds and looks: voice, tone and messaging.',
    helps: ['Keeps your brand voice guidelines in one place', 'Drafts messaging in that voice', 'Drafts only: nothing is published'],
    example: 'Draft a launch announcement in our brand voice.'
  },
  legal: {
    team: 'Looks after contracts, clauses and anything with legal risk.',
    helps: ['Finds clauses in your recorded documents', 'Flags questions for your counsel (with approval)', 'Never gives legal advice or judgment'],
    example: 'Find the termination clause in our standard contract.'
  }
}

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
