/** Job roles and data tags as admins see them. The server decides what each one grants. */

import type { DepartmentMode } from './types'

/** The departments an admin can give a person (the domain packs). Admin has every department. */
export const DEPARTMENTS = [
  { value: 'general', label: 'General' },
  { value: 'sales', label: 'Sales' },
  { value: 'planning', label: 'Planning' },
  { value: 'finance', label: 'Finance' },
  { value: 'legal', label: 'Legal' },
  { value: 'operations', label: 'Operations' },
  { value: 'intake', label: 'Intake' },
  { value: 'strategy', label: 'Strategy' },
  { value: 'brand', label: 'Brand' },
  { value: 'hr', label: 'Human Resources' }
] as const

/** Listed with the departments, but these are levels: no role inside them, and choosing one replaces
 *  the person's departments. */
export const LEVEL_CHOICES = [
  {
    value: 'employee',
    label: 'Employee',
    meaning: 'Asks and looks things up. Cannot change anything.'
  },
  { value: 'admin', label: 'Admin', meaning: 'Every department, full access.' }
] as const

/** A person's role inside one department. Only admins can change it. */
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

export function departmentLabel(value: string): string {
  return DEPARTMENTS.find((d) => d.value === value)?.label ?? value
}

export function modeMeaning(value: string): string {
  return DEPARTMENT_MODES.find((m) => m.value === value)?.meaning ?? ''
}

export const DATA_TAGS = [
  { value: 'crm', label: 'CRM' },
  { value: 'email', label: 'Gmail' },
  { value: 'calendar', label: 'Calendar' },
  { value: 'sales', label: 'Sales' },
  { value: 'finance', label: 'Finance' },
  { value: 'hr', label: 'HR' },
  { value: 'salary', label: 'Salary' },
  { value: 'ops', label: 'Operations' },
  { value: 'legal', label: 'Legal' },
  { value: 'exec', label: 'Exec' }
] as const

export function tagLabel(value: string): string {
  return DATA_TAGS.find((tag) => tag.value === value)?.label ?? value
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
