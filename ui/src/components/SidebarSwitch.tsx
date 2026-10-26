'use client'

import type { LucideIcon } from 'lucide-react'

/** A small on/off row for the sidebar footer (Debug, Acquisition pages). */
export function SidebarSwitch({
  label,
  icon: Icon,
  on,
  onChange,
  title
}: {
  label: string
  icon: LucideIcon
  on: boolean
  onChange: (next: boolean) => void
  title: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => onChange(!on)}
      title={title}
      className="mb-2 flex w-full items-center gap-2 rounded-lg border border-border bg-background-elevated px-3 py-2 text-[12px] font-semibold text-secondary hover:text-primary"
    >
      <Icon size={14} aria-hidden />
      <span className="flex-1 text-left">{label}</span>
      <span
        className={`relative inline-flex h-4 w-7 items-center rounded-full transition-colors ${on ? 'bg-brand' : 'bg-border'}`}
        aria-hidden
      >
        <span
          className={`inline-block h-3 w-3 rounded-full bg-white transition-transform ${on ? 'translate-x-3.5' : 'translate-x-0.5'}`}
        />
      </span>
    </button>
  )
}
