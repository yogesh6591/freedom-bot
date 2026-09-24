'use client'

/** Shared UI primitives — light / yellow product theme. */

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
        'rounded-xl border border-border bg-background-secondary p-5 shadow-panel',
        className
      )}
    >
      {(title || actions) && (
        <header className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title && (
              <h2 className="font-geist text-lg font-semibold tracking-tight text-primary">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted">
                {description}
              </p>
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
  neutral: 'bg-background-elevated text-muted border-border',
  info: 'bg-info/10 text-info border-info/25',
  good: 'bg-positive/10 text-positive border-positive/25',
  warn: 'bg-brand/25 text-brand-ink border-brand/50',
  bad: 'bg-destructive/10 text-destructive border-destructive/25',
  brand: 'bg-brand text-brand-ink border-brand-deep/40'
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
        'inline-flex items-center rounded-full border px-2.5 py-0.5 font-dmmono text-[10px] font-medium uppercase tracking-wider',
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

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-background-elevated/60 px-4 py-14 text-center text-sm leading-relaxed text-muted">
      {children}
    </div>
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-sm text-destructive">
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
    <label className="block space-y-1.5">
      <span className="font-geist text-sm font-medium text-primary">{label}</span>
      {children}
      {hint && <span className="block text-xs leading-relaxed text-muted">{hint}</span>}
    </label>
  )
}

export const inputClass =
  'w-full rounded-xl border border-border bg-white px-3.5 py-2.5 font-geist text-sm text-primary outline-none transition placeholder:text-muted/55 focus:border-brand-deep focus:shadow-glow'

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
    <div className="overflow-x-auto rounded-xl border border-border bg-white shadow-panel">
      <table className="w-full min-w-[640px] border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-background-elevated">
            {columns.map((column) => (
              <th
                key={column.key}
                className="px-3.5 py-3 font-geist text-[11px] font-semibold uppercase tracking-wider text-muted"
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
                'border-b border-border/50 align-top transition last:border-0',
                onRowClick && 'cursor-pointer hover:bg-background-wash/50'
              )}
            >
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={cn('px-3.5 py-3 text-sm text-primary', column.className)}
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
