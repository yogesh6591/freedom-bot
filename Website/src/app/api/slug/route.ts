import { notConfigured, platform } from '@/lib/server/freedombot'

export const dynamic = 'force-dynamic'

/** Whether a workspace identifier is still free on FreedomBot. */
export async function GET(request: Request) {
  const missing = notConfigured()
  if (missing) return missing
  const slug = new URL(request.url).searchParams.get('slug') || ''
  try {
    return await platform(`/api/public/signup/slug?slug=${encodeURIComponent(slug)}`, { method: 'GET' }, request)
  } catch {
    return Response.json({ detail: 'FreedomBot could not be reached.' }, { status: 502 })
  }
}
