/** Job roles and data tags as admins see them. The server decides what each one grants. */

export const JOB_ROLES = [
  { value: 'HR', label: 'HR' },
  { value: 'FINANCE', label: 'Finance' },
  { value: 'OPERATIONS', label: 'Operations' },
  { value: 'SALES', label: 'Sales' },
  { value: 'ADMIN', label: 'Admin' },
  { value: 'EMPLOYEE', label: 'Employee' }
] as const

export const DATA_TAGS = [
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
