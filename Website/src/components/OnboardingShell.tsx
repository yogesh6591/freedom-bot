'use client'

import { usePathname } from 'next/navigation'
import { BrandMark } from './BrandMark'
import { ThemeToggle } from './ThemeToggle'
import { APP_URL } from '@/lib/urls'
import { cn } from '@/lib/utils'

const STEPS = [
  { href: '/onboarding/company', label: 'Company', step: 1 },
  { href: '/onboarding/domain', label: 'Access', step: 2 },
  { href: '/onboarding/plan', label: 'Plan', step: 3 },
  { href: '/onboarding/payment', label: 'Payment', step: 4 }
]

export function OnboardingShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const current =
    STEPS.find((s) => pathname.startsWith(s.href))?.step ??
    (pathname.includes('complete') ? 5 : 1)

  return (
    <div className="min-h-screen">
      <header className="site-header is-scrolled sticky top-0 z-40">
        <div className="container-page flex h-16 items-center justify-between gap-3">
          <BrandMark href="/" />
          <div className="flex items-center gap-2 sm:gap-3">
            <ThemeToggle />
            <a href={APP_URL} className="text-sm text-fg-muted hover:text-fg">
              Already have an account? Sign in
            </a>
          </div>
        </div>
      </header>

      <div className="container-page py-10 sm:py-14">
        {!pathname.includes('complete') && (
          <ol className="mb-10 flex flex-wrap items-center gap-2 sm:gap-3">
            {STEPS.map((item, index) => {
              const done = current > item.step
              const active = current === item.step
              return (
                <li key={item.href} className="flex items-center gap-2 sm:gap-3">
                  <div
                    className={cn(
                      'flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-semibold',
                      active && 'border-brand/50 bg-brand/15 text-fg',
                      done && 'border-accent/30 bg-accent/10 text-accent-soft',
                      !active && !done && 'border-line text-fg-muted'
                    )}
                  >
                    <span
                      className={cn(
                        'inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px]',
                        active && 'bg-brand text-white',
                        done && 'bg-accent/30 text-accent-soft',
                        !active && !done && 'bg-surface-3'
                      )}
                    >
                      {item.step}
                    </span>
                    {item.label}
                  </div>
                  {index < STEPS.length - 1 && (
                    <span className="hidden h-px w-6 bg-[color:var(--line)] sm:block" />
                  )}
                </li>
              )
            })}
          </ol>
        )}
        {children}
      </div>
    </div>
  )
}
