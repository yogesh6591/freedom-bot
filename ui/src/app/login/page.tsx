'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { LoginScreen } from '@/app/login/LoginScreen'

/** Home sign-in (the website at / links here); signing in opens /chat. ?panel=full is the old admin link. */
export default function LoginPage() {
  const router = useRouter()
  const [admin, setAdmin] = useState<boolean | null>(null)
  useEffect(() => {
    const full = new URLSearchParams(window.location.search).get('panel') === 'full'
    if (full) router.replace('/admin')
    setAdmin(full)
  }, [router])
  return admin === false ? <LoginScreen /> : null
}
