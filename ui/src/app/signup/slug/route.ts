import { platform, signupOff } from '@/lib/signupServer'

export const dynamic = 'force-dynamic'

/** Whether a workspace ID is still free. */
export async function GET(request: Request) {
  const off = signupOff()
  if (off) return off
  const slug = new URL(request.url).searchParams.get('slug') || ''
  return platform(`/api/public/signup/slug?slug=${encodeURIComponent(slug)}`, { method: 'GET' }, request)
}
