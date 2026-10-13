import type { Metadata } from 'next'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { Toaster } from '@/components/ui/sonner'
import { SessionProvider } from '@/components/SessionProvider'
import { ThemeProvider } from '@/components/ThemeProvider'
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

/** Apply saved theme before paint to avoid a light/dark flash. Default: light. */
const THEME_BOOT =
  "(function(){try{var t=localStorage.getItem('freedombot-theme');var d=t==='dark';var r=document.documentElement;r.classList.toggle('dark',d);r.style.colorScheme=d?'dark':'light';}catch(e){}})();"

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
        {/* Runtime Google Fonts — avoids next/font fetch during Docker `next build`. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* eslint-disable-next-line @next/next/no-page-custom-font -- Docker builds cannot fetch next/font at build time */}
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="font-geist antialiased">
        <NuqsAdapter>
          <ThemeProvider>
            <SessionProvider>{children}</SessionProvider>
            <Toaster />
          </ThemeProvider>
        </NuqsAdapter>
      </body>
    </html>
  )
}
