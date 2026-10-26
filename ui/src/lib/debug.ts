'use client'

/**
 * Per-browser sidebar switches, stored in localStorage and broadcast with a window event so every open
 * component updates at once.
 *
 * - Debug: the chat shows which tools each reply called.
 * - Acquisition pages: shows the acquisition links the demo hides (see DEMO_HIDE_ACQUISITION in AppShell).
 */

import { useCallback, useEffect, useState } from 'react'

const EVENT = 'bizos:switch-changed'

function read(key: string): boolean {
  try {
    return localStorage.getItem(key) === 'on'
  } catch {
    return false
  }
}

function useStoredSwitch(key: string): [boolean, (next: boolean) => void] {
  const [on, setOn] = useState(false)

  useEffect(() => {
    setOn(read(key))
    const sync = () => setOn(read(key))
    window.addEventListener(EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [key])

  const set = useCallback(
    (next: boolean) => {
      try {
        localStorage.setItem(key, next ? 'on' : 'off')
      } catch {
        /* storage blocked: still switch for this page */
      }
      setOn(next)
      window.dispatchEvent(new Event(EVENT))
    },
    [key]
  )

  return [on, set]
}

export function useDebug(): [boolean, (next: boolean) => void] {
  return useStoredSwitch('bizos:debug')
}

export function useShowAcquisition(): [boolean, (next: boolean) => void] {
  return useStoredSwitch('bizos:show-acquisition')
}
