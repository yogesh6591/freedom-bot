'use client'

/**
 * FreedomBot's persistent workspace shell.
 * This file deliberately changes presentation only: role and tenant checks
 * still come from the existing session context and the server remains authoritative.
 */

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useDebug, useShowAcquisition } from '@/lib/debug'
import { adminUnlocked, isHomePath } from '@/lib/panel'
import { RequestAccessDialog } from '@/components/RequestAccess'
import { SidebarSwitch } from '@/components/SidebarSwitch'
import { useEffect, useRef, useState } from 'react'
import {
  BookOpenText,
  CheckCircle2,
  ChevronRight,
  Headset,
  KeyRound,
  LayoutGrid,
  LogOut,
  Megaphone,
  Menu,
  MessageSquareText,
  MessagesSquare,
  Plug,
  Route,
  ScrollText,
  Send,
  Sparkles,
  Settings2,
  ShieldCheck,
  Unplug,
  Users,
  X,
  type LucideIcon,
  Bug,
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
  /** Hidden while DEMO_HIDE_ACQUISITION is true. */
  demoHidden?: boolean
}

/**
 * Demo switch: true hides the acquisition pages tagged `demoHidden` (Overview, Prospects,
 * Assessments, Engine & AI, Buyer Journey) unless an admin turns on "Acquisition pages" in the sidebar.
 * Set to false to always show them. The pages still work by URL.
 */
const DEMO_HIDE_ACQUISITION = true

type NavGroup = { label: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Workspace',
    items: [
      { href: '/admin/chat', label: 'Assistant', icon: MessageSquareText },
      { href: '/memory', label: 'Memory', icon: BookOpenText },
      { href: '/approvals', label: 'Approvals', icon: CheckCircle2 },
      { href: '/integrations', label: 'Integrations', icon: Plug }
    ]
  },
  {
    label: 'Acquisition',
    items: [
      { href: '/acquisition/overview', label: 'Overview', icon: Send, roles: ['ADMIN'], demoHidden: true },
      { href: '/acquisition/prospects', label: 'Prospects', icon: Users, roles: ['ADMIN'], demoHidden: true }
    ]
  },
  {
    label: 'Outreach',
    items: [
      { href: '/acquisition/assessments', label: 'Assessments', icon: MessagesSquare, roles: ['ADMIN'], demoHidden: true }
    ]
  },
  {
    label: 'Settings',
    items: [
      { href: '/acquisition/settings/ai', label: 'Engine & AI', icon: Sparkles, roles: ['ADMIN'], demoHidden: true },
      { href: '/acquisition/settings/journey', label: 'Buyer Journey', icon: Route, roles: ['ADMIN'], demoHidden: true },
      { href: '/acquisition/integrations', label: 'Integrations', icon: Unplug, roles: ['ADMIN'] }
    ]
  },
  {
    label: 'Management',
    items: [
      {
        // Everyone may raise a ticket; only Operators and Admins work the queue (the API decides).
        href: '/support',
        label: 'Support',
        icon: Headset
      },
      {
        href: '/audit',
        label: 'Audit log',
        icon: ScrollText,
        roles: ['APPROVER', 'OPERATOR', 'ADMIN']
      },
      {
        href: '/admin/settings',
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
  if (me.user.job_role_label) return me.user.job_role_label
  const scope = me.user.data_scopes?.[0]
  return scope ? (AREA_LABEL[scope.toLowerCase()] ?? scope) : 'Member'
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, loading, hasRole, logout } = useSession()
  const [debug, setDebug] = useDebug()
  const [showAcquisition, setShowAcquisition] = useShowAcquisition()
  const pathname = usePathname()
  // /chat is the home screen; every other page is the admin panel, unlocked by signing in at /admin.
  const chatOnly = isHomePath(pathname)
  const [adminOk, setAdminOk] = useState<boolean | null>(null)
  const router = useRouter()
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [accessOpen, setAccessOpen] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
  const mobileNavTriggerRef = useRef<HTMLButtonElement>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [acquirePath, setAcquirePath] = useState<string | null>(null)
  const [locationQuery, setLocationQuery] = useState('')

  useEffect(() => {
    if (loading || me) return
    // Keep a ticket email link (?ticket=<id>) through sign-in.
    const ticket = chatOnly ? new URLSearchParams(window.location.search).get('ticket') : null
    router.replace(chatOnly ? (ticket ? `/login?ticket=${encodeURIComponent(ticket)}` : '/login') : '/admin')
  }, [loading, me, router, chatOnly])
  useEffect(() => {
    if (me) setAdminOk(adminUnlocked(me.user.id))
  }, [me, pathname])
  // Admin pages need the admin sign-in; a home sign-in alone does not open them.
  useEffect(() => {
    if (me && !chatOnly && adminOk === false) router.replace('/admin')
  }, [me, chatOnly, adminOk, router])
  useEffect(() => setNotice(takeNotice()), [])
  useEffect(() => {
    setAcquirePath(null)
    setLocationQuery(window.location.search.replace(/^\?/, ''))
  }, [pathname])
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin) return
      const data = event.data as { type?: string; path?: string } | null
      if (!data || data.type !== 'fb-acquire-nav' || typeof data.path !== 'string') return
      const next = `/acquisition${data.path.startsWith('/') ? data.path : `/${data.path}`}`
      setAcquirePath(next.split('?')[0])
      if (window.location.pathname !== next.split('?')[0]) {
        window.history.replaceState(null, '', next)
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])
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
  if (!me || (!chatOnly && adminOk !== true)) return null
  // Chat home: the chat page draws its own sidebar (conversations, Debug, account), full screen.
  if (chatOnly) {
    return (
      <div className="flex h-dvh min-h-0 overflow-hidden bg-background text-primary">
        <main id="main-content" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {children}
        </main>
      </div>
    )
  }

  const isChat = pathname === '/admin/chat' || pathname.startsWith('/admin/chat/')
  const isAcquisition = pathname === '/acquisition' || pathname.startsWith('/acquisition/')
  const navPath = isAcquisition && acquirePath ? acquirePath.split('?')[0] : pathname
  const navQuery = isAcquisition && acquirePath?.includes('?')
    ? acquirePath.slice(acquirePath.indexOf('?') + 1)
    : locationQuery
  const accessibleGroups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) =>
      DEMO_HIDE_ACQUISITION && item.demoHidden && !showAcquisition
        ? false
        : item.platformOwner
        ? Boolean(me.user.is_platform_owner)
        : !item.roles || hasRole(...item.roles)
    )
  })).filter((group) => group.items.length > 0)
  const activeItem = accessibleGroups
    .flatMap((group) => group.items)
    .find(
      (item) => {
        const [itemPath, itemQuery] = item.href.split('?')
        const pathMatch = navPath === itemPath || navPath.startsWith(`${itemPath}/`)
        if (!itemQuery) return pathMatch && !item.href.includes('?')
        return navPath === itemPath && new URLSearchParams(navQuery).get('tab') === new URLSearchParams(itemQuery).get('tab')
      }
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
          className="fixed inset-0 z-40 bg-[#1c102b]/75 lg:hidden"
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
          'fixed inset-y-0 left-0 z-50 flex w-[268px] shrink-0 flex-col border-r border-border bg-background-secondary text-primary transition-transform duration-200 lg:static lg:translate-x-0',
          mobileNavOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex h-[88px] shrink-0 items-center justify-between border-b border-border px-5">
          <Link
            href="/admin/chat"
            onClick={() => setMobileNavOpen(false)}
            className="flex min-h-11 min-w-0 items-center gap-3 rounded-lg focus-visible:outline-offset-4"
            aria-label="FreedomBot — assistant"
          >
            <BrandMark inverse className="h-10 w-10" />
            <span className="min-w-0">
              <span className="block text-[17px] font-semibold tracking-[-0.035em] text-primary">
                FreedomBot
              </span>
              <span className="block text-[10px] font-medium uppercase tracking-[0.17em] text-muted">
                Workspace
              </span>
            </span>
          </Link>
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="Close menu"
            className="flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-background-elevated lg:hidden"
          >
            <X size={19} />
          </button>
        </div>

        <div className="px-4 pb-3 pt-5">
          <div className="flex min-w-0 items-center gap-3 rounded-xl border border-border bg-background-elevated px-3 py-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand text-sm font-bold text-white">
              {me.client.company_name?.charAt(0)?.toUpperCase() || 'W'}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-semibold text-primary">
                {me.client.company_name}
              </p>
              <p className="mt-0.5 text-[11px] text-muted">
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
              <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted">
                {group.label}
              </p>
              <div className="space-y-1">
                {group.items.map((item) => {
                  const active = activeItem?.href === item.href
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
                          ? 'bg-accent font-semibold text-primary'
                          : 'text-secondary hover:bg-background-elevated hover:text-primary'
                      )}
                    >
                      {active && (
                        <span
                          aria-hidden
                          className="absolute bottom-2.5 left-0 top-2.5 w-[3px] rounded-r bg-brand"
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
                          className="text-brand"
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

        <div className="shrink-0 border-t border-border px-4 pb-4 pt-4">
          <SidebarSwitch
            label="Debug"
            icon={Bug}
            on={debug}
            onChange={setDebug}
            title="Show which tools the assistant called"
          />
          {DEMO_HIDE_ACQUISITION && hasRole('ADMIN') && (
            <SidebarSwitch
              label="Acquisition pages"
              icon={Megaphone}
              on={showAcquisition}
              onChange={setShowAcquisition}
              title="Show Overview, Prospects, Assessments, Engine & AI and Buyer Journey"
            />
          )}
          {(!me.client.allow_phi || !me.client.allow_card_data) && (
            <div className="mb-4 rounded-lg border border-border bg-background-elevated px-3 py-2.5">
              <div className="mb-1.5 flex items-center gap-2 text-[11px] font-semibold text-secondary">
                <ShieldCheck size={14} /> Data restrictions
              </div>
              <p className="text-[11px] leading-[1.6] text-muted">
                {[
                  !me.client.allow_phi ? 'Health data blocked' : null,
                  !me.client.allow_card_data ? 'Card data blocked' : null
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            </div>
          )}
          {!me.user.roles.includes('ADMIN') && (
            <button
              type="button"
              onClick={() => setAccessOpen(true)}
              className="mb-3 flex w-full items-center gap-2 rounded-lg border border-border bg-background-elevated px-3 py-2 text-[12px] font-semibold text-secondary hover:text-primary"
            >
              <KeyRound size={14} aria-hidden /> Request access
            </button>
          )}
          <div className="flex items-center gap-2.5">
            <div
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand text-xs font-semibold text-white"
              aria-hidden
            >
              {userInitials}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-semibold text-primary">
                {me.user.display_name}
              </p>
              <p className="truncate text-[11px] text-muted">
                {accessLabel(me)}
              </p>
            </div>
            <button
              type="button"
              className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-background-elevated hover:text-primary"
              title="Sign out"
              aria-label="Sign out"
              onClick={() => void logout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <RequestAccessDialog open={accessOpen} onClose={() => setAccessOpen(false)} />

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
              className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-xs font-semibold text-brand"
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
              isAcquisition || isChat
                ? 'h-full max-w-none'
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
