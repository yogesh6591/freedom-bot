/**
 * Calls to the FreedomBot platform from the website's server. The signup key stays here: it is never
 * sent to the browser (no NEXT_PUBLIC_ prefix).
 */

export function apiBase(): string {
  return (process.env.FREEDOMBOT_API_URL || process.env.NEXT_PUBLIC_API_URL || '').replace(/\/+$/, '')
}

export function signupKey(): string {
  return process.env.SELF_SERVE_SIGNUP_KEY || ''
}

export function notConfigured(): Response | null {
  if (!apiBase() || !signupKey()) {
    return Response.json(
      { detail: 'Signup is not connected to FreedomBot yet. Set FREEDOMBOT_API_URL and SELF_SERVE_SIGNUP_KEY.' },
      { status: 503 }
    )
  }
  return null
}

/** Forward to the platform with the key and the customer's address (for its rate limit). */
export async function platform(path: string, init: RequestInit, request: Request): Promise<Response> {
  const customerIp = (request.headers.get('x-forwarded-for') || '').split(',')[0].trim()
  const response = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-Signup-Key': signupKey(),
      ...(customerIp ? { 'X-Customer-IP': customerIp } : {}),
      ...(init.headers || {})
    },
    cache: 'no-store'
  })
  const body = await response.text()
  return new Response(body, {
    status: response.status,
    headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' }
  })
}
