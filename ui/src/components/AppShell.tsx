'use client'

/**
 * Application shell — chat-first command center.
 * Chat is the product; Memory / Approvals / Audit / Admin are workspace tools.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Badge, Btn, modeTone } from '@/components/common/primitives'
import { useSession } from '@/components/SessionProvider'
import { takeNotice } from '@/lib/sessionSync'
import type { Role } from '@/lib/types'

const PRIMARY_NAV = { href: '/chat', label: 'Ask FreedomBot' } as const

const WORKSPACE_NAV: { href: string; label: string; roles?: Role[] }[] = [
  { href: '/memory', label: 'Memory' },
  { href: '/approvals', label: 'Approvals' },
  { href: '/support', label: 'Support', roles: ['OPERATOR', 'ADMIN'] },
  { href: '/owner', label: 'Owner', roles: ['ADMIN'] },
  { href: '/audit', label: 'Audit', roles: ['APPROVER', 'OPERATOR', 'ADMIN'] },
  { href: '/admin', label: 'Admin', roles: ['ADMIN'] }
]

const MODE_LABEL: Record<string, string> = {
  ADVISE: 'Advise — read only',
  DRAFT: 'Draft — prepares, never sends',
  WAIT_FOR_APPROVAL: 'Approval required',
  AUTO_WITHIN_SCOPE: 'Auto within scope'
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading, hasRole, logout } = useSession()
  const pathname = usePathname()
  const router = useRouter()

  useEffect(() => {
    if (!loading && !me) router.replace('/login')
  }, [loading, me, router])

  // One-time explanation after this tab was switched by another tab (M01-06).
  const [notice, setNotice] = useState<string | null>(null)
  useEffect(() => {
    setNotice(takeNotice())
  }, [])

  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-background" role="status">
        <div className="flex flex-col items-center gap-3">
          <div className="h-10 w-10 animate-pulse rounded-xl bg-brand/30" aria-hidden />
          <p className="type-muted animate-pulseSoft">Loading workspace…</p>
        </div>
      </div>
    )
  }
  if (!me) return null

  const mode = me.client.default_execution_mode
  const tools = WORKSPACE_NAV.filter((item) => !item.roles || hasRole(...item.roles))
  const chatActive = pathname === '/chat' || pathname.startsWith('/chat/')

  return (
    <div className="flex h-dvh flex-col text-primary">
      <header className="z-10 border-b border-border bg-background-secondary/90 shadow-panel backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <Link
            href="/chat"
            className="flex min-h-11 items-center gap-2.5 rounded-xl ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80"
          >
            <span
              aria-hidden
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand text-sm font-bold text-brand-ink shadow-glow"
            >
              FB
            </span>
            <div className="leading-tight">
              <div className="font-geist text-base font-bold tracking-tight">FreedomBot</div>
              <div className="type-caption">{me.client.company_name}</div>
            </div>
          </Link>

          <nav
            aria-label="Primary"
            className="order-last flex w-full items-center gap-1 overflow-x-auto border-t border-border pt-2 sm:order-none sm:ml-4 sm:w-auto sm:border-0 sm:pt-0"
          >
            <Link
              href={PRIMARY_NAV.href}
              aria-current={chatActive ? 'page' : undefined}
              className={cn(
                'inline-flex min-h-11 items-center whitespace-nowrap rounded-full px-4 font-geist text-sm font-semibold ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80',
                chatActive
                  ? 'bg-brand text-brand-ink shadow-glow'
                  : 'bg-brand/15 text-brand-soft hover:bg-brand/25 hover:text-primary'
              )}
            >
              {PRIMARY_NAV.label}
            </Link>

            <span aria-hidden className="mx-1 hidden h-4 w-px bg-border sm:inline-block" />

            {tools.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'inline-flex min-h-11 items-center whitespace-nowrap rounded-full px-3 font-geist text-xs font-medium ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80',
                    active
                      ? 'bg-background-elevated text-primary'
                      : 'text-muted/80 hover:bg-background-elevated/60 hover:text-muted'
                  )}
                >
                  {item.label}
                </Link>
              )
            })}
          </nav>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Badge tone={modeTone(mode)}>{MODE_LABEL[mode] ?? mode}</Badge>
            {!me.client.allow_phi && <Badge tone="neutral">PHI off</Badge>}
            {!me.client.allow_card_data && <Badge tone="neutral">Card off</Badge>}
            <div className="hidden border-l border-border pl-3 text-right md:block">
              <div className="font-geist text-xs font-semibold">{me.user.display_name}</div>
              <div className="type-caption">{me.user.roles.join(' · ')}</div>
            </div>
            <Btn variant="secondary" size="sm" onClick={() => void logout()}>
              Sign out
            </Btn>
          </div>
        </div>
      </header>

      {notice && (
        <div
          role="status"
          className="border-b border-brand/30 bg-brand/10 px-4 py-2 text-sm text-primary sm:px-6"
        >
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3">
            <span>{notice}</span>
            <Btn variant="ghost" size="sm" onClick={() => setNotice(null)}>
              Dismiss
            </Btn>
          </div>
        </div>
      )}
      <main className="min-h-0 flex-1 overflow-hidden">
        <div
          className={cn(
            'mx-auto flex h-full animate-fade-up flex-col p-4 sm:p-6',
            pathname.startsWith('/chat')
              ? 'min-h-0 max-w-measure'
              : 'max-w-6xl overflow-y-auto'
          )}
        >
          {children}
        </div>
      </main>
    </div>
  )
}
