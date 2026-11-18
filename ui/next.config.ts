import type { NextConfig } from 'next'

const acquisitionProxy = process.env.ACQUISITION_PROXY_URL || 'http://127.0.0.1:8001'
// The marketing website (../Website, its own container) is served here at /website.
const websiteProxy = (process.env.WEBSITE_PROXY_URL || 'http://127.0.0.1:3100').replace(/\/+$/, '')

const nextConfig: NextConfig = {
  devIndicators: false,
  experimental: {
    proxyTimeout: 180000,
  },
  async redirects() {
    // Old sign-in links (/?workspace=acme) open the sign-in page; / itself is now the website.
    return [
      {
        source: '/',
        has: [{ type: 'query', key: 'workspace' }],
        destination: '/login',
        permanent: false
      }
    ]
  },
  async rewrites() {
    return {
      // The website is the front page: / shows its home (its other pages stay under /website).
      beforeFiles: [{ source: '/', destination: `${websiteProxy}/website` }],
      afterFiles: [
      {
        source: '/acquire/:path*',
        destination: `${acquisitionProxy}/acquire/:path*`
      },
      { source: '/website', destination: `${websiteProxy}/website` },
      { source: '/website/:path*', destination: `${websiteProxy}/website/:path*` }
      ],
      fallback: []
    }
  }
}

export default nextConfig
