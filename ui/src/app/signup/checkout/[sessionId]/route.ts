import { platform, signupOff } from '@/lib/signupServer'

export const dynamic = 'force-dynamic'

/** Whether the Stripe payment for this signup has gone through (the signup tab asks until it has). */
export async function GET(request: Request, { params }: { params: Promise<{ sessionId: string }> }) {
  const off = signupOff()
  if (off) return off
  const { sessionId } = await params
  return platform(`/api/public/signup/checkout/${encodeURIComponent(sessionId)}`, { method: 'GET' }, request)
}
