'use client'

/**
 * Debug mode: a per-browser switch (sidebar) that makes the chat show which tools each reply called.
 * Stored in localStorage and broadcast with a window event so every open component updates at once.
 */

import { useCallback, useEffect, useState } from 'react'

const KEY = 'bizos:debug'
const EVENT = 'bizos:debug-changed'

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === 'on'
  } catch {
    return false
  }
}

export function useDebug(): [boolean, (next: boolean) => void] {
  const [on, setOn] = useState(false)

  useEffect(() => {
    setOn(read())
    const sync = () => setOn(read())
    window.addEventListener(EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])

  const set = useCallback((next: boolean) => {
    try {
      localStorage.setItem(KEY, next ? 'on' : 'off')
    } catch {
      /* storage blocked: still switch for this page */
    }
    setOn(next)
    window.dispatchEvent(new Event(EVENT))
  }, [])

  return [on, set]
}
