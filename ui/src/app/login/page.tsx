'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'

/** Old sign-in address: the home sign-in is now /, the admin one /admin. */
export default function LoginRedirect() {
  const router = useRouter()
  useEffect(() => {
    const admin = new URLSearchParams(window.location.search).get('panel') === 'full'
    router.replace(admin ? '/admin' : '/')
  }, [router])
  return null
}
