import type { NextConfig } from 'next'

const acquisitionProxy = process.env.ACQUISITION_PROXY_URL || 'http://127.0.0.1:8001'

const nextConfig: NextConfig = {
  devIndicators: false,
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
