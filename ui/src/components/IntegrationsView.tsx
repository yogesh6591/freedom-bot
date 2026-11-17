'use client'

/**
 * The signed-in user's own connections through Composio: every platform Composio offers (searchable),
 * and for each one which of its tools the assistant may use ("All tools" by default).
 * Shown on the Integrations page (admin panel) and in the Settings pop-up on the home chat.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plug, Search, X } from 'lucide-react'
import { api } from '@/lib/api'
import {
  Badge,
  Btn,
  ErrorNote,
  LoadingBlock,
  PageHeader,
  PageStack
} from '@/components/common/primitives'

interface Platform {
  slug: string
  name: string
  description: string
  logo: string
  categories: string[]
  tools_count: number
  managed: boolean
  auth_schemes: string[]
  connected: boolean
  connectable: boolean
}

interface CatalogPage {
  total: number
  items: Platform[]
  categories: string[]
}

interface PlatformTool {
  slug: string
  name: string
  description: string
  tier: 'read' | 'write' | 'destructive'
}

interface PlatformDetail {
  platform: Platform
  tools: PlatformTool[]
  all: boolean
  selected: string[]
}

interface SyncSource {
  source: string
  items: number
  last_ok_at: string | null
  last_error: string | null
}

interface SyncStatus {
  enabled: boolean
  interval_sec: number
  next_run_in_sec: number | null
  sources: SyncSource[]
}

/** Built-in apps whose latest activity also syncs in the background (freedombot/sync). */
const SYNC_SOURCE: Record<string, string> = {
  gmail: 'gmail',
  googlecalendar: 'calendar',
  youtube: 'youtube',
  hubspot: 'hubspot'
}

const PAGE = 60

const TIER: Record<PlatformTool['tier'], { label: string; tone: string }> = {
  read: { label: 'Read', tone: 'bg-emerald-500/10 text-emerald-700' },
  write: { label: 'Create / update', tone: 'bg-amber-500/10 text-amber-700' },
  destructive: { label: 'Delete / send', tone: 'bg-red-500/10 text-red-600' }
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return hours < 24 ? `${hours} h ago` : new Date(iso).toLocaleString()
}

function Logo({ platform, size = 40 }: { platform: Pick<Platform, 'logo' | 'name'>; size?: number }) {
  const [broken, setBroken] = useState(false)
  return (
    <span
      className="flex shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-white"
      style={{ width: size, height: size }}
    >
      {platform.logo && !broken ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={platform.logo} alt="" loading="lazy" className="h-3/4 w-3/4 object-contain" onError={() => setBroken(true)} />
      ) : (
        <Plug className="h-1/2 w-1/2 text-secondary" aria-hidden />
      )}
    </span>
  )
}

export function IntegrationsView({
  callbackPath = '/integrations',
  compact = false
}: {
  /** Where Composio sends the user back after signing in (it adds ?connected=<platform>). */
  callbackPath?: string
  /** Pop-up layout: smaller grid, no page header. */
  compact?: boolean
}) {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [connectedOnly, setConnectedOnly] = useState(false)
  const [page, setPage] = useState<CatalogPage | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [openSlug, setOpenSlug] = useState<string | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)

  const params = useCallback(
    (offset: number) =>
      new URLSearchParams({
        q: query,
        category,
        connected_only: String(connectedOnly),
        offset: String(offset),
        limit: String(PAGE)
      }).toString(),
    [query, category, connectedOnly]
  )

  const loadCatalog = useCallback(async () => {
    try {
      setPage(await api.get<CatalogPage>(`/api/integrations/composio/catalog?${params(0)}`))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load integrations')
    }
  }, [params])

  async function loadMore() {
    if (!page) return
    setLoadingMore(true)
    try {
      const next = await api.get<CatalogPage>(`/api/integrations/composio/catalog?${params(page.items.length)}`)
      setPage({ ...next, items: [...page.items, ...next.items] })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load more')
    } finally {
      setLoadingMore(false)
    }
  }

  const loadSync = useCallback(async () => {
    try {
      setSync(await api.get<SyncStatus>('/api/integrations/composio/sync'))
    } catch {
      setSync(null)
    }
  }, [])

  // First load: refresh the connection status when coming back from a sign-in.
  useEffect(() => {
    const returned = new URLSearchParams(window.location.search).has('connected')
    void (async () => {
      try {
        const status = returned
          ? await api.post<{ configured: boolean }>('/api/integrations/composio/refresh')
          : await api.get<{ configured: boolean }>('/api/integrations/composio')
        setConfigured(status.configured)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not load integrations')
      }
    })()
    void loadSync()
    if (returned) window.history.replaceState(null, '', callbackPath)
  }, [loadSync, callbackPath])

  // Search as the user types (short pause), and on filter changes.
  useEffect(() => {
    if (configured === false) return
    const timer = window.setTimeout(() => void loadCatalog(), 250)
    return () => window.clearTimeout(timer)
  }, [loadCatalog, configured])

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

  async function connect(slug: string) {
    setBusy(slug)
    try {
      const callback = `${window.location.origin}${callbackPath}?connected=${slug}`
      const data = await api.post<{ redirect_url: string }>(`/api/integrations/composio/${slug}/connect`, {
        callback_url: callback
      })
      window.location.href = data.redirect_url
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start sign-in')
      setBusy(null)
    }
  }

  async function disconnect(slug: string, name: string) {
    if (!window.confirm(`Disconnect ${name}? The assistant will stop using it.`)) return
    setBusy(slug)
    try {
      await api.post(`/api/integrations/composio/${slug}/disconnect`)
      await api.post('/api/integrations/composio/refresh')
      await loadCatalog()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not disconnect')
    } finally {
      setBusy(null)
    }
  }

  const anyBuiltInConnected = page?.items.some((p) => p.connected && SYNC_SOURCE[p.slug]) ?? false

  return (
    <PageStack>
      {compact ? (
        <p className="text-[13px] leading-5 text-muted">
          Connect your own accounts. The assistant then reads and acts on them, subject to your
          workspace&rsquo;s approval rules. Choose which tools it may use on each platform.
        </p>
      ) : (
        <PageHeader
          eyebrow="Your accounts"
          title="Integrations"
          description="Connect any platform Composio supports. For each one, choose whether the assistant may use all of its tools or only some. Everything it does still follows your workspace’s approval rules."
        />
      )}

      {error && <ErrorNote>{error}</ErrorNote>}

      {configured === false && (
        <ErrorNote>
          Connections are not enabled on this server yet. An administrator needs to set COMPOSIO_API_KEY.
        </ErrorNote>
      )}

      {sync && anyBuiltInConnected && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-background-elevated px-4 py-3">
          <p className="text-[13px] text-secondary">
            {sync.enabled
              ? `Latest emails, meetings, videos and CRM changes sync automatically every ${Math.round(sync.interval_sec / 60)} min.`
              : 'Automatic sync is off on this server. Use Sync now to pull your latest activity.'}
          </p>
          <Btn variant="secondary" size="sm" loading={syncing} onClick={() => void syncNow()}>
            Sync now
          </Btn>
        </div>
      )}

      {!compact && <CrmStorageCard />}

      {configured !== false && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative min-w-[14rem] flex-1">
              <span className="sr-only">Search platforms</span>
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search apps, e.g. Gmail, Drive, HubSpot, Apollo"
                className="h-10 w-full rounded-lg border border-border bg-background pl-9 pr-3 text-[14px] outline-none focus:border-purple-300"
              />
            </label>
            <label className="inline-flex items-center gap-2 text-[13px] text-secondary">
              <input type="checkbox" checked={connectedOnly} onChange={(e) => setConnectedOnly(e.target.checked)} />
              Connected only
            </label>
          </div>
          {page && page.categories.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {['', ...page.categories.slice(0, compact ? 10 : 16)].map((c) => (
                <button
                  key={c || 'all'}
                  type="button"
                  onClick={() => setCategory(c)}
                  className={`rounded-full border px-2.5 py-1 text-[12px] capitalize ${
                    category === c ? 'border-purple-300 bg-purple-50 text-brand' : 'border-border text-secondary hover:text-primary'
                  }`}
                >
                  {c || 'All'}
                </button>
              ))}
            </div>
          )}
          {page && <p className="text-[12px] text-muted">{page.total.toLocaleString()} platforms</p>}
        </div>
      )}

      {configured !== false && !page ? (
        <LoadingBlock />
      ) : page ? (
        <>
          <div className={compact ? 'grid grid-cols-1 gap-3 sm:grid-cols-2' : 'grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3'}>
            {page.items.map((p) => {
              const state = p.connected && sync ? sync.sources.find((s) => s.source === SYNC_SOURCE[p.slug]) : undefined
              return (
                <div
                  key={p.slug}
                  role="button"
                  tabIndex={0}
                  onClick={() => setOpenSlug(p.slug)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setOpenSlug(p.slug)}
                  className="flex cursor-pointer flex-col gap-3 rounded-xl border border-border bg-background p-4 text-left transition-colors hover:border-purple-200"
                >
                  <div className="flex items-start gap-3">
                    <Logo platform={p} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-[14px] font-semibold text-primary">{p.name}</p>
                        {p.connected ? (
                          <Badge tone="good" className="shrink-0 whitespace-nowrap">Connected</Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 line-clamp-2 text-[12.5px] leading-5 text-muted">{p.description}</p>
                    </div>
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-2">
                    <span className="text-[12px] text-muted">
                      {p.tools_count ? `${p.tools_count} tools` : ''}
                      {state ? (state.last_error ? ' · last sync failed' : ` · synced ${ago(state.last_ok_at)}`) : ''}
                    </span>
                    {p.connected ? (
                      <Btn variant="secondary" size="sm" onClick={(e) => { e.stopPropagation(); setOpenSlug(p.slug) }}>
                        Manage
                      </Btn>
                    ) : p.connectable ? (
                      <Btn size="sm" loading={busy === p.slug} onClick={(e) => { e.stopPropagation(); void connect(p.slug) }}>
                        Connect
                      </Btn>
                    ) : (
                      <span className="text-[12px] text-muted" title="Uses its own API key or OAuth app: an admin sets it up in Composio first.">
                        Admin setup needed
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
          {page.items.length === 0 && <p className="text-[14px] text-muted">No platforms match.</p>}
          {page.items.length < page.total && (
            <div className="flex justify-center">
              <Btn variant="secondary" size="sm" loading={loadingMore} onClick={() => void loadMore()}>
                Load more
              </Btn>
            </div>
          )}
        </>
      ) : null}

      {openSlug && (
        <PlatformDialog
          slug={openSlug}
          busy={busy === openSlug}
          onClose={() => setOpenSlug(null)}
          onConnect={() => void connect(openSlug)}
          onDisconnect={(name) => void disconnect(openSlug, name)}
        />
      )}
    </PageStack>
  )
}

/** One platform: connect or disconnect, and which tools the assistant may use ("All tools" by default). */
function PlatformDialog({
  slug,
  busy,
  onClose,
  onConnect,
  onDisconnect
}: {
  slug: string
  busy: boolean
  onClose: () => void
  onConnect: () => void
  onDisconnect: (name: string) => void
}) {
  const [detail, setDetail] = useState<PlatformDetail | null>(null)
  const [all, setAll] = useState(true)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.get<PlatformDetail>(`/api/integrations/composio/catalog/${encodeURIComponent(slug)}`)
        setDetail(data)
        setAll(data.all)
        setPicked(new Set(data.all ? data.tools.map((t) => t.slug) : data.selected))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not load this platform')
      }
    })()
  }, [slug])

  const shown = useMemo(() => {
    const words = filter.trim().toLowerCase()
    return (detail?.tools ?? []).filter((t) => !words || `${t.name} ${t.slug} ${t.description}`.toLowerCase().includes(words))
  }, [detail, filter])

  function toggle(tool: string) {
    setSaved(false)
    setPicked((current) => {
      const next = new Set(current)
      if (next.has(tool)) next.delete(tool)
      else next.add(tool)
      return next
    })
  }

  function setMany(tools: PlatformTool[], on: boolean) {
    setSaved(false)
    setPicked((current) => {
      const next = new Set(current)
      for (const t of tools) {
        if (on) next.add(t.slug)
        else next.delete(t.slug)
      }
      return next
    })
  }

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await api.put(`/api/integrations/composio/catalog/${encodeURIComponent(slug)}/tools`, {
        all,
        actions: all ? [] : [...picked]
      })
      setSaved(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  const p = detail?.platform
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={p?.name ?? 'Platform'}
        className="flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 border-b border-border p-5">
          {p ? <Logo platform={p} size={44} /> : null}
          <div className="min-w-0 flex-1">
            <p className="text-[16px] font-semibold text-primary">{p?.name ?? 'Loading…'}</p>
            <p className="mt-1 text-[13px] leading-5 text-muted">{p?.description}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded p-1 text-muted hover:text-primary">
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {error && <ErrorNote>{error}</ErrorNote>}
          {!detail ? (
            !error && <LoadingBlock />
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                {p!.connected ? (
                  <Badge tone="good">Connected</Badge>
                ) : p!.connectable ? (
                  <span className="text-[13px] text-muted">Not connected yet. You can choose its tools now.</span>
                ) : (
                  <span className="text-[13px] text-muted">
                    Admin setup needed: {p!.name} signs in with its own {p!.auth_schemes.join(' or ') || 'credentials'}.
                  </span>
                )}
                {p!.connected ? (
                  <Btn variant="secondary" size="sm" loading={busy} onClick={() => onDisconnect(p!.name)}>
                    Disconnect
                  </Btn>
                ) : p!.connectable ? (
                  <Btn size="sm" loading={busy} onClick={onConnect}>
                    Connect {p!.name}
                  </Btn>
                ) : null}
              </div>

              <div className="rounded-xl border border-border p-4">
                <p className="text-[14px] font-semibold text-primary">Tools the assistant may use</p>
                <p className="mt-1 text-[12.5px] text-muted">
                  Read tools run straight away; create, update, delete and send tools follow your workspace&rsquo;s
                  approval rules.
                </p>
                <label className="mt-3 flex items-center gap-2 text-[14px] font-medium text-primary">
                  <input
                    type="checkbox"
                    checked={all}
                    onChange={(e) => {
                      setAll(e.target.checked)
                      setSaved(false)
                      if (!e.target.checked && picked.size === 0) setPicked(new Set(detail.tools.map((t) => t.slug)))
                    }}
                  />
                  All tools ({detail.tools.length})
                </label>

                {!all && (
                  <div className="mt-3 flex flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        placeholder="Filter tools"
                        className="h-9 min-w-[10rem] flex-1 rounded-lg border border-border bg-background px-3 text-[13px] outline-none focus:border-purple-300"
                      />
                      <Btn variant="secondary" size="sm" onClick={() => setMany(shown, true)}>Select shown</Btn>
                      <Btn variant="secondary" size="sm" onClick={() => setMany(shown, false)}>Clear shown</Btn>
                    </div>
                    <p className="text-[12px] text-muted">{picked.size} of {detail.tools.length} selected</p>
                    <div className="max-h-[40vh] overflow-y-auto rounded-lg border border-border">
                      {shown.map((t) => (
                        <label key={t.slug} className="flex cursor-pointer items-start gap-3 border-b border-border px-3 py-2 last:border-0 hover:bg-background-elevated">
                          <input type="checkbox" className="mt-1" checked={picked.has(t.slug)} onChange={() => toggle(t.slug)} />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="text-[13px] font-medium text-primary">{t.name}</span>
                              <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${TIER[t.tier].tone}`}>{TIER[t.tier].label}</span>
                            </span>
                            {t.description ? <span className="mt-0.5 block text-[12px] leading-5 text-muted">{t.description}</span> : null}
                          </span>
                        </label>
                      ))}
                      {shown.length === 0 && <p className="px-3 py-4 text-[13px] text-muted">No tools match.</p>}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {detail && (
          <div className="flex items-center justify-end gap-3 border-t border-border p-4">
            {saved && <span className="text-[13px] text-emerald-700">Saved</span>}
            <Btn variant="secondary" size="sm" onClick={onClose}>Close</Btn>
            <Btn size="sm" loading={saving} disabled={!all && picked.size === 0} onClick={() => void save()}>
              Save tools
            </Btn>
          </div>
        )}
      </div>
    </div>
  )
}

type CrmStorage = {
  choice: string
  choice_name: string
  saving_to: string
  saving_to_name: string
  note: string
  available: Record<string, boolean>
  sheet_url: string | null
  message?: string
  pending_approval?: boolean
}

const STORAGE_OPTIONS: { value: string; label: string; needs?: string }[] = [
  { value: 'auto', label: 'Automatic (HubSpot when connected, otherwise FreedomBot)' },
  { value: 'freedombot', label: 'FreedomBot database' },
  { value: 'hubspot', label: 'HubSpot', needs: 'hubspot' },
  { value: 'google_drive', label: 'Google Drive (a FreedomBot CRM sheet)', needs: 'google_drive' }
]

/** Where the signed-in user's new CRM records are saved. Hidden for people without CRM access. */
function CrmStorageCard() {
  const [info, setInfo] = useState<CrmStorage | null>(null)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setInfo(await api.get<CrmStorage>('/api/integrations/crm-storage'))
    } catch {
      setInfo(null)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function choose(destination: string) {
    setSaving(true)
    setNote(null)
    try {
      const out = await api.put<CrmStorage>('/api/integrations/crm-storage', { destination })
      if (out.pending_approval) {
        setNote(out.message ?? 'Sent for approval. It changes once an admin approves it.')
      } else {
        setInfo(out)
        setNote(out.message ?? null)
      }
    } catch (err) {
      setNote(err instanceof Error ? err.message : 'Could not change where CRM records are saved')
    } finally {
      setSaving(false)
    }
  }

  if (!info) return null
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-background-elevated px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[14px] font-medium text-primary">Save CRM records in</p>
          <p className="text-[12px] text-muted">
            Contacts, companies and deals you add go to {info.saving_to_name}. Existing records stay where they are.
          </p>
        </div>
        <label className="min-w-[16rem]">
          <span className="sr-only">Save CRM records in</span>
          <select
            value={info.choice}
            disabled={saving}
            onChange={(e) => void choose(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-background px-2 text-[13px]"
          >
            {STORAGE_OPTIONS.map((o) => {
              const off = o.needs ? !info.available[o.needs] : false
              return (
                <option key={o.value} value={o.value} disabled={off && o.value !== info.choice}>
                  {o.label}
                  {off ? ' (connect it first)' : ''}
                </option>
              )
            })}
          </select>
        </label>
      </div>
      {info.note && <p className="text-[12px] text-amber-700">{info.note}</p>}
      {info.saving_to === 'google_drive' && info.sheet_url && (
        <a href={info.sheet_url} target="_blank" rel="noreferrer" className="text-[12px] text-purple-700 underline">
          Open the FreedomBot CRM sheet
        </a>
      )}
      {note && <p className="text-[12px] text-secondary">{note}</p>}
    </div>
  )
}
