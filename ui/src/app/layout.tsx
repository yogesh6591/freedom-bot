import type { Metadata } from 'next'
import { JetBrains_Mono, Space_Grotesk } from 'next/font/google'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { Toaster } from '@/components/ui/sonner'
import { SessionProvider } from '@/components/SessionProvider'
import './globals.css'

const ui = Space_Grotesk({
  variable: '--font-ui',
  subsets: ['latin'],
  weight: ['400', '500', '600', '700']
})

const mono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  weight: ['400', '500']
})

export const metadata: Metadata = {
  title: 'FreedomBot',
  description:
    'Company AI workspace — memory, approvals, and policy-governed actions in one place.'
}

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${ui.variable} ${mono.variable} font-geist antialiased`}>
        <NuqsAdapter>
          <SessionProvider>{children}</SessionProvider>
        </NuqsAdapter>
        <Toaster />
      </body>
    </html>
  )
}
