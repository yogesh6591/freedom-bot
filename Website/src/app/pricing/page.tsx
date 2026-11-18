import type { Metadata } from 'next'
import { signupUrl } from '@/lib/urls'
import { SiteShell } from '@/components/SiteShell'
import { PricingCards } from '@/components/PricingCards'
import { PricingExtras } from '@/components/PricingExtras'

export const metadata: Metadata = {
  title: 'Pricing'
}

export default function PricingPage() {
  return (
    <SiteShell>
      <section className="section relative overflow-hidden">
        <div className="container-page relative">
          <div className="mx-auto mb-14 max-w-2xl text-center">
            <p className="eyebrow">Subscription</p>
            <h1 className="mt-3 font-display text-[2.6rem] font-semibold tracking-[-0.05em] text-fg sm:text-6xl">
              Pricing that maps to
              <span className="headline-gradient mt-1 block">domain access</span>
            </h1>
            <p className="lead mx-auto mt-5 text-center">
              Starter covers everyday operations. Growth adds sales and intake.
              Enterprise unlocks the full FreedomBot surface.
            </p>
          </div>
          <PricingCards />
          <PricingExtras />
          <div className="mt-12 text-center">
            <a href={signupUrl()} className="btn-primary btn-shine px-6 py-3.5">
              Start onboarding
            </a>
          </div>
        </div>
      </section>
    </SiteShell>
  )
}
