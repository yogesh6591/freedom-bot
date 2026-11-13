'use client'

/** Platform owners only: every workspace and who belongs to it. */

import { useCallback, useEffect, useState } from 'react'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  DataTable,
  EmptyState,
  ErrorNote,
  LoadingBlock,
  Panel,
  inputClass,
  timestamp
} from '@/components/common/primitives'

export interface WorkspaceMember {
  id: string
  email: string
  display_name: string
  role: string
  job_role?: string | null
  status: string
  last_login_at?: string | null
  platform_owner?: boolean
}

interface Workspace {
  client_id: string
  slug: string
  company_name: string
  status: string
  internal: boolean
  created_at?: string | null
  member_count: number
  active_member_count: number
  members: WorkspaceMember[]
}

interface WorkspacesResponse {
  workspace_count: number
  member_count: number
  workspaces: Workspace[]
}

const ROLE_LABEL: Record<string, string> = {
  ADMIN: 'Admin',
  OPERATOR: 'Operator',
  APPROVER: 'Approver',
  DRAFTER: 'Drafter',
  VIEWER: 'Viewer'
}

/** The member table used on the owner page and the Administration page. */
export function MemberTable({ members }: { members: WorkspaceMember[] }) {
  return (
    <DataTable
      rows={members}
      empty="No members yet."
      columns={[
        {
          key: 'n',
          header: 'Name',
          render: (m) => (
            <span className="flex flex-wrap items-center gap-1.5">
              {m.display_name || '—'}
              {m.platform_owner && <Badge tone="brand">Platform owner</Badge>}
            </span>
          )
        },
        { key: 'e', header: 'Email', render: (m) => m.email },
        {
          key: 'r',
          header: 'Role',
          render: (m) =>
            `${ROLE_LABEL[m.role] ?? m.role}${m.job_role ? ` · ${m.job_role}` : ''}`
        },
        {
          key: 's',
          header: 'Status',
          render: (m) => (
            <Badge tone={m.status === 'ACTIVE' ? 'good' : 'neutral'}>
              {m.status === 'ACTIVE' ? 'Active' : m.status.toLowerCase()}
            </Badge>
          )
        },
        {
          key: 'l',
          header: 'Last sign-in',
          render: (m) => (m.last_login_at ? timestamp(m.last_login_at) : 'Never')
        }
      ]}
    />
  )
}

/** All workspaces with their members, with search and open/close per workspace. */
export function AllWorkspaces() {
  const [data, setData] = useState<WorkspacesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    try {
      setData(await api.get<WorkspacesResponse>('/api/owner/workspaces'))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load workspaces')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const q = query.trim().toLowerCase()
  const shown = (data?.workspaces ?? [])
    .map((w) => {
      if (!q) return w
      const nameHit =
        w.company_name.toLowerCase().includes(q) || w.slug.toLowerCase().includes(q)
      const members = nameHit
        ? w.members
        : w.members.filter(
            (m) =>
              m.email.toLowerCase().includes(q) ||
              m.display_name.toLowerCase().includes(q)
          )
      return nameHit || members.length ? { ...w, members } : null
    })
    .filter((w): w is Workspace => w !== null)

  return (
    <Panel
      title="All workspaces"
      description="Every client workspace on this platform and its members. Only platform owners see this."
      actions={
        <Btn size="sm" variant="secondary" onClick={() => void load()}>
          Refresh
        </Btn>
      }
    >
      {error && <ErrorNote>{error}</ErrorNote>}
      {!data && !error && <LoadingBlock />}
      {data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <input
              aria-label="Search workspaces and members"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a company, name or email"
              className={`${inputClass} max-w-sm`}
            />
            <span className="text-xs text-muted">
              {data.workspace_count} workspaces · {data.member_count} members
            </span>
          </div>
          {shown.length === 0 ? (
            <EmptyState>No workspace matches.</EmptyState>
          ) : (
            <div className="space-y-3">
              {shown.map((w) => {
                const isOpen = open.has(w.client_id) || Boolean(q)
                return (
                  <div
                    key={w.client_id}
                    className="rounded-xl border border-border bg-background-elevated p-4"
                  >
                    <button
                      type="button"
                      onClick={() => toggle(w.client_id)}
                      aria-expanded={isOpen}
                      className="flex w-full flex-wrap items-center gap-2 text-left"
                    >
                      <span className="type-subtitle text-base">{w.company_name}</span>
                      <Badge tone="neutral">{w.slug}</Badge>
                      {w.internal && <Badge tone="brand">Internal</Badge>}
                      {w.status !== 'ACTIVE' && (
                        <Badge tone="warn">{w.status.toLowerCase()}</Badge>
                      )}
                      <span className="ml-auto text-xs text-muted">
                        {w.active_member_count} active of {w.member_count} members
                        {w.created_at ? ` · since ${timestamp(w.created_at)}` : ''}
                        {isOpen ? ' ▴' : ' ▾'}
                      </span>
                    </button>
                    {isOpen && (
                      <div className="mt-3">
                        <MemberTable members={w.members} />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}
    </Panel>
  )
}
