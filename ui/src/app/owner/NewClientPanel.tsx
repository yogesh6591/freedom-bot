'use client'

/** New client from a screen, instead of running scripts/provision_client.py on the server. */

import { useEffect, useState, type FormEvent } from 'react'
import { api } from '@/lib/api'
import {
  Btn,
  ErrorNote,
  Field,
  Panel,
  inputClass
} from '@/components/common/primitives'

interface DomainOption {
  name: string
  label: string
  description: string
  always_on: boolean
}

interface FormDefaults {
  domains: DomainOption[]
  default_domains: string[]
  min_password_length: number
  signs_in_here: boolean
}

interface Created {
  client: {
    id: string
    slug: string
    company_name: string
    purchased_domains: string[]
  }
  admin: { email: string; display_name: string }
  signs_in_here: boolean
}

/** Mirrors freedombot.util.ids.slugify so the preview matches what the server will use. */
function previewSlug(value: string): string {
  let slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
    .replace(/_+$/g, '')
  if (!slug) slug = 'client'
  if (/^[0-9]/.test(slug)) slug = `c_${slug}`
  return slug
}

export function NewClientPanel({
  onCreated
}: {
  onCreated: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [defaults, setDefaults] = useState<FormDefaults | null>(null)
  const [company, setCompany] = useState('')
  const [slug, setSlug] = useState('')
  const [domains, setDomains] = useState<Set<string>>(new Set())
  const [adminName, setAdminName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<Created | null>(null)

  useEffect(() => {
    if (!open || defaults) return
    api
      .get<FormDefaults>('/api/owner/clients/new')
      .then((d) => {
        setDefaults(d)
        setDomains(new Set(d.default_domains))
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : 'Could not load the form')
      )
  }, [open, defaults])

  const reset = () => {
    setCompany('')
    setSlug('')
    setAdminName('')
    setAdminEmail('')
    setPassword('')
    setError(null)
    if (defaults) setDomains(new Set(defaults.default_domains))
  }

  const toggle = (name: string) =>
    setDomains((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })

  const minPassword = defaults?.min_password_length ?? 12
  const workspace = previewSlug(slug || company)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (password.length < minPassword) {
      setError(`The password must be at least ${minPassword} characters.`)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await api.post<Created>('/api/owner/clients', {
        company_name: company,
        slug: slug.trim() || null,
        purchased_domains: Array.from(domains),
        admin_name: adminName,
        admin_email: adminEmail,
        admin_password: password
      })
      setCreated(result)
      setOpen(false)
      reset()
      await onCreated()
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Could not create the client'
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title="New client"
      description="Creates the client's own workspace from the FreedomBot template, with a first admin. No server command needed."
      actions={
        !open && (
          <Btn
            size="sm"
            onClick={() => {
              setCreated(null)
              setOpen(true)
            }}
          >
            Add a client
          </Btn>
        )
      }
    >
      {created && (
        <div
          role="status"
          className="rounded-xl border border-border bg-background-elevated px-4 py-3 text-sm text-secondary"
        >
          <p className="font-semibold text-primary">
            {created.client.company_name} is ready.
          </p>
          <p className="mt-1">
            {created.signs_in_here ? (
              <>
                Their admin signs in at{' '}
                <span className="font-mono">/admin</span> with{' '}
                <span className="font-mono">{created.admin.email}</span> and the
                password you set. Workspace identifier:{' '}
                <span className="font-mono">{created.client.slug}</span>.
              </>
            ) : (
              <>
                This deployment serves one client only, so {created.admin.email}{' '}
                signs in on the client&apos;s own deployment. Workspace
                identifier:{' '}
                <span className="font-mono">{created.client.slug}</span>.
              </>
            )}
          </p>
          <p className="mt-1 text-xs text-muted">
            What they bought: {created.client.purchased_domains.join(', ')}.
            Send the password to the admin yourself; FreedomBot does not email
            it.
          </p>
        </div>
      )}

      {!open && !created && (
        <p className="text-sm text-muted">
          Add a company here when they sign up. Their data is kept apart from
          every other client.
        </p>
      )}

      {open && (
        <form className="space-y-5" onSubmit={(e) => void submit(e)}>
          {error && <ErrorNote>{error}</ErrorNote>}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Company name" htmlFor="new-client-company">
              <input
                id="new-client-company"
                required
                maxLength={120}
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                className={inputClass}
                placeholder="Globex Ltd"
              />
            </Field>
            <Field
              label="Workspace identifier (optional)"
              htmlFor="new-client-slug"
              hint={`Will be: ${workspace}. Used in sign-in and the database name. It can't be changed later.`}
            >
              <input
                id="new-client-slug"
                maxLength={40}
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
                className={inputClass}
                placeholder="Made from the company name"
              />
            </Field>
          </div>

          <fieldset>
            <legend className="font-geist text-sm font-medium text-primary">
              What they bought
            </legend>
            <p className="mt-1 text-xs text-muted">
              Their admin can turn these on and off later. Anything else needs a
              change order.
            </p>
            {!defaults ? (
              <p className="mt-3 text-xs text-muted">Loading…</p>
            ) : (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {defaults.domains.map((d) => (
                  <label
                    key={d.name}
                    className="flex items-start gap-2 rounded-lg border border-border px-3 py-2"
                  >
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={d.always_on || domains.has(d.name)}
                      disabled={d.always_on}
                      onChange={() => toggle(d.name)}
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-primary">
                        {d.label}
                        {d.always_on ? ' (always included)' : ''}
                      </span>
                      <span className="block text-xs leading-5 text-muted">
                        {d.description}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="First admin's name" htmlFor="new-client-admin-name">
              <input
                id="new-client-admin-name"
                maxLength={120}
                value={adminName}
                onChange={(e) => setAdminName(e.target.value)}
                className={inputClass}
                placeholder="Client admin"
              />
            </Field>
            <Field label="First admin's email" htmlFor="new-client-admin-email">
              <input
                id="new-client-admin-email"
                type="email"
                required
                value={adminEmail}
                onChange={(e) => setAdminEmail(e.target.value)}
                className={inputClass}
                placeholder="owner@globex.com"
                autoComplete="off"
              />
            </Field>
            <Field
              label="First admin's password"
              htmlFor="new-client-admin-password"
              hint={`At least ${minPassword} characters.`}
            >
              <input
                id="new-client-admin-password"
                type="password"
                required
                minLength={minPassword}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClass}
                autoComplete="new-password"
              />
            </Field>
          </div>

          <div className="flex gap-2">
            <Btn type="submit" size="sm" loading={saving} disabled={!defaults}>
              {saving ? 'Creating the workspace…' : 'Create client'}
            </Btn>
            <Btn
              variant="secondary"
              size="sm"
              disabled={saving}
              onClick={() => {
                setOpen(false)
                reset()
              }}
            >
              Cancel
            </Btn>
          </div>
        </form>
      )}
    </Panel>
  )
}
