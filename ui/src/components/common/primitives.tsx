'use client'

/** Shared UI primitives — spacing, type, and control consistency (palette unchanged). */

import { cn } from '@/lib/utils'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function PageStack({
  children,
  className
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={cn('flex flex-col gap-6', className)}>{children}</div>
}

export function Panel({
  title,
  description,
  actions,
  children,
  className
}: {
  title?: string
  description?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        'rounded-xl border border-border bg-background-secondary p-5 shadow-panel sm:p-6',
        className
      )}
    >
      {(title || actions) && (
        <header className="mb-5 flex items-start justify-between gap-4">
          <div className="min-w-0 max-w-prose">
            {title && <h2 className="type-subtitle">{title}</h2>}
            {description && <p className="type-muted mt-1.5">{description}</p>}
          </div>
          {actions ? <div className="shrink-0">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  )
}

const TONES = {
  neutral: 'bg-background-elevated text-muted border-border',
  info: 'bg-info/10 text-info border-info/25',
  good: 'bg-positive/10 text-positive border-positive/25',
  warn: 'bg-brand/20 text-brand-soft border-brand/40',
  bad: 'bg-destructive/10 text-destructive border-destructive/25',
  brand: 'bg-brand text-brand-ink border-brand-soft/40'
} as const

export type Tone = keyof typeof TONES

export function Badge({
  children,
  tone = 'neutral',
  className
}: {
  children: ReactNode
  tone?: Tone
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-1 type-caption',
        TONES[tone],
        className
      )}
    >
      {children}
    </span>
  )
}

export function riskTone(risk?: string | null): Tone {
  switch (risk) {
    case 'LOW':
      return 'good'
    case 'MEDIUM':
      return 'info'
    case 'HIGH':
      return 'warn'
    case 'CRITICAL':
      return 'bad'
    default:
      return 'neutral'
  }
}

export function statusTone(status?: string | null): Tone {
  switch (status) {
    case 'COMPLETED':
    case 'APPROVED':
    case 'RESOLVED':
    case 'CONNECTED':
    case 'OK':
      return 'good'
    case 'PENDING_APPROVAL':
    case 'EXECUTING':
    case 'OPEN':
    case 'AWAITING_APPROVAL':
    case 'AWAITING_REVIEW':
      return 'warn'
    case 'REJECTED':
    case 'FAILED':
    case 'CANCELLED':
    case 'DISMISSED':
      return 'bad'
    case 'DRAFT':
      return 'info'
    default:
      return 'neutral'
  }
}

export function modeTone(mode?: string | null): Tone {
  switch (mode) {
    case 'ADVISE':
      return 'info'
    case 'DRAFT':
      return 'neutral'
    case 'WAIT_FOR_APPROVAL':
      return 'warn'
    case 'AUTO_WITHIN_SCOPE':
      return 'brand'
    default:
      return 'neutral'
  }
}

const BTN_BASE =
  'inline-flex items-center justify-center gap-2 rounded-xl font-geist font-semibold ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50'

const BTN_VARIANT = {
  primary: 'bg-brand text-brand-ink shadow-glow hover:bg-brand-soft',
  secondary:
    'border border-border bg-background-elevated text-muted hover:border-brand-soft hover:text-primary',
  ghost: 'text-muted hover:bg-background-elevated hover:text-primary',
  positive: 'bg-positive text-primaryAccent hover:opacity-90',
  danger: 'bg-destructive text-primary hover:opacity-90'
} as const

const BTN_SIZE = {
  sm: 'min-h-9 px-3 text-xs',
  md: 'min-h-11 px-4 text-sm',
  lg: 'min-h-12 px-6 text-sm'
} as const

export type BtnVariant = keyof typeof BTN_VARIANT
export type BtnSize = keyof typeof BTN_SIZE

export function Btn({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant
  size?: BtnSize
}) {
  return (
    <button
      type={type}
      className={cn(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size], className)}
      {...props}
    />
  )
}

export function Chip({
  active,
  children,
  className,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type={type}
      aria-pressed={active}
      className={cn(
        'inline-flex min-h-11 items-center rounded-full px-4 font-geist text-sm font-medium ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/80 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        active
          ? 'bg-brand text-brand-ink shadow-glow'
          : 'bg-background-elevated text-muted hover:text-primary',
        className
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      className="rounded-xl border border-dashed border-border bg-background-elevated/60 px-6 py-16 text-center type-muted"
    >
      {children}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
    >
      {children}
    </div>
  )
}

export function Field({
  label,
  hint,
  children,
  htmlFor
}: {
  label: string
  hint?: string
  children: ReactNode
  htmlFor?: string
}) {
  return (
    <label className="block space-y-2" htmlFor={htmlFor}>
      <span className="font-geist text-sm font-medium text-primary">{label}</span>
      {children}
      {hint && <span className="block text-xs leading-relaxed text-muted">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full min-h-11 rounded-xl border border-border bg-background-elevated px-3.5 py-2.5 font-geist text-sm text-primary outline-none ui-transition placeholder:text-muted/55 focus:border-brand-soft focus:shadow-glow disabled:opacity-50'

export function LoadingBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading"
      className={cn('space-y-3', className)}
    >
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className="h-12 animate-pulse rounded-xl bg-background-elevated"
          style={{ opacity: 1 - index * 0.12 }}
        />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  )
}

export function DataTable<T>({
  rows,
  columns,
  empty,
  onRowClick
}: {
  rows: T[]
  columns: { key: string; header: string; render: (row: T) => ReactNode; className?: string }[]
  empty: string
  onRowClick?: (row: T) => void
}) {
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-background-secondary shadow-panel">
      <table className="w-full min-w-[640px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-background-elevated">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="px-4 py-3 type-caption text-left"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={index}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={
                onRowClick
                  ? (event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault()
                        onRowClick(row)
                      }
                    }
                  : undefined
              }
              tabIndex={onRowClick ? 0 : undefined}
              className={cn(
                'border-b border-border/50 align-top ui-transition last:border-0',
                onRowClick &&
                  'cursor-pointer hover:bg-background-wash/50 focus-visible:bg-background-wash/50 focus-visible:outline-none'
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn('px-4 py-3.5 text-sm text-primary', column.className)}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function timestamp(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
