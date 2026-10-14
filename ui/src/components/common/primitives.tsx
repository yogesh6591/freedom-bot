'use client'

/** Shared workspace primitives — functional components, consistent enterprise styling. */

import { cn } from '@/lib/utils'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function PageStack({
  children,
  className
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col gap-6 pb-9', className)}>{children}</div>
  )
}

export function PageHeader({
  title,
  description,
  eyebrow = 'Workspace',
  actions
}: {
  title: string
  description?: string
  eyebrow?: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4 pb-1">
      <div className="min-w-0">
        <p className="page-eyebrow">{eyebrow}</p>
        <h1 className="page-heading">{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
      )}
    </header>
  )
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
        'rounded-xl border border-border bg-background-secondary p-5 shadow-panel sm:p-6 lg:p-7',
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
  neutral: 'bg-background-elevated text-secondary border-border',
  info: 'bg-info/10 text-info border-info/25',
  good: 'bg-success/10 text-success border-success/25',
  warn: 'bg-warning/15 text-warning border-warning/35',
  bad: 'bg-error/10 text-error border-error/25',
  brand: 'bg-purple-50 text-brand border-purple-200'
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
        'inline-flex min-h-[25px] items-center rounded-md border px-2 py-0.5 font-geist text-[11px] font-semibold leading-4 tracking-[0.005em]',
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
  'inline-flex shrink-0 items-center justify-center gap-2 rounded-lg font-geist font-semibold ui-transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-soft/70 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-45 active:translate-y-px'

const BTN_VARIANT = {
  primary: 'btn-primary-fill border border-transparent',
  secondary:
    'border border-border bg-background-secondary text-primary shadow-sm hover:border-slate-300 hover:bg-background-elevated',
  ghost: 'text-secondary hover:bg-background-elevated hover:text-primary',
  positive: 'bg-success text-white hover:brightness-105',
  danger: 'bg-error text-white hover:brightness-105',
  destructive: 'bg-error text-white hover:brightness-105'
} as const

const BTN_SIZE = {
  sm: 'min-h-10 px-3.5 text-[13px] rounded-lg',
  md: 'min-h-11 px-4 text-[13px] rounded-lg',
  lg: 'min-h-12 px-5 text-sm rounded-lg'
} as const

export type BtnVariant = keyof typeof BTN_VARIANT
export type BtnSize = keyof typeof BTN_SIZE

export function Btn({
  variant = 'primary',
  size = 'md',
  className,
  type = 'button',
  loading = false,
  children,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant
  size?: BtnSize
  loading?: boolean
}) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size], className)}
      {...props}
    >
      {loading ? (
        <>
          <span
            className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"
            aria-hidden
          />
          <span>{children}</span>
        </>
      ) : (
        children
      )}
    </button>
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
      data-active={active ? 'true' : undefined}
      className={cn(
        'segmented-item capitalize',
        !active && 'bg-transparent',
        className
      )}
      {...props}
    >
      {children}
    </button>
  )
}

/** Consistent filter / section tabs used across Approvals, Memory, Admin, Support. */
export function SegmentedTabs({
  'aria-label': ariaLabel,
  children,
  className
}: {
  'aria-label': string
  children: ReactNode
  className?: string
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn('segmented', className)}
    >
      {children}
    </div>
  )
}

export function SegmentedTab({
  active,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-active={active ? 'true' : undefined}
      className={cn('segmented-item', className)}
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
      className="type-muted rounded-xl border border-dashed border-border bg-background-elevated/60 px-6 py-16 text-center"
    >
      {children}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-error/30 bg-error/5 px-4 py-3 text-sm text-error"
    >
      {children}
    </div>
  )
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor
}: {
  label: string
  hint?: string
  error?: string
  children: ReactNode
  htmlFor?: string
}) {
  return (
    <label className="block space-y-2" htmlFor={htmlFor}>
      <span className="font-geist text-sm font-medium text-primary">
        {label}
      </span>
      {children}
      {error ? (
        <span className="block text-xs font-medium text-error" role="alert">
          {error}
        </span>
      ) : hint ? (
        <span className="block text-xs leading-relaxed text-muted">{hint}</span>
      ) : null}
    </label>
  )
}

export const inputClass =
  'w-full min-h-11 rounded-lg border border-border bg-background-secondary px-3.5 py-2.5 font-geist text-sm text-primary outline-none ui-transition placeholder:text-muted/60 focus:border-brand focus:ring-[3px] focus:ring-brand/10 disabled:opacity-50'

export function LoadingBlock({
  rows = 3,
  className
}: {
  rows?: number
  className?: string
}) {
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
          className={cn(
            'h-12 animate-pulse rounded-lg bg-background-elevated',
            index === 1 && 'opacity-80',
            index === 2 && 'opacity-60',
            index >= 3 && 'opacity-45'
          )}
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
  columns: {
    key: string
    header: string
    render: (row: T) => ReactNode
    className?: string
  }[]
  empty: string
  onRowClick?: (row: T) => void
}) {
  if (rows.length === 0) return <EmptyState>{empty}</EmptyState>
  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-background-secondary shadow-panel">
      <table className="w-full min-w-[640px] border-collapse text-left">
        <thead className="sticky top-0 z-[1]">
          <tr className="border-b border-border bg-background-elevated">
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className="whitespace-nowrap px-5 py-3.5 text-left font-geist text-[11px] font-semibold uppercase tracking-[0.075em] text-muted"
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
                'ui-transition border-b border-border/75 align-top last:border-0 odd:bg-background-secondary even:bg-background-elevated/35',
                onRowClick &&
                  'cursor-pointer hover:!bg-purple-50 focus-visible:!bg-purple-50 focus-visible:outline-none dark:hover:!bg-background-elevated dark:focus-visible:!bg-background-elevated'
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn(
                    'px-5 py-4 text-[13px] leading-[1.55] text-primary',
                    column.className
                  )}
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
