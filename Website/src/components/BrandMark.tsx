import Link from 'next/link'
import { cn } from '@/lib/utils'
import { RobotMark } from './RobotMark'

export function BrandMark({
  className,
  href = '/',
  showWordmark = true
}: {
  className?: string
  href?: string
  showWordmark?: boolean
}) {
  return (
    <Link href={href} className={cn('inline-flex items-center gap-3', className)}>
      <span
        aria-hidden
        className="inline-flex h-10 w-10 items-center justify-center rounded-[11px] bg-brand text-white shadow-glow"
      >
        <RobotMark size={24} />
      </span>
      {showWordmark && (
        <span className="font-display text-lg font-semibold tracking-[-0.03em] text-fg">
          FreedomBot
        </span>
      )}
    </Link>
  )
}
