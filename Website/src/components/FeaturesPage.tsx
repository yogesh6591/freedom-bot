'use client'

import Link from 'next/link'
import {
  Building2,
  Fingerprint,
  LockKeyhole,
  Network,
  ScrollText,
  Users
} from 'lucide-react'
import { SiteShell } from '@/components/SiteShell'
import { Reveal, Stagger, StaggerItem } from '@/components/motion'
import { DOMAINS } from '@/lib/plans'

const PLATFORM = [
  {
    icon: Building2,
    title: 'Dedicated workspaces',
    text: 'Each client gets an isolated workspace and database. Company data stays apart from every other tenant.'
  },
  {
    icon: Users,
    title: 'Role-aware access',
    text: 'Administrators, operators, and approvers see only what policy allows. Permissions are enforced in code.'
  },
  {
    icon: ScrollText,
    title: 'Versioned memory',
    text: 'Policies and SOPs are stored with history so the assistant cites what the company currently trusts.'
  },
  {
    icon: LockKeyhole,
    title: 'Approval queue',
    text: 'Destructive or outbound actions wait for a named person. Nothing irreversible happens on a draft.'
  },
  {
    icon: Network,
    title: 'Integrations',
    text: 'Connect email, CRM, calendar, and more. Tool calls are audited with a clear why.'
  },
  {
    icon: Fingerprint,
    title: 'Data restrictions',
    text: 'Health data and card data can be blocked at the platform layer before the model is invoked.'
  }
]

export function FeaturesPage() {
  return (
    <SiteShell>
      <section className="section relative overflow-hidden">
        <div className="container-page relative">
          <Reveal>
            <div className="mb-12 max-w-2xl">
              <p className="eyebrow">Product</p>
              <h1 className="mt-3 font-display text-[2.6rem] font-semibold tracking-[-0.05em] text-fg sm:text-6xl">
                Everything operators need
                <span className="headline-gradient mt-1 block">
                  to trust an assistant
                </span>
              </h1>
              <p className="mt-5 lead">
                FreedomBot combines capability packs with a governed workspace
                shell — so the team can ask, draft, and act without giving up
                control.
              </p>
            </div>
          </Reveal>

          <Stagger className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {PLATFORM.map((item) => (
              <StaggerItem key={item.title}>
                <article className="panel tilt-card group p-6">
                  <item.icon className="mb-4 h-6 w-6 text-brand-soft transition duration-300 group-hover:scale-110" />
                  <h2 className="text-lg font-semibold text-fg">{item.title}</h2>
                  <p className="mt-2 text-sm leading-6 text-fg-muted">{item.text}</p>
                </article>
              </StaggerItem>
            ))}
          </Stagger>

          <Reveal className="mt-16">
            <h2 className="font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-4xl">
              Capability packs
            </h2>
            <p className="mt-3 max-w-2xl text-fg-muted">
              During onboarding, customers pick exactly which department domains
              unlock in FreedomBot.
            </p>
          </Reveal>

          <Stagger className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {DOMAINS.map((domain, index) => (
              <StaggerItem key={domain.id}>
                <div className="panel group p-5 transition duration-300 hover:-translate-y-1">
                  <div className="mb-2 flex items-center justify-between">
                    <p className="font-semibold text-fg">{domain.label}</p>
                    <span className="font-mono text-[11px] text-fg-muted">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                  </div>
                  <p className="text-sm text-fg-muted">{domain.description}</p>
                </div>
              </StaggerItem>
            ))}
          </Stagger>

          <Reveal className="mt-12">
            <Link href="/signup" className="btn-primary btn-shine px-6 py-3.5">
              Start signup
            </Link>
          </Reveal>
        </div>
      </section>
    </SiteShell>
  )
}
