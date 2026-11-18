'use client'

/**
 * Self-serve onboarding at /signup, the customer's own front door (like /admin is the admin portal):
 * account -> company and workspace ID -> departments -> plan -> payment -> sign-in details.
 *
 * After payment the server makes the workspace, the departments and the first admin (app/api/signup.py,
 * the same as a platform owner's New client form). The password is kept only in this page's memory and
 * the card details never leave the browser (demo checkout).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Copy, CreditCard, Eye, EyeOff, FileText, Info, Lock, Upload, X } from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Btn, ErrorNote, Field, inputClass } from '@/components/common/primitives'
import { api, API_BASE } from '@/lib/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { setAdminUnlocked } from '@/lib/panel'
import { announceSessionChange } from '@/lib/sessionSync'
import {
  DEPARTMENT_GUIDE,
  DEPARTMENTS,
  PLANS,
  getPlan,
  planCovers,
  recommendPlan,
  usd,
  workspaceIdFrom,
  type PlanId
} from '@/lib/signup'

const STEPS = ['Account', 'Company', 'Departments', 'Plan', 'Knowledge', 'Payment'] as const
const PAY_STEP = STEPS.length - 1

/** Files for company memory: the same types and size the Memory page's upload accepts (documents.py). */
const DOC_TYPES = ['.pdf', '.docx', '.txt', '.md']
const DOC_MAX_MB = 10
const DOC_MAX_FILES = 10

/** One chosen file's upload after the workspace is made. */
interface UploadState {
  name: string
  state: 'waiting' | 'uploading' | 'added' | 'failed'
  detail?: string
}
// Non-secret progress survives a refresh; the password never does.
const STORE = 'freedombot:signup'

interface Draft {
  fullName: string
  email: string
  companyName: string
  workspaceId: string
  departments: string[]
  planId: PlanId
}

interface Created {
  workspace: string
  company_name: string
  admin_email: string
  purchased_domains: string[]
}

const EMPTY: Draft = {
  fullName: '',
  email: '',
  companyName: '',
  workspaceId: '',
  departments: ['general'],
  planId: 'starter'
}

function readDraft(): Draft {
  try {
    const raw = sessionStorage.getItem(STORE)
    return raw ? { ...EMPTY, ...JSON.parse(raw) } : EMPTY
  } catch {
    return EMPTY
  }
}

function detail(body: unknown, fallback: string): string {
  const d = (body as { detail?: unknown })?.detail
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).filter(Boolean).join(' ') || fallback
  return typeof d === 'string' && d ? d : fallback
}

export default function SignupPage() {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [password, setPassword] = useState('')
  const [agree, setAgree] = useState(false)
  const [card, setCard] = useState({ name: '', number: '', expiry: '', cvc: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Payment: Stripe's hosted checkout in its own tab when the server has Stripe switched on (as for
  // acquisition's payment links), else the demo card form. This tab keeps the password and the files.
  const [payMode, setPayMode] = useState<'loading' | 'demo' | 'stripe' | 'error'>('loading')
  const [stripeCheckout, setStripeCheckout] = useState<{ url: string; sessionId: string } | null>(null)
  const [stripeOpened, setStripeOpened] = useState(false)
  const provisionRef = useRef<(reference: string) => Promise<void>>(async () => {})
  const [created, setCreated] = useState<Created | null>(null)
  // Company documents picked in the Knowledge step. Kept in this tab only, never in storage.
  const [files, setFiles] = useState<File[]>([])
  const [uploads, setUploads] = useState<UploadState[]>([])
  const [signedIn, setSignedIn] = useState(false)
  // "What each department does": opens by itself the first time the Departments step is reached.
  const [guideOpen, setGuideOpen] = useState(false)
  const [guideShown, setGuideShown] = useState(false)

  useEffect(() => {
    if (step === 2 && !guideShown) {
      setGuideOpen(true)
      setGuideShown(true)
    }
  }, [step, guideShown])
  const [idEdited, setIdEdited] = useState(false)

  useEffect(() => {
    const saved = readDraft()
    // A plan picked on the website's pricing cards arrives as ?plan=growth.
    const wanted = new URLSearchParams(window.location.search).get('plan')
    const picked = PLANS.find((p) => p.id === wanted)
    setDraft(picked ? { ...saved, planId: picked.id } : saved)
    setIdEdited(Boolean(saved.workspaceId && saved.workspaceId !== workspaceIdFrom(saved.companyName)))
  }, [])

  function update(patch: Partial<Draft>) {
    setDraft((current) => {
      const next = { ...current, ...patch }
      try {
        sessionStorage.setItem(STORE, JSON.stringify(next))
      } catch {
        /* storage blocked: progress just isn't kept across a refresh */
      }
      return next
    })
  }

  const plan = getPlan(draft.planId)
  const workspaceId = workspaceIdFrom(draft.workspaceId || draft.companyName)
  const recommended = useMemo(() => recommendPlan(draft.departments), [draft.departments])

  function go(next: number) {
    setError(null)
    setStep(next)
    window.scrollTo({ top: 0 })
  }

  function addFiles(list: FileList | null) {
    setError(null)
    const picked = Array.from(list ?? [])
    const problems: string[] = []
    const ok = picked.filter((f) => {
      const ext = f.name.toLowerCase().slice(f.name.lastIndexOf('.'))
      if (!DOC_TYPES.includes(ext)) problems.push(`${f.name}: only PDF, Word (.docx), .txt or .md files`)
      else if (f.size > DOC_MAX_MB * 1024 * 1024) problems.push(`${f.name}: larger than ${DOC_MAX_MB} MB`)
      else return true
      return false
    })
    setFiles((current) => {
      const names = new Set(current.map((f) => f.name))
      const merged = [...current, ...ok.filter((f) => !names.has(f.name))]
      if (merged.length > DOC_MAX_FILES) problems.push(`Up to ${DOC_MAX_FILES} files; add more later from Memory.`)
      return merged.slice(0, DOC_MAX_FILES)
    })
    if (problems.length) setError(problems.join(' · '))
  }

  /** Sign the new admin in, then add each chosen file to company memory for everyone in the workspace. */
  async function addKnowledge(made: Created) {
    if (!files.length) return
    setUploads(files.map((f) => ({ name: f.name, state: 'waiting' })))
    try {
      await api.post('/api/auth/login', { email: made.admin_email, password, client_slug: made.workspace },
        { anyWorkspace: true })
      setAdminUnlocked(null)
      announceSessionChange()
      setSignedIn(true)
    } catch {
      setUploads(files.map((f) => ({ name: f.name, state: 'failed', detail: 'Sign in and add it from Memory.' })))
      return
    }
    for (const [i, file] of files.entries()) {
      setUploads((u) => u.map((x, j) => (j === i ? { ...x, state: 'uploading' } : x)))
      const form = new FormData()
      form.append('file', file)
      form.append('title', '')
      form.append('access_area', '') // empty: everyone in the workspace can use it
      let result: UploadState = { name: file.name, state: 'added' }
      try {
        const response = await fetch(`${API_BASE}/api/memory/documents`, { method: 'POST', body: form, credentials: 'include' })
        if (!response.ok) {
          const body = await response.json().catch(() => ({}))
          result = { name: file.name, state: 'failed', detail: detail(body, 'It could not be added.') }
        }
      } catch {
        result = { name: file.name, state: 'failed', detail: 'FreedomBot could not be reached.' }
      }
      setUploads((u) => u.map((x, j) => (j === i ? result : x)))
    }
    setFiles([])
  }

  async function next() {
    setError(null)
    if (step === 0) {
      if (!draft.fullName.trim() || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(draft.email.trim())) {
        return setError('Enter your name and a valid work email.')
      }
      if (password.length < 12) return setError('Choose a password of at least 12 characters.')
      if (!agree) return setError('Please accept the Terms and Privacy Policy to continue.')
      update({ email: draft.email.trim().toLowerCase(), fullName: draft.fullName.trim() })
    }
    if (step === 1) {
      if (!draft.companyName.trim()) return setError('Enter your business name.')
      if (workspaceId === 'client') return setError('Choose a more specific workspace ID.')
      setBusy(true)
      try {
        const response = await fetch(`/signup/slug?slug=${encodeURIComponent(workspaceId)}`)
        const body = await response.json().catch(() => ({}))
        if (response.ok && body.available === false) {
          return setError(`The workspace ID "${workspaceId}" is already taken. Choose another.`)
        }
        if (response.status === 503) return setError(detail(body, 'Signup is not switched on yet.'))
      } catch {
        /* not reachable now: payment checks again before anything is made */
      } finally {
        setBusy(false)
      }
      update({ workspaceId })
    }
    if (step === 2) {
      if (!planCovers(draft.planId, draft.departments)) update({ planId: recommended })
    }
    if (step === 3 && !planCovers(draft.planId, draft.departments)) {
      return setError(`${plan.name} doesn't include every department you picked. Choose ${getPlan(recommended).name} or higher.`)
    }
    go(step + 1)
  }

  /** Create the workspace for a payment ("demo", or a Stripe Checkout Session the server checks). */
  async function provision(reference: string) {
    setError(null)
    setBusy(true)
    try {
      const response = await fetch('/signup/provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...draft, workspaceId, password, paymentReference: reference })
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok || !body.workspace) {
        setError(detail(body, 'Your workspace could not be created. Try again.'))
        if (response.status === 409 && reference === 'demo') go(1)
        return
      }
      setCreated(body as Created)
      setCard({ name: '', number: '', expiry: '', cvc: '' })
      void addKnowledge(body as Created)
      try {
        sessionStorage.removeItem(STORE)
      } catch {
        /* nothing kept */
      }
    } catch {
      setError('FreedomBot could not be reached. Try again in a minute.')
    } finally {
      setBusy(false)
    }
  }
  provisionRef.current = provision

  /** Demo checkout (no Stripe on the server): the card details are checked for shape and never sent anywhere. */
  async function pay() {
    setError(null)
    const digits = card.number.replace(/\D/g, '')
    if (!card.name.trim() || digits.length < 12 || !/^\d{1,2}\s*\/\s*\d{2,4}$/.test(card.expiry) || !/^\d{3,4}$/.test(card.cvc)) {
      return setError('Enter the card details to continue.')
    }
    await provision('demo')
  }

  // On the Payment step, ask the server how to pay: a Stripe Checkout Session for this order, or the demo.
  useEffect(() => {
    if (step !== PAY_STEP || created) return
    let cancelled = false
    setPayMode('loading')
    setStripeCheckout(null)
    setStripeOpened(false)
    void (async () => {
      try {
        const response = await fetch('/signup/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...draft, workspaceId })
        })
        const body = await response.json().catch(() => ({}))
        if (cancelled) return
        if (!response.ok) {
          setError(detail(body, 'Payment could not be started. Try again in a minute.'))
          return setPayMode('error')
        }
        if (body.mode !== 'stripe' || !body.url) return setPayMode('demo')
        setStripeCheckout({ url: body.url as string, sessionId: body.session_id as string })
        setPayMode('stripe')
      } catch {
        if (!cancelled) {
          setError('Payment could not be started. Try again in a minute.')
          setPayMode('error')
        }
      }
    })()
    return () => {
      cancelled = true
    }
    // A new session for each visit to the Payment step; the order can only change on the earlier steps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, created])

  // After Stripe's tab is opened, ask every few seconds whether it was paid; then make the workspace here.
  useEffect(() => {
    if (!stripeOpened || !stripeCheckout || created) return
    let stopped = false
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`/signup/checkout/${encodeURIComponent(stripeCheckout.sessionId)}`)
        const body = await response.json().catch(() => ({}))
        if (stopped || !response.ok) return
        if (body.paid) {
          stopped = true
          window.clearInterval(timer)
          await provisionRef.current(stripeCheckout.sessionId)
        } else if (body.expired) {
          stopped = true
          window.clearInterval(timer)
          setError('That payment page expired. Go back a step and continue to start a new one.')
          setStripeOpened(false)
        }
      } catch {
        /* not reachable this time: ask again */
      }
    }, 3000)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [stripeOpened, stripeCheckout, created])

  return (
    <div className="min-h-dvh bg-background text-primary">
      <header className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-5 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <BrandMark className="h-9 w-9" />
          <span className="font-geist text-[17px] font-semibold">FreedomBot</span>
        </Link>
        <div className="flex items-center gap-3 text-[13px]">
          <span className="hidden text-muted sm:inline">Already have a workspace?</span>
          <Link href="/login" className="font-semibold text-brand hover:underline">
            Sign in
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 pb-16 sm:px-6">
        {created ? (
          <Done created={created} password={password} departments={draft.departments} planId={draft.planId}
            uploads={uploads} signedIn={signedIn} />
        ) : (
          <>
            <ol className="mb-6 flex flex-wrap gap-2" aria-label="Steps">
              {STEPS.map((label, i) => (
                <li
                  key={label}
                  aria-current={i === step ? 'step' : undefined}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12px] font-medium ${
                    i === step
                      ? 'border-brand bg-brand/10 text-primary'
                      : i < step
                        ? 'border-border text-secondary'
                        : 'border-border text-muted'
                  }`}
                >
                  {i < step ? <Check size={12} aria-hidden /> : <span>{i + 1}</span>}
                  {label}
                </li>
              ))}
            </ol>

            <section className="rounded-2xl border border-border bg-background-secondary p-5 shadow-panel sm:p-8">
              {error && (
                <div className="mb-5">
                  <ErrorNote>{error}</ErrorNote>
                </div>
              )}

              {step === 0 && (
                <div className="grid max-w-lg gap-4">
                  <Heading title="Create your account" text="You'll be the admin of your company's FreedomBot workspace." />
                  <Field label="Full name">
                    <input className={inputClass} value={draft.fullName} autoComplete="name"
                      onChange={(e) => update({ fullName: e.target.value })} />
                  </Field>
                  <Field label="Work email">
                    <input className={inputClass} type="email" value={draft.email} autoComplete="email"
                      onChange={(e) => update({ email: e.target.value })} />
                  </Field>
                  <Field label="Password" hint="At least 12 characters. You'll sign in with this.">
                    <input className={inputClass} type="password" value={password} autoComplete="new-password"
                      onChange={(e) => setPassword(e.target.value)} />
                  </Field>
                  <label className="flex items-start gap-2.5 text-[13px] text-secondary">
                    <input type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
                    I agree to the Terms of Service and the Privacy Policy.
                  </label>
                </div>
              )}

              {step === 1 && (
                <div className="grid max-w-lg gap-4">
                  <Heading title="Your business" text="Your workspace ID comes from the business name. You sign in with it." />
                  <Field label="Business name">
                    <input className={inputClass} value={draft.companyName} autoComplete="organization"
                      onChange={(e) => update({
                        companyName: e.target.value,
                        ...(idEdited ? {} : { workspaceId: workspaceIdFrom(e.target.value) })
                      })} />
                  </Field>
                  <Field label="Workspace ID" hint={`Letters, numbers and _ only. It will be: ${workspaceId}`}>
                    <input className={`${inputClass} font-mono`} value={draft.workspaceId}
                      onChange={(e) => {
                        setIdEdited(true)
                        update({ workspaceId: e.target.value })
                      }} />
                  </Field>
                </div>
              )}

              {step === 2 && (
                <div className="grid gap-4">
                  <div className="flex flex-wrap items-end justify-between gap-3">
                    <Heading title="Choose your departments" text="Your team can use FreedomBot in these departments. General is always included." />
                    <Btn variant="ghost" onClick={() => setGuideOpen(true)}>
                      <Info size={15} aria-hidden /> What each department does
                    </Btn>
                  </div>
                  <DepartmentGuideDialog open={guideOpen} onClose={() => setGuideOpen(false)} picked={draft.departments} />
                  <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
                    {DEPARTMENTS.map((d) => {
                      const on = draft.departments.includes(d.id)
                      const fixed = d.id === 'general'
                      return (
                        <button
                          key={d.id}
                          type="button"
                          aria-pressed={on}
                          disabled={fixed}
                          onClick={() => update({
                            departments: on ? draft.departments.filter((x) => x !== d.id) : [...draft.departments, d.id]
                          })}
                          className={`rounded-xl border p-3.5 text-left ui-transition ${
                            on ? 'border-brand bg-brand/10' : 'border-border hover:border-brand/50'
                          } ${fixed ? 'cursor-default' : ''}`}
                        >
                          <span className="flex items-center justify-between gap-2 text-[14px] font-semibold">
                            {d.label}
                            {on && <Check size={15} className="text-brand" aria-hidden />}
                          </span>
                          <span className="mt-1 block text-[12px] leading-5 text-muted">{d.description}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {step === 3 && (
                <div className="grid gap-4">
                  <Heading title="Choose a plan" text={`For the departments you picked, ${getPlan(recommended).name} is the smallest plan that covers them.`} />
                  <div className="grid gap-3 md:grid-cols-3">
                    {PLANS.map((p) => {
                      const covers = planCovers(p.id, draft.departments)
                      const chosen = draft.planId === p.id
                      return (
                        <button
                          key={p.id}
                          type="button"
                          disabled={!covers}
                          aria-pressed={chosen}
                          onClick={() => update({ planId: p.id })}
                          className={`rounded-xl border p-4 text-left ui-transition disabled:opacity-45 ${
                            chosen ? 'border-brand bg-brand/10' : 'border-border hover:border-brand/50'
                          }`}
                        >
                          <span className="flex items-center justify-between text-[15px] font-semibold">
                            {p.name}
                            {p.id === recommended && <span className="rounded-full bg-brand px-2 py-0.5 text-[10px] text-white">Recommended</span>}
                          </span>
                          <span className="mt-1 block text-[12px] text-muted">{p.tagline}</span>
                          <span className="mt-3 block font-geist text-2xl font-semibold">
                            {usd(p.priceMonthly)}<span className="text-[12px] font-normal text-muted"> / month</span>
                          </span>
                          <ul className="mt-3 space-y-1 text-[12px] text-secondary">
                            {p.features.map((f) => (
                              <li key={f} className="flex gap-1.5"><Check size={12} className="mt-0.5 text-brand" aria-hidden />{f}</li>
                            ))}
                          </ul>
                          {!covers && <span className="mt-2 block text-[11px] text-muted">Doesn&apos;t include all your departments</span>}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}

              {step === 4 && (
                <div className="grid gap-4">
                  <Heading
                    title="Add your company knowledge"
                    text="Optional. Upload handbooks, policies, price lists or FAQs. They go into company memory, so everyone in your workspace gets answers from them."
                  />
                  <label
                    className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-background px-4 py-8 text-center ui-transition hover:border-brand/60"
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault()
                      addFiles(e.dataTransfer.files)
                    }}
                  >
                    <Upload size={22} className="text-brand" aria-hidden />
                    <span className="text-[14px] font-semibold text-primary">Choose files or drop them here</span>
                    <span className="text-[12px] text-muted">
                      PDF, Word (.docx), .txt or .md · up to {DOC_MAX_MB} MB each · up to {DOC_MAX_FILES} files
                    </span>
                    <input
                      type="file"
                      multiple
                      accept={DOC_TYPES.join(',')}
                      className="sr-only"
                      onChange={(e) => {
                        addFiles(e.target.files)
                        e.target.value = ''
                      }}
                    />
                  </label>
                  {files.length > 0 && (
                    <ul className="grid gap-2">
                      {files.map((f) => (
                        <li key={f.name} className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2 text-[13px]">
                          <span className="flex min-w-0 items-center gap-2">
                            <FileText size={15} className="shrink-0 text-brand" aria-hidden />
                            <span className="truncate">{f.name}</span>
                            <span className="shrink-0 text-[11px] text-muted">{Math.max(1, Math.round(f.size / 1024))} KB</span>
                          </span>
                          <button type="button" aria-label={`Remove ${f.name}`} className="text-muted hover:text-primary"
                            onClick={() => setFiles((list) => list.filter((x) => x !== f))}>
                            <X size={15} />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="text-[12px] text-muted">
                    Files are added right after your workspace is created. You can add more any time from Memory.
                  </p>
                </div>
              )}

              {step === PAY_STEP && (
                <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
                  <div className="grid gap-4">
                    <Heading title="Review and pay" text="Your workspace is created as soon as the payment goes through." />
                    {payMode === 'loading' && <p className="text-[13px] text-muted">Preparing secure payment…</p>}
                    {payMode === 'stripe' && stripeCheckout && (
                      <div className="grid gap-3 rounded-xl border border-border bg-background p-5 text-[13px]">
                        <p className="flex items-center gap-2 text-muted">
                          <Lock size={13} className="text-brand" aria-hidden /> Secure payment by Stripe. Your card details go to Stripe only.
                        </p>
                        {busy ? (
                          <p className="font-medium text-primary">Payment received. Creating your workspace…</p>
                        ) : stripeOpened ? (
                          <>
                            <p className="font-medium text-primary">Finish paying in the Stripe tab.</p>
                            <p className="text-muted">
                              Keep this tab open. Your workspace is created here as soon as the payment goes through.
                            </p>
                            <a href={stripeCheckout.url} target="_blank" rel="noopener noreferrer"
                              className="font-semibold text-brand hover:underline">
                              Reopen the payment page
                            </a>
                          </>
                        ) : (
                          <p className="text-muted">
                            Stripe&apos;s payment page opens in a new tab. Keep this tab open: it finishes setting up your workspace.
                          </p>
                        )}
                      </div>
                    )}
                    {payMode === 'demo' && (
                    <>
                    <p className="flex items-center gap-2 text-[12px] text-muted">
                      <Lock size={13} className="text-brand" aria-hidden /> Demo checkout: no real charge is made.
                    </p>
                    <Field label="Name on card">
                      <input className={inputClass} value={card.name} autoComplete="cc-name"
                        onChange={(e) => setCard({ ...card, name: e.target.value })} />
                    </Field>
                    <Field label="Card number">
                      <div className="relative">
                        <input className={`${inputClass} pr-10`} inputMode="numeric" autoComplete="cc-number"
                          placeholder="4242 4242 4242 4242" value={card.number}
                          onChange={(e) => setCard({ ...card, number: e.target.value })} />
                        <CreditCard size={16} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted" aria-hidden />
                      </div>
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Expiry">
                        <input className={inputClass} placeholder="MM/YY" autoComplete="cc-exp" value={card.expiry}
                          onChange={(e) => setCard({ ...card, expiry: e.target.value })} />
                      </Field>
                      <Field label="CVC">
                        <input className={inputClass} inputMode="numeric" autoComplete="cc-csc" placeholder="123" value={card.cvc}
                          onChange={(e) => setCard({ ...card, cvc: e.target.value })} />
                      </Field>
                    </div>
                    </>
                    )}
                  </div>
                  <aside className="h-fit rounded-xl border border-border bg-background p-5 text-[13px]">
                    <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Order summary</p>
                    <p className="mt-2 text-[17px] font-semibold">{plan.name}</p>
                    <p className="font-geist text-2xl font-semibold">
                      {usd(plan.priceMonthly)}<span className="text-[12px] font-normal text-muted"> / month</span>
                    </p>
                    <dl className="mt-4 space-y-2 border-t border-border pt-4">
                      <Row label="Business" value={draft.companyName} />
                      <Row label="Workspace ID" value={workspaceId} mono />
                      <Row label="Admin" value={draft.email} />
                    </dl>
                    <p className="mt-4 text-[11px] font-semibold uppercase tracking-wider text-muted">Departments</p>
                    <p className="mt-1 text-secondary">
                      {DEPARTMENTS.filter((d) => draft.departments.includes(d.id)).map((d) => d.label).join(' · ')}
                    </p>
                    {files.length > 0 && (
                      <>
                        <p className="mt-4 text-[11px] font-semibold uppercase tracking-wider text-muted">Company knowledge</p>
                        <p className="mt-1 text-secondary">{files.length} {files.length === 1 ? 'file' : 'files'} to add</p>
                      </>
                    )}
                  </aside>
                </div>
              )}

              <div className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-5">
                {step > 0 ? (
                  <Btn variant="ghost" onClick={() => go(step - 1)} disabled={busy}>
                    <ArrowLeft size={15} aria-hidden /> Back
                  </Btn>
                ) : (
                  <span />
                )}
                {step < PAY_STEP ? (
                  <Btn onClick={() => void next()} loading={busy}>
                    {step === 4 && files.length === 0 ? 'Skip for now' : 'Continue'} <ArrowRight size={15} aria-hidden />
                  </Btn>
                ) : payMode === 'stripe' && stripeCheckout && !stripeOpened ? (
                  <Btn
                    onClick={() => {
                      window.open(stripeCheckout.url, '_blank', 'noopener')
                      setStripeOpened(true)
                    }}
                  >
                    Pay {usd(plan.priceMonthly)}/mo with Stripe <ArrowRight size={15} aria-hidden />
                  </Btn>
                ) : payMode !== 'demo' ? (
                  <span />
                ) : (
                  <Btn onClick={() => void pay()} loading={busy}>
                    Pay {usd(plan.priceMonthly)}/mo and create workspace
                  </Btn>
                )}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

/** One dialog explaining every department: what the team usually does and how FreedomBot helps. */
function DepartmentGuideDialog({ open, onClose, picked }: { open: boolean; onClose: () => void; picked: string[] }) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>What each department does</DialogTitle>
          <DialogDescription className="text-muted">
            Pick the departments your team works in.
          </DialogDescription>
        </DialogHeader>
        <ul className="mt-2 grid gap-3 sm:grid-cols-2">
          {DEPARTMENTS.map((d) => {
            const points = DEPARTMENT_GUIDE[d.id]
            if (!points) return null
            return (
              <li key={d.id} className="rounded-xl border border-border bg-background p-4 text-[13px]">
                <p className="flex items-center justify-between gap-2 text-[15px] font-semibold text-primary">
                  {d.label}
                  {picked.includes(d.id) && (
                    <span className="rounded-full bg-brand/15 px-2 py-0.5 text-[11px] font-medium text-brand">Picked</span>
                  )}
                </p>
                <ul className="mt-2 grid list-disc gap-1 pl-4 text-secondary marker:text-brand">
                  {points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
              </li>
            )
          })}
        </ul>
      </DialogContent>
    </Dialog>
  )
}

function Heading({ title, text }: { title: string; text: string }) {
  return (
    <div>
      <h1 className="font-geist text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
      <p className="mt-1.5 text-[14px] text-muted">{text}</p>
    </div>
  )
}

function Row({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted">{label}</dt>
      <dd className={`text-right ${mono ? 'font-mono' : ''}`}>{value}</dd>
    </div>
  )
}

/** The sign-in details, shown once. The password is not kept anywhere after this page. */
function Done({
  created,
  password,
  departments,
  planId,
  uploads,
  signedIn
}: {
  created: Created
  password: string
  departments: string[]
  planId: PlanId
  uploads: UploadState[]
  signedIn: boolean
}) {
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)

  // Sign the new admin in with the details just made, so an older session in this browser (another
  // account) is replaced instead of reopened. If that fails, sign the old one out and show the sign-in.
  async function openWorkspace() {
    setOpening(true)
    // Adding the company files already signed the new admin in.
    if (signedIn) return window.location.assign('/chat')
    try {
      await api.post(
        '/api/auth/login',
        { email: created.admin_email, password, client_slug: created.workspace },
        { anyWorkspace: true }
      )
      setAdminUnlocked(null)
      announceSessionChange()
      window.location.assign('/chat')
    } catch {
      try {
        await api.post('/api/auth/logout', undefined, { anyWorkspace: true })
        announceSessionChange()
      } catch {
        /* no session to end */
      }
      window.location.assign(`/login?workspace=${encodeURIComponent(created.workspace)}`)
    }
  }
  const rows = [
    { label: 'Workspace ID', value: created.workspace },
    { label: 'Admin email', value: created.admin_email },
    { label: 'Password', value: password, secret: true }
  ]

  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(label)
      setTimeout(() => setCopied(null), 1600)
    } catch {
      /* clipboard blocked: the value is on screen */
    }
  }

  return (
    <section className="mx-auto max-w-2xl rounded-2xl border border-border bg-background-secondary p-6 shadow-panel sm:p-10">
      <CheckCircle2 size={40} className="text-success" aria-hidden />
      <h1 className="mt-4 font-geist text-2xl font-semibold tracking-tight sm:text-3xl">
        {created.company_name} is ready
      </h1>
      <p className="mt-2 text-[14px] text-muted">
        Payment received for the {getPlan(planId).name} plan. Your workspace and admin account are created with the
        departments you chose.
      </p>

      <div className="mt-6 rounded-xl border border-border bg-background p-5 text-[14px]">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Your sign-in details</p>
        <div className="mt-3 grid gap-3">
          {rows.map((row) => (
            <div key={row.label} className="flex items-center justify-between gap-3">
              <span className="text-muted">{row.label}</span>
              <span className="flex items-center gap-2 font-mono">
                {row.secret && !reveal ? '••••••••••••' : row.value}
                {row.secret && (
                  <button type="button" aria-label={reveal ? 'Hide password' : 'Show password'}
                    onClick={() => setReveal((v) => !v)} className="text-muted hover:text-primary">
                    {reveal ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                )}
                <button type="button" aria-label={`Copy ${row.label}`} onClick={() => void copy(row.label, row.value)}
                  className="text-muted hover:text-primary">
                  <Copy size={14} />
                </button>
                {copied === row.label && <span className="text-[11px] text-success">Copied</span>}
              </span>
            </div>
          ))}
        </div>
        <p className="mt-4 text-[12px] leading-5 text-muted">
          Save the password now: it is shown only here and is not stored in your browser.
        </p>
      </div>

      <p className="mt-4 text-[13px] text-secondary">
        Departments: {DEPARTMENTS.filter((d) => departments.includes(d.id)).map((d) => d.label).join(' · ')}
      </p>

      {uploads.length > 0 && (
        <div className="mt-5 rounded-xl border border-border bg-background p-5 text-[13px]">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted">Company knowledge</p>
          <ul className="mt-3 grid gap-2">
            {uploads.map((u) => (
              <li key={u.name} className="flex items-start justify-between gap-3">
                <span className="flex min-w-0 items-center gap-2">
                  <FileText size={14} className="shrink-0 text-brand" aria-hidden />
                  <span className="truncate">{u.name}</span>
                </span>
                <span className={`shrink-0 text-right text-[12px] ${u.state === 'added' ? 'text-success' : u.state === 'failed' ? 'text-error' : 'text-muted'}`}>
                  {u.state === 'added' ? 'Added to memory' : u.state === 'failed' ? `Not added: ${u.detail ?? ''}` : u.state === 'uploading' ? 'Adding…' : 'Waiting'}
                </span>
              </li>
            ))}
          </ul>
          {uploads.some((u) => u.state === 'failed') && (
            <p className="mt-3 text-[12px] text-muted">You can add these later from Memory.</p>
          )}
        </div>
      )}

      <Btn className="mt-6" disabled={opening || uploads.some((u) => u.state === 'waiting' || u.state === 'uploading')}
        onClick={() => void openWorkspace()}>
        {opening ? 'Opening FreedomBot…' : 'Open FreedomBot'} <ArrowRight size={15} aria-hidden />
      </Btn>
    </section>
  )
}
