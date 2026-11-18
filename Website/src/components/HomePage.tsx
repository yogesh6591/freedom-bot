'use client'

import Link from 'next/link'
import { motion, useReducedMotion } from 'framer-motion'
import {
  ArrowRight,
  BookOpenText,
  CheckCheck,
  MessageSquareText,
  ShieldCheck,
  Sparkles,
  Workflow
} from 'lucide-react'
import { SiteShell } from '@/components/SiteShell'
import { PricingCards } from '@/components/PricingCards'
import {
  AuthHeroBackdrop,
  AuthOrbitStage
} from '@/components/AuthOrbitStage'
import { DomainMarquee } from '@/components/DomainMarquee'
import { FadeIn, Reveal, Stagger, StaggerItem } from '@/components/motion'
import { DOMAINS } from '@/lib/plans'
import { SIGN_IN_URL, signupUrl } from '@/lib/urls'

const CAPABILITIES = [
  {
    icon: MessageSquareText,
    title: 'One conversation across work',
    description:
      'Ask once. FreedomBot routes across memory, CRM, calendar, and finance tools — with a clear explanation of what it did.'
  },
  {
    icon: BookOpenText,
    title: 'Organizational memory',
    description:
      'SOPs, policies, and decisions stay versioned. The assistant answers from what your company actually approved.'
  },
  {
    icon: CheckCheck,
    title: 'Approval before action',
    description:
      'Reads and drafts run automatically. Sends, CRM changes, and anything that spends money wait for a named approver.'
  },
  {
    icon: ShieldCheck,
    title: 'Regulated-data guardrails',
    description:
      'Health data and card data are blocked by default. Policy is enforced in code — not left to prompt instructions.'
  }
]

const STEPS = [
  {
    title: 'Create your account',
    text: 'Sign up with your work email and set the first admin password.'
  },
  {
    title: 'Choose domain access',
    text: 'Select department packs — Sales, Planning, Finance, Legal, and more. Only those unlock in FreedomBot.'
  },
  {
    title: 'Choose a subscription',
    text: 'Starter, Growth, or Enterprise — recommended from the domains you picked.'
  },
  {
    title: 'Pay and open FreedomBot',
    text: 'Complete checkout, then sign into the main app with the same account and limited domain access.'
  }
]

export function HomePage() {
  const reduce = useReducedMotion()

  return (
    <SiteShell>
      <section className="relative min-h-[92vh] overflow-hidden border-b border-line">
        <AuthHeroBackdrop />
        <div aria-hidden className="hero-grain" />

        <div className="container-page relative grid min-h-[92vh] items-center gap-12 py-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-14 lg:py-20">
          <div className="relative z-10 max-w-xl space-y-5">
            <FadeIn delay={0.05}>
              <p className="eyebrow inline-flex items-center gap-2">
                <span
                  className="h-1.5 w-1.5 animate-pulse rounded-full"
                  style={{ background: 'var(--accent)' }}
                />
                FreedomBot
              </p>
            </FadeIn>

            <motion.p
              className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.19em] text-[#5b00e4] dark:text-[#b58cff]"
              initial={reduce ? false : { opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
            >
              <span className="h-1.5 w-1.5 rounded-full bg-[#8740f2]" aria-hidden />
              Intelligence, with accountability
            </motion.p>

            <motion.h1
              className="font-display text-[2.35rem] font-semibold leading-[1.08] tracking-[-0.05em] text-fg sm:text-5xl lg:text-[3.4rem]"
              initial={reduce ? false : { opacity: 0, y: 28 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.85, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}
            >
              The company assistant
              <span className="headline-gradient mt-1 block">
                that waits for permission.
              </span>
            </motion.h1>

            <motion.p
              className="max-w-lg text-lg leading-8 text-fg-muted"
              initial={reduce ? false : { opacity: 0, y: 18 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.75, delay: 0.22, ease: [0.22, 1, 0.36, 1] }}
            >
              Governed memory, department domains, and approval checkpoints —
              so operators move faster without losing control.
            </motion.p>

            <motion.div
              className="flex flex-wrap items-center gap-3 pt-2"
              initial={reduce ? false : { opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.32, ease: [0.22, 1, 0.36, 1] }}
            >
              <a href={signupUrl()} className="btn-primary btn-shine px-6 py-3.5 text-[15px]">
                Start signup
                <ArrowRight size={16} />
              </a>
              <Link href="/pricing" className="btn-secondary px-6 py-3.5 text-[15px]">
                See pricing
              </Link>
            </motion.div>

            <motion.div
              className="flex flex-wrap gap-x-6 gap-y-2 pt-1 text-sm text-fg-muted"
              initial={reduce ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.42, duration: 0.6 }}
            >
              <span>Multi-tenant isolation</span>
              <span>Approval-first actions</span>
              <span>Audit every tool call</span>
            </motion.div>
          </div>

          <motion.div
            className="relative z-10"
            initial={reduce ? false : { opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.9, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
          >
            <AuthOrbitStage />
          </motion.div>
        </div>
      </section>

      <DomainMarquee />

      <section className="section relative">
        <div className="container-page">
          <Reveal>
            <div className="mb-12 max-w-2xl">
              <p className="eyebrow">Why teams switch</p>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-5xl">
                Built like an operator platform — not a demo chatbot.
              </h2>
            </div>
          </Reveal>

          <Stagger className="grid gap-4 sm:grid-cols-2">
            {CAPABILITIES.map((item) => (
              <StaggerItem key={item.title}>
                <article className="group panel tilt-card relative overflow-hidden p-6 sm:p-7">
                  <div
                    aria-hidden
                    className="absolute -right-8 -top-8 h-28 w-28 rounded-full bg-brand/10 blur-2xl transition duration-500 group-hover:bg-brand/25"
                  />
                  <item.icon className="relative mb-5 h-6 w-6 text-brand-soft transition duration-300 group-hover:scale-110" />
                  <h3 className="relative text-xl font-semibold tracking-[-0.02em] text-fg">
                    {item.title}
                  </h3>
                  <p className="relative mt-3 text-sm leading-7 text-fg-muted">
                    {item.description}
                  </p>
                </article>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      <section className="section border-t border-line">
        <div className="container-page">
          <Reveal>
            <div className="mb-10 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="eyebrow">Domain access</p>
                <h2 className="mt-3 font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-5xl">
                  Unlock only the departments you need.
                </h2>
              </div>
              <Link href="/features" className="btn-ghost">
                See all features
                <ArrowRight size={16} />
              </Link>
            </div>
          </Reveal>

          <Stagger className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {DOMAINS.map((domain, index) => (
              <StaggerItem key={domain.id}>
                <div className="panel group relative overflow-hidden p-5 transition duration-300 hover:-translate-y-1">
                  <div className="mb-3 flex items-center justify-between">
                    <p className="text-base font-semibold text-fg">{domain.label}</p>
                    <span className="font-mono text-[11px] text-fg-muted">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                  </div>
                  <p className="text-sm leading-6 text-fg-muted">{domain.description}</p>
                </div>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      <section id="how-it-works" className="section border-t border-line">
        <div className="container-page">
          <Reveal>
            <div className="mb-12 max-w-2xl">
              <p className="eyebrow">Onboarding</p>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-5xl">
                From signup to signed-in workspace.
              </h2>
            </div>
          </Reveal>

          <Stagger className="grid gap-4 lg:grid-cols-4">
            {STEPS.map((step, index) => (
              <StaggerItem key={step.title}>
                <article className="panel relative h-full p-5 sm:p-6">
                  <motion.div
                    className="mb-5 inline-flex h-9 w-9 items-center justify-center rounded-full border border-brand/40 bg-brand/15 text-sm font-semibold text-brand-soft"
                    whileHover={reduce ? undefined : { scale: 1.08 }}
                  >
                    {index + 1}
                  </motion.div>
                  <h3 className="text-lg font-semibold text-fg">{step.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-fg-muted">{step.text}</p>
                </article>
              </StaggerItem>
            ))}
          </Stagger>
        </div>
      </section>

      <section className="section border-t border-line">
        <div className="container-page">
          <Reveal>
            <div className="mb-12 max-w-2xl">
              <p className="eyebrow">Pricing</p>
              <h2 className="mt-3 font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-5xl">
                Three tiers. Clear domain packs.
              </h2>
              <p className="mt-4 text-fg-muted">
                Every plan includes memory, approvals, and an isolated workspace.
                Upgrade when you add sales, intake, strategy, or finance.
              </p>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <PricingCards />
          </Reveal>
        </div>
      </section>

      <section className="section border-t border-line pb-28">
        <div className="container-page">
          <Reveal>
            <div className="cta-banner relative overflow-hidden rounded-[1.75rem] border border-line px-6 py-14 sm:px-12 sm:py-16">
              <div aria-hidden className="cta-orb cta-orb-a" />
              <div aria-hidden className="cta-orb cta-orb-b" />
              <div className="relative max-w-2xl">
                <Sparkles className="mb-4 h-6 w-6 text-brand-soft" />
                <h2 className="font-display text-3xl font-semibold tracking-[-0.04em] text-fg sm:text-5xl">
                  Ready when your operators are.
                </h2>
                <p className="mt-4 text-lg text-fg-muted">
                  Create an account, pick domain access, choose a plan, and open
                  FreedomBot with the same credentials.
                </p>
                <div className="mt-8 flex flex-wrap gap-3">
                  <a href={signupUrl()} className="btn-primary btn-shine px-6 py-3.5">
                    Create account
                    <Workflow size={16} />
                  </a>
                  <a href={SIGN_IN_URL} className="btn-secondary px-6 py-3.5">
                    Sign in
                  </a>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </section>
    </SiteShell>
  )
}
