'use client'

/**
 * Which screen a signed-in browser gets, decided by where the person signed in:
 *
 * - `/login` (home)        → "chat": the assistant only, with Debug, Acquisition pages and the account.
 * - `/admin` → `/login?panel=full` → "full": every page in the sidebar.
 *
 * A UI choice per browser, not a permission: the API still checks every request by role.
 */

import { useEffect, useState } from 'react'

export type Panel = 'chat' | 'full'

const KEY = 'bizos:panel'

export function readPanel(): Panel {
  try {
    return localStorage.getItem(KEY) === 'full' ? 'full' : 'chat'
  } catch {
    return 'chat'
  }
}

export function setPanel(panel: Panel): void {
  try {
    localStorage.setItem(KEY, panel)
  } catch {
    /* storage blocked: the default (chat) applies */
  }
}

/** The current panel, or null until it has been read in the browser. */
export function usePanel(): Panel | null {
  const [panel, set] = useState<Panel | null>(null)
  useEffect(() => set(readPanel()), [])
  return panel
}
