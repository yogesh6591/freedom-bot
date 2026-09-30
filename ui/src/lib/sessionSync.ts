/**
 * Cross-tab session sync (M01-06).
 *
 * All tabs of one browser share the HttpOnly session cookie. When one tab signs
 * in or out, the others must stop showing the previous workspace. Tabs tell
 * each other over a BroadcastChannel (with a localStorage fallback), and a tab
 * that finds itself out of date reloads into the current session with a notice.
 */

const CHANNEL = 'bizos-session'
const STORAGE_KEY = 'bizos:session-changed'
const NOTICE_KEY = 'bizos:notice'

/** Identifies this tab so it ignores its own announcements. */
const TAB_ID =
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`

interface Announcement {
  tab: string
  at: number
}

/** Tell other tabs the signed-in session changed (login or logout). */
export function announceSessionChange(): void {
  const message: Announcement = { tab: TAB_ID, at: Date.now() }
  try {
    const channel = new BroadcastChannel(CHANNEL)
    channel.postMessage(message)
    channel.close()
  } catch {
    // BroadcastChannel unavailable; the storage event below still works.
  }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(message))
  } catch {
    // Storage blocked; the server-side 409 check still protects the tab.
  }
}

/** Run ``callback`` when another tab announces a session change. */
export function onSessionChange(callback: () => void): () => void {
  const handle = (data: unknown) => {
    const message = data as Announcement | null
    if (!message || message.tab === TAB_ID) return
    callback()
  }
  let channel: BroadcastChannel | null = null
  try {
    channel = new BroadcastChannel(CHANNEL)
    channel.onmessage = (event) => handle(event.data)
  } catch {
    channel = null
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY || !event.newValue) return
    try {
      handle(JSON.parse(event.newValue))
    } catch {
      // ignore malformed values
    }
  }
  window.addEventListener('storage', onStorage)
  return () => {
    channel?.close()
    window.removeEventListener('storage', onStorage)
  }
}

/** Leave a one-time notice for the next page load of this tab. */
export function setNotice(text: string): void {
  try {
    sessionStorage.setItem(NOTICE_KEY, text)
  } catch {
    // no storage: the reload still happens, just without the explanation
  }
}

/** Read and clear the pending notice, if any. */
export function takeNotice(): string | null {
  try {
    const text = sessionStorage.getItem(NOTICE_KEY)
    if (text) sessionStorage.removeItem(NOTICE_KEY)
    return text
  } catch {
    return null
  }
}
