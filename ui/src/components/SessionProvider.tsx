'use client'

/**
 * Session context.
 *
 * Holds who the signed-in user is and what their workspace allows, fetched from
 * `/api/auth/me`. Used to hide controls a role cannot use — a convenience, never
 * a control: the same checks run server-side on every request.
 *
 * M01-06: every tab of a browser shares the session cookie. This provider tells
 * the API which workspace the tab is showing (so a stale tab is refused rather
 * than answered with another client's data), announces sign-in/out to other
 * tabs, and reloads a tab whose workspace changed underneath it.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { api, ApiError, setExpectedSession, WORKSPACE_CHANGED_EVENT } from '@/lib/api'
import { announceSessionChange, onSessionChange, setNotice } from '@/lib/sessionSync'
import type { Me, Role } from '@/lib/types'

interface SessionValue {
  me: Me | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  logout: () => Promise<void>
  hasRole: (...roles: Role[]) => boolean
}

const SessionContext = createContext<SessionValue | null>(null)

function sameSession(a: Me | null, b: Me | null): boolean {
  return !!a && !!b && a.client.id === b.client.id && a.user.id === b.user.id
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMeState] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const router = useRouter()
  const pathname = usePathname()
  const meRef = useRef<Me | null>(null)
  const checking = useRef(false)

  const setMe = useCallback((value: Me | null) => {
    meRef.current = value
    setExpectedSession(value ? { clientId: value.client.id, userId: value.user.id } : null)
    setMeState(value)
  }, [])

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>('/api/auth/me', { anyWorkspace: true }))
      setError(null)
    } catch (err) {
      setMe(null)
      if (err instanceof ApiError && err.status === 401) {
        if (pathname !== '/login') router.replace('/login')
      } else {
        setError(err instanceof Error ? err.message : 'Could not load session')
      }
    } finally {
      setLoading(false)
    }
  }, [pathname, router, setMe])

  useEffect(() => {
    void refresh()
  }, [refresh])

  /** Compare the cookie's session with what this tab shows; reload if different. */
  const checkSession = useCallback(async () => {
    const shown = meRef.current
    if (!shown || checking.current) return
    checking.current = true
    try {
      const current = await api.get<Me>('/api/auth/me', { anyWorkspace: true })
      if (sameSession(shown, current)) return
      setNotice(
        `You signed in as ${current.user.display_name || current.user.email} at ` +
          `${current.client.company_name} in another tab, so this tab switched too. ` +
          `Nothing from ${shown.client.company_name} is shown here any more.`
      )
      window.location.assign('/chat')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setNotice('You signed out in another tab.')
        window.location.assign('/login')
      }
    } finally {
      checking.current = false
    }
  }, [])

  useEffect(() => {
    const stopSync = onSessionChange(() => void checkSession())
    const onFocus = () => void checkSession()
    const onVisible = () => {
      if (document.visibilityState === 'visible') void checkSession()
    }
    const onStale = () => void checkSession()
    window.addEventListener('focus', onFocus)
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener(WORKSPACE_CHANGED_EVENT, onStale)
    return () => {
      stopSync()
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener(WORKSPACE_CHANGED_EVENT, onStale)
    }
  }, [checkSession])

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout', undefined, { anyWorkspace: true })
    } finally {
      setMe(null)
      announceSessionChange()
      router.replace('/login')
    }
  }, [router, setMe])

  const hasRole = useCallback(
    (...roles: Role[]) => {
      if (!me) return false
      if (me.user.roles.includes('ADMIN')) return true
      return roles.some((role) => me.user.roles.includes(role))
    },
    [me]
  )

  return (
    <SessionContext.Provider value={{ me, loading, error, refresh, logout, hasRole }}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSession() {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession must be used inside SessionProvider')
  return value
}
