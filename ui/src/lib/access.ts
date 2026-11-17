/** Job roles and data tags as admins see them. The server decides what each one grants. */

import type { DepartmentMode } from './types'

/** Departments as data access tags: where a person works. Operations is "ops"; HR also brings salary. */
export const DEPARTMENTS = [
  { value: 'general', label: 'General' },
  { value: 'sales', label: 'Sales' },
  { value: 'planning', label: 'Planning' },
  { value: 'finance', label: 'Finance' },
  { value: 'legal', label: 'Legal' },
  { value: 'ops', label: 'Operations' },
  { value: 'intake', label: 'Intake' },
  { value: 'strategy', label: 'Strategy' },
  { value: 'brand', label: 'Brand' },
  { value: 'hr', label: 'HR' }
] as const

/** Apps as data access tags. */
export const APP_TAGS = [
  { value: 'crm', label: 'CRM' },
  { value: 'email', label: 'Gmail' },
  { value: 'calendar', label: 'Calendar' }
] as const

/** A person's one role, for all their work. Only admins can change it. */
export const DEPARTMENT_MODES: {
  value: DepartmentMode
  label: string
  meaning: string
}[] = [
  { value: 'advise', label: 'Advise', meaning: 'Just answers. Nothing is drafted, sent or changed.' },
  { value: 'draft', label: 'Draft', meaning: 'Prepares it, they review. Nothing is sent.' },
  {
    value: 'approve',
    label: 'Approve',
    meaning: 'Waits for a human before sending email, CRM writes or spending money.'
  },
  { value: 'auto', label: 'Auto', meaning: 'Routine work runs by itself.' }
]

export function modeMeaning(value: string): string {
  return DEPARTMENT_MODES.find((m) => m.value === value)?.meaning ?? ''
}

/** Everything a person can be given or ask for: departments, then apps. */
export const DATA_TAGS = [...DEPARTMENTS, ...APP_TAGS]

const OTHER_LABELS: Record<string, string> = { salary: 'Salary', exec: 'Exec' }

export function tagLabel(value: string): string {
  return DATA_TAGS.find((tag) => tag.value === value)?.label ?? OTHER_LABELS[value] ?? value
}

export interface AccessOverview {
  areas: string[]
  granted: string[]
  pending: string[]
  admin_emails: string[]
}

export interface AccessRequest {
  id: string
  user_id: string
  email: string
  display_name: string
  areas: string[]
  reason: string
  created_at: string | null
}

/** A ready-to-send email to the workspace admins, opened in the person's own mail app. */
export function accessEmailLink(
  adminEmails: string[],
  areas: string[],
  reason: string,
  name: string
): string {
  const labels = areas.map(tagLabel).join(', ')
  const body = [
    'Hi,',
    '',
    `Please give me access to: ${labels}.`,
    reason ? `Reason: ${reason}` : '',
    '',
    'You can grant it in FreedomBot under Administration > Users.',
    '',
    name
  ]
    .filter((line, index, all) => line || all[index - 1])
    .join('\n')
  const subject = `FreedomBot access request: ${labels}`
  return `mailto:${adminEmails.join(',')}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`
}
