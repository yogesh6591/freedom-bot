'use client'

/** Small presentational building blocks shared by every section. */

import { cn } from '@/lib/utils'
import type { ReactNode } from 'react'

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
        'rounded-xl border border-border bg-background-secondary/40 p-5',
        className
      )}
    >
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title && (
              <h2 className="font-geist text-sm font-medium uppercase tracking-wide text-primary">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-1 max-w-2xl text-xs text-muted">{description}</p>
            )}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  )
}

const TONES = {
  neutral: 'bg-background-secondary text-muted border-border',
  info: 'bg-sky-500/10 text-sky-300 border-sky-500/30',
  good: 'bg-positive/10 text-positive border-positive/30',
  warn: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  bad: 'bg-destructive/10 text-red-300 border-destructive/30',
  brand: 'bg-brand/10 text-brand border-brand/30'
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
        'inline-flex items-center rounded-md border px-2 py-0.5 font-dmmono text-[10px] uppercase tracking-wide',
        TONES[tone],
        className
      )}
    >
      {children}
    </span>
  )
}

/** Consistent colour semantics for the vocabularies users see everywhere. */
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

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-xs text-muted">
      {children}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-red-300">
      {children}
    </div>
  )
}

export function Field({
  label,
  hint,
  children
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="block space-y-1">
      <span className="font-geist text-xs text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted/70">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-lg border border-border bg-background px-3 py-2 font-geist text-sm text-primary outline-none placeholder:text-muted/60 focus:border-brand/60'

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
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border">
            {columns.map((column) => (
              <th
                key={column.key}
                className="px-3 py-2 font-geist text-[11px] font-medium uppercase tracking-wide text-muted"
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
              className={cn(
                'border-b border-border/40 align-top',
                onRowClick && 'cursor-pointer hover:bg-background-secondary/60'
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn('px-3 py-2 text-xs text-primary', column.className)}
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
