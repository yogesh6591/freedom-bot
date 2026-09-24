'use client'

/**
 * Session context.
 *
 * Holds who the signed-in user is and what their workspace allows, fetched from
 * `/api/auth/me`. Used to hide controls a role cannot use — a convenience, never
 * a control: the same checks run server-side on every request.
 */

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { useRouter, usePathname } from 'next/navigation'
import { api, ApiError } from '@/lib/api'
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

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const router = useRouter()
  const pathname = usePathname()

  const refresh = useCallback(async () => {
    try {
      setMe(await api.get<Me>('/api/auth/me'))
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
  }, [pathname, router])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout')
    } finally {
      setMe(null)
      router.replace('/login')
    }
  }, [router])

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
