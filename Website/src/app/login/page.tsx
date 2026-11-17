import { redirect } from 'next/navigation'
import { APP_URL } from '@/lib/urls'

/** Sign in lives on the main FreedomBot app — not this marketing site. */
export default function LoginPage() {
  redirect(APP_URL)
}
