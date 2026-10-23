'use client'

/** Per-user Gmail / Google Calendar connections (Composio). */

import { useCallback, useEffect, useState } from 'react'
import { Building2, CalendarDays, Mail, Youtube } from 'lucide-react'
import { AppShell } from '@/components/AppShell'
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

export default function IntegrationsPage() {
  return (
    <AppShell>
      <IntegrationsView />
    </AppShell>
  )
}

function IntegrationsView() {
  const [status, setStatus] = useState<ComposioStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

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
    if (returned) window.history.replaceState(null, '', '/integrations')
  }, [load])

  async function connect(toolkit: string) {
    setBusy(toolkit)
    try {
      const callback = `${window.location.origin}/integrations?connected=${toolkit}`
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

      {error && <ErrorNote>{error}</ErrorNote>}

      {status && !status.configured && (
        <ErrorNote>
          Connections are not enabled on this server yet. An administrator needs to set
          COMPOSIO_API_KEY.
        </ErrorNote>
      )}

      {!status ? (
        <LoadingBlock />
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
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
                  <Badge tone={item.connected ? 'good' : 'neutral'}>
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
