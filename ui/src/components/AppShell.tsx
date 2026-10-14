'use client'

/**
 * FreedomBot's persistent workspace shell.
 * This file deliberately changes presentation only: role and tenant checks
 * still come from the existing session context and the server remains authoritative.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import {
  BookOpenText,
  CheckCircle2,
  ChevronRight,
  Headset,
  LayoutGrid,
  LogOut,
  Menu,
  MessageSquareText,
  ScrollText,
  Settings2,
  ShieldCheck,
  X,
  type LucideIcon
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Btn } from '@/components/common/primitives'
import { BrandMark } from '@/components/BrandMark'
import { useSession } from '@/components/SessionProvider'
import { ThemeToggle } from '@/components/ThemeToggle'
import { takeNotice } from '@/lib/sessionSync'
import type { Me, Role } from '@/lib/types'

type NavItem = {
  href: string
  label: string
  icon: LucideIcon
  roles?: Role[]
  platformOwner?: boolean
}

type NavGroup = { label: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Workspace',
    items: [
      { href: '/chat', label: 'Assistant', icon: MessageSquareText },
      { href: '/memory', label: 'Memory', icon: BookOpenText },
      { href: '/approvals', label: 'Approvals', icon: CheckCircle2 }
    ]
  },
  {
    label: 'Management',
    items: [
      {
        href: '/support',
        label: 'Support',
        icon: Headset,
        roles: ['OPERATOR', 'ADMIN']
      },
      {
        href: '/audit',
        label: 'Audit log',
        icon: ScrollText,
        roles: ['APPROVER', 'OPERATOR', 'ADMIN']
      },
      {
        href: '/admin',
        label: 'Administration',
        icon: Settings2,
        roles: ['ADMIN']
      },
      {
        href: '/owner',
        label: 'Platform owner',
        icon: LayoutGrid,
        platformOwner: true
      }
    ]
  }
]

const AREA_LABEL: Record<string, string> = {
  finance: 'Finance',
  ops: 'Operations',
  operations: 'Operations',
  hr: 'Human resources',
  salary: 'Payroll',
  legal: 'Legal',
  exec: 'Executive',
  sales: 'Sales',
  strategy: 'Strategy'
}

function accessLabel(me: Me): string {
  if (me.user.primary_role === 'ADMIN' || me.user.roles.includes('ADMIN'))
    return 'Administrator'
  const scope = me.user.data_scopes?.[0]
  if (scope) return AREA_LABEL[scope.toLowerCase()] ?? scope
  return (me.user.primary_role ?? 'Member')
    .toLowerCase()
    .replace(/^./, (c) => c.toUpperCase())
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading, hasRole, logout } = useSession()
  const pathname = usePathname()
  const router = useRouter()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    if (!loading && !me) router.replace('/login')
  }, [loading, me, router])
  useEffect(() => setNotice(takeNotice()), [])
  useEffect(() => setMobileNavOpen(false), [pathname])
  useEffect(() => {
    if (!mobileNavOpen) return
    const container = sidebarRef.current
    const trigger = mobileNavTriggerRef.current
    if (!container) return
    const focusable = Array.from(
      container.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')
    ).filter((item) => item.getClientRects().length > 0)
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    first?.focus()

    function onKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        setMobileNavOpen(false)
      }
      if (event.key !== 'Tab' || focusable.length === 0) return
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }

    document.addEventListener('keydown', onKeydown)
    return () => {
      document.removeEventListener('keydown', onKeydown)
      trigger?.focus()
    }
  }, [mobileNavOpen])

  if (loading) {
    return (
      <div
        className="flex h-dvh items-center justify-center bg-background"
        role="status"
        aria-label="Loading workspace"
      >
        <div className="flex flex-col items-center gap-4">
          <BrandMark className="h-12 w-12 animate-pulse" />
          <p className="text-sm font-medium text-muted">
            Opening your workspace…
          </p>
        </div>
      </div>
    )
  }
  if (!me) return null

  const isChat = pathname === '/chat' || pathname.startsWith('/chat/')
  const accessibleGroups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) =>
      item.platformOwner
        ? Boolean(me.user.is_platform_owner)
        : !item.roles || hasRole(...item.roles)
    )
  })).filter((group) => group.items.length > 0)
  const activeItem = accessibleGroups
    .flatMap((group) => group.items)
    .find(
      (item) => pathname === item.href || pathname.startsWith(`${item.href}/`)
    )
  const activeLabel = activeItem?.label ?? 'Workspace'
  const userInitials = (me.user.display_name?.trim() || 'M')
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0))
    .join('')
    .toUpperCase()

  return (
    <div className="flex h-dvh min-h-0 overflow-hidden bg-background text-primary">
      {mobileNavOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-[#0c1a20]/65 lg:hidden"
          aria-label="Close navigation"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <aside
        ref={sidebarRef}
        role={mobileNavOpen ? 'dialog' : undefined}
        aria-modal={mobileNavOpen ? true : undefined}
        aria-label={mobileNavOpen ? 'Workspace navigation' : undefined}
        id="workspace-navigation"
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-[268px] shrink-0 flex-col border-r border-white/10 bg-[#152a30] text-[#e8f0f0] transition-transform duration-200 lg:static lg:translate-x-0',
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex h-[88px] shrink-0 items-center justify-between border-b border-white/[0.09] px-5">
          <Link
            href="/chat"
            onClick={() => setMobileNavOpen(false)}
            className="flex min-h-11 min-w-0 items-center gap-3 rounded-lg focus-visible:outline-offset-4"
            aria-label="FreedomBot — assistant"
          >
            <BrandMark inverse className="h-10 w-10" />
            <span className="min-w-0">
              <span className="block text-[17px] font-semibold tracking-[-0.035em] text-white">
                FreedomBot
              </span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.17em] text-[#8da6aa]">
                Workspace
              </span>
            </span>
          </Link>
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close menu"
            className="flex h-10 w-10 items-center justify-center rounded-lg text-[#b4c9cb] hover:bg-white/10 lg:hidden"
          >
            <X size={19} />
          </button>
        </div>

        <div className="px-4 pb-3 pt-5">
          <div className="flex min-w-0 items-center gap-3 rounded-xl border border-white/[0.09] bg-white/[0.045] px-3 py-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#315157] text-sm font-bold text-[#d5ede9]">
              {me.client.company_name?.charAt(0)?.toUpperCase() || 'W'}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-semibold text-white">
                {me.client.company_name}
              </p>
              <p className="mt-0.5 text-[11px] text-[#9bb1b5]">
                Company workspace
              </p>
            </div>
          </div>
        </div>

        <nav
          aria-label="Main navigation"
          className="workspace-scroll min-h-0 flex-1 overflow-y-auto px-3 py-2"
        >
          {accessibleGroups.map((group) => (
            <div key={group.label} className="mb-6">
              <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-[#7f9a9e]">
                {group.label}
              </p>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active =
                    pathname === item.href ||
                    pathname.startsWith(`${item.href}/`)
                  const Icon = item.icon
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      onClick={() => setMobileNavOpen(false)}
                      className={cn(
                        'group relative flex min-h-11 items-center gap-3 rounded-lg px-3 text-[13px] font-medium transition-colors focus-visible:outline-offset-[-3px]',
                        active
                          ? 'bg-[#29484d] font-semibold text-white'
                          : 'text-[#b3c7ca] hover:bg-white/[0.07] hover:text-white'
                      )}
                    >
                      {active && (
                        <span
                          aria-hidden
                          className="absolute bottom-2.5 left-0 top-2.5 w-[3px] rounded-r bg-[#84d5c5]"
                        />
                      )}
                      <Icon
                        size={18}
                        strokeWidth={active ? 2.15 : 1.8}
                        aria-hidden
                      />
                      <span className="flex-1 truncate">{item.label}</span>
                      {active && (
                        <ChevronRight
                          size={15}
                          className="text-[#9bc6c1]"
                          aria-hidden
                        />
                      )}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className="shrink-0 border-t border-white/[0.09] px-4 pb-4 pt-4">
          {(!me.client.allow_phi || !me.client.allow_card_data) && (
            <div className="mb-4 rounded-lg border border-white/[0.07] bg-white/[0.035] px-3 py-2.5">
              <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-[#bfd4d3]">
                <ShieldCheck size={14} /> Data restrictions
              </div>
              <p className="text-[11px] leading-[1.6] text-[#98b0b2]">
                {[
                  !me.client.allow_phi ? 'Health data disabled' : null,
                  !me.client.allow_card_data ? 'Card data disabled' : null
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </div>
          )}
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#36565c] text-xs font-semibold text-white"
              aria-hidden
            >
              {userInitials}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-semibold text-white">
                {me.user.display_name}
              </p>
              <p className="truncate text-[11px] text-[#9bb1b5]">
                {accessLabel(me)}
              </p>
            </div>
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-[#a8c0c3] hover:bg-white/10 hover:text-white"
              title="Sign out"
              aria-label="Sign out"
              onClick={() => void logout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="relative z-10 flex h-[72px] shrink-0 items-center justify-between gap-4 border-b border-border bg-background-secondary px-4 sm:px-7 lg:px-9">
          <div className="flex min-w-0 items-center gap-3">
            <button
              ref={mobileNavTriggerRef}
              type="button"
              aria-label="Open navigation"
              aria-expanded={mobileNavOpen}
              aria-controls="workspace-navigation"
              onClick={() => setMobileNavOpen(true)}
              className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border text-secondary hover:bg-background-elevated lg:hidden"
            >
              <Menu size={19} />
            </button>
            <div className="flex min-w-0 items-center gap-2 text-[13px]">
              <span className="hidden text-muted sm:inline">Workspace</span>
              <ChevronRight
                size={14}
                className="hidden text-slate-400 sm:inline"
                aria-hidden
              />
              <span className="truncate font-semibold text-primary">
                {activeLabel}
              </span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2 sm:gap-4">
            <div className="hidden items-center gap-2 rounded-md bg-purple-50 px-2.5 py-1.5 text-[11px] font-medium text-purple-700 dark:bg-background-elevated dark:text-secondary sm:flex">
              <ShieldCheck size={14} aria-hidden /> Private workspace
            </div>
            <span className="hidden h-6 w-px bg-border sm:block" aria-hidden />
            <ThemeToggle className="!rounded-lg" />
            <span
              className="flex h-9 w-9 items-center justify-center rounded-full bg-[#e5f0ee] text-xs font-semibold text-[#1e6d68] dark:bg-background-elevated dark:text-primary"
              aria-label={`Signed in as ${me.user.display_name}`}
            >
              {userInitials}
            </span>
          </div>
        </header>

        {notice && (
          <div
            role="status"
            className="flex shrink-0 items-start justify-between gap-4 border-b border-purple-200 bg-purple-50 px-5 py-3 text-[13px] text-secondary sm:px-9"
          >
            <span>{notice}</span>
            <Btn
              variant="ghost"
              size="sm"
              className="!min-h-7 shrink-0"
              onClick={() => setNotice(null)}
            >
              Dismiss
            </Btn>
          </div>
        )}

        <main
          id="main-content"
          className="min-h-0 min-w-0 flex-1 overflow-hidden"
        >
          <div
            className={cn(
              'mx-auto flex h-full min-h-0 min-w-0 flex-col',
              isChat
                ? 'max-w-[1600px] px-3 py-3 sm:px-6 sm:py-5 lg:px-7'
                : 'workspace-scroll max-w-[1440px] overflow-y-auto px-4 py-6 sm:px-7 sm:py-8 lg:px-10 lg:py-9'
            )}
          >
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
