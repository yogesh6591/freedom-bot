'use client'

import { ShieldCheck } from 'lucide-react'
import { cn } from '@/lib/utils'

const CAPABILITIES = [
  {
    title: 'Ask with context',
    description: 'One conversation across your work.'
  },
  {
    title: 'Use trusted knowledge',
    description: 'Company memory, with version history.'
  },
  {
    title: 'Keep control',
    description: 'Approval checkpoints before actions.'
  }
] as const

/** Same orbit / mesh stage as FreedomBot sign-in left panel. */
export function AuthOrbitStage({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'relative grid min-h-[300px] w-full place-items-center sm:min-h-[360px]',
        className
      )}
    >
      <div
        aria-hidden
        className="fb-auth-ring absolute aspect-square w-[min(88%,360px)] rounded-full border border-[#b58cff]/35 shadow-[inset_0_0_40px_rgba(135,64,242,0.08)]"
      >
        <span className="absolute left-1/2 top-[8%] h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[#a773ff] shadow-[0_0_16px_rgba(167,115,255,0.85)]" />
      </div>
      <div
        aria-hidden
        className="fb-auth-ring-b absolute aspect-square w-[min(64%,260px)] rounded-full border border-[#8740f2]/35"
      >
        <span className="absolute bottom-[10%] left-1/2 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[#8740f2] shadow-[0_0_16px_rgba(135,64,242,0.8)]" />
      </div>

      <div className="relative z-10 grid h-[78px] w-[78px] place-items-center rounded-[22px] bg-gradient-to-br from-[#8740f2] to-[#5b00e4] text-2xl font-bold text-white shadow-[0_18px_50px_rgba(91,0,228,0.4)]">
        <span className="fb-auth-pulse absolute -inset-2.5 rounded-[28px] border border-[#a773ff]/45" />
        F
      </div>

      {CAPABILITIES.map(({ title, description }, index) => (
        <div
          key={title}
          className={cn(
            'fb-auth-float absolute z-10 max-w-[170px] rounded-xl border px-3 py-2 backdrop-blur',
            index === 0 && 'left-0 top-[6%] sm:left-[2%]',
            index === 1 && 'right-0 top-[40%] sm:right-[2%]',
            index === 2 && 'bottom-[4%] left-[8%] sm:left-[10%]'
          )}
          style={{ animationDelay: `${index * 0.4}s` }}
        >
          <p className="text-[12px] font-semibold uppercase tracking-wide text-fg">
            {title}
          </p>
          <p className="mt-0.5 text-[12px] leading-snug text-fg-muted">
            {description}
          </p>
        </div>
      ))}
    </div>
  )
}

export function AuthHeroBackdrop() {
  return (
    <>
      <div aria-hidden className="fb-auth-hero-glow pointer-events-none absolute inset-[-20%]" />
      <div
        aria-hidden
        className="fb-auth-hero-glow-b pointer-events-none absolute left-[-10%] top-[20%] h-[60%] w-[75%]"
      />
      <div aria-hidden className="fb-auth-mesh pointer-events-none absolute inset-0" />
    </>
  )
}

export function AuthTrustLine() {
  return (
    <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.06em] text-fg-muted">
      <ShieldCheck size={15} aria-hidden />
      Workspace-scoped access and human approval controls
    </div>
  )
}
