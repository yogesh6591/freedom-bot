import { Layers3 } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Shared mark: one visual identity across sign-in, the shell and the assistant. */
export function BrandMark({
  className,
  inverse = false
}: {
  className?: string
  inverse?: boolean
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-[11px] bg-brand text-white',
        inverse && 'bg-white text-[#176f69]',
        className
      )}
    >
      <Layers3 size={21} strokeWidth={2.1} />
    </span>
  )
}
