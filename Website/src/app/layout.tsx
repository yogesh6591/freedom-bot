import type { Metadata } from 'next'
import { Outfit, Fraunces, JetBrains_Mono } from 'next/font/google'
import { ThemeProvider } from '@/components/ThemeProvider'
import './globals.css'

const sans = Outfit({
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap'
})

const display = Fraunces({
  subsets: ['latin'],
  variable: '--font-display',
  display: 'swap'
})

const mono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap'
})

export const metadata: Metadata = {
  title: {
    default: 'FreedomBot — Governed company assistant',
    template: '%s · FreedomBot'
  },
  description:
    'FreedomBot is the approval-first company assistant with organizational memory, capability packs, and multi-tenant workspaces.'
}

const themeBootScript = `
(function () {
  try {
    var key = 'freedombot-theme';
    var alt = 'fb-theme';
    var stored = localStorage.getItem(key) || localStorage.getItem(alt);
    var theme = stored === 'light' || stored === 'dark' ? stored : 'dark';
    var root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    root.style.colorScheme = theme;
  } catch (e) {}
})();
`

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      className={`${sans.variable} ${display.variable} ${mono.variable} dark`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  )
}
