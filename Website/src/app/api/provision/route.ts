import { notConfigured, platform } from '@/lib/server/freedombot'

export const dynamic = 'force-dynamic'

/** After payment: create the company's FreedomBot workspace, departments and first admin. */
export async function POST(request: Request) {
  const missing = notConfigured()
  if (missing) return missing
  let body: Record<string, unknown>
  try {
    body = await request.json()
  } catch {
    return Response.json({ detail: 'Send the signup details as JSON.' }, { status: 400 })
  }
  const payload = {
    company_name: body.companyName,
    slug: body.workspaceSlug || null,
    purchased_domains: body.purchasedDomains,
    plan_id: body.planId,
    admin_name: body.fullName,
    admin_email: body.email,
    admin_password: body.password,
    payment_reference: body.paymentReference || ''
  }
  try {
    return await platform('/api/public/signup', { method: 'POST', body: JSON.stringify(payload) }, request)
  } catch {
    return Response.json({ detail: 'FreedomBot could not be reached. Try again in a minute.' }, { status: 502 })
  }
}
