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

export type StreamHandlers = {
  onEvent: (event: string, data: unknown) => void
}

/**
 * POST that consumes a Server-Sent Events body (chat streaming).
 * Calls ``onEvent`` for each ``event:`` / ``data:`` pair as chunks arrive.
 */
async function streamRequest(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  opts: RequestOptions = {}
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(expected && !opts.anyWorkspace
        ? { 'X-Bizos-Client': expected.clientId, 'X-Bizos-User': expected.userId }
        : {})
    },
    body: JSON.stringify(body)
  })

  if (!response.ok) {
    const text = await response.text()
    const data = text ? safeJson(text) : null
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

  if (!response.body) {
    throw new ApiError(502, 'No response body from stream')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE events are separated by a blank line.
    let splitAt: number
    while ((splitAt = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, splitAt)
      buffer = buffer.slice(splitAt + 2)
      let eventName = 'message'
      const dataLines: string[] = []
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }
      }
      if (dataLines.length === 0) continue
      const payload = dataLines.join('\n')
      let parsed: unknown = payload
      try {
        parsed = JSON.parse(payload)
      } catch {
        // keep raw string
      }
      handlers.onEvent(eventName, parsed)
    }
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
  stream: (path: string, body: unknown, handlers: StreamHandlers, opts?: RequestOptions) =>
    streamRequest(path, body, handlers, opts),
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
