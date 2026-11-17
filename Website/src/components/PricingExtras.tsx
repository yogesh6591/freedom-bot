'use client'

import { Reveal, Stagger, StaggerItem } from '@/components/motion'

const FAQ = [
  {
    q: 'Can I change plans later?',
    a: 'Yes. Upgrade anytime to unlock more capability packs. Downgrades take effect at the next billing cycle.'
  },
  {
    q: 'What is domain access?',
    a: 'During onboarding you choose department packs like Sales, Finance, or Legal. FreedomBot only enables the domains you purchase.'
  },
  {
    q: 'Do seats include approvals?',
    a: 'Every plan includes approval checkpoints. Enterprise adds custom policy packs, SSO, and dedicated success support.'
  },
  {
    q: 'Is payment required before signing into FreedomBot?',
    a: 'Yes for self-serve onboarding. After payment completes, use the same email and password on the main FreedomBot app.'
  }
]

export function PricingExtras() {
  return (
    <Reveal className="mt-16">
      <Stagger className="grid gap-4 md:grid-cols-2">
        {FAQ.map((item) => (
          <StaggerItem key={item.q}>
            <article className="panel tilt-card p-6">
              <h2 className="text-base font-semibold text-fg">{item.q}</h2>
              <p className="mt-2 text-sm leading-6 text-fg-muted">{item.a}</p>
            </article>
          </StaggerItem>
        ))}
      </Stagger>
    </Reveal>
  )
}
