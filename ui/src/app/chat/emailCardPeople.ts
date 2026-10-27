import type { EmailViewPerson } from '../../lib/types'

export type Names = { first: string; last: string; company: string }

const TOKEN_FIRST = '{{firstName}}'
const TOKEN_LAST = '{{lastName}}'
const TOKEN_COMPANY = '{{companyName}}'
const TOKEN_ASSESS = '{{assessment_url}}'

export function namesFromGreeting(text: string): { first_name: string; last_name: string } {
  const pattern =
    /\b(?:Hi|Hello|Dear|Hey)\s+(?!\{\{)(?!there\b)([A-Za-z][A-Za-z'-]*)(?:\s+([A-Z][A-Za-z'-]*))?\b/g
  const match = pattern.exec(text || '')
  const first = (match?.[1] || '').trim()
  const last = (match?.[2] || '').trim()
  if (!first || /^firstName$/i.test(first)) return { first_name: '', last_name: '' }
  return {
    first_name: first,
    last_name: /^lastName$/i.test(last) ? '' : last
  }
}

export function applyNames(person: EmailViewPerson | undefined, names: Names): EmailViewPerson {
  return {
    email: person?.email || '',
    first_name: names.first,
    last_name: names.last,
    company: names.company,
    sources: {
      first_name: names.first !== (person?.first_name || '') ? 'typed' : person?.sources.first_name || 'typed',
      last_name: names.last !== (person?.last_name || '') ? 'typed' : person?.sources.last_name || 'missing',
      company: names.company !== (person?.company || '') ? 'typed' : person?.sources.company || 'missing'
    }
  }
}

export function fillText(text: string, person?: EmailViewPerson, assessmentUrl = ''): string {
  const first = (person?.first_name || '').trim() || 'there'
  const last = (person?.last_name || '').trim()
  const company = (person?.company || '').trim()
  const link = (assessmentUrl || '').trim() || TOKEN_ASSESS
  return (text || '')
    .split(TOKEN_FIRST)
    .join(first)
    .split(TOKEN_LAST)
    .join(last)
    .split(TOKEN_COMPANY)
    .join(company)
    .split(TOKEN_ASSESS)
    .join(link)
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[ \t]+([,.!?:;])/g, '$1')
}

/** Overlay the typed Details for the selected person; everyone else keeps their own record. */
export function detailsForPerson(
  person: EmailViewPerson,
  typed: Names | null,
  selectedEmail: string
): EmailViewPerson {
  if (
    typed &&
    person.email &&
    selectedEmail &&
    person.email.toLowerCase() === selectedEmail.toLowerCase()
  ) {
    return applyNames(person, typed)
  }
  return person
}

function usableFirst(value: string): string {
  const first = (value || '').trim()
  return first && first.toLowerCase() !== 'there' ? first : ''
}

/**
 * Label for a Preview as button. Uses the same filled greeting the card shows:
 * typed name (already overlaid on `person`) first, then database/chat first_name,
 * then a name written in the preview, then the mailbox.
 */
export function previewAsLabel(
  person: EmailViewPerson,
  sharedBody = '',
  assessmentUrl = ''
): string {
  const filled = fillText(sharedBody, person, assessmentUrl)
  const greet = namesFromGreeting(filled)
  if (greet.first_name) return greet.first_name
  const first = usableFirst(person.first_name)
  if (first) return first
  const last = (person.last_name || '').trim()
  if (last) return last.split(/\s+/)[0]
  const local = (person.email || '').split('@')[0].trim()
  return local || 'Recipient'
}
