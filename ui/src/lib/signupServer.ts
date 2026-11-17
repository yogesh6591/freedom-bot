/**
 * Server side of /signup: forwards to FreedomBot's self-serve signup API with the shared key. Only
 * imported by route handlers, so the key (no NEXT_PUBLIC_ prefix) never reaches a browser.
 */

function apiBase(): string {
  return (
    process.env.FREEDOMBOT_API_URL ||
    process.env.ACQUISITION_PROXY_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    'http://127.0.0.1:8001'
  ).replace(/\/+$/, '')
}

export function signupOff(): Response | null {
  if (!process.env.SELF_SERVE_SIGNUP_KEY) {
    return Response.json(
      { detail: 'Signup is not switched on yet. Please contact FreedomBot to get started.' },
      { status: 503 }
    )
  }
  return null
}

/** Call the platform with the key and the customer's address (for its rate limit). */
export async function platform(path: string, init: RequestInit, request: Request): Promise<Response> {
  const customerIp = (request.headers.get('x-forwarded-for') || '').split(',')[0].trim()
  try {
    const response = await fetch(`${apiBase()}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        'X-Signup-Key': process.env.SELF_SERVE_SIGNUP_KEY || '',
        ...(customerIp ? { 'X-Customer-IP': customerIp } : {})
      },
      cache: 'no-store'
    })
    return new Response(await response.text(), {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('content-type') || 'application/json' }
    })
  } catch {
    return Response.json({ detail: 'FreedomBot could not be reached. Try again in a minute.' }, { status: 502 })
  }
}
