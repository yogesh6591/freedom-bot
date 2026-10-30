import type { NextConfig } from 'next'

const acquisitionProxy = process.env.ACQUISITION_PROXY_URL || 'http://127.0.0.1:8001'

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
      }
    ]
  }
}

export default nextConfig
