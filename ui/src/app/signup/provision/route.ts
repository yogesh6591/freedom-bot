import { platform, signupOff } from '@/lib/signupServer'

export const dynamic = 'force-dynamic'

/** After payment: create the company's workspace, its departments and the first admin. */
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
    admin_name: body.fullName,
    admin_email: body.email,
    admin_password: body.password,
    payment_reference: body.paymentReference || ''
  }
  return platform('/api/public/signup', { method: 'POST', body: JSON.stringify(payload) }, request)
}
