'use client'

/**
 * Self-serve onboarding at /signup, the customer's own front door (like /admin is the admin portal):
 * account -> company and workspace ID -> departments -> plan -> payment -> sign-in details.
 *
 * After payment the server makes the workspace, the departments and the first admin (app/api/signup.py,
 * the same as a platform owner's New client form). The password is kept only in this page's memory and
 * the card details never leave the browser (demo checkout).
 */

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, ArrowRight, Check, CheckCircle2, Copy, CreditCard, Eye, EyeOff, Lock } from 'lucide-react'
import { BrandMark } from '@/components/BrandMark'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Btn, ErrorNote, Field, inputClass } from '@/components/common/primitives'
import {
  DEPARTMENTS,
  PLANS,
  getPlan,
  planCovers,
  recommendPlan,
  usd,
  workspaceIdFrom,
  type PlanId
} from '@/lib/signup'

const STEPS = ['Account', 'Company', 'Departments', 'Plan', 'Payment'] as const
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
  const [created, setCreated] = useState<Created | null>(null)
  const [idEdited, setIdEdited] = useState(false)

  useEffect(() => {
    const saved = readDraft()
    setDraft(saved)
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

  async function pay() {
    setError(null)
    const digits = card.number.replace(/\D/g, '')
    if (!card.name.trim() || digits.length < 12 || !/^\d{1,2}\s*\/\s*\d{2,4}$/.test(card.expiry) || !/^\d{3,4}$/.test(card.cvc)) {
      return setError('Enter the card details to continue.')
    }
    setBusy(true)
    // Demo checkout: replace with Stripe Checkout / Billing. Card details are never sent anywhere.
    await new Promise((resolve) => setTimeout(resolve, 700))
    try {
      const response = await fetch('/signup/provision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...draft, workspaceId, password, paymentReference: 'demo' })
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok || !body.workspace) {
        setError(detail(body, 'Your workspace could not be created. Try again.'))
        if (response.status === 409) go(1)
        return
      }
      setCreated(body as Created)
      setCard({ name: '', number: '', expiry: '', cvc: '' })
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

  return (
    <div className="min-h-dvh bg-background text-primary">
      <header className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-4 py-5 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <BrandMark className="h-9 w-9" />
          <span className="font-geist text-[17px] font-semibold">FreedomBot</span>
        </Link>
        <div className="flex items-center gap-3 text-[13px]">
          <span className="hidden text-muted sm:inline">Already have a workspace?</span>
          <Link href="/" className="font-semibold text-brand hover:underline">
            Sign in
          </Link>
          <ThemeToggle />
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 pb-16 sm:px-6">
        {created ? (
          <Done created={created} password={password} departments={draft.departments} planId={draft.planId} />
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
                  <Heading title="Choose your departments" text="Your team can use FreedomBot in these departments. General is always included." />
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
                <div className="grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
                  <div className="grid gap-4">
                    <Heading title="Review and pay" text="Your workspace is created as soon as the payment goes through." />
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
                {step < 4 ? (
                  <Btn onClick={() => void next()} loading={busy}>
                    Continue <ArrowRight size={15} aria-hidden />
                  </Btn>
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
  planId
}: {
  created: Created
  password: string
  departments: string[]
  planId: PlanId
}) {
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
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

      <Btn
        className="mt-6"
        onClick={() => window.location.assign(`/?workspace=${encodeURIComponent(created.workspace)}`)}
      >
        Sign in to FreedomBot <ArrowRight size={15} aria-hidden />
      </Btn>
    </section>
  )
}
