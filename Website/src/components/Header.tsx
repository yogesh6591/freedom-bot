'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { Menu, X } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { BrandMark } from './BrandMark'
import { ThemeToggle } from './ThemeToggle'
import { SIGN_IN_URL, signupUrl } from '@/lib/urls'
import { cn } from '@/lib/utils'

const NAV = [
  { href: '/features', label: 'Features' },
  { href: '/pricing', label: 'Pricing' },
  { href: '/#how-it-works', label: 'How it works' }
]

export function Header() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 12)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  return (
    <header
      className={cn('site-header sticky top-0 z-40 transition duration-300', scrolled && 'is-scrolled')}
    >
      <div className="container-page flex h-16 items-center justify-between gap-4">
        <BrandMark />

        <nav className="hidden items-center gap-1 md:flex">
          {NAV.map((item) => {
            const active = pathname === item.href
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  'rounded-lg px-3 py-2 text-sm font-medium transition',
                  active
                    ? 'bg-[color:var(--surface-3)] text-fg'
                    : 'text-fg-muted hover:bg-[color:var(--surface-3)] hover:text-fg'
                )}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <ThemeToggle />
          <a href={SIGN_IN_URL} className="btn-ghost">
            Sign in
          </a>
          <a href={signupUrl()} className="btn-primary btn-shine">
            Start signup
          </a>
        </div>

        <div className="flex items-center gap-2 md:hidden">
          <ThemeToggle />
          <button
            type="button"
            className="btn-secondary px-3 py-2"
            aria-label={open ? 'Close menu' : 'Open menu'}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden border-t border-line md:hidden"
            style={{ background: 'var(--bg-2)' }}
          >
            <nav className="flex flex-col gap-1 px-5 py-4">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="rounded-lg px-3 py-3 text-sm text-fg-muted hover:bg-[color:var(--surface-3)]"
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </Link>
              ))}
              <a
                href={SIGN_IN_URL}
                className="rounded-lg px-3 py-3 text-sm text-fg-muted hover:bg-[color:var(--surface-3)]"
                onClick={() => setOpen(false)}
              >
                Sign in
              </a>
              <a
                href={signupUrl()}
                className="btn-primary mt-2"
                onClick={() => setOpen(false)}
              >
                Start signup
              </a>
            </nav>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  )
}
