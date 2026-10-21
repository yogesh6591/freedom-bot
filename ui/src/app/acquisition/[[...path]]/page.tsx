'use client'

/**
 * Acquisition screens inside the FreedomBot shell. The signed-in FreedomBot
 * session is the only login; these pages do not have their own.
 */

import { AppShell } from '@/components/AppShell'
import { useParams, useSearchParams } from 'next/navigation'

export default function AcquisitionPage() {
  const params = useParams<{ path?: string[] }>()
  const searchParams = useSearchParams()
  const parts = params.path ?? ['overview']
  const query = searchParams.toString()
  const src = `/acquire/${parts.map(encodeURIComponent).join('/')}${query ? `?${query}` : ''}`

  return (
    <AppShell>
      <iframe
        title="Acquisition"
        src={src}
        className="h-full min-h-0 w-full flex-1 border-0 bg-background"
      />
    </AppShell>
  )
}
