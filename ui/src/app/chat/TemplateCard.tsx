'use client'

import type { TemplateView } from '@/lib/types'

/** The saved email template as a card, in the same style as the other chat cards. Blanks stay visible as {{name}}. */
export function TemplateCard({ view }: { view: TemplateView }) {
  return (
    <section
      aria-label="Saved email template"
      className="w-full max-w-[36rem] self-start overflow-hidden rounded-2xl border border-black/10 bg-white text-primary shadow-sm"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pt-3.5">
        <span className="assistant-orb h-5 w-5 shrink-0 rounded-full" aria-hidden />
        <h3 className="text-[15px] font-semibold">{view.saved ? 'Template saved' : 'Saved email template'}</h3>
        <span className="rounded-full bg-black/[0.06] px-2 py-0.5 text-[12px] font-medium text-secondary">
          {view.sending_on ? 'Sends without review' : 'Review card first'}
        </span>
      </header>
      <div className="space-y-3 px-4 pb-4 pt-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-secondary">Subject</p>
          <p className="mt-0.5 break-words text-[14.5px] font-medium">{view.subject}</p>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-secondary">First email</p>
          <p className="mt-0.5 whitespace-pre-wrap break-words text-[14px] leading-6">{view.body}</p>
        </div>
        {view.followup ? (
          <div className="border-t border-black/10 pt-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-secondary">Follow-up</p>
            <p className="mt-0.5 whitespace-pre-wrap break-words text-[14px] leading-6">{view.followup}</p>
          </div>
        ) : null}
      </div>
    </section>
  )
}
