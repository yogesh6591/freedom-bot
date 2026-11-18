'use client'

import { motion, useReducedMotion } from 'framer-motion'
import { Check } from 'lucide-react'
import { DOMAINS, PLANS, type PlanId } from '@/lib/plans'
import { cn } from '@/lib/utils'
import { signupUrl } from '@/lib/urls'

export function PricingCards({
  selected,
  onSelect
}: {
  selected?: PlanId
  onSelect?: (id: PlanId) => void
}) {
  const reduce = useReducedMotion()

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      {PLANS.map((plan, index) => {
        const isSelected = selected === plan.id
        const domainLabels = DOMAINS.filter((d) =>
          plan.domains.includes(d.id)
        ).map((d) => d.label)

        return (
          <motion.div
            key={plan.id}
            initial={reduce ? false : { opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-8% 0px' }}
            transition={{
              duration: 0.55,
              delay: index * 0.08,
              ease: [0.22, 1, 0.36, 1]
            }}
            whileHover={reduce ? undefined : { y: -6 }}
          >
            <div
              className={cn(
                'panel relative flex h-full flex-col p-6 transition duration-300 sm:p-7',
                plan.highlighted && 'scale-[1.02] border-brand/40 shadow-glow',
                isSelected && 'ring-2 ring-brand'
              )}
            >
              {plan.highlighted && (
                <span className="absolute -top-3 left-6 rounded-full bg-brand px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-white">
                  Most popular
                </span>
              )}
              <p className="text-sm font-semibold text-brand-soft">{plan.name}</p>
              <div className="mt-3 flex items-end gap-1">
                <span className="font-display text-4xl font-semibold tracking-tight text-fg">
                  {plan.priceLabel}
                </span>
                <span className="pb-1 text-sm text-fg-muted">/ month</span>
              </div>
              <p className="mt-3 text-sm leading-6 text-fg-muted">{plan.tagline}</p>

              <ul className="mt-6 space-y-3">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex gap-2.5 text-sm text-fg-soft">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>

              <div className="mt-6 rounded-xl border border-line bg-surface-2 p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
                  Capability packs
                </p>
                <p className="mt-1.5 text-xs leading-5 text-fg-muted">
                  {domainLabels.join(' · ')}
                </p>
              </div>

              <div className="mt-auto pt-7">
                {onSelect ? (
                  <button
                    type="button"
                    onClick={() => onSelect(plan.id)}
                    className={cn(
                      'w-full',
                      plan.highlighted || isSelected
                        ? 'btn-primary'
                        : 'btn-secondary'
                    )}
                  >
                    {isSelected ? 'Selected' : plan.cta}
                  </button>
                ) : (
                  <a
                    href={signupUrl(plan.id)}
                    className={cn(
                      'w-full',
                      plan.highlighted ? 'btn-primary btn-shine' : 'btn-secondary'
                    )}
                  >
                    {plan.cta}
                  </a>
                )}
              </div>
            </div>
          </motion.div>
        )
      })}
    </div>
  )
}
