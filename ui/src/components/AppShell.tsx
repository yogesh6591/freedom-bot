'use client'

/**
 * Application shell — light product chrome with top navigation.
 * Mode stays visible so users always know whether the assistant can act.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect } from 'react'
import { cn } from '@/lib/utils'
import { Badge, modeTone } from '@/components/common/primitives'
import { useSession } from '@/components/SessionProvider'
import type { Role } from '@/lib/types'

const NAV: { href: string; label: string; roles?: Role[] }[] = [
  { href: '/chat', label: 'Chat' },
  { href: '/memory', label: 'Memory' },
  { href: '/approvals', label: 'Approvals' },
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

  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-background">
        <p className="animate-pulseSoft font-geist text-sm text-muted">Loading workspace…</p>
      </div>
    )
  }
  if (!me) return null

  const mode = me.client.default_execution_mode
  const items = NAV.filter((item) => !item.roles || hasRole(...item.roles))

  return (
    <div className="flex h-dvh flex-col text-primary">
      <header className="z-10 border-b border-border bg-white/90 shadow-panel backdrop-blur-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3 sm:px-6">
          <Link href="/chat" className="flex items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand text-sm font-bold text-brand-ink shadow-glow"
            >
              FB
            </span>
            <div className="leading-tight">
              <div className="font-geist text-base font-bold tracking-tight">FreedomBot</div>
              <div className="font-dmmono text-[10px] uppercase tracking-wider text-muted">
                {me.client.company_name}
              </div>
            </div>
          </Link>

          <nav className="order-last flex w-full gap-1 overflow-x-auto border-t border-border pt-2 sm:order-none sm:ml-4 sm:w-auto sm:border-0 sm:pt-0">
            {items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn(
                    'relative whitespace-nowrap rounded-full px-3.5 py-1.5 font-geist text-sm font-medium transition',
                    active
                      ? 'bg-brand text-brand-ink shadow-glow'
                      : 'text-muted hover:bg-background-elevated hover:text-primary'
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
              <div className="font-dmmono text-[10px] uppercase tracking-wider text-muted">
                {me.user.roles.join(' · ')}
              </div>
            </div>
            <button
              onClick={() => void logout()}
              className="rounded-full border border-border bg-white px-3 py-1.5 font-geist text-xs font-medium text-muted transition hover:border-brand-deep hover:bg-background-elevated hover:text-primary"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl animate-fade-up p-4 sm:p-6">{children}</div>
      </main>
    </div>
  )
}
