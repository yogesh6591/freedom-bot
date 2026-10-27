'use client'

/**
 * The signed-in user's own connections through Composio (Gmail, Google Calendar, YouTube, HubSpot).
 * Shown on the Integrations page (admin panel) and in the Settings pop-up on the home chat.
 */

import { useCallback, useEffect, useState } from 'react'
import { Building2, CalendarDays, Mail, Youtube } from 'lucide-react'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  ErrorNote,
  LoadingBlock,
  PageHeader,
  PageStack,
  Panel
} from '@/components/common/primitives'

interface ComposioItem {
  toolkit: string
  category: string
  name: string
  description: string
  connected: boolean
}

interface SyncSource {
  source: string
  items: number
  last_ok_at: string | null
  last_error: string | null
  items_new: number
  items_updated: number
}

interface SyncStatus {
  enabled: boolean
  interval_sec: number
  next_run_in_sec: number | null
  sources: SyncSource[]
}

/** Composio toolkit -> sync source (bizos/sync). */
const SYNC_SOURCE: Record<string, string> = {
  gmail: 'gmail',
  googlecalendar: 'calendar',
  youtube: 'youtube',
  hubspot: 'hubspot'
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 24 ? `${hours} h ago` : new Date(iso).toLocaleString()
}

interface ComposioStatus {
  configured: boolean
  items: ComposioItem[]
}

const ICONS: Record<string, typeof Mail> = {
  hubspot: Building2,
  gmail: Mail,
  googlecalendar: CalendarDays,
  youtube: Youtube
}

const EXAMPLES: Record<string, string[]> = {
  hubspot: [
    'Find contacts at Contoso',
    'Add a note on John Baker: wants quarterly billing',
    'Move the Contoso pilot deal to negotiation'
  ],
  gmail: [
    'Show my latest emails',
    'Any unread emails from this week?',
    'Send an email to alex@acme.com about tomorrow’s demo'
  ],
  googlecalendar: [
    'What meetings do I have tomorrow?',
    'Am I free Thursday afternoon?',
    'Schedule a 30 minute call with alex@acme.com tomorrow at 4pm'
  ],
  youtube: [
    'How many subscribers does my channel have?',
    'How are my last 5 videos performing?',
    'Find recent YouTube videos about AI agents for small business'
  ]
}

export function IntegrationsView({
  callbackPath = '/integrations',
  compact = false
}: {
  /** Where Composio sends the user back after signing in (it adds ?connected=<toolkit>). */
  callbackPath?: string
  /** Pop-up layout: one column, no page header. */
  compact?: boolean
}) {
  const [status, setStatus] = useState<ComposioStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)

  const loadSync = useCallback(async () => {
    try {
      setSync(await api.get<SyncStatus>('/api/integrations/composio/sync'))
    } catch {
      setSync(null)
    }
  }, [])

  async function syncNow() {
    setSyncing(true)
    try {
      const data = await api.post<{ sources: SyncSource[] }>('/api/integrations/composio/sync')
      setSync((current) => (current ? { ...current, sources: data.sources } : current))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not sync now')
    } finally {
      setSyncing(false)
    }
  }

  const load = useCallback(async (fresh = false) => {
    try {
      const data = fresh
        ? await api.post<ComposioStatus>('/api/integrations/composio/refresh')
        : await api.get<ComposioStatus>('/api/integrations/composio')
      setStatus(data)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load integrations')
    }
  }, [])

  useEffect(() => {
    const returned = new URLSearchParams(window.location.search).has('connected')
    void load(returned)
    void loadSync()
    if (returned) window.history.replaceState(null, '', callbackPath)
  }, [load, loadSync, callbackPath])

  async function connect(toolkit: string) {
    setBusy(toolkit)
    try {
      const callback = `${window.location.origin}${callbackPath}?connected=${toolkit}`
      const data = await api.post<{ redirect_url: string }>(
        `/api/integrations/composio/${toolkit}/connect`,
        { callback_url: callback }
      )
      window.location.href = data.redirect_url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start sign-in')
      setBusy(null)
    }
  }

  async function disconnect(toolkit: string, name: string) {
    if (!window.confirm(`Disconnect ${name}? Chat will stop reading it.`)) return
    setBusy(toolkit)
    try {
      await api.post(`/api/integrations/composio/${toolkit}/disconnect`)
      await load(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not disconnect')
    } finally {
      setBusy(null)
    }
  }

  return (
    <PageStack>
      {compact ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-[13px] leading-5 text-muted">
            Connect your own accounts. The assistant then reads and acts on them, subject to your
            workspace&rsquo;s approval rules.
          </p>
          <Btn variant="secondary" size="sm" onClick={() => void load(true)}>
            Refresh
          </Btn>
        </div>
      ) : (
      <PageHeader
        eyebrow="Your accounts"
        title="Integrations"
        description="Connect your own Gmail, Google Calendar and YouTube. Once connected, the assistant reads your inbox, meetings and channel, and can send email and schedule events from your account — subject to your workspace’s approval rules."
        actions={
          <Btn variant="secondary" size="sm" onClick={() => void load(true)}>
            Refresh
          </Btn>
        }
      />
      )}

      {error && <ErrorNote>{error}</ErrorNote>}

      {status?.configured && sync && status.items.some((item) => item.connected) && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-background-elevated px-4 py-3">
          <p className="text-[13px] text-secondary">
            {sync.enabled
              ? `Latest emails, meetings, videos and CRM changes sync automatically every ${Math.round(sync.interval_sec / 60)} min${
                  sync.next_run_in_sec != null ? ` · next in ${Math.max(1, Math.round(sync.next_run_in_sec / 60))} min` : ''
                }.`
              : 'Automatic sync is off on this server. Use Sync now to pull your latest activity.'}
          </p>
          <Btn variant="secondary" size="sm" loading={syncing} onClick={() => void syncNow()}>
            Sync now
          </Btn>
        </div>
      )}

      {status && !status.configured && (
        <ErrorNote>
          Connections are not enabled on this server yet. An administrator needs to set
          COMPOSIO_API_KEY.
        </ErrorNote>
      )}

      {!status ? (
        <LoadingBlock />
      ) : (
        <div className={compact ? 'grid grid-cols-1 gap-4 sm:grid-cols-2' : 'grid grid-cols-1 gap-5 lg:grid-cols-2'}>
          {status.items.map((item) => {
            const Icon = ICONS[item.toolkit] ?? Mail
            return (
              <Panel key={item.toolkit}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-background-elevated">
                      <Icon className="h-5 w-5 text-secondary" aria-hidden />
                    </span>
                    <div className="min-w-0">
                      <h2 className="type-subtitle">{item.name}</h2>
                      <p className="type-muted mt-1">{item.description}</p>
                    </div>
                  </div>
                  <Badge tone={item.connected ? 'good' : 'neutral'} className="shrink-0 whitespace-nowrap">
                    {item.connected ? 'Connected' : 'Not connected'}
                  </Badge>
                </div>

                <div className="mt-5">
                  <p className="type-caption mb-2">Try asking</p>
                  <ul className="space-y-1 text-sm text-secondary">
                    {(EXAMPLES[item.toolkit] ?? []).map((example) => (
                      <li key={example}>“{example}”</li>
                    ))}
                  </ul>
                </div>

                {item.connected && sync && (() => {
                  const state = sync.sources.find((s) => s.source === SYNC_SOURCE[item.toolkit])
                  if (!state) return null
                  return (
                    <p className={`mt-4 text-[12px] ${state.last_error ? 'text-red-600' : 'text-muted'}`}>
                      {state.last_error
                        ? `Last sync failed: ${state.last_error}`
                        : `Synced ${ago(state.last_ok_at)} · ${state.items} item${state.items === 1 ? '' : 's'} kept`}
                    </p>
                  )
                })()}

                <div className="mt-6 flex justify-end">
                  {item.connected ? (
                    <Btn
                      variant="secondary"
                      size="sm"
                      loading={busy === item.toolkit}
                      onClick={() => void disconnect(item.toolkit, item.name)}
                    >
                      Disconnect
                    </Btn>
                  ) : (
                    <Btn
                      size="sm"
                      disabled={!status.configured}
                      loading={busy === item.toolkit}
                      onClick={() => void connect(item.toolkit)}
                    >
                      Connect {item.name}
                    </Btn>
                  )}
                </div>
              </Panel>
            )
          })}
        </div>
      )}
    </PageStack>
  )
}
