import Link from 'next/link'
import { BrandMark } from './BrandMark'
import { APP_URL } from '@/lib/urls'

const COLUMNS = [
  {
    title: 'Product',
    links: [
      { href: '/features', label: 'Features' },
      { href: '/pricing', label: 'Pricing' },
      { href: '/signup', label: 'Start signup' }
    ]
  },
  {
    title: 'Company',
    links: [
      { href: '/#how-it-works', label: 'How it works' },
      { href: APP_URL, label: 'Sign in', external: true }
    ]
  },
  {
    title: 'Legal',
    links: [
      { href: '/privacy', label: 'Privacy policy' },
      { href: '/terms', label: 'Terms of service' }
    ]
  }
]

export function Footer() {
  return (
    <footer className="border-t border-line" style={{ background: 'color-mix(in srgb, var(--bg) 88%, transparent)' }}>
      <div className="container-page grid gap-10 py-14 sm:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-4">
          <BrandMark />
          <p className="max-w-xs text-sm leading-6 text-fg-muted">
            The governed company assistant for operators who need memory,
            approvals, and accountable action — not another chatbot.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <div key={col.title}>
            <p className="mb-3 text-sm font-semibold text-fg">{col.title}</p>
            <ul className="space-y-2">
              {col.links.map((link) => (
                <li key={link.href}>
                  {'external' in link && link.external ? (
                    <a
                      href={link.href}
                      className="text-sm text-fg-muted transition hover:text-fg"
                    >
                      {link.label}
                    </a>
                  ) : (
                    <Link
                      href={link.href}
                      className="text-sm text-fg-muted transition hover:text-fg"
                    >
                      {link.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-line">
        <div className="container-page flex flex-col gap-2 py-6 text-xs text-fg-muted sm:flex-row sm:items-center sm:justify-between">
          <p>© {new Date().getFullYear()} FreedomBot. All rights reserved.</p>
          <p>Built for multi-tenant workspaces with approval-first automation.</p>
        </div>
      </div>
    </footer>
  )
}
