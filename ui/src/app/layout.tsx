import type { Metadata } from 'next'
import { DM_Mono, Geist } from 'next/font/google'
import { NuqsAdapter } from 'nuqs/adapters/next/app'
import { Toaster } from '@/components/ui/sonner'
import { SessionProvider } from '@/components/SessionProvider'
import './globals.css'

const geistSans = Geist({
  variable: '--font-geist-sans',
  weight: '400',
  subsets: ['latin']
})

const dmMono = DM_Mono({
  subsets: ['latin'],
  variable: '--font-dm-mono',
  weight: '400'
})

export const metadata: Metadata = {
  title: 'Business AI Agent Platform',
  description:
    'Multi-tenant business AI agent workspace with organizational memory, policy-governed execution modes, an approval queue and a full audit trail.'
}

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${dmMono.variable} antialiased`}>
        <NuqsAdapter>
          <SessionProvider>{children}</SessionProvider>
        </NuqsAdapter>
        <Toaster />
      </body>
    </html>
  )
}
