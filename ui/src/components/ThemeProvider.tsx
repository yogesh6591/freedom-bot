'use client'

/**
 * Day / night theme. Persists to localStorage and toggles the `dark` class on <html>.
 * Default is night, matching Acquisition. The same choice is written for Acquisition screens.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode
} from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'freedombot-theme'
const ACQUISITION_KEY = 'fb-theme'

type ThemeContextValue = {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function readStored(): Theme {
  if (typeof window === 'undefined') return 'dark'
  try {
    const raw = localStorage.getItem(STORAGE_KEY) || localStorage.getItem(ACQUISITION_KEY)
    if (raw === 'light' || raw === 'dark') return raw
  } catch {
    /* ignore */
  }
  return 'dark'
}

function applyTheme(theme: Theme, animate = true) {
  const root = document.documentElement
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (animate && !reduce) {
    root.classList.add('theme-transition')
    window.setTimeout(() => root.classList.remove('theme-transition'), 600)
  }
  root.classList.toggle('dark', theme === 'dark')
  root.style.colorScheme = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
    localStorage.setItem(ACQUISITION_KEY, theme)
  } catch {
    /* ignore */
  }
  document.querySelectorAll('iframe').forEach((frame) => {
    try {
      frame.contentDocument?.documentElement.setAttribute('data-theme', theme)
    } catch {
      /* cross-origin frame */
    }
  })
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>('dark')

  useEffect(() => {
    const initial = readStored()
    setThemeState(initial)
    applyTheme(initial, false)
  }, [])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    applyTheme(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setTheme(theme === 'dark' ? 'light' : 'dark')
  }, [setTheme, theme])

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return ctx
}
