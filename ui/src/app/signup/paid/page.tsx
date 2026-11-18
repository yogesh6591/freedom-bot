'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle } from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'

/** Where Stripe sends the customer after paying (or cancelling) in its own tab. The signup tab finishes. */
export default function SignupPaidPage() {
  const [cancelled, setCancelled] = useState<boolean | null>(null)

  useEffect(() => {
    setCancelled(new URLSearchParams(window.location.search).get('cancelled') === '1')
  }, [])

  if (cancelled === null) return null
  return (
    <main className="grid min-h-dvh place-items-center bg-background px-4 text-primary">
      <section className="max-w-md rounded-2xl border border-border bg-background-secondary p-8 text-center shadow-panel">
        <BrandMark className="mx-auto h-10 w-10" />
        {cancelled ? (
          <>
            <XCircle size={36} className="mx-auto mt-5 text-muted" aria-hidden />
            <h1 className="mt-3 font-geist text-xl font-semibold">Payment cancelled</h1>
            <p className="mt-2 text-[14px] text-muted">
              Nothing was charged. Close this tab and go back to the signup tab to try again.
            </p>
          </>
        ) : (
          <>
            <CheckCircle2 size={36} className="mx-auto mt-5 text-success" aria-hidden />
            <h1 className="mt-3 font-geist text-xl font-semibold">Payment received</h1>
            <p className="mt-2 text-[14px] text-muted">
              Close this tab and go back to the signup tab: it is creating your workspace now.
            </p>
          </>
        )}
        <button
          type="button"
          onClick={() => window.close()}
          className="mt-6 text-[13px] font-semibold text-brand hover:underline"
        >
          Close this tab
        </button>
      </section>
    </main>
  )
}
