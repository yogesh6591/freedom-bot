import { cn } from '@/lib/utils'

const Skeleton = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  return (
    <div
      className={cn('animate-pulse rounded-xl bg-background-elevated', className)}
      {...props}
    />
  )
}

export { Skeleton }
