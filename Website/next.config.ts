import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Allow LAN access during local testing (e.g. http://192.168.1.21:3100)
  allowedDevOrigins: ['192.168.1.21', 'localhost', '127.0.0.1']
}

export default nextConfig
