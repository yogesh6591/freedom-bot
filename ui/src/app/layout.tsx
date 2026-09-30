import type { Metadata } from 'next'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { Toaster } from '@/components/ui/sonner'
import { SessionProvider } from '@/components/SessionProvider'
import './globals.css'

/**
 * Fonts load via CSS @import in globals.css (runtime), not next/font/google.
 * Docker builds often cannot reach Google Fonts during `next build`, which
 * made next/font throw "Cannot read properties of null".
 */

export const metadata: Metadata = {
  title: 'FreedomBot',
  description:
    'One company AI — ask in chat. Routing, memory, and approvals stay behind the scenes.'
}

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        {/* Runtime Google Fonts — avoids next/font fetch during Docker `next build`. */}
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="font-geist antialiased">
        <NuqsAdapter>
          <SessionProvider>{children}</SessionProvider>
        </NuqsAdapter>
        <Toaster />
      </body>
    </html>
  )
}
