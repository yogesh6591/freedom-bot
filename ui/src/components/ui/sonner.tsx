'use client'

import { Toaster as Sonner } from 'sonner'
import { useTheme } from '@/components/ThemeProvider'

type ToasterProps = React.ComponentProps<typeof Sonner>

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme } = useTheme()
  return (
    <Sonner
      theme={theme}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            'group toast group-[.toaster]:bg-background-secondary group-[.toaster]:text-primary group-[.toaster]:border-border group-[.toaster]:shadow-lg',
          description: 'group-[.toast]:text-muted',
          actionButton:
            'group-[.toast]:bg-brand group-[.toast]:text-brand-ink',
          cancelButton:
            'group-[.toast]:bg-background-elevated group-[.toast]:text-muted'
        }
      }}
      {...props}
    />
  )
}

export { Toaster }
