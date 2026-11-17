'use client'

import { motion, useReducedMotion } from 'framer-motion'
import { CheckCheck, Layers3, Shield } from 'lucide-react'
import { useEffect, useState } from 'react'

const MESSAGES = [
  {
    role: 'you' as const,
    text: 'Draft outreach for stalled deals this week, then queue for approval.'
  },
  {
    role: 'bot' as const,
    text: 'Prepared 4 drafts from sales memory. Nothing was sent — waiting in Approvals.'
  }
]

export function HeroShowcase() {
  const reduce = useReducedMotion()
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (reduce) {
      setStep(2)
      return
    }
    const timers = [
      window.setTimeout(() => setStep(1), 700),
      window.setTimeout(() => setStep(2), 1800),
      window.setTimeout(() => setStep(3), 2800)
    ]
    return () => timers.forEach(clearTimeout)
  }, [reduce])

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 40, rotateX: 8 }}
      animate={{ opacity: 1, y: 0, rotateX: 0 }}
      transition={{ duration: 1, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="relative mx-auto w-full max-w-5xl perspective-[1200px]"
    >
      <div
        aria-hidden
        className="absolute -inset-8 rounded-[2rem] bg-[radial-gradient(circle_at_50%_0%,rgba(109,124,255,0.28),transparent_55%)] blur-2xl"
      />
      <div className="hero-stage relative overflow-hidden rounded-[1.6rem] border border-white/12 bg-[#0a0d16]/90 shadow-[0_40px_120px_-40px_rgba(0,0,0,0.9)] backdrop-blur-xl">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-[9px] bg-brand text-fg">
              <Layers3 size={16} />
            </span>
            <div>
              <p className="text-sm font-semibold text-fg">FreedomBot</p>
              <p className="text-[11px] text-fg-muted">Operator workspace</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className="status-pulse inline-flex items-center gap-1.5 rounded-full border border-accent/25 bg-accent/10 px-2.5 py-1 text-[11px] font-semibold text-accent-soft">
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              Live
            </span>
          </div>
        </div>

        <div className="grid lg:grid-cols-[200px_1fr]">
          <aside className="hidden border-r border-line p-4 lg:block">
            {['Assistant', 'Memory', 'Approvals', 'Integrations'].map((item, i) => (
              <motion.div
                key={item}
                initial={reduce ? false : { opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.55 + i * 0.08 }}
                className={`mb-1 rounded-lg px-3 py-2 text-sm ${
                  i === 0
                    ? 'bg-white/8 text-fg'
                    : 'text-fg-muted'
                }`}
              >
                {item}
              </motion.div>
            ))}
          </aside>

          <div className="space-y-3 p-5 sm:p-6">
            {MESSAGES.map((msg, index) => {
              const visible = step > index
              return (
                <motion.div
                  key={msg.role}
                  initial={false}
                  animate={
                    visible
                      ? { opacity: 1, y: 0, scale: 1 }
                      : { opacity: 0, y: 12, scale: 0.98 }
                  }
                  transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                  className={
                    msg.role === 'you'
                      ? 'rounded-2xl border border-line bg-surface-2 p-4'
                      : 'rounded-2xl border border-brand/30 bg-gradient-to-br from-brand/20 to-brand/5 p-4'
                  }
                >
                  <p
                    className={`text-[11px] font-semibold uppercase tracking-[0.14em] ${
                      msg.role === 'you' ? 'text-fg-muted' : 'text-brand-soft'
                    }`}
                  >
                    {msg.role === 'you' ? 'You' : 'FreedomBot'}
                  </p>
                  <p className="mt-1.5 text-sm leading-6 text-fg">{msg.text}</p>
                </motion.div>
              )
            })}

            <motion.div
              initial={false}
              animate={
                step >= 3
                  ? { opacity: 1, y: 0 }
                  : { opacity: 0, y: 10 }
              }
              className="grid gap-2 sm:grid-cols-3"
            >
              {[
                { icon: CheckCheck, label: 'Queued for approval' },
                { icon: Shield, label: 'Policy checked' },
                { icon: Layers3, label: 'Memory cited' }
              ].map((item) => (
                <div
                  key={item.label}
                  className="flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-3 py-3 text-xs text-fg-muted"
                >
                  <item.icon size={14} className="text-accent shrink-0" />
                  {item.label}
                </div>
              ))}
            </motion.div>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
