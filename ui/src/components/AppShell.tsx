'use client'

/**
 * Application shell: navigation, workspace banner and the mode indicator.
 *
 * The mode banner is deliberately always visible. A user needs to know at a
 * glance whether the assistant can act, because that is the difference between
 * "I asked it to send the email" and "an email was sent".
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
  { href: '/knowledge', label: 'Knowledge' },
  { href: '/approvals', label: 'Approvals' },
  { href: '/reviews', label: 'Reviews' },
  { href: '/workflows', label: 'Workflows' },
  { href: '/integrations', label: 'Integrations' },
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
      <div className="flex h-dvh items-center justify-center text-xs text-muted">
        Loading workspace…
      </div>
    )
  }
  if (!me) return null

  const mode = me.client.default_execution_mode
  const items = NAV.filter((item) => !item.roles || hasRole(...item.roles))

  return (
    <div className="flex h-dvh flex-col bg-background text-primary">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-6 py-3">
        <div className="flex items-baseline gap-3">
          <span className="font-geist text-sm font-medium">
            {me.client.company_name}
          </span>
          <span className="font-dmmono text-[10px] uppercase text-muted">
            {me.client.slug}
          </span>
        </div>

        <Badge tone={modeTone(mode)}>{MODE_LABEL[mode] ?? mode}</Badge>
        {!me.client.allow_phi && <Badge tone="neutral">PHI off</Badge>}
        {!me.client.allow_card_data && <Badge tone="neutral">Card data off</Badge>}

        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <div className="font-geist text-xs">{me.user.display_name}</div>
            <div className="font-dmmono text-[10px] uppercase text-muted">
              {me.user.roles.join(' · ')}
            </div>
          </div>
          <button
            onClick={() => void logout()}
            className="rounded-lg border border-border px-3 py-1.5 font-geist text-xs text-muted transition hover:text-primary"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav className="w-44 shrink-0 border-r border-border p-3">
          <ul className="space-y-1">
            {items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`)
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    className={cn(
                      'block rounded-lg px-3 py-2 font-geist text-xs transition',
                      active
                        ? 'bg-background-secondary text-primary'
                        : 'text-muted hover:bg-background-secondary/50 hover:text-primary'
                    )}
                  >
                    {item.label}
                  </Link>
                </li>
              )
            })}
          </ul>
        </nav>
        <main className="min-w-0 flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  )
}
