import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-[13px] font-semibold ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/65 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 active:translate-y-px',
  {
    variants: {
      variant: {
        default: 'btn-primary-fill',
        destructive: 'bg-error text-white hover:brightness-105',
        outline:
          'border border-border bg-background-secondary text-primary shadow-sm hover:border-slate-300 hover:bg-background-elevated',
        secondary:
          'border border-border bg-background-elevated text-primary hover:bg-background-wash',
        ghost: 'text-secondary hover:bg-background-elevated hover:text-primary',
        link: 'text-brand underline-offset-4 hover:underline'
      },
      size: {
        default: 'h-11 px-4 py-2',
        sm: 'h-10 rounded-lg px-3 text-[13px]',
        lg: 'h-12 rounded-lg px-8',
        icon: 'h-11 w-11'
      }
    },
    defaultVariants: {
      variant: 'default',
      size: 'default'
    }
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
