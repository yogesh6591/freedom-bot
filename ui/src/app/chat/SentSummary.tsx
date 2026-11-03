'use client'

import { useState } from 'react'
import type { SentView } from '@/lib/types'

const COLLAPSED_ROWS = 6

function pluralise(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/** Compact "who was emailed" table. Shows a few rows; the rest scroll inside the card, so a long list never stretches the chat. */
export function SentSummary({ view }: { view: SentView }) {
  const [open, setOpen] = useState(false)
  const rows = view.rows || []
  const long = rows.length > COLLAPSED_ROWS
  const shown = open || !long ? rows : rows.slice(0, COLLAPSED_ROWS)
  const skipped = view.skipped || []
  const when = view.minutes < 1 ? 'under a minute' : `about ${view.minutes} min`

  return (
    <section
      aria-label="Emails queued"
      className="w-full max-w-[36rem] self-start overflow-hidden rounded-2xl border border-[#e6e6e6] bg-white text-[#202124] shadow-[0_1px_2px_rgba(60,64,67,0.15),0_2px_6px_rgba(60,64,67,0.1)]"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3.5">
        <span className="assistant-orb h-5 w-5 shrink-0 rounded-full" aria-hidden />
        <h3 className="text-[15px] font-semibold">
          Queued {pluralise(view.count, 'email')}
        </h3>
        <div className="flex flex-wrap items-center gap-1.5 text-[12px] font-medium">
          {view.first > 0 ? (
            <span className="rounded-full bg-black/[0.06] px-2 py-0.5 text-[#5f6368]">
              {view.first} first
            </span>
          ) : null}
          {view.follow_up > 0 ? (
            <span className="rounded-full bg-[#ece9ff] px-2 py-0.5 text-[#4c3fd6]">
              {view.follow_up} follow-up{view.follow_up === 1 ? '' : 's'}
            </span>
          ) : null}
        </div>
      </header>
      <p className="px-4 pb-2 pt-1 text-[12.5px] text-[#5f6368]">
        Sending one at a time, {when} to finish. Not delivered yet.
      </p>
      <div className={open && long ? 'max-h-72 overflow-y-auto' : ''}>
        <table className="w-full border-collapse text-left text-[13.5px]">
          <thead className="sticky top-0 bg-white">
            <tr className="border-y border-black/10 text-[11px] font-semibold uppercase tracking-wide text-[#5f6368]">
              <th className="px-4 py-1.5 font-semibold">Name</th>
              <th className="py-1.5 pr-2 font-semibold">Email</th>
              <th className="py-1.5 pr-4 text-right font-semibold">Type</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <tr key={row.email} className="border-b border-black/5 last:border-0">
                <td className="max-w-[9rem] truncate px-4 py-1.5 font-medium">{row.name}</td>
                <td className="max-w-[12rem] truncate py-1.5 pr-2 text-[#5f6368]">{row.email}</td>
                <td className="whitespace-nowrap py-1.5 pr-4 text-right text-[12px] text-[#5f6368]">
                  {row.kind === 'follow_up' ? 'Follow-up' : 'First'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {long ? (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="w-full border-t border-black/10 px-4 py-2 text-center text-[13px] font-medium text-[#4c3fd6] hover:bg-black/[0.03]"
        >
          {open ? 'Show less' : `Show all ${rows.length}`}
        </button>
      ) : null}
      {skipped.length > 0 ? (
        <p className="border-t border-black/10 px-4 py-2 text-[12px] text-[#5f6368]">
          {pluralise(skipped.length, 'person')} skipped (opted out, no valid email or emailed recently).
        </p>
      ) : null}
    </section>
  )
}
