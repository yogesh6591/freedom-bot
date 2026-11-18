import type { NextConfig } from 'next'

const acquisitionProxy = process.env.ACQUISITION_PROXY_URL || 'http://127.0.0.1:8001'
// The marketing website (../Website, its own container) is served here at /website.
const websiteProxy = (process.env.WEBSITE_PROXY_URL || 'http://127.0.0.1:3100').replace(/\/+$/, '')

const nextConfig: NextConfig = {
  devIndicators: false,
  experimental: {
    proxyTimeout: 180000,
  },
  async rewrites() {
    return [
      {
        source: '/acquire/:path*',
        destination: `${acquisitionProxy}/acquire/:path*`
      },
      { source: '/website', destination: `${websiteProxy}/website` },
      { source: '/website/:path*', destination: `${websiteProxy}/website/:path*` }
    ]
  }
}

export default nextConfig
