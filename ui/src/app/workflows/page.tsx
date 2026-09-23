'use client'

/** Workflows (§17) — enable/disable, schedule, mode, recipients, manual run. */

import { useCallback, useEffect, useState } from 'react'
import { AppShell } from '@/components/AppShell'
import { useSession } from '@/components/SessionProvider'
import { api } from '@/lib/api'
import type { WorkflowRunResult, WorkflowSummary } from '@/lib/types'
import {
  Badge,
  EmptyState,
  ErrorNote,
  Field,
  Panel,
  inputClass,
  statusTone,
  timestamp
} from '@/components/common/primitives'

const MODES = ['', 'ADVISE', 'DRAFT', 'WAIT_FOR_APPROVAL', 'AUTO_WITHIN_SCOPE']

export default function WorkflowsPage() {
  return (
    <AppShell>
      <WorkflowsView />
    </AppShell>
  )
}

function WorkflowsView() {
  const { hasRole } = useSession()
  const [items, setItems] = useState<WorkflowSummary[]>([])
  const [runs, setRuns] = useState<Record<string, unknown>[]>([])
  const [error, setError] = useState<string | null>(null)
  const isAdmin = hasRole('ADMIN')
  const canRun = hasRole('OPERATOR') || hasRole('ADMIN')

  const load = useCallback(async () => {
    try {
      const [flows, history] = await Promise.all([
        api.get<{ items: WorkflowSummary[] }>('/api/workflows'),
        api.get<{ items: Record<string, unknown>[] }>('/api/workflow-runs?limit=25')
      ])
      setItems(flows.items)
      setRuns(history.items)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load workflows')
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="space-y-4">
      <Panel
        title="Workflows"
        description="Reusable playbooks. Every write a workflow makes goes through the same policy engine as a chat request — running on a schedule grants no extra authority."
      >
        <p className="text-xs text-muted">
          {items.length} playbook(s) available. A step that exceeds this
          workspace&apos;s policy produces an approval request instead of acting.
        </p>
      </Panel>
      {error && <ErrorNote>{error}</ErrorNote>}

      {items.map((workflow) => (
        <WorkflowCard
          key={workflow.id}
          workflow={workflow}
          isAdmin={isAdmin}
          canRun={canRun}
          onChanged={load}
        />
      ))}

      <Panel title="Recent runs">
        {runs.length === 0 ? (
          <EmptyState>No runs yet.</EmptyState>
        ) : (
          <ul className="space-y-2 text-xs">
            {runs.map((run) => (
              <li
                key={String(run.id)}
                className="flex flex-wrap items-center gap-2 border-b border-border/40 pb-2"
              >
                <Badge tone={statusTone(String(run.status))}>{String(run.status)}</Badge>
                <span className="font-geist text-primary">{String(run.template)}</span>
                <span className="text-muted">
                  {String(run.trigger)} · {timestamp(String(run.started_at))}
                </span>
                {Array.isArray(run.steps) && (
                  <span className="text-muted">{run.steps.length} steps</span>
                )}
                {run.error ? <span className="text-red-300">{String(run.error)}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  )
}

function WorkflowCard({
  workflow,
  isAdmin,
  canRun,
  onChanged
}: {
  workflow: WorkflowSummary
  isAdmin: boolean
  canRun: boolean
  onChanged: () => Promise<void>
}) {
  const [inputs, setInputs] = useState(() => {
    if (workflow.id === 'lead_intake') {
      return JSON.stringify(
        {
          email: 'new.lead@example.com',
          name: 'New Lead',
          message: 'Interested in pricing and a demo'
        },
        null,
        0
      )
    }
    return '{}'
  })
  const [mode, setMode] = useState(workflow.config.execution_mode ?? '')
  const [cron, setCron] = useState(workflow.config.cron ?? workflow.cron ?? '')
  const [recipients, setRecipients] = useState(
    (workflow.config.recipients ?? []).join(', ')
  )
  const [result, setResult] = useState<WorkflowRunResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function patch(body: Record<string, unknown>) {
    setBusy(true)
    setError(null)
    try {
      await api.patch(`/api/workflows/${workflow.id}`, body)
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update')
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const parsed = JSON.parse(inputs || '{}')
      setResult(
        await api.post<WorkflowRunResult>(`/api/workflows/${workflow.id}/run`, {
          inputs: parsed,
          execution_mode: mode || undefined
        })
      )
      await onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Run failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title={workflow.name}
      description={workflow.description}
      actions={
        <div className="flex items-center gap-2">
          <Badge tone="neutral">{workflow.domain}</Badge>
          <Badge tone={workflow.config.enabled ? 'good' : 'neutral'}>
            {workflow.config.enabled ? 'enabled' : 'disabled'}
          </Badge>
        </div>
      }
    >
      <div className="space-y-3">
        <ol className="flex flex-wrap gap-1.5 text-[11px]">
          {workflow.steps.map((step, index) => (
            <li
              key={step}
              className="rounded border border-border px-2 py-0.5 font-dmmono text-muted"
            >
              {index + 1}. {step}
            </li>
          ))}
        </ol>

        {error && <ErrorNote>{error}</ErrorNote>}

        {isAdmin && (
          <div className="grid gap-2 border-t border-border pt-3 md:grid-cols-4">
            <Field label="Execution mode" hint="May only narrow the workspace mode.">
              <select
                className={inputClass}
                value={mode}
                onChange={(event) => setMode(event.target.value)}
              >
                {MODES.map((value) => (
                  <option key={value} value={value}>
                    {value || 'workspace default'}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Schedule (cron)">
              <input
                className={inputClass}
                value={cron}
                onChange={(event) => setCron(event.target.value)}
                placeholder="0 8 * * 1"
              />
            </Field>
            <Field label="Recipients">
              <input
                className={inputClass}
                value={recipients}
                onChange={(event) => setRecipients(event.target.value)}
                placeholder="a@b.com, c@d.com"
              />
            </Field>
            <div className="flex items-end gap-2">
              <button
                disabled={busy}
                onClick={() =>
                  void patch({
                    execution_mode: mode || null,
                    cron: cron || null,
                    recipients: recipients
                      .split(',')
                      .map((value) => value.trim())
                      .filter(Boolean)
                  })
                }
                className="rounded-lg border border-border px-3 py-2 font-geist text-xs text-muted"
              >
                Save
              </button>
              <button
                disabled={busy}
                onClick={() => void patch({ enabled: !workflow.config.enabled })}
                className="rounded-lg border border-border px-3 py-2 font-geist text-xs text-muted"
              >
                {workflow.config.enabled ? 'Disable' : 'Enable'}
              </button>
            </div>
          </div>
        )}

        {canRun && (
          <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
            <div className="min-w-[260px] flex-1">
              <Field
                label="Inputs (JSON)"
                hint={
                  workflow.required_inputs.length
                    ? `Required: ${workflow.required_inputs.join(', ')}`
                    : 'No required inputs'
                }
              >
                <input
                  className={`${inputClass} font-dmmono text-[11px]`}
                  value={inputs}
                  onChange={(event) => setInputs(event.target.value)}
                />
              </Field>
            </div>
            <button
              disabled={busy}
              onClick={() => void run()}
              className="rounded-lg bg-brand px-4 py-2 font-geist text-xs text-white disabled:opacity-50"
            >
              {busy ? 'Running…' : 'Run now'}
            </button>
          </div>
        )}

        {result && (
          <div className="space-y-1 rounded-lg border border-border bg-background p-3">
            <div className="flex items-center gap-2">
              <Badge tone={statusTone(result.status)}>{result.status}</Badge>
              <span className="text-xs text-muted">{result.error ?? ''}</span>
            </div>
            <ol className="space-y-1 text-[11px]">
              {result.steps.map((step) => (
                <li key={step.name} className="flex gap-2">
                  <Badge tone={step.ok ? 'good' : step.awaiting_approval ? 'warn' : 'bad'}>
                    {step.ok ? 'ok' : step.awaiting_approval ? 'queued' : 'failed'}
                  </Badge>
                  <span className="font-dmmono text-primary">{step.name}</span>
                  <span className="text-muted">{step.summary}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </Panel>
  )
}
