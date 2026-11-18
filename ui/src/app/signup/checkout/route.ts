import { platform, signupOff } from '@/lib/signupServer'

export const dynamic = 'force-dynamic'

/** Start paying: a Stripe embedded Checkout Session for the chosen plan, or { mode: 'demo' } without Stripe. */
export async function POST(request: Request) {
  const off = signupOff()
  if (off) return off
  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return Response.json({ detail: 'Send the signup details as JSON.' }, { status: 400 })
  }
  const payload = {
    company_name: body.companyName,
    slug: body.workspaceId || null,
    purchased_domains: body.departments,
    plan_id: body.planId,
    admin_email: body.email
  }
  return platform('/api/public/signup/checkout', { method: 'POST', body: JSON.stringify(payload) }, request)
}
