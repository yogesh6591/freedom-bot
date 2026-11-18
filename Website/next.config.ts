import type { NextConfig } from 'next'

// The main app serves this site at /website (its next.config rewrites /website/* here), so every page and
// asset lives under that path. Sign in and onboarding are the main app's own pages.
const appUrl = (process.env.NEXT_PUBLIC_APP_URL || 'https://freedombot-core.cloud.cepoch.com').replace(/\/+$/, '')

const nextConfig: NextConfig = {
  reactStrictMode: true,
  basePath: '/website',
  // Allow LAN access during local testing (e.g. http://192.168.1.21:3100)
  allowedDevOrigins: ['192.168.1.21', 'localhost', '127.0.0.1'],
  async redirects() {
    // The website's own signup and onboarding moved to the main app's /signup.
    return [
      { source: '/signup', destination: `${appUrl}/signup`, permanent: false },
      { source: '/onboarding/:path*', destination: `${appUrl}/signup`, permanent: false },
      { source: '/login', destination: `${appUrl}/login`, permanent: false }
    ]
  }
}

export default nextConfig
