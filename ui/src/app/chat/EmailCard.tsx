'use client'

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode
} from 'react'
import { api, ApiError } from '@/lib/api'
import type { EmailCardScope, EmailView, EmailViewPerson } from '@/lib/types'
import {
  applyNames,
  detailsForPerson,
  fillText,
  namesFromGreeting,
  previewAsLabel,
  type Names
} from '@/app/chat/emailCardPeople'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'

const TOKEN_FIRST = '{{firstName}}'
const TOKEN_LAST = '{{lastName}}'
const TOKEN_COMPANY = '{{companyName}}'
const TOKEN_ASSESS = '{{assessment_url}}'
const TOKEN_SPLIT = /(\{\{(?:firstName|lastName|companyName|assessment_url)\}\})/g
const KNOWN_TOKENS = new Set([TOKEN_FIRST, TOKEN_LAST, TOKEN_COMPANY, TOKEN_ASSESS])
const KEEP_LINK = /(https?:\/\/[^\s]+|\{+\s*assessment[_-]?url\s*\}+)/gi

type NameKey = 'first' | 'last' | 'company'

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function isGreetingPlainTextWarning(warning: string): boolean {
  return warning.includes('as plain text, so everyone who gets this email sees exactly that')
}

function uniqueKeepOrder(values: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of values) {
    const value = raw.trim()
    if (!value) continue
    const key = value.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(value)
  }
  return out
}

function isEmail(value: string): boolean {
  return value.includes('@')
}

function displayName(person: EmailViewPerson): string {
  const name = [person.first_name, person.last_name]
    .map((part) => part.trim())
    .filter((part) => part && part.toLowerCase() !== 'there')
    .join(' ')
  return name || person.email || ''
}

function uniquePeople(people: EmailViewPerson[]): EmailViewPerson[] {
  const seen = new Set<string>()
  const out: EmailViewPerson[] = []
  for (const person of people) {
    const key = (person.email || displayName(person)).trim().toLowerCase()
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(person)
  }
  return out
}

function recipientEmails(view: EmailView): string[] {
  return uniqueKeepOrder([
    ...(view.people || []).map((person) => person.email),
    ...(view.previews || []).map((item) => item.email),
    ...(view.audience?.will_send_now || []).filter(isEmail)
  ])
}

function attachEmails(people: EmailViewPerson[], emails: string[]): EmailViewPerson[] {
  const unique = uniqueKeepOrder(emails)
  if (!people.length) {
    return unique.map((email) => ({
      email,
      first_name: '',
      last_name: '',
      company: '',
      sources: { first_name: 'missing', last_name: 'missing', company: 'missing' }
    }))
  }
  const used = new Set<string>()
  return people.map((person, index) => {
    const email = person.email.trim() || unique[index] || unique[0] || ''
    const key = email.toLowerCase()
    if (key && used.has(key)) return { ...person, email }
    if (key) used.add(key)
    return { ...person, email }
  })
}

function peopleForBlanks(view: EmailView): EmailViewPerson[] {
  const greeting = namesFromGreeting(
    [view.template?.subject, view.template?.body, view.previews?.[0]?.body]
      .filter(Boolean)
      .join('\n')
  )
  const emails = recipientEmails(view)
  const fallback = {
    first_name: greeting.first_name,
    last_name: greeting.last_name,
    company: '',
    sources: {
      first_name: greeting.first_name ? 'chat' : 'missing',
      last_name: greeting.last_name ? 'chat' : 'missing',
      company: 'missing'
    } as EmailViewPerson['sources']
  }
  if (view.people?.length) {
    return attachEmails(view.people, emails)
  }
  if (!emails.length) return [{ email: '', ...fallback }]
  return emails.map((email) => ({ email, ...fallback }))
}

function replaceOutsideTokens(
  text: string,
  pattern: RegExp,
  replacement: string | ((match: string, ...args: string[]) => string)
): string {
  return (text || '')
    .split(TOKEN_SPLIT)
    .map((part) => {
      if (KNOWN_TOKENS.has(part)) return part
      return typeof replacement === 'string'
        ? part.replace(pattern, replacement)
        : part.replace(pattern, replacement)
    })
    .join('')
}

function isProtectedLink(part: string): boolean {
  return /^https?:\/\//i.test(part) || /^\{+\s*assessment[_-]?url\s*\}+$/i.test(part)
}

function keepAssessmentLink(text: string, base = ''): string {
  let out = text || ''
  const root = (base || '').replace(/\/$/, '')
  if (root) {
    out = out.replace(new RegExp(`${escapeRegExp(root)}(?:/a/\\S+)?`, 'g'), TOKEN_ASSESS)
  }
  out = out.replace(/\{+\s*assessment[_-]?url\s*\}+/gi, TOKEN_ASSESS)
  if (!out.includes(TOKEN_ASSESS)) {
    out = `${out.replace(/\s+$/, '')}\n\n${TOKEN_ASSESS}`
  }
  return out
}

function stripAssessmentLink(text: string, base = ''): string {
  let out = text || ''
  const root = (base || '').replace(/\/$/, '')
  if (root) {
    out = out.replace(new RegExp(`${escapeRegExp(root)}(?:/a/\\S+)?`, 'g'), '')
  }
  out = out.replace(/\{+\s*assessment[_-]?url\s*\}+/gi, '')
  out = out.replace(TOKEN_ASSESS, '')
  return out.replace(/[ \t]{2,}/g, ' ').replace(/\n{3,}/g, '\n\n').trim()
}

function mapUnprotected(text: string, transform: (chunk: string) => string): string {
  return (text || '')
    .split(KEEP_LINK)
    .map((part) => (isProtectedLink(part) ? part : transform(part)))
    .join('')
}

/** Shared text never contains a real name — blanks fill in per person after save. */
function tokenizeText(
  text: string,
  people: EmailViewPerson[],
  assessmentUrl = '',
  ensureLink = false
): string {
  const framed = mapUnprotected(text || '', (chunk) => tokenizeNames(chunk, people))
  if (ensureLink) return keepAssessmentLink(framed, assessmentUrl)
  return stripAssessmentLink(framed, assessmentUrl)
}

function tokenizeNames(text: string, people: EmailViewPerson[]): string {
  let out = text || ''
  for (const person of people) {
    const first = person.first_name.trim()
    const last = person.last_name.trim()
    if (!first) continue
    const lastGroup = last ? `(?:\\s+${escapeRegExp(last)})?` : ''
    out = replaceOutsideTokens(
      out,
      new RegExp(`\\b(Hi|Hello|Dear|Hey)\\s+${escapeRegExp(first)}${lastGroup}\\b`, 'gi'),
      (match, greeting: string) =>
        `${greeting} ${TOKEN_FIRST}` +
        (last && match.toLowerCase().includes(last.toLowerCase()) ? ` ${TOKEN_LAST}` : '')
    )
  }
  out = replaceOutsideTokens(
    out,
    /\b(Hi|Hello|Dear|Hey)\s+there\b/gi,
    `$1 ${TOKEN_FIRST}`
  )
  for (const person of people) {
    const first = person.first_name.trim()
    const last = person.last_name.trim()
    const company = person.company.trim()
    if (first && !/^there$/i.test(first)) {
      out = replaceOutsideTokens(
        out,
        new RegExp(`(?<!\\{)${escapeRegExp(first)}(?!\\w)`, 'gi'),
        TOKEN_FIRST
      )
    }
    if (last && last.length > 1) {
      out = replaceOutsideTokens(
        out,
        new RegExp(`(?<!\\{)${escapeRegExp(last)}(?!\\w)`, 'gi'),
        TOKEN_LAST
      )
    }
    if (company) {
      out = replaceOutsideTokens(
        out,
        new RegExp(`(?<!\\{)${escapeRegExp(company)}(?!\\w)`, 'gi'),
        TOKEN_COMPANY
      )
    }
  }
  return out
}

function namesFromPerson(person?: EmailViewPerson): Names {
  return {
    first: (person?.first_name || '').trim() || 'there',
    last: (person?.last_name || '').trim(),
    company: (person?.company || '').trim()
  }
}

function nameChange(original: Names, next: Names): { from: string; to: string } | null {
  if (next.first !== original.first) return { from: original.first, to: next.first }
  if (next.last !== original.last) return { from: original.last, to: next.last }
  if (next.company !== original.company) return { from: original.company, to: next.company }
  return null
}

/** Chip replacements stay blanks in shared text. A typed name never becomes everyone else's wording. */
function keepSharedBlanks(text: string, original: string, names: Names): string {
  let out = text || ''
  const slots: { token: string; typed: string }[] = [
    { token: TOKEN_FIRST, typed: names.first },
    { token: TOKEN_LAST, typed: names.last },
    { token: TOKEN_COMPANY, typed: names.company }
  ]
  for (const { token, typed } of slots) {
    if (!original.includes(token) || out.includes(token)) continue
    const origAt = original.indexOf(token)
    const prefix = original.slice(0, origAt)
    const suffix = original.slice(origAt + token.length)
    if (out.startsWith(prefix) && out.endsWith(suffix) && prefix.length + suffix.length <= out.length) {
      out = prefix + token + suffix
      continue
    }
    const name = visibleName(typed)
    if (name) {
      const at = out.indexOf(name)
      if (at >= 0) {
        out = out.slice(0, at) + token + out.slice(at + name.length)
      }
    }
  }
  return out
}

function isSelectedPerson(
  person: EmailViewPerson,
  index: number,
  selected: EmailViewPerson | undefined,
  all: EmailViewPerson[]
): boolean {
  if (selected?.email && person.email) {
    return person.email.toLowerCase() === selected.email.toLowerCase()
  }
  return (!selected?.email && index === 0) || all.length === 1
}

function typedFromRemovedChip(text: string, original: string, token: string): string {
  if (!original.includes(token) || (text || '').includes(token)) return ''
  const origAt = original.indexOf(token)
  const prefix = original.slice(0, origAt)
  const suffix = original.slice(origAt + token.length)
  if (!(text.startsWith(prefix) && text.endsWith(suffix))) return ''
  if (prefix.length + suffix.length > text.length) return ''
  return visibleName(text.slice(prefix.length, text.length - suffix.length))
}

function namesFromChipReplacement(
  subjectText: string,
  bodyText: string,
  originalSubject: string,
  originalBody: string,
  names: Names
): Names {
  const next = { ...names }
  const slots: { token: string; key: NameKey }[] = [
    { token: TOKEN_FIRST, key: 'first' },
    { token: TOKEN_LAST, key: 'last' },
    { token: TOKEN_COMPANY, key: 'company' }
  ]
  for (const { token, key } of slots) {
    const typed =
      typedFromRemovedChip(subjectText, originalSubject, token) ||
      typedFromRemovedChip(bodyText, originalBody, token)
    if (typed) next[key] = typed
  }
  return next
}

const TOKEN_META: Record<string, { key: NameKey; ariaLabel: string }> = {
  [TOKEN_FIRST]: { key: 'first', ariaLabel: 'First name' },
  [TOKEN_LAST]: { key: 'last', ariaLabel: 'Last name' },
  [TOKEN_COMPANY]: { key: 'company', ariaLabel: 'Company' }
}

function visibleName(value: string): string {
  const text = value.replace(/[{}]/g, '')
  return text.toLowerCase() === 'there' ? '' : text
}

function NameChip({
  value,
  original,
  onChange,
  'aria-label': ariaLabel
}: {
  value: string
  original: string
  onChange: (next: string) => void
  'aria-label': string
}) {
  const inner = visibleName(value)
  const copied = `{{${inner || visibleName(original)}}}`
  const width = Math.max(inner.length, 1)

  function copyBlank(event: { preventDefault: () => void; clipboardData: DataTransfer }) {
    event.preventDefault()
    event.clipboardData.setData('text/plain', copied)
  }

  return (
    <span
      contentEditable={false}
      data-token={copied}
      className="mx-0.5 inline-flex items-baseline rounded-md bg-[#e8f0fe] px-1 py-0.5 align-baseline text-[13px] font-medium text-[#1a73e8]"
      onCopy={copyBlank}
    >
      <span aria-hidden>{'{{'}</span>
      <input
        value={inner}
        aria-label={ariaLabel}
        size={width}
        style={{ width: `${width}ch` }}
        className="min-w-[1ch] border-0 bg-transparent p-0 text-[13px] font-medium text-[#1a73e8] outline-none"
        onChange={(event) => onChange(event.target.value.replace(/[{}]/g, ''))}
        onBlur={() => {
          if (!visibleName(value)) onChange(original)
        }}
        onCopy={copyBlank}
        onKeyDown={(event) => event.stopPropagation()}
      />
      <span aria-hidden>{'}}'}</span>
    </span>
  )
}

function EditableText({
  value,
  onChange,
  multiline
}: {
  value: string
  onChange: (next: string) => void
  multiline: boolean
}) {
  const ref = useRef<HTMLSpanElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    if ((el.textContent || '') === value) return
    el.textContent = value
  }, [value])

  return (
    <span
      ref={ref}
      contentEditable
      suppressContentEditableWarning
      className={multiline ? 'whitespace-pre-wrap outline-none' : 'outline-none'}
      onInput={() => {
        const el = ref.current
        if (!el) return
        onChange(el.textContent || '')
      }}
      onKeyDown={(event) => {
        if (!multiline && event.key === 'Enter') event.preventDefault()
      }}
    />
  )
}

function TokenField({
  value,
  names,
  originalNames,
  onChange,
  onNames,
  multiline = false,
  assessmentUrl = '',
  'aria-label': ariaLabel
}: {
  value: string
  names: Names
  originalNames: Names
  onChange: (next: string) => void
  onNames: (key: NameKey, next: string) => void
  multiline?: boolean
  assessmentUrl?: string
  'aria-label': string
}) {
  const parts = (value || '').split(TOKEN_SPLIT)

  function setPart(index: number, next: string) {
    const copy = (value || '').split(TOKEN_SPLIT)
    if (KNOWN_TOKENS.has(copy[index])) return
    copy[index] = next
    onChange(copy.join(''))
  }

  return (
    <div
      role="textbox"
      aria-multiline={multiline}
      aria-label={ariaLabel}
      className={
        multiline
          ? 'min-h-[12rem] w-full whitespace-pre-wrap break-words px-4 py-3 text-[14px] leading-6 text-[#202124]'
          : 'w-full whitespace-nowrap text-[14px] leading-5 text-[#202124]'
      }
      onCopy={(event) => {
        const selection = window.getSelection()
        if (!selection || selection.rangeCount === 0) return
        const copied = selection.getRangeAt(0).cloneContents().textContent || ''
        if (!copied) return
        event.preventDefault()
        event.clipboardData.setData('text/plain', copied)
      }}
    >
      {parts.map((part, index) => {
        const meta = TOKEN_META[part]
        if (meta) {
          return (
            <NameChip
              key={`${part}-${index}`}
              value={names[meta.key]}
              original={originalNames[meta.key]}
              onChange={(next) => onNames(meta.key, next)}
              aria-label={meta.ariaLabel}
            />
          )
        }
        if (part === TOKEN_ASSESS) {
          return (
            <span
              key={`${part}-${index}`}
              contentEditable={false}
              className="break-all text-[#1a73e8]"
            >
              {assessmentUrl.trim() || TOKEN_ASSESS}
            </span>
          )
        }
        return (
          <EditableText
            key={`text-${index}`}
            value={part}
            multiline={multiline}
            onChange={(next) => setPart(index, next)}
          />
        )
      })}
    </div>
  )
}

function MailRow({
  label,
  children
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-3 border-b border-[#f1f3f4] px-4 py-2.5">
      <span className="w-[4.75rem] shrink-0 text-[13px] text-[#5f6368]">{label}</span>
      <div className="min-w-0 flex-1 text-[14px] leading-5 text-[#202124]">{children}</div>
    </div>
  )
}

function modeChipClass(mode: string): string {
  if (mode === 'Follow-up (same thread)') {
    return 'bg-sky-50 text-sky-700 ring-sky-200/80'
  }
  if (mode === 'New email (new thread)') {
    return 'bg-violet-50 text-violet-700 ring-violet-200/80'
  }
  return 'bg-emerald-50 text-emerald-700 ring-emerald-200/80'
}

function recipientTypeLabel(mode?: string): string {
  if (mode === 'Follow-up (same thread)') return 'Follow-up'
  if (mode === 'New email (new thread)') return 'New email'
  if (mode === 'First email') return 'First email'
  return ''
}

export function EmailCard({
  view,
  sessionId,
  editable,
  onView
}: {
  view: EmailView
  sessionId: string
  editable: boolean
  onView: (next: EmailView) => void
}) {
  const template = view.template
  const blanks = peopleForBlanks(view)
  const assessmentUrl = (view.assessment_url || '').trim()
  const sharedSubject = tokenizeText(template?.subject ?? '', blanks, assessmentUrl, false)
  const sharedBody = tokenizeText(template?.body ?? '', blanks, assessmentUrl, true)
  const previews = view.previews ?? []
  const audience = view.audience
  const warnings = (view.warnings ?? []).filter((warning) => !isGreetingPlainTextWarning(warning))
  const everyone = uniquePeople(blanks)
  const previewPeople =
    everyone.length > 0
      ? everyone
      : uniqueKeepOrder(previews.map((item) => item.email)).map(
          (email) =>
            blanks.find((person) => person.email === email) || {
              email,
              first_name: '',
              last_name: '',
              company: '',
              sources: {
                first_name: 'missing' as const,
                last_name: 'missing' as const,
                company: 'missing' as const
              }
            }
        )
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState(sharedSubject)
  const [body, setBody] = useState(sharedBody)
  const [names, setNames] = useState<Names>(() => namesFromPerson(blanks[0]))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [previewIndex, setPreviewIndex] = useState(0)
  const [askNameScope, setAskNameScope] = useState(false)

  const previewPerson = previewPeople[previewIndex] || previewPeople[0]
  const originalNames = namesFromPerson(previewPerson)

  useEffect(() => {
    if (!editing) {
      setSubject(sharedSubject)
      setBody(sharedBody)
      setNames(originalNames)
      setAskNameScope(false)
      setError(null)
    }
  }, [editing, sharedSubject, sharedBody, originalNames.first, originalNames.last, originalNames.company])

  useEffect(() => {
    if (previewIndex >= Math.max(previewPeople.length, 1)) setPreviewIndex(0)
  }, [previewIndex, previewPeople.length])

  if (!template) return null

  const fillPerson = editing
    ? applyNames(previewPerson, names)
    : {
        email: previewPerson?.email || '',
        first_name: (previewPerson?.first_name || '').trim(),
        last_name: (previewPerson?.last_name || '').trim(),
        company: (previewPerson?.company || '').trim(),
        sources: previewPerson?.sources || {
          first_name: 'missing',
          last_name: 'missing',
          company: 'missing'
        }
      }
  // The server writes each person's own text (different wording, "Re:" follow-ups). Show it when it applies.
  const serverPreview = previews.find((item) => item.email === previewPerson?.email) as
    | ((typeof previews)[number] & { mode?: string; custom?: boolean })
    | undefined
  const modeLabel = serverPreview?.mode || ''
  const useServerText = Boolean(
    !editing && serverPreview && (serverPreview.subject || '').trim() && (serverPreview.body || '').trim()
  )
  const shownSubject = useServerText
    ? serverPreview?.subject || ''
    : fillText(sharedSubject, fillPerson, assessmentUrl)
  const shownBody = useServerText
    ? serverPreview?.body || ''
    : fillText(sharedBody, fillPerson, assessmentUrl)
  const waitingNames = uniqueKeepOrder(
    (audience?.also_waiting ?? []).map((person) => person.name)
  ).join(', ')
  const showWaitingNote =
    Boolean(audience) &&
    !audience?.campaign_is_new &&
    (audience?.also_waiting?.length ?? 0) > 0 &&
    Boolean(audience?.template_changed)
  const campaign = audience?.campaign || 'this campaign'
  const resolvedNames = namesFromChipReplacement(
    subject,
    body,
    sharedSubject,
    sharedBody,
    names
  )
  const changed = nameChange(originalNames, resolvedNames)
  const namesChanged = Boolean(changed)
  const templateChanged = subject !== sharedSubject || body !== sharedBody
  const canSave =
    Boolean(subject.trim() && body.trim()) &&
    (templateChanged || namesChanged) &&
    !saving

  function updateNames(key: NameKey, next: string) {
    setNames((current) => ({ ...current, [key]: next }))
  }

  function openEditor() {
    setSubject(sharedSubject)
    setBody(sharedBody)
    setNames(originalNames)
    setAskNameScope(false)
    setError(null)
    setEditing(true)
  }

  function cancel() {
    setSubject(sharedSubject)
    setBody(sharedBody)
    setNames(originalNames)
    setAskNameScope(false)
    setError(null)
    setEditing(false)
  }

  function localView(_scope: 'forever' | 'this_email' | null): EmailView {
    const emails = recipientEmails(view)
    const sourcePeople = attachEmails(
      view.people?.length ? view.people : blanks,
      emails
    )
    const people = sourcePeople.map((person, index) =>
      isSelectedPerson(person, index, previewPerson, sourcePeople)
        ? applyNames(person, resolvedNames)
        : person
    )
    const nextSubject = keepSharedBlanks(subject, sharedSubject, resolvedNames)
    const nextBody = keepSharedBlanks(body, sharedBody, resolvedNames)
    const previewRows = previews.length
      ? previews
      : emails.map((email) => ({ email, subject: nextSubject, body: nextBody }))
    const uniquePreviews = uniqueKeepOrder(previewRows.map((item) => item.email)).map(
      (email) =>
        previewRows.find((item) => item.email === email) || {
          email,
          subject: nextSubject,
          body: nextBody
        }
    )
    const rows = uniquePreviews.length
      ? uniquePreviews
      : [{ email: emails[0] || '', subject: nextSubject, body: nextBody }]
    return {
      ...view,
      template: { subject: nextSubject, body: nextBody },
      people,
      previews: rows.map((item) => {
        const person = people.find(
          (row) =>
            row.email &&
            item.email &&
            row.email.toLowerCase() === item.email.toLowerCase()
        ) || {
          email: item.email || '',
          first_name: '',
          last_name: '',
          company: '',
          sources: {
            first_name: 'missing' as const,
            last_name: 'missing' as const,
            company: 'missing' as const
          }
        }
        return {
          ...item,
          subject: fillText(nextSubject, person, view.assessment_url),
          body: fillText(nextBody, person, view.assessment_url)
        }
      })
    }
  }

  async function postCard(bodyPayload: {
    subject?: string
    body?: string
    people?: Record<string, { first_name?: string; last_name?: string; company?: string }>
    name_scope?: 'forever' | 'this_email'
    scope?: EmailCardScope
  }) {
    const data = await api.post<{ email_view: EmailView }>('/api/chat/email-card', {
      session_id: sessionId,
      ...bodyPayload
    })
    return data.email_view
  }

  function requestSave() {
    if (!canSave || !template) return
    if (namesChanged) {
      setAskNameScope(true)
      return
    }
    void save(null)
  }

  async function save(scope: 'forever' | 'this_email' | null) {
    if (!template) return
    if (namesChanged && !scope) return
    const next = localView(scope)
    onView(next)
    setEditing(false)
    setAskNameScope(false)
    setSaving(true)
    setError(null)
    try {
      const people: Record<string, { first_name?: string; last_name?: string; company?: string }> =
        {}
      for (const person of next.people || []) {
        if (!person.email) continue
        people[person.email] = {
          first_name: person.first_name,
          last_name: person.last_name,
          company: person.company
        }
      }
      const target =
        previewPerson?.email ||
        recipientEmails(view)[0] ||
        Object.keys(people)[0]
      if (target) {
        people[target] = {
          first_name: resolvedNames.first,
          last_name: resolvedNames.last,
          company: resolvedNames.company
        }
      }
      if (!sessionId) {
        setError('This conversation is not saved yet. Send a message first, then edit the email.')
        return
      }
      const remote = await postCard({
        subject: next.template?.subject ?? subject,
        body: next.template?.body ?? body,
        ...(Object.keys(people).length ? { people } : {}),
        ...(scope ? { name_scope: scope } : {})
      })
      if (remote) {
        onView({
          ...remote,
          draft_id: remote.draft_id || next.draft_id || view.draft_id
        })
      }
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  async function setScope(scope: 'everyone' | '') {
    setSaving(true)
    setError(null)
    try {
      const remote = await postCard({ scope })
      if (remote) onView(remote)
    } catch (err) {
      onView({
        ...view,
        audience: view.audience ? { ...view.audience, scope } : view.audience
      })
      if (!(err instanceof ApiError) || err.status !== 404) {
        setError(err instanceof ApiError || err instanceof Error ? err.message : 'Could not save')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
    <article className="w-full max-w-[36rem] self-start overflow-hidden rounded-2xl border border-[#e6e6e6] bg-white text-[#202124] shadow-[0_1px_2px_rgba(60,64,67,0.15),0_2px_6px_rgba(60,64,67,0.1)]">
      <div className="flex items-center justify-between gap-2 px-3 pt-3">
        <span className="assistant-orb h-5 w-5 shrink-0 rounded-full" aria-hidden />
        {modeLabel ? (
          <span
            className={`inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-[11px] font-medium tracking-wide ring-1 ring-inset ${modeChipClass(modeLabel)}`}
          >
            {modeLabel}
          </span>
        ) : null}
      </div>

      <MailRow label="To">
        {previewPeople.length ? (
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            {previewPeople.map((person, index) => {
              const preview = previews.find(
                (item) =>
                  Boolean(item.email) &&
                  Boolean(person.email) &&
                  item.email.toLowerCase() === person.email.toLowerCase()
              )
              const typeLabel = recipientTypeLabel(preview?.mode)
              return (
                <span key={person.email || index} className="inline-flex max-w-full items-center gap-1.5">
                  <span className="break-all">{person.email || '—'}</span>
                  {typeLabel ? (
                    <span
                      className={`inline-flex shrink-0 items-center rounded-full px-1.5 py-0 text-[10px] font-medium tracking-wide ring-1 ring-inset ${modeChipClass(preview?.mode || '')}`}
                    >
                      {typeLabel}
                    </span>
                  ) : null}
                  {index < previewPeople.length - 1 ? (
                    <span className="text-[#5f6368]">,</span>
                  ) : null}
                </span>
              )
            })}
          </span>
        ) : (
          <span className="break-words">—</span>
        )}
      </MailRow>

      {previewPeople.length > 1 ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-[#f1f3f4] px-4 py-2.5">
          <span className="text-[13px] text-[#5f6368]">Preview as:</span>
          <div role="tablist" aria-label="Preview as" className="flex flex-wrap gap-1.5">
            {previewPeople.map((person, index) => {
              const selected = index === Math.min(previewIndex, previewPeople.length - 1)
              const resolved = detailsForPerson(
                person,
                editing ? names : null,
                previewPerson?.email || ''
              )
              return (
                <button
                  key={person.email || index}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  className={
                    selected
                      ? 'rounded-full bg-[#e8f0fe] px-3 py-1 text-[13px] font-medium text-[#1a73e8]'
                      : 'rounded-full bg-[#f1f3f4] px-3 py-1 text-[13px] font-medium text-[#5f6368] hover:bg-[#e8eaed]'
                  }
                  onClick={() => {
                    setPreviewIndex(index)
                    if (editing) setNames(namesFromPerson(previewPeople[index]))
                  }}
                >
                  {previewAsLabel(resolved, sharedBody, assessmentUrl)}
                </button>
              )
            })}
          </div>
        </div>
      ) : null}

      <MailRow label="Subject">
        {editing ? (
          <TokenField
            value={subject}
            names={names}
            originalNames={originalNames}
            onChange={setSubject}
            onNames={updateNames}
            assessmentUrl={assessmentUrl}
            aria-label="Subject"
          />
        ) : (
          <span className="break-words">{shownSubject || '—'}</span>
        )}
      </MailRow>

      {editing ? (
        <TokenField
          value={body}
          names={names}
          originalNames={originalNames}
          onChange={setBody}
          onNames={updateNames}
          multiline
          assessmentUrl={assessmentUrl}
          aria-label="Body"
        />
      ) : (
        <p className="whitespace-pre-wrap px-4 py-4 text-[14px] leading-6 text-[#202124]">
          {shownBody}
        </p>
      )}

      {error ? (
        <p className="mx-4 mb-3 rounded-lg bg-red-50 px-3 py-2 text-[12px] leading-5 text-red-700">
          {error}
        </p>
      ) : null}

      {showWaitingNote && audience?.scope !== 'everyone' ? (
        <p className="mx-4 mb-2 rounded-lg bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
          This changes the first email for everyone in {campaign} who hasn&apos;t been
          emailed yet: {waitingNames}.
        </p>
      ) : null}

      {editable && audience?.scope_needed && audience.scope !== 'everyone' ? (
        <div className="px-4 pb-3">
          <button
            type="button"
            disabled={saving}
            className="rounded-lg border border-[#e6e6e6] px-3 py-2 text-left text-[13px] font-medium text-[#202124] hover:bg-[#f8f9fa] disabled:opacity-40"
            onClick={() => void setScope('everyone')}
          >
            Change it for everyone in {campaign}
          </button>
        </div>
      ) : null}

      {audience?.scope === 'everyone' ? (
        <p className="px-4 pb-3 text-[12px] leading-5 text-[#5f6368]">
          <span className="mr-2 inline-flex rounded-md bg-[#e8f0fe] px-2 py-0.5 font-medium text-[#1a73e8]">
            Change it for everyone in {campaign}
          </span>
          {editable ? (
            <button
              type="button"
              className="text-[12px] font-medium text-[#1a73e8]"
              onClick={() => void setScope('')}
            >
              change
            </button>
          ) : null}
        </p>
      ) : null}

      {warnings.map((warning) => (
        <p
          key={warning}
          className="mx-4 mb-3 rounded-lg bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800"
        >
          {warning}
        </p>
      ))}
    </article>

    <Dialog open={askNameScope} onOpenChange={setAskNameScope}>
      <DialogContent className="max-w-[28rem] bg-white text-[#202124]">
        <DialogHeader>
          <DialogTitle className="sr-only">Replace this name?</DialogTitle>
          <DialogDescription className="pb-4 text-[15px] leading-6 text-[#202124]">
            Do you want {changed?.to || 'this name'} to replace{' '}
            {changed?.from || 'the current name'} forever, or only for this email?
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="mt-2 flex-col gap-2 sm:flex-col sm:space-x-0">
          <button
            type="button"
            disabled={saving}
            className="rounded-lg border border-[#e6e6e6] px-3 py-2 text-left text-[13px] font-medium text-[#202124] hover:bg-[#f8f9fa] disabled:opacity-40"
            onClick={() => void save('forever')}
          >
            Replace {changed?.from || 'this name'} forever
          </button>
          <button
            type="button"
            disabled={saving}
            className="rounded-lg border border-[#e6e6e6] px-3 py-2 text-left text-[13px] font-medium text-[#202124] hover:bg-[#f8f9fa] disabled:opacity-40"
            onClick={() => void save('this_email')}
          >
            Only this email
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    </>
  )
}
