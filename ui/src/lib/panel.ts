'use client'

/**
 * Two front doors, kept apart by URL:
 *
 * - Home:  `/` signs in, `/chat` is the assistant on its own.
 * - Admin: `/admin` signs in, `/admin/chat` and every other page is the full panel.
 *
 * Signing in at `/admin` unlocks the admin panel for that user in this browser; signing in at home or
 * signing out locks it again. This only decides which screen shows: the API still checks every request
 * by role, and both doors use the same account and data.
 */

const KEY = 'freedombot:admin-panel'

/** Home pages: the chat-only screen. Everything else (except the sign-in pages) is the admin panel. */
export function isHomePath(path: string): boolean {
  return path === '/chat' || path.startsWith('/chat/')
}

export function isSignInPath(path: string): boolean {
  return path === '/' || path === '/admin' || path === '/login'
}

/** Pages anyone may open signed out: self-serve signup and onboarding. */
export function isPublicPath(path: string): boolean {
  return path === '/signup' || path.startsWith('/signup/')
}

/** Where a signed-out visitor to ``path`` signs in. */
export function signInPathFor(path: string): string {
  return isHomePath(path) || path === '/' ? '/' : '/admin'
}

export function adminUnlocked(userId: string): boolean {
  try {
    return localStorage.getItem(KEY) === userId
  } catch {
    return false
  }
}

export function setAdminUnlocked(userId: string | null): void {
  try {
    if (userId) localStorage.setItem(KEY, userId)
    else localStorage.removeItem(KEY)
  } catch {
    /* storage blocked: the admin panel stays locked */
  }
}
