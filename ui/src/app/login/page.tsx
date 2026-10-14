'use client'

import { useEffect, useState } from 'react'
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
import { api } from '@/lib/api'
import { useSession } from '@/components/SessionProvider'
import { announceSessionChange, takeNotice } from '@/lib/sessionSync'
import { BrandMark } from '@/components/BrandMark'
import {
  Btn,
  ErrorNote,
  Field,
  inputClass
} from '@/components/common/primitives'
import { ThemeToggle } from '@/components/ThemeToggle'

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

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [clientSlug, setClientSlug] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNoticeText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const router = useRouter()
  const { refresh } = useSession()

  useEffect(() => {
    setNoticeText(takeNotice())
  }, [])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/api/auth/login', {
        email,
        password,
        client_slug: clientSlug || undefined
      })
      await refresh()
      // Other open tabs must stop showing the previous workspace (M01-06).
      announceSessionChange()
      router.replace('/chat')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background lg:flex-row">
      <section className="relative flex min-h-[250px] flex-col justify-between overflow-hidden bg-[#152d32] px-6 pb-8 pt-7 text-white sm:px-10 lg:min-h-dvh lg:w-[50%] lg:px-[clamp(40px,6vw,110px)] lg:pb-12 lg:pt-11 xl:w-[52%]">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 bottom-[-220px] h-[630px] w-[630px] rounded-full border border-[#47716e]/25"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-16 bottom-[-155px] h-[500px] w-[500px] rounded-full border border-[#47716e]/25"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -right-0 bottom-[-92px] h-[370px] w-[370px] rounded-full border border-[#47716e]/25"
        />
        <div className="relative z-10 flex items-center gap-3">
          <BrandMark inverse className="h-11 w-11" />
          <div>
            <p className="text-[19px] font-semibold tracking-[-0.04em]">
              FreedomBot
            </p>
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-[#8fb1b1]">
              Business intelligence workspace
            </p>
          </div>
        </div>

        <div className="relative z-10 mb-2 mt-11 max-w-[610px] lg:my-auto lg:py-16">
          <p className="mb-6 inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.19em] text-[#9fd9ca]">
            <span
              className="h-1.5 w-1.5 rounded-full bg-[#7ad0b8]"
              aria-hidden
            />
            Intelligence, with accountability
          </p>
          <h1 className="max-w-[570px] text-[37px] font-semibold leading-[1.14] tracking-[-0.055em] sm:text-[46px] lg:text-[clamp(42px,4.2vw,65px)]">
            Good decisions start with the full picture.
          </h1>
          <p className="mt-6 max-w-[460px] text-[14px] leading-[1.9] text-[#b9cfd0] sm:text-[15px]">
            Bring company knowledge, everyday questions, and decisions into one
            carefully governed workspace.
          </p>

          <div className="mt-11 hidden space-y-5 border-t border-white/15 pt-8 lg:block">
            {CAPABILITIES.map(({ icon: Icon, title, description }, index) => (
              <div key={title} className="flex items-start gap-4">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-white/15 bg-white/[0.065] text-[#a3e3d5]">
                  <Icon size={19} strokeWidth={1.7} />
                </div>
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-[#f4fbfa]">
                    {title}
                  </p>
                  <p className="mt-1 text-[12px] leading-5 text-[#9fb8bb]">
                    {description}
                  </p>
                </div>
                <span className="ml-auto hidden text-[10px] font-medium text-[#648b90] xl:block">
                  0{index + 1}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 hidden items-center gap-2 text-[11px] text-[#96b3b5] lg:flex">
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

          <form onSubmit={submit} className="mt-8 space-y-5" noValidate>
            <Field label="Work email" htmlFor="login-email">
              <input
                id="login-email"
                className={inputClass}
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
                htmlFor="login-password"
                className="block font-geist text-sm font-medium text-primary"
              >
                Password
              </label>
              <span className="relative block">
                <input
                  id="login-password"
                  className={`${inputClass} pr-12`}
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

            <details className="group rounded-lg border border-border bg-background-elevated/35">
              <summary className="cursor-pointer rounded-lg px-3.5 py-3 text-[12px] font-medium text-secondary marker:text-muted hover:text-primary focus-visible:outline-brand">
                Signing in to a different workspace?
              </summary>
              <div className="border-t border-border px-3.5 py-3.5">
                <Field
                  label="Workspace identifier (optional)"
                  htmlFor="login-workspace"
                  hint="Use this if your email belongs to more than one company."
                >
                  <input
                    id="login-workspace"
                    className={inputClass}
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
              className="mt-1 w-full justify-center"
            >
              {busy ? 'Signing in…' : 'Sign in to workspace'}
              {!busy && <ArrowRight size={17} aria-hidden />}
            </Btn>
          </form>
          <div className="mt-9 flex items-start gap-2.5 rounded-lg border border-border bg-background-elevated/40 px-3.5 py-3.5">
            <ShieldCheck
              size={17}
              className="mt-0.5 shrink-0 text-brand"
              aria-hidden
            />
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
