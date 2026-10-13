/**
 * API client.
 *
 * Two rules this file exists to enforce:
 *
 * 1. `credentials: 'include'` on every call, so the HttpOnly session cookie is
 *    sent and the browser never has to hold a token in JavaScript. An XSS on
 *    this page therefore cannot read the session.
 * 2. No API key, secret or client identifier is ever embedded here (§28). The
 *    server derives the workspace and the roles from the signed cookie; the
 *    frontend cannot ask for a different one.
 *
 * Frontend permission checks are cosmetic — they hide buttons the user cannot
 * use. Every one is independently enforced server-side.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '') ?? 'http://localhost:8000'

/**
 * The client and user this tab is displaying (M01-06). Sent on every request so
 * the server can refuse to answer for a different workspace when another tab
 * has signed in elsewhere with the shared session cookie.
 */
let expected: { clientId: string; userId: string } | null = null

export function setExpectedSession(value: { clientId: string; userId: string } | null) {
  expected = value
}

/** Server detail for a 409 when the cookie no longer matches this tab. */
export const WORKSPACE_CHANGED = 'workspace_changed'
/** Window event fired when a request hits that 409. */
export const WORKSPACE_CHANGED_EVENT = 'bizos:workspace-changed'

export interface RequestOptions {
  /** Skip the expected-workspace check (session lookup, logout). */
  anyWorkspace?: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  opts: RequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...(expected && !opts.anyWorkspace
        ? { 'X-Bizos-Client': expected.clientId, 'X-Bizos-User': expected.userId }
        : {}),
      ...(options.headers ?? {})
    }
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const data = text ? safeJson(text) : null

  if (!response.ok) {
    const detail =
      (data && typeof data === 'object' && 'detail' in data
        ? String((data as { detail: unknown }).detail)
        : null) ?? `Request failed (${response.status})`
    if (response.status === 409 && detail === WORKSPACE_CHANGED && typeof window !== 'undefined') {
      window.dispatchEvent(new Event(WORKSPACE_CHANGED_EVENT))
      throw new ApiError(
        409,
        'This tab is out of date — you signed in to another workspace in a different tab.'
      )
    }
    throw new ApiError(response.status, detail)
  }
  return data as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { detail: text }
  }
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>(path, {}, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    request<T>(
      path,
      {
        method: 'POST',
        body: body === undefined ? undefined : JSON.stringify(body)
      },
      opts
    ),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body)
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      body: body === undefined ? undefined : JSON.stringify(body)
    }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form })
}
