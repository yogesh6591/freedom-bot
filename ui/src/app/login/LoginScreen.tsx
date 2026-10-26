'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { useRouter } from 'next/navigation'
import {
  ArrowRight,
  BookOpenText,
  CheckCheck,
  Eye,
  EyeOff,
  MessageSquareText,
  ShieldCheck
} from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import { adminUnlocked, setAdminUnlocked } from '@/lib/panel'
import { announceSessionChange, takeNotice } from '@/lib/sessionSync'
import { BrandMark } from '@/components/BrandMark'
import {
  Btn,
  ErrorNote,
  Field,
  inputClass
} from '@/components/common/primitives'
import { ThemeToggle } from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

const CAPABILITIES = [
  {
    icon: MessageSquareText,
    title: 'Ask with context',
    description: 'One conversation across your work.'
  },
  {
    icon: BookOpenText,
    title: 'Use trusted knowledge',
    description: 'Company memory, with version history.'
  },
  {
    icon: CheckCheck,
    title: 'Keep control',
    description: 'Approval checkpoints before actions.'
  }
] as const

function signInError(err: unknown): string {
  if (err instanceof ApiError && err.status >= 500) {
    return 'Sign in is unavailable right now. Try again in a moment.'
  }
  if (err instanceof TypeError) {
    return 'Could not reach FreedomBot. Check your connection and try again.'
  }
  return 'Could not sign in. Check your email and password.'
}

type FormProps = {
  email: string
  password: string
  clientSlug: string
  showPassword: boolean
  busy: boolean
  error: string | null
  notice: string | null
  submitLabel: string
  idPrefix: string
  setEmail: (value: string) => void
  setPassword: (value: string) => void
  setClientSlug: (value: string) => void
  setShowPassword: (value: boolean | ((current: boolean) => boolean)) => void
  onSubmit: (event: FormEvent) => void
  appearance?: 'home' | 'admin'
}

function SignInForm({
  email,
  password,
  clientSlug,
  showPassword,
  busy,
  error,
  notice,
  submitLabel,
  idPrefix,
  setEmail,
  setPassword,
  setClientSlug,
  setShowPassword,
  onSubmit,
  appearance = 'home'
}: FormProps) {
  const admin = appearance === 'admin'
  const fieldClass = admin
    ? 'w-full min-h-12 rounded-xl border border-border bg-background px-4 py-3 font-geist text-[14px] text-primary outline-none transition placeholder:text-muted focus:border-brand focus:ring-[3px] focus:ring-brand/20'
    : inputClass
  return (
    <>
      {notice && (
        <div
          role="status"
          className="mt-6 rounded-lg border border-border bg-background-elevated p-3.5 text-[13px] leading-6 text-secondary"
        >
          {notice}
        </div>
      )}
      {error && (
        <div className="mt-6">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
      <form onSubmit={onSubmit} className={admin ? 'mt-7 space-y-4' : 'mt-8 space-y-5'}>
        <Field label="Work email" htmlFor={`${idPrefix}-email`}>
          <input
            id={`${idPrefix}-email`}
            className={fieldClass}
            type="email"
            inputMode="email"
            autoCapitalize="off"
            spellCheck={false}
            autoComplete="username"
            placeholder="name@company.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </Field>
        <div className="block space-y-2">
          <label
            htmlFor={`${idPrefix}-password`}
            className="block font-geist text-sm font-medium text-primary"
          >
            Password
          </label>
          <span className="relative block">
            <input
              id={`${idPrefix}-password`}
              className={`${fieldClass} pr-12`}
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              placeholder="Enter your password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            <button
              type="button"
              onClick={() => setShowPassword((value) => !value)}
              title={showPassword ? 'Hide password' : 'Show password'}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              aria-pressed={showPassword}
              className="absolute inset-y-0 right-1 my-auto flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-background-elevated hover:text-primary"
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </span>
        </div>
        <details className={`group ${admin ? 'rounded-xl border border-border bg-background' : 'rounded-lg border border-border bg-background-elevated/35'}`}>
          <summary className="cursor-pointer rounded-xl px-3.5 py-3 text-[12px] font-medium text-secondary marker:text-muted hover:text-primary focus-visible:outline-brand">
            Signing in to a different workspace?
          </summary>
          <div className="border-t border-border px-3.5 py-3.5">
            <Field
              label="Workspace identifier (optional)"
              htmlFor={`${idPrefix}-workspace`}
              hint="Use this if your email belongs to more than one company."
            >
              <input
                id={`${idPrefix}-workspace`}
                className={fieldClass}
                placeholder="e.g. acme"
                autoComplete="off"
                value={clientSlug}
                onChange={(event) => setClientSlug(event.target.value)}
              />
            </Field>
          </div>
        </details>
        <Btn
          type="submit"
          loading={busy}
          size="lg"
          className={
            admin
              ? 'mt-2 w-full justify-center rounded-xl border-0 bg-gradient-to-r from-violet-600 via-indigo-500 to-sky-500 text-white shadow-[0_12px_28px_-12px_rgba(79,70,229,0.9)] hover:brightness-105'
              : 'mt-1 w-full justify-center'
          }
        >
          {busy ? 'Signing in…' : submitLabel}
          {!busy && <ArrowRight size={17} aria-hidden />}
        </Btn>
      </form>
    </>
  )
}

/** Sign-in for the home chat (``/``) or, with ``admin``, for the admin panel (``/admin``). */
export function LoginScreen({ admin = false }: { admin?: boolean }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [clientSlug, setClientSlug] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNoticeText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const { refresh, me } = useSession()

  // Already signed in: home goes straight to the chat; the admin panel only once unlocked here.
  useEffect(() => {
    if (!me) return
    if (!admin) router.replace('/chat')
    else if (adminUnlocked(me.user.id)) router.replace('/admin/chat')
  }, [me, admin, router])

  const adminPanel = admin

  useEffect(() => {
    setNoticeText(takeNotice())
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session = await api.post<{ user?: { id?: string } }>('/api/auth/login', {
        email,
        password,
        client_slug: clientSlug || undefined
      })
      await refresh()
      // Other open tabs must stop showing the previous workspace (M01-06).
      announceSessionChange()
      const fresh = await api.get<{ user: { id: string } }>('/api/auth/me', { anyWorkspace: true })
      setAdminUnlocked(adminPanel ? fresh.user.id || session.user?.id || null : null)
      router.replace(adminPanel ? '/admin/chat' : '/chat')
    } catch (err) {
      setError(signInError(err))
    } finally {
      setBusy(false)
    }
  }

  const form = (
    <SignInForm
      email={email}
      password={password}
      clientSlug={clientSlug}
      showPassword={showPassword}
      busy={busy}
      error={error}
      notice={notice}
      submitLabel={adminPanel ? 'Continue' : 'Sign in to workspace'}
      idPrefix={adminPanel ? 'admin-login' : 'login'}
      setEmail={setEmail}
      setPassword={setPassword}
      setClientSlug={setClientSlug}
      setShowPassword={setShowPassword}
      onSubmit={submit}
      appearance={adminPanel ? 'admin' : 'home'}
    />
  )

  if (adminPanel) {
    return (
      <div className="fb-admin-stage relative flex min-h-dvh flex-col text-primary">
        <div aria-hidden className="fb-admin-grid pointer-events-none absolute inset-0" />
        <header className="relative z-10 flex h-16 shrink-0 items-center justify-between border-b border-border bg-background-secondary/80 px-5 backdrop-blur-md sm:px-8">
          <div className="flex min-w-0 items-center gap-3">
            <BrandMark className="h-9 w-9" />
            <div className="min-w-0">
              <p className="truncate text-[14px] font-semibold tracking-[-0.03em] text-primary">
                FreedomBot
              </p>
              <p className="text-[11px] text-muted">Administration</p>
            </div>
          </div>
          <ThemeToggle />
        </header>

        <main className="relative z-10 flex flex-1 items-center justify-center px-5 py-12 sm:px-8">
          <div className="w-full max-w-[440px] overflow-hidden rounded-2xl border border-border bg-background-secondary shadow-lift">
            <div className="h-1 w-full bg-gradient-to-r from-violet-600 via-indigo-500 to-sky-500" aria-hidden />
            <div className="p-7 sm:p-8">
              <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-brand">
                Administration
              </p>
              <h1 className="font-[family-name:var(--font-display)] text-[28px] font-semibold tracking-[-0.04em] text-primary">
                Sign in
              </h1>
              <p className="mt-2 text-[14px] leading-6 text-muted">
                This account opens the full workspace: assistant, memory, approvals, and acquisition.
              </p>
              {form}
              <p className="mt-8 text-[12px] leading-5 text-muted">
                Need only the assistant? Use the home sign-in instead.
              </p>
            </div>
          </div>
        </main>

        <footer className="relative z-10 shrink-0 px-5 py-4 text-center text-[11px] text-muted sm:px-8">
          © {new Date().getFullYear()} FreedomBot
        </footer>
      </div>
    )
  }

  return (
    <div className="grid min-h-dvh bg-background lg:grid-cols-[minmax(0,1.1fr)_minmax(380px,0.9fr)]">
      <section className="relative hidden min-h-dvh flex-col justify-between overflow-hidden border-r border-border px-10 py-11 lg:flex xl:px-14">
        <div aria-hidden className="fb-auth-hero-glow pointer-events-none absolute inset-[-20%]" />
        <div aria-hidden className="fb-auth-hero-glow-b pointer-events-none absolute left-[-10%] top-[35%] h-[55%] w-[70%]" />
        <div aria-hidden className="fb-auth-mesh pointer-events-none absolute inset-0" />

        <div className="relative z-10">
          <div className="mb-6 flex items-center gap-3">
            <BrandMark className="h-11 w-11" />
            <div>
              <p className="font-[family-name:var(--font-display)] text-[19px] font-semibold tracking-[-0.04em] text-primary">
                FreedomBot
              </p>
              <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#7dd3fc]">
                Business intelligence workspace
              </p>
            </div>
          </div>
          <p className="mb-4 inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.19em] text-[#7dd3fc]">
            <span className="h-1.5 w-1.5 rounded-full bg-[#38bdf8]" aria-hidden />
            Intelligence, with accountability
          </p>
          <h1 className="max-w-[16ch] font-[family-name:var(--font-display)] text-[clamp(32px,3vw,44px)] font-semibold leading-[1.2] tracking-[-0.03em] text-primary">
            Good decisions start with the full picture.
          </h1>
          <p className="mt-4 max-w-[40ch] text-[15px] leading-relaxed text-secondary">
            Bring company knowledge, everyday questions, and decisions into one
            carefully governed workspace.
          </p>
        </div>

        <div className="relative z-10 my-8 grid min-h-[220px] flex-1 place-items-center">
          <div aria-hidden className="fb-auth-ring absolute aspect-square w-[min(78%,340px)] rounded-full border border-[#7dd3fc]/20 shadow-[inset_0_0_40px_rgba(56,189,248,0.06)]">
            <span className="absolute left-1/2 top-[8%] h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[#38bdf8] shadow-[0_0_16px_rgba(56,189,248,0.85)]" />
          </div>
          <div aria-hidden className="fb-auth-ring-b absolute aspect-square w-[min(58%,250px)] rounded-full border border-[#818cf8]/20">
            <span className="absolute bottom-[10%] left-1/2 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[#818cf8] shadow-[0_0_16px_rgba(129,140,248,0.8)]" />
          </div>
          <div className="relative z-10 grid h-[78px] w-[78px] place-items-center rounded-[22px] bg-gradient-to-br from-[#0ea5e9] to-[#4f46e5] text-2xl font-bold text-white shadow-[0_18px_50px_rgba(14,165,233,0.35)]">
            <span className="fb-auth-pulse absolute -inset-2.5 rounded-[28px] border border-[#38bdf8]/45" />
            F
          </div>
          {CAPABILITIES.map(({ title, description }, index) => (
            <div
              key={title}
              className={cn(
                'fb-auth-float absolute z-10 max-w-[170px] rounded-xl border border-white/10 bg-[#080c16]/55 px-3 py-2 backdrop-blur',
                index === 0 && 'left-0 top-[8%]',
                index === 1 && 'right-0 top-[42%]',
                index === 2 && 'bottom-[6%] left-[12%]'
              )}
              style={{ animationDelay: `${index * 0.4}s` }}
            >
              <p className="text-[12px] font-semibold uppercase tracking-wide text-[#e2e8f0]">
                {title}
              </p>
              <p className="mt-0.5 text-[12px] leading-snug text-secondary">{description}</p>
            </div>
          ))}
        </div>

        <div className="relative z-10 flex items-center gap-2 text-[11px] uppercase tracking-[0.06em] text-muted">
          <ShieldCheck size={15} aria-hidden /> Workspace-scoped access and
          human approval controls
        </div>
      </section>

      <section className="flex min-h-0 flex-1 flex-col bg-background-secondary px-6 pb-8 pt-5 sm:px-10 lg:px-[clamp(36px,5vw,100px)] lg:pb-10 lg:pt-9">
        <div className="flex justify-end">
          <ThemeToggle />
        </div>
        <div className="mx-auto flex w-full max-w-[430px] flex-1 flex-col justify-center py-9 lg:py-12">
          <p className="page-eyebrow">Secure access</p>
          <h2 className="mt-1 text-[34px] font-semibold tracking-[-0.05em] text-primary sm:text-[39px]">
            Welcome back.
          </h2>
          <p className="mt-2 text-[14px] leading-6 text-muted">
            Sign in to continue to your workspace.
          </p>
          {form}
          <div className="mt-9 flex items-start gap-2.5 rounded-lg border border-border bg-background-elevated/40 px-3.5 py-3.5">
            <ShieldCheck size={17} className="mt-0.5 shrink-0 text-brand" aria-hidden />
            <p className="text-[12px] leading-5 text-muted">
              Your workspace determines which information you can see and what
              actions you can take.
            </p>
          </div>
        </div>
        <p className="mx-auto w-full max-w-[430px] border-t border-border pt-5 text-[11px] text-muted">
          © {new Date().getFullYear()} FreedomBot · Authorized access only
        </p>
      </section>
    </div>
  )
}
