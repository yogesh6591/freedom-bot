'use client'

import { DOMAINS } from '@/lib/plans'

export function DomainMarquee() {
  const row = [...DOMAINS, ...DOMAINS]
  return (
    <div className="relative overflow-hidden border-y border-line py-5">
      <div aria-hidden className="marquee-fade-l pointer-events-none absolute inset-y-0 left-0 z-10 w-24" />
      <div aria-hidden className="marquee-fade-r pointer-events-none absolute inset-y-0 right-0 z-10 w-24" />
      <div className="marquee-track flex w-max gap-3">
        {row.map((domain, index) => (
          <span
            key={`${domain.id}-${index}`}
            className="chip inline-flex items-center rounded-full px-4 py-2 text-sm"
          >
            <span
              className="mr-2 h-1.5 w-1.5 rounded-full"
              style={{ background: 'var(--brand-soft)' }}
            />
            {domain.label}
          </span>
        ))}
      </div>
    </div>
  )
}
