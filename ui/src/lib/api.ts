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

function headersFor(options: RequestInit, opts: RequestOptions): HeadersInit {
  return {
    ...(options.body && !(options.body instanceof FormData)
      ? { 'Content-Type': 'application/json' }
      : {}),
    ...(expected && !opts.anyWorkspace
      ? { 'X-Bizos-Client': expected.clientId, 'X-Bizos-User': expected.userId }
      : {}),
    ...(options.headers ?? {})
  }
}

/** The ApiError for a failed response (and the workspace-changed event for that 409). */
function failure(status: number, data: unknown): ApiError {
  const detail = readErrorDetail(data, status)
  if (status === 409 && detail === WORKSPACE_CHANGED && typeof window !== 'undefined') {
    window.dispatchEvent(new Event(WORKSPACE_CHANGED_EVENT))
    return new ApiError(
      409,
      'This tab is out of date — you signed in to another workspace in a different tab.'
    )
  }
  return new ApiError(status, detail)
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  opts: RequestOptions = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: headersFor(options, opts)
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const data = text ? safeJson(text) : null

  if (!response.ok) throw failure(response.status, data)
  return data as T
}

/** One event of a streamed reply (POST /api/chat/stream). */
export interface StreamEvent {
  type: 'status' | 'token' | 'reset' | 'done' | 'error'
  text?: string
  response?: unknown
  status?: number
  detail?: unknown
}

/**
 * POST and read Server-Sent Events as they arrive, calling `onEvent` for each.
 * An `error` event becomes a thrown ApiError, like a failed request.
 */
async function stream(path: string, body: unknown, onEvent: (event: StreamEvent) => void): Promise<void> {
  const init: RequestInit = { method: 'POST', body: JSON.stringify(body) }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers: { ...headersFor(init, {}), Accept: 'text/event-stream' }
  })
  if (!response.ok || !response.body) {
    const text = await response.text()
    throw failure(response.status, text ? safeJson(text) : null)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const chunk = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      for (const line of chunk.split('\n')) {
        if (!line.startsWith('data:')) continue
        const event = safeJson(line.slice(5).trim()) as StreamEvent
        if (event.type === 'error') throw failure(event.status ?? 500, { detail: event.detail })
        onEvent(event)
      }
    }
  }
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { detail: text }
  }
}

/** FastAPI `detail` may be a string, a list of `{ msg }` items, or another object. */
function readErrorDetail(data: unknown, status: number): string {
  const fallback = `Request failed (${status})`
  if (data == null) return fallback
  if (typeof data !== 'object') return String(data).trim() || fallback
  if (!('detail' in data)) return fallback
  return flattenDetail((data as { detail: unknown }).detail) || fallback
}

function flattenDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail.trim()
  if (typeof detail === 'number' || typeof detail === 'boolean') return String(detail)
  if (Array.isArray(detail)) {
    return detail.map(flattenDetail).filter(Boolean).join('. ')
  }
  if (detail && typeof detail === 'object') {
    const rec = detail as Record<string, unknown>
    if (typeof rec.msg === 'string' && rec.msg.trim()) return rec.msg.trim()
    if (typeof rec.message === 'string' && rec.message.trim()) return rec.message.trim()
    if (typeof rec.detail === 'string' && rec.detail.trim()) return rec.detail.trim()
  }
  return ''
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
    request<T>(path, { method: 'POST', body: form }),
  /** A file from the API, with the same session as every other request. */
  blob: async (path: string): Promise<Blob> => {
    const response = await fetch(`${API_BASE}${path}`, {
      credentials: 'include',
      headers: headersFor({}, {})
    })
    if (!response.ok) {
      const text = await response.text()
      throw failure(response.status, text ? safeJson(text) : null)
    }
    return response.blob()
  },
  stream
}
